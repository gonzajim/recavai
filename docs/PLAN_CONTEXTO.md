# Plan: contexto adaptativo

> **Estado (2026-09-24): ejecutado el mismo día.** Resultados en
> [RESULTADOS_CONTEXTO.md](../benchmarks/RESULTADOS_CONTEXTO.md). En resumen:
> - Fase 0: el razonamiento dinámico era lo que disparaba la latencia; se quita
>   (`RAG_THINKING_BUDGET=0`).
> - Fases 1, 3 y 4 **adoptadas** (configuración H7): cobertura 63 → 81 % (IC 95 % +11 a
>   +26), fidelidad 90 → 95 %, datos críticos sin respaldo 0, latencia p50/p95 7,7/15,1 →
>   5,7/11,6 s; a ciegas, 37–17 en la batería y 29–8 en 40 preguntas reales.
> - Fase 2 (planificador) **no adoptada**: ninguna de las dos variantes cumplió la regla;
>   el código queda desactivado (`RAG_PLANNER=0`).
> - Desviaciones del plan:
>   - 30 candidatos, no 20.
>   - Los fragmentos sin unidad se amplían con sus vecinos de la misma unidad, no por
>     `section`: la mitad de las secciones son títulos de menos de 150 tokens y alguna
>     pasa de 100.000.
>   - Almacén comprimido: `data/unidades.json.gz`, 2,2 MB.
>   - Nivel S de 6.000 tokens, no 4.000; el nivel L acepta unidades de hasta 17.000 tokens.
>   - La segunda pasada pide 50 candidatos.
>   - El juez ve cada fragmento entero (cambio de instrumento).
>   - Se añadió `clean_answer` tras ver una etiqueta interna en una respuesta.
>
> Lo que sigue es el plan tal como se escribió.

Hoy el asesor responde con el mismo contexto a cualquier pregunta: los 6 mejores
fragmentos, más hasta 3 por referencia explícita y 3 por el grafo. Ocupan unos 1.800
tokens de una ventana de 1.048.576, y el presupuesto de `format_context` (180.000
caracteres) se usa al 4 %. Este plan propone **dar a cada pregunta el contexto que
necesita**. Tiene tres reglas: recuperar para localizar, leer entera la unidad localizada
y ampliar solo cuando haga falta. Se mantienen los controles de alucinación y la latencia.

---

## 1. Diagnóstico, con datos de hoy

### 1.1 La mitad de lo que hace falta no llega al modelo

En H4 (la versión desplegada) la cobertura media es del 63 %: **33 de 60 respuestas están
incompletas**. Los fallos se reparten a partes iguales:

| Causa | Preguntas | Tipos donde se concentra |
|---|---:|---|
| El pasaje con la respuesta no se recupera | 16 | coloquial (4 de 6), ámbito (3 de 5), real (3 de 4), cambios, multiartículo |
| Se recupera, pero la respuesta queda incompleta | 17 | artículo largo (art. 10 y 27 de la CSDDD), listas (los cinco pasos de la OCDE, temas de GRI 14), contenidos GRI, sectoriales |

El segundo grupo no es un fallo del modelo. El fragmento trae **parte** del artículo o de
la lista, y la habilidad de fundamentación prohíbe completar de memoria, que es lo que
queremos.

### 1.2 Curva de contexto: cuánto hace falta

Es una simulación sobre el índice v2 en memoria, sin grafo, con las 57 preguntas de la
batería que tienen hechos clave. Un hecho cuenta como «en contexto» si al menos el 60 % de
sus palabras aparece en algún fragmento, el mismo criterio que `pasaje@6`. No usa Gemini.

| Contexto | Pasaje | Hechos clave en contexto | Preguntas con todos | Tokens (mediana / p95) |
|---|---:|---:|---:|---:|
| **6 mejores (hoy)** | 65 % | **48 %** | **35 %** | 1.468 / 1.838 |
| 12 mejores | 79 % | 65 % | 51 % | 2.937 / 3.565 |
| 20 mejores | 81 % | 69 % | 58 % | 5.057 / 5.818 |
| 50 mejores | 93 % | 79 % | 65 % | 12.125 / 14.170 |
| 6 mejores + unidad completa | 75 % | 65 % | 58 % | 5.367 / 10.006 |
| **12 mejores + unidad completa** | 86 % | **77 %** | **68 %** | 9.557 / 18.520 |
| 20 mejores + unidad completa | 88 % | 81 % | 74 % | 14.258 / 27.608 |

- **Ampliar a la unidad completa rinde más que pedir más fragmentos.** Con 12 fragmentos
  ampliados se obtiene casi lo mismo que con 50 sueltos.
