# src/kb_experiment.py
"""
Experimento factorial de construcción de la base de conocimiento (Línea 1 · RAG avanzado).

Pregunta: ¿cómo interactúan la estrategia de segmentación, la jerarquía del texto
normativo y el enriquecimiento de la base de conocimiento con la calidad de la
recuperación y la precisión de la cita, en un corpus regulatorio en español?

Factores
--------
  chunking   fixed | semantic_free | semantic_struct | semantic_struct_ctx | small_to_big | propositions
  embedding  cualquier nombre de sentence-transformers (lista; p. ej. EN monolingüe, ML-384d, ML-768d)
  query      literal | paraphrased   (campos `pregunta` / `parafrasis` del golden set)
  graph      none | skeleton | emergent   (H1.3; `emergent` requiere --llm)

Métricas por consulta
---------------------
  hit@k, recall@k, precision@k, mrr         a nivel documento(+página, tolerancia ±1) — como rag_benchmark
  cit_recall@art, cit_precision@art          NUEVA: a nivel de ARTÍCULO. La etiqueta que citaría el sistema
                                             (artículo en el que ARRANCA el fragmento) frente al artículo gold.
  cit_recall@art_lenient                     variante laxa: cuenta si el fragmento ABARCA el artículo gold.
  Subconjuntos: por tipo (conceptual/operational/resource/comparativa/multi_salto), dificultad, categoría.

Índice: en memoria (numpy, coseno). Reproducible, sin Pinecone, sin coste.
Salida:  <out>.rows.csv (una fila por consulta×condición), <out>.build.csv (coste de construcción),
         <out>.report.md (tablas pivot + contrastes pre-registrados con IC bootstrap pareado y Wilcoxon).

Golden set (extensión retro-compatible del formato de rag_benchmark)
------------------------------------------------------------------
{
  "id": "gs-011", "pregunta": "...", "parafrasis": "...",          # parafrasis: opcional (condición paraphrased)
  "categoria": "CSDDD", "tipo": "multi_salto", "dificultad": "alta",
  "fuentes_esperadas": [
     {"documento": "02_NORMATIVAS/CSDDD", "pagina": 12, "articulo": "Artículo 8"},   # articulo: opcional
     {"documento": "02_NORMATIVAS/CSDDD", "pagina": 15, "articulo": "art. 10.2"}
  ]
}

Uso
---
  python -m src.kb_experiment --corpus ./corpus --golden benchmarks/golden_set_v1/t1_recuperacion.jsonl \
      --embedding-models sentence-transformers/all-MiniLM-L6-v2 \
                         sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
                         sentence-transformers/paraphrase-multilingual-mpnet-base-v2 \
      --chunking fixed semantic_free semantic_struct semantic_struct_ctx small_to_big \
      --graph none skeleton --k 6 --out results/kb_exp

Nunca toca el índice de producción.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import pickle
import random
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np

from src import corpus_pipeline as cp
from src.rag_benchmark import _source_match, _page_match

logger = logging.getLogger("kb_experiment")

CHUNKING_STRATEGIES = ("fixed", "semantic_free", "semantic_struct", "semantic_struct_ctx",
                       "small_to_big", "propositions")
GRAPH_MODES = ("none", "skeleton", "emergent")
QUERY_CONDITIONS = ("literal", "paraphrased")

FIXED_WORDS = 400          # constantes de producción (src/rag_service.py)
FIXED_OVERLAP = 50
CHILD_SENTENCES = 2        # small_to_big: frases por hijo
GRAPH_EXPAND = 3           # fragmentos extra que aporta el grafo (1 salto)
DEFAULT_K = 6
PAGE_TOL = 1
BOOTSTRAP_N = 2000
SEED = 13


# ======================================================================================
# Identificador de artículo (unidad de cita jurídica)
# ======================================================================================
_ART_RE = re.compile(r"\bart(?:[íi]culo|\.)?\s*(\d+)\s*(bis|ter|qu[áa]ter)?", re.I)
_ANEXO_RE = re.compile(r"\banexo\s+([IVXLC]+|\d+)\b", re.I)
_GRI_RE = re.compile(r"\b(?:contenido|disclosure)?\s*(\d{3}-\d{1,2})\b", re.I)
_ESRS_RE = re.compile(r"\b([EGS]\d?-\d{1,2}|ESRS\s*\d)\b")


def article_id(label: str) -> str | None:
    """Normaliza una etiqueta humana ('Artículo 8.3', 'art. 10 bis', 'Anexo I', '306-2', 'E1-6')
    a un id de artículo. Los apartados (8.3 -> art.8) se ignoran: la unidad es el artículo."""
    if not label:
        return None
    s = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    m = _ART_RE.search(s)
    if m:
        return f"art.{m.group(1)}{('.' + m.group(2).lower()) if m.group(2) else ''}"
    m = _ANEXO_RE.search(s)
    if m:
        return f"anexo.{m.group(1).upper()}"
    m = _ESRS_RE.search(s)
    if m:
        return f"esrs.{m.group(1).replace(' ', '').upper()}"
    m = _GRI_RE.search(s)
    if m:
        return f"gri.{m.group(1)}"
    return None


def article_of_section(section_path: str) -> str | None:
    """El artículo (o anexo) más profundo del path de sección 'TÍTULO I · CAPÍTULO II · Artículo 8 …'."""
    if not section_path:
        return None
    parts = [p.strip() for p in section_path.split("·")]
    for p in reversed(parts):
        a = article_id(p)
        if a:
            return a
    return None


# ======================================================================================
# Estructuras
# ======================================================================================
@dataclass
class ExpChunk:
    text: str                  # texto literal (lo que se citaría)
    embed_text: str            # lo que se embebe (== text salvo *_ctx)
    source: str
    page: int
    page_end: int
    section: str
    article: str | None        # artículo en el que ARRANCA (etiqueta de cita)
    articles_spanned: list[str] = field(default_factory=list)   # todos los que abarca
    start: int = 0
    end: int = 0
    parent: int | None = None  # small_to_big / propositions: índice del padre
    entities: list[str] = field(default_factory=list)


@dataclass
class ExpExpected:
    documento: str
    pagina: int | None = None
    articulo: str | None = None          # id normalizado (article_id)


@dataclass
class ExpItem:
    id: str
    pregunta: str
    parafrasis: str | None
    categoria: str
    tipo: str
    dificultad: str
    expected: list[ExpExpected]

    @property
    def gold_articles(self) -> set[tuple[str, str]]:
        return {(e.documento, e.articulo) for e in self.expected if e.articulo}


def load_golden(path: str) -> list[ExpItem]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    rows = json.loads(raw) if raw.startswith("[") else [json.loads(l) for l in raw.splitlines() if l.strip()]
    items = []
    for r in rows:
        exp = [ExpExpected(e["documento"], e.get("pagina"), article_id(e.get("articulo") or ""))
               for e in r.get("fuentes_esperadas", [])]
        items.append(ExpItem(str(r["id"]), r["pregunta"], r.get("parafrasis") or None,
                             r.get("categoria", "general"), r.get("tipo", "conceptual"),
                             r.get("dificultad", "media"), exp))
    return items


# ======================================================================================
# Lectura de corpus (una vez) — texto, spans, headings por documento
# ======================================================================================
@dataclass
class Doc:
    path: Path
    root: Path
    source: str
    text: str
    spans: list[tuple[int, int, int]]
    info: cp.DocInfo


def load_corpus(root: Path, limit: int | None = None) -> list[Doc]:
    docs = []
    for p in cp.iter_documents(root):
        try:
            text, spans = cp._read_document(p)
        except Exception as e:                     # noqa: BLE001
            logger.warning("No se pudo leer %s: %s", p, e)
            continue
        if not text.strip():
            continue
        di = cp._analyze_document(text, spans, p, root, None)
        docs.append(Doc(p, root, di.source, text, spans, di))
        if limit and len(docs) >= limit:
            break
    logger.info("Corpus: %d documentos", len(docs))
    return docs


def _articles_between(headings: list[cp.Heading], start: int, end: int) -> list[str]:
    """Artículos cuyo encabezado cae dentro de [start, end) más el vigente al inicio."""
    out: list[str] = []
    current = article_of_section(cp._section_path_of(start, headings))
    if current:
        out.append(current)
    for h in headings:
        if start < h.start < end:
            a = article_id(h.label)
            if a and a not in out:
                out.append(a)
    return out


# ======================================================================================
# Estrategias de chunking
# ======================================================================================
def _mk(doc: Doc, text: str, start: int, end: int, embed_text: str | None = None,
        parent: int | None = None) -> ExpChunk:
    section = cp._section_path_of(start, doc.info.headings)
    return ExpChunk(
        text=text, embed_text=embed_text or text, source=doc.source,
        page=cp._page_of(start, doc.spans), page_end=cp._page_of(max(start, end - 1), doc.spans),
        section=section, article=article_of_section(section),
        articles_spanned=_articles_between(doc.info.headings, start, end),
        start=start, end=end, parent=parent,
    )


def chunk_fixed(doc: Doc, words: int = FIXED_WORDS, overlap: int = FIXED_OVERLAP) -> list[ExpChunk]:
    """Ventana fija de palabras con solape — reproduce la estrategia de producción
    (extract_pdf_chunks) pero conservando offsets para poder asignar página y artículo."""
    tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", doc.text)]
    out, i = [], 0
    step = max(1, words - overlap)
    while i < len(tokens):
        win = tokens[i:i + words]
        s, e = win[0][0], win[-1][1]
        out.append(_mk(doc, doc.text[s:e], s, e))
        if i + words >= len(tokens):
            break
        i += step
    return out


def chunk_semantic_free(doc: Doc, percentile: int = cp.BREAKPOINT_PERCENTILE,
                        min_chars: int = cp.MIN_CHARS, max_chars: int = cp.MAX_CHARS) -> list[ExpChunk]:
    """Chunking semántico por breakpoint SIN restricción estructural: frases sobre el texto
    completo; los encabezados son frases más; un grupo puede cruzar artículos."""
    sentences = cp._split_sentences(doc.text, 0)
    if not sentences:
        return []
    bps = cp._find_breakpoints(sentences, percentile)
    groups = cp._adjust_sizes(cp._group_sentences(sentences, bps), min_chars, max_chars)
    return [_mk(doc, " ".join(s.text for s in g).strip(), g[0].start, g[-1].end) for g in groups if g]


def chunk_semantic_struct(doc: Doc, with_context: bool, percentile: int = cp.BREAKPOINT_PERCENTILE,
                          min_chars: int = cp.MIN_CHARS, max_chars: int = cp.MAX_CHARS) -> list[ExpChunk]:
    """Chunking semántico restringido por la jerarquía — misma lógica que
    corpus_pipeline.chunk_document (los breakpoints nunca cruzan un encabezado; tablas y
    figuras atómicas) pero sobre el texto ya leído, conservando offsets para asignar
    página y artículos abarcados. with_context=True embebe la cabecera contextual
    [título · tipo año emisor · sección] delante del texto literal."""
    out: list[ExpChunk] = []

    def _emit(content: str, start: int, end: int) -> None:
        section = cp._section_path_of(start, doc.info.headings)
        ctx = cp._build_context(doc.info, section)
        emb = f"{ctx}\n\n{content}" if (with_context and ctx) else content
        out.append(_mk(doc, content, start, end, embed_text=emb))

    for block in cp._segment_structure(doc.text, doc.info.headings):
        if block.kind == "heading":
            continue
        if block.kind in ("table", "figure"):
            _emit(block.text, block.start, block.start + len(block.text))
            continue
        sentences = cp._split_sentences(block.text, block.start)
        if not sentences:
            continue
        bps = cp._find_breakpoints(sentences, percentile)
        for g in cp._adjust_sizes(cp._group_sentences(sentences, bps), min_chars, max_chars):
            content = " ".join(s.text for s in g).strip()
            if content:
                _emit(content, g[0].start, g[-1].end)
    return out


def chunk_small_to_big(doc: Doc, child_sentences: int = CHILD_SENTENCES, **kw) -> tuple[list[ExpChunk], list[ExpChunk]]:
    """Hijos = ventanas de `child_sentences` frases dentro de cada chunk estructural; se recupera
    por el hijo y se devuelve el PADRE (chunk semántico+estructura, con contexto)."""
    parents = chunk_semantic_struct(doc, with_context=True, **kw)
    children: list[ExpChunk] = []
    for pi, p in enumerate(parents):
        sents = cp._split_sentences(p.text, p.start)
        if len(sents) <= child_sentences:
            children.append(ExpChunk(p.text, p.embed_text, p.source, p.page, p.page_end, p.section,
                                     p.article, p.articles_spanned, p.start, p.end, parent=pi))
            continue
        for i in range(0, len(sents), child_sentences):
            g = sents[i:i + child_sentences]
            txt = " ".join(s.text for s in g)
            ctx = p.embed_text[:p.embed_text.find("\n\n")] if "\n\n" in p.embed_text else ""
            children.append(ExpChunk(txt, f"{ctx}\n\n{txt}" if ctx else txt, p.source, p.page, p.page_end,
                                     p.section, p.article, p.articles_spanned, g[0].start, g[-1].end, parent=pi))
    return children, parents


def chunk_propositions(doc: Doc, genai_client, **kw) -> tuple[list[ExpChunk], list[ExpChunk]]:
    """Proposiciones atómicas (Dense X Retrieval) generadas por LLM a partir del chunk estructural;
    se recupera por proposición y se devuelve el padre. Requiere --llm."""
    parents = chunk_semantic_struct(doc, with_context=True, **kw)
    parents_cp = [cp.Chunk(content=p.text, block_type="text", source=p.source, section=p.section,
                           page=p.page, page_end=p.page_end, total_pages=doc.info.total_pages,
                           primary_category=doc.info.primary_category) for p in parents]
    props_cp = cp.build_propositions(parents_cp, genai_client)
    # build_propositions numera parent_id sobre los chunks que superan MIN_CHARS (no sobre todos)
    targets_idx = [i for i, p in enumerate(parents) if len(p.text) >= cp.MIN_CHARS]
    children = []
    for pr in props_cp:
        m = re.search(r"(\d+)$", pr.parent_id or "")
        t = int(m.group(1)) if m else 0
        pi = targets_idx[t] if t < len(targets_idx) else 0
        p = parents[pi]
        children.append(ExpChunk(pr.content, pr.content, p.source, p.page, p.page_end, p.section,
                                 p.article, p.articles_spanned, p.start, p.end, parent=pi))
    return children, parents


def build_chunks(strategy: str, docs: list[Doc], genai_client=None) -> tuple[list[ExpChunk], list[ExpChunk] | None, dict]:
    """Devuelve (unidades_indexadas, padres_o_None, coste)."""
    t0 = time.time()
    units: list[ExpChunk] = []
    parents_all: list[ExpChunk] | None = None
    llm_calls = 0
    for doc in docs:
        if strategy == "fixed":
            units.extend(chunk_fixed(doc))
        elif strategy == "semantic_free":
            units.extend(chunk_semantic_free(doc))
        elif strategy == "semantic_struct":
            units.extend(chunk_semantic_struct(doc, with_context=False))
        elif strategy == "semantic_struct_ctx":
            units.extend(chunk_semantic_struct(doc, with_context=True))
        elif strategy in ("small_to_big", "propositions"):
            if strategy == "propositions":
                if genai_client is None:
                    raise RuntimeError("propositions requiere --llm (cliente Gemini)")
                ch, pa = chunk_propositions(doc, genai_client)
                llm_calls += len(pa)
            else:
                ch, pa = chunk_small_to_big(doc)
            parents_all = parents_all or []
            offset = len(parents_all)
            for c in ch:
                c.parent = (c.parent or 0) + offset
            parents_all.extend(pa)
            units.extend(ch)
        else:
            raise ValueError(strategy)
    cost = {
        "n_units": len(units), "n_parents": len(parents_all) if parents_all else len(units),
        "mean_chars_unit": round(float(np.mean([len(u.text) for u in units])) if units else 0, 1),
        "median_chars_unit": round(float(np.median([len(u.text) for u in units])) if units else 0, 1),
        "chunks_crossing_articles": sum(1 for u in units if len(u.articles_spanned) > 1),
        "build_seconds": round(time.time() - t0, 2), "llm_calls": llm_calls,
    }
    return units, parents_all, cost


# ======================================================================================
# Índice denso en memoria (+ BM25 mínimo opcional para híbrido)
# ======================================================================================
class DenseIndex:
    def __init__(self, units: list[ExpChunk]):
        self.units = units
        t0 = time.time()
        self.vecs = cp.embed_texts([u.embed_text for u in units], is_query=False) if units else np.zeros((0, 1))
        self.embed_seconds = round(time.time() - t0, 2)

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        if not self.units:
            return []
        q = cp.embed_texts([query], is_query=True)[0]
        sims = self.vecs @ q
        idx = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in idx]


_TOK_RE = re.compile(r"[a-záéíóúüñ0-9]+", re.I)


class BM25:
    """Okapi BM25 mínimo (sin dependencias) para el canal léxico del modo híbrido."""
    def __init__(self, units: list[ExpChunk], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [[t.lower() for t in _TOK_RE.findall(u.text)] for u in units]
        self.avgdl = (sum(len(d) for d in self.docs) / max(1, len(self.docs))) or 1.0
        self.df: dict[str, int] = defaultdict(int)
        self.tf: list[dict[str, int]] = []
        for d in self.docs:
            c: dict[str, int] = defaultdict(int)
            for t in d:
                c[t] += 1
            self.tf.append(c)
            for t in c:
                self.df[t] += 1
        self.N = len(self.docs)

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        q = [t.lower() for t in _TOK_RE.findall(query)]
        scores = np.zeros(self.N)
        for t in set(q):
            if t not in self.df:
                continue
            idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i, tf in enumerate(self.tf):
                f = tf.get(t, 0)
                if f:
                    dl = len(self.docs[i])
                    scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        idx = np.argsort(-scores)[:k]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > 0]


def rrf(*rankings: list[tuple[int, float]], c: int = 60) -> list[tuple[int, float]]:
    acc: dict[int, float] = defaultdict(float)
    for r in rankings:
        for rank, (i, _) in enumerate(r, 1):
            acc[i] += 1.0 / (c + rank)
    return sorted(acc.items(), key=lambda x: -x[1])


# ======================================================================================
# Grafo normativo: esqueleto (artículos + referencias cruzadas) vs emergente (entidades LLM)
# ======================================================================================
_XREF_RE = re.compile(
    r"\b(?:art(?:[íi]culos?|s?\.)\s*)(\d+(?:\s*(?:bis|ter))?)(?:\s*(?:,|y|a|al|hasta)\s*(\d+))?", re.I)
_XREF_ANEXO_RE = re.compile(r"\banexos?\s+([IVXLC]+|\d+)\b", re.I)


class NormativeGraph:
    """Grafo de fragmentos. Modo `skeleton`: nodos = artículos preexistentes por documento,
    aristas = referencias cruzadas detectadas en el texto ('de conformidad con el artículo 8');
    expansión = fragmentos de los artículos referenciados por los fragmentos recuperados.
    Modo `emergent`: aristas por entidades compartidas extraídas por LLM (enrich_chunks)."""

    def __init__(self, units: list[ExpChunk], parents: list[ExpChunk] | None, mode: str):
        self.mode = mode
        self.targets = parents if parents is not None else units
        self.by_article: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, t in enumerate(self.targets):
            if t.article:
                self.by_article[(t.source, t.article)].append(i)
        self.adj: dict[int, set[int]] = defaultdict(set)
        if mode == "skeleton":
            self._build_skeleton()
        elif mode == "emergent":
            self._build_emergent()
        self.n_edges = sum(len(v) for v in self.adj.values()) // 2

    def _build_skeleton(self):
        for i, t in enumerate(self.targets):
            refs: set[str] = set()
            for m in _XREF_RE.finditer(t.text):
                a = article_id("art. " + m.group(1))
                if a and a != t.article:
                    refs.add(a)
                if m.group(2):
                    b = article_id("art. " + m.group(2))
                    if b and b != t.article:
                        refs.add(b)
            for m in _XREF_ANEXO_RE.finditer(t.text):
                refs.add(f"anexo.{m.group(1).upper()}")
            for a in refs:
                for j in self.by_article.get((t.source, a), []):
                    if j != i:
                        self.adj[i].add(j); self.adj[j].add(i)

    def _build_emergent(self):
        by_entity: dict[str, list[int]] = defaultdict(list)
        for i, t in enumerate(self.targets):
            for e in t.entities:
                by_entity[e].append(i)
        for members in by_entity.values():
            if 1 < len(members) <= 25:            # entidades demasiado frecuentes no informan
                for a in members:
                    for b in members:
                        if a != b:
                            self.adj[a].add(b)

    def expand(self, seed_targets: list[int], query_vec_scores: np.ndarray | None, limit: int) -> list[int]:
        cand: set[int] = set()
        for s in seed_targets:
            cand |= self.adj.get(s, set())
        cand -= set(seed_targets)
        if not cand:
            return []
        if query_vec_scores is not None:
            return sorted(cand, key=lambda i: -float(query_vec_scores[i]))[:limit]
        return list(cand)[:limit]


# ======================================================================================
# Recuperación por condición
# ======================================================================================
@dataclass
class Retrieved:
    unit: ExpChunk
    score: float


def retrieve(index: DenseIndex, bm25: BM25 | None, graph: NormativeGraph | None,
             parents: list[ExpChunk] | None, parent_index: DenseIndex | None,
             query: str, k: int) -> list[Retrieved]:
    """Devuelve exactamente k unidades objetivo (chunks, o padres en small_to_big/propositions).

    Presupuesto igualado entre condiciones: sin grafo, los k mejores del ranking denso (o
    híbrido RRF); con grafo, los (k − e) mejores como semillas + hasta e vecinos a 1 salto
    re-ordenados por similitud con la consulta, rellenando con el ranking denso si el grafo
    aporta menos de e. Así todas las condiciones se evalúan con el mismo k."""
    use_graph = graph is not None and graph.mode != "none"
    budget = (k - GRAPH_EXPAND) if use_graph else k
    dense = index.search(query, k * 4)
    ranking = rrf(dense, bm25.search(query, k * 4)) if bm25 else dense
    # colapsar a unidades objetivo conservando el orden (small_to_big/propositions → padre)
    ordered: list[int] = []
    score: dict[int, float] = {}
    for i, sc in ranking:
        t = index.units[i].parent if parents is not None else i
        if t is None or t in score:
            continue
        score[t] = float(sc); ordered.append(t)
    targets = parents if parents is not None else index.units
    chosen = ordered[:budget]
    if use_graph:
        tgt_index = parent_index if parents is not None else index
        qv = cp.embed_texts([query], is_query=True)[0]
        qscores = tgt_index.vecs @ qv
        for j in graph.expand(chosen, qscores, GRAPH_EXPAND):
            if j not in chosen and len(chosen) < k:
                chosen.append(j); score.setdefault(j, float(qscores[j]))
    for t in ordered:                                  # relleno hasta k
        if len(chosen) >= k:
            break
        if t not in chosen:
            chosen.append(t)
    return [Retrieved(targets[t], score.get(t, 0.0)) for t in chosen[:k]]


# ======================================================================================
# Métricas
# ======================================================================================
def _doc_relevant(r: ExpChunk, exp: list[ExpExpected]) -> bool:
    return any(_source_match(r.source, e.documento) and _page_match(r.page, e.pagina, PAGE_TOL) for e in exp)


def evaluate(item: ExpItem, results: list[Retrieved], k: int) -> dict:
    top = results[:k]
    rel = [_doc_relevant(r.unit, item.expected) for r in top]
    found_docs = set()
    for r in top:
        for e in item.expected:
            if _source_match(r.unit.source, e.documento) and _page_match(r.unit.page, e.pagina, PAGE_TOL):
                found_docs.add((e.documento, e.pagina))
    n_exp = max(1, len(item.expected))
    first = next((i for i, x in enumerate(rel, 1) if x), None)
    m = {
        "hit": float(any(rel)),
        "recall": len(found_docs) / n_exp,
        "precision": (sum(rel) / len(top)) if top else 0.0,
        "mrr": (1.0 / first) if first else 0.0,
    }
    gold = item.gold_articles
    if gold:
        cited = {(r.unit.source, r.unit.article) for r in top if r.unit.article}
        spanned = {(r.unit.source, a) for r in top for a in r.unit.articles_spanned}

        def _hit(gs, cs):
            return sum(1 for (gd, ga) in gs if any(_source_match(cs_src, gd) and cs_a == ga for cs_src, cs_a in cs))

        m["cit_recall_art"] = _hit(gold, cited) / len(gold)
        m["cit_recall_art_lenient"] = _hit(gold, spanned) / len(gold)
        cited_list = [(r.unit.source, r.unit.article) for r in top if r.unit.article]
        m["cit_precision_art"] = (sum(1 for (s, a) in cited_list
                                      if any(_source_match(s, gd) and a == ga for gd, ga in gold)) / len(cited_list)) if cited_list else 0.0
        # all-or-nothing: útil para preguntas multi-artículo
        m["cit_all_art"] = float(m["cit_recall_art"] >= 0.999)
    return m


# ======================================================================================
# Estadística: bootstrap pareado + Wilcoxon
# ======================================================================================
def paired_bootstrap(a: np.ndarray, b: np.ndarray, n: int = BOOTSTRAP_N, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    d = a - b
    if len(d) == 0:
        return {"mean_diff": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0}
    boots = np.array([rng.choice(d, size=len(d), replace=True).mean() for _ in range(n)])
    out = {"mean_diff": round(float(d.mean()), 4), "ci_low": round(float(np.percentile(boots, 2.5)), 4),
           "ci_high": round(float(np.percentile(boots, 97.5)), 4), "n": int(len(d))}
    try:
        from scipy.stats import wilcoxon
        if np.any(d != 0):
            out["wilcoxon_p"] = round(float(wilcoxon(a, b, zero_method="wilcox").pvalue), 4)
        else:
            out["wilcoxon_p"] = 1.0
    except Exception:                               # noqa: BLE001
        out["wilcoxon_p"] = float("nan")
    return out


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(((p, k) for k, p in pvals.items() if not math.isnan(p)))
    m, adj, prev = len(items), {}, 0.0
    for i, (p, k) in enumerate(items):
        v = min(1.0, max(prev, (m - i) * p))
        adj[k] = round(v, 4); prev = v
    return adj


# ======================================================================================
# Orquestación
# ======================================================================================
METRICS = ("hit", "recall", "precision", "mrr", "cit_recall_art", "cit_precision_art",
           "cit_recall_art_lenient", "cit_all_art")


def run_experiment(corpus: str, golden: str, embedding_models: list[str], chunkings: list[str],
                   graphs: list[str], k: int = DEFAULT_K, hybrid: bool = False, llm: bool = False,
                   limit_docs: int | None = None, cache_dir: str | None = None,
                   out: str = "results/kb_exp") -> None:
    random.seed(SEED); np.random.seed(SEED)
    root = Path(corpus)
    docs = load_corpus(root, limit_docs)
    items = load_golden(golden)
    n_para = sum(1 for it in items if it.parafrasis)
    n_art = sum(1 for it in items if it.gold_articles)
    logger.info("Golden: %d ítems · %d con paráfrasis · %d con artículo gold", len(items), n_para, n_art)

    genai_client = cp._get_genai_client() if llm else None
    cache = Path(cache_dir) if cache_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    builds: list[dict] = []
    for model in embedding_models:
        cp.set_embedding_model(model)
        for strategy in chunkings:
            key = re.sub(r"[^a-z0-9]+", "_", f"{model}_{strategy}".lower())
            cfile = cache / f"{key}.pkl" if cache else None
            if cfile and cfile.exists():
                units, parents, cost = pickle.loads(cfile.read_bytes())
                logger.info("cache: %s", cfile.name)
            else:
                logger.info("build: %s × %s", model, strategy)
                units, parents, cost = build_chunks(strategy, docs, genai_client)
                if llm and "emergent" in graphs:
                    # entidades para el grafo emergente (sobre las unidades objetivo)
                    tgt = parents if parents is not None else units
                    cps = [cp.Chunk(content=t.text, block_type="text", source=t.source, section=t.section,
                                    page=t.page, page_end=t.page_end, total_pages=0, primary_category="")
                           for t in tgt]
                    cp.enrich_chunks(cps, genai_client)
                    for t, c in zip(tgt, cps):
                        t.entities = list(c.entities)
                    cost["llm_calls"] += len(cps)
                if cfile:
                    cfile.write_bytes(pickle.dumps((units, parents, cost)))
            index = DenseIndex(units)
            parent_index = DenseIndex(parents) if parents is not None else None
            bm25 = BM25(units) if hybrid else None
            builds.append({"embedding": model, "chunking": strategy, **cost,
                           "embed_seconds": index.embed_seconds, "dim": int(index.vecs.shape[1]) if len(units) else 0})
            for gmode in graphs:
                if gmode == "emergent" and not llm:
                    logger.warning("graph=emergent requiere --llm; se omite")
                    continue
                graph = NormativeGraph(units, parents, gmode) if gmode != "none" else None
                for cond in QUERY_CONDITIONS:
                    for it in items:
                        q = it.pregunta if cond == "literal" else it.parafrasis
                        if not q:
                            continue
                        res = retrieve(index, bm25, graph, parents, parent_index, q, k)
                        m = evaluate(it, res, k)
                        rows.append({"embedding": model, "chunking": strategy, "graph": gmode,
                                     "query": cond, "hybrid": hybrid, "k": k, "item": it.id,
                                     "tipo": it.tipo, "dificultad": it.dificultad, "categoria": it.categoria,
                                     "multi_source": len(it.expected) > 1,
                                     "n_edges": graph.n_edges if graph else 0, **m,
                                     "top_sources": " | ".join(f"{r.unit.source}#p{r.unit.page}:{r.unit.article or '-'}"
                                                               for r in res[:k])})
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(f"{out}.rows.csv", rows)
    _write_csv(f"{out}.build.csv", builds)
    Path(f"{out}.report.md").write_text(report(rows, builds, k), encoding="utf-8")
    logger.info("Escrito %s.rows.csv, %s.build.csv, %s.report.md", out, out, out)
    print(report(rows, builds, k))


def _write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        Path(path).write_text("", encoding="utf-8"); return
    keys = list({k: None for r in rows for k in r})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)


# ======================================================================================
# Informe: pivots + contrastes pre-registrados (H1.1–H1.4)
# ======================================================================================
def _mean(rows: list[dict], metric: str) -> float | None:
    vals = [r[metric] for r in rows if metric in r]
    return round(float(np.mean(vals)), 3) if vals else None


def _sel(rows: list[dict], **f) -> list[dict]:
    return [r for r in rows if all(r.get(k) == v for k, v in f.items())]


def _vec(rows: list[dict], metric: str) -> tuple[np.ndarray, list[str]]:
    d = {r["item"]: r[metric] for r in rows if metric in r}
    ids = sorted(d)
    return np.array([d[i] for i in ids], dtype=float), ids


def _paired(rows_a: list[dict], rows_b: list[dict], metric: str) -> dict:
    a, ia = _vec(rows_a, metric); b, ib = _vec(rows_b, metric)
    common = sorted(set(ia) & set(ib))
    if not common:
        return {"mean_diff": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "n": 0, "wilcoxon_p": float("nan")}
    da = {i: v for i, v in zip(ia, a)}; db = {i: v for i, v in zip(ib, b)}
    return paired_bootstrap(np.array([da[i] for i in common]), np.array([db[i] for i in common]))


def report(rows: list[dict], builds: list[dict], k: int) -> str:
    L: list[str] = []
    L.append(f"# KB experiment report (k={k}, n_rows={len(rows)})\n")
    models = sorted({r["embedding"] for r in rows})
    chunkings = [c for c in CHUNKING_STRATEGIES if any(r["chunking"] == c for r in rows)]
    graphs = [g for g in GRAPH_MODES if any(r["graph"] == g for r in rows)]

    L.append("## Build cost\n")
    L.append("| embedding | chunking | units | parents | mean chars | median chars | crossing articles | build s | embed s | llm calls |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for b in builds:
        L.append(f"| {b['embedding'].split('/')[-1]} | {b['chunking']} | {b['n_units']} | {b['n_parents']} | {b['mean_chars_unit']} | "
                 f"{b['median_chars_unit']} | {b['chunks_crossing_articles']} | {b['build_seconds']} | {b['embed_seconds']} | {b['llm_calls']} |")

    for metric in METRICS:
        if not any(metric in r for r in rows):
            continue
        L.append(f"\n## {metric}@{k} — chunking × query (graph=none), by embedding\n")
        for model in models:
            L.append(f"**{model}**\n")
            L.append("| chunking | " + " | ".join(QUERY_CONDITIONS) + " | Δ(para−lit) |")
            L.append("|---|" + "---:|" * (len(QUERY_CONDITIONS) + 1))
            for c in chunkings:
                vals = [_mean(_sel(rows, embedding=model, chunking=c, graph="none", query=q), metric) for q in QUERY_CONDITIONS]
                delta = (vals[1] - vals[0]) if None not in vals else None
                L.append(f"| {c} | " + " | ".join("–" if v is None else f"{v:.3f}" for v in vals) +
                         f" | {'–' if delta is None else f'{delta:+.3f}'} |")
            L.append("")
        if len(graphs) > 1:
            L.append(f"### {metric}@{k} — graph mode (literal), by embedding × chunking\n")
            L.append("| embedding | chunking | " + " | ".join(graphs) + " |")
            L.append("|---|---|" + "---:|" * len(graphs))
            for model in models:
                for c in chunkings:
                    vals = [_mean(_sel(rows, embedding=model, chunking=c, graph=g, query="literal"), metric) for g in graphs]
                    L.append(f"| {model.split('/')[-1]} | {c} | " + " | ".join("–" if v is None else f"{v:.3f}" for v in vals) + " |")
            L.append("")

    # ---- Por subconjuntos (tipo / dificultad / multi_source) para la métrica de cita
    for metric in ("cit_recall_art", "recall"):
        if not any(metric in r for r in rows):
            continue
        L.append(f"\n## {metric}@{k} by tipo (graph=none, literal)\n")
        tipos = sorted({r["tipo"] for r in rows})
        L.append("| embedding | chunking | " + " | ".join(tipos) + " |")
        L.append("|---|---|" + "---:|" * len(tipos))
        for model in models:
            for c in chunkings:
                vals = [_mean(_sel(rows, embedding=model, chunking=c, graph="none", query="literal", tipo=t), metric) for t in tipos]
                L.append(f"| {model.split('/')[-1]} | {c} | " + " | ".join("–" if v is None else f"{v:.3f}" for v in vals) + " |")

    # ---- Contrastes pre-registrados
    L.append("\n## Pre-registered contrasts (paired bootstrap 95% CI · Wilcoxon · Holm-adjusted)\n")
    contrasts: list[tuple[str, list[dict], list[dict], str]] = []
    for model in models:
        base = dict(embedding=model, graph="none")
        if {"semantic_struct", "fixed"} <= set(chunkings):
            for q in QUERY_CONDITIONS:
                contrasts.append((f"H1.1 semantic_struct − fixed [{q}] ({model.split('/')[-1]})",
                                  _sel(rows, chunking="semantic_struct", query=q, **base),
                                  _sel(rows, chunking="fixed", query=q, **base), "cit_recall_art"))
        if {"semantic_struct", "semantic_free"} <= set(chunkings):
            contrasts.append((f"H1.1 semantic_struct − semantic_free [literal] ({model.split('/')[-1]})",
                              _sel(rows, chunking="semantic_struct", query="literal", **base),
                              _sel(rows, chunking="semantic_free", query="literal", **base), "cit_recall_art"))
        if {"semantic_struct_ctx", "semantic_struct"} <= set(chunkings):
            for q in QUERY_CONDITIONS:
                contrasts.append((f"H1.2 ctx − no-ctx [{q}] ({model.split('/')[-1]})",
                                  _sel(rows, chunking="semantic_struct_ctx", query=q, **base),
                                  _sel(rows, chunking="semantic_struct", query=q, **base), "recall"))
        if {"semantic_struct", "fixed"} <= set(chunkings):
            for q in QUERY_CONDITIONS:
                contrasts.append((f"H1.2 semantic_struct − fixed on recall [{q}] ({model.split('/')[-1]})",
                                  _sel(rows, chunking="semantic_struct", query=q, **base),
                                  _sel(rows, chunking="fixed", query=q, **base), "recall"))
        for c in chunkings:
            if "skeleton" in graphs:
                for subset, flt in (("all", {}), ("multi-source", {"multi_source": True})):
                    contrasts.append((f"H1.3 skeleton − none [{c}, literal, {subset}] ({model.split('/')[-1]})",
                                      _sel(rows, chunking=c, graph="skeleton", query="literal", embedding=model, **flt),
                                      _sel(rows, chunking=c, graph="none", query="literal", embedding=model, **flt),
                                      "cit_recall_art"))
            if "emergent" in graphs and "skeleton" in graphs:
                contrasts.append((f"H1.3 skeleton − emergent [{c}, literal] ({model.split('/')[-1]})",
                                  _sel(rows, chunking=c, graph="skeleton", query="literal", embedding=model),
                                  _sel(rows, chunking=c, graph="emergent", query="literal", embedding=model),
                                  "cit_recall_art"))
    if len(models) > 1:
        en = [m for m in models if "multilingual" not in m.lower() and "e5" not in m.lower() and "bge-m3" not in m.lower()]
        ml = [m for m in models if m not in en]
        for a in ml:
            for b in en:
                for c in chunkings:
                    contrasts.append((f"H1.4 {a.split('/')[-1]} − {b.split('/')[-1]} [{c}, literal]",
                                      _sel(rows, embedding=a, chunking=c, graph="none", query="literal"),
                                      _sel(rows, embedding=b, chunking=c, graph="none", query="literal"), "recall"))

    L.append("| contrast | metric | n | mean Δ | 95% CI | Wilcoxon p | Holm p |")
    L.append("|---|---|---:|---:|---|---:|---:|")
    stats = [(name, metric, _paired(a, b, metric)) for name, a, b, metric in contrasts]
    adj = holm({name: s.get("wilcoxon_p", float("nan")) for name, _, s in stats})
    for name, metric, s in stats:
        L.append(f"| {name} | {metric} | {s['n']} | {s['mean_diff']:+.3f} | [{s['ci_low']:+.3f}, {s['ci_high']:+.3f}] | "
                 f"{s.get('wilcoxon_p', float('nan')):.4f} | {adj.get(name, float('nan')):.4f} |")

    # ---- Interacción H1.2: (semantic_struct_ctx − fixed)[paraphrased] − (semantic_struct_ctx − fixed)[literal]
    if {"semantic_struct_ctx", "fixed"} <= set(chunkings):
        L.append("\n### H1.2 interaction: gain of semantic_struct_ctx over fixed, paraphrased vs literal (recall)\n")
        L.append("| embedding | gain literal | gain paraphrased | interaction (para − lit) | 95% CI |")
        L.append("|---|---:|---:|---:|---|")
        for model in models:
            base = dict(embedding=model, graph="none")
            gl = _paired(_sel(rows, chunking="semantic_struct_ctx", query="literal", **base),
                         _sel(rows, chunking="fixed", query="literal", **base), "recall")
            gp = _paired(_sel(rows, chunking="semantic_struct_ctx", query="paraphrased", **base),
                         _sel(rows, chunking="fixed", query="paraphrased", **base), "recall")
            # bootstrap de la diferencia de diferencias
            a1, i1 = _vec(_sel(rows, chunking="semantic_struct_ctx", query="literal", **base), "recall")
            b1, _ = _vec(_sel(rows, chunking="fixed", query="literal", **base), "recall")
            a2, i2 = _vec(_sel(rows, chunking="semantic_struct_ctx", query="paraphrased", **base), "recall")
            b2, _ = _vec(_sel(rows, chunking="fixed", query="paraphrased", **base), "recall")
            common = sorted(set(i1) & set(i2))
            if common and len(a1) == len(b1) and len(a2) == len(b2):
                d1 = dict(zip(i1, a1 - b1)); d2 = dict(zip(i2, a2 - b2))
                inter = paired_bootstrap(np.array([d2[i] for i in common]), np.array([d1[i] for i in common]))
                L.append(f"| {model.split('/')[-1]} | {gl['mean_diff']:+.3f} | {gp['mean_diff']:+.3f} | "
                         f"{inter['mean_diff']:+.3f} | [{inter['ci_low']:+.3f}, {inter['ci_high']:+.3f}] |")
    L.append("\n_Decision rules (pre-registered): a hypothesis is supported when the 95% paired-bootstrap CI of the "
             "primary contrast excludes 0 in the predicted direction and the Holm-adjusted Wilcoxon p < 0.05; "
             "H1.2 additionally requires the interaction CI to exclude 0._\n")
    return "\n".join(L)


# ======================================================================================
# CLI
# ======================================================================================
def _cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--golden", required=True)
    ap.add_argument("--embedding-models", nargs="+", default=[
        "sentence-transformers/all-MiniLM-L6-v2",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"])
    ap.add_argument("--chunking", nargs="+", default=["fixed", "semantic_free", "semantic_struct",
                                                     "semantic_struct_ctx", "small_to_big"], choices=CHUNKING_STRATEGIES)
    ap.add_argument("--graph", nargs="+", default=["none", "skeleton"], choices=GRAPH_MODES)
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    ap.add_argument("--hybrid", action="store_true", help="fusiona canal BM25 por RRF")
    ap.add_argument("--llm", action="store_true", help="habilita propositions y graph=emergent (Gemini)")
    ap.add_argument("--limit-docs", type=int)
    ap.add_argument("--cache", help="directorio para cachear chunks por (modelo, estrategia)")
    ap.add_argument("--out", default="results/kb_exp")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_experiment(a.corpus, a.golden, a.embedding_models, a.chunking, a.graph, a.k, a.hybrid, a.llm,
                   a.limit_docs, a.cache, a.out)


if __name__ == "__main__":
    _cli()
