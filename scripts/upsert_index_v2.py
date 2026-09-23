#!/usr/bin/env python3
"""
Carga en Pinecone el índice v2 construido en local (scripts/build_memory_index.py
--chunker-v2), con los mismos ids que referencia data/normative_graph.json.

Uso:
  python scripts/upsert_index_v2.py --npz .cache/idx_C.npz --index recavai-corpus-v2
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", required=True)
    ap.add_argument("--index", required=True)
    ap.add_argument("--batch", type=int, default=100)
    a = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from pinecone import Pinecone

    d = np.load(a.npz, allow_pickle=False)
    vecs, meta = d["vecs"], json.loads(str(d["meta"]))
    assert all("id" in m for m in meta), "el .npz no es de troceado v2 (faltan ids)"
    index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(a.index)

    done = 0
    for s in range(0, len(meta), a.batch):
        payload = []
        for k in range(s, min(s + a.batch, len(meta))):
            md = {key: v for key, v in meta[k].items() if key != "id" and v is not None}
            payload.append({"id": meta[k]["id"], "values": vecs[k].tolist(), "metadata": md})
        index.upsert(vectors=payload)
        done += len(payload)
        print(f"\r  {done}/{len(meta)}", end="", file=sys.stderr)
    print(file=sys.stderr)
    print(index.describe_index_stats())


if __name__ == "__main__":
    main()
