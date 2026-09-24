"""
Contexto adaptativo (docs/PLAN_CONTEXTO.md): de los fragmentos recuperados a los bloques
que lee el modelo.

    recuperar para localizar ─► ampliar cada fragmento a su unidad (artículo, requisito
    NEIS, contenido GRI) o, si no tiene, a sus vecinos ─► llenar el presupuesto del nivel
    por orden de prioridad ─► presentar agrupado por documento y en el orden del texto

Por qué. Con los 6 mejores fragmentos solo llegaba al modelo el 48 % de los hechos clave
de la batería; ampliando 12 fragmentos a su unidad, el 77 %, con unos 9.600 tokens. Las
respuestas incompletas no eran fallos del modelo: el fragmento traía media lista o medio
artículo y la habilidad de fundamentación le prohíbe completar de memoria.

Niveles (presupuesto de contexto en tokens):
  S   6.000   la pregunta nombra la unidad; verificación del auditor
  M  12.000   por defecto
  L  45.000   enumeraciones, panorama de una norma y segunda pasada
RAG_CONTEXT_MAX_LEVEL limita el nivel máximo (p. ej. M desactiva L y la segunda pasada).

Orden de lectura (OP-RAG, Yu et al., 2024): los documentos por su mejor fragmento y,
dentro de cada uno, el orden del texto. Cada bloque es una unidad o un tramo contiguo,
con su cabecera, y lleva su número [n]: las citas apuntan a unidades.

Se activa con RAG_UNIT_STORE=<ruta a data/unidades.json.gz>, que genera
scripts/build_unit_store.py desde el mismo índice que Pinecone. Sin él, cada fragmento es
un bloque (comportamiento anterior). Si está activado y no carga, ERROR en el registro.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from src.config import logger

CHARS_PER_TOKEN = 4.18       # medido con countTokens de Gemini sobre el corpus (24/09/2026)
CANDIDATE_K = 30             # candidatos del corpus por búsqueda (antes 12, con tope de 6)
SECOND_PASS_K = 50           # en la segunda pasada, para lo que no salió entre los 30


@dataclass(frozen=True)
class Level:
    name: str
    budget: int              # tokens de contexto
    unit_max: int            # una unidad entra entera si no supera esto
    window: int              # vecinos a cada lado si no hay unidad o la unidad es mayor


LEVELS = {
    "S": Level("S", 6_000, 6_000, 1),
    "M": Level("M", 12_000, 4_000, 1),
    "L": Level("L", 45_000, 17_000, 3),
}
ORDER = "SML"


def max_level() -> str:
    v = os.getenv("RAG_CONTEXT_MAX_LEVEL", "L").strip().upper()
    return v if v in LEVELS else "L"


def clamp(level: str) -> str:
    top = max_level()
    return level if ORDER.index(level) <= ORDER.index(top) else top


def next_level(level: str) -> str | None:
    i = ORDER.index(level)
    nxt = ORDER[i + 1] if i + 1 < len(ORDER) else None
    return nxt if nxt and clamp(nxt) == nxt else None


def tokens_of(text_or_chars) -> int:
    n = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars or "")
    return round(n / CHARS_PER_TOKEN)


def join_overlapping(texts: list[str]) -> str:
    """Une fragmentos consecutivos quitando la frase de solape que repite chunking_v2."""
    out = ""
    for t in texts:
        if not out:
            out = t
            continue
        k_max = min(len(out), len(t), 700)
        cut = next((k for k in range(k_max, 15, -1) if out.endswith(t[:k])), 0)
        out = out + ("\n" if not cut else "") + t[cut:]
    return out


# ======================================================================================
# Almacén de unidades
# ======================================================================================
class UnitStore:
    def __init__(self, data: dict):
        self.docs = data["docs"]
        self.units = data["units"]
        self.chunks = data["chunks"]
        self.index = data.get("index")
        self._pos = {cid: i for d in self.docs.values() for i, cid in enumerate(d["ids"])}

    def chunk(self, cid: str | None) -> dict | None:
        return self.chunks.get(cid) if cid else None

    def chars(self, ids) -> int:
        return sum(len(self.chunks[i]["t"]) for i in ids)

    def window(self, cid: str, w: int) -> list[str]:
        """El fragmento y sus w vecinos a cada lado, del mismo documento y de la misma
        unidad (o, si no tiene, vecinos que tampoco la tengan): una ventana que cruzara a un
        artículo metería un trozo suelto de él."""
        c = self.chunks[cid]
        ids = self.docs[c["s"]]["ids"]
        p = self._pos[cid]
        return [i for i in ids[max(0, p - w): p + w + 1] if self.chunks[i]["u"] == c["u"]]

    def unit_ids(self, key: str) -> list[str]:
        return list(self.units.get(key, {}).get("ids", []))

    def expansion(self, cid: str, level: Level) -> list[str]:
        """Qué entra en el contexto por haber recuperado `cid` en este nivel."""
        c = self.chunks[cid]
        if c["u"]:
            ids = self.unit_ids(c["u"])
            if tokens_of(self.chars(ids)) <= level.unit_max:
                return ids
        return self.window(cid, level.window)

    def document_ids(self, source: str) -> list[str]:
        return list(self.docs.get(source, {}).get("ids", []))


_STORE: UnitStore | None = None
_LOADED = False
_LOCK = threading.Lock()


def get_unit_store() -> UnitStore | None:
    global _STORE, _LOADED
    if _LOADED:
        return _STORE
    with _LOCK:
        if _LOADED:
            return _STORE
        path = os.getenv("RAG_UNIT_STORE", "").strip()
        if path:
            try:
                raw = Path(path).read_bytes()
                _STORE = UnitStore(json.loads(gzip.decompress(raw) if path.endswith(".gz") else raw))
                logger.info("Almacén de unidades cargado: %d unidades, %d fragmentos, %s",
                            len(_STORE.units), len(_STORE.chunks), path)
            except Exception:
                logger.error("Almacén de unidades ACTIVADO pero no se pudo cargar %s", path, exc_info=True)
                _STORE = None
        _LOADED = True
    return _STORE


# ======================================================================================
# Construcción de bloques
# ======================================================================================
def _fragment_block(d: dict) -> dict:
    b = dict(d)
    b.setdefault("excerpt", (d.get("content") or "")[:220])
    return b


def build_blocks(docs: list[dict], level: str = "M", store: UnitStore | None = None,
                 whole_documents: list[str] | None = None) -> list[dict]:
    """Bloques de contexto a partir de los fragmentos recuperados, en orden de prioridad
    (referencia explícita, búsqueda, grafo). Sin almacén: un bloque por fragmento."""
    if store is None:
        return [_fragment_block(d) for d in docs]
    lv = LEVELS[level]
    budget = int(lv.budget * CHARS_PER_TOKEN)

    chosen: dict[str, float] = {}           # id → mejor puntuación de quien lo trajo
    first_seen: dict[str, int] = {}         # documento → prioridad de su mejor fragmento
    best_hit: dict[str, str] = {}           # id recuperado → su texto (para el extracto)
    loose: list[dict] = []                  # fragmentos fuera del almacén (PDF del usuario)
    used = 0

    def take(ids, score, rank, hit=None) -> bool:
        nonlocal used
        new = [i for i in ids if i not in chosen]
        size = store.chars(new)
        if used + size > budget:
            return False
        for i in new:
            chosen[i] = score
        for i in ids:
            chosen[i] = max(chosen[i], score)
        src = store.chunks[ids[0]]["s"]
        first_seen.setdefault(src, rank)
        if hit:
            best_hit.setdefault(hit, store.chunks[hit]["t"])
        used += size
        return True

    rank = 0
    for src in whole_documents or []:
        ids = store.document_ids(src)
        if ids and take(ids, 1.0, rank):
            rank += 1

    for d in docs:
        rank += 1
        cid = d.get("id")
        c = store.chunk(cid)
        score = float(d.get("score") or 0.0)
        if c is None:
            size = len(d.get("content") or "")
            key = (d.get("title"), (d.get("content") or "")[:80])
            if any((x.get("title"), (x.get("content") or "")[:80]) == key for x in loose):
                continue
            if used + size <= budget:
                loose.append(dict(_fragment_block(d), _rank=rank))
                used += size
            continue
        if cid in chosen:
            chosen[cid] = max(chosen[cid], score)
            best_hit.setdefault(cid, c["t"])
            continue
        if not take(store.expansion(cid, lv), score, rank, hit=cid):
            take([cid], score, rank, hit=cid)       # si la unidad no cabe, al menos el fragmento

    # Presentación: documentos por prioridad; dentro, tramos contiguos de la misma unidad.
    blocks: list[dict] = []
    by_doc: dict[str, list[str]] = {}
    for cid in chosen:
        by_doc.setdefault(store.chunks[cid]["s"], []).append(cid)
    items = [(first_seen.get(src, 10**6), "doc", src) for src in by_doc] + \
            [(x["_rank"], "loose", x) for x in loose]
    for _, kind, obj in sorted(items, key=lambda t: t[0]):
        if kind == "loose":
            blocks.append({k: v for k, v in obj.items() if k != "_rank"})
            continue
        src, meta = obj, store.docs[obj]
        ids = sorted(by_doc[src], key=lambda i: store._pos[i])
        run: list[str] = []
        for cid in ids + [None]:
            if run and (cid is None or store._pos[cid] != store._pos[run[-1]] + 1
                        or store.chunks[cid]["u"] != store.chunks[run[-1]]["u"]):
                blocks.append(_block(store, src, meta, run, chosen, best_hit))
                run = []
            if cid is not None:
                run.append(cid)
    return blocks


def _block(store: UnitStore, src: str, meta: dict, ids: list[str], chosen: dict, best_hit: dict) -> dict:
    cs = [store.chunks[i] for i in ids]
    unit = cs[0]["u"]
    pages = [c["p"] for c in cs if c["p"] is not None]
    ends = [c["pe"] or c["p"] for c in cs if c["p"] is not None]
    hit = next((i for i in ids if i in best_hit), ids[0])
    return {
        "id": ids[0], "ids": ids, "title": src, "category": meta.get("category") or "",
        "content": join_overlapping([c["t"] for c in cs]),
        "page": min(pages) if pages else None, "page_end": max(ends) if ends else None,
        "total_pages": meta.get("total_pages"),
        "unit_label": cs[0]["l"] or None,           # sin unidad: el título de la sección
        "article": unit.split("#", 1)[1] if unit else None,
        "score": max(chosen[i] for i in ids),
        "excerpt": store.chunks[hit]["t"][:220],
    }
