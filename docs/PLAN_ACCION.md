# Plan de acción sin esperar al golden set

Versión ejecutable de [PLAN_RAG_V2.md](PLAN_RAG_V2.md). Aquel plan bloqueaba la puesta
en producción hasta tener el golden set v1; este no. Seis semanas, seis entregas, cada
una con su criterio de aceptación y su vuelta atrás.

---

## El principio que lo hace posible

El golden set hace falta para **afirmar un resultado**, que es lo que exige el artículo.
No hace falta para **arreglar un sistema roto**. Son cosas distintas y conviene
separarlas, porque la mayoría de lo que hay que hacer no admite duda.

Clasifico cada cambio en tres clases, y cada clase se trata de forma diferente:

| Clase | Definición | Qué exige |
|---|---|---|
| **A · Dominado** | Estrictamente mejor, sin contrapartida posible | Nada. Solo verificar que no rompe |
| **B · Diferencia grande** | Hay contrapartida, pero el efecto esperado es enorme | Conjunto de plata + ancla de registro |
| **C · Diferencia fina** | La elección depende de matices del registro del usuario | **No decidir.** Congelar el valor actual |

La regla de la clase C es la que hace honesto el plan: donde no hay evidencia
suficiente, **no se toca**. No se elige el mejor de dos empatados; se deja el que está.

---

## Las tres fuentes de evidencia que ya existen

### 1. Conjunto de plata — ~2.100 autorreferencias del corpus

Descrito en `PLAN_RAG_V2.md` §0.1. Verdad-terreno exacta, coste de anotación cero.
Limitación: consultas en registro jurídico.

### 2. 280 preguntas reales de usuario

En BigQuery hay **420 turnos registrados, 119 hilos, 16 usuarios, de junio de 2025 a
septiembre de 2026**: 280 del asesor y 140 del auditor. Son preguntas reales, en el
registro real:

> «A qué empresas aplica la CSDDD?»
> «Cómo puedo identificar y cuantificar mis emisiones?»
> «Somos una pyme española que importa ropa laboral y uniformes desde Bangladesh…»
> «¿Qué riesgos sociales, laborales y ambientales deberíamos priorizar…?»

No están etiquetadas, así que no dan recall. Sirven para otras dos cosas que el conjunto
de plata no puede dar:

- **Ancla de registro.** Comprobar que las consultas sintéticas se parecen a las reales
  (longitud, vocabulario, distancia en el espacio de embeddings). Si no se parecen, el
  conjunto de plata está midiendo otra cosa y hay que saberlo.
- **Banco de no-regresión.** Recuperar con la configuración vieja y con la nueva sobre
  las mismas 280 preguntas y comparar los contextos lado a lado con un juez LLM
  (ciego al orden). No dice cuál es correcta; dice si empeoró.

Hay que filtrar antes: parte de esos turnos son de prueba («¿Qué significa
ZORROVIOLETA42?», «quiénes son tus desarrolladores»). Estimo 150–200 sustantivas.

### 3. Comparación en sombra sobre tráfico real

Durante el cambio, resolver cada consulta real contra los dos índices y registrar ambos
resultados, sirviendo siempre el viejo. Coste: una búsqueda extra por turno. Da datos de
producción sin arriesgar producción.

---

## Sprint 0 — Lo que no necesita nada (1 día)

Cambios de clase A puros. Se hacen hoy.

| # | Acción | Estado |
|---|---|---|
| 0.1 | Alinear `firestore.rules` con lo que está publicado | **hecho** (ver abajo) |
| 0.2 | Activar la protección de borrado del índice de Pinecone | pendiente |
| 0.3 | Revocar la clave de OpenAI en el panel del proveedor | pendiente, requiere tu acceso |
| 0.4 | Investigar el secreto `gcp-credentials` (parece clave de cuenta de servicio de larga duración) | pendiente |
| 0.5 | Subir los 14 commits pendientes a GitHub | pendiente, requiere tu autorización |

### Corrección sobre `firestore.rules`

Dije en turnos anteriores que las reglas abiertas eran «el riesgo de seguridad vivo más
serio». **Era falso y conviene corregirlo porque cambia la urgencia.** Consultando la
API de Firebase Rules, el *ruleset* publicado (`cbe2190d…`, de 2025-10-19) es:

```
allow read, write: if false;
```

Que es lo correcto en esta arquitectura: todo el acceso pasa por el orquestador con el
Admin SDK, que no está sujeto a las reglas. La base de datos **no está abierta**.

