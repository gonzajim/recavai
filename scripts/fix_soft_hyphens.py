#!/usr/bin/env python3
"""
Repara las palabras partidas por guión de línea en el texto ya indexado.

Los PDF oficiales de EUR-Lex justifican el texto partiendo palabras al final de
línea con un guión blando (U+00AD). Al extraer el texto, la partición sobrevive:
en el índice hay 'obliga­ ciones', 'colecti­ vos', 'empre­ sarial'. Eso hace dos
daños:

  1. El modelo de embeddings ve dos fragmentos sin sentido en lugar de una palabra,
     así que ese trozo se recupera peor de lo que debería.
  2. Cuando el asistente cita el fragmento literal, el usuario lee la palabra rota.

La reparación es conservadora: no cambia los límites de los fragmentos, ni sus
identificadores, ni cuántos hay. Solo quita el guión blando (y el espacio que lo
sigue), vuelve a calcular el vector con el MISMO modelo que usa producción y
sobrescribe el vector con su mismo id.

Se comprobó (2026-09-23) que los vectores del índice son exactamente el embedding
normalizado del campo `text` con all-MiniLM-L6-v2 —coseno 1,0000 en la muestra—,
así que recalcularlos de esta forma reproduce el índice tal cual, salvo por la
corrección.

Por defecto NO escribe nada. Hay que pedirlo con --apply.

Uso:
  python scripts/fix_soft_hyphens.py                       # diagnóstico
  python scripts/fix_soft_hyphens.py --source 02_NORMATIVAS/01_CSDDD
  python scripts/fix_soft_hyphens.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

SOFT = "­"
# Guión blando seguido de los espacios que el extractor haya dejado: se une la palabra.
_BROKEN = re.compile(SOFT + r"\s*")


def clean(text: str) -> str:
    return _BROKEN.sub("", text)


def get_index():
    from pinecone import Pinecone
    key = os.getenv("PINECONE_API_KEY")
    name = os.getenv("PINECONE_INDEX_NAME")
    if not key or not name:
        sys.exit("Faltan PINECONE_API_KEY / PINECONE_INDEX_NAME (revisa .env).")
    pc = Pinecone(api_key=key)
    return pc.Index(host=name) if name.startswith("http") else pc.Index(name)


def main() -> int:
    ap = argparse.ArgumentParser(description="Une las palabras partidas por guión en el índice.")
    ap.add_argument("--apply", action="store_true", help="Escribe en Pinecone. Sin esto solo informa.")
    ap.add_argument("--source", default=None, help="Limitar a un documento (valor del metadato `source`).")
    ap.add_argument("--namespace", default="__default__")
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--model", default=os.getenv("EMBEDDING_MODEL_NAME",
                                                 "sentence-transformers/all-MiniLM-L6-v2"))
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    index = get_index()
    ns = args.namespace

    print(f"Leyendo el índice (namespace {ns!r})…", file=sys.stderr)
    afectados: list[tuple[str, dict, str, str]] = []   # (id, metadata, antes, después)
    por_doc: dict[str, int] = defaultdict(int)
    leidos = 0
    for page in index.list(namespace=ns):
        ids = [getattr(i, "id", i) for i in page]
        if not ids:
            continue
        for v in (index.fetch(ids=ids, namespace=ns).vectors or {}).values():
            leidos += 1
            md = dict(getattr(v, "metadata", None) or {})
            src = str(md.get("source", ""))
            if args.source and src != args.source:
                continue
            antes = str(md.get("text", ""))
            if SOFT not in antes:
                continue
            despues = clean(antes)
            afectados.append((getattr(v, "id", ""), md, antes, despues))
            por_doc[src] += 1
        print(f"\r  {leidos} vectores leídos · {len(afectados)} a reparar", end="", file=sys.stderr)
    print(file=sys.stderr)

    if not afectados:
        print("No hay palabras partidas en el índice. Nada que hacer.")
        return 0

    print(f"\n{len(afectados)} fragmentos con palabras partidas, en {len(por_doc)} documentos:")
    for s in sorted(por_doc, key=lambda k: -por_doc[k]):
        print(f"  {por_doc[s]:5d}  {s}")

    print("\nEjemplos (antes → después):")
    vistos = set()
    for _id, _md, antes, _despues in afectados:
        m = _BROKEN.search(antes)
        if not m:
            continue
        ini, fin = max(0, m.start() - 22), min(len(antes), m.end() + 18)
        roto = antes[ini:fin].replace("\n", " ")
        if roto in vistos:
            continue
        vistos.add(roto)
        print(f"  {roto.replace(SOFT, '·')}   →   {clean(roto)}")
        if len(vistos) >= 5:
            break

    if not args.apply:
        print("\nDiagnóstico solamente. Para corregirlo: --apply")
        return 0

    from sentence_transformers import SentenceTransformer
    print(f"\nCargando {args.model}…", file=sys.stderr)
    model = SentenceTransformer(args.model)

    escritos = 0
    for start in range(0, len(afectados), args.batch):
        window = afectados[start:start + args.batch]
        vecs = model.encode([w[3] for w in window], normalize_embeddings=True,
                            convert_to_numpy=True, show_progress_bar=False)
        payload = []
        for k, (vid, md, _antes, despues) in enumerate(window):
            nueva = dict(md)
            nueva["text"] = despues
            payload.append({"id": vid, "values": vecs[k].tolist(), "metadata": nueva})
        index.upsert(vectors=payload, namespace=ns)
        escritos += len(payload)
        print(f"\r  {escritos}/{len(afectados)} reparados", end="", file=sys.stderr)
    print(file=sys.stderr)
    print(f"\nListo: {escritos} fragmentos reparados en {ns!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
