# src/corpus_pipeline.py
"""
Pipeline de procesamiento e indexación del corpus normativo (uclm-corpus-roma).

Chunking SEMÁNTICO real por breakpoint de percentil sobre similitud de embeddings
(mismo enfoque que SemanticChunker de LangChain/LlamaIndex) y además CONSCIENTE DE
LA ESTRUCTURA: tablas y figuras se preservan como bloque atómico.

Flujo por documento
-------------------
1. _read_document      PDF/TXT/DOCX -> texto completo + mapa de páginas (offset->página).
                       Las tablas se renderizan como markdown `| ... |` y las figuras
                       con su caption en línea propia, para que _segment_structure sea fiable.
2. _segment_structure  separa bloques `table` (filas `|...|`), `figure` (captions
                       "Tabla N" / "Figura N" / "Table/Figure N") y `text`.
3. _split_sentences    regex sobre .!? con protección de abreviaturas (Dr., Art., Núm., ...).
4. _find_breakpoints   NÚCLEO SEMÁNTICO: codifica cada frase, similitud coseno entre
                       consecutivas, threshold = percentil (100 - breakpoint_percentile)
                       de las similitudes. Con breakpoint_percentile=80 se corta en el
                       20 % de fronteras con MENOR similitud (cambio de tema).
5. _group_sentences    agrupa frases entre cortes.
6. _adjust_sizes       fusiona grupos < min_chars; parte grupos > max_chars
                       RESPETANDO FRONTERA DE FRASE (no por palabras).
7. graph               (opcional) tripletes KG vía LLM + entidades + graph_importance
                       por PageRank sobre el grafo de conocimiento del corpus.
8. upsert              embed_texts(chunk) -> index.upsert en lotes de 100 con metadatos
                       ricos (source, page, block_type, primary_category, graph_importance,
                       triplets, entities).

Parámetros:  breakpoint_percentile=80, min_chars=350, max_chars=1800.  Sin solapamiento.

--------------------------------------------------------------------------------
NOTA DE COHERENCIA (riesgo #1, el más grave)
--------------------------------------------------------------------------------
El índice DEBE construirse y consultarse con EL MISMO modelo de embeddings y la
misma dimensión. Este módulo expone `embed_texts()` como única fuente de verdad.
El backend de producción debe importar y usar ESTA función para su embedding de
consulta, en lugar de `pc.inference.embed(...)` (API hosteada de Pinecone), que
usa un modelo distinto y rompe/degrada la recuperación.

    from src.corpus_pipeline import embed_texts
    query_vec = embed_texts([pregunta], is_query=True)[0]

Idioma (riesgo #2): el corpus es español. El modelo por defecto es
`paraphrase-multilingual-MiniLM-L12-v2` (384 dims, sin prefijos, buen español,
misma dimensión que el índice actual). Alternativa más fuerte: `intfloat/
multilingual-e5-base` (768 dims, requiere prefijos "query:"/"passage:", exige
re-provisionar el índice). Se controla con --embedding-model.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

logger = logging.getLogger("corpus_pipeline")

# ======================================================================================
# Parámetros
# ======================================================================================
BREAKPOINT_PERCENTILE = 80      # corta en el (100 - 80) = percentil 20 de menor similitud
MIN_CHARS = 350                 # grupos más pequeños se fusionan con el vecino
MAX_CHARS = 1800                # grupos más grandes se parten (por frase, no por palabra)
UPSERT_BATCH = 100
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_NAMESPACE = ""          # namespace por defecto de Pinecone (corpus global)
GRAPH_LLM_MODEL = "gemini-2.5-flash"

# Extensiones soportadas
_SUPPORTED = {".pdf", ".txt", ".md", ".docx"}


# ======================================================================================
# Embeddings — ÚNICA FUENTE DE VERDAD (indexación y consulta)
# ======================================================================================
_MODEL_CACHE: dict[str, "SentenceTransformer"] = {}  # noqa: F821
_MODEL_NAME = DEFAULT_EMBEDDING_MODEL


def set_embedding_model(name: str) -> None:
    global _MODEL_NAME
    _MODEL_NAME = name


def _needs_e5_prefix(name: str) -> bool:
    return "e5" in name.lower()


def get_model():
    """Carga perezosa y cacheada del SentenceTransformer."""
    if _MODEL_NAME not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer
        logger.info("Cargando modelo de embeddings: %s", _MODEL_NAME)
        _MODEL_CACHE[_MODEL_NAME] = SentenceTransformer(_MODEL_NAME)
    return _MODEL_CACHE[_MODEL_NAME]


def embed_texts(texts: list[str], is_query: bool = False, batch_size: int = 64) -> np.ndarray:
    """
    Codifica una lista de textos con el modelo del corpus. Normaliza L2 (cosine).
    Para modelos e5-* añade el prefijo requerido ('query: ' / 'passage: ').
    Devuelve np.ndarray (n, dim) float32.
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    model = get_model()
    if _needs_e5_prefix(_MODEL_NAME):
        prefix = "query: " if is_query else "passage: "
        texts = [prefix + t for t in texts]
    vecs = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return vecs.astype(np.float32)


