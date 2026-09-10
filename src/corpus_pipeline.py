# src/corpus_pipeline.py
"""
Pipeline de análisis + chunking semántico + indexación del corpus normativo.

Reemplaza la versión anterior (bytecode fuera del repo) que producía fragmentos
de mediana 2 palabras (un vector por bloque de layout, sin agregación).

FLUJO POR DOCUMENTO
-------------------
0. _analyze_document   Análisis PREVIO al chunking:
                       - idioma, tipo de documento (directiva/reglamento/guía/…),
                         año, órgano emisor, título.
                       - _detect_headings: árbol de encabezados (Título > Capítulo >
                         Sección > Artículo > N.N…) con offset y página.
                       - (--enrich) resumen del documento vía LLM.
1. _read_document      PDF/TXT/DOCX -> texto completo + mapa de páginas por offset.
                       Las tablas se renderizan como markdown `| ... |`.
2. _segment_structure  Separa bloques `heading` (frontera + contexto), `table`,
                       `figure` (captions) y `text`. El chunking semántico corre
                       DENTRO de cada sección: los chunks nunca cruzan un encabezado.
3. _split_sentences    Regex sobre .!? con protección de abreviaturas (ES).
4. _find_breakpoints   NÚCLEO SEMÁNTICO: codifica cada frase, similitud coseno entre
                       consecutivas, umbral = percentil (100 - breakpoint_percentile).
                       Con 80 -> corta en el 20 % de fronteras con MENOR similitud.
5. _group_sentences    Agrupa frases entre cortes.
6. _adjust_sizes       Fusiona grupos < min_chars; parte grupos > max_chars por
                       FRONTERA DE FRASE (no por palabras).
7. Enriquecimiento por chunk (--enrich, LLM):
   - context header (siempre; plantilla sin LLM, refinado con LLM si --enrich):
     se antepone al texto SOLO para el embedding; el `text` almacenado queda literal.
   - summary (1 frase), keywords, entities, triplets, y graph_importance por PageRank.
8. (--index-propositions) Descompone cada chunk en afirmaciones atómicas e indexa
   cada una como vector adicional en un namespace aparte (para poder comparar
   recuperación por chunk vs. por proposición sin romper la cita literal).
9. upsert              embed_texts(context + text) -> index.upsert en lotes de 100
                       con metadatos ricos.

Parámetros: breakpoint_percentile=80, min_chars=350, max_chars=1800. Sin overlap.

--------------------------------------------------------------------------------
NOTA DE COHERENCIA (riesgo crítico)
--------------------------------------------------------------------------------
El índice DEBE construirse y consultarse con EL MISMO modelo y dimensión.
`embed_texts()` es la única fuente de verdad. El backend debe hacer

    from src.corpus_pipeline import embed_texts
    query_vec = embed_texts([pregunta], is_query=True)[0]

en lugar de `pc.inference.embed(...)` (API hosteada de Pinecone), que usa otro
modelo y rompe/degrada la recuperación.

Idioma: el corpus es español. Modelo por defecto multilingüe
`paraphrase-multilingual-MiniLM-L12-v2` (384d, misma dimensión que el índice
actual). `intfloat/multilingual-e5-*` (768d, con prefijos) vía --embedding-model.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

logger = logging.getLogger("corpus_pipeline")

# ======================================================================================
# Parámetros
# ======================================================================================
BREAKPOINT_PERCENTILE = 80
MIN_CHARS = 350
MAX_CHARS = 1800
UPSERT_BATCH = 100
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_NAMESPACE = ""
PROPOSITIONS_NAMESPACE = "propositions"
LLM_MODEL = "gemini-2.5-flash"
LLM_WORKERS = 4

_SUPPORTED = {".pdf", ".txt", ".md", ".docx"}
_UP = "A-ZÁÉÍÓÚÜÑ"


# ======================================================================================
# Embeddings — ÚNICA FUENTE DE VERDAD (indexación y consulta)
# ======================================================================================
_MODEL_CACHE: dict = {}
_MODEL_NAME = DEFAULT_EMBEDDING_MODEL


def set_embedding_model(name: str) -> None:
    global _MODEL_NAME
    _MODEL_NAME = name


def _needs_e5_prefix(name: str) -> bool:
    return "e5" in name.lower()


def get_model():
    if _MODEL_NAME not in _MODEL_CACHE:
        from sentence_transformers import SentenceTransformer
        logger.info("Cargando modelo de embeddings: %s", _MODEL_NAME)
        _MODEL_CACHE[_MODEL_NAME] = SentenceTransformer(_MODEL_NAME)
    return _MODEL_CACHE[_MODEL_NAME]


def embed_texts(texts: list[str], is_query: bool = False, batch_size: int = 64) -> np.ndarray:
    """Codifica textos con el modelo del corpus. Normaliza L2 (cosine).
    Para e5-* añade el prefijo requerido. Devuelve np.ndarray (n, dim) float32."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    model = get_model()
    if _needs_e5_prefix(_MODEL_NAME):
        prefix = "query: " if is_query else "passage: "
        texts = [prefix + t for t in texts]
    vecs = model.encode(
        texts, batch_size=batch_size, normalize_embeddings=True,
        convert_to_numpy=True, show_progress_bar=False,
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
class Heading:
    level: int          # 1 = Título/Anexo, 2 = Capítulo/Módulo, 3 = Sección, 4 = Artículo, 5+ = N.N…
    label: str          # texto del encabezado, normalizado
    start: int
    page: int


@dataclass
class Block:
    kind: str           # "text" | "table" | "figure" | "heading"
    text: str
    start: int
    heading: Heading | None = None


@dataclass
class Sentence:
    text: str
    start: int
    end: int


@dataclass
class DocInfo:
    source: str
    title: str = ""
    doc_type: str = "otro"          # directiva | reglamento | guía | estándar | marco_teórico | glosario | otro
    year: int | None = None
    issuer: str | None = None       # UE | OCDE | GRI | OIT | ISO | ONU | None
    lang: str = "es"
    total_pages: int = 1
    primary_category: str = "general"
    headings: list[Heading] = field(default_factory=list)
    toc: list[tuple[int, str, int]] = field(default_factory=list)   # (level, label, page)
    summary: str = ""


@dataclass
class Chunk:
    content: str                    # texto LITERAL (para la cita)
    block_type: str                 # "text" | "table" | "figure" | "proposition"
    source: str
    section: str
    page: int
    page_end: int
    total_pages: int
    primary_category: str
    doc_type: str = "otro"
    doc_year: int | None = None
    doc_issuer: str | None = None
    doc_lang: str = "es"
    n_sentences: int = 0
    context: str = ""               # cabecera contextual (se embebe, no sustituye a `content`)
    summary: str = ""
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    triplets: list[list[str]] = field(default_factory=list)
    graph_importance: float = 0.0
    parent_id: str | None = None    # solo para propositions

    def embedding_text(self) -> str:
        return f"{self.context}\n\n{self.content}" if self.context else self.content

    def metadata(self) -> dict:
        return {
            "text": self.content,
            "context": self.context,
            "source": self.source,
            "section": self.section,
            "page": self.page,
            "page_end": self.page_end,
            "total_pages": self.total_pages,
            "primary_category": self.primary_category,
            "doc_type": self.doc_type,
            "doc_year": self.doc_year if self.doc_year is not None else 0,
            "doc_issuer": self.doc_issuer or "",
            "doc_lang": self.doc_lang,
            "block_type": self.block_type,
            "n_sentences": self.n_sentences,
            "summary": self.summary,
            "keywords": self.keywords,
            "entities": self.entities,
            "triplets": [list(t) for t in self.triplets],
            "graph_importance": round(float(self.graph_importance), 6),
            **({"parent_id": self.parent_id} if self.parent_id else {}),
        }


# ======================================================================================
# 1) Lectura de documento -> (texto, page_spans)  [página por offset, no por substring]
# ======================================================================================
def _table_to_markdown(rows: list) -> str:
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
        import pdfplumber
    except ImportError:  # pragma: no cover
        return _read_pdf_pypdf(path)
    parts, spans, cursor = [], [], 0
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            pieces = []
            for tbl in (page.extract_tables() or []):
                md = _table_to_markdown(tbl)
                if md:
                    pieces.append(md)
            txt = (page.extract_text() or "").strip()
            if txt:
                pieces.append(txt)
            page_text = "\n\n".join(pieces)
            start = cursor
            parts.append(page_text)
            cursor += len(page_text) + 2
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
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(str(path))
    parts = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            t = Paragraph(child, doc).text.strip()
            if t:
                parts.append(t)
        elif tag == "tbl":
            tbl = Table(child, doc)
            md = _table_to_markdown([[c.text for c in row.cells] for row in tbl.rows])
            if md:
                parts.append(md)
    text = "\n\n".join(parts)
    return text, [(1, 0, len(text))]


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
# 0) ANÁLISIS PREVIO AL CHUNKING
# ======================================================================================
_ES_WORDS = {"de", "la", "que", "el", "y", "los", "las", "del", "por", "para", "con",
             "una", "como", "más", "sobre", "este", "esta", "es", "se", "su", "al"}
_EN_WORDS = {"the", "and", "of", "to", "in", "for", "that", "is", "on", "with", "as",
             "by", "this", "are", "be", "or", "an", "from", "which", "at"}


def _detect_language(text: str) -> str:
    toks = re.findall(r"[a-záéíóúñ]+", text.lower())[:4000]
    if not toks:
        return "es"
    es = sum(1 for t in toks if t in _ES_WORDS)
    en = sum(1 for t in toks if t in _EN_WORDS)
    return "en" if en > es else "es"


_ROMAN = r"[IVXLCDM]+"
_HEADING_PATTERNS = [
    (1, re.compile(rf"^\s*(T[ÍI]TULO)\s+({_ROMAN}|PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|\d+)\b.*", re.I)),
    (1, re.compile(rf"^\s*(ANEXO)\s+({_ROMAN}|\d+)\b.*", re.I)),
    (2, re.compile(rf"^\s*(CAP[ÍI]TULO)\s+({_ROMAN}|\d+)\b.*", re.I)),
    (2, re.compile(r"^\s*(M[ÓO]DULO)\s+(\d+)\b.*", re.I)),
    (3, re.compile(rf"^\s*(SECCI[ÓO]N)\s+({_ROMAN}|\d+)\b.*", re.I)),
    (4, re.compile(r"^\s*(Art[íi]culo|Art\.)\s+(\d+)\s*(bis|ter|qu[áa]ter)?\b.*", re.I)),
]
_NUMBERED_HEADING = re.compile(rf"^\s*(\d+(?:\.\d+){{0,3}})\.?\s+([{_UP}][^\n]{{2,88}})$")
_ALLCAPS_HEADING = re.compile(rf"^\s*([{_UP}][{_UP}\s\d.,:;()\-/]{{4,78}})$")


def _detect_headings(full_text: str, spans: list[tuple[int, int, int]]) -> list[Heading]:
    heads: list[Heading] = []
    cursor = 0
    for line in full_text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith("|"):
            matched = None
            for level, pat in _HEADING_PATTERNS:
                if pat.match(stripped):
                    matched = (level, re.sub(r"\s+", " ", stripped)[:120])
                    break
            if not matched:
                m = _NUMBERED_HEADING.match(stripped)
                if m and not stripped.rstrip().endswith((",", ";", ":")):
                    depth = m.group(1).count(".") + 1
                    matched = (min(4 + depth, 8), re.sub(r"\s+", " ", stripped)[:120])
            if not matched:
                m = _ALLCAPS_HEADING.match(stripped)
                if m and 4 <= len(stripped) <= 80 and not stripped.endswith("."):
                    letters = [c for c in stripped if c.isalpha()]
                    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.85:
                        matched = (3, re.sub(r"\s+", " ", stripped)[:120])
            if matched:
                heads.append(Heading(matched[0], matched[1], cursor, _page_of(cursor, spans)))
        cursor += len(line)
    return heads


def _section_path_of(offset: int, headings: list[Heading]) -> str:
    stack: dict[int, str] = {}
    for h in headings:
        if h.start > offset:
            break
        for lvl in list(stack):
            if lvl >= h.level:
                del stack[lvl]
        stack[h.level] = h.label
    return " · ".join(stack[k] for k in sorted(stack))


_DOCTYPE_RULES = [
    ("directiva",      ("directiva (ue)", "directiva 20", "directive (eu)")),
    ("reglamento",     ("reglamento (ue)", "reglamento 20", "regulation (eu)")),
    ("guía",           ("guía de", "guia de", "guidance", "líneas directrices", "lineas directrices", "due diligence guidance")),
    ("estándar",       ("gri ", "estándar gri", "standard", "norma iso", "esrs")),
    ("glosario",       ("glosario", "glossary")),
    ("marco_teórico",  ("marco teórico", "marco teorico")),
]
_ISSUER_RULES = [
    ("OCDE", ("ocde", "oecd")),
    ("UE",   ("unión europea", "union europea", "parlamento europeo", "comisión europea", "comision europea", "(ue)", "diario oficial de la unión")),
    ("GRI",  ("global reporting initiative", "gri standards", "estándares gri")),
    ("OIT",  ("organización internacional del trabajo", "organizacion internacional del trabajo", " oit ", " ilo ")),
    ("ISO",  ("iso 26000", "norma iso", "international organization for standardization")),
    ("ONU",  ("naciones unidas", " onu ", "united nations")),
]


def _analyze_document(full_text: str, spans: list[tuple[int, int, int]], path: Path,
                      root: Path, genai_client=None) -> DocInfo:
    source = str(path.relative_to(root).with_suffix("")).replace("\\", "/")
    head_text = full_text[:3500]
    low = (source + " " + head_text).lower()

    di = DocInfo(source=source)
    di.total_pages = spans[-1][0] if spans else 1
    di.lang = _detect_language(head_text)
    di.primary_category = _classify_category(source)

    di.doc_type = next((t for t, sigs in _DOCTYPE_RULES if any(s in low for s in sigs)),
                       "marco_teórico" if source.lower().startswith("01_marco") else "otro")
    di.issuer = next((i for i, sigs in _ISSUER_RULES if any(s in low for s in sigs)), None)
    ym = re.search(r"\b(20[0-3]\d)\b", head_text)
    di.year = int(ym.group(1)) if ym else None

    first_line = next((l.strip() for l in head_text.splitlines() if len(l.strip()) > 15), "")
    di.title = (first_line or Path(source).name.replace("_", " "))[:160]

    di.headings = _detect_headings(full_text, spans)
    di.toc = [(h.level, h.label, h.page) for h in di.headings]

    if genai_client is not None:
        di.summary = _llm_doc_summary(genai_client, di, head_text)
    return di


def _llm_doc_summary(genai_client, di: DocInfo, head_text: str) -> str:
    try:
        from google.genai import types
        prompt = (
            "Resume en 2 frases, en español, de qué trata este documento normativo "
            "y a quién obliga. Devuelve solo el texto del resumen.\n\n"
            f"Título: {di.title}\nTipo: {di.doc_type}  Emisor: {di.issuer}  Año: {di.year}\n\n"
            f"{head_text[:2500]}"
        )
        resp = genai_client.models.generate_content(
            model=LLM_MODEL, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return (resp.text or "").strip()[:600]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Resumen de documento fallido: %s", exc)
        return ""


# ======================================================================================
# 2) Segmentación estructural (emite bloques heading / table / figure / text)
# ======================================================================================
_CAPTION_RE = re.compile(
    r"^\s*(tabla|figura|table|figure|cuadro|gr[áa]fico|ilustraci[óo]n|recuadro)\s+\d+[.:]?\d*\b.*$",
    re.IGNORECASE,
)


def _segment_structure(full_text: str, headings: list[Heading]) -> list[Block]:
    head_starts = {h.start: h for h in headings}
    blocks: list[Block] = []
    lines = full_text.splitlines(keepends=True)
    cursor = 0
    i = 0
    buf: list[str] = []
    buf_start = 0

    def flush_text():
        nonlocal buf
        if buf:
            txt = "".join(buf).strip()
            if txt:
                blocks.append(Block("text", txt, buf_start))
            buf = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # encabezado detectado en _detect_headings (por offset exacto de línea)
        if cursor in head_starts:
            flush_text()
            h = head_starts[cursor]
            blocks.append(Block("heading", h.label, cursor, heading=h))
            cursor += len(line)
            i += 1
            continue

        # tabla: >= 2 líneas consecutivas que empiezan por '|'
        if stripped.startswith("|"):
            j, tbl_start, tbl_lines, local = i, cursor, [], cursor
            while j < len(lines) and lines[j].strip().startswith("|"):
                tbl_lines.append(lines[j]); local += len(lines[j]); j += 1
            if len(tbl_lines) >= 2:
                flush_text()
                blocks.append(Block("table", "".join(tbl_lines).strip(), tbl_start))
                cursor, i = local, j
                continue

        # figura: caption aislado
        if _CAPTION_RE.match(stripped):
            flush_text()
            blocks.append(Block("figure", stripped, cursor))
            cursor += len(line); i += 1
            continue

        if not buf:
            buf_start = cursor
        buf.append(line)
        cursor += len(line); i += 1

    flush_text()
    return blocks


# ======================================================================================
# 3) División en frases
# ======================================================================================
_ABBREV = {
    "sr", "sra", "srs", "sras", "dr", "dra", "dres", "prof", "profa", "art", "arts",
    "núm", "num", "nº", "pág", "pag", "págs", "pags", "p", "pp", "cap", "caps", "apdo",
    "apdos", "fig", "figs", "tab", "ref", "refs", "ed", "eds", "vol", "vols", "op",
    "cit", "etc", "vs", "ud", "uds", "ej", "ee", "uu", "aa", "s", "l", "a", "n", "no",
    "cf", "cfr", "aprox", "máx", "max", "mín", "min", "coord", "coords", "trad", "ene",
    "feb", "mar", "abr", "jun", "jul", "ago", "sep", "sept", "oct", "nov", "dic",
}
_SENT_END_RE = re.compile(r'([.!?]+)(["»”\')\]]*)(\s+)')


def _split_sentences(text: str, base_offset: int) -> list[Sentence]:
    text = text.replace("\r\n", "\n")
    out: list[Sentence] = []
    start = 0
    for m in _SENT_END_RE.finditer(text):
        end = m.end(2)
        candidate = text[start:end].strip()
        if not candidate:
            start = m.end()
            continue
        prev_word = re.search(r"(\S+)$", text[:m.start(1)])
        token = prev_word.group(1).lower().strip(".,;:()[]") if prev_word else ""
        after = text[m.end():m.end() + 1]
        is_abbrev = token in _ABBREV or (len(token) == 1 and token.isalpha())
        is_decimal = token.isdigit() and after.isdigit()
        is_enum = re.fullmatch(r"\d{1,3}", token) is not None and text[start:m.start(1)].strip() == token
        is_ellipsis = m.group(1) == "..." and after and after.islower()
        if is_abbrev or is_decimal or is_enum or is_ellipsis:
            continue
        out.append(Sentence(candidate, base_offset + start, base_offset + end))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(Sentence(tail, base_offset + start, base_offset + len(text)))
    return out


# ======================================================================================
# 4) Breakpoints semánticos
# ======================================================================================
def _find_breakpoints(sentences: list[Sentence], breakpoint_percentile: int = BREAKPOINT_PERCENTILE) -> list[int]:
    """Índices i (1..n-1) donde ARRANCA un grupo nuevo: la similitud coseno entre la
    frase i-1 e i cae por debajo del percentil (100 - breakpoint_percentile) de todas
    las similitudes consecutivas. Con 80 -> percentil 20."""
    n = len(sentences)
    if n < 3:
        return []
    embs = embed_texts([s.text for s in sentences], is_query=False)
    sims = np.sum(embs[:-1] * embs[1:], axis=1)
    threshold = float(np.percentile(sims, max(0, min(100, 100 - breakpoint_percentile))))
    return [i + 1 for i, s in enumerate(sims) if s < threshold]


# ======================================================================================
# 5) Agrupación
# ======================================================================================
def _group_sentences(sentences: list[Sentence], breakpoints: list[int]) -> list[list[Sentence]]:
    if not sentences:
        return []
    groups, prev = [], 0
    for c in sorted(set(breakpoints)):
        if prev < c < len(sentences):
            groups.append(sentences[prev:c])
            prev = c
    groups.append(sentences[prev:])
    return [g for g in groups if g]


# ======================================================================================
# 6) Ajuste de tamaño
# ======================================================================================
def _chars(group: list[Sentence]) -> int:
    return sum(len(s.text) for s in group) + max(0, len(group) - 1)


def _split_oversized(group: list[Sentence], max_chars: int) -> list[list[Sentence]]:
    out, cur, cur_len = [], [], 0
    for s in group:
        slen = len(s.text)
        if slen > max_chars:
            if cur:
                out.append(cur); cur, cur_len = [], 0
            words = s.text.split()
            step = max(1, int(len(words) * max_chars / max(1, slen)))
            for k in range(0, len(words), step):
                out.append([Sentence(" ".join(words[k:k + step]), s.start, s.end)])
            continue
        if cur and cur_len + 1 + slen > max_chars:
            out.append(cur); cur, cur_len = [], 0
        cur.append(s)
        cur_len += (1 if cur_len else 0) + slen
    if cur:
        out.append(cur)
    return out


def _adjust_sizes(groups: list[list[Sentence]], min_chars: int = MIN_CHARS,
                  max_chars: int = MAX_CHARS) -> list[list[Sentence]]:
    sized: list[list[Sentence]] = []
    for g in groups:
        if _chars(g) > max_chars:
            sized.extend(_split_oversized(g, max_chars))
        else:
            sized.append(g)
    merged: list[list[Sentence]] = []
    for g in sized:
        if merged and (_chars(merged[-1]) < min_chars or _chars(g) < min_chars) \
                and _chars(merged[-1]) + 1 + _chars(g) <= max_chars:
            merged[-1] = merged[-1] + g
        else:
            merged.append(list(g))
    return [g for g in merged if g]


# ======================================================================================
# Categoría + contexto
# ======================================================================================
_CAT_SIGNALS = {
    "CSDDD": ("csddd", "diligencia debida", "1760", "due diligence", "cer_2018", "conducta empresarial responsable"),
    "GRI": ("gri", "global reporting", "estandar gri", "estándar gri"),
}


def _classify_category(source: str) -> str:
    low = source.lower()
    for cat, sigs in _CAT_SIGNALS.items():
        if any(sig in low for sig in sigs):
            return cat
    return "general"


def _build_context(di: DocInfo, section: str) -> str:
    parts = []
    if di.title:
        parts.append(di.title)
    meta = " ".join(x for x in (di.doc_type if di.doc_type != "otro" else "",
                                str(di.year) if di.year else "", di.issuer or "") if x).strip()
    if meta:
        parts.append(meta)
    if section:
        parts.append(section)
    ctx = " · ".join(parts)
    return f"[{ctx}]" if ctx else ""


# ======================================================================================
# chunk_document
# ======================================================================================
def chunk_document(path: Path, root: Path, genai_client=None,
                   breakpoint_percentile: int = BREAKPOINT_PERCENTILE,
                   min_chars: int = MIN_CHARS, max_chars: int = MAX_CHARS,
                   enrich: bool = False) -> list[Chunk]:
    full_text, spans = _read_document(path)
    if not full_text.strip():
        logger.warning("Documento vacío: %s", path)
        return []

    di = _analyze_document(full_text, spans, path, root, genai_client if enrich else None)

    chunks: list[Chunk] = []

    def _mk(content: str, block_type: str, start: int, end: int, n_sent: int) -> Chunk:
        section = _section_path_of(start, di.headings)
        return Chunk(
            content=content, block_type=block_type, source=di.source, section=section,
            page=_page_of(start, spans), page_end=_page_of(end, spans),
            total_pages=di.total_pages, primary_category=di.primary_category,
            doc_type=di.doc_type, doc_year=di.year, doc_issuer=di.issuer, doc_lang=di.lang,
            n_sentences=n_sent, context=_build_context(di, section),
        )

    for block in _segment_structure(full_text, di.headings):
        if block.kind == "heading":
            continue  # solo actualiza el path de sección
        if block.kind in ("table", "figure"):
            chunks.append(_mk(block.text, block.kind, block.start,
                              block.start + len(block.text), 0))
            continue
        sentences = _split_sentences(block.text, block.start)
        if not sentences:
            continue
        bps = _find_breakpoints(sentences, breakpoint_percentile)
        groups = _adjust_sizes(_group_sentences(sentences, bps), min_chars, max_chars)
        for g in groups:
            content = " ".join(s.text for s in g).strip()
            if content:
                chunks.append(_mk(content, "text", g[0].start, g[-1].end, len(g)))
    return chunks


# ======================================================================================
# 7) Enriquecimiento por chunk (LLM): summary + keywords + entities + triplets + PageRank
# ======================================================================================
_ENRICH_PROMPT = (
    "Analiza este fragmento normativo en español y devuelve SOLO JSON válido con la "
    "forma exacta: {\"summary\": \"...\", \"keywords\": [\"...\"], \"entities\": [\"...\"], "
    "\"triplets\": [[\"sujeto\",\"relación\",\"objeto\"]]}. "
    "summary = 1 frase. keywords = 3-6 términos clave. entities = máx 8 entidades canónicas. "
    "triplets = máx 8, relaciones cortas ('obliga a', 'define', 'aplica a'). Fragmento:\n\n"
)


def _enrich_one(genai_client, text: str) -> dict:
    try:
        from google.genai import types
        resp = genai_client.models.generate_content(
            model=LLM_MODEL, contents=_ENRICH_PROMPT + text[:4000],
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        data = json.loads(resp.text)
        return {
            "summary": str(data.get("summary", "")).strip()[:400],
            "keywords": [str(k).strip() for k in data.get("keywords", []) if str(k).strip()][:8],
            "entities": [str(e).strip() for e in data.get("entities", []) if str(e).strip()][:12],
            "triplets": [[str(x).strip() for x in t][:3] for t in data.get("triplets", [])
                         if isinstance(t, (list, tuple)) and len(t) == 3][:12],
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Enriquecimiento fallido: %s", exc)
        return {"summary": "", "keywords": [], "entities": [], "triplets": []}


def _norm_entity(e: str) -> str:
    e = unicodedata.normalize("NFKD", e).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", e).strip().lower()


def enrich_chunks(chunks: list[Chunk], genai_client) -> None:
    import networkx as nx
    targets = [c for c in chunks if c.block_type == "text" and len(c.content) >= MIN_CHARS]
    logger.info("Enriqueciendo %d chunks vía LLM...", len(targets))
    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as ex:
        for c, d in zip(targets, ex.map(lambda c: _enrich_one(genai_client, c.content), targets)):
            c.summary, c.keywords = d["summary"], d["keywords"]
            c.entities, c.triplets = d["entities"], d["triplets"]

    g = nx.Graph()
    for c in targets:
        keys = list({_norm_entity(e) for e in c.entities if e})
        for k in keys:
            g.add_node(k)
        for a in range(len(keys)):
            for b in range(a + 1, len(keys)):
                w = g.get_edge_data(keys[a], keys[b], {}).get("weight", 0)
                g.add_edge(keys[a], keys[b], weight=w + 1)
        for s, r, o in c.triplets:
            ks, ko = _norm_entity(s), _norm_entity(o)
            if ks and ko:
                w = g.get_edge_data(ks, ko, {}).get("weight", 0)
                g.add_edge(ks, ko, weight=w + 2)
    if g.number_of_nodes():
        pr = nx.pagerank(g, weight="weight")
        mx = max(pr.values()) or 1.0
        for c in targets:
            ks = [_norm_entity(e) for e in c.entities if e]
            c.graph_importance = float(np.mean([pr.get(k, 0.0) for k in ks]) / mx) if ks else 0.0


# ======================================================================================
# 8) Propositions (índice companion, namespace aparte)
# ======================================================================================
_PROP_PROMPT = (
    "Descompón el siguiente fragmento en afirmaciones atómicas, autónomas y "
    "verificables (cada una entendible sin contexto). Devuelve SOLO un array JSON de "
    "strings, máximo 8. Fragmento:\n\n"
)


def _propositions_one(genai_client, text: str) -> list[str]:
    try:
        from google.genai import types
        resp = genai_client.models.generate_content(
            model=LLM_MODEL, contents=_PROP_PROMPT + text[:4000],
            config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
        )
        data = json.loads(resp.text)
        return [str(p).strip() for p in data if str(p).strip()][:8]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Propositions fallido: %s", exc)
        return []


def build_propositions(chunks: list[Chunk], genai_client) -> list[Chunk]:
    targets = [c for c in chunks if c.block_type == "text" and len(c.content) >= MIN_CHARS]
    logger.info("Extrayendo propositions de %d chunks...", len(targets))
    props: list[Chunk] = []
    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as ex:
        results = list(ex.map(lambda c: _propositions_one(genai_client, c.content), targets))
    for idx, (c, plist) in enumerate(zip(targets, results)):
        for k, p in enumerate(plist):
            props.append(Chunk(
                content=p, block_type="proposition", source=c.source, section=c.section,
                page=c.page, page_end=c.page_end, total_pages=c.total_pages,
                primary_category=c.primary_category, doc_type=c.doc_type, doc_year=c.doc_year,
                doc_issuer=c.doc_issuer, doc_lang=c.doc_lang, context=c.context,
                parent_id=f"chunk_{idx:06d}",
            ))
    return props


# ======================================================================================
# 9) Upsert
# ======================================================================================
def upsert_chunks(chunks: list[Chunk], index, namespace: str = DEFAULT_NAMESPACE,
                  batch: int = UPSERT_BATCH, id_prefix: str = "chunk") -> int:
    total = 0
    for start in range(0, len(chunks), batch):
        window = chunks[start:start + batch]
        vecs = embed_texts([c.embedding_text() for c in window], is_query=False)
        payload = [
            {"id": f"{id_prefix}_{start + k:06d}", "values": vecs[k].tolist(),
             "metadata": window[k].metadata()}
            for k in range(len(window))
        ]
        index.upsert(vectors=payload, namespace=namespace)
        total += len(payload)
        logger.info("  upsert [%s] %d/%d", namespace or "default", total, len(chunks))
    return total


# ======================================================================================
# Runner
# ======================================================================================
def iter_documents(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in _SUPPORTED:
            yield p


def _get_genai_client():
    try:
        from src.config import genai_client
        return genai_client
    except Exception:  # noqa: BLE001
        import os
        from google import genai
        return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _get_index():
    try:
        from src.config import pinecone_index as index
        assert index is not None
        return index
    except Exception:  # noqa: BLE001
        import os
        from pinecone import Pinecone
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        name = os.environ["PINECONE_INDEX_NAME"]
        return pc.Index(host=name) if name.startswith("http") else pc.Index(name)


def run(input_dir: str, *, embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        breakpoint_percentile: int = BREAKPOINT_PERCENTILE, min_chars: int = MIN_CHARS,
        max_chars: int = MAX_CHARS, namespace: str = DEFAULT_NAMESPACE,
        enrich: bool = False, index_propositions: bool = False,
        propositions_namespace: str = PROPOSITIONS_NAMESPACE,
        wipe: bool = False, dry_run: bool = False, limit: int | None = None) -> None:
    set_embedding_model(embedding_model)
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"No es un directorio: {root}")

    genai_client = _get_genai_client() if (enrich or index_propositions) else None

    docs = list(iter_documents(root))
    if limit:
        docs = docs[:limit]
    logger.info("Documentos: %d · modelo: %s · dim: %d · enrich=%s · propositions=%s",
                len(docs), embedding_model, embedding_dim(), enrich, index_propositions)

    all_chunks: list[Chunk] = []
    for doc in docs:
        cs = chunk_document(doc, root, genai_client, breakpoint_percentile,
                            min_chars, max_chars, enrich)
        di_types = {c.doc_type for c in cs}
        logger.info("%-58s -> %3d chunks  %s", str(doc.relative_to(root)), len(cs),
                    "/".join(di_types) if di_types else "")
        all_chunks.extend(cs)

    lens = [len(c.content) for c in all_chunks if c.block_type == "text"]
    if lens:
        logger.info("CHUNKS texto: %d · chars min/mediana/max = %d/%d/%d · <%d: %d · tablas+figuras: %d · con sección: %d",
                    len(lens), min(lens), int(np.median(lens)), max(lens), min_chars,
                    sum(1 for x in lens if x < min_chars),
                    sum(1 for c in all_chunks if c.block_type in ("table", "figure")),
                    sum(1 for c in all_chunks if c.section))

    if enrich and not dry_run:
        enrich_chunks(all_chunks, genai_client)

    props: list[Chunk] = []
    if index_propositions and not dry_run:
        props = build_propositions(all_chunks, genai_client)
        logger.info("Propositions: %d", len(props))

    if dry_run:
        for c in all_chunks[:6]:
            logger.info("--- %s · p%d · %s · [%s] %d ch\n  contexto: %s\n  %s",
                        c.source, c.page, c.section or "(sin sección)", c.block_type,
                        len(c.content), c.context, c.content[:280])
        logger.info("DRY-RUN: nada escrito en Pinecone.")
        return

    index = _get_index()
    if wipe:
        logger.warning("Borrando namespace %r...", namespace)
        index.delete(delete_all=True, namespace=namespace)
        if index_propositions:
            try:
                index.delete(delete_all=True, namespace=propositions_namespace)
            except Exception:  # noqa: BLE001
                pass

    n = upsert_chunks(all_chunks, index, namespace=namespace)
    logger.info("Indexados %d chunks en %r.", n, namespace or "default")
    if props:
        m = upsert_chunks(props, index, namespace=propositions_namespace, id_prefix="prop")
        logger.info("Indexadas %d propositions en %r.", m, propositions_namespace)


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Análisis + chunking semántico + indexación del corpus.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    ap.add_argument("--breakpoint-percentile", type=int, default=BREAKPOINT_PERCENTILE)
    ap.add_argument("--min-chars", type=int, default=MIN_CHARS)
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS)
    ap.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    ap.add_argument("--enrich", action="store_true",
                    help="Resumen de documento + summary/keywords/entities/triplets/PageRank por chunk (LLM).")
    ap.add_argument("--index-propositions", action="store_true",
                    help="Indexa además afirmaciones atómicas por chunk en un namespace aparte (LLM).")
    ap.add_argument("--propositions-namespace", default=PROPOSITIONS_NAMESPACE)
    ap.add_argument("--wipe", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", stream=sys.stdout,
    )
    for noisy in ("httpx", "httpcore", "urllib3", "huggingface_hub", "sentence_transformers",
                  "filelock", "pinecone", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    run(args.input, embedding_model=args.embedding_model,
        breakpoint_percentile=args.breakpoint_percentile, min_chars=args.min_chars,
        max_chars=args.max_chars, namespace=args.namespace, enrich=args.enrich,
        index_propositions=args.index_propositions,
        propositions_namespace=args.propositions_namespace, wipe=args.wipe,
        dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    _cli()
