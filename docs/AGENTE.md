# El agente asesor: habilidades, controles y harness

Cómo está organizado el asesor de RecavAI desde el 2026-09-23. Sustituye al prompt
monolítico `EXPERT_SYSTEM_PROMPT` y sigue el patrón habitual de las plataformas agénticas:
instrucciones modulares (*skills*), controles deterministas (*guardrails*), un único bucle
de ejecución (*harness*) y un banco de evaluación que usa ese mismo bucle.

```
                        ┌──────────────────────── harness.run_turn ────────────────────────┐
 pregunta ─► recuperar ─► controles de entrada ─► controles de contexto ─► componer habilidades
                           (norma o artículo      (nada recuperado)          (según la tarea y
                            que no existe)              │                     la pregunta)
                                  └────── AVISOS DEL SISTEMA en el mensaje ───────┘      │
                                                                                         ▼
          registrar ◄─ anotar lo que siga ◄─ reparar UNA vez ◄─ controles de salida ◄─ generar
         (agent_turn)   sin respaldo          (si hay violaciones)  (datos críticos,     (T = 0,2)
                                                                      citas [n])
                        └──────────────────────────────────────────────────────────────────┘
```

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
| 10 | **fundamentacion** | 2 | todas | El contrato: todo dato normativo con cita [n] o «no consta»; los fragmentos consolidados prevalecen sobre la memoria; «completo sí, relleno no» |
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

**Qué no detectan:** obligaciones inventadas sin cifra ni artículo («la empresa debe
documentar…»). Cubrirlo exigiría verificar frase a frase con un segundo modelo (capa 4,
no implantada).

**Si un control falla** (excepción), se registra como ERROR y el turno sigue (ARC-6).

## Harness — `src/agent/harness.py`

`run_turn(…, task=)` es el único punto por el que pasa el asesor:

| Tarea | Quién la usa |
|---|---|
| `asesor` | `/chat_assistant` |
| `herramienta` | el auditor, con `invoke_sustainability_expert` |
| `verificacion` | el auditor, al contrastar cada respuesta de la empresa (`_verify_compliance`) |

**Política de reparación.** Si los controles de salida encuentran violaciones, se pide
**una** reescritura que nombra exactamente los datos que no constan. Se sirve la versión
con menos violaciones. Si aún queda alguna, se añade al final una nota visible:

> *Verificación automática: no he podido localizar en la base documental «…». Compruébalo
> en el texto oficial antes de usarlo.*

Nunca se bloquea la respuesta: un asesor que no contesta tampoco sirve, pero uno que
contesta con un dato inventado sin avisar es peligroso.

**Política de generación.** `temperature=0.2`, `max_output_tokens=8192`
(`GenerationPolicy`).

**Registro.** Una línea JSON por turno:

```json
{"evt": "agent_turn", "task": "asesor", "skills_version": "f4faec66b2",
 "skills": ["identidad", "fundamentacion", …], "avisos": ["referencia_desconocida"],
 "violaciones_borrador": 2, "violaciones_final": 0, "reparada": true, "anotada": false,
 "ms": {"recuperacion": 480, "generacion": 9800, "reparacion": 8700, "total": 18990}}
```

Resumen de las últimas 24 h: `./scripts/logs.sh agente` (turnos, avisos, reparados, anotados). Consulta directa en Cloud Logging: `jsonPayload.evt="agent_turn"` o, con texto plano,
`textPayload:"agent_turn"`. Proporción de turnos reparados y anotados: el indicador de
salud de la fidelidad en producción.

## Evaluación — el mismo harness

`scripts/eval_battery.py` llama a `chat_with_expert(..., return_result=True)`, que pasa por
`run_turn`: lo que se mide es lo que se sirve. Además del juez, registra por pregunta el
borrador, la respuesta final, los avisos y la métrica determinista `criticos` (datos
críticos sin respaldo). `split-draft` convierte los borradores en una ejecución propia para
medir por separado el efecto de las habilidades (capas 1-2) y el de la reparación (capa 3).

Pruebas sin red: `python scripts/test_agent.py` (habilidades, controles y harness con un
modelo simulado) y `python scripts/test_auditor.py`.

## Ficheros de datos en ejecución

| Fichero | Lo genera | Lo usa |
|---|---|---|
| `data/normative_graph.json` | `scripts/build_normative_graph.py` | grafo normativo, control de artículos inexistentes |
| `data/normas_corpus.json` | `scripts/build_norm_registry.py` | controles de entrada y salida (números de norma) |

Ambos se copian en la imagen (`Dockerfile`). Las habilidades son Markdown: `.dockerignore`
y `.gcloudignore` excluyen `*.md` salvo `src/agent/skills/*.md`.