El defecto real es otro, y sigue siendo real: el fichero del repositorio decía `if true`
y **había divergido de producción**, de modo que un `firebase deploy --only
firestore:rules` desde una copia limpia habría abierto la base de datos al mundo. Ya
está alineado y documentado. `DEBT-1` baja de gravedad alta a media y cambia de motivo.

---

## Sprint 1 — Instrumentación y línea base (semana 1)

No se cambia nada del sistema. Se construye con qué medirlo y se congela dónde está.

**Entregables**

- `scripts/build_silver_set.py` → `data/silver_set_v1.jsonl` (~1.500–2.000 ítems)
- `scripts/build_regression_set.py` → `data/real_queries.jsonl` (las reales, filtradas)
- `scripts/compare_retrieval.py` — recupera con dos configuraciones y compara
- `results/baseline_v1.json` — la medida del sistema **actual**, tal cual está hoy

**Criterio de aceptación.** La línea base existe y es reproducible. El ancla de registro
está calculada: distancia media entre consultas de plata y consultas reales en el
espacio de embeddings, con su desviación.

> Si el ancla revela que las consultas de plata no se parecen en nada a las reales, hay
> que decirlo aquí y reducir el peso que se les da en los sprints 2 y 3, no descubrirlo
> al final.

---

## Sprint 2 — Modelo de vectorización (semana 2)

Clase B: hay contrapartida (memoria, latencia) pero el efecto es enorme (41,2 % del
texto descartado frente a 0,0 %).

**Acción**

```
python -m src.kb_experiment --corpus "<carpeta>" --golden data/silver_set_v1.jsonl \
  --embedding-models intfloat/multilingual-e5-small intfloat/multilingual-e5-base \
                     Alibaba-NLP/gte-multilingual-base sentence-transformers/all-MiniLM-L6-v2 \
  --chunking semantic_struct --graph none --cache .cache/kbexp --out results/sprint2
python scripts/compare_retrieval.py --queries data/real_queries.jsonl --judge
```

**Criterio de decisión, fijado ahora y por escrito:**

1. Gana el modelo más pequeño que **domine** al actual: no peor en ninguna métrica
   primaria, mejor en al menos una, con el IC del 95 % excluyendo el 0.
2. Si dos candidatos empatan estadísticamente → **clase C** → gana el más pequeño y el
   que no cambie la dimensión del índice. Es decir, `multilingual-e5-small`, que mantiene
   las 384 dimensiones.
3. Si **ninguno** domina al actual → no se cambia el modelo y se escala el problema.
   Es un resultado posible y hay que estar dispuesto a aceptarlo.

**Cambio de código obligatorio si gana la familia e5:** añadir los prefijos `query:` /
`passage:` a `rag_service.generate_embedding`. Sin ellos el modelo rinde muy por debajo
de lo que debe. `corpus_pipeline` ya los pone; el servicio en línea no.

**Vuelta atrás.** El índice viejo se conserva. Revertir es cambiar dos variables de
entorno y redesplegar.

---

## Sprint 3 — Chunking y metadatos (semanas 3–4)

Dos partes con clase distinta, y conviene no mezclarlas.

### 3a. Metadatos de estructura — clase A

Añadir `article`, `articles_spanned`, `section`, `page_end`, `unit_type`. Es adición
pura: nada de lo que hoy funciona depende de que esos campos no existan, y sin ellos
**el asistente no puede citar «artículo 8, apartado 2»** salvo por casualidad. No
requiere evaluación previa.

### 3b. Estrategia de troceado — clase B

Comparar las cinco estrategias ya implementadas, **por familia documental**
(EUR-Lex / ESRS / GRI / OCDE) y no solo en agregado. Recordatorio del riesgo: las guías
OCDE son un tercio del corpus y no tienen numeración de artículos, así que la estrategia
estructural se apoya allí en encabezados de sección, mucho menos fiables.

**Criterio de decisión.** Mismo principio de dominancia. Si la estrategia estructural
gana en EUR-Lex/ESRS/GRI pero pierde en OCDE, **se aplica por familia**, no se fuerza una
sola para todo el corpus. Es más código, pero es la respuesta honesta al dato.

**Regalo incluido.** Reindexar desde el PDF con el troceador nuevo elimina de paso los
1.065 fragmentos con palabras partidas (`DEBT-12`) sin trabajo adicional.

---

## Sprint 4 — Grafo normativo (semana 5)

Clase A en su capa determinista: hoy el grafo aporta **exactamente cero** (la instancia
de Neo4j no existe), así que cualquier grafo que funcione es una mejora sobre nada.
Clase C en su parametrización.

**Acción**

