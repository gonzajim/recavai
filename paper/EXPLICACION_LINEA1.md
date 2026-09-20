# Explicación del artículo de la Línea 1 (para el autor)

Este documento acompaña a `linea1_kb_construction_es_regulatory_rag.md`. Explica qué hay en cada sección, por qué está ahí, qué decisiones he tomado por ti y qué te queda por hacer. Léelo antes de tocar el artículo.

---

## 0. Lo primero: qué es y qué no es este borrador

**Es** un artículo completo en estructura: resumen, introducción, revisión sistemática con protocolo, método formalizado, diseño experimental pre-registrado, plantillas de resultados y discusión, amenazas a la validez, referencias y apéndices. **Está en inglés** porque los destinos (ICAIL, JURIX, *AI & Law*, ECIR/SIGIR) son en inglés; escribirlo en español obligaría a traducirlo entero después.

**No es** un artículo con resultados. No existe todavía el golden set v1 (lo produce la Línea 3, plan de 8 semanas con IDPEI) y por tanto no se ha corrido el experimento. La sección 6 (Resultados) y la 7 (Discusión) son **plantillas pre-registradas**: dicen qué tablas habrá y cómo se interpretará cada resultado posible *antes* de verlo. Esto no es una debilidad: es lo que distingue un estudio confirmatorio de uno exploratorio, y un tribunal lo valora.

**Tampoco es** una revisión sistemática ejecutada. La sección 3 tiene el protocolo completo (preguntas, fuentes, cadenas de búsqueda, criterios de inclusión/exclusión, formulario de extracción) y una síntesis basada en el conjunto semilla de ~40 estudios que conozco. Los recuentos PRISMA están como `[n]` porque inventarlos sería fraude. Tienes que ejecutar las búsquedas en Scopus/ACL/ACM/IEEE/arXiv y rellenarlos.

---

## 1. Resumen e introducción (§Abstract, §1)

**Qué dice.** Plantea el problema (el RAG regulatorio depende de cómo se construye la base de conocimiento), lo que se sabe (chunking semántico, proposiciones, RAPTOR, contextual retrieval, GraphRAG existen, en inglés y dominio general) y lo que no (cómo se comportan sobre texto normativo europeo en español, evaluado a nivel de artículo). Formula la pregunta de investigación y las cuatro hipótesis H1.1–H1.4 tal como las definiste, y lista cuatro contribuciones.

**Decisión importante: §1.5 "On novelty".** Tu propio análisis de riesgos dice: *"el chunking semántico ya está inventado; la novedad tiene que estar en el estudio controlado y en el dominio, no en el método. Hay que decirlo así en el paper."* Lo he dicho exactamente así, en una subsección propia. Un revisor que lea eso no puede acusarte de vender un método viejo como nuevo; te está diciendo de antemano dónde buscar el valor.

**Qué te queda.** Confirmar los datos del despliegue original que cito (índice con mediana de 2 palabras por bloque; línea base hit@k 0,80 / recall@k 0,65). Son los que medimos en septiembre; si reindexas antes de enviar, actualízalos.

---

## 2. Background (§2)

**Qué dice.** Define los términos: qué es la etapa de construcción de la base de conocimiento, las cinco familias de segmentación (fija, recursiva, semántica por breakpoint, proposiciones, jerárquica), qué es enriquecimiento (cabecera contextual, *late chunking*), qué es recuperación con grafo, y — la parte que más importa — las cinco propiedades del texto regulatorio europeo en español (§2.5): jerarquía explícita, referencias cruzadas densas, instrumentos solapados con distinta autoridad, versionado temporal, idioma.

**Por qué está.** §2.5 es el argumento de por qué este corpus no es "otro dataset más": cada propiedad es una razón por la que los resultados en inglés/Wikipedia no se trasladan. Y cierra con la frase que gobierna todo el artículo: *la unidad que cita un jurista es el artículo*.

---

## 3. Revisión sistemática (§3)

Esta es la sección que convierte el artículo en "nivel doctorado". Tiene cuatro partes:

**3.1 Protocolo.** Sigue Kitchenham & Charters (revisiones sistemáticas en ingeniería de software) y PRISMA 2020. Define cuatro preguntas de revisión (RQ-A a RQ-D), las fuentes, cuatro cadenas de búsqueda (S1–S4), el periodo (2020–2026), criterios de inclusión (I1–I3) y exclusión (E1–E5), el cribado a dos revisores con κ, el formulario de extracción y una evaluación de calidad de cuatro ítems.

**3.2 Resultados de la búsqueda.** **Todo en placeholders `[n]`.** Está escrito en negrita que el artículo no puede enviarse con eso sin rellenar.

**3.3 Síntesis por temas (a)–(h).** Granularidad; construcción consciente de la estructura; enriquecimiento contextual; grafos; RAG jurídico y su evaluación; NLP jurídico multilingüe y en español; NLP de sostenibilidad; metodología de evaluación. Cada tema termina diciendo qué *no* cubre la literatura.

