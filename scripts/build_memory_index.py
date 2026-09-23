#!/usr/bin/env python3
"""
Copia el índice de producción a un fichero local y, opcionalmente, recalcula sus
vectores con otro modelo de embeddings.

Sirve para aislar el efecto del MODELO: mismos fragmentos, mismos metadatos, solo
cambian los vectores. El resultado (.npz) lo consume `eval_battery.py --index
memory:<ruta>`, que implementa la misma interfaz de consulta que Pinecone.

Uso:
  python scripts/build_memory_index.py --out .cache/idx_A.npz                 # copia tal cual
  python scripts/build_memory_index.py --out .cache/idx_B.npz \\
      --reembed intfloat/multilingual-e5-small
  python scripts/build_memory_index.py --out .cache/idx_C.npz \\
      --chunker-v2 .cache/corpus_txt --reembed intfloat/multilingual-e5-small
      (troceado nuevo, src/chunking_v2.py; se embebe la cabecera de contexto + texto)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KEEP = ("text", "source", "page", "total_pages", "block_type", "primary_category")


def read_index(name: str, namespace: str = "__default__") -> tuple[list[str], np.ndarray, list[dict]]:
    from pinecone import Pinecone
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    index = pc.Index(name)
    ids, vecs, meta = [], [], []
    for page in index.list(namespace=namespace):
        batch = [getattr(i, "id", i) for i in page]
        if not batch:
            continue
        for vid, v in (index.fetch(ids=batch, namespace=namespace).vectors or {}).items():
            md = dict(getattr(v, "metadata", None) or {})
            ids.append(vid)
            vecs.append(v.values)
            meta.append({k: md.get(k) for k in KEEP})
        print(f"\r  leídos {len(ids)}", end="", file=sys.stderr)
    print(file=sys.stderr)
    return ids, np.asarray(vecs, dtype="float32"), meta


def page_quality(meta: list[dict]) -> None:
    """Cuántos fragmentos dicen estar en la página 1 de documentos de muchas páginas."""
    tot, p1 = Counter(), Counter()
    for m in meta:
        s = m["source"]
        tot[s] += 1
        if (m.get("page") in (0, 1, None)) and (m.get("total_pages") or 0) > 3:
            p1[s] += 1
    n1 = sum(p1.values())
    print(f"  fragmentos marcados en p.1 (o sin página) en documentos de >3 páginas: "
          f"{n1} de {len(meta)} ({n1/len(meta):.0%})", file=sys.stderr)
    for s, c in p1.most_common(6):
        print(f"    {c:5d}/{tot[s]:<5d} {s}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default="uclm-corpus-roma")
    ap.add_argument("--out", required=True)
    ap.add_argument("--reembed", help="modelo con el que recalcular los vectores")
    ap.add_argument("--cache-from", help="reutilizar metadatos de otro .npz en vez de leer Pinecone")
    ap.add_argument("--chunker-v2", metavar="TXT_DIR", help="trocear de nuevo con src/chunking_v2.py")
    a = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    embed_texts = None
    if a.chunker_v2:
        sys.path.insert(0, str(ROOT))
        from src import chunking_v2 as c2
        cats = c2.categories()
        pairs = c2.chunk_corpus(Path(a.chunker_v2))
        meta = [dict(c.metadata(c2.category_of(c.source, cats)), id=vid) for vid, c in pairs]
        embed_texts = [c.embed_text for _, c in pairs]
        vecs = np.zeros((len(meta), 1), dtype="float32")
        print(f"  troceado v2: {len(meta)} fragmentos", file=sys.stderr)
    elif a.cache_from:
        d = np.load(a.cache_from, allow_pickle=False)
        vecs, meta = d["vecs"], json.loads(str(d["meta"]))
    else:
        _ids, vecs, meta = read_index(a.index)
    page_quality(meta)

    if a.reembed:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(a.reembed)
        prefix = "passage: " if "e5" in a.reembed.lower() else ""
        texts = [prefix + t for t in (embed_texts or [x.get("text") or "" for x in meta])]
        t0 = time.time()
        vecs = m.encode(texts, batch_size=64, normalize_embeddings=True, convert_to_numpy=True,
                        show_progress_bar=True).astype("float32")
        print(f"  recalculados {len(texts)} vectores con {a.reembed} en {time.time()-t0:.0f} s "
              f"(dim {vecs.shape[1]})", file=sys.stderr)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez(a.out, vecs=vecs, meta=np.array(json.dumps(meta, ensure_ascii=False)))
    print(f"  → {a.out}  {vecs.shape}", file=sys.stderr)


if __name__ == "__main__":
    main()
