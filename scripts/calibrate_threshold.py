#!/usr/bin/env python3
"""
Calibra el umbral de similitud (_MIN_SCORE) de un modelo de embeddings nuevo.

El umbral de producción (0,55) está ajustado a la distribución de similitudes de
all-MiniLM-L6-v2. Otros modelos puntúan en otra escala: con e5 casi todo supera
0,7, así que 0,55 no filtraría nada. Copiar el número no sirve.

Criterio: que el modelo nuevo deje SIN CONTEXTO la misma fracción de preguntas que
el de referencia. Es lo que nota el usuario: una pregunta sin ningún fragmento se
responde sin base documental. Se calcula sobre las preguntas reales de los usuarios
(data/real_queries.jsonl), no sobre la batería, para no ajustar el umbral a las
preguntas con las que luego se evalúa.

Se descartó igualar la fracción de candidatos que pasan el filtro: con e5 las
similitudes se concentran en una franja estrecha (0,85-0,90) y ese criterio dejaba
sin contexto el 15,5 % de las preguntas, casi el doble que hoy.

Uso:
  python scripts/calibrate_threshold.py \\
      --ref-index .cache/idx_A.npz --ref-model sentence-transformers/all-MiniLM-L6-v2 --ref-threshold 0.55 \\
      --new-index .cache/idx_B.npz --new-model intfloat/multilingual-e5-small
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
K = 12


def top_scores(index_path: str, model_name: str, queries: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    d = np.load(index_path, allow_pickle=False)
    vecs = d["vecs"].astype("float32")
    m = SentenceTransformer(model_name)
    prefix = "query: " if "e5" in model_name.lower() else ""
    q = m.encode([prefix + x for x in queries], normalize_embeddings=True, convert_to_numpy=True,
                 show_progress_bar=False).astype("float32")
    s = q @ vecs.T
    return -np.sort(-s, axis=1)[:, :K]          # (n_queries, K), descendente


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queries", default=str(ROOT / "data" / "real_queries.jsonl"))
    ap.add_argument("--ref-index", required=True)
    ap.add_argument("--ref-model", required=True)
    ap.add_argument("--ref-threshold", type=float, default=0.55)
    ap.add_argument("--new-index", required=True)
    ap.add_argument("--new-model", required=True)
    a = ap.parse_args()

    qs = [json.loads(l)["pregunta"] for l in Path(a.queries).read_text(encoding="utf-8").splitlines() if l.strip()]
    ref = top_scores(a.ref_index, a.ref_model, qs)
    new = top_scores(a.new_index, a.new_model, qs)

    keep = float((ref >= a.ref_threshold).mean())
    empty = float((ref[:, 0] < a.ref_threshold).mean())
    thr = float(np.quantile(new[:, 0], empty))
    print(f"{len(qs)} preguntas reales · top-{K} candidatos por pregunta")
    print(f"referencia {a.ref_model}: umbral {a.ref_threshold} deja pasar el {keep:.1%} de los candidatos")
    print(f"  similitud top-1: mediana {np.median(ref[:, 0]):.3f} · p10 {np.quantile(ref[:, 0], .1):.3f}")
    print(f"  preguntas que se quedan SIN ningún fragmento: {(ref[:, 0] < a.ref_threshold).mean():.1%}")
    print(f"nuevo {a.new_model}:")
    print(f"  similitud top-1: mediana {np.median(new[:, 0]):.3f} · p10 {np.quantile(new[:, 0], .1):.3f}")
    print(f"  con el umbral viejo ({a.ref_threshold}) pasaría el {(new >= a.ref_threshold).mean():.1%}")
    print(f"  UMBRAL CALIBRADO: {thr:.3f}  → deja pasar el {(new >= thr).mean():.1%}; "
          f"preguntas sin ningún fragmento: {(new[:, 0] < thr).mean():.1%}")


if __name__ == "__main__":
    main()