1. `scripts/build_normative_graph.py` → `data/normative_graph.json`. Capa determinista
   solamente: `REFERENCIA`, `ANEXO`, `DEFINE`, `MODIFICA`, `JERARQUIA`. ~240 nodos,
   ~2.100 aristas. Sin LLM, sin alucinación posible.
2. Sustituir `get_graph_context` por una versión en memoria que lea ese JSON. **Retirar
   el driver de Neo4j, la variable `NEO4J_URI` y el secreto `NEO4J_PASSWORD`.**
3. Que el fallo deje de ser silencioso: si el grafo no carga, debe registrarse como
   error, no devolver cadena vacía indistinguible de «no había entidades».

**La capa de equivalencias entre marcos queda fuera de este plan.** Requiere validación
jurídica del IDPEI y es exactamente el tipo de cosa que no se debe meter sin golden set.

**Criterio de decisión sobre la expansión.** El presupuesto de expansión (hoy 3
fragmentos) es clase C: se deja en 3 y no se optimiza. Lo que sí se exige es que activar
el grafo **no empeore la precisión** en el conjunto de plata; si la empeora, se entrega
el grafo construido pero desactivado en recuperación, y se decide con el golden set.

---

## Sprint 5 — Puesta en producción (semana 6)

1. Índice nuevo `recavai-corpus-v2` en **región europea** (hoy `us-east-1`, con el
   servicio en `europe-west1`: cada búsqueda cruza el Atlántico).
2. Identificadores por documento (`{source_hash}_{n:04d}`) en lugar de la numeración
   global secuencial actual, que impide reindexar un solo documento.
3. **Una semana en sombra**: ambos índices resuelven, solo el viejo responde, ambos se
   registran.
4. Revisión de la sombra: si el juez LLM da al índice nuevo igual o mejor contexto en
   ≥ 90 % de las consultas reales, se pasa al canario.
5. Canario y traspaso de tráfico con `scripts/deploy.sh`.
6. El índice viejo se conserva **tres meses**, no se borra al cambiar.

---

## Qué sustituye a la garantía del golden set

| Garantía que daría el golden set | Sustituto en este plan |
|---|---|
| «La configuración nueva es mejor» | Criterio de dominancia: no peor en nada, mejor en algo |
| «Es mejor para usuarios reales» | Ancla de registro + juez sobre 280 consultas reales |
| «No rompe casos que funcionaban» | Semana en sombra sobre tráfico real |
| «Merece la pena el riesgo» | Índice viejo intacto, vuelta atrás en un minuto |

### Lo que este plan NO autoriza a afirmar

- Que el chunking estructural mejora la recuperación **en general**. Sobre el conjunto
  de plata, sí; publicarlo requiere el golden set.
- Ninguna de las hipótesis H1.1–H1.4 del artículo. El banco factorial se ejecuta aquí
  con datos de plata y eso es material de desarrollo, no de resultados.
- Nada sobre equivalencias entre marcos normativos.

**El artículo sigue bloqueado por el golden set. El sistema, no.**

---

## Una forma de que el golden set deje de ser un cuello de botella

Al revisar BigQuery apareció algo que no esperaba: la tabla tiene una columna
`expert_response`, y el panel de administración ya permite que un experto corrija una
respuesta. **De 420 turnos registrados, solo 1 tiene corrección de experto.**

Es decir: la infraestructura para construir el golden set de forma incremental ya está
construida y sin usar. Si el IDPEI corrigiera cinco respuestas al día sobre preguntas
reales, en ocho semanas habría unos 200 ítems anotados —con preguntas del registro
real, que es justo lo que al conjunto de plata le falta— sin un solo taller.

Sugiero plantearlo en la próxima reunión con el IDPEI como alternativa o complemento al
taller. Cuesta unos minutos al día y empieza a producir desde el primer día.

---

## Resumen de una línea por sprint

| Sprint | Semana | Entrega | Riesgo si sale mal |
|---|---|---|---|
| 0 | hoy | Higiene: reglas, protección de borrado, claves | Ninguno |
| 1 | 1 | Conjunto de plata, banco de regresión, línea base | Se descubre que no se puede medir. Mejor saberlo ya |
| 2 | 2 | Modelo elegido y justificado | Ninguno domina → no se cambia |
| 3 | 3–4 | Troceador nuevo con metadatos de artículo | Se aplica por familia documental |
| 4 | 5 | Grafo determinista, sin Neo4j | Se entrega construido pero desactivado |
| 5 | 6 | Índice v2 en producción con sombra y canario | Vuelta atrás en un minuto |
