"""
Troceado v2: estructura normativa primero, tamaño después.

La unidad natural de un texto regulatorio no es un número de caracteres: es el
artículo, el requisito de divulgación NEIS o el contenido GRI. Este troceador:

  1. Limpia el texto de cada página: cabeceras y pies repetidos, líneas de índice
     (con puntos de relleno), la cabecera de EUR-Lex («02024L1760 — ES — …») y los
     guiones blandos que parten palabras.
  2. Detecta las unidades normativas según la familia del documento:
       EUR-Lex (CSDDD, CSRD)  «Artículo N», «ANEXO»
       NEIS                   «Requisito de divulgación E1-6», «NEIS E1», anexos
       GRI                    «Contenido 305-1» (solo el cuerpo, no el índice), «Tema 14.8»
       resto (OCDE, marco…)   títulos genéricos (corpus_pipeline._detect_headings)
  3. Nunca cruza una frontera de unidad. Dentro de cada unidad agrupa frases hasta
     ~1.400 caracteres (máximo 2.000), con una frase de solape, y fusiona el último
     trozo si queda demasiado pequeño.
  4. Antepone una cabecera de contexto («CSDDD … · Artículo 10 · Prevención de
     efectos adversos potenciales») al texto QUE SE EMBEBE, pero no al que se cita.
  5. Guarda en metadatos la página inicial y final, la unidad normativa (`article`),
     su etiqueta legible y qué modificaciones del texto consolidado contiene
     (▼M1, ▼M2…).

Con multilingual-e5-small (512 tokens), 2.000 caracteres de español más la cabecera
caben sin truncado. Con all-MiniLM-L6-v2 (256) no cabían: por eso el troceado se
cambia a la vez que el modelo.

El resultado es determinista: no usa LLM ni embeddings para decidir los cortes.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from src import corpus_pipeline as cp
from src.doc_titles import DOC_TITLES, doc_title  # noqa: F401  (se reexportan)

TARGET_CHARS = 1400
MAX_CHARS = 2000
MIN_CHARS = 350

ROOT = Path(__file__).resolve().parent.parent
_CATEGORIES_FILE = ROOT / "data" / "categorias_v1.json"


_EURLEX_HEADER = re.compile(r"^\s*0\d{4}[LR]\d{4}\s*[—–-]\s*[A-Z]{2}\s*[—–-].*$")
_DOT_LEADER = re.compile(r"\.{5,}|…{3,}")
_MARKER = re.compile(r"[▼►◄]\s*(?:(M\s?\d+|C\s?\d+|B)\b)?")
# «Artículo 19 bis» con comilla: artículos que la CSRD inserta en la Directiva 2013/34/UE.
_ART = re.compile(r"(?m)^\s*«?\s*Art[íi]culo\s+(\d+)\s*(bis|ter|qu[áa]ter|quinquies|sexies)?\s*$")
_ANEXO = re.compile(r"(?m)^\s*ANEXO\s*([IVX]+)?\s*$")
_NEIS_DR = re.compile(r"(?m)^Requisito de divulgación (?:relacionado con NEIS 2 )?((?:[EGS]\d|SBM|IRO|GOV|BP|MDR)-\w{1,2})\b\s*[:–-]?\s*(.*)$")
_NEIS_STD = re.compile(r"(?m)^NEIS ([12]|[EGS]\d)\b\s*(.{0,80})$")
_GRI_DISC = re.compile(r"(?m)^Contenido (\d{1,3}-\d{1,2})\b\s*(.*)$")
_GRI_TOPIC = re.compile(r"(?m)^Tema (\d{1,2}\.\d{1,2}) (.+)$")


@dataclass
class Unit:
    start: int
    uid: str | None          # id normalizado: art.10, anexo.I, esrs.E1-6, gri.305-1, gri.14.8
    label: str               # «Artículo 10», «Requisito de divulgación E1-6»…
    title: str = ""
    section: str = ""


@dataclass
class ChunkV2:
    text: str
    embed_text: str
    source: str
    page: int
    page_end: int
    total_pages: int
    article: str | None
    unit_label: str
    section: str
    modifications: list[str] = field(default_factory=list)

    def metadata(self, category: str) -> dict:
        md = {
            "text": self.text, "source": self.source, "page": self.page, "page_end": self.page_end,
            "total_pages": self.total_pages, "block_type": "text", "primary_category": category,
            "unit_label": self.unit_label, "section": self.section, "chunker": "v2",
        }
        if self.article:
            md["article"] = self.article
        if self.modifications:
            md["modifications"] = self.modifications
        return md


# ======================================================================================
# 1) Páginas limpias
# ======================================================================================
def _norm_line(s: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", s.strip().lower()))


def clean_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Quita cabeceras/pies que se repiten en muchas páginas, líneas de índice y la
    cabecera de EUR-Lex. Conserva los marcadores ▼M2 (se leen y se quitan al trocear)."""
    edge = Counter()
    for _, t in pages:
        lines = [l for l in t.splitlines() if l.strip()]
        for l in lines[:2] + lines[-2:]:
            edge[_norm_line(l)] += 1
    thr = max(3, int(0.3 * len(pages)))
    boiler = {k for k, v in edge.items() if v >= thr and len(k) < 160}
    out = []
    for p, t in pages:
        keep = []
        for l in t.splitlines():
            s = l.strip()
            if not s or _EURLEX_HEADER.match(s) or _DOT_LEADER.search(s) or _norm_line(s) in boiler:
                continue
            keep.append(l.rstrip())
        out.append((p, "\n".join(keep)))
    return out