def embedding_dim() -> int:
    m = get_model()
    getter = getattr(m, "get_embedding_dimension", None) or m.get_sentence_embedding_dimension
    return int(getter())


# ======================================================================================
# Estructuras de datos
# ======================================================================================
@dataclass
class Block:
    kind: str          # "text" | "table" | "figure"
    text: str
    start: int         # offset de carácter en el texto completo del documento


@dataclass
class Sentence:
    text: str
    start: int
    end: int


@dataclass
class Chunk:
    content: str
    block_type: str          # "text" | "table" | "figure"
    source: str              # ruta lógica del documento, sin extensión
    page: int
    page_end: int
    total_pages: int
    primary_category: str     # "CSDDD" | "GRI" | "general"
    n_sentences: int = 0
    entities: list[str] = field(default_factory=list)
    triplets: list[list[str]] = field(default_factory=list)
    graph_importance: float = 0.0

    def metadata(self) -> dict:
        return {
            "text": self.content,
            "source": self.source,
            "page": self.page,
            "page_end": self.page_end,
            "total_pages": self.total_pages,
            "primary_category": self.primary_category,
            "block_type": self.block_type,
            "n_sentences": self.n_sentences,
            "entities": self.entities,
            "triplets": [list(t) for t in self.triplets],
            "graph_importance": round(float(self.graph_importance), 6),
        }


# ======================================================================================
# 1) Lectura de documento  ->  (texto completo, page_spans)
#    page_spans: [(page_no, char_start, char_end), ...] sobre el texto completo.
#    Fija el riesgo #7: la página se resuelve por offset, nunca por substring match.
# ======================================================================================
def _table_to_markdown(rows: list[list[str | None]]) -> str:
    rows = [[("" if c is None else str(c).replace("\n", " ").strip()) for c in r] for r in rows if r]
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * ncols) + " |"]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _read_pdf(path: Path) -> tuple[str, list[tuple[int, int, int]]]:
    try:
        import pdfplumber  # extractor con soporte de tablas y layout
    except ImportError:  # pragma: no cover
        return _read_pdf_pypdf(path)

    parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_chunks: list[str] = []
            for tbl in (page.extract_tables() or []):
                md = _table_to_markdown(tbl)
                if md:
                    page_chunks.append(md)
            txt = page.extract_text() or ""
            if txt.strip():
                page_chunks.append(txt.strip())
            page_text = "\n\n".join(page_chunks)
            start = cursor
            parts.append(page_text)
            cursor += len(page_text) + 2  # separador "\n\n"
            spans.append((i, start, cursor))
    return "\n\n".join(parts), spans


def _read_pdf_pypdf(path: Path) -> tuple[str, list[tuple[int, int, int]]]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts, spans, cursor = [], [], 0
    for i, page in enumerate(reader.pages, start=1):
        txt = (page.extract_text() or "").strip()
        start = cursor
        parts.append(txt)
        cursor += len(txt) + 2
        spans.append((i, start, cursor))
    return "\n\n".join(parts), spans


