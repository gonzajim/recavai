# Plan: chunking, vectorización y grafo normativo

> **Estado (2026-09-23): ejecutado, en versión comprimida.** Este plan de 12 semanas se
> sustituyó por [PLAN_ACCION.md](PLAN_ACCION.md) y después por una ejecución en un solo día
> con una batería de 60 preguntas en lugar del conjunto de plata y del golden set. Se hizo:
> modelo `multilingual-e5-small` (Fase 1, sin comparar modelos grandes), troceado por unidad
> normativa (Fase 2, sin comparar las cinco estrategias), grafo determinista en memoria sin
> Neo4j (Fase 3, sin la capa de equivalencias entre marcos) e índice nuevo con ids por
> documento (Fase 4, en us-east-1 porque el plan gratuito de Pinecone no admite Europa).
> Una estimación de §1.2 resultó equivocada: e5-small «cabe de sobra» en 4 GiB con un
> proceso, pero no con los 4 que arrancaba gunicorn (~1 GB cada uno); se pasó a 2 procesos
> × 8 hilos. Resultados: [RAG_V2_RESULTADOS.md](RAG_V2_RESULTADOS.md). Lo que sigue es el
> plan tal como se escribió.

Propuesta de trabajo para las tres líneas que se quieren abordar. Todas las cifras que
aparecen aquí están medidas sobre el sistema real el 2026-09-23, no estimadas; el
detalle está en [ARQUITECTURA_DATOS.md](ARQUITECTURA_DATOS.md).

El plan está ordenado por dependencia, no por importancia. El orden importa por una
razón concreta que conviene decir antes que nada.

---

## 0. Por qué este orden

Hay un resultado medido que reordena las prioridades:

> El modelo actual corta en 256 *word-pieces*. El 45,6 % de los fragmentos lo superan
> y se descarta el 41,2 % de los tokens indexados. El vector de un fragmento **es** el
> vector de su primer tercio.

Esto tiene una consecuencia metodológica: **no se puede comparar estrategias de
chunking con este modelo**. Cualquier estrategia que produzca fragmentos más grandes
—que es lo que casi siempre mejora la calidad jurídica— sale penalizada por el
truncado, no por sus méritos. Medir chunking sobre un embeddador que tira el 41 % del
texto es medir el truncado.

Por eso: **primero el modelo, después el chunking, después el grafo.** Y antes que los
tres, algo con lo que medir.

---

## Fase 0 — Con qué medir (2 semanas)

Ahora mismo no hay forma de decir si un cambio mejora o empeora. `src/kb_experiment.py`
—el banco factorial ya escrito— exige `--golden`, y el *golden set* v1 no existe: está
pendiente del taller con el IDPEI, 8 semanas según el plan acordado.

Esperar 8 semanas para empezar a medir no es necesario. Propongo dos conjuntos, uno
barato ahora y el bueno después.

### 0.1 Conjunto de plata, automático y sin anotación humana

El corpus regulatorio tiene una propiedad que lo hace autoevaluable: **el texto se cita
a sí mismo**. «de conformidad con el artículo 8, apartado 2», «según se establece en el
anexo I», «véase el contenido 305-1». Cada una de esas referencias es un par
(consulta, respuesta correcta) que nadie tiene que anotar.

Material disponible, contado sobre el corpus real:

| Familia | Referencias explotables |
|---|---:|
| CSDDD: artículo → artículo | 213 (127 pares distintos) |
| CSDDD: → anexo | 18 |
| CSRD: artículo → artículo | 95 (33 pares distintos) |
| NEIS: menciones de códigos ESRS | 636 sobre 82 códigos |
| GRI: menciones de contenidos | 1.165 sobre 94 códigos |

**Procedimiento.** Para cada referencia: tomar la frase que la contiene, enmascarar la
referencia («de conformidad con el artículo ███»), y usarla como consulta; el objetivo
correcto es el fragmento que contiene ese artículo. Se obtiene del orden de 1.500–2.000
ítems con verdad-terreno exacta y coste cero de anotación.

Limitaciones que hay que declarar: son consultas en lenguaje jurídico, no en lenguaje de
usuario, y miden recuperación intra-documento. **No sustituyen al golden set**; sirven
para descartar configuraciones malas rápido y para la condición «literal» de H1.2.

