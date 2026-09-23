"""
Grafo normativo determinista, en memoria. Sustituye a Neo4j.

La instancia de Neo4j que usaba el sistema no existe (NXDOMAIN; ver DEBT-14) y la
consulta fallaba en silencio. Un grafo de este tamaño —unos cientos de nodos— no
justifica una base de datos de grafos: cabe en un JSON versionado junto al código.

Nodos: unidades normativas de cada documento (artículo, anexo, requisito NEIS,
contenido o tema GRI), con los ids de los vectores que las componen.
Aristas, extraídas del propio texto (sin LLM, sin nada que alucinar):
  REFERENCIA  «de conformidad con el artículo 8», «artículos 10 y 11», «anexo»,
              «E1-6», «Contenido 305-1» — dentro del mismo documento
  MODIFICA    marcadores ▼M1/▼M2 del texto consolidado de EUR-Lex

Uso en la búsqueda (`expand`): a partir de los fragmentos recuperados que tienen
unidad normativa, se toman las unidades a las que remiten (un salto), se recuperan
sus fragmentos y se añaden los `limit` más parecidos a la pregunta. Las unidades a
las que remite casi todo (más de HUB_IN_DEGREE referencias) no se expanden: traerlas a
cada respuesta sería ruido, no contexto. En el corpus de 2026-09 solo cae ahí el
anexo I de las NEIS (34 referencias).

Referencia explícita (`explicit`): si la pregunta nombra una unidad («artículo 9 de la
CSDDD», «E1-6», «GRI 305-1»), se añaden directamente sus fragmentos. Los vectores no
distinguen números: ante «¿Qué contiene el artículo 9?» la búsqueda semántica devolvía
los arts. 2, 3 y 38, pero no el 9. Si el artículo no dice de qué norma es y la pregunta
no lo deja claro, no se adivina.

Se activa con la variable de entorno RAG_NORMATIVE_GRAPH=<ruta al JSON>. Si está
activada y el fichero no carga, se registra como ERROR (nunca en silencio).
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from src.config import logger

HUB_IN_DEGREE = 25          # unidades más referenciadas que esto no se expanden
MAX_CANDIDATES = 60         # vectores candidatos a traer por consulta

_ART_REF = re.compile(
    r"\bart[íi]culos?\s+(\d+)(?:\s*(bis|ter|qu[áa]ter|quinquies))?"
    r"((?:\s*(?:,|y|a|e)\s*\d+(?:\s*(?:bis|ter|qu[áa]ter))?)*)", re.I)
_EXTERNAL = re.compile(r"^[^.;]{0,70}?\b(de la Directiva|del Reglamento|de dicho Reglamento|de dicha Directiva|"
                       r"de la Decisión|del Convenio|del Pacto|de la Carta|del Tratado|de la Declaración)", re.I)
_ANEXO_REF = re.compile(r"\banexos?(?:\s+([IVX]+)\b)?", re.I)
_ESRS_REF = re.compile(r"\b((?:[EGS]\d|SBM|IRO|GOV|BP)-\d{1,2})\b")
_GRI_REF = re.compile(r"\bContenidos?\s+(\d{1,3}-\d{1,2})\b")

MOD_LABEL = {"M1": "Directiva (UE) 2025/794 (stop-the-clock)", "M2": "Directiva (UE) 2026/470 (Ómnibus I)"}

# Referencias en la PREGUNTA del usuario
_Q_ART = re.compile(r"\bart(?:[íi]culo|\.)\s*(\d+)(?:\s*(bis|ter|qu[áa]ter|quinquies))?", re.I)
_Q_ESRS = re.compile(r"\b((?:[EGS]\d|SBM|IRO|GOV|BP)-\d{1,2})\b", re.I)
_Q_GRI = re.compile(r"\b(?:GRI\s*)?(\d{3}-\d{1,2}|[1-3]-\d{1,2})\b")
_DOC_HINTS = {
    "02_NORMATIVAS/01_CSDDD": ("csddd", "diligencia debida", "2024/1760"),
    "02_NORMATIVAS/02_CSDR": ("csrd", "2022/2464", "2013/34", "información sobre sostenibilidad"),
}


def _refs(text: str, source_family: str) -> set[str]:
    out: set[str] = set()
    for m in _ART_REF.finditer(text):
        if _EXTERNAL.match(text[m.end():m.end() + 90]):
            continue                                    # «artículo 2 de la Directiva 2013/34»: otra norma
        suf = f".{m.group(2).lower()}" if m.group(2) else ""
        out.add(f"art.{m.group(1)}{suf}")
        tail = m.group(3) or ""
        nums = re.findall(r"\d+", tail)
        if re.search(r"\ba\b", tail) and nums:          # «artículos 7 a 16»
            lo, hi = int(m.group(1)), int(nums[-1])
            if 0 < hi - lo <= 20:
                out.update(f"art.{k}" for k in range(lo, hi + 1))
        else:
            out.update(f"art.{k}" for k in nums)
    for m in _ANEXO_REF.finditer(text):
        out.add(f"anexo.{(m.group(1) or 'I').upper()}")
    if source_family == "neis":
        out.update(f"esrs.{c.upper()}" for c in _ESRS_REF.findall(text))
    if source_family == "gri":
        out.update(f"gri.{c}" for c in _GRI_REF.findall(text))
    return out


def build(pairs) -> dict:
    """pairs: [(vector_id, ChunkV2)] de src.chunking_v2.chunk_corpus."""
    from src.chunking_v2 import family
    nodes: dict[str, dict] = {}
    for vid, c in pairs:
        if not c.article:
            continue
        key = f"{c.source}#{c.article}"
        n = nodes.setdefault(key, {"source": c.source, "unit": c.article, "label": c.unit_label,
                                   "ids": [], "pages": [c.page, c.page_end]})
        n["ids"].append(vid)
        n["pages"] = [min(n["pages"][0], c.page), max(n["pages"][1], c.page_end)]

    edges: Counter = Counter()
    for vid, c in pairs:
        if not c.article:
            continue
        src = f"{c.source}#{c.article}"
        for r in _refs(c.text, family(c.source)):
            dst = f"{c.source}#{r}"
            if dst != src and dst in nodes:
                edges[(src, dst, "REFERENCIA")] += 1
        for mod in c.modifications:
            if mod.startswith("M"):
                mkey = f"{c.source}#mod.{mod}"
                nodes.setdefault(mkey, {"source": c.source, "unit": f"mod.{mod}",
                                        "label": MOD_LABEL.get(mod, mod), "ids": [], "pages": [0, 0]})
                edges[(mkey, src, "MODIFICA")] += 1
    return {
        "version": 1,
        "nodes": nodes,
        "edges": [{"from": a, "to": b, "type": t, "n": n} for (a, b, t), n in sorted(edges.items())],
    }


# ======================================================================================
# Uso en línea
# ======================================================================================
class NormativeGraph:
    def __init__(self, data: dict):
        self.nodes = data["nodes"]
        self.out: dict[str, set[str]] = defaultdict(set)
        indeg: Counter = Counter()
        for e in data["edges"]:
            if e["type"] == "REFERENCIA":
                self.out[e["from"]].add(e["to"])
                indeg[e["to"]] += 1
        self.hubs = {k for k, v in indeg.items() if v > HUB_IN_DEGREE}

    def explicit_units(self, query: str) -> list[str]:
        """Claves de nodo de las unidades que la pregunta nombra expresamente."""
        q = query.lower()
        keys: list[str] = []
        docs = [d for d, hints in _DOC_HINTS.items() if any(h in q for h in hints)]
        if len(docs) == 1:                       # «artículo 9» solo si la norma está clara
            for m in _Q_ART.finditer(query):
                uid = f"art.{m.group(1)}" + (f".{m.group(2).lower()}" if m.group(2) else "")
                keys.append(f"{docs[0]}#{uid}")
        for code in _Q_ESRS.findall(query):
            keys.append(f"02_NORMATIVAS/03_NEIS#esrs.{code.upper()}")
        if "gri" in q or "contenido" in q:
            for code in _Q_GRI.findall(query):
                keys += [k for k, n in self.nodes.items() if n["unit"] == f"gri.{code}"]
        return [k for k in dict.fromkeys(keys) if k in self.nodes]

    def explicit(self, index, query: str, query_embedding, limit: int = 3) -> list[dict]:
        """Fragmentos de las unidades nombradas en la pregunta, los más parecidos primero."""
        keys = self.explicit_units(query)
        if not keys:
            return []
        ids = [vid for k in keys for vid in self.nodes[k]["ids"]][:MAX_CANDIDATES]
        return self._fetch_scored(index, ids, query_embedding, seen=set(), limit=limit, category="referencia")

    def _fetch_scored(self, index, ids, query_embedding, seen, limit, category) -> list[dict]:
        try:
            fetched = index.fetch(ids=ids).vectors or {}
        except Exception:
            logger.error("Grafo normativo: fetch fallido", exc_info=True)
            return []
        q = np.asarray(query_embedding, dtype="float32")
        scored = []
        for vid, v in fetched.items():
            md = dict(getattr(v, "metadata", None) or {})
            if (md.get("source"), (md.get("text") or "")[:80]) in seen:
                continue
            scored.append((float(np.dot(q, np.asarray(v.values, dtype="float32"))), md))
        scored.sort(key=lambda x: -x[0])
        return [{
            "content": md.get("text", ""), "title": md.get("source", ""), "category": category,
            "score": sc, "page": md.get("page"), "total_pages": md.get("total_pages"),
            "page_end": md.get("page_end"), "article": md.get("article"), "unit_label": md.get("unit_label"),
        } for sc, md in scored[:limit]]

    def neighbours(self, source: str, unit: str) -> set[str]:
        return {k for k in self.out.get(f"{source}#{unit}", set()) if k not in self.hubs}

    def expand(self, index, docs: list[dict], query_embedding, limit: int = 3) -> list[dict]:
        seeds = {(d.get("title"), d.get("article")) for d in docs if d.get("article")}
        if not seeds:
            return []
        seed_keys = {f"{s}#{u}" for s, u in seeds}
        targets = set()
        for s, u in seeds:
            targets |= self.neighbours(s, u)
        targets -= seed_keys
        ids = [vid for t in sorted(targets) for vid in self.nodes[t]["ids"]][:MAX_CANDIDATES]
        if not ids:
            return []
        seen = {(d.get("title"), (d.get("content") or "")[:80]) for d in docs}
        return self._fetch_scored(index, ids, query_embedding, seen, limit, category="grafo")


_GRAPH: NormativeGraph | None = None
_LOADED = False
_LOCK = threading.Lock()


def get_graph() -> NormativeGraph | None:
    """El grafo si RAG_NORMATIVE_GRAPH apunta a un JSON válido; None si está desactivado.
    Si está activado y no carga, ERROR en el log: el fallo silencioso de Neo4j no se repite."""
    global _GRAPH, _LOADED
    if _LOADED:
        return _GRAPH
    with _LOCK:                                   # gunicorn con hilos: cargar una sola vez
        if _LOADED:
            return _GRAPH
        path = os.getenv("RAG_NORMATIVE_GRAPH", "").strip()
        if path:
            try:
                _GRAPH = NormativeGraph(json.loads(Path(path).read_text(encoding="utf-8")))
                logger.info("Grafo normativo cargado: %d nodos, %s", len(_GRAPH.nodes), path)
            except Exception:
                logger.error("Grafo normativo ACTIVADO pero no se pudo cargar %s", path, exc_info=True)
                _GRAPH = None
        _LOADED = True
    return _GRAPH
