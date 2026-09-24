#!/usr/bin/env python3
"""
Almacén local de unidades para el contexto adaptativo (docs/PLAN_CONTEXTO.md, fase 1).

Lee el índice v2 construido en local (.cache/idx_C.npz, el mismo que se cargó en Pinecone
con scripts/upsert_index_v2.py) y escribe data/unidades.json.gz (JSON comprimido: 2,2 MB
en lugar de 10,9):

  docs    documento → total de páginas, categoría e ids en el orden del texto
  chunks  id → documento, posición, unidad, etiqueta, páginas y texto
  units   clave «documento#unidad» (la misma que los nodos de data/normative_graph.json)
          → etiqueta, ids en orden y páginas

Con él, el servicio amplía un fragmento recuperado a su artículo completo, o a sus
vecinos si no tiene unidad, sin otra consulta a Pinecone.

  python scripts/build_unit_store.py --npz .cache/idx_C.npz --out data/unidades.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", default=".cache/idx_C.npz")
    ap.add_argument("--out", default="data/unidades.json.gz")
    ap.add_argument("--index", default="recavai-corpus-v2", help="índice de Pinecone con los mismos ids")
    a = ap.parse_args()

    raw = Path(a.npz).read_bytes()
    d = np.load(a.npz, allow_pickle=False)
    meta = json.loads(str(d["meta"]))
    assert all("id" in m for m in meta), "el .npz no es de troceado v2 (faltan ids)"

    chunks, docs, units = {}, defaultdict(list), {}
    for m in meta:
        seq = int(m["id"].rsplit("-", 1)[1])
        unit = f"{m['source']}#{m['article']}" if m.get("article") else None
        chunks[m["id"]] = {"s": m["source"], "n": seq, "u": unit, "l": m.get("unit_label") or "",
                           "p": _int(m.get("page")), "pe": _int(m.get("page_end")), "t": m["text"]}
        docs[m["source"]].append((seq, m["id"], _int(m.get("total_pages")), m.get("primary_category")))

    out_docs = {}
    for src, rows in docs.items():
        rows.sort()
        out_docs[src] = {"total_pages": rows[0][2], "category": rows[0][3], "ids": [r[1] for r in rows]}
        for _, cid, _, _ in rows:
            u = chunks[cid]["u"]
            if u:
                n = units.setdefault(u, {"label": chunks[cid]["l"], "ids": [], "pages": [None, None]})
                n["ids"].append(cid)
                p, pe = chunks[cid]["p"], chunks[cid]["pe"] or chunks[cid]["p"]
                if p is not None:
                    n["pages"][0] = p if n["pages"][0] is None else min(n["pages"][0], p)
                    n["pages"][1] = pe if n["pages"][1] is None else max(n["pages"][1], pe)

    store = {"version": 1, "index": a.index, "npz_sha1": hashlib.sha1(raw).hexdigest()[:12],
             "docs": out_docs, "units": units, "chunks": chunks}
    raw_json = json.dumps(store, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if a.out.endswith(".gz"):
        # mtime=0: el mismo índice produce exactamente el mismo fichero (sin cambios en git)
        with open(a.out, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", compresslevel=9, mtime=0) as gz:
            gz.write(raw_json)
    else:
        Path(a.out).write_bytes(raw_json)
    size = Path(a.out).stat().st_size / 1e6
    print(f"{a.out}: {len(out_docs)} documentos · {len(units)} unidades · {len(chunks)} fragmentos · {size:.1f} MB")


if __name__ == "__main__":
    main()
