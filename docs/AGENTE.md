# El agente asesor: habilidades, controles y harness

Cómo está organizado el asesor de RecavAI desde el 2026-09-23. Sustituye al prompt
monolítico `EXPERT_SYSTEM_PROMPT` y sigue el patrón habitual de las plataformas agénticas:
instrucciones modulares (*skills*), controles deterministas (*guardrails*), un único bucle
de ejecución (*harness*) y un banco de evaluación que usa ese mismo bucle.

```
                        ┌──────────────────────── harness.run_turn ────────────────────────┐
 pregunta ─► recuperar ─► nivel de contexto ─► controles de entrada ─► controles de contexto
            (30 candidatos)  (S / M / L: unidades     (norma o artículo      (nada recuperado)
                              completas, en orden)     que no existe)              │
                                  └────── AVISOS DEL SISTEMA en el mensaje ────────┘
                                                                                   ▼
                        componer habilidades ─► generar ─► ¿dice que algo no consta?
                        (según tarea y pregunta)  (T = 0,2)    └─ sí: segunda pasada, nivel siguiente
                                                                                   ▼
          registrar ◄─ anotar lo que siga ◄─ reparar UNA vez ◄─ controles de salida
         (agent_turn)   sin respaldo          (si hay violaciones)  (datos críticos, citas [n],
                                                                     cita en el fragmento correcto)
                        └──────────────────────────────────────────────────────────────────┘
```