def read_pages(txt_path: Path) -> list[tuple[int, str]]:
    t = txt_path.read_text(encoding="utf-8")
    parts = re.split(r"=== p\.(\d+) ===", t)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


# ======================================================================================
# 2) Unidades normativas
# ======================================================================================
def family(source: str) -> str:
    if source in ("02_NORMATIVAS/01_CSDDD", "02_NORMATIVAS/02_CSDR"):
        return "eurlex"
    if source == "02_NORMATIVAS/03_NEIS":
        return "neis"
    if "/PROCESOS GRI/" in source:
        return "gri"
    return "generic"


def _next_line(text: str, pos: int) -> str:
    rest = text[pos:].split("\n", 2)
    return rest[1].strip() if len(rest) > 1 else ""


def detect_units(text: str, spans: list[tuple[int, int, int]], fam: str) -> list[Unit]:
    units: list[Unit] = []
    if fam in ("eurlex", "neis"):
        for m in _ART.finditer(text):
            n = m.group(1) + (f" {m.group(2).lower()}" if m.group(2) else "")
            uid = f"art.{m.group(1)}" + (f".{m.group(2).lower()}" if m.group(2) else "")
            title = _next_line(text, m.start())
            if len(title) > 140 or re.match(r"^\d+\.", title):
                title = ""
            units.append(Unit(m.start(), uid, f"Artículo {n}", title))
        for m in _ANEXO.finditer(text):
            roman = (m.group(1) or "I").upper()
            units.append(Unit(m.start(), f"anexo.{roman}", f"Anexo {roman}"))
    if fam == "neis":
        std = ""
        heads = []
        for m in _NEIS_STD.finditer(text):
            heads.append((m.start(), "std", m))
        for m in _NEIS_DR.finditer(text):
            heads.append((m.start(), "dr", m))
        for pos, kind, m in sorted(heads, key=lambda x: x[0]):
            if kind == "std":
                std = f"NEIS {m.group(1)}"
                units.append(Unit(pos, None, std, m.group(2).strip()))
            else:
                code = m.group(1).upper()
                units.append(Unit(pos, f"esrs.{code}", f"Requisito de divulgación {code}",
                                  m.group(2).strip()[:140], std))
    if fam == "gri":
        for m in _GRI_DISC.finditer(text):
            ahead = text[m.end(): m.end() + 350]
            if re.search(r"organizaci[óo]n informante debe|REQUERIMIENTOS", ahead) and not re.search(r"\s\d{1,3}$", m.group(0)):
                units.append(Unit(m.start(), f"gri.{m.group(1)}", f"Contenido {m.group(1)}", m.group(2).strip()[:140]))
        for m in _GRI_TOPIC.finditer(text):
            line = m.group(0).rstrip()
            nxt = _next_line(text, m.start())
            if re.search(r"\s\d{1,3}$", line) or nxt.startswith("Tema "):
                continue                              # índice o lista, no el cuerpo
            units.append(Unit(m.start(), f"gri.{m.group(1)}", f"Tema {m.group(1)}", m.group(2).strip()[:140]))
    if fam == "generic":
        heads = cp._detect_headings(text, spans)
        for h in heads:
            units.append(Unit(h.start, None, h.label, "", cp._section_path_of(h.start, heads)))

    units.sort(key=lambda u: u.start)
    dedup: list[Unit] = []
    for u in units:
        if dedup and u.start == dedup[-1].start:
            continue
        dedup.append(u)
    if not dedup or dedup[0].start > 0:
        dedup.insert(0, Unit(0, None, "Introducción"))
    return dedup


# ======================================================================================
# 3) Troceado dentro de cada unidad
# ======================================================================================
def _page_of(offset: int, spans: list[tuple[int, int, int]]) -> int:
    for p, s, e in spans:
        if s <= offset < e:
            return p
    return spans[-1][0] if spans else 1