Para la condición «parafraseada» se generan variantes con Gemini a partir de esas mismas
frases y se valida a mano una muestra del 10 %.

### 0.2 Golden set v1 (el que decide)

El que ya está planificado con el IDPEI. Sigue siendo el criterio de aceptación para
producción y para el artículo. El conjunto de plata es un filtro previo, no un
sustituto.

### 0.3 Qué se mide

Ya está implementado en `kb_experiment.evaluate()`: `hit`, `recall@k`, `precision@k`,
`mrr`, y la familia `citation@article` (estricta, indulgente, precisión y
todo-o-nada). La estadística también: bootstrap pareado con 2.000 remuestreos, Wilcoxon
y corrección de Holm.

**Entregable de la fase:** `scripts/build_silver_set.py` y `data/silver_set_v1.jsonl`.

---

## Fase 1 — Modelo de vectorización para texto legal en español (2 semanas)

### 1.1 Candidatos y por qué

El problema del modelo actual no es solo que sea inglés: es que su vocabulario
*WordPiece* de 30.522 piezas fragmenta el español al doble, y por eso choca contra su
propio límite de 256. Un vocabulario multilingüe *SentencePiece* de 250.000 piezas
resuelve las dos cosas a la vez.

Medido tokenizando **los mismos 9.176 fragmentos** con cada tokenizador:

| Modelo | Params | Dim | Límite | Tokens totales | Frag. truncados | Tokens perdidos |
|---|---:|---:|---:|---:|---:|---:|
| `all-MiniLM-L6-v2` (actual) | 22,7 M | 384 | 256 | 2.567.811 | **45,6 %** | **41,2 %** |
| `multilingual-e5-small` | 117,7 M | **384** | 512 | 1.759.599 | 0,1 % | **0,0 %** |
| `multilingual-e5-base` | 278,0 M | 768 | 512 | 1.759.599 | 0,1 % | 0,0 % |
| `gte-multilingual-base` | 305,4 M | 768 | 8192 | 1.759.599 | 0,0 % | 0,0 % |
| `jina-embeddings-v2-base-es` | 160,9 M | 768 | 8192 | — | 0,0 % | 0,0 % |
| `bge-m3` | ~568 M | 1024 | 8192 | — | 0,0 % | 0,0 % |

El vocabulario multilingüe consume **un 31 % menos de tokens** para el mismo texto.

Descartado de entrada: `paraphrase-multilingual-mpnet-base-v2`, que tiene un límite de
128 tokens —peor que el actual— pese a aparecer como candidato en el CLI del
experimento.

### 1.2 El candidato con mejor relación coste/beneficio

`intfloat/multilingual-e5-small` merece un comentario aparte porque **mantiene las 384
dimensiones**. Eso significa que no cambia la forma del índice: mismo esquema, misma
métrica, migración mucho más simple.

Coste real, medido en local:

| | actual | e5-small |
|---|---:|---:|
| Memoria (fp32) | 91 MB | 471 MB |
| Carga en frío | 4,0 s | 11,6 s |
| Latencia por consulta | 4 ms | 7 ms |

En una instancia de 2 vCPU / 4 GiB cabe de sobra. **+3 ms por consulta y +380 MB de RAM
a cambio de pasar del 41,2 % de texto descartado al 0,0 %.**

Los de 768 y 1024 dimensiones se evalúan igualmente, pero cambian la dimensión del
índice y multiplican memoria y latencia; tienen que ganar por un margen que lo
justifique.

### 1.3 Detalles que hay que respetar

- La familia **e5 exige prefijos**: `query:` en consultas y `passage:` en documentos.
  Olvidarlos degrada mucho el resultado. `corpus_pipeline.embed_texts` ya lo hace
  (`_needs_e5_prefix`); `rag_service.generate_embedding` **no**, y hay que añadirlo.
- `ARC-3` del SPEC: cambiar el modelo sin reindexar es un fallo de corrección, no un
  cambio de configuración. Índice nuevo, siempre.
- `gte-multilingual-base` requiere `trust_remote_code=True`. Es código de terceros
  ejecutándose en producción; hay que decidirlo conscientemente.

### 1.4 Protocolo

