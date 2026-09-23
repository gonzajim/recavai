# Estado técnico de la capa de datos

Medido el 2026-09-23 contra los sistemas vivos: la carpeta del corpus, el índice de
Pinecone de producción y la instancia de Neo4j declarada en Cloud Run. No hay ninguna
cifra estimada; todas salen de leer los 9.176 vectores uno a uno, de tokenizar su texto
con el modelo real y de resolver el DNS de la base de grafos.

Se reproduce con:

```
python scripts/corpus_inventory.py --corpus "<carpeta>" --pinecone --out docs/CORPUS
python scripts/check_eurlex_versions.py --corpus "<carpeta>"
python scripts/fix_soft_hyphens.py
```

---

## 1. Corpus documental (origen)

69 documentos · **1.215.181 palabras** · 2.955 páginas · 80,9 MB · 60 PDF + 9 DOCX.

| Carpeta | Docs | Palabras | Páginas | Tamaño |
|---|---:|---:|---:|---:|
| 01_MARCO TEORICO | 1 | 122.601 | 258 | 2,5 MB |
| 02_NORMATIVAS | 6 | 235.247 | 472 | 5,2 MB |
| 03_ESTANDARES/GUIAS OCDE | 11 | 411.907 | 1.015 | 25,6 MB |
| 03_ESTANDARES/PROCESOS GRI | 41 | 433.929 | 1.198 | 47,3 MB |
| Audit | 9 | 8.544 | — | 240,8 KB |
| (raíz) | 1 | 2.953 | 12 | 72,7 KB |

Los ficheros **no están en el repositorio**: viven en una carpeta local y solo sus
vectores están en Pinecone. El detalle documento a documento está en [CORPUS.md](CORPUS.md).

### Vigencia normativa

Los tres textos de EUR-Lex están en su última consolidación publicada (comprobado
contra el repositorio Cellar):

| Documento | CELEX | En el corpus | Última publicada |
|---|---|---|---|
| CSDDD | 02024L1760 | 18.03.2026 (v002.002) | 18.03.2026 |
| CSRD | 02022L2464 | 18.03.2026 (v002.001) | 18.03.2026 |
| NEIS (ESRS) | 02023R2772 | 01.01.2025 (v001.001) | 01.01.2025 |

La CSDDD incorpora las Directivas (UE) 2025/794 (*stop the clock*) y 2026/470
(Ómnibus I) y las rectificaciones DO L 90894 y L 90192.

---

## 2. Base de datos vectorial

| | |
|---|---|
| Servicio | Pinecone **serverless** |
| Índice | `uclm-corpus-roma` |
| Host | `uclm-corpus-roma-dptaw1c.svc.aped-4627-b74a.pinecone.io` |
| Nube / región | AWS `us-east-1` |
| Tipo | denso |
| Dimensión | **384** |
| Métrica | coseno |
| Namespaces | 1, llamado literalmente `__default__` (no la cadena vacía) |
| Vectores | **9.176** |
| Protección de borrado | **desactivada** |

Región relevante: el índice está en Virginia y el servicio de Cloud Run en
`europe-west1`. Cada búsqueda cruza el Atlántico.

### Identificadores y metadatos

Los ids son globales y secuenciales (`chunk_000000`, `chunk_000001`…), no por
documento: no se puede reindexar un solo documento sin recalcular el resto.

Los 9.176 vectores llevan exactamente las mismas 9 claves:

| Clave | Contenido | Estado |
|---|---|---|
| `text` | texto literal del fragmento | poblado |
| `source` | ruta relativa sin extensión (`02_NORMATIVAS/01_CSDDD`) | poblado |
| `page` | página de inicio | poblado |
| `total_pages` | páginas del documento | poblado |
| `block_type` | `text` 8.995 · `figure` 181 | poblado |
| `primary_category` | `general` 4.571 · `GRI` 3.738 · `CSDDD` 829 · `CSRD` 38 | poblado |
| `graph_importance` | — | **siempre 0** |
| `entities` | — | **siempre `'[]'`, en los 9.176** |
| `triplets` | — | **siempre `'[]'`, en los 9.176** |

