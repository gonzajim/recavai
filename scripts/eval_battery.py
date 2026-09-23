#!/usr/bin/env python3
"""
Pasa la batería de evaluación por una configuración del sistema y la puntúa.

Usa el MISMO código que producción: `gemini_service.chat_with_expert` para el asesor
y `build_verification_query` para las comprobaciones del modo auditor. Lo único que
cambia entre configuraciones es el índice, el modelo de embeddings y el umbral de
similitud; para capturar qué se recuperó se envuelve `search_documents`, sin
modificarlo.

Tres familias de métricas por pregunta:
  Recuperación (determinista, sin LLM)
    doc@6         algún fragmento del documento esperado entre los recuperados
    pasaje@6      algún fragmento recuperado CONTIENE el pasaje de un hecho clave (≥60 % de
                  sus palabras), mire o no la página. Independiente de los metadatos de página,
                  que en el índice v1 están mal en muchos fragmentos (p. ej. «p.1» en mitad de
                  la CSDDD).
    pag@6         ... y además de la página esperada (±1)
    mrr           1/posición del primer fragmento documento+página correcto
    fuentes       fracción de fuentes esperadas encontradas (preguntas multi-fuente)
  Respuesta (juez LLM, que no sabe qué configuración evalúa)
    cobertura     fracción de hechos clave presentes (parcial cuenta 0,5)
    fiel          1 si no afirma nada que no respalden los fragmentos o los hechos clave
    rechazo_ok    en trampas: corrige la premisa o dice que no está en el corpus
    veredicto_ok  en el modo auditor: el VEREDICTO coincide con el esperado
    cita_unidad   la respuesta menciona el artículo / requisito / contenido esperado
  Funcionamiento
    ms_total, ms_busqueda, error

Uso:
  python scripts/eval_battery.py run --name A --index uclm-corpus-roma \\
      --model sentence-transformers/all-MiniLM-L6-v2 --min-score 0.55
  python scripts/eval_battery.py run --name B --index memory:.cache/idx_B.npz \\
      --model intfloat/multilingual-e5-small --min-score 0.80
  python scripts/eval_battery.py compare A C          # informe comparativo
  python scripts/eval_battery.py pairwise A CG        # juez a ciegas, respuesta contra respuesta
  python scripts/eval_battery.py run --name realA --queries data/real_queries.jsonl --no-generate ...
  python scripts/eval_battery.py pairwise realA realCG --context   # contexto contra contexto
  python scripts/eval_battery.py rejudge A --fragments-from A_busqueda --judge-model gemini-3.5-flash --out A.j35
      (vuelve a juzgar respuestas ya generadas con otro juez; todas las configuraciones que se
       comparan tienen que pasar por el MISMO juez)
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import threading
import time
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "results" / "rag_v2"
from benchmarks.bateria_v1 import _fold, _words      # noqa: E402  mismo criterio que --verify
BATTERY = ROOT / "benchmarks" / "bateria_v1.jsonl"


# ======================================================================================
# Índice en memoria con la misma interfaz que Pinecone (para la configuración B)
# ======================================================================================
class MemoryIndex:
    """Vectores + metadatos en un .npz. Implementa el subconjunto de `Index.query` que usa
    `rag_service.search_documents`, incluido el filtro {'primary_category': {'$in': [...]}}."""

    def __init__(self, path: str):
        import numpy as np
        d = np.load(path, allow_pickle=False)
        self.vecs = d["vecs"].astype("float32")
        self.meta = json.loads(str(d["meta"]))
        self.np = np
        self.by_id = {m["id"]: i for i, m in enumerate(self.meta) if "id" in m}

    def fetch(self, ids, namespace=None):
        """Lo que usa el grafo normativo para traer los fragmentos vecinos."""
        from types import SimpleNamespace
        vecs = {i: SimpleNamespace(values=self.vecs[self.by_id[i]].tolist(), metadata=self.meta[self.by_id[i]])
                for i in ids if i in self.by_id}
        return SimpleNamespace(vectors=vecs)

    def query(self, vector, top_k=10, include_metadata=True, filter=None, namespace=None):
        np = self.np
        scores = self.vecs @ np.asarray(vector, dtype="float32")
        idx = np.argsort(-scores)
        allowed = None
        if filter and "primary_category" in filter:
            allowed = set(filter["primary_category"].get("$in", []))
        out = []
        for i in idx:
            m = self.meta[i]
            if allowed is not None and m.get("primary_category") not in allowed:
                continue
            out.append({"score": float(scores[i]), "metadata": m, "id": str(i)})
            if len(out) >= top_k:
                break
        return {"matches": out}


class LockedModel:
    """SentenceTransformer compartido entre hilos: encode no es reentrante de forma fiable."""

    def __init__(self, model, name: str):
        self._m, self._lock = model, threading.Lock()
        self.recava_model_name = name

    def encode(self, *a, **k):
        with self._lock:
            return self._m.encode(*a, **k)


# ======================================================================================
# Unidades normativas mencionadas en un texto
# ======================================================================================
_UNIT_PATTERNS = [
    (re.compile(r"\bart(?:[íi]culo|s?\.)\s*(\d+)(?:\s*(bis|ter|qu[áa]ter))?", re.I),
     lambda m: f"art.{m.group(1)}" + (f".{m.group(2).lower()}" if m.group(2) else "")),
    (re.compile(r"\banexo\s+([IVX]+|\d+)\b", re.I), lambda m: f"anexo.{m.group(1).upper()}"),
    (re.compile(r"\b((?:[EGS]\d|SBM|IRO|GOV|BP)-\d{1,2})\b"), lambda m: f"esrs.{m.group(1).upper()}"),
    (re.compile(r"\b(\d{1,3}-\d{1,2})\b"), lambda m: f"gri.{m.group(1)}"),
]


def units_in(text: str) -> set[str]:
    found = set()
    for rx, fn in _UNIT_PATTERNS:
        for m in rx.finditer(text or ""):
            found.add(fn(m))
    return found


def norm_unit(label: str | None) -> str | None:
    if not label:
        return None
    u = units_in(label)
    if not u and label.lower().startswith("anexo"):
        return "anexo.I"
    return next(iter(u), None)


# ======================================================================================
# Juez
# ======================================================================================
JUDGE_PROMPT = """Eres un evaluador experto en normativa europea de sostenibilidad (CSDDD, CSRD, NEIS/ESRS, GRI, guías OCDE).
Evalúas UNA respuesta de un asistente. No sabes qué sistema la produjo. Sé estricto y literal.