Tres hallazgos de la síntesis que debes conocer porque afectan a tu posicionamiento:

1. **Qu et al. (2024), "Is semantic chunking worth the computational cost?"** encuentran que la ventaja del chunking semántico sobre el fijo es inconsistente. Esto es *bueno* para ti: es exactamente la contradicción que H1.2 (distancia léxica) pretende explicar. Cítalo como motivación, no como amenaza.
2. **Louis et al. (2024), LLeQA** (AAAI 2024) hacen recuperación a nivel de *artículo* sobre legislación belga en francés. Es el precedente más cercano a tu métrica. No lo escondas: lo cito y explico la diferencia — ellos toman el artículo como unidad *dada* y no comparan estrategias de segmentación; tú comparas segmentaciones y mides si la etiqueta citada coincide con el artículo.
3. **Yepes et al. (2024)**, chunking de informes financieros por elementos de layout, es el precedente más cercano a "estructura como restricción". Es en inglés, sobre informes de empresa, y no jurídico.

**3.4 Tabla de extracción.** ~25 filas con las columnas del formulario. La última fila es "This study", y es la forma más rápida de que un revisor vea el hueco: es la única fila con "yes (constraint)", "ES", "article" y "yes" en distancia léxica.

**3.5 Hueco.** Cinco dimensiones que ningún estudio cubre a la vez.

**Qué te queda.** (1) Ejecutar las búsquedas y rellenar §3.2 y Apéndice A. (2) Verificar cada referencia contra DOI/arXiv — he escrito los identificadores que recuerdo con confianza y he puesto una nota de verificación explícita en la lista de referencias; no des por buena ninguna sin comprobarla. (3) Añadir lo que la búsqueda saque de 2025–2026 que yo no conozca.

---

## 4. Método (§4)

Aquí formalizo lo que ya está implementado en `corpus_pipeline.py` y lo nuevo de `kb_experiment.py`.

**4.1 Modelo estructural.** Una tabla con la gramática de encabezados (TÍTULO / CAPÍTULO / SECCIÓN / Artículo / numerados) y — esto lo añadí hoy al pipeline porque faltaba — las unidades de los estándares: `Contenido 306-2` (GRI) y `E1-6` (ESRS). Sin eso, los documentos GRI/ESRS nunca tendrían etiqueta de artículo y la métrica sería 0 para ellos por construcción. Define el *path de sección* σ(x) y el *artículo* α(x). Decisión: los apartados (8.3 → art.8) se ignoran; la unidad es el artículo, y la anotación a nivel de apartado queda para v2 del golden set.

**4.2 Cinco estrategias de segmentación**, definidas formalmente:
- **F** (fija): 400 palabras / 50 de solape, la de producción.
- **S-free** (semántica libre): breakpoints por percentil sobre *todo* el documento, sin respetar encabezados. Es el "chunking semántico tal como viene en LangChain".
- **S-struct**: la misma regla pero *dentro* de cada bloque delimitado por encabezados; nunca cruza un artículo. Es `corpus_pipeline.chunk_document`.
- **S-struct+ctx**: mismos trozos, pero se embebe `[título · tipo año emisor · sección] + texto`. Lo citado sigue siendo el texto literal. Aísla el efecto de la cabecera.
- **S2B** (small-to-big): hijos de 2 frases, se devuelve el padre.
- **P** (proposiciones): opcional, requiere LLM.

**4.4 Grafo esqueleto vs emergente.** El esqueleto tiene como nodos los artículos que ya existen y como aristas las referencias cruzadas detectadas por regex ("de conformidad con el artículo 8"); coste cero, determinista. El emergente une fragmentos que comparten entidades extraídas por LLM; una llamada por fragmento, no determinista. Ambos expanden a 1 salto y admiten como máximo 3 vecinos.

**4.5 citation@article.** La métrica nueva. Cuatro variantes: `CitRecall` (estricta: el artículo en el que *arranca* el fragmento — lo que el sistema imprimiría), `CitPrecision`, `CitRecall-lenient` (cuenta si el fragmento *abarca* el artículo) y `CitAll` (todo o nada, para preguntas multi-artículo). La diferencia estricta−laxa es un diagnóstico: mide cuánta recuperación de una estrategia depende de citas ambiguas.

**4.6 Presupuesto igualado.** Todas las condiciones devuelven exactamente k unidades. Con grafo: k−3 semillas + hasta 3 vecinos. Esto quita el confundido "el grafo gana porque devuelve más cosas", que está presente en varias evaluaciones de GraphRAG. Lo descubrí porque mi primera versión del código tenía justo ese fallo (anexaba los vecinos después del top-k y la evaluación no los veía).

---

## 5. Diseño experimental (§5)

**5.1 Corpus.** Los ~48 documentos actuales. Con nota sobre licencias GRI/OCDE.

