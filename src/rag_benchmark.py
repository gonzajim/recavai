# src/rag_benchmark.py
"""
Benchmark de calidad del RAG sobre el corpus.

Mide, para un "golden set" de preguntas con respuesta de referencia y fuentes
esperadas (documento + página), cómo de bien recupera y responde una configuración
concreta. Sirve para tener una LÍNEA BASE del índice actual y comparar contra
cualquier cambio de arquitectura (chunking, modelo de embeddings, routing, grafo,
re-ranking, propositions...).

Métricas
--------
Recuperación (siempre):
  hit@k        1 si al menos una fuente esperada aparece en el top-k
  recall@k     fuentes esperadas recuperadas / fuentes esperadas
  precision@k  fragmentos recuperados que son relevantes / k
  mrr          1 / (rango del primer fragmento relevante)
Generación (--generate):
  latency_ms, cost_usd  (Gemini 2.5 Flash)
Juez LLM (--judge, requiere --generate; usa respuesta_ref cuando existe):
  groundedness    la respuesta se apoya SOLO en los fragmentos recuperados (0..1)
  correctness     la respuesta coincide con la respuesta de referencia (0..1)
  citation_ok     las citas [n] apuntan a fragmentos de las fuentes esperadas (0..1)

Formato del golden set  (JSONL, una entrada por línea; también admite array JSON)
--------------------------------------------------------------------------------
{
  "id": "gs-001",
  "pregunta": "¿Qué es la diligencia debida según las Líneas Directrices de la OCDE?",
  "categoria": "general",                         # CSDDD | GRI | general
  "tipo": "conceptual",                           # conceptual | operational | resource
  "dificultad": "media",                          # baja | media | alta
  "respuesta_ref": "",                            # TODO IDPEI
  "fuentes_esperadas": [                           # documento obligatorio; pagina opcional
    {"documento": "03_ESTANDARES/GUIAS OCDE/OCDE_00_Lineas_Directrices_EMN_2023_ES", "pagina": null}
  ],
  "notas_juridicas": ""
}

Uso
---
  # Línea base del índice de producción (mismo modelo con el que se construyó):
  python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl \
      --embedding-model sentence-transformers/all-MiniLM-L6-v2 \
      --out benchmarks/baseline.jsonl

  # Comparar un índice reconstruido con el pipeline nuevo:
  python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl \
      --embedding-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
      --baseline benchmarks/baseline.jsonl --out benchmarks/run_v2.jsonl

  # Con generación + juez LLM (más caro):
  python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl --generate --judge
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("rag_benchmark")

# Precio Gemini 2.5 Flash (USD / 1M tokens) — septiembre 2026
_PRICE_IN = 0.15 / 1_000_000
_PRICE_OUT = 1.25 / 1_000_000

DEFAULT_TOP_K = 12
DEFAULT_MIN_SCORE = 0.55
DEFAULT_MAX_RESULTS = 6
DEFAULT_PAGE_TOL = 1
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
LLM_MODEL = "gemini-2.5-flash"


# ======================================================================================
# Golden set
# ======================================================================================
@dataclass
class Expected:
    documento: str
    pagina: int | None = None


@dataclass
class GoldenItem:
    id: str
    pregunta: str
    categoria: str = "general"
    tipo: str = "conceptual"
    dificultad: str = "media"
    respuesta_ref: str = ""
    fuentes_esperadas: list[Expected] = field(default_factory=list)
    notas_juridicas: str = ""


def load_golden(path: str) -> list[GoldenItem]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    rows: list[dict]
    if raw.startswith("["):
        rows = json.loads(raw)
    else:
        rows = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
    items = []
    for r in rows:
        items.append(GoldenItem(
            id=str(r["id"]),
            pregunta=r["pregunta"],
            categoria=r.get("categoria", "general"),
            tipo=r.get("tipo", "conceptual"),
            dificultad=r.get("dificultad", "media"),
            respuesta_ref=r.get("respuesta_ref", "") or "",
            fuentes_esperadas=[
                Expected(e["documento"], e.get("pagina")) for e in r.get("fuentes_esperadas", [])
            ],
            notas_juridicas=r.get("notas_juridicas", "") or "",
        ))
    return items


# ======================================================================================
# Emparejamiento fuente recuperada <-> fuente esperada
# ======================================================================================
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("_", " ").replace("-", " ").replace("/", " ").replace(".", " ")
    return " ".join(s.split())


def _source_match(retrieved_src: str, expected_doc: str) -> bool:
    a, b = _norm(retrieved_src), _norm(expected_doc)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    inter = len(ta & tb)
    return inter / max(1, len(ta | tb)) >= 0.6


def _page_match(retrieved_page, expected_page, tol: int) -> bool:
    if expected_page is None:
        return True
    try:
        return abs(int(retrieved_page or 0) - int(expected_page)) <= tol
    except (TypeError, ValueError):
        return False


@dataclass
class Retrieved:
    source: str
    page: int
    score: float
    text: str
    section: str = ""


def _is_relevant(r: Retrieved, expected: list[Expected], tol: int) -> bool:
    return any(_source_match(r.source, e.documento) and _page_match(r.page, e.pagina, tol)
              for e in expected)


# ======================================================================================
# Retrieval — modo local (control total sobre modelo/namespace/parámetros)
# ======================================================================================
class LocalRetriever:
    def __init__(self, embedding_model: str, namespace: str, top_k: int, min_score: float,
                 max_results: int, category_filter: bool):
        from src.corpus_pipeline import embed_texts, set_embedding_model
        set_embedding_model(embedding_model)
        self._embed = embed_texts
        self.namespace = namespace
        self.top_k = top_k
        self.min_score = min_score
        self.max_results = max_results
        self.category_filter = category_filter
        try:
            from src.config import pinecone_index
            assert pinecone_index is not None
            self.index = pinecone_index
        except Exception:  # noqa: BLE001
            import os
            from pinecone import Pinecone
            pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
            name = os.environ["PINECONE_INDEX_NAME"]
            self.index = pc.Index(host=name) if name.startswith("http") else pc.Index(name)

    def retrieve(self, question: str) -> list[Retrieved]:
        qv = self._embed([question], is_query=True)[0].tolist()
        kwargs = dict(vector=qv, top_k=self.top_k, include_metadata=True)
        if self.namespace:
            kwargs["namespace"] = self.namespace
        if self.category_filter:
            try:
                from src.rag_service import detect_category_filter
                cf = detect_category_filter(question)
                if cf:
                    kwargs["filter"] = cf
            except Exception:  # noqa: BLE001
                pass
        res = self.index.query(**kwargs)
        out: list[Retrieved] = []
        for m in res.get("matches", []):
            if m.get("score", 0.0) < self.min_score:
                continue
            md = m.get("metadata", {}) or {}
            out.append(Retrieved(
                source=md.get("source") or md.get("title", ""),
                page=md.get("page") or 0,
                score=float(m.get("score", 0.0)),
                text=md.get("text") or md.get("content", ""),
                section=md.get("section", ""),
            ))
            if len(out) >= self.max_results:
                break
        return out


# ======================================================================================
# Retrieval — modo API (contra el backend desplegado; prueba el pipeline real end-to-end)
# ======================================================================================
class ApiRetriever:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def retrieve_and_answer(self, question: str) -> tuple[list[Retrieved], str, int]:
        import requests
        t0 = time.time()
        resp = requests.post(
            f"{self.base_url}/chat_assistant",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json={"message": question}, timeout=120,
        )
        dt = int((time.time() - t0) * 1000)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        retr = [
            Retrieved(source=s.get("title", ""), page=s.get("page") or 0,
                      score=float(s.get("score", 0.0)), text=s.get("excerpt", ""))
            for s in data.get("sources", [])
        ]
        return retr, data.get("response", ""), dt


# ======================================================================================
# Generación local + juez LLM
# ======================================================================================
def _augmented_prompt(question: str, chunks: list[Retrieved]) -> str:
    blocks = [f"[{i+1}] ({c.source}, p.{c.page}) {c.text}" for i, c in enumerate(chunks)]
    return (
        "Responde a la pregunta usando ÚNICAMENTE los extractos numerados. Cita como "
        "[1], [2]... el extracto que respalda cada afirmación. Si los extractos no "
        "bastan, dilo.\n\nEXTRACTOS:\n" + "\n\n".join(blocks) +
        f"\n\nPREGUNTA: {question}"
    )


def _generate(genai_client, question: str, chunks: list[Retrieved]) -> tuple[str, int, float]:
    from google.genai import types
    prompt = _augmented_prompt(question, chunks)
    t0 = time.time()
    resp = genai_client.models.generate_content(
        model=LLM_MODEL, contents=prompt,
        config=types.GenerateContentConfig(temperature=0.0),
    )
    dt = int((time.time() - t0) * 1000)
    um = getattr(resp, "usage_metadata", None)
    cost = 0.0
    if um:
        cost = (getattr(um, "prompt_token_count", 0) * _PRICE_IN
                + getattr(um, "candidates_token_count", 0) * _PRICE_OUT)
    return (resp.text or "").strip(), dt, cost


_JUDGE_PROMPT = (
    "Eres un evaluador. Devuelve SOLO JSON: "
    '{"groundedness": 0.0, "correctness": 0.0, "citation_ok": 0.0}. '
    "groundedness: ¿toda afirmación de la RESPUESTA está respaldada por los EXTRACTOS? (0..1). "
    "correctness: ¿la RESPUESTA coincide en sustancia con la RESPUESTA_REF? (0..1; si REF vacía, pon -1). "
    "citation_ok: ¿las citas [n] de la RESPUESTA apuntan a extractos cuyo documento está en FUENTES_ESPERADAS? (0..1).\n\n"
)


def _judge(genai_client, item: GoldenItem, answer: str, chunks: list[Retrieved]) -> dict:
    from google.genai import types
    ex = "\n".join(f"[{i+1}] ({c.source}, p.{c.page}) {c.text[:500]}" for i, c in enumerate(chunks))
    fe = ", ".join(e.documento for e in item.fuentes_esperadas)
    prompt = (_JUDGE_PROMPT + f"EXTRACTOS:\n{ex}\n\nRESPUESTA:\n{answer}\n\n"
              f"RESPUESTA_REF:\n{item.respuesta_ref or '(vacía)'}\n\nFUENTES_ESPERADAS: {fe}")
    try:
        resp = genai_client.models.generate_content(
            model=LLM_MODEL, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        d = json.loads(resp.text)
        return {k: float(d.get(k, 0.0)) for k in ("groundedness", "correctness", "citation_ok")}
    except Exception as exc:  # noqa: BLE001
        logger.debug("juez fallido: %s", exc)
        return {"groundedness": 0.0, "correctness": 0.0, "citation_ok": 0.0}


# ======================================================================================
# Ejecución
# ======================================================================================
def evaluate_item(item: GoldenItem, retrieved: list[Retrieved], page_tol: int) -> dict:
    k = max(1, len(retrieved))
    rel_flags = [_is_relevant(r, item.fuentes_esperadas, page_tol) for r in retrieved]
    n_rel = sum(rel_flags)
    covered = sum(
        1 for e in item.fuentes_esperadas
        if any(_source_match(r.source, e.documento) and _page_match(r.page, e.pagina, page_tol)
               for r in retrieved)
    )
    first_rank = next((i + 1 for i, f in enumerate(rel_flags) if f), 0)
    return {
        "id": item.id,
        "categoria": item.categoria,
        "tipo": item.tipo,
        "dificultad": item.dificultad,
        "k": len(retrieved),
        "hit@k": 1.0 if n_rel else 0.0,
        "recall@k": covered / max(1, len(item.fuentes_esperadas)),
        "precision@k": n_rel / k,
        "mrr": (1.0 / first_rank) if first_rank else 0.0,
        "retrieved": [{"source": r.source, "page": r.page, "score": round(r.score, 4),
                       "relevant": bool(rel_flags[i])} for i, r in enumerate(retrieved)],
    }


def aggregate(results: list[dict]) -> dict:
    def mean(key, rows):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(statistics.mean(vals), 4) if vals else None

    keys = ["hit@k", "recall@k", "precision@k", "mrr", "groundedness", "correctness",
            "citation_ok", "latency_ms", "cost_usd"]
    out = {"n": len(results), "overall": {k: mean(k, results) for k in keys}}
    for dim in ("categoria", "tipo", "dificultad"):
        groups: dict[str, list[dict]] = {}
        for r in results:
            groups.setdefault(r.get(dim, "?"), []).append(r)
        out[dim] = {g: {k: mean(k, rows) for k in keys if mean(k, rows) is not None}
                    for g, rows in sorted(groups.items())}
    return out


def _fmt(v):
    return "  -  " if v is None else f"{v:6.3f}"


def print_report(agg: dict, baseline: dict | None = None) -> None:
    o = agg["overall"]
    print(f"\n=== BENCHMARK RAG  ·  {agg['n']} preguntas ===")
    cols = ["hit@k", "recall@k", "precision@k", "mrr", "groundedness", "correctness",
            "citation_ok", "latency_ms", "cost_usd"]
    print("  " + "  ".join(f"{c:>12}" for c in cols))
    print("  " + "  ".join(f"{_fmt(o.get(c)):>12}" for c in cols))
    if baseline:
        b = baseline["overall"]
        deltas = []
        for c in cols:
            if o.get(c) is None or b.get(c) is None:
                deltas.append("  -  ")
            else:
                d = o[c] - b[c]
                deltas.append(f"{d:+.3f}")
        print("  " + "  ".join(f"{d:>12}" for d in deltas) + "   (Δ vs baseline)")
    for dim in ("categoria", "tipo", "dificultad"):
        print(f"\n  por {dim}:")
        for g, m in agg[dim].items():
            print(f"    {g:<14} " + "  ".join(f"{k}={_fmt(m.get(k)).strip()}"
                  for k in ("hit@k", "recall@k", "mrr")))


def run(golden_path: str, *, mode: str = "local",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL, namespace: str = "",
        top_k: int = DEFAULT_TOP_K, min_score: float = DEFAULT_MIN_SCORE,
        max_results: int = DEFAULT_MAX_RESULTS, page_tol: int = DEFAULT_PAGE_TOL,
        category_filter: bool = True, generate: bool = False, judge: bool = False,
        api_base_url: str = "", api_token: str = "", baseline_path: str = "",
        out_path: str = "") -> dict:
    golden = load_golden(golden_path)
    logger.info("Golden set: %d preguntas (%d con respuesta_ref)",
                len(golden), sum(1 for g in golden if g.respuesta_ref))

    genai_client = None
    if generate or judge:
        try:
            from src.config import genai_client as _gc
            genai_client = _gc
        except Exception:  # noqa: BLE001
            import os
            from google import genai
            genai_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    if mode == "api":
        retr = ApiRetriever(api_base_url, api_token)
    else:
        retr = LocalRetriever(embedding_model, namespace, top_k, min_score, max_results, category_filter)

    results: list[dict] = []
    for item in golden:
        answer, latency, cost = "", None, None
        if mode == "api":
            chunks, answer, latency = retr.retrieve_and_answer(item.pregunta)
        else:
            chunks = retr.retrieve(item.pregunta)
            if generate and genai_client is not None and chunks:
                answer, latency, cost = _generate(genai_client, item.pregunta, chunks)

        row = evaluate_item(item, chunks, page_tol)
        if latency is not None:
            row["latency_ms"] = latency
        if cost is not None:
            row["cost_usd"] = round(cost, 6)
        if judge and genai_client is not None and answer:
            j = _judge(genai_client, item, answer, chunks)
            row.update({k: v for k, v in j.items() if v >= 0})
            row["answer"] = answer
        results.append(row)
        logger.info("%-8s hit@k=%.0f recall@k=%.2f mrr=%.2f  %s",
                    item.id, row["hit@k"], row["recall@k"], row["mrr"], item.pregunta[:60])

    agg = aggregate(results)
    base = None
    if baseline_path and Path(baseline_path).exists():
        base_rows = [json.loads(ln) for ln in Path(baseline_path).read_text().splitlines() if ln.strip()]
        base = aggregate(base_rows)

    print_report(agg, base)

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        logger.info("Resultados por pregunta -> %s", out_path)
    return agg


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Benchmark de calidad del RAG del corpus.")
    ap.add_argument("--golden", required=True)
    ap.add_argument("--mode", choices=["local", "api"], default="local")
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL,
                    help="DEBE ser el modelo con el que se construyó el índice consultado.")
    ap.add_argument("--namespace", default="")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    ap.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    ap.add_argument("--page-tol", type=int, default=DEFAULT_PAGE_TOL)
    ap.add_argument("--no-category-filter", action="store_true")
    ap.add_argument("--generate", action="store_true", help="Genera respuesta con Gemini (mide latencia/coste).")
    ap.add_argument("--judge", action="store_true", help="Juez LLM: groundedness/correctness/citation_ok (implica --generate).")
    ap.add_argument("--api-base-url", default="https://orchestrator-dev-370417116045.europe-west1.run.app")
    ap.add_argument("--api-token", default="", help="ID token de Firebase (modo api).")
    ap.add_argument("--baseline", default="", help="Fichero .jsonl de una corrida previa para el delta.")
    ap.add_argument("--out", default="", help="Escribe resultados por pregunta a este .jsonl.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(message)s", stream=sys.stdout)
    for n in ("httpx", "httpcore", "urllib3", "huggingface_hub", "sentence_transformers",
              "filelock", "pinecone"):
        logging.getLogger(n).setLevel(logging.WARNING)

    run(args.golden, mode=args.mode, embedding_model=args.embedding_model,
        namespace=args.namespace, top_k=args.top_k, min_score=args.min_score,
        max_results=args.max_results, page_tol=args.page_tol,
        category_filter=not args.no_category_filter, generate=args.generate or args.judge,
        judge=args.judge, api_base_url=args.api_base_url, api_token=args.api_token,
        baseline_path=args.baseline, out_path=args.out)


if __name__ == "__main__":
    _cli()