Ejecutar el banco factorial sobre el conjunto de plata, con chunking fijo en la
estrategia actual para aislar el efecto del modelo:

```
python -m src.kb_experiment --corpus "<carpeta>" --golden data/silver_set_v1.jsonl \
  --embedding-models intfloat/multilingual-e5-small intfloat/multilingual-e5-base \
                     Alibaba-NLP/gte-multilingual-base sentence-transformers/all-MiniLM-L6-v2 \
  --chunking semantic_struct --graph none --cache .cache/kbexp --out results/fase1
```

**Criterio de decisión (fijado antes de mirar los resultados):** gana el modelo más
pequeño cuyo intervalo de confianza del 95 % de `recall@6` no sea inferior al del mejor,
con Holm aplicado. Ante empate estadístico, decide el tamaño.

**Entregable:** modelo elegido, informe con intervalos, y `EMBEDDING_MODEL_NAME`
actualizado en `.env.example` pero **no** en producción todavía.

---

## Fase 2 — Chunking (3 semanas, solapa con la fase 1)

### 2.1 Qué falla hoy

El troceador actual no está en el repositorio (`DEBT-3`), así que se describe por lo que
dejó en el índice:

- Tope duro de **1.800 caracteres**, mediana 634, p25 227, **mínimo 1 carácter**.
- Metadatos: `page` (solo la de inicio), `source`, `block_type`, `primary_category`.
- **No hay `section`, ni `article`, ni `page_end`.**

Esa última línea es la más grave para lo que hace este sistema. Sin `article`, el
asistente no puede citar «artículo 8, apartado 2» salvo que el número aparezca por
casualidad dentro del texto del fragmento, y **la métrica `citation@article` del
artículo no se puede calcular en producción**: solo en el banco de pruebas, que sí
reconstruye esa información.

### 2.2 Estrategia propuesta: estructura primero

La unidad natural de un texto regulatorio no es un número de caracteres: es el artículo,
el apartado, el requisito ESRS o el contenido GRI. La propuesta es trocear por esa
unidad y solo después por semántica:

1. **Segmentar por unidad normativa.** Detectar encabezados (`Artículo N`, `ANEXO I`,
   `E1-6`, `305-1`) y cortar ahí. Nunca cruzar una frontera de artículo.
2. **Partir por semántica lo que se pase de tamaño**, con el umbral de percentil que ya
   implementa `corpus_pipeline._find_breakpoints`.
3. **Fusionar lo que quede demasiado pequeño** con su vecino dentro del mismo artículo.
   Esto mata de raíz los fragmentos de 1 carácter.
4. **Cabecera contextual embebida, no mostrada**: `«CSDDD · Artículo 8 · Diligencia
   debida en las operaciones propias»` se antepone al texto *para calcular el vector*,
   pero la cita literal que ve el usuario sigue siendo el texto puro. Es la estrategia
   `semantic_struct_ctx` del banco. Con 512 tokens cabe sin problema; con 256 no cabía.
5. **Metadatos nuevos**: `article`, `articles_spanned`, `section`, `page_end`,
   `unit_type` (`articulo` | `anexo` | `esrs` | `gri` | `parrafo`).

### 2.3 Variantes a comparar

Las cinco ya implementadas en `CHUNKING_STRATEGIES`: `fixed` (la de producción, como
línea base), `semantic_free`, `semantic_struct`, `semantic_struct_ctx` y `small_to_big`
(indexar hijos pequeños, devolver padres grandes — prometedor porque desacopla la
granularidad de búsqueda de la de lectura).

`propositions` queda fuera de la primera ronda: cuesta una llamada al LLM por fragmento
y conviene ver antes si hace falta.

### 2.4 Un aviso sobre el corpus OCDE

Las guías de la OCDE son 11 documentos y 411.907 palabras —un tercio del corpus— y **no
tienen numeración de artículos**. Toda la estrategia de estructura se apoya ahí en
encabezados de sección, que son mucho menos fiables. Hay que medir el chunking por
familia documental (EUR-Lex / ESRS / GRI / OCDE) y no solo en agregado, o el promedio
esconderá que en un tercio del corpus no funciona.

**Entregable:** `src/chunking_v2.py`, comparativa por familia documental, y la
estrategia ganadora con su intervalo de confianza.

---

