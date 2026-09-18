# AGENT CONTEXT — Línea 1: construcción de la base de conocimiento para RAG regulatorio en español

*Contexto operativo para agentes que trabajen en esta línea. Última actualización: 2026-09-18. Lee esto entero antes de tocar código o texto.*

## 1. Qué es esto

Proyecto RECAVA-IA / RecavAI: asistente RAG de cumplimiento en sostenibilidad (CSRD, CSDDD, ESRS, GRI, OCDE) en español. Spin-off UCLM. El usuario es Gonzalo Jiménez Martín, doctorando en IA; la Línea 1 de su tesis es este estudio. Artículo objetivo: ICAIL / JURIX / *AI & Law* (o ECIR/SIGIR short para el hallazgo de distancia léxica).

**Pregunta de investigación.** ¿Cómo interactúan la estrategia de segmentación, la jerarquía del texto normativo y el enriquecimiento de la base de conocimiento con la calidad de la recuperación, la precisión de la cita y la fidelidad de la respuesta, en un corpus regulatorio en español?

**Hipótesis pre-registradas** (no cambiar sin el usuario):
- H1.1 Chunking semántico restringido por jerarquía (nunca cruza encabezado) > semántico libre y > fijo en `cit_recall_art`, sobre todo en comparativas y multi-salto.
- H1.2 La ventaja semántico-vs-fijo depende de la distancia léxica pregunta–evidencia: convergen en literales; semántico+cabecera domina en parafraseadas (interacción).
- H1.3 Grafo esqueleto (nodos = artículos existentes, aristas = referencias cruzadas) > grafo emergente por extracción LLM en recall multi-salto, con menor coste y varianza.
- H1.4 Embedding multilingüe > monolingüe inglés (`all-MiniLM-L6-v2`, el del despliegue original) sobre corpus español.

**Postura de novedad (fijada):** el chunking semántico NO es nuevo. La contribución es el estudio controlado, el dominio/idioma, la métrica `citation@article` y el grafo esqueleto. No redactar nada que venda el chunker como novedad.

## 2. Estado (2026-09-18)

| Pieza | Estado |
|---|---|
| `src/corpus_pipeline.py` | Hecho y validado. Chunking semántico por breakpoint (percentil 80, 350–1800 chars, sin solape), consciente de estructura, cabecera contextual, enrich LLM (summary/keywords/entities/triplets + PageRank), propositions, upsert a Pinecone. Hoy se añadieron patrones de encabezado GRI `Contenido NNN-N` y ESRS `E1-6`. |
| `src/rag_benchmark.py` | Hecho. Métricas doc+página (hit/recall/precision/MRR), juez LLM, modo API. Línea base del índice de producción: hit@k 0,80, recall@k 0,65, precision@k 0,42, MRR 0,51. |
| `src/kb_experiment.py` | **Nuevo hoy.** Harness factorial (ver §4). Smoke test pasa. |
| `benchmarks/smoke/` | Corpus sintético (2 docs .txt) + 8 preguntas con `articulo` y `parafrasis`. Solo para probar el instrumento. |
| `benchmarks/golden_set.jsonl` | 10 semillas, `respuesta_ref` vacía, sin `articulo` ni `parafrasis`. NO es el golden set v1. |
| Golden set v1 | **No existe.** Lo produce la Línea 3 (plan de 8 semanas con IDPEI, artefacto "Plan del Golden Set v1"). Tareas T1 (con columna `articulo`) y T9 (`parafrasis`) alimentan este experimento. |
| `paper/linea1_kb_construction_es_regulatory_rag.md` | Borrador v0.1 en inglés. §3.2 (recuentos PRISMA) y §6–7 (resultados/discusión) son plantillas con placeholders. |
| `paper/EXPLICACION_LINEA1.md` | Explicación en español, sección a sección, con la lista de tareas pendientes del autor. |
| Resultados reales | **Ninguno.** No inventar. No presentar el smoke test como resultado. |