def _read_docx(path: Path) -> tuple[str, list[tuple[int, int, int]]]:
    from docx import Document
    doc = Document(str(path))
    parts: list[str] = []
    body = doc.element.body
    # Recorre párrafos y tablas en orden de aparición
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            t = Paragraph(child, doc).text.strip()
            if t:
                parts.append(t)
        elif tag == "tbl":
            tbl = Table(child, doc)
            rows = [[cell.text for cell in row.cells] for row in tbl.rows]
            md = _table_to_markdown(rows)
            if md:
                parts.append(md)
    text = "\n\n".join(parts)
    return text, [(1, 0, len(text))]  # DOCX no tiene paginación real


def _read_text(path: Path) -> tuple[str, list[tuple[int, int, int]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, [(1, 0, len(text))]


def _read_document(path: Path) -> tuple[str, list[tuple[int, int, int]]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext == ".docx":
        return _read_docx(path)
    if ext in (".txt", ".md"):
        return _read_text(path)
    raise ValueError(f"Extensión no soportada: {ext}")


def _page_of(offset: int, spans: list[tuple[int, int, int]]) -> int:
    for page_no, s, e in spans:
        if s <= offset < e:
            return page_no
    return spans[-1][0] if spans else 0


# ======================================================================================
# 2) Segmentación estructural
#    Tablas: bloques de líneas consecutivas que empiezan por '|'.
#    Figuras: líneas de caption ("Tabla 3", "Figura 12", "Table 4", "Figure 1").
#    El resto es prosa.
# ======================================================================================
_CAPTION_RE = re.compile(
    r"^\s*(tabla|figura|table|figure|cuadro|gr[áa]fico|ilustraci[óo]n)\s+"
    r"(\d+[.:]?\d*)\b.*$",
    re.IGNORECASE,
)


def _segment_structure(full_text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = full_text.splitlines(keepends=True)
    cursor = 0
    i = 0
    buf: list[str] = []
    buf_start = 0

    def flush_text():
        nonlocal buf, buf_start
        if buf:
            txt = "".join(buf).strip()
            if txt:
                blocks.append(Block("text", txt, buf_start))
            buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # --- tabla: >= 2 líneas consecutivas que empiezan por '|' ---
        if stripped.startswith("|"):
            j = i
            tbl_start = cursor
            tbl_lines: list[str] = []
            local = cursor
            while j < len(lines) and lines[j].strip().startswith("|"):
                tbl_lines.append(lines[j])
                local += len(lines[j])
                j += 1
            if len(tbl_lines) >= 2:
                flush_text()
                blocks.append(Block("table", "".join(tbl_lines).strip(), tbl_start))
                cursor = local
                i = j
                continue  # sin añadir al buffer de texto

        # --- figura: línea de caption aislada ---
        if _CAPTION_RE.match(stripped):
            flush_text()
            blocks.append(Block("figure", stripped, cursor))
            cursor += len(line)
            i += 1
            continue

        # --- prosa ---
        if not buf:
            buf_start = cursor
        buf.append(line)
        cursor += len(line)
        i += 1

    flush_text()
    return blocks


# ======================================================================================
# 3) División en frases (con protección de abreviaturas)
# ======================================================================================
_ABBREV = {
    "sr", "sra", "srs", "sras", "dr", "dra", "dres", "prof", "profa",
    "art", "arts", "núm", "num", "pág", "pag", "págs", "pags", "p", "pp",
    "cap", "caps", "apdo", "apdos", "fig", "figs", "tab", "ref", "refs",
    "ed", "eds", "vol", "vols", "op", "cit", "etc", "vs", "ud", "uds",
    "ej", "ee", "uu", "aa", "s", "l", "a", "n", "no", "nº", "cf", "cfr",
    "aprox", "máx", "max", "mín", "min", "coord", "coords", "trad",
    "ene", "feb", "mar", "abr", "jun", "jul", "ago", "sep", "sept", "oct", "nov", "dic",
}
_SENT_END_RE = re.compile(r'([.!?]+)(["»”\')\]]*)(\s+)')


def _split_sentences(text: str, base_offset: int) -> list[Sentence]:
    text = text.replace("\r\n", "\n")
    out: list[Sentence] = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        end = m.end(2)  # tras el signo y comillas de cierre, antes del espacio
        candidate = text[start:end].strip()
        if not candidate:
            start = m.end()
            continue

        # ¿el "punto" pertenece a una abreviatura o a una inicial / enumeración?
        prev_word = re.search(r"(\S+)$", text[:m.start(1)])
        token = prev_word.group(1).lower().strip(".,;:()[]") if prev_word else ""
        after = text[m.end():m.end() + 1]

        is_abbrev = token in _ABBREV or (len(token) == 1 and token.isalpha())
        is_decimal = token.isdigit() and after.isdigit()
        is_enum = re.fullmatch(r"\d{1,3}", token) is not None and text[start:m.start(1)].strip() == token
        # ellipsis: '...' seguido de minúscula -> no cortar
        is_ellipsis = m.group(1) == "..." and after and after.islower()

        if is_abbrev or is_decimal or is_enum or is_ellipsis:
            continue

        s0 = base_offset + start
        s1 = base_offset + end
        out.append(Sentence(candidate, s0, s1))
        start = m.end()

    tail = text[start:].strip()
    if tail:
        out.append(Sentence(tail, base_offset + start, base_offset + len(text)))
    return out


# ======================================================================================
# 4) Detección de puntos de corte semánticos  (NÚCLEO)
# ======================================================================================
def _find_breakpoints(sentences: list[Sentence], breakpoint_percentile: int = BREAKPOINT_PERCENTILE) -> list[int]:
    """
    Devuelve los índices i (1..n-1) donde ARRANCA un nuevo grupo, es decir, donde la
    similitud coseno entre la frase i-1 y la i cae por debajo del umbral.

    umbral = percentil (100 - breakpoint_percentile) de todas las similitudes
    consecutivas. Con breakpoint_percentile=80 -> percentil 20 -> se corta en el
    20 % de fronteras con MENOR similitud.
    """
    n = len(sentences)
    if n < 3:
        return []
    embs = embed_texts([s.text for s in sentences], is_query=False)
    # similitud coseno entre consecutivas (embeddings ya normalizados -> producto punto)
    sims = np.sum(embs[:-1] * embs[1:], axis=1)
    threshold = float(np.percentile(sims, max(0, min(100, 100 - breakpoint_percentile))))
    return [i + 1 for i, s in enumerate(sims) if s < threshold]


# ======================================================================================
# 5) Agrupación
# ======================================================================================
def _group_sentences(sentences: list[Sentence], breakpoints: list[int]) -> list[list[Sentence]]:
    if not sentences:
        return []
    cuts = sorted(set(breakpoints))
    groups: list[list[Sentence]] = []
    prev = 0
    for c in cuts:
        if c <= prev or c >= len(sentences):
            continue
        groups.append(sentences[prev:c])
        prev = c
    groups.append(sentences[prev:])
    return [g for g in groups if g]


# ======================================================================================
# 6) Ajuste de tamaño  (fusiona pequeños, parte grandes por FRASE)
# ======================================================================================
def _chars(group: list[Sentence]) -> int:
    return sum(len(s.text) for s in group) + max(0, len(group) - 1)


def _split_oversized(group: list[Sentence], max_chars: int) -> list[list[Sentence]]:
    """Empaqueta frases hasta max_chars respetando la frontera de frase.
    Solo si una frase suelta supera max_chars, se parte esa frase por palabras."""
    out: list[list[Sentence]] = []
    cur: list[Sentence] = []
    cur_len = 0
    for s in group:
        slen = len(s.text)
        if slen > max_chars:
            if cur:
                out.append(cur)
                cur, cur_len = [], 0
            words = s.text.split()
            step = max(1, int(len(words) * max_chars / max(1, slen)))
            for k in range(0, len(words), step):
                piece = " ".join(words[k:k + step])
                out.append([Sentence(piece, s.start, s.end)])
            continue
        if cur and cur_len + 1 + slen > max_chars:
            out.append(cur)
            cur, cur_len = [], 0
        cur.append(s)
        cur_len += (1 if cur_len else 0) + slen
    if cur:
        out.append(cur)
    return out


def _adjust_sizes(groups: list[list[Sentence]], min_chars: int = MIN_CHARS,
                  max_chars: int = MAX_CHARS) -> list[list[Sentence]]:
    # (a) partir los grupos que se pasan de max_chars
    sized: list[list[Sentence]] = []
    for g in groups:
        if _chars(g) > max_chars:
            sized.extend(_split_oversized(g, max_chars))
        else:
            sized.append(g)

    # (b) fusionar grupos por debajo de min_chars con el vecino, sin superar max_chars
    merged: list[list[Sentence]] = []
    for g in sized:
        if merged and _chars(merged[-1]) < min_chars and _chars(merged[-1]) + 1 + _chars(g) <= max_chars:
            merged[-1] = merged[-1] + g
        elif merged and _chars(g) < min_chars and _chars(merged[-1]) + 1 + _chars(g) <= max_chars:
            merged[-1] = merged[-1] + g
        else:
            merged.append(list(g))
    return [g for g in merged if g]


# ======================================================================================
# Clasificación de categoría por ruta lógica del documento
# ======================================================================================
_CAT_SIGNALS = {
    "CSDDD": ("csddd", "diligencia debida", "1760", "due diligence", "cer_2018"),
    "GRI": ("gri", "global reporting", "estandar gri", "estándar gri"),
}


def _classify_category(source: str) -> str:
    low = source.lower()
    for cat, sigs in _CAT_SIGNALS.items():
        if any(sig in low for sig in sigs):
            return cat
    return "general"


# ======================================================================================
# chunk_document  — orquesta 1..6 para un fichero
# ======================================================================================
def chunk_document(path: Path, root: Path,
                   breakpoint_percentile: int = BREAKPOINT_PERCENTILE,
                   min_chars: int = MIN_CHARS, max_chars: int = MAX_CHARS) -> list[Chunk]:
    full_text, spans = _read_document(path)
    if not full_text.strip():
        logger.warning("Documento vacío: %s", path)
        return []

    total_pages = spans[-1][0] if spans else 1
    source = str(path.relative_to(root).with_suffix("")).replace("\\", "/")
    category = _classify_category(source)

    chunks: list[Chunk] = []
    for block in _segment_structure(full_text):
        if block.kind in ("table", "figure"):
            # bloque atómico: nunca se parte ni se mezcla con prosa
            p = _page_of(block.start, spans)
            chunks.append(Chunk(
                content=block.text, block_type=block.kind, source=source,
                page=p, page_end=_page_of(block.start + len(block.text), spans),
                total_pages=total_pages, primary_category=category, n_sentences=0,
            ))
            continue

        sentences = _split_sentences(block.text, block.start)
        if not sentences:
            continue
        bps = _find_breakpoints(sentences, breakpoint_percentile)
        groups = _group_sentences(sentences, bps)
        groups = _adjust_sizes(groups, min_chars, max_chars)

        for g in groups:
            content = " ".join(s.text for s in g).strip()
            if not content:
                continue
            start_off = g[0].start
            end_off = g[-1].end
            chunks.append(Chunk(
                content=content, block_type="text", source=source,
                page=_page_of(start_off, spans), page_end=_page_of(end_off, spans),
                total_pages=total_pages, primary_category=category, n_sentences=len(g),
            ))
    return chunks


# ======================================================================================
# 7) Grafo de conocimiento:  tripletes (LLM) + entidades + graph_importance (PageRank)
#    Opcional (--extract-graph). Degrada limpio si falla (triplets/entities vacíos).
# ======================================================================================
_TRIPLET_PROMPT = (
    "Extrae los tripletes de conocimiento (sujeto, relación, objeto) y las entidades "
    "canónicas del siguiente fragmento normativo en español. Devuelve SOLO JSON válido "
    'con esta forma exacta: {"entities": ["..."], "triplets": [["sujeto","relación","objeto"], ...]}. '
    "Usa relaciones cortas en infinitivo o nominales (p. ej. 'obliga a', 'define', 'aplica a', "
    "'es responsable de'). Máximo 8 entidades y 8 tripletes. Fragmento:\n\n"
)


def _extract_one(genai_client, text: str) -> tuple[list[str], list[list[str]]]:
    try:
        from google.genai import types
        resp = genai_client.models.generate_content(
            model=GRAPH_LLM_MODEL,
            contents=_TRIPLET_PROMPT + text[:4000],
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        data = json.loads(resp.text)
        ents = [str(e).strip() for e in data.get("entities", []) if str(e).strip()][:12]
        trps = [
            [str(x).strip() for x in t][:3]
            for t in data.get("triplets", [])
            if isinstance(t, (list, tuple)) and len(t) == 3
        ][:12]
        return ents, trps
    except Exception as exc:  # noqa: BLE001
        logger.debug("Extracción KG fallida: %s", exc)
        return [], []


def _norm_entity(e: str) -> str:
    e = unicodedata.normalize("NFKD", e).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", e).strip().lower()


def extract_graph(chunks: list[Chunk], genai_client, workers: int = 4) -> None:
    """Rellena entities/triplets de cada chunk y calcula graph_importance por PageRank."""
    import networkx as nx
    from concurrent.futures import ThreadPoolExecutor

    logger.info("Extrayendo grafo de conocimiento de %d chunks...", len(chunks))
    text_chunks = [c for c in chunks if c.block_type == "text" and len(c.content) >= MIN_CHARS]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: _extract_one(genai_client, c.content), text_chunks))
    for c, (ents, trps) in zip(text_chunks, results):
        c.entities = ents
        c.triplets = trps

    # grafo: nodos = entidades canónicas; aristas = co-aparición en un triplete o chunk
    g = nx.Graph()
    ent_to_chunks: dict[str, list[Chunk]] = {}
    for c in text_chunks:
        canon = {_norm_entity(e): e for e in c.entities if e}
        for k in canon:
            g.add_node(k)
            ent_to_chunks.setdefault(k, []).append(c)
        keys = list(canon)
        for a_i in range(len(keys)):
            for b_i in range(a_i + 1, len(keys)):
                g.add_edge(keys[a_i], keys[b_i], weight=g.get_edge_data(keys[a_i], keys[b_i], {}).get("weight", 0) + 1)
        for s, r, o in c.triplets:
            ks, ko = _norm_entity(s), _norm_entity(o)
            if ks and ko:
                g.add_edge(ks, ko, weight=g.get_edge_data(ks, ko, {}).get("weight", 0) + 2)

    if g.number_of_nodes() == 0:
        return
    pr = nx.pagerank(g, weight="weight")
    mx = max(pr.values()) or 1.0
    for c in text_chunks:
        keys = [_norm_entity(e) for e in c.entities if e]
        score = np.mean([pr.get(k, 0.0) for k in keys]) if keys else 0.0
        c.graph_importance = float(score / mx)  # normalizado 0..1


# ======================================================================================
# 8) Upsert a Pinecone
# ======================================================================================
def upsert_chunks(chunks: list[Chunk], index, namespace: str = DEFAULT_NAMESPACE,
                  batch: int = UPSERT_BATCH, id_prefix: str = "chunk") -> int:
    total = 0
    for start in range(0, len(chunks), batch):
        window = chunks[start:start + batch]
        vecs = embed_texts([c.content for c in window], is_query=False)
        payload = [
            {"id": f"{id_prefix}_{start + k:06d}", "values": vecs[k].tolist(), "metadata": window[k].metadata()}
            for k in range(len(window))
        ]
        index.upsert(vectors=payload, namespace=namespace)
        total += len(payload)
        logger.info("  upsert %d/%d", total, len(chunks))
    return total


# ======================================================================================
# Runner
# ======================================================================================
def iter_documents(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in _SUPPORTED:
            yield p


def run(input_dir: str, *, embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        breakpoint_percentile: int = BREAKPOINT_PERCENTILE, min_chars: int = MIN_CHARS,
        max_chars: int = MAX_CHARS, namespace: str = DEFAULT_NAMESPACE,
        extract_graph_flag: bool = False, wipe: bool = False, dry_run: bool = False,
        limit: int | None = None) -> None:
    set_embedding_model(embedding_model)
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"No es un directorio: {root}")

    docs = list(iter_documents(root))
    if limit:
        docs = docs[:limit]
    logger.info("Documentos a procesar: %d  ·  modelo: %s  ·  dim: %s",
                len(docs), embedding_model, embedding_dim())

    all_chunks: list[Chunk] = []
    for doc in docs:
        cs = chunk_document(doc, root, breakpoint_percentile, min_chars, max_chars)
        logger.info("%-60s -> %3d chunks", str(doc.relative_to(root)), len(cs))
        all_chunks.extend(cs)

    # informe de calidad del chunking
    lens = [len(c.content) for c in all_chunks]
    if lens:
        logger.info("CHUNKS: %d  ·  chars min/mediana/max = %d / %d / %d  ·  <%d chars: %d  ·  tablas/figuras: %d",
                    len(all_chunks), min(lens), int(np.median(lens)), max(lens), min_chars,
                    sum(1 for x in lens if x < min_chars),
                    sum(1 for c in all_chunks if c.block_type != "text"))

    if extract_graph_flag and not dry_run:
        try:
            from src.config import genai_client
        except Exception:  # noqa: BLE001
            import os
            from google import genai
            genai_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        extract_graph(all_chunks, genai_client)

    if dry_run:
        for c in all_chunks[:5]:
            logger.info("--- %s p%d [%s] (%d chars, gi=%.3f)\n%s",
                        c.source, c.page, c.block_type, len(c.content), c.graph_importance, c.content[:300])
        logger.info("DRY-RUN: no se ha escrito nada en Pinecone.")
        return

    # Pinecone
    try:
        from src.config import pinecone_index as index
        assert index is not None
    except Exception:  # noqa: BLE001
        import os
        from pinecone import Pinecone
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        name = os.environ["PINECONE_INDEX_NAME"]
        index = pc.Index(host=name) if name.startswith("http") else pc.Index(name)

    if wipe:
        logger.warning("Borrando namespace %r del índice antes de reindexar...", namespace)
        index.delete(delete_all=True, namespace=namespace)

    n = upsert_chunks(all_chunks, index, namespace=namespace)
    logger.info("Hecho. %d chunks indexados en namespace %r.", n, namespace)


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Pipeline de chunking semántico + indexación del corpus.")
    ap.add_argument("--input", required=True, help="Directorio raíz con los PDF/TXT/DOCX del corpus.")
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL,
                    help="Modelo de SentenceTransformers (mismo que usará el backend para consultar).")
    ap.add_argument("--breakpoint-percentile", type=int, default=BREAKPOINT_PERCENTILE)
    ap.add_argument("--min-chars", type=int, default=MIN_CHARS)
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    ap.add_argument("--extract-graph", action="store_true", help="Extraer tripletes KG + PageRank (usa Gemini).")
    ap.add_argument("--wipe", action="store_true", help="Borra el namespace antes de reindexar.")
    ap.add_argument("--dry-run", action="store_true", help="Procesa y reporta, sin escribir en Pinecone.")
    ap.add_argument("--limit", type=int, default=None, help="Procesar solo los N primeros documentos.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "sentence_transformers", "filelock", "pinecone"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    run(
        args.input,
        embedding_model=args.embedding_model,
        breakpoint_percentile=args.breakpoint_percentile,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        namespace=args.namespace,
        extract_graph_flag=args.extract_graph,
        wipe=args.wipe,
        dry_run=args.dry_run,
        limit=args.limit,
    )


if __name__ == "__main__":
    _cli()
