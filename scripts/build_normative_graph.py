#!/usr/bin/env python3
"""
Construye el grafo normativo (data/normative_graph.json) a partir del troceado v2.

Determinista: las aristas salen de las referencias cruzadas que hace el propio texto
y de los marcadores de modificación de EUR-Lex. Ver src/normative_graph.py.

Uso:
  python scripts/build_normative_graph.py --txt .cache/corpus_txt --out data/normative_graph.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--txt", default=".cache/corpus_txt")
    ap.add_argument("--out", default="data/normative_graph.json")
    a = ap.parse_args()

    import logging
    logging.disable(logging.INFO)
    import types
    cfg = types.ModuleType("src.config"); cfg.logger = logging.getLogger("graph")
    sys.modules.setdefault("src.config", cfg)
    from src import chunking_v2 as c2
    from src import normative_graph as ng

    pairs = c2.chunk_corpus(Path(a.txt))
    g = ng.build(pairs)
    Path(a.out).write_text(json.dumps(g, ensure_ascii=False, indent=0), encoding="utf-8")

    types_ = Counter(e["type"] for e in g["edges"])
    units = [k for k, n in g["nodes"].items() if not n["unit"].startswith("mod.")]
    by_doc = Counter(g["nodes"][k]["source"].split("/")[-1] for k in units)
    print(f"nodos: {len(g['nodes'])} ({len(units)} unidades normativas) · aristas: {len(g['edges'])} {dict(types_)}")
    print("documentos con más unidades:", by_doc.most_common(6))
    indeg = Counter(e["to"] for e in g["edges"] if e["type"] == "REFERENCIA")
    print("unidades más referenciadas:", [(k.split('/')[-1], v) for k, v in indeg.most_common(6)])
    print(f"→ {a.out} ({Path(a.out).stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
