# Benchmark de calidad del RAG

Mide cómo de bien el RAG **recupera** (y opcionalmente **responde**) sobre el
corpus normativo, para un conjunto fijo de preguntas con respuesta y fuentes de
referencia. Sirve para:

1. Tener una **línea base** del índice actual (`uclm-corpus-roma`).
2. Comparar contra cualquier cambio de arquitectura (chunking semántico del
   nuevo `src/corpus_pipeline.py`, modelo de embeddings, routing, grafo,
   re-ranking, propositions…).

## Ficheros

| Fichero | Qué es |
|---|---|
| `golden_set.jsonl` | Preguntas de referencia. **10 de arranque; IDPEI lo lleva a 50.** Cada línea: pregunta + categoría/tipo/dificultad + `respuesta_ref` + `fuentes_esperadas` (documento y, opcionalmente, página). Ahora mismo `respuesta_ref` y `pagina` están como `TODO IDPEI`. |
| `../src/rag_benchmark.py` | El harness. |
| `baseline.jsonl` | *(se genera)* resultados por pregunta de la línea base. |

## Paso 0 — completar el golden set (IDPEI)

Por cada entrada, rellenar:
- `respuesta_ref`: la respuesta jurídicamente correcta (1–4 frases).
- `fuentes_esperadas[].pagina`: la(s) página(s) reales donde está la evidencia.
  Si se deja `null`, el emparejamiento se hace solo por documento (menos preciso).

Objetivo: ~50 preguntas, repartidas por `categoria` (CSDDD/GRI/general),
`tipo` (conceptual/operational/resource) y `dificultad` (baja/media/alta).

## Correr la línea base

Instala deps si hace falta: `pip install pinecone sentence-transformers google-genai requests`

```bash
cd recavai
set -a; source .env; set +a       # PINECONE_*, GEMINI_API_KEY

# Línea base del índice de producción. El --embedding-model DEBE ser el que
# construyó ese índice (hoy: all-MiniLM-L6-v2).
python -m src.rag_benchmark \
    --golden benchmarks/golden_set.jsonl \
    --embedding-model sentence-transformers/all-MiniLM-L6-v2 \
    --out benchmarks/baseline.jsonl
```

Sale una tabla con `hit@k`, `recall@k`, `precision@k`, `mrr` (global y por
categoría/tipo/dificultad). Parámetros de recuperación por defecto = los de
producción (`top_k=12`, `min_score=0.55`, `max_results=6`, filtro por categoría).

## Comparar una arquitectura nueva

```bash
# 1) Reindexar el corpus en un namespace de pruebas con el pipeline nuevo:
python -m src.corpus_pipeline --input ./corpus --namespace corpus_v2 --enrich

# 2) Benchmark contra ese namespace, con su modelo, y delta vs baseline:
python -m src.rag_benchmark \
    --golden benchmarks/golden_set.jsonl \
    --namespace corpus_v2 \
    --embedding-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    --baseline benchmarks/baseline.jsonl \
    --out benchmarks/run_v2.jsonl
```

La tabla añade una fila `Δ vs baseline` con el cambio en cada métrica.

## Con generación + juez LLM (más caro)

```bash
python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl --judge \
    --out benchmarks/run_judged.jsonl
```

Añade `groundedness` (la respuesta se apoya solo en lo recuperado),
`correctness` (coincide con `respuesta_ref`), `citation_ok` (las citas `[n]`
apuntan a las fuentes esperadas), `latency_ms` y `cost_usd`.

## Modo API (pipeline real end-to-end)

Para medir el backend desplegado tal cual (routing + filtro + merge + grafo):

```bash
python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl --mode api \
    --api-token "<ID token de Firebase>"
```

(El ID token se saca de DevTools tras iniciar sesión en el sitio, o con el
flujo de `mint_token` que usa el equipo.)

## Experimento factorial de construcción de la KB (Línea 1) — `src/kb_experiment.py`

Compara **estrategia de chunking × modelo de embeddings × distancia léxica de la consulta
× grafo normativo** sobre el mismo corpus y golden set, en memoria (no toca Pinecone), y
añade la métrica **`citation@article`** (¿el artículo que citaría el sistema es el gold?).
Diseño, hipótesis y reglas de decisión: `paper/linea1_kb_construction_es_regulatory_rag.md`.

El golden set necesita dos campos extra por ítem: `parafrasis` (condición *paraphrased*) y
`fuentes_esperadas[].articulo` ("Artículo 8", "art. 10 bis", "Anexo I", "306-2", "E1-6").
Los produce el taller con IDPEI (tareas T1 y T9).

```bash
# Smoke test del instrumento (corpus sintético de 2 docs, ~1 min). NO son resultados.
python -m src.kb_experiment --corpus benchmarks/smoke/corpus --golden benchmarks/smoke/golden_smoke.jsonl \
    --embedding-models sentence-transformers/all-MiniLM-L6-v2 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    --chunking fixed semantic_free semantic_struct semantic_struct_ctx small_to_big \
    --graph none skeleton --k 4 --out results/smoke

# Experimento real (cuando exista golden_set_v1 con articulo + parafrasis):
python -m src.kb_experiment --corpus ./corpus --golden benchmarks/golden_set_v1/t1_recuperacion.jsonl \
    --embedding-models sentence-transformers/all-MiniLM-L6-v2 \
                       sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
                       sentence-transformers/paraphrase-multilingual-mpnet-base-v2 \
    --chunking fixed semantic_free semantic_struct semantic_struct_ctx small_to_big \
    --graph none skeleton --k 6 --cache results/cache --out results/v1
# Brazos con LLM (propositions, grafo emergente): añadir --llm --chunking ... propositions --graph none skeleton emergent
# Canal léxico BM25 fusionado por RRF como comprobación de robustez: --hybrid
```

Salida: `results/v1.rows.csv` (una fila por ítem × condición), `results/v1.build.csv` (coste
de construcción: unidades, chars, unidades que cruzan artículo, segundos, llamadas LLM) y
`results/v1.report.md` (tablas pivot + contrastes pre-registrados H1.1–H1.4 con IC bootstrap
pareado, Wilcoxon y Holm, e interacción de H1.2).

## Interpretación rápida

- `hit@k` bajo (<0.7) → el corpus no contiene bien la evidencia o el chunking la
  fragmenta demasiado (es el caso esperado del índice actual: mediana de 2
  palabras por chunk).
- `recall@k` alto pero `mrr` bajo → se recupera lo relevante pero muy abajo →
  hace falta re-ranking.
- `groundedness` alto y `correctness` bajo → el modelo cita bien pero el corpus
  no tiene la respuesta correcta.