def _clean_chunk(raw: str) -> tuple[str, list[str]]:
    mods = sorted({re.sub(r"\s", "", m) for m in re.findall(r"[▼►]\s*(M\s?\d+|C\s?\d+)", raw)})
    txt = _MARKER.sub(" ", raw)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", " ", txt).strip()
    return txt, mods


def _pack(sentences: list[cp.Sentence]) -> list[list[cp.Sentence]]:
    groups: list[list[cp.Sentence]] = []
    cur: list[cp.Sentence] = []
    size = 0
    for s in sentences:
        ln = len(s.text)
        if cur and size + ln > TARGET_CHARS and (size >= MIN_CHARS or size + ln > MAX_CHARS):
            groups.append(cur)
            tail = cur[-1] if len(cur[-1].text) < 300 else None     # una frase de solape
            cur, size = ([tail] if tail else []), (len(tail.text) if tail else 0)
        cur.append(s)
        size += ln
    if cur:
        if groups and size < MIN_CHARS and sum(len(x.text) for x in groups[-1]) + size <= MAX_CHARS + 300:
            groups[-1].extend(x for x in cur if x not in groups[-1])
        else:
            groups.append(cur)
    # grupos que siguen pasándose del máximo (frases gigantes: tablas aplanadas, listas sin
    # puntuación): cortar por palabras
    out: list[list[cp.Sentence]] = []
    for g in groups:
        if sum(len(x.text) + 1 for x in g) > MAX_CHARS:
            s = cp.Sentence(" ".join(x.text for x in g), g[0].start, g[-1].end)
            words, buf, start = s.text.split(" "), [], s.start
            for w in words:
                buf.append(w)
                if sum(len(x) + 1 for x in buf) > TARGET_CHARS:
                    t = " ".join(buf)
                    out.append([cp.Sentence(t, start, start + len(t))])
                    start += len(t) + 1
                    buf = []
            if buf:
                t = " ".join(buf)
                out.append([cp.Sentence(t, start, start + len(t))])
        else:
            out.append(g)
    return out




def chunk_pages(source: str, pages: list[tuple[int, str]]) -> list[ChunkV2]:
    pages = clean_pages(pages)
    total = max((p for p, _ in pages), default=1)
    text_parts, spans, pos = [], [], 0
    for p, t in pages:
        seg = t + "\n"
        spans.append((p, pos, pos + len(seg)))
        text_parts.append(seg)
        pos += len(seg)
    text = "".join(text_parts)
    fam = family(source)
    units = detect_units(text, spans, fam)
    title = doc_title(source)

    chunks: list[ChunkV2] = []
    for i, u in enumerate(units):
        end = units[i + 1].start if i + 1 < len(units) else len(text)
        body = text[u.start:end]
        if len(body.strip()) < 20:
            continue
        sents = cp._split_sentences(body, u.start)
        for g in _pack(sents):
            raw = " ".join(s.text for s in g)
            clean, mods = _clean_chunk(raw)
            if len(clean) < 25:
                continue
            header = " · ".join(x for x in (title, u.section if fam == "generic" else u.section,
                                             u.label, u.title) if x)
            chunks.append(ChunkV2(
                text=clean, embed_text=f"{header}\n{clean}", source=source,
                page=_page_of(g[0].start, spans), page_end=_page_of(max(g[-1].end - 1, g[0].start), spans),
                total_pages=total, article=u.uid, unit_label=u.label + (f" · {u.title}" if u.title else ""),
                section=u.section, modifications=mods))
    return chunks


# ======================================================================================
# 4) Corpus completo
# ======================================================================================
def _ascii_slug(s: str, n: int = 36) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:n]


def vector_id(source: str, k: int) -> str:
    """Id por documento (no global): permite reindexar un documento sin tocar el resto."""
    h = hashlib.sha1(source.encode("utf-8")).hexdigest()[:8]
    return f"{_ascii_slug(source.split('/')[-1])}-{h}-{k:04d}"


def categories() -> dict[str, str]:
    try:
        return json.loads(_CATEGORIES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def category_of(source: str, cats: dict[str, str]) -> str:
    if source in cats:
        return cats[source]
    low = source.lower()
    if "gri" in low:
        return "GRI"
    if "csddd" in low:
        return "CSDDD"
    return "general"


def chunk_corpus(txt_dir: Path) -> list[tuple[str, ChunkV2]]:
    """[(id, chunk)] para todo el corpus a partir de la caché de texto
    (scripts/extract_corpus_text.py)."""
    out = []
    for f in sorted(Path(txt_dir).glob("*.txt")):
        source = f.stem.replace("__", "/")
        for k, c in enumerate(chunk_pages(source, read_pages(f))):
            out.append((vector_id(source, k), c))
    return out
