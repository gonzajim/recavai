# Evaluación de calidad

Hay tres instrumentos, de más a menos usado:

| Instrumento | Para qué | Estado |
|---|---|---|
| **Batería v1 + `scripts/eval_battery.py`** | Medir antes y después de cualquier cambio de recuperación, modelo, índice o habilidades del asesor | **En uso** desde el 23/09/2026 |
| Golden set del IDPEI + `src/kb_experiment.py` | Resultados publicables de la Línea 1 (artículo) | Pendiente del taller con el IDPEI |
| `src/rag_benchmark.py` + `golden_set.jsonl` | Línea base inicial (10 preguntas) | Anterior; se conserva |

La batería **no sustituye** al golden set: la escribió el equipo de desarrollo, no juristas,
y sirve para decidir despliegues, no para publicar.

## Batería v1 — `bateria_v1.py` → `bateria_v1.jsonl`

60 preguntas en 13 tipos (definiciones, ámbito, artículo concreto, lenguaje coloquial,
cambios del Ómnibus, varios artículos, NEIS, GRI, sectoriales, cruce entre marcos, 8
preguntas reales de usuarios, 4 comprobaciones del modo auditor y 3 trampas). Cada una
lleva fuentes esperadas (documento, página, unidad normativa) y 2-4 datos clave
localizados en el corpus. Legible: [BATERIA_V1.md](BATERIA_V1.md).

```bash
python scripts/extract_corpus_text.py --corpus "<carpeta>" --out .cache/corpus_txt
python benchmarks/bateria_v1.py --verify .cache/corpus_txt     # 175/175 datos localizados
```

Congelada en el commit `2c90e90`, antes de construir el índice v2. **No se edita** para
favorecer un cambio; si hay que corregirla, se crea `bateria_v2` y se vuelve a medir la
línea base.

## `scripts/eval_battery.py`

Pasa la batería por una configuración **usando el mismo código que producción** (el
harness del asesor) y la puntúa.

| Familia | Métricas |
|---|---|
| Búsqueda (determinista) | `doc@6`, `pasaje@6` (algún fragmento contiene el pasaje del dato clave), `pag@6`, `mrr`, `fuentes` |
| Respuesta (juez LLM, a ciegas) | `cobertura` de datos clave, `fiel` (sin afirmaciones no respaldadas), `rechazo_ok` en trampas, `veredicto_ok` en el auditor, `cita_unidad` (menciona el artículo esperado) |
| Fidelidad (determinista) | `criticos`: cifras, fechas, artículos, normas y códigos sin respaldo en los fragmentos |
| Funcionamiento | `ms_total`, `ms_busqueda`, errores |

```bash
# Configuración actual (índice v2 + grafo + agente)
python scripts/eval_battery.py run --name X --index recavai-corpus-v2 \
    --model intfloat/multilingual-e5-small --min-score 0.841 \
    --graph data/normative_graph.json --judge-model gemini-3.5-flash

python scripts/eval_battery.py compare H4 X                          # IC por bootstrap pareado
python scripts/eval_battery.py pairwise H4 X --with-context \        # juez a ciegas que ve los fragmentos
    --judge-model gemini-3.7-flash

# Preguntas reales (data/real_queries.jsonl, fuera del repositorio): muestra sin juez
python scripts/eval_battery.py run --name realX --queries data/real_queries.jsonl --sample 40 --no-judge ...

# Otras utilidades
python scripts/eval_battery.py run ... --no-generate                  # solo búsqueda: sin coste de Gemini
python scripts/eval_battery.py rejudge X --judge-model M --out X.jM   # otro juez, mismas respuestas
python scripts/eval_battery.py split-draft X --out X.borrador         # borradores del harness (antes de reparar)
python scripts/eval_battery.py add-critical X [--fragments-from Y]    # métrica `criticos` sin llamadas
```

**Reglas de uso**, aprendidas el 23/09/2026:

- **Mismo juez** para todas las configuraciones que se comparan (`rejudge` si cambia).
- **Dos pasadas** si la métrica depende de la generación: la fidelidad varía ±13 puntos
  y la cobertura ±6 entre pasadas idénticas. La búsqueda es determinista.
