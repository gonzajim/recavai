#!/usr/bin/env python3
"""
Comprueba si los documentos de EUR-Lex del corpus siguen siendo la última versión
consolidada publicada.

La normativa europea se consolida cada vez que una modificación entra en vigor. El
corpus guarda un PDF con la consolidación vigente el día que se descargó; meses
después puede haber otra. Un asistente que cita una consolidación superada da una
respuesta legalmente equivocada sin dar ninguna señal de que lo está haciendo, así
que conviene comprobarlo de vez en cuando.

Cómo lo hace:
  1. Lee la cabecera de cada PDF. Los textos consolidados de EUR-Lex empiezan por
     una línea del tipo `02024L1760 — ES — 18.03.2026 — 002.002`: CELEX base, lengua,
     fecha de consolidación y número de versión.
  2. Pregunta al repositorio Cellar de la Oficina de Publicaciones (SPARQL) qué
     consolidaciones existen para ese CELEX base y se queda con la más reciente.
  3. Compara fechas.

No descarga nada ni toca el índice: solo informa.

Uso:
  python scripts/check_eurlex_versions.py --corpus "/ruta/al/corpus"
  python scripts/check_eurlex_versions.py --corpus "/ruta" --json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"

# `02024L1760 — ES — 18.03.2026 — 002.002`. El separador es una raya larga (—) y a
# veces viene con espacios finos, así que se acepta cualquier guión/raya.
_HEADER = re.compile(
    r"\b(?P<celex>[0-9]{5}[A-Z][0-9]{4})\s*[—–-]\s*(?P<lang>[A-Z]{2})\s*[—–-]\s*"
    r"(?P<fecha>\d{2}\.\d{2}\.\d{4})\s*[—–-]\s*(?P<version>\d{3}\.\d{3})"
)

# Un CELEX consolidado es el original con un 0 delante: 32024L1760 -> 02024L1760.
_CONSOLIDATED_ID = re.compile(r"^(?P<base>0\d{4}[A-Z]\d{4})-(?P<fecha>\d{8})$")


@dataclass
class Check:
    fichero: str
    celex: str
    version_local: str          # dd.mm.aaaa
    version_ultima: str         # dd.mm.aaaa
    numero_version: str
    estado: str                 # "al dia" | "DESACTUALIZADO" | "sin datos" | "no es eur-lex"
    url: str = ""


def header_of(path: Path, pages: int = 2) -> re.Match | None:
    """Lee las primeras páginas y busca la cabecera de texto consolidado."""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages[:pages])
    except Exception:                                    # noqa: BLE001
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = "\n".join((p.extract_text() or "") for p in reader.pages[:pages])
        except Exception:                                # noqa: BLE001
            return None
    return _HEADER.search(text)


def latest_consolidation(celex_base: str) -> str | None:
    """Fecha (aaaammdd) de la consolidación más reciente publicada para ese CELEX.

    Se consulta Cellar en vez de eur-lex.europa.eu porque el portal web responde 202
    a los clientes automáticos; el endpoint SPARQL sí contesta.
    """
    query = (
        "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
        "SELECT DISTINCT ?id WHERE { ?x cdm:resource_legal_id_celex ?id . "
        f'FILTER(STRSTARTS(STR(?id),"{celex_base}")) }} ORDER BY DESC(?id) LIMIT 20'
    )
    url = f"{SPARQL}?{urllib.parse.urlencode({'query': query})}"
    req = urllib.request.Request(url, headers={"Accept": "text/csv"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as e:                               # noqa: BLE001
        print(f"  (Cellar no respondió para {celex_base}: {type(e).__name__})", file=sys.stderr)
        return None

    fechas = []
    for row in csv.DictReader(io.StringIO(body)):
        m = _CONSOLIDATED_ID.match((row.get("id") or "").strip())
        if m:
            fechas.append(m.group("fecha"))
    return max(fechas) if fechas else None


def check(path: Path, root: Path) -> Check:
    rel = str(path.relative_to(root))
    m = header_of(path)
    if not m:
        return Check(rel, "", "", "", "", "no es eur-lex")

    celex = m.group("celex")                 # ya viene con el 0 inicial: 02024L1760
    local = m.group("fecha")                 # dd.mm.aaaa
    local_iso = "".join(reversed(local.split(".")))       # aaaammdd

    ultima = latest_consolidation(celex)
    if not ultima:
        return Check(rel, celex, local, "", m.group("version"), "sin datos")

    bonita = f"{ultima[6:8]}.{ultima[4:6]}.{ultima[:4]}"
    estado = "al dia" if ultima <= local_iso else "DESACTUALIZADO"
    url = (f"https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:{celex}-{ultima}"
           if estado == "DESACTUALIZADO" else "")
    return Check(rel, celex, local, bonita, m.group("version"), estado, url)


def main() -> int:
    ap = argparse.ArgumentParser(description="¿Siguen vigentes las versiones de EUR-Lex del corpus?")
    ap.add_argument("--corpus", required=True, help="Carpeta raíz del corpus.")
    ap.add_argument("--json", action="store_true", help="Salida en JSON en vez de tabla.")
    args = ap.parse_args()

    root = Path(args.corpus).expanduser().resolve()
    if not root.is_dir():
        print(f"No es un directorio: {root}", file=sys.stderr)
        return 2

    pdfs = sorted(p for p in root.rglob("*.pdf") if p.is_file())
    print(f"Revisando {len(pdfs)} PDF…", file=sys.stderr)
    results = [check(p, root) for p in pdfs]
    eurlex = [r for r in results if r.estado != "no es eur-lex"]

    if args.json:
        print(json.dumps([asdict(r) for r in eurlex], ensure_ascii=False, indent=2))
    else:
        if not eurlex:
            print("No se encontró ningún texto consolidado de EUR-Lex en el corpus.")
        for r in eurlex:
            marca = "OK " if r.estado == "al dia" else "!! "
            print(f"{marca}{r.celex}  local {r.version_local} (v{r.numero_version})"
                  f"  ·  última publicada {r.version_ultima or '?'}  ·  {r.fichero}")
            if r.url:
                print(f"     descargar: {r.url}")

    caducados = sum(1 for r in eurlex if r.estado == "DESACTUALIZADO")
    print(f"\n{len(eurlex)} documentos de EUR-Lex · {caducados} desactualizados", file=sys.stderr)
    return 1 if caducados else 0


if __name__ == "__main__":
    raise SystemExit(main())
