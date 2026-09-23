#!/usr/bin/env python3
"""
Extrae el texto de cada documento del corpus a un .txt con marcas de página.

Es una caché de trabajo: la usan el verificador de la batería de evaluación
(benchmarks/bateria_v1.py --verify) y cualquier búsqueda literal sobre el corpus.
No forma parte del sistema en producción.

Formato: un fichero por documento, con la ruta relativa y '/' sustituida por '__',
y una línea '=== p.N ===' al comienzo de cada página. Se eliminan los guiones
blandos (U+00AD) que parten palabras al final de línea en los PDF de EUR-Lex.

Uso:
  python scripts/extract_corpus_text.py --corpus "<carpeta>" --out .cache/corpus_txt
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def extract(path: Path) -> str:
    parts: list[str] = []
    if path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                parts.append(f"\n=== p.{i} ===\n" + (page.extract_text() or ""))
    else:
        from docx import Document
        parts.append("\n=== p.1 ===\n" + "\n".join(p.text for p in Document(str(path)).paragraphs))
    return re.sub("­\\s*", "", "".join(parts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", default=".cache/corpus_txt")
    ap.add_argument("--force", action="store_true", help="reextraer aunque exista")
    a = ap.parse_args()

    root = Path(a.corpus).expanduser().resolve()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".pdf", ".docx"):
            continue
        src = str(p.relative_to(root).with_suffix(""))
        dst = out / (src.replace("/", "__") + ".txt")
        if dst.exists() and not a.force:
            continue
        dst.write_text(extract(p), encoding="utf-8")
        n += 1
        print(f"  {src}")
    print(f"{n} documentos extraídos en {out}")


if __name__ == "__main__":
    main()