## 3. Hechos del sistema que hay que respetar

- Índice de producción Pinecone `uclm-corpus-roma` (384d, `all-MiniLM-L6-v2`): 9.176 vectores, un vector por bloque de layout, mediana 2 palabras/chunk. **Nunca escribir en él.** Reindexar siempre en un namespace de prueba (`corpus_v2`) o en memoria.
- Constantes de recuperación en producción: `_CANDIDATE_K=12`, `_MIN_SCORE=0.55`, `_MAX_RESULTS=6`. Por eso `k=6` es el valor por defecto del experimento.
- Chunking fijo del despliegue original: 400 palabras, 50 solape (`_CHUNK_WORDS`, `_CHUNK_OVERLAP` en `rag_service.py`). `kb_experiment.chunk_fixed` lo reproduce.
- `.env` en la raíz (gitignored) con `PINECONE_*`, `GEMINI_API_KEY`. Cargar con `set -a; source .env; set +a; unset GOOGLE_APPLICATION_CREDENTIALS`. Nunca commitearlo.
- Python 3.14, venv en `.venv`. Modelos cacheados en HF: `all-MiniLM-L6-v2`, `paraphrase-multilingual-MiniLM-L12-v2`. `paraphrase-multilingual-mpnet-base-v2` se descarga en la primera ejecución.
- `scipy` disponible; `rank_bm25` NO (por eso `kb_experiment.BM25` es una implementación propia).
- Rama `main` protegida en GitHub; los commits locales van por PR. No hacer commit ni push sin que el usuario lo pida.

## 4. `src/kb_experiment.py` — mapa rápido

```
article_id(label) -> "art.8" | "art.10.bis" | "anexo.I" | "gri.306-2" | "esrs.E1-6" | None
article_of_section(section_path) -> artículo más profundo del path
load_golden(path) -> list[ExpItem]           # campos extra: parafrasis, fuentes_esperadas[].articulo
load_corpus(root, limit) -> list[Doc]        # texto + spans + headings (una lectura por doc)
chunk_fixed / chunk_semantic_free / chunk_semantic_struct(with_context) / chunk_small_to_big / chunk_propositions
build_chunks(strategy, docs, genai_client) -> (units, parents|None, cost)
DenseIndex(units).search(q, k)               # numpy coseno, en memoria
BM25(units).search(q, k); rrf(*rankings)     # canal léxico opcional (--hybrid)
NormativeGraph(units, parents, mode)         # mode: skeleton (regex xref) | emergent (entidades LLM)
retrieve(...)                                # presupuesto igualado: k-3 semillas + ≤3 vecinos si hay grafo
evaluate(item, results, k) -> hit, recall, precision, mrr, cit_recall_art, cit_precision_art, cit_recall_art_lenient, cit_all_art
paired_bootstrap(a, b) ; holm(pvals)
run_experiment(...) -> <out>.rows.csv, <out>.build.csv, <out>.report.md
```

Semántica de `cit_recall_art` (estricta): la etiqueta citada es el artículo en el que **arranca** el fragmento. `cit_recall_art_lenient`: cuenta si el fragmento **abarca** el artículo gold. Ambas se reportan; la estricta es primaria.

Factores: `--chunking {fixed,semantic_free,semantic_struct,semantic_struct_ctx,small_to_big,propositions}`, `--embedding-models …`, `--graph {none,skeleton,emergent}`, condición de consulta automática (literal = `pregunta`, paraphrased = `parafrasis`). `propositions` y `emergent` requieren `--llm`.