- **Regla de decisión escrita antes de medir** (ver `PREREGISTRO_FIDELIDAD.md`).
- El juez por parejas **debe ver los fragmentos**: sin ellos juzga con su propio
  conocimiento, anterior al Ómnibus, y llegó a llamar «norma inexistente» a la Directiva
  2026/470.
- **Coste:** una pasada = 60 generaciones + 60 juicios; una comparación por parejas, 60
  juicios. La clave local gasta del mismo crédito que producción (`docs/DESPLIEGUE.md` §5.1).
  `gemini-3.1-pro-preview` admite solo 250 peticiones al día.

Resultados guardados: `results/rag_v2/` (fuera del repositorio). Informes:
[../docs/RAG_V2_RESULTADOS.md](../docs/RAG_V2_RESULTADOS.md) (índice, modelo, grafo) y
[RESULTADOS_FIDELIDAD.md](RESULTADOS_FIDELIDAD.md) (habilidades y controles).

## Instrumento anterior: `src/rag_benchmark.py` (10 preguntas de arranque)

Mide cómo de bien el RAG **recupera** (y opcionalmente **responde**) sobre el
corpus normativo, para un conjunto fijo de preguntas con respuesta y fuentes de
referencia. Sirve para:

1. Tener una **línea base** del índice v1 (`uclm-corpus-roma`).
2. Comparar contra cualquier cambio de arquitectura (chunking semántico del
   nuevo `src/corpus_pipeline.py`, modelo de embeddings, routing, grafo,
   re-ranking, propositions…).

### Ficheros

| Fichero | Qué es |
|---|---|
| `golden_set.jsonl` | Preguntas de referencia. **10 de arranque; IDPEI lo lleva a 50.** Cada línea: pregunta + categoría/tipo/dificultad + `respuesta_ref` + `fuentes_esperadas` (documento y, opcionalmente, página). Ahora mismo `respuesta_ref` y `pagina` están como `TODO IDPEI`. |
| `../src/rag_benchmark.py` | El harness. |
| `baseline.jsonl` | *(se genera)* resultados por pregunta de la línea base. |

### Paso 0 — completar el golden set (IDPEI)

Por cada entrada, rellenar:
- `respuesta_ref`: la respuesta jurídicamente correcta (1–4 frases).
- `fuentes_esperadas[].pagina`: la(s) página(s) reales donde está la evidencia.
  Si se deja `null`, el emparejamiento se hace solo por documento (menos preciso).

Objetivo: ~50 preguntas, repartidas por `categoria` (CSDDD/GRI/general),
`tipo` (conceptual/operational/resource) y `dificultad` (baja/media/alta).

### Correr la línea base

Instala deps si hace falta: `pip install pinecone sentence-transformers google-genai requests`

```bash
cd recavai
set -a; source .env; set +a       # PINECONE_*, GEMINI_API_KEY

# Línea base del índice de producción. El --embedding-model DEBE ser el que
# construyó ese índice (v1: all-MiniLM-L6-v2; v2: intfloat/multilingual-e5-small).
python -m src.rag_benchmark \
    --golden benchmarks/golden_set.jsonl \
    --embedding-model sentence-transformers/all-MiniLM-L6-v2 \
    --out benchmarks/baseline.jsonl
```

Sale una tabla con `hit@k`, `recall@k`, `precision@k`, `mrr` (global y por
categoría/tipo/dificultad). Parámetros de recuperación por defecto = los de
producción (`top_k=12`, `min_score=0.55`, `max_results=6`, filtro por categoría).

### Comparar una arquitectura nueva

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

### Con generación + juez LLM (más caro)

```bash
python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl --judge \
    --out benchmarks/run_judged.jsonl
```

Añade `groundedness` (la respuesta se apoya solo en lo recuperado),
`correctness` (coincide con `respuesta_ref`), `citation_ok` (las citas `[n]`
apuntan a las fuentes esperadas), `latency_ms` y `cost_usd`.

### Modo API (pipeline real end-to-end)

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
  fragmenta demasiado. No es el caso del índice actual: medido el 23/09/2026
  cubre el 98,4 % del texto con mediana de 95 palabras por fragmento.
- `recall@k` alto pero `mrr` bajo → se recupera lo relevante pero muy abajo →
  hace falta re-ranking.
- `groundedness` alto y `correctness` bajo → el modelo cita bien pero el corpus
  no tiene la respuesta correcta.