PREGUNTA:
{pregunta}

HECHOS CLAVE que una buena respuesta debe transmitir (extraídos del corpus normativo):
{hechos}

{extra}

FRAGMENTOS que el asistente tenía delante al responder:
{fragmentos}

RESPUESTA DEL ASISTENTE:
{respuesta}

Devuelve SOLO un JSON con esta forma:
{{
  "hechos": [{{"i": 1, "estado": "presente" | "parcial" | "ausente"}}, ...],
  "afirmaciones_no_soportadas": ["afirmación concreta de la respuesta que NO respaldan ni los fragmentos ni los hechos clave", ...],
  "contradice_hechos": true | false,
  "rechazo_correcto": true | false | null,
  "comentario": "una frase"
}}

Criterios:
- "presente": la respuesta transmite el hecho, aunque con otras palabras. "parcial": lo menciona de forma incompleta o imprecisa. "ausente": no aparece.
- "afirmaciones_no_soportadas": solo afirmaciones sustantivas (cifras, plazos, obligaciones, artículos) que no estén respaldadas. No cuentes consejos genéricos ni frases de cortesía.
- "contradice_hechos": true si algo de la respuesta es incompatible con un hecho clave (p. ej. una cifra o un plazo distinto).
- "rechazo_correcto": {rechazo_instr}
"""


def judge(genai_client, model: str, item: dict, answer: str, fragments: list[dict]) -> dict:
    from google.genai import types
    hechos = "\n".join(f"{i}. {h}" for i, h in enumerate(item["hechos_clave"], 1))
    def head(i, f):
        # La misma información que ve el modelo en _build_rag_message: si allí aparece
        # «Artículo 3», el juez tiene que verlo, o marcará como inventada una cita correcta.
        pages = f"{f.get('page')}" + (f"-{f['page_end']}" if f.get("page_end") and f.get("page_end") != f.get("page") else "")
        unit = f"{f['unit_label']} · " if f.get("unit_label") else ""
        return f"[{i}] {f.get('title','')} ({unit}p.{pages})"
    frag = "\n\n".join(f"{head(i, f)}\n{(f.get('content') or '')[:2000]}"
                       for i, f in enumerate(fragments, 1)) or "(ninguno)"
    extra = f"NOTA DEL EVALUADOR HUMANO: {item['nota']}" if item.get("nota") else ""
    if item["debe_rechazar"]:
        rechazo = ("esta pregunta tiene una premisa falsa o pregunta por algo que no está en el corpus; "
                   "true si la respuesta lo detecta y NO inventa el contenido, false si inventa o sigue la premisa.")
    else:
        rechazo = "null (esta pregunta sí tiene respuesta en el corpus)."
    prompt = JUDGE_PROMPT.format(pregunta=item["pregunta"], hechos=hechos, extra=extra,
                                 fragmentos=frag, respuesta=answer or "(respuesta vacía)",
                                 rechazo_instr=rechazo)
    last = None
    for _ in range(3):
        try:
            r = genai_client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"))
            return json.loads(r.text)
        except Exception as e:                       # noqa: BLE001
            last = e
            time.sleep(3)
    return {"error": f"{type(last).__name__}: {last}"}


# ======================================================================================
# Ejecución de una configuración
# ======================================================================================
def load_battery(path: Path = BATTERY) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_queries(path: Path) -> list[dict]:
    """Preguntas sin respuesta de referencia (p. ej. las reales de BigQuery) con la forma
    de un ítem de la batería, para poder pasarlas por el mismo circuito."""
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [{"id": f"R{k:03d}", "tipo": "real", "modo": "asesor", "origen": "real", "pregunta": r["pregunta"],
             "fuentes_esperadas": [], "hechos_clave": [], "debe_rechazar": False,
             "veredicto_esperado": None, "nota": ""} for k, r in enumerate(rows, 1)]


def setup(index_spec: str, model_name: str, min_score: float, graph: str | None = None):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    os.environ["EMBEDDING_MODEL_NAME"] = model_name      # lo lee rag_service para el prefijo e5
    os.environ["RAG_NORMATIVE_GRAPH"] = graph or ""      # lo lee src/normative_graph al primer uso
    from sentence_transformers import SentenceTransformer
    from src import gemini_service as gs, rag_service as rs
    from src.config import genai_client

    rs._MIN_SCORE = min_score
    # search_documents recibe min_score como argumento por defecto: se fija en el wrapper.
    embed = LockedModel(SentenceTransformer(model_name), model_name)

    if index_spec.startswith("memory:"):
        index = MemoryIndex(index_spec.split(":", 1)[1])
    else:
        from pinecone import Pinecone
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        index = pc.Index(index_spec)

    captured = threading.local()
    original = rs.search_documents

    def spy(pinecone_index, query_embedding, top_k=rs._CANDIDATE_K, metadata_filter=None,
            min_score=None, namespace=None):
        t0 = time.time()
        res = original(pinecone_index, query_embedding, top_k=top_k, metadata_filter=metadata_filter,
                       min_score=min_score_value if min_score is None else min_score, namespace=namespace)
        captured.calls = getattr(captured, "calls", []) + [res]
        captured.ms = getattr(captured, "ms", 0.0) + (time.time() - t0) * 1000
        return res

    min_score_value = min_score
    gs.search_documents = spy

    graph_obj = gs.get_graph()
    if graph_obj is not None:
        original_expand, original_explicit = graph_obj.expand, graph_obj.explicit
        def expand_spy(*a, **k):
            extra = original_expand(*a, **k)
            captured.graph_docs = extra
            return extra
        def explicit_spy(*a, **k):
            direct = original_explicit(*a, **k)
            captured.direct_docs = direct
            return direct
        graph_obj.expand, graph_obj.explicit = expand_spy, explicit_spy
    return gs, embed, index, genai_client, captured


def score_retrieval(item: dict, docs: list[dict]) -> dict:
    exp = item["fuentes_esperadas"]
    if not exp:
        return {}
    def hit(d, e):
        if d.get("title") != e["documento"]:
            return False
        p, pe = d.get("page"), d.get("page_end") or d.get("page")
        return p is not None and (p - 1) <= e["pagina"] <= (pe + 1)
    doc_ok = any(d.get("title") == e["documento"] for d in docs for e in exp)
    bags = [set(_fold(d.get("content") or "").split()) for d in docs]
    def covered(h):
        ws = _words(h)
        return any(sum(1 for w in ws if w in b) / max(1, len(ws)) >= 0.6 for b in bags)
    pasaje = any(covered(h) for h in item["hechos_clave"]) if not item["debe_rechazar"] else None
    first = next((i for i, d in enumerate(docs, 1) if any(hit(d, e) for e in exp)), None)
    found = sum(1 for e in exp if any(hit(d, e) for d in docs))
    out = {"pasaje@6": float(pasaje)} if pasaje is not None else {}
    return out | {"doc@6": float(doc_ok), "pag@6": float(first is not None),
            "mrr": (1.0 / first) if first else 0.0, "fuentes": found / len(exp)}


def HAS_HARNESS(gs) -> bool:
    import inspect
    return "return_result" in inspect.signature(gs.chat_with_expert).parameters


def critical_count(answer: str, docs: list[dict], question: str) -> float | None:
    """Datos críticos de la respuesta (cifras, fechas, artículos, normas, códigos) sin
    respaldo en los fragmentos ni en la pregunta. Determinista: mismo criterio que el
    control de salida del harness (src/agent/guardrails.py). Se descuenta la nota de
    verificación que añade el harness, que repite los datos dudosos a propósito."""
    if not answer:
        return None
    from src.agent import guardrails as gr
    body = answer.split("\n\n---\n*Verificación automática:")[0]
    st = gr.TurnState(question=question, search_text=question, used_docs=docs, answer=body)
    return float(len(gr.unsupported(body, gr.build_evidence(gr.evidence_texts(st)), gr.norm_registry())))


def run_item(item, gs, embed, index, genai_client, captured, judge_model, generate=True) -> dict:
    captured.calls, captured.ms, captured.graph_docs, captured.direct_docs = [], 0.0, [], []
    rec = {"id": item["id"], "tipo": item["tipo"], "pregunta": item["pregunta"]}
    t0 = time.time()
    try:
        retrieval_query = None
        if item["modo"] == "auditor":
            escenario = re.sub(r"\s*¿Cumple con la normativa\?\s*$", "", item["pregunta"]).strip()
            preguntas = ["¿Cumple la empresa con la normativa aplicable en este aspecto?"]
            query = gs.build_verification_query(preguntas, escenario)
            # igual que _verify_compliance, si el servicio ya separa búsqueda e instrucciones
            if hasattr(gs, "build_verification_retrieval_query"):
                retrieval_query = gs.build_verification_retrieval_query(preguntas, escenario)
        else:
            query = item["pregunta"]
        kw = {"retrieval_query": retrieval_query} if retrieval_query else {}
        turn = None
        if generate and HAS_HARNESS(gs):
            # Con harness: la misma ruta y la misma tarea que usa producción, y el resultado
            # completo (borrador, controles que saltaron, fragmentos exactos del contexto).
            turn = gs.chat_with_expert(genai_client, embed, index, [], query, return_result=True,
                                       task="verificacion" if item["modo"] == "auditor" else "asesor", **kw)
            answer = turn.text
        elif generate:
            answer, _sources = gs.chat_with_expert(genai_client, embed, index, [], query, **kw)
        else:
            gs._build_rag_message(embed, index, query, **kw)
            answer = ""
    except Exception as e:                            # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
        answer, turn = "", None
    rec["ms_total"] = round((time.time() - t0) * 1000)
    rec["ms_busqueda"] = round(captured.ms)
    # Lo que realmente llegó al modelo: referencia explícita delante, búsqueda, grafo detrás.
    docs = captured.calls[-1] if captured.calls else []
    direct = getattr(captured, "direct_docs", [])
    seen = {(d.get("title"), (d.get("content") or "")[:80]) for d in direct}
    docs = direct + [d for d in docs if (d.get("title"), (d.get("content") or "")[:80]) not in seen]
    docs = docs + getattr(captured, "graph_docs", [])
    if turn is not None:
        docs = turn.used_docs                      # exactamente lo que entró en el contexto
        rec["borrador"] = turn.draft
        rec["agente"] = turn.report()
        rec["avisos"] = [n["guard"] for n in turn.notices]
    rec["recuperados"] = [{"title": d.get("title"), "page": d.get("page"), "page_end": d.get("page_end"),
                           "article": d.get("article"), "unit_label": d.get("unit_label"),
                           "category": d.get("category"), "score": round(d.get("score", 0), 4),
                           "content": d.get("content")} for d in docs]
    rec.update(score_retrieval(item, docs))
    if not generate:
        return rec

    rec["respuesta"] = answer
    rec["criticos"] = critical_count(answer, docs, item["pregunta"])
    if "borrador" in rec:
        rec["criticos_borrador"] = critical_count(rec["borrador"], docs, item["pregunta"])
    exp_units = {norm_unit(f["articulo"]) for f in item["fuentes_esperadas"] if f["articulo"]} - {None}
    if exp_units:
        rec["cita_unidad"] = float(bool(exp_units & units_in(answer)))
    if item["modo"] == "auditor":
        m = gs._VERDICT_RE.search(answer or "")
        rec["veredicto"] = m.group(1).lower() if m else None
        rec["veredicto_ok"] = float(rec["veredicto"] == item["veredicto_esperado"])

    score_judgement(rec, item, judge(genai_client, judge_model, item, answer, docs))
    return rec


def cmd_run(a) -> None:
    items = load_queries(Path(a.queries)) if a.queries else load_battery()
    if a.only:
        items = [it for it in items if it["id"] in set(a.only.split(","))]
    if a.limit:
        items = items[: a.limit]
    gs, embed, index, genai_client, captured = setup(a.index, a.model, a.min_score, a.graph)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{a.name}.jsonl"
    print(f"[{a.name}] {len(items)} preguntas · índice {a.index} · modelo {a.model} · umbral {a.min_score}"
          f" · generación {'sí' if not a.no_generate else 'no'}", file=sys.stderr)

    def work(it):
        return run_item(it, gs, embed, index, genai_client, captured, a.judge_model, not a.no_generate)

    results = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for k, r in enumerate(ex.map(work, items), 1):
            results.append(r)
            print(f"\r  {k}/{len(items)}", end="", file=sys.stderr)
    print(file=sys.stderr)
    with open(path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {"name": a.name, "index": a.index, "model": a.model, "min_score": a.min_score, "graph": a.graph,
            "judge": a.judge_model, "battery_sha256": _sha(BATTERY), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "generate": not a.no_generate, "queries": a.queries}
    (OUT / f"{a.name}.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print_summary(a.name, results)


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ======================================================================================
# Resumen y comparación
# ======================================================================================
METRICS = ["criticos", "criticos_borrador", "doc@6", "pasaje@6", "pag@6", "mrr", "fuentes", "cobertura", "fiel", "cita_unidad", "rechazo_ok", "veredicto_ok"]


def load_run(name: str) -> dict[str, dict]:
    return {r["id"]: r for r in (json.loads(l) for l in (OUT / f"{name}.jsonl").read_text(encoding="utf-8").splitlines())}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def print_summary(name: str, results: list[dict]) -> None:
    print(f"\n== {name} ==")
    for m in METRICS:
        v = [r[m] for r in results if m in r]
        if v:
            print(f"  {m:<13} {mean(v):.3f}  (n={len(v)})")
    lat = [r["ms_total"] for r in results if "ms_total" in r]
    errs = sum(1 for r in results if r.get("error"))
    if lat:
        print(f"  ms p50 {statistics.median(lat):.0f} · p95 {sorted(lat)[int(0.95 * (len(lat) - 1))]:.0f} · errores {errs}")


def paired_ci(a: list[float], b: list[float], n: int = 2000, seed: int = 13) -> tuple[float, float, float]:
    d = [y - x for x, y in zip(a, b)]
    if not d:
        return (float("nan"),) * 3
    rng = random.Random(seed)
    boots = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(n))
    return sum(d) / len(d), boots[int(0.025 * n)], boots[int(0.975 * n)]


def cmd_compare(a) -> None:
    base, new = load_run(a.base), load_run(a.new)
    items = {it["id"]: it for it in load_battery()}
    ids = [i for i in items if i in base and i in new]
    L = [f"# {a.new} frente a {a.base}", "",
         f"{len(ids)} preguntas emparejadas. IC 95 % por bootstrap pareado (2.000 remuestreos).", "",
         "| Métrica | " + a.base + " | " + a.new + " | Δ | IC 95 % | n |", "|---|---:|---:|---:|---|---:|"]
    for m in METRICS:
        pairs = [(base[i][m], new[i][m]) for i in ids if m in base[i] and m in new[i]]
        if not pairs:
            continue
        x, y = [p[0] for p in pairs], [p[1] for p in pairs]
        d, lo, hi = paired_ci(x, y)
        L.append(f"| {m} | {mean(x):.3f} | {mean(y):.3f} | {d:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {len(pairs)} |")

    for name, run in ((a.base, base), (a.new, new)):
        lat = [run[i]["ms_total"] for i in ids if "ms_total" in run[i]]
        errs = sum(1 for i in ids if run[i].get("error"))
        if lat:
            L.append(f"\n{name}: latencia p50 {statistics.median(lat):.0f} ms · p95 "
                     f"{sorted(lat)[int(0.95 * (len(lat) - 1))]:.0f} ms · errores {errs}")

    L += ["", "## Por tipo de pregunta", "",
          "| Tipo | n | pasaje@6 " + a.base + " → " + a.new + " | cobertura " + a.base + " → " + a.new +
          " | fiel " + a.base + " → " + a.new + " |", "|---|---:|---|---|---|"]
    by = defaultdict(list)
    for i in ids:
        by[items[i]["tipo"]].append(i)
    for t, tids in by.items():
        def f(run, m):
            v = mean([run[i].get(m) for i in tids])
            return "—" if v is None else f"{v:.2f}"
        L.append(f"| {t} | {len(tids)} | {f(base,'pasaje@6')} → {f(new,'pasaje@6')} | "
                 f"{f(base,'cobertura')} → {f(new,'cobertura')} | {f(base,'fiel')} → {f(new,'fiel')} |")

    L += ["", "## Preguntas que cambian", ""]
    for i in ids:
        b, n = base[i], new[i]
        db = (n.get("pasaje@6", 0) or 0) - (b.get("pasaje@6", 0) or 0)
        dc = (n.get("cobertura") or 0) - (b.get("cobertura") or 0)
        if abs(db) >= 1 or abs(dc) >= 0.5:
            L.append(f"- **{i}** ({items[i]['tipo']}): pasaje@6 {b.get('pasaje@6')} → {n.get('pasaje@6')}, "
                     f"cobertura {b.get('cobertura')} → {n.get('cobertura')} · {items[i]['pregunta'][:90]}")
    text = "\n".join(L)
    out = OUT / f"compare_{a.base}_vs_{a.new}.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n→ {out}")


PAIR_PROMPT = """Eres un evaluador experto en normativa europea de sostenibilidad. Compara dos {que}
para la misma pregunta. No sabes qué sistema produjo cada una. El orden es aleatorio.

