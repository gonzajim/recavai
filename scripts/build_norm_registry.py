#!/usr/bin/env python3
"""
Registro de números de norma que aparecen en el corpus (data/normas_corpus.json).

Lo usan dos controles del asesor (src/agent/guardrails.py):
  - entrada: si la pregunta nombra una norma que no está en el registro («Ley 11/2018»),
    se avisa al modelo de que no está en la base documental.
  - salida: un número de norma de la respuesta que no está ni en los fragmentos ni en
    el registro («Directiva 2024/1109») se trata como inventado.

Uso:
  python scripts/build_norm_registry.py --txt .cache/corpus_txt --out data/normas_corpus.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

_NORM_ID = re.compile(r"(?<![\d./])(\d{1,4})/(\d{2,4})(?:/(?:UE|CE|CEE))?(?![\d/])")
# Solo cuenta como norma si va cerca de una palabra que lo indique: descarta fracciones,
# fechas sueltas y numeraciones de página.
_CONTEXT = re.compile(r"(directiva|reglamento|decisi[óo]n|ley|real decreto|recomendaci[óo]n|"
                      r"convenio|n\.?\s*[ºo°]|do\s+l|diario oficial)", re.I)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--txt", default=".cache/corpus_txt")
    ap.add_argument("--out", default="data/normas_corpus.json")
    a = ap.parse_args()
    found: Counter = Counter()
    for f in sorted(Path(a.txt).glob("*.txt")):
        t = f.read_text(encoding="utf-8")
        for m in _NORM_ID.finditer(t):
            if _CONTEXT.search(t[max(0, m.start() - 45):m.start()]):
                found[f"{int(m.group(1))}/{int(m.group(2))}"] += 1
    out = {"descripcion": "Números de norma (AAAA/N o N/AAAA) citados en el corpus; ver scripts/build_norm_registry.py",
           "normas": sorted(found)}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(found)} números de norma → {a.out}")
    for k in ("2024/1760", "2022/2464", "2023/2772", "2025/794", "2026/470", "2013/34", "11/2018", "2024/1109"):
        print(f"  {k:<10} {'sí' if k in found else 'NO'}")


if __name__ == "__main__":
    main()
