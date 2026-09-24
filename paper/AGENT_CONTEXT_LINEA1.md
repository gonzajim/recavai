# AGENT CONTEXT — Línea 1: construcción de la base de conocimiento para RAG regulatorio en español

*Contexto operativo para agentes que trabajen en esta línea. Última actualización: 2026-09-23. Lee esto entero antes de tocar código o texto.*

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
| `src/chunking_v2.py` | **Nuevo (23/09).** Troceado por unidad normativa (artículo, requisito NEIS, contenido GRI) + cabecera contextual embebida; es el troceado del índice de producción v2. Emparenta con `semantic_struct_ctx` del experimento, pero parte primero por unidad y solo después por tamaño (no por breakpoints semánticos). |
| `src/normative_graph.py` | **Nuevo (23/09).** Grafo esqueleto determinista (381 unidades, 408 referencias, 24 modificaciones) en producción. Es la versión desplegada de la condición `skeleton` de H1.3, más la resolución de referencias explícitas en la pregunta. |
| Batería v1 (`benchmarks/bateria_v1.jsonl`) | **Nueva (23/09).** 60 preguntas de desarrollo con fuentes y unidad esperada. **No es el golden set**: la escribió el equipo, no juristas, sin doble anotación. Sirve para decidir despliegues. |
| Resultados reales | **Ninguno confirmatorio.** Hay evidencia de desarrollo con la batería (§3.1). No presentarla como resultado del artículo. No presentar el smoke test como resultado. |

## 3. Hechos del sistema que hay que respetar

- **Índice de producción desde el 23/09/2026: `recavai-corpus-v2`** (384d, `intfloat/multilingual-e5-small`, 8.127 vectores, troceado `chunking_v2`, umbral 0,841). El anterior, `uclm-corpus-roma` (384d, `all-MiniLM-L6-v2`, 9.176 vectores, mediana 95 palabras por fragmento, 98,4 % del corpus indexado), se conserva para volver atrás. **No escribir en ninguno de los dos.** Experimentos en memoria (`scripts/build_memory_index.py`) o en un índice de prueba.
- ~~Mediana de 2 palabras por fragmento~~: dato **falso**, repetido hasta el 23/09/2026 en varios documentos. Medido: mediana 95.
- Constantes de recuperación: `_CANDIDATE_K=12`, `_MIN_SCORE` por variable de entorno (0,55 con MiniLM; 0,841 con e5), `_MAX_RESULTS=6`. Por eso `k=6` es el valor por defecto del experimento.
- Troceado fijo para PDF subidos por el usuario: 250 palabras, 40 de solape desde el 23/09 (antes 400/50). `kb_experiment.chunk_fixed` usa `FIXED_WORDS=400` para reproducir el despliegue original.
- `paraphrase-multilingual-mpnet-base-v2`, candidato por defecto del CLI del experimento, **corta en 128 tokens**: peor que MiniLM. Declararlo o sustituirlo antes de ejecutar H1.4.

### 3.1 Evidencia de desarrollo (23/09/2026, batería v1, no confirmatoria)

- **Truncado de MiniLM:** 4.186 de 9.176 fragmentos (45,6 %) superan 256 *word-pieces*; se descarta el 41,2 % de los tokens indexados. Relevante para H1.4: la ventaja del multilingüe se confunde con la del límite de contexto.
- **Página errónea en v1:** el 64 % de los fragmentos dice estar en la página 1. Cualquier métrica doc+página sobre v1 está sesgada a la baja.
- **Solo modelo (B) frente a modelo + troceado (C):** pasaje recuperado 35 % (MiniLM, v1) → 47 % (e5, mismos fragmentos, n.s.) → 61 % (e5 + troceado por unidad, IC excluye 0) → 68 % (+ grafo esqueleto, +7 sobre C con IC que excluye 0). Sugiere interacción modelo × troceado (cf. H1.1, H1.4) y efecto del grafo esqueleto (cf. H1.3). **Diseño no factorial, batería no validada: solo orienta.**
- Detalle: `docs/RAG_V2_RESULTADOS.md`, `docs/ARQUITECTURA_DATOS.md`.
- **Contexto adaptativo (24/09/2026):** el techo de la cobertura era el contexto, no la recuperación. Con 6 fragmentos solo llegaba al modelo el 48-58 % de los hechos clave; ampliando cada fragmento a su **unidad normativa completa** (misma búsqueda), el 83 %, y la cobertura del juez pasó de 63 % a 81 % (IC +11 a +26). Ampliar 12 fragmentos a su unidad rinde casi lo mismo que 50 fragmentos sueltos. Relevante para H1.1: la unidad normativa importa **también al leer**, no solo al trocear. `benchmarks/RESULTADOS_CONTEXTO.md`.
- **Tamaño del corpus:** 1.904.245 tokens de Gemini (1,58 por palabra); no cabe en la ventana de 1.048.576. El núcleo normativo, 352.704. Argumento medido para RAG frente a contexto largo.
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
3. Correr `kb_experiment` sobre el corpus real (en memoria) con MiniLM, `intfloat/multilingual-e5-small` (el de producción) y un tercer modelo con ≥ 512 tokens (`multilingual-e5-base` o `bge-m3`); añadir `chunking_v2` como estrategia comparada si se quiere evaluar el troceado desplegado.
4. Pegar tablas en §6, escribir §7 siguiendo los marcos fijados.
5. Insertar título del paper IPMU 2026 en la referencia `[Own]`.
6. Opcional: brazo `--llm` (propositions + grafo emergente), ejecutado dos veces para reportar varianza.

## 8. No hacer

- No inventar resultados ni recuentos PRISMA. No presentar el smoke test como evidencia.
- No tocar los índices de producción (`recavai-corpus-v2`, `uclm-corpus-roma`).
- No cambiar hipótesis, contrastes o reglas de decisión sin el usuario (están pre-registradas).
- No commitear `.env`, `results/`, `benchmarks/baseline.jsonl`, `benchmarks/run_*.jsonl`.
- No reescribir el paper en español: el destino es en inglés. La explicación sí va en español.

## 9. Documentos relacionados

- Artefactos (privados, claude.ai): "Líneas doctorales RAG jurídico" (define las 3 líneas), "Plan RAG, Explicabilidad y Benchmark" (§04 tareas T1–T9), "Metodología del Golden Set", "Plan del Golden Set v1" (8 semanas con IDPEI, κ).
- `benchmarks/README.md` (cómo correr todo), `docs/ARQUITECTURA_DATOS.md` (estado medido de corpus, índices, modelo y grafo), `docs/AGENTE.md` (asesor: habilidades, controles, harness), `docs/SPEC.md`.