Comando del smoke test (≈1 min, sin red salvo carga de modelos):
```
python -m src.kb_experiment --corpus benchmarks/smoke/corpus --golden benchmarks/smoke/golden_smoke.jsonl \
  --embedding-models sentence-transformers/all-MiniLM-L6-v2 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --chunking fixed semantic_free semantic_struct semantic_struct_ctx small_to_big --graph none skeleton --k 4 --out results/smoke
```
Comportamiento esperado del smoke (chequeo del instrumento, no resultado): `fixed` cruza artículos (5/6 unidades) y da `cit_recall_art` ≈ 0,3; `semantic_struct` no cruza y da ≈ 0,9; métricas doc-level saturan a 1,0; grafo esqueleto con ~10 aristas mete `art.8` en el top-k de los ítems multi-salto.

## 5. Esquema del golden set que consume el experimento

```json
{"id": "gs-011", "pregunta": "…", "parafrasis": "…",
 "categoria": "CSDDD", "tipo": "multi_salto", "dificultad": "alta", "respuesta_ref": "…",
 "fuentes_esperadas": [{"documento": "02_NORMATIVAS/CSDDD", "pagina": 12, "articulo": "Artículo 8"}]}
```
`tipo` admite además `comparativa`. Ítems sin `articulo` no cuentan en métricas de cita; sin `parafrasis` no entran en la condición paraphrased. `documento` se empareja por substring normalizado o Jaccard ≥ 0,6 (`rag_benchmark._source_match`).

## 6. Reglas de decisión pre-registradas (ya implementadas en `report()`)

Hipótesis apoyada ⇔ IC bootstrap pareado 95 % del contraste primario excluye 0 en la dirección prevista **y** p de Wilcoxon ajustado por Holm < 0,05. H1.2 exige además que el IC de la interacción (diferencia de diferencias) excluya 0. H1.3 se evalúa primariamente sobre el subconjunto `multi_source=True`.

| H | Contraste primario | Métrica |
|---|---|---|
| H1.1 | semantic_struct − fixed; semantic_struct − semantic_free | cit_recall_art |
| H1.2 | (semantic_struct_ctx − fixed)[para] − (…)[lit] | recall, cit_recall_art |
| H1.3 | skeleton − none; skeleton − emergent | cit_recall_art (multi-source) |
| H1.4 | multilingüe-384d − EN-384d | recall, cit_recall_art |

## 7. Pendiente (en orden)

1. Golden set v1 con `articulo` y `parafrasis` (Línea 3 / IDPEI). Bloqueante.
2. Ejecutar búsquedas de la revisión sistemática; rellenar §3.2 y Apéndice A del paper; verificar TODAS las referencias contra DOI/arXiv (están escritas de memoria, con nota de verificación).
3. Reindexar corpus real con `corpus_pipeline` en namespace de prueba; correr `kb_experiment` con los tres modelos (+ `intfloat/multilingual-e5-base`, `BAAI/bge-m3` como extensión).
4. Pegar tablas en §6, escribir §7 siguiendo los marcos fijados.
5. Insertar título del paper IPMU 2026 en la referencia `[Own]`.
6. Opcional: brazo `--llm` (propositions + grafo emergente), ejecutado dos veces para reportar varianza.

## 8. No hacer

- No inventar resultados ni recuentos PRISMA. No presentar el smoke test como evidencia.
- No tocar el índice de producción ni el namespace `__default__`.
- No cambiar hipótesis, contrastes o reglas de decisión sin el usuario (están pre-registradas).
- No commitear `.env`, `results/`, `benchmarks/baseline.jsonl`, `benchmarks/run_*.jsonl`.
- No reescribir el paper en español: el destino es en inglés. La explicación sí va en español.

## 9. Documentos relacionados

- Artefactos (privados, claude.ai): "Líneas doctorales RAG jurídico" (define las 3 líneas), "Plan RAG, Explicabilidad y Benchmark" (§04 tareas T1–T9), "Metodología del Golden Set", "Plan del Golden Set v1" (8 semanas con IDPEI, κ).
- `benchmarks/README.md` (cómo correr todo), `DOCUMENTACION.md` (arquitectura del sistema; no describe el chunking del corpus).
