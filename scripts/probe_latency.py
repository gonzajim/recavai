#!/usr/bin/env python3
"""
Fase 0 de docs/PLAN_CONTEXTO.md: cuánto cuesta en latencia dar más contexto al asesor.

Para cada pregunta construye contextos reales (los fragmentos más parecidos del índice v2
en memoria, hasta N tokens) y mide la generación con distintos presupuestos de
razonamiento. Registra tiempo total y tokens de entrada, salida y razonamiento. Mide
además la latencia del planificador de búsqueda (gemini-2.5-flash-lite, sin razonamiento).

  python scripts/probe_latency.py --npz .cache/idx_C.npz --out results/contexto/latencia.jsonl

Gasta crédito de Gemini (~40 llamadas, unos 0,3 $). Se detiene al primer error de saldo
o de cuota: el crédito se comparte con producción (docs/DESPLIEGUE.md §5.1).
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

QUESTIONS = [
    "¿Qué establece el artículo 10 de la CSDDD sobre la prevención de efectos adversos potenciales?",
    "¿Qué hay que informar en el contenido GRI 305-1?",
]
SIZES = (2_000, 12_000, 40_000)          # tokens de contexto
THINKING = (None, 512, 0)                # None = dinámico (lo decide el modelo)
CHARS_PER_TOKEN = 4.18                   # medido con countTokens sobre el corpus (24/09/2026)


def build_context(question: str, vecs, meta, model, tokens: int) -> str:
    q = model.encode(["query: " + question], normalize_embeddings=True)[0]
    order = np.argsort(-(vecs @ q))
    parts, used = [], 0
    for i in order:
        m = meta[i]
        block = f"[{len(parts) + 1}] {m['source']} ({m.get('unit_label') or ''}, p.{m.get('page')})\n{m['text']}"
        if used + len(block) > tokens * CHARS_PER_TOKEN:
            break
        parts.append(block)
        used += len(block)
    return ("Fragmentos relevantes de la base documental (cítalos inline como [1], [2]… cuando los uses "
            "en tu respuesta):\n\n" + "\n\n".join(parts) + f"\n\n---\n\nPregunta: {question}")


def fatal(e: Exception) -> bool:
    s = str(e)
    return any(k in s for k in ("402", "RESOURCE_EXHAUSTED", "429", "billing", "credit"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--npz", default=".cache/idx_C.npz")
    ap.add_argument("--out", default="results/contexto/latencia.jsonl")
    ap.add_argument("--reps", type=int, default=2)
    a = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import os
    from google import genai
    from google.genai import types
    from sentence_transformers import SentenceTransformer
    from src.agent.skills import compose

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    d = np.load(a.npz, allow_pickle=False)
    vecs, meta = d["vecs"].astype("float32"), json.loads(str(d["meta"]))
    model = SentenceTransformer("intfloat/multilingual-e5-small")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    total = len(QUESTIONS) * len(SIZES) * len(THINKING) * a.reps
    k = 0
    for question in QUESTIONS:
        system = compose("asesor", question).text
        for size in SIZES:
            msg = build_context(question, vecs, meta, model, size)
            for tb in THINKING:
                for rep in range(a.reps):
                    k += 1
                    cfg = dict(system_instruction=system, temperature=0.2, max_output_tokens=8192)
                    if tb is not None:
                        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=tb)
                    t0 = time.time()
                    try:
                        r = client.models.generate_content(model="gemini-2.5-flash", contents=msg,
                                                           config=types.GenerateContentConfig(**cfg))
                    except Exception as e:                 # noqa: BLE001
                        print(f"\nERROR: {e}", file=sys.stderr)
                        if fatal(e):
                            print("Saldo o cuota agotados: me detengo.", file=sys.stderr)
                            sys.exit(2)
                        continue
                    um = r.usage_metadata
                    row = {"pregunta": question[:40], "contexto": size, "thinking": "dinámico" if tb is None else tb,
                           "rep": rep, "ms": round((time.time() - t0) * 1000),
                           "tok_entrada": um.prompt_token_count, "tok_salida": um.candidates_token_count,
                           "tok_razonamiento": um.thoughts_token_count or 0}
                    rows.append(row)
                    print(f"\r  {k}/{total}", end="", file=sys.stderr)

    # Planificador: prompt corto, salida JSON pequeña
    plan_rows = []
    for question in QUESTIONS * 3:
        t0 = time.time()
        try:
            r = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=f"Reformula para buscar en normativa europea, en JSON {{\"busquedas\": [3 frases]}}: {question}",
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                                   thinking_config=types.ThinkingConfig(thinking_budget=0)))
            plan_rows.append(round((time.time() - t0) * 1000))
        except Exception as e:                             # noqa: BLE001
            print(f"\nERROR planificador: {e}", file=sys.stderr)
            if fatal(e):
                sys.exit(2)
    print(file=sys.stderr)

    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"planificador_ms": plan_rows}) + "\n")

    print(f"{'contexto':>9s} {'razonamiento':>13s} {'ms p50':>7s} {'tok entrada':>12s} {'tok razon.':>11s} {'tok salida':>11s}")
    for size in SIZES:
        for tb in THINKING:
            lab = "dinámico" if tb is None else tb
            g = [r for r in rows if r["contexto"] == size and r["thinking"] == lab]
            if not g:
                continue
            print(f"{size:>9,} {str(lab):>13s} {st.median(r['ms'] for r in g):>7,.0f} "
                  f"{st.median(r['tok_entrada'] for r in g):>12,.0f} {st.median(r['tok_razonamiento'] for r in g):>11,.0f} "
                  f"{st.median(r['tok_salida'] for r in g):>11,.0f}")
    if plan_rows:
        print(f"\nplanificador (flash-lite, sin razonamiento): p50 {st.median(plan_rows):,.0f} ms · máx {max(plan_rows):,} ms")


if __name__ == "__main__":
    main()