**5.2 Golden set.** Se consume de la Línea 3: T1 (recuperación y cita, ~50 preguntas con artículo gold) y T9 (paráfrasis). El campo `parafrasis` es el nivel "paraphrased" del factor consulta. Aquí está el enganche explícito entre este artículo y el plan de 8 semanas con IDPEI: **sin ese golden set, este artículo no tiene sección 6**.

**5.3 Factores.** Tabla de factores y niveles: 5 × 3 × 2 × 2 = 60 condiciones × ~50 ítems ≈ 3.000 observaciones pareadas por métrica.

**5.5 Análisis estadístico y pre-registro.** Todo pareado por ítem; bootstrap percentil 95 % (2.000 remuestreos); Wilcoxon; corrección de Holm. La interacción de H1.2 es una diferencia de diferencias con su propio IC. Y una tabla que fija, para cada hipótesis, el contraste primario, la métrica y el subconjunto, más las **reglas de decisión** (apoyada / no apoyada / contradicha). Esto ya está implementado en el informe que genera el código, con la misma redacción.

**5.7 Validación del instrumento.** Menciona el smoke test sintético (2 documentos, 8 preguntas) como comprobación de que la métrica discrimina lo que debe. Dice explícitamente que esos números *no son resultados*.

---

## 6 y 7. Resultados y discusión (plantillas)

§6 lista las siete tablas que generará `kb_experiment.py` y qué va en cada una. §7 fija de antemano los marcos interpretativos: "si H1.1 se apoya, la brecha estricta/laxa dice si la ganancia es de recuperación o de cita"; "si H1.2 se apoya, la inconsistencia de la literatura tiene explicación"; "si el grafo emergente gana, apunta a un grafo híbrido"; etc. Escribir esto antes de ver datos evita ajustar la historia a posteriori.

---

## 8. Amenazas a la validez (§8)

Constructo (artículo vs apartado; gramática de encabezados), interna (los breakpoints dependen del modelo de embeddings, así que segmentación y embedding no son independientes — se trata el embedding como factor de bloqueo), externa (un corpus, un idioma, un dominio, un instituto anotador), estadística (~50 ítems, potencia limitada; ICs en vez de solo p), reproducibilidad (modelos fijados por revisión; brazos LLM ejecutados dos veces; hashes para textos no redistribuibles).

---

## El código: `src/kb_experiment.py`

Qué hace, en orden:
1. Lee el corpus una vez (texto, mapa de páginas, encabezados) con las funciones de `corpus_pipeline`.
2. Para cada modelo de embeddings × estrategia, construye los fragmentos (`build_chunks`) y un índice denso en memoria (numpy; **no toca Pinecone**). Cachea con `--cache`.
3. Para cada modo de grafo × condición de consulta × ítem, recupera k unidades con presupuesto igualado y calcula todas las métricas.
4. Escribe `*.rows.csv` (una fila por ítem y condición), `*.build.csv` (coste) y `*.report.md` (pivots + contrastes pre-registrados con IC, Wilcoxon y Holm + interacción de H1.2).

Comando completo en `benchmarks/README.md`. El smoke test tarda menos de un minuto:

```bash
python -m src.kb_experiment --corpus benchmarks/smoke/corpus --golden benchmarks/smoke/golden_smoke.jsonl \
  --embedding-models sentence-transformers/all-MiniLM-L6-v2 sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --chunking fixed semantic_free semantic_struct semantic_struct_ctx small_to_big --graph none skeleton --k 4 --out results/smoke
```

Lo que el smoke test mostró (y que es un chequeo del instrumento, no un resultado): sobre 2 documentos sintéticos, `fixed` produjo 6 fragmentos de los que 5 cruzaban artículos y obtuvo CitRecall ≈ 0,25–0,31; `semantic_struct` produjo 18–19 fragmentos con 0 cruces y CitRecall ≈ 0,88–0,94; las métricas a nivel de documento saturaron a 1,0 (solo hay 2 documentos), que es justo por lo que hace falta la métrica de artículo. El grafo esqueleto detectó 10 referencias cruzadas y metió `art.8` en el top-k de los dos ítems multi-salto que lo requerían.

---

## Qué tienes que hacer tú, en orden

1. **Golden set v1** (Línea 3, plan de 8 semanas). Sin él no hay §6. Asegúrate de que las hojas de T1 llevan la columna `articulo` y de que T9 produce `parafrasis`.
2. **Ejecutar las búsquedas de la revisión sistemática** y rellenar §3.2 y Apéndice A. Verificar todas las referencias.
3. **Reindexar el corpus real** con el pipeline (namespace de prueba, nunca producción) y correr `kb_experiment.py` con los tres modelos. Añadir `multilingual-e5-base` y `bge-m3` como extensión si hay tiempo.
4. **Pegar las tablas** del `report.md` en §6 y escribir §7 siguiendo los marcos ya fijados.
5. **Título del paper IPMU 2026** en la referencia `[Own]`.
6. Decidir destino: si los resultados de H1.2 son limpios, ECIR/SIGIR short (es el hallazgo más general); si lo que brilla es la cita y el grafo, ICAIL/JURIX.