- **El umbral de similitud (0,841) no corta casi nada por encima de los 6 primeros.** Es
  el tope de 6 lo que limita.
- **Hay tipos que más contexto no arregla.** Hechos en contexto con 12 fragmentos + unidad:
  - coloquial: 49 %
  - real: 52 %
  - multiartículo: 65 %
  - cambios: 67 %

  El usuario pregunta con palabras que no están en la norma («cortar con un proveedor»
  frente a «suspensión o terminación de la relación comercial»). Eso se arregla
  **reformulando la búsqueda**, no ampliando.

### 1.3 Unidades

Hay 480 unidades normativas (artículo, anexo, requisito NEIS, contenido y tema GRI)
etiquetadas en `unit_label`, que cubren el 36 % de los fragmentos. Tamaño en tokens:

| Mediana | p90 | p99 | Máximo |
|---:|---:|---:|---:|
| 1.150 | 3.475 | 10.225 | 14.816 |

El resto (guías OCDE, marco teórico) no tiene unidad, pero sí `section` y orden dentro del
documento (el sufijo `NNNN` del id).

### 1.4 Latencia

Tiempos de H4:

| | p50 | p95 |
|---|---:|---:|
| Total | 7,6 s | 15,1 s |
| Búsqueda | 0,14 s | |
| Recuperación completa | 1,4 s | |
| Generación | 7,0 s | 14,8 s |

La respuesta mediana tiene unos 700 tokens, que en Flash son 3–4 s. El resto
probablemente es **razonamiento interno**: `GenerationPolicy` no fija `thinking_budget`,
así que Gemini 2.5 Flash decide cuánto piensa. Es la palanca que puede pagar el contexto
adicional. No está medido todavía (fase 0).

---

## 2. Objetivos y regla de adopción (prerregistrados)

Se evalúan sobre la batería de 60 preguntas y las 40 reales reservadas, con el mismo juez
y la misma higiene que en [RESULTADOS_FIDELIDAD.md](../benchmarks/RESULTADOS_FIDELIDAD.md):
juez `gemini-3.5-flash` y comparación por parejas con fragmentos.

| Métrica | H4 (hoy) | Objetivo | Tipo |
|---|---:|---:|---|
| Hechos clave en contexto (`hechos_ctx`, nueva, determinista) | 48 % (sin grafo) | ≥ 75 % | precisión |
| Cobertura (juez) | 63 % | ≥ 71 % (+8; ruido ±6) | precisión |
| Datos críticos sin respaldo por respuesta | 0 | 0 | alucinación, **bloqueante** |
| Contradicen un dato clave | 3 | ≤ 3 | alucinación, **bloqueante** |
| Trampas detectadas | 3/3 | 3/3 | alucinación, **bloqueante** |
| Fidelidad (juez) | 90 % | ≥ 85 % (ruido ±13) | alucinación |
| Nombra la unidad citada (`cita_unidad`) | 70 % | ≥ 66 % | precisión |
| Por parejas con fragmentos, frente a H4 (batería y reales) | — | gana o empata | global |
| Latencia p50 / p95 | 7,6 / 15,1 s | ≤ 8,5 / ≤ 16 s | latencia, **bloqueante** |
| Coste por pregunta | ~0,003 $ | ≤ 0,008 $ | coste |

**Regla:** se adopta la configuración que cumple todas las bloqueantes y mejora la
cobertura en ≥ 8 puntos. Si ninguna la cumple, se queda H4. Cada fase lleva su variable
de entorno para desactivarla sin volver a desplegar la imagen.

---

## 3. Fases

### Fase 0 · Medir qué cuesta el contexto en latencia — ~0,15 $

- Mismas 4 preguntas con contexto de 2k, 10k, 20k y 40k tokens.
- Para cada tamaño, `thinking_budget` dinámico, 1.024 y 0; 2 repeticiones.
- Se miden el tiempo total y los tokens de pensamiento (`usage_metadata`).
- Además, el harness registra en `agent_turn` los tokens de entrada, salida y
  pensamiento, y el nivel de contexto.

**Sale:** la curva de latencia frente a tamaño y pensamiento, que fija los presupuestos
de las fases 1 y 3.

### Fase 1 · Leer la unidad entera, en orden — determinista, sin llamadas

Es el cambio con más efecto medido (48 % → 77 % de hechos en contexto) y no añade
llamadas al modelo.

1. **Almacén local de unidades**: `data/unidades.json`, generado por
   `scripts/build_unit_store.py` desde el índice v2.
   - Guarda por unidad los ids en orden, el texto, las páginas y la etiqueta.
   - Los documentos sin unidades se agrupan por `section`.
   - Ocupa unos 10 MB, va en la imagen como el grafo y evita otra ida a Pinecone.