Es decir: el esquema declara enriquecimiento por grafo, y no hay ni una sola entidad
ni una sola tripleta almacenada.

### Tamaño de los fragmentos

| | mín | p25 | mediana | p75 | p95 | máx | media | total |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Caracteres | 1 | 227 | 634 | 1.791 | 1.799 | **1.800** | 863 | 7.915.251 |
| Palabras | 1 | 32 | 95 | 260 | 286 | 377 | 130,3 | 1.195.723 |

El máximo exacto de 1.800 caracteres delata un tope duro en el troceador. Las
1.195.723 palabras indexadas son el **98,4 %** de las del corpus: no falta contenido
por indexar y ningún documento se quedó fuera.

El *chunker* que produjo estos límites **no está en el repositorio** (`DEBT-3`). Los
vectores, en cambio, sí son reproducibles: cada uno es exactamente el embedding
normalizado de su propio campo `text` (coseno 1,0000 sobre muestra).

---

## 3. Modelo de generación de vectores

| | |
|---|---|
| Modelo | `sentence-transformers/all-MiniLM-L6-v2` |
| Arquitectura | BERT de 6 capas + *mean pooling* + normalización L2 |
| Dimensión de salida | 384 |
| Tokenizador | WordPiece, vocabulario de 30.522 piezas, **entrenado en inglés** |
| Longitud máxima | **256 word-pieces** |
| Dónde corre | en proceso, dentro de Cloud Run (2 vCPU / 4 GiB) |

Se usa el mismo modelo para indexar y para consultar, que es lo correcto. Dos
problemas, uno conocido y otro no:

**a) Modelo inglés sobre corpus español** (`DEBT-4`). Ya estaba documentado. El
vocabulario inglés fragmenta el español mucho más de lo normal: 1.800 caracteres de
texto jurídico español producen del orden de 550 word-pieces, más del doble de lo que
producirían en inglés.

**b) Truncado silencioso** (`DEBT-13`, nuevo). Es consecuencia directa de lo anterior:

| Word-pieces por fragmento | mín | p25 | mediana | p75 | p95 | máx |
|---|---:|---:|---:|---:|---:|---:|
| | 3 | 73 | **207** | 556 | 596 | 1.514 |

- **4.186 fragmentos (45,6 %) superan el límite de 256.**
- Se descartan **1.057.855 de 2.567.811 word-pieces: el 41,2 %**.
- Los fragmentos truncados contienen el **83,4 %** de las palabras del corpus.

Comprobado sobre un fragmento real de 735 tokens:

```
cos(vector guardado, embedding de solo los primeros 254 tokens) = 1,0000
cos(vector guardado, embedding de solo la cola descartada)      = 0,7398
```

El vector del fragmento entero y el de su primer tercio son **el mismo vector**. El
resto del fragmento no interviene en la recuperación.

Matiz importante: el texto completo sigue guardado en `text`, así que **si** el
fragmento se recupera, el modelo generador lo ve entero. Lo que está roto es la
recuperación: un fragmento solo se encuentra por lo que dicen sus primeras ~180
palabras. Una respuesta que esté en el segundo tercio de un fragmento es, en la
práctica, inalcanzable salvo que el principio del fragmento hable de lo mismo.

Documentos más afectados: NEIS 582 fragmentos, Marco Teórico 456, OCDE Textil 247,
OCDE Extractivo 177, OCDE EMN 159.

---

## 4. Grafo normativo

| | |
|---|---|
| Servicio | Neo4j AuraDB |
| URI | `neo4j+s://273d9982.databases.neo4j.io` |
| Base | `273d9982` |
| Credenciales | usuario en variable de entorno, contraseña en Secret Manager (`NEO4J_PASSWORD`) |

**La instancia no existe.** El host da `NXDOMAIN` en los resolutores de Google
(8.8.8.8) y de Cloudflare (1.1.1.1), mientras que el dominio padre
`databases.neo4j.io` sí resuelve. Es el patrón de una instancia Aura eliminada.

Por tanto:

- **Tripletas en el grafo: 0.** No es que estén vacías: no hay base contra la que
  contarlas.