PREGUNTA:
{pregunta}
{hechos}
=== {etiqueta} X ===
{x}

=== {etiqueta} Y ===
{y}

Criterio: {criterio}
Devuelve SOLO un JSON: {{"mejor": "X" | "Y" | "empate", "motivo": "una frase"}}"""

CRIT_ANSWER = ("gana la respuesta más correcta y útil para quien pregunta: que transmita los hechos clave, "
               "que no afirme cosas falsas o no respaldadas, que cite la norma y el artículo correctos. "
               "Una respuesta larga no es mejor por serlo. Empate solo si son equivalentes de verdad.")
CRIT_CONTEXT = ("gana el conjunto de fragmentos que permite responder MEJOR a la pregunta con la normativa: "
                "más pertinente, más completo, de la norma y el artículo adecuados. Empate si son equivalentes "
                "o si ninguno sirve.")


def _fmt_context(rec: dict) -> str:
    frs = rec.get("recuperados") or []
    if not frs:
        return "(ningún fragmento)"
    return "\n\n".join(f"[{i}] {f.get('title','').split('/')[-1]} "
                        f"({(f.get('unit_label') + ' · ') if f.get('unit_label') else ''}p.{f.get('page')})\n"
                        f"{(f.get('content') or '')[:1200]}" for i, f in enumerate(frs, 1))


def cmd_pairwise(a) -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    base, new = load_run(a.base), load_run(a.new)
    items = {it["id"]: it for it in load_battery()}
    ids = [i for i in base if i in new]

    def one(i):
        b, n = base[i], new[i]
        flip = zlib.crc32(i.encode()) % 2 == 1          # reproducible, independiente de los hilos
        if a.context:
            xb, xn, etiqueta, que, crit = _fmt_context(b), _fmt_context(n), "FRAGMENTOS", "conjuntos de fragmentos recuperados", CRIT_CONTEXT
        else:
            xb, xn, etiqueta, que, crit = b.get("respuesta") or "(vacía)", n.get("respuesta") or "(vacía)", "RESPUESTA", "respuestas", CRIT_ANSWER
        x, y = (xn, xb) if flip else (xb, xn)
        it = items.get(i)
        hechos = ("\nHECHOS CLAVE (referencia):\n" + "\n".join(f"- {h}" for h in it["hechos_clave"]) + "\n") if it else ""
        prompt = PAIR_PROMPT.format(que=que, pregunta=it["pregunta"] if it else b.get("pregunta", ""),
                                    hechos=hechos, etiqueta=etiqueta, x=x, y=y, criterio=crit)
        for _ in range(3):
            try:
                r = client.models.generate_content(model=a.judge_model, contents=prompt,
                        config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"))
                v = json.loads(r.text)
                m = v.get("mejor")
                winner = "empate" if m == "empate" else ((a.new if m == "X" else a.base) if flip else (a.base if m == "X" else a.new))
                return i, winner, v.get("motivo", "")
            except Exception:                               # noqa: BLE001
                time.sleep(3)
        return i, "error", ""

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        res = list(ex.map(one, ids))
    from collections import Counter
    c = Counter(w for _, w, _ in res)
    by = defaultdict(Counter)
    for i, w, _ in res:
        by[(items.get(i) or {}).get("tipo", "real")][w] += 1
    L = [f"# {'Contexto' if a.context else 'Respuesta'}: {a.new} contra {a.base} (juez a ciegas, orden aleatorio)", "",
         f"{len(res)} comparaciones · gana {a.new}: **{c[a.new]}** · gana {a.base}: **{c[a.base]}** · "
         f"empate: {c['empate']} · error: {c['error']}", "",
         "| Tipo | gana " + a.new + " | gana " + a.base + " | empate |", "|---|---:|---:|---:|"]
    for t, cc in sorted(by.items()):
        L.append(f"| {t} | {cc[a.new]} | {cc[a.base]} | {cc['empate']} |")
    L += ["", f"## Donde gana {a.base}", ""] + [f"- **{i}**: {m}" for i, w, m in res if w == a.base]
    text = "\n".join(L)
    out = OUT / f"pairwise_{'ctx_' if a.context else ''}{a.base}_vs_{a.new}.md"
    out.write_text(text, encoding="utf-8")
    (OUT / f"pairwise_{'ctx_' if a.context else ''}{a.base}_vs_{a.new}.jsonl").write_text(
        "\n".join(json.dumps({"id": i, "gana": w, "motivo": m}, ensure_ascii=False) for i, w, m in res), encoding="utf-8")
    print(text[:3000])
    print(f"\n→ {out}")


def score_judgement(rec: dict, item: dict, j: dict) -> None:
    rec["juez"] = j
    for k in ("cobertura", "fiel", "rechazo_ok"):
        rec.pop(k, None)
    if "hechos" in j:
        n = max(1, len(item["hechos_clave"]))
        val = {"presente": 1.0, "parcial": 0.5}
        rec["cobertura"] = sum(val.get(h.get("estado"), 0.0) for h in j["hechos"]) / n
        rec["fiel"] = float(not j.get("afirmaciones_no_soportadas") and not j.get("contradice_hechos"))
        if item["debe_rechazar"]:
            rec["rechazo_ok"] = float(bool(j.get("rechazo_correcto")))


def cmd_rejudge(a) -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    run = load_run(a.name)
    frags = load_run(a.fragments_from) if a.fragments_from else run
    items = {it["id"]: it for it in load_battery()}

    def one(i):
        rec = dict(run[i])
        docs = frags[i]["recuperados"]
        assert all("content" in d for d in docs), f"{i}: faltan los textos de los fragmentos"
        if a.fragments_from:          # comprobación: la búsqueda repetida es la misma que se usó
            same = [(d["title"], d["page"]) for d in docs] == [(d["title"], d["page"]) for d in run[i]["recuperados"]]
            rec["fragmentos_identicos"] = same
        score_judgement(rec, items[i], judge(client, a.judge_model, items[i], rec.get("respuesta") or "", docs))
        return rec

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        out = list(ex.map(one, list(run)))
    errs = sum(1 for r in out if "error" in (r.get("juez") or {}))
    diff = sum(1 for r in out if r.get("fragmentos_identicos") is False)
    with open(OUT / f"{a.out}.jsonl", "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{a.out}: {len(out)} juzgadas con {a.judge_model} · errores del juez {errs} · fragmentos distintos {diff}")
    print_summary(a.out, out)


def cmd_add_critical(a) -> None:
    """Calcula `criticos` en una ejecución ya hecha (sin llamadas a ningún modelo)."""
    run = load_run(a.name)
    frags = load_run(a.fragments_from) if a.fragments_from else run
    items = {it["id"]: it for it in load_battery()}
    for i, r in run.items():
        r["criticos"] = critical_count(r.get("respuesta") or "", frags[i]["recuperados"], items[i]["pregunta"])
    with open(OUT / f"{a.name}.jsonl", "w", encoding="utf-8") as fh:
        for r in run.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{a.name}: criticos medio {mean([r['criticos'] for r in run.values()]):.2f}")


def cmd_split_draft(a) -> None:
    """Convierte los BORRADORES de una ejecución con harness en una ejecución propia (lo que
    se habría servido sin la capa de reparación) para juzgarla con `rejudge`."""
    run = load_run(a.name)
    items = {it["id"]: it for it in load_battery()}
    with open(OUT / f"{a.out}.jsonl", "w", encoding="utf-8") as fh:
        for i, r in run.items():
            d = {k: v for k, v in r.items() if k not in ("juez", "cobertura", "fiel", "rechazo_ok", "criticos_borrador")}
            d["respuesta"] = r.get("borrador") or r.get("respuesta")
            d["criticos"] = r.get("criticos_borrador", r.get("criticos"))
            exp_units = {norm_unit(f["articulo"]) for f in items[i]["fuentes_esperadas"] if f["articulo"]} - {None}
            if exp_units:
                d["cita_unidad"] = float(bool(exp_units & units_in(d["respuesta"])))
            if items[i]["modo"] == "auditor":
                m = re.search(r"VEREDICTO:\s*(cumple parcialmente|no evaluable|no cumple|cumple)", d["respuesta"] or "", re.I)
                d["veredicto"] = m.group(1).lower() if m else None
                d["veredicto_ok"] = float(d["veredicto"] == items[i]["veredicto_esperado"])
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"{a.out}: borradores de {a.name}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--name", required=True)
    r.add_argument("--index", required=True, help="nombre de índice Pinecone o memory:<ruta.npz>")
    r.add_argument("--model", required=True)
    r.add_argument("--min-score", type=float, required=True)
    r.add_argument("--judge-model", default="gemini-3.1-pro-preview")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--no-generate", action="store_true", help="solo recuperación")
    r.add_argument("--graph", help="ruta al grafo normativo (activa la expansión por grafo)")
    r.add_argument("--queries", help="fichero de preguntas sin referencia (en vez de la batería)")
    r.add_argument("--only", help="ids separados por comas")
    r.add_argument("--limit", type=int)
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("new")
    pw = sub.add_parser("pairwise")
    pw.add_argument("base")
    pw.add_argument("new")
    pw.add_argument("--context", action="store_true", help="comparar fragmentos recuperados, no respuestas")
    pw.add_argument("--judge-model", default="gemini-3.1-pro-preview")
    pw.add_argument("--workers", type=int, default=6)
    rj = sub.add_parser("rejudge")
    rj.add_argument("name")
    rj.add_argument("--fragments-from", help="ejecución solo-búsqueda con los textos de los fragmentos")
    rj.add_argument("--judge-model", required=True)
    rj.add_argument("--out", required=True)
    rj.add_argument("--workers", type=int, default=6)
    ac = sub.add_parser("add-critical")
    ac.add_argument("name")
    ac.add_argument("--fragments-from")
    sd = sub.add_parser("split-draft")
    sd.add_argument("name")
    sd.add_argument("--out", required=True)
    a = ap.parse_args()
    {"run": cmd_run, "compare": cmd_compare, "pairwise": cmd_pairwise, "rejudge": cmd_rejudge,
     "add-critical": cmd_add_critical, "split-draft": cmd_split_draft}[a.cmd](a)


if __name__ == "__main__":
    main()