2. **Selección por unidades**:
   - Se piden 20 candidatos y se agrupan por unidad, conservando el orden de la mejor
     puntuación.
   - Cada unidad entra entera si ocupa ≤ 4.000 tokens (p90). Si es mayor, entran los
     fragmentos recuperados y sus vecinos ±1.
   - Se añaden unidades hasta el presupuesto del nivel **M, 12.000 tokens**, y se aplican
     las mismas reglas al grafo y a las referencias explícitas.
3. **Orden de lectura**:
   - Se agrupa por documento y, dentro de cada documento, se sigue el orden del texto
     (OP-RAG, Yu et al., 2024).
   - Cada unidad es un bloque `[n]` con su cabecera (unidad y páginas). Así las citas
     apuntan a unidades, lo que favorece `cita_unidad`.
4. **Fuentes en la interfaz**: se devuelven solo las citadas en la respuesta, con su
   numeración. Con 10–15 bloques, la lista completa deja de ser útil.
5. **Nueva métrica `hechos_ctx`** en `eval_battery.py`: la de §1.2, determinista y gratis,
   para saber sin juez si el contexto tenía la respuesta.

**Mitigación:** con 10–20k tokens el control `DatosCriticos` sigue siendo útil, porque
compara contra un contexto acotado y no contra 350.000 tokens. Se refuerza en la fase 4.

**Vuelta atrás:** `RAG_CONTEXT_MODE=fragmentos`, el comportamiento actual.

### Fase 2 · Planificador de búsqueda — una llamada corta al modelo

Sirve para las preguntas que la fase 1 no arregla: coloquiales, reales, de cambios y
multiartículo.

1. **Habilidad `planificacion`** (`src/agent/skills/05_planificador.md`, tarea
   `planificacion`, versionada como las demás). Una llamada a `gemini-2.5-flash-lite` sin
   pensamiento devuelve un JSON:
   ```json
   {"tipo": "puntual | enumeracion | multiunidad | cambios | panorama",
    "normas": ["CSDDD"], "unidades": ["art. 10", "art. 11"],
    "busquedas": ["suspensión o terminación de la relación comercial con un socio", "..."],
    "nivel": "S | M | L"}
   ```
2. **Búsqueda múltiple**: la pregunta original y 2–3 reformulaciones en vocabulario
   normativo, fusionadas por rango (RRF). Las preguntas multiartículo se descomponen en
   una búsqueda por unidad.
3. **Latencia**: la búsqueda con la pregunta original arranca en paralelo con el
   planificador. El planificador se omite si la pregunta ya nombra una unidad («artículo 9
   de la CSDDD») y la resuelve el grafo.
4. **Mitigación**:
   - Lo que produce el planificador solo sirve para **buscar**; nunca llega al modelo
     como dato.
   - Las normas que nombre se validan contra `data/normas_corpus.json`, como en
     `ReferenciaDesconocida`, y una norma inventada se descarta.
   - Si el planificador falla o tarda más de 2 s, se sigue sin él (ARC-6).

**Antes de la batería completa** se mide solo la recuperación: 60 llamadas al
planificador (~0,02 $) y `hechos_ctx` por tipo. Si los tipos coloquial y real no suben
al menos 15 puntos, la fase 2 no se despliega.

**Vuelta atrás:** `RAG_PLANNER=0`.

### Fase 3 · Presupuesto por nivel y segunda pasada

| Nivel | Cuándo | Contexto | Pensamiento* |
|---|---|---|---|
| **S** | Unidad explícita, definición, tarea `verificacion` del auditor | ~4.000 tokens | bajo |
| **M** | Por defecto | ~12.000 | medio |
| **L** | Enumeraciones («qué temas recoge GRI 14», «los cinco pasos») y panorama de una norma que cabe (CSDDD 36k, CSRD 41k) | sección o norma completa, ordenada por artículos, hasta ~45.000 | el que salga de la fase 0 |

\* Los valores concretos salen de la fase 0.

- Las NEIS (259.000 tokens), GRI y OCDE nunca entran enteras: en nivel L entra la
  **sección**, no el documento.
- El auditor usa S y M: una auditoría hace unas 70 llamadas y su latencia se suma.
- Los **PDF del usuario** siguen el mismo criterio. Si pregunta por su documento y cabe
  en el nivel, entra entero.

**Segunda pasada (Self-Route, Li et al., 2024).** Si el primer intento se abstiene en
parte («no consta en los fragmentos»), o saltó `ContextoVacio`, se recupera en el nivel
siguiente y se genera **una** vez más. Solo se hace si el primer intento tardó menos de
8 s, para no romper el p95. Se registra (`segunda_pasada: true`) para vigilar su
frecuencia; se espera entre el 10 y el 15 % de los turnos.