- **Tripletas en Pinecone: 0** de 9.176 fragmentos.
- **No hay ningún ejemplo de tripleta que mostrar.** El grafo normativo existe como
  código y como esquema de metadatos, no como dato.

### Lo que sí existe: el código que lo consultaría

El enrutador clasifica cada pregunta por coincidencia de cadenas y decide estrategia:

| Tipo | Señales | Estrategia |
|---|---|---|
| `resource` | «cuáles son los», «qué herramientas», «qué sellos»… | **hybrid** (Pinecone + Neo4j) |
| `operational` | «cómo hago», «qué pasos», «qué documentos»… | **hybrid** |
| `conceptual` | «qué es», «en qué consiste», «explica»… | semantic |
| `unknown` | — | semantic |

En la rama híbrida se buscan 73 entidades canónicas (CSDDD, CSRD, EUDR, ESRS, REACH,
EMAS, GRI, OIT, OCDE, ISO 26000, «diligencia debida», «cadena de valor»…) por
subcadena dentro de la pregunta, y si alguna aparece se lanza:

```cypher
MATCH (n:Entity)
WHERE any(e IN $entities WHERE toLower(n.name) CONTAINS toLower(e))
OPTIONAL MATCH (n)-[r:RELATED]->(related:Entity)
RETURN n.name AS subject, r.predicate AS relation, related.name AS object
ORDER BY n.name LIMIT 40
```

con un presupuesto de 6.000 caracteres en el contexto.

### Por qué no se ha notado

`get_graph_context` devuelve cadena vacía ante cualquier fallo, y también cuando no
encuentra entidades o no hay tripletas. Los tres casos son indistinguibles desde
fuera: el asistente responde con normalidad, solo que sin grafo. En 30 días de logs de
producción no aparece ni una línea de «Graph RAG», lo que sugiere que el driver ni
siquiera llega a inicializarse.

Consecuencia para el sistema: **el sistema es RAG semántico puro**. Las descripciones
que lo llaman GraphRAG o «híbrido» describen el código, no lo que está corriendo.

---

## 5. Parámetros de recuperación

| Constante | Valor | Qué hace |
|---|---:|---|
| `_CANDIDATE_K` | 12 | candidatos que pide a Pinecone |
| `_MIN_SCORE` | 0,55 | umbral de coseno para el corpus global |
| `_MAX_RESULTS` | 6 | fragmentos que llegan al modelo generador |
| `_USER_DOCS_MIN_SCORE` | 0,25 | umbral para documentos subidos por el usuario |
| `_CHUNK_WORDS` | 400 | troceado de los PDF que sube el usuario |
| `_CHUNK_OVERLAP` | 50 | solape en ese troceado |
| `_MAX_CHUNKS` | 500 | tope por documento subido |
| `_CONTEXT_BUDGET` | 180.000 car. | contexto máximo hacia Gemini |
| `_MAX_OUTPUT_TOKENS` | 8.192 | salida máxima |

Los PDF que sube el usuario se trocean a 400 palabras, que son del orden de 800
word-pieces en español: **más del triple del límite del modelo**. El truncado los
afecta aún más que al corpus global.

---

## 6. Resumen de lo que está roto

| | Gravedad | Alcance |
|---|---|---|
| El 41,2 % de los tokens indexados no entra en el vector (`DEBT-13`) | alta | 4.186 de 9.176 fragmentos |
| El grafo normativo no existe; el sistema es RAG semántico puro (`DEBT-14`) | alta | toda la rama «hybrid» |
| 1.065 fragmentos con palabras partidas por guión (`DEBT-12`) | media | NEIS, CSRD, CSDDD |
| Modelo de embeddings inglés sobre corpus español (`DEBT-4`) | media | todo el índice |
| El *chunker* original no está en el repositorio (`DEBT-3`) | baja | reindexación completa |

Los dos primeros son la misma historia contada dos veces: se diseñó una arquitectura
de recuperación rica —grafo normativo, entidades, tripletas, enrutado híbrido— y lo
que quedó en producción es una búsqueda por coseno sobre el primer tercio de cada
fragmento.