## Fase 3 — Grafo de conocimiento normativo (3 semanas)

### 3.1 Punto de partida: no hay grafo

La instancia de Neo4j declarada en Cloud Run **no existe** (NXDOMAIN en 8.8.8.8 y
1.1.1.1). `entities` y `triplets` valen `'[]'` en los 9.176 vectores. Cero tripletas,
cero ejemplos. El sistema que está en producción es RAG semántico puro (`DEBT-14`).

Eso, visto de otra manera, es una ventaja: no hay nada que migrar ni que respetar.

### 3.2 Cuánto grafo hay realmente

Antes de elegir tecnología conviene saber el tamaño. Medido sobre el corpus:

| Familia | Nodos | Aristas / menciones |
|---|---:|---:|
| CSDDD | 38 artículos + 6 anexos | 213 referencias (127 pares) + 18 a anexos |
| CSRD | 14 artículos | 95 referencias (33 pares) |
| NEIS / ESRS | 82 códigos + 6 anexos | 636 menciones + 132 a anexos |
| GRI | 94 contenidos | 1.165 menciones |
| **Total aproximado** | **~240 nodos** | **~2.100 aristas tipadas** |

**Doscientos cuarenta nodos.** Esto cambia una decisión de arquitectura: un grafo de
este tamaño no justifica una base de datos de grafos gestionada, con su secreto, su
salto de red y su modo de fallo silencioso. Cabe en un JSON de pocos cientos de KB
cargado en memoria al arrancar.

**Propuesta: retirar Neo4j y sustituirlo por un artefacto precalculado en el
repositorio.** Se gana reproducibilidad (el grafo pasa a estar versionado junto al
código), se elimina una dependencia externa y desaparece el fallo silencioso. Se pierde
la posibilidad de consultas Cypher arbitrarias, que hoy no se usa: la única consulta del
sistema es una expansión de un salto.

Si más adelante el grafo crece un orden de magnitud —al incorporar la Directiva
2013/34/UE, el Reglamento de Taxonomía, normativa nacional— se reconsidera.

### 3.3 Qué aristas construir

Dos capas, con criterios de calidad muy distintos.

**Capa determinista (sin LLM, sin alucinación posible).** Es la que sostiene el grafo:

| Relación | Origen | Ejemplo |
|---|---|---|
| `REFERENCIA` | expresión regular sobre «artículo N, apartado M» | `CSDDD art.3 → CSDDD art.2` |
| `ANEXO` | «anexo I», «anexo II» | `NEIS art.1 → NEIS anexo.I` |
| `DEFINE` | artículo de definiciones → término | `CSDDD art.3 → «cadena de actividades»` |
| `MODIFICA` | marcadores `▼M1` `▼M2` del texto consolidado | `Dir. 2026/470 → CSDDD art.6` |
| `JERARQUIA` | estructura del documento | `ESRS E1-6 → ESRS E1` |

La relación `MODIFICA` es una oportunidad concreta: los textos consolidados de EUR-Lex
llevan marcadas las modificaciones con `▼B`, `▼M1`, `▼M2`. Eso permite responder «¿qué
cambió el Ómnibus en el artículo 6?» con trazabilidad, que es exactamente el tipo de
pregunta que hace un jurista y que hoy el sistema no puede contestar.

**Capa de equivalencias entre marcos.** Es donde está el valor para el usuario final:
«este requisito de la CSDDD se cubre con el contenido GRI 308-1 y el datapoint ESRS
S2-1». Aquí hay que ser prudente:

- **Primero, las tablas de correspondencia publicadas.** GRI y EFRAG publican tablas
  oficiales de equivalencia. Si están en el corpus, se extraen de ahí y son verdad
  documentada, no inferencia.
- **Solo después, el LLM**, y como propuesta a validar, nunca como arista directa. Cada
  equivalencia generada por Gemini lleva su fragmento de origen y pasa por revisión del
  IDPEI antes de entrar. El código de extracción ya existe (`corpus_pipeline.enrich_chunks`).

Esta separación es lo que permite decir en el artículo que el esqueleto es
reproducible y auditable, y acotar la parte inferida.

### 3.4 Cómo se usa en recuperación