**Vuelta atrás:** `RAG_CONTEXT_MAX_LEVEL=M` desactiva L y la segunda pasada.

### Fase 4 · Controles para un contexto más grande

1. **`DatosCriticos` con cita**. Hoy basta con que la cifra esté en *algún* fragmento.
   Con más contexto se exigirá que esté en el fragmento **citado en la misma frase**:
   - Si está en otro fragmento, es una cita equivocada: se repara, pidiendo que cite bien.
   - Si no está en ningún fragmento, se trata como hasta ahora.

   Compensa que haya más texto donde encontrar cifras por casualidad.
2. **`fundamentacion` v3**: con muchas unidades delante, responder a lo preguntado y no
   volcar todo lo que hay en el contexto («completo sí, relleno no»). Más contexto tiende
   a alargar las respuestas y eso sube la latencia y las ocasiones de error.
3. **Pruebas**: `scripts/test_agent.py` con casos de cita equivocada, unidad partida,
   nivel L y segunda pasada.
4. **Registro**: `logs.sh agente` muestra el nivel, los tokens de contexto, el tiempo del
   planificador y el porcentaje de turnos con segunda pasada.

### Fase 5 · Evaluación y despliegue

| Ejecución | Qué incluye |
|---|---|
| H5 | Fase 1 + 4 |
| H6 | H5 + fase 2 |
| H7 | H6 + fase 3 |

- Cada una se compara con H4 en la batería y H7 también en las 40 preguntas reales
  reservadas, por parejas y con fragmentos.
- Despliegue como la última vez: versión de prueba, comprobaciones, promote y
  `rollback` disponible.

---

## 4. Presupuesto de latencia

Las cifras marcadas con * son estimaciones hasta la fase 0.

| Paso | Hoy | Con el plan |
|---|---:|---|
| Recuperación (embedding, Pinecone, grafo) | 1,4 s | 1,4 s; la unidad sale del almacén local (~0 s) |
| Planificador | — | +0,5–1,0 s*, en parte en paralelo; se omite con referencia explícita |
| Generación | 7,0 s | +0,3–1,0 s* por 10k tokens más de entrada, compensables fijando el pensamiento |
| Segunda pasada | — | solo en el 10–15 % de los turnos y solo si el primer intento tardó menos de 8 s |

## 5. Coste

| | Por pregunta |
|---|---:|
| Hoy (~5.000 tokens de entrada) | ~0,003 $ |
| Nivel M + planificador (~15.000) | ~0,006 $ |
| Nivel L (~50.000) | ~0,017 $ |

**Evaluación del plan completo:**
- Unas 750 llamadas y 12 M de tokens: **~5–8 $**, más la fase 0 (~0,15 $).
- El crédito es el mismo que usa producción: **comprobar el saldo antes** y lanzar por
  tandas (ver [DESPLIEGUE.md §5.1](DESPLIEGUE.md)).

Precios de la lista pública de Gemini 2.5 Flash de 2025. Hay que comprobarlos, aunque no
cambian el orden de magnitud.

## 6. Riesgos

| Riesgo | Mitigación |
|---|---|
| Más contexto, más ruido: el modelo mezcla unidades cercanas | Orden del documento, cabecera por unidad, `DatosCriticos` con cita |
| Respuestas más largas y lentas | `fundamentacion` v3, pensamiento fijado, objetivo de p95 bloqueante |
| El planificador inventa una norma | Solo busca; se valida contra el registro; se descarta si no existe |
| La segunda pasada dispara el p95 | Solo si el primer intento tardó < 8 s; porcentaje registrado |
| «Perdido en medio» en el nivel L | L limitado a ~45k tokens y ordenado; la unidad más relevante va primero y se repite su etiqueta en la pregunta |
| Mejora dentro del ruido del juez | La regla exige +8 puntos y lleva la métrica determinista `hechos_ctx` al lado |

## 7. Fuera de este plan

- **Reordenación con un modelo de *reranking*** (alojado en Pinecone o un *cross-encoder*
  local). Solo si, tras las fases 1 y 2, el pasaje se recupera pero queda por debajo del
  corte. En CPU con 2 vCPU añadiría segundos.
- **Búsqueda léxica híbrida (BM25)**. Los códigos exactos (E1-6, 305-1, «artículo 10») ya
  los resuelve el grafo, y el fallo que queda es de vocabulario, que la búsqueda léxica no
  arregla.
- **Inyectar el núcleo normativo entero** (353.000 tokens): descartado por coste,
  latencia y verificabilidad. El análisis está en la conversación del 24/09/2026; el
  nivel L recoge lo aprovechable, la norma completa cuando cabe.