Desde el 24/09/2026 el contexto es **adaptativo** ([PLAN_CONTEXTO.md](PLAN_CONTEXTO.md),
[resultados](../benchmarks/RESULTADOS_CONTEXTO.md)): cada fragmento recuperado se amplía a
su unidad completa (artículo, requisito NEIS, contenido GRI) y cada pregunta recibe el
presupuesto de su nivel. Ver [Contexto adaptativo](#contexto-adaptativo--srccontext_builderpy).

## Por qué

La evaluación del 23/09 mostró que 40 de 60 respuestas tenían alguna afirmación que no
respaldaban los fragmentos, y 8 contradecían un dato clave (p. ej. los umbrales de la CSRD
anteriores al Ómnibus). El prompt lo autorizaba: «si usas información de tu conocimiento
general, no cites», los documentos solo servían para «enriquecer» y una estructura
obligatoria de 5 secciones que el modelo rellenaba de memoria.

## Qué consiguió

Medido el 23/09/2026 ([benchmarks/RESULTADOS_FIDELIDAD.md](../benchmarks/RESULTADOS_FIDELIDAD.md)):

| | Prompt monolítico | Agente |
|---|---:|---:|
| Respuestas sin afirmaciones no respaldadas (juez) | 33 % | **90 %** |
| Datos críticos sin respaldo por respuesta (batería / preguntas reales) | 0,40 / 0,80 | **0 / 0** |
| Contradicen un dato clave (de 60) | 8 | **3** |
| Trampas detectadas (artículo inexistente, término inventado, ley fuera del corpus) | 2/3 | **3/3** |
| Juez a ciegas viendo los fragmentos (batería) | 6 | **52** (2 empates) |
| Juez a ciegas viendo los fragmentos (40 preguntas reales no usadas para diseñar) | 4 | **36** |
| Latencia p50 / p95 | 12,0 / 21,0 s | **7,7 / 15,1 s** |

La capa de reparación apenas actúa (1 vez en 40 preguntas reales): el contrato de las
habilidades basta casi siempre. Queda como red de seguridad.

**Limitaciones conocidas:** errores sustantivos sin cifra ni artículo (p. ej. afirmar que
habrá NEIS sectoriales obligatorias) no los detecta ningún control; los encargos de
redacción (cláusulas, guías paso a paso) se resuelven resumiendo la norma en vez de
redactar; a veces se abstiene aunque el fragmento tenía la respuesta (1 caso en 60).

## Habilidades — `src/agent/skills/*.md`

Cada fichero es una habilidad con cabecera:

```yaml
---
name: csrd_neis
description: Formulaciones correctas para CSRD y NEIS
version: 1
tasks: [asesor, herramienta, verificacion]
triggers: [csrd, neis, esrs, doble materialidad, …]   # opcional
order: 50
---
```

| Orden | Habilidad | Versión | Tareas | Qué fija |
|---:|---|---:|---|---|
| 0 | identidad | 2 | todas | Rol, ámbito, qué puede hacer el usuario (preguntar, subir PDF, modo auditor); responder con naturalidad a saludos |
| 5 | planificador | 1 | planificacion | Reformular la pregunta en el vocabulario de la norma y clasificarla. **Desactivado** (`RAG_PLANNER=0`): no pasó la regla de adopción |
| 10 | **fundamentacion** | 3 | todas | El contrato: todo dato normativo con cita [n] o «no consta»; los fragmentos consolidados prevalecen sobre la memoria; «completo sí, relleno no»; con artículos enteros delante, responder a lo preguntado, citar el [n] que contiene el dato y dar las listas enteras |
| 20 | citas | 2 | todas | Nombrar la unidad en la frase («artículo 3 de la CSDDD [1]»), tomada de la cabecera del fragmento |
| 30 | abstencion | 2 | todas | Obedecer los AVISOS DEL SISTEMA, revisar índices y listas antes de decir que algo no consta, corregir premisas falsas |
| 40 | jerarquia_normativa | 1 | todas | Niveles 1-3 y precedencia; no mezclar CSRD y CSDDD |
| 50 | csrd_neis | 1 | todas, con disparadores | Formulaciones correctas de CSRD y NEIS |
| 51 | csddd | 1 | todas, con disparadores | Formulaciones correctas de la CSDDD tras el Ómnibus |
| 52 | estandares | 1 | todas, con disparadores | OCDE y GRI como Nivel 3; no enumerar de memoria |
| 60 | formato_asesor | 3 | asesor | Completa y ordenada: respuesta directa, fundamento con encabezados, listas enteras, resumen final; hablar de la norma, no de «la base documental» |
| 61 | orientacion_practica | 1 | asesor | Consejo práctico solo en sección rotulada, sin datos normativos |
| 70 | herramienta_auditor | 1 | herramienta | Conciso, sin orientación práctica |
| 71 | verificacion | 1 | verificacion | Formato VEREDICTO/BRECHA/RECOMENDACIÓN/BASE, «no evaluable» si no hay base |

**Composición** (`skills.compose(tarea, pregunta)`): siempre las habilidades sin
disparadores de la tarea; de las que tienen disparadores, solo las que la pregunta activa
(si no activa ninguna, todas). Es la *divulgación progresiva*: una pregunta sobre la guía
OCDE de minerales no carga las reglas de la CSRD.

**Versión.** Cada composición tiene un hash de 10 caracteres de las habilidades elegidas y
su contenido. Se registra con cada turno: se puede saber con qué instrucciones exactas se
generó cualquier respuesta. Cambiar una habilidad cambia la versión.

**Para modificar una habilidad:** editar el `.md`, subir `version`, ejecutar
`python scripts/test_agent.py` y la batería de evaluación antes de desplegar.

## Controles — `src/agent/guardrails.py`

Todos deterministas (expresiones regulares y comparación de valores normalizados): cuestan
milisegundos, se reproducen exactamente y cada hallazgo dice qué dato falló.

| Punto | Control | Qué hace |
|---|---|---|
| entrada | `ReferenciaDesconocida` | Si la pregunta nombra una norma que no está en `data/normas_corpus.json` («Ley 11/2018») o un artículo que la norma no tiene según el grafo («art. 52 de la CSDDD»), añade un aviso al mensaje |
| contexto | `ContextoVacio` | Si no ha entrado ningún fragmento, avisa de que debe decirlo |
| salida | `DatosCriticos` | Cifras, fechas, años, artículos, números de norma y códigos (E1-6, 305-1) de la respuesta que no aparecen en los fragmentos ni en la pregunta. Compara valores: «1.500 millones de euros» = «1 500 000 000 EUR» |
| salida | `CitasInexistentes` | Citas [n] a fragmentos que el modelo no recibió |
| salida | `CitaSinRespaldo` | Un dato crítico de un tramo que termina en [n] está en la base documental, pero en otro fragmento. Se repara (se pide citar el correcto); si persiste, se registra pero **no se anota** al usuario: el dato sí consta |

Con el contexto adaptativo hay más texto donde encontrar una cifra por casualidad;
`CitaSinRespaldo` compensa exigiendo que el dato esté en el fragmento que se cita. En las
60 respuestas de H4 habría saltado en 2 (las dos, imprecisiones reales).

**Qué no detectan:** obligaciones inventadas sin cifra ni artículo («la empresa debe
documentar…»). Cubrirlo exigiría verificar frase a frase con un segundo modelo (capa 4,
no implantada).

**Si un control falla** (excepción), se registra como ERROR y el turno sigue (ARC-6).

## Harness — `src/agent/harness.py`

`run_turn(…, task=)` es el único punto por el que pasa el asesor:

| Tarea | Quién la usa | Nivel de contexto |
|---|---|---|
| `asesor` | `/chat_assistant` | S, M o L según la pregunta |
| `herramienta` | el auditor, con `invoke_sustainability_expert` | S o M (una auditoría hace ~70 llamadas) |
| `verificacion` | el auditor, al contrastar cada respuesta de la empresa (`_verify_compliance`) | S |

**Política de reparación.** Si los controles de salida encuentran violaciones, se pide
**una** reescritura que nombra exactamente los datos que no constan. Se sirve la versión
con menos violaciones. Si aún queda alguna, se añade al final una nota visible:

> *Verificación automática: no he podido localizar en la base documental «…». Compruébalo
> en el texto oficial antes de usarlo.*

Nunca se bloquea la respuesta: un asesor que no contesta tampoco sirve, pero uno que
contesta con un dato inventado sin avisar es peligroso.

**Política de generación.** `temperature=0.2`, `max_output_tokens=8192`
(`GenerationPolicy`). Razonamiento interno de Gemini: `RAG_THINKING_BUDGET` (vacío =
dinámico, el modelo decide; `0` = sin razonamiento). Medido con
`scripts/probe_latency.py` el 24/09/2026:

| Contexto (tokens) | Dinámico | Sin razonamiento |
|---:|---:|---:|
| 2.000 | 3,8 s | 3,5 s |
| 12.000 | 10,4 s | 4,4 s |
| 40.000 | 23,0 s | 5,9 s |

Con razonamiento dinámico, más contexto multiplica la latencia (el modelo piensa más);
sin él, cada 10.000 tokens añaden alrededor de 1 s.

**Segunda pasada (Self-Route).** Si el borrador dice que algo no consta en la base
documental (`guardrails.abstentions`), el primer intento tardó menos de 8 s y no hay aviso
de premisa falsa, se recupera otra vez (50 candidatos) y se genera **una** vez más con el
nivel siguiente. Se sirve la que se abstiene menos. Llamadas al modelo por turno, como
mucho: borrador + segunda pasada + reparación (+ planificador si estuviera activado).

**Fuentes.** Con el contexto adaptativo, la interfaz recibe solo las fuentes **citadas**
en la respuesta, con su número; con 10-15 bloques la lista completa dejaba de ser útil.

**Registro.** Una línea JSON por turno:

```json
{"evt": "agent_turn", "task": "asesor", "skills_version": "f4faec66b2",
 "skills": ["identidad", "fundamentacion", …], "avisos": ["referencia_desconocida"],
 "violaciones_borrador": 2, "violaciones_final": 0, "reparada": true, "anotada": false,
 "nivel": "M", "tokens_contexto": 11240, "bloques": 14, "planificador": null,
 "segunda_pasada": false, "tokens": {"entrada": 15100, "salida": 910, "razonamiento": 0},
 "ms": {"recuperacion": 480, "generacion": 4800, "segunda_pasada": 0, "reparacion": 0,
        "planificador": 0, "total": 5290}}
```

Resumen de las últimas 24 h: `./scripts/logs.sh agente` (turnos, avisos, reparados,
anotados, niveles, segunda pasada y tokens de contexto). Consulta directa en Cloud Logging: `jsonPayload.evt="agent_turn"` o, con texto plano,
`textPayload:"agent_turn"`. Proporción de turnos reparados y anotados: el indicador de
salud de la fidelidad en producción.

## Evaluación — el mismo harness

`scripts/eval_battery.py` llama a `chat_with_expert(..., return_result=True)`, que pasa por
`run_turn`: lo que se mide es lo que se sirve. Además del juez, registra por pregunta el
borrador, la respuesta final, los avisos y la métrica determinista `criticos` (datos
críticos sin respaldo). `split-draft` convierte los borradores en una ejecución propia para
medir por separado el efecto de las habilidades (capas 1-2) y el de la reparación (capa 3).

Pruebas sin red: `python scripts/test_agent.py` (91 comprobaciones: habilidades, controles,
harness, contexto adaptativo y planificador, con un modelo simulado) y
`python scripts/test_auditor.py`.

## Contexto adaptativo — `src/context_builder.py`

Recuperar para localizar, leer entera la unidad localizada y ampliar solo cuando haga
falta ([PLAN_CONTEXTO.md](PLAN_CONTEXTO.md)).

1. **Candidatos.** La búsqueda pide 30 fragmentos (antes 12, con tope de 6); la referencia
   explícita y el grafo añaden los suyos.
2. **Unidades.** Cada candidato se amplía a su unidad completa desde el almacén local
   `data/unidades.json.gz` (sin otra consulta a Pinecone). Si la unidad supera el tope del
   nivel, o el fragmento no tiene unidad, entran él y sus vecinos de la misma unidad.
   Al unir fragmentos consecutivos se quita la frase de solape de `chunking_v2`.
3. **Presupuesto.** Se añaden unidades por orden de prioridad hasta el presupuesto del nivel:

   | Nivel | Tokens | Unidad entera si ≤ | Vecinos | Cuándo |
   |---|---:|---:|---:|---|
   | S | 6.000 | 6.000 | ±1 | la pregunta nombra la unidad; verificación del auditor |
   | M | 12.000 | 4.000 | ±1 | por defecto |
   | L | 45.000 | 17.000 | ±3 | enumeraciones y panoramas; segunda pasada |

4. **Orden de lectura.** Documentos por su mejor fragmento y, dentro, el orden del texto
   (OP-RAG). Cada bloque es una unidad o un tramo contiguo con su cabecera y su [n].

| Variable | Valor en producción | Efecto |
|---|---|---|
| `RAG_UNIT_STORE` | `/app/data/unidades.json.gz` | Vacío: un bloque por fragmento (comportamiento anterior) |
| `RAG_CONTEXT_MAX_LEVEL` | ver [DESPLIEGUE.md](DESPLIEGUE.md) | `M` quita el nivel L y la segunda pasada |
| `RAG_THINKING_BUDGET` | `0` | Vacío: razonamiento dinámico |
| `RAG_PLANNER` | `0` | `1` activa el planificador |

**Planificador — `src/agent/planner.py`.** Una llamada a `gemini-2.5-flash-lite` sin
razonamiento (p50 0,9 s), en paralelo con la búsqueda, que reformula la pregunta y la
clasifica; sus búsquedas se fusionan por rango recíproco. Lo que produce solo sirve para
buscar. **No está activado:** subió 34 puntos los hechos clave en contexto de las preguntas
coloquiales, pero bajó los de definiciones y cambios, y ninguna de las dos variantes
probadas cumplió la regla prerregistrada ([resultados](../benchmarks/RESULTADOS_CONTEXTO.md)).

## Ficheros de datos en ejecución

| Fichero | Lo genera | Lo usa |
|---|---|---|
| `data/normative_graph.json` | `scripts/build_normative_graph.py` | grafo normativo, control de artículos inexistentes |
| `data/normas_corpus.json` | `scripts/build_norm_registry.py` | controles de entrada y salida (números de norma) |
| `data/unidades.json.gz` | `scripts/build_unit_store.py` | contexto adaptativo: texto de cada fragmento, unidades y orden de cada documento |

Los tres se copian en la imagen (`Dockerfile`). Las habilidades son Markdown: `.dockerignore`
y `.gcloudignore` excluyen `*.md` salvo `src/agent/skills/*.md`.