Expansión de un salto, acotada, como ya implementa `NormativeGraph.expand`: se recuperan
los k fragmentos por similitud, se añaden los fragmentos de los artículos que estos
referencian, y se reordena por similitud con la consulta. El presupuesto de expansión
(hoy 3 fragmentos) es un parámetro a barrer.

**Hay que medirlo en las dos direcciones.** La expansión por grafo sube el recall pero
puede hundir la precisión: traer el artículo 2 (definiciones) a cada respuesta porque
todo el mundo lo referencia es ruido, no contexto. Por eso `_build_emergent` ya descarta
entidades con más de 25 miembros, y por eso la métrica que decide es
`citation@article` con precisión, no recall a secas.

**Entregable:** `scripts/build_normative_graph.py`, `data/normative_graph.json`,
sustitución de `get_graph_context` por la versión en memoria, y la medida del efecto del
grafo con su intervalo de confianza.

---

## Fase 4 — Puesta en producción (2 semanas)

1. **Índice nuevo, no sobrescritura.** `recavai-corpus-v2`, con el modelo y el chunking
   ganadores. El índice actual se conserva intacto para poder volver atrás.
2. **Región.** El índice actual está en AWS `us-east-1` y el servicio en
   `europe-west1`: cada búsqueda cruza el Atlántico. El índice nuevo debería crearse en
   una región europea. Conviene además mirarlo desde el lado del RGPD.
3. **Identificadores por documento.** Los ids actuales son globales y secuenciales
   (`chunk_000000`…), lo que impide reindexar un solo documento. En el índice nuevo:
   `{source_hash}_{n:04d}`. Con eso, actualizar la CSDDD tras el próximo Ómnibus es
   reindexar un documento, no el corpus.
4. **Activar la protección de borrado**, hoy desactivada.
5. **Despliegue canario**, comparando respuestas de ambos índices sobre el golden set
   antes de mover el tráfico. El *pipeline* ya lo soporta (`scripts/deploy.sh`).
6. **Corregir `DEBT-12`** (1.065 fragmentos con palabras partidas) en el mismo paso: si
   se reindexa desde el PDF con el troceador nuevo, desaparece sin trabajo extra.

---

## Calendario y dependencias

```
Semana  1  2  3  4  5  6  7  8  9 10 11 12
F0 plata ██████
F0 golden ████████████████████████  (taller IDPEI, en paralelo)
F1 modelo    ██████
F2 chunking     █████████
F3 grafo              █████████
F4 producción                 ██████
```

La fase 4 no arranca hasta que el golden set v1 valide lo que eligió el conjunto de
plata. Si el golden set contradice al de plata, manda el golden set y se rehace la
elección: es el riesgo asumido por empezar antes de tenerlo.

---

## Decisiones que necesito de ti

| # | Decisión | Mi recomendación |
|---|---|---|
| 1 | ¿Se arranca con el conjunto de plata o se espera al golden set? | Arrancar. El truncado es tan grande que ninguna elección razonable va a depender del conjunto de evaluación. |
| 2 | ¿Se retira Neo4j? | Sí. 240 nodos no justifican una base de grafos gestionada, y la actual lleva meses muerta sin que se notara. |
| 3 | ¿Índice nuevo en región europea? | Sí. Elimina el salto transatlántico y simplifica el encaje con el RGPD. |
| 4 | ¿Se permite `trust_remote_code=True` si gana `gte-multilingual-base`? | Preferiría que no; si e5-small queda estadísticamente empatado, se elige e5-small. |
| 5 | ¿Quién valida las equivalencias entre marcos? | El IDPEI, en el mismo taller del golden set. Es trabajo jurídico, no técnico. |
| 6 | ¿Se aplica ya la corrección de guiones (`DEBT-12`) o se espera al reindexado? | Esperar, si la fase 4 llega en 12 semanas. Aplicarla ya si el calendario se alarga. |

---

## Lo que este plan no resuelve

- **`DEBT-1`: las reglas de Firestore siguen abiertas** (`allow read, write: if true`).
  No tiene nada que ver con el RAG y es más urgente que todo lo anterior.
- La clave de OpenAI de la etapa anterior sigue sin revocar en el panel del proveedor.
- El *chunker* original seguirá sin aparecer; lo que se hace aquí es sustituirlo por uno
  versionado, no recuperarlo.
