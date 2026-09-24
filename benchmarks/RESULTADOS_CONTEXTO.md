# Resultados: contexto adaptativo (24/09/2026)

Evaluación de [docs/PLAN_CONTEXTO.md](../docs/PLAN_CONTEXTO.md). Mismo instrumento que
[RESULTADOS_FIDELIDAD.md](RESULTADOS_FIDELIDAD.md): batería de 60 preguntas, juez
`gemini-3.5-flash`, comparación por parejas con `gemini-3.7-flash` viendo los fragmentos,
índice `recavai-corpus-v2` con grafo, 4 hilos.

**Decisión: se despliega H7** (unidades completas, niveles S/M/L, segunda pasada, sin
razonamiento interno, planificador desactivado). Cumple todas las condiciones bloqueantes
y sube la cobertura 18 puntos (IC 95 % +11 a +26), con menos latencia que antes.

---

## 1. Configuraciones

| | Contexto | Nivel máx. | Segunda pasada | Razonamiento | Planificador |
|---|---|---|---|---|---|
| **H4** (producción hasta hoy) | 6 fragmentos + grafo (~1.800 tokens) | — | no | dinámico | no |
| **H5** | unidades completas | M | no | 0 | no |
| **H7** | unidades completas | L | sí | 0 | no |

## 2. Fase 0 — latencia según contexto y razonamiento

`scripts/probe_latency.py`, 2 preguntas × 3 tamaños × 3 presupuestos × 2 repeticiones
(35 llamadas válidas; una se perdió por un corte de conexión). Mediana:

| Contexto | Dinámico | Presupuesto 512 | Sin razonamiento | Tokens de razonamiento (dinámico) |
|---:|---:|---:|---:|---:|
| 2.000 | 3,8 s | 8,0 s | 3,5 s | 504 |
| 12.000 | 10,4 s | 6,3 s | 4,4 s | 1.152 |
| 40.000 | 23,0 s | 8,1 s | 5,9 s | 3.060 |

El razonamiento dinámico crece con el contexto y es lo que dispara la latencia. Sin él,
10.000 tokens más cuestan alrededor de 1 s. Planificador (`gemini-2.5-flash-lite` sin
razonamiento): p50 0,9 s, máximo 1,2 s.

## 3. Recuperación sola (sin coste de Gemini)

Hechos clave cuyo pasaje llega al modelo (`hechos_ctx`, nueva, determinista). Índice en
Pinecone con grafo, las mismas preguntas.

| Configuración | Hechos en contexto | Tokens de contexto (mediana / p95) |
|---|---:|---:|
| H4: 6 fragmentos + grafo | 58 % | 1.810 / 2.802 |
| 30 candidatos sueltos, sin ampliar (prueba intermedia) | 75 % | |
| Unidades, nivel M para todo (prueba intermedia) | 84 % | 11.276 / 11.531 |
| **Unidades con niveles S/M/L** (la de H5 y H7) | **83 %** | 11.226 / 11.554 |
| + planificador (fusión con peso igual) | 83 % | 11.308 / 42.064 |
| + planificador (la pregunta pesa como todas las reformulaciones) | 84 % | 11.327 / 41.359 |

Sin planificador, el nivel L solo lo pide el patrón de enumeraciones (3 preguntas); con
él, el planificador clasifica 16 como enumeración o panorama y el p95 de contexto sube a
42.000 tokens.

### El planificador no pasa su regla

Regla escrita en el plan antes de medir: *si los tipos coloquial y real no suben al menos
15 puntos, la fase 2 no se despliega*.

| Tipo | n | Unidades | + planificador | + planificador ponderado |
|---|---:|---:|---:|---:|
| coloquial | 6 | 54 | **88** | 54 |
| real | 4 | 60 | 67 | **75** |
| cambios | 5 | 73 | 53 | 60 |
| definición | 5 | 100 | 85 | 95 |
| multiartículo | 5 | 65 | 60 | 75 |
| sectorial | 5 | 90 | 90 | 100 |

Cada variante sube uno de los dos tipos y no el otro, y ambas bajan los cambios
normativos. La segunda variante se probó **después** de ver la primera, con una
justificación a priori (la pregunta del usuario quedaba en minoría frente a cuatro
reformulaciones) y la misma regla. **No se despliega**; el código queda con
`RAG_PLANNER=0`. Ahorra además 1-1,5 s por pregunta.

## 4. Batería completa

| Métrica | H4 | H5 | **H7** | Δ H7−H4 (IC 95 %) | Objetivo |
|---|---:|---:|---:|---|---|
| Hechos clave en contexto | 58 % | 83 % | 83 % | +25 (+17, +35) | ≥ 75 % ✓ |
| **Cobertura (juez)** | 63 % | 81 % | **81 %** | **+18 (+11, +26)** | ≥ 71 % ✓ |
| Fidelidad (juez) | 90 % | 93 % | 95 % | +5 (−3, +13) | ≥ 85 % ✓ |
| Datos críticos sin respaldo / respuesta | 0 | 0 | 0 | | 0 ✓ |
| Contradicen un dato clave | 3 | 2 | 2 | | ≤ 3 ✓ |
| Trampas | 3/3 | 3/3 | 3/3 | | 3/3 ✓ |
| Nombra la unidad citada | 70 % | 77 % | 72 % | +2 (−9, +13) | ≥ 66 % ✓ |
| Datos en el fragmento que se cita (`cita_ok`) | 96 % | 99,7 % | 99,7 % | | |
| Latencia p50 / p95 | 7,7 / 15,1 s | 4,9 / 11,8 s | 5,7 / 11,6 s | | ≤ 8,5 / 16 s ✓ |
| Tokens de entrada por pregunta | ~5.000 | | 13.500 | | |
| Coste por pregunta | ~0,003 $ | | ~0,006 $ | | ≤ 0,008 $ ✓ |

`mrr` baja (0,46 → 0,30) porque los bloques van en el orden del documento, no de la
similitud: ya no mide lo mismo.

### Por tipo de pregunta

| Tipo | n | Hechos en contexto H4 → H7 | Cobertura H4 → H5 → H7 | Fidelidad H4 → H5 → H7 |
|---|---:|---|---|---|
| ámbito | 5 | 40 → 80 | 40 → 60 → 60 | 80 → 80 → 80 |
| artículo | 5 | 80 → 100 | 87 → 100 → 93 | 100 → 100 → 100 |
| auditor | 4 | 75 → 83 | 88 → 88 → 88 | 100 → 100 → 100 |
| cambios | 5 | 43 → 73 | 53 → 83 → 80 | 80 → 100 → 100 |
| coloquial | 6 | 28 → 54 | 47 → 50 → 56 | 100 → 83 → 83 |
| cruce | 3 | 100 → 100 | 67 → 100 → 100 | 33 → 100 → 67 |
| definición | 5 | 95 → 100 | 90 → 100 → 100 | 80 → 100 → 100 |
| GRI | 5 | 55 → 100 | 57 → 92 → 90 | 100 → 100 → 100 |
| multiartículo | 5 | 45 → 65 | 57 → 70 → 72 | 80 → 100 → 100 |
| NEIS | 5 | 100 → 100 | 100 → 100 → 100 | 100 → 100 → 100 |
| real | 4 | 6 → 60 | 9 → 51 → 56 | 100 → 75 → 100 |
| sectorial | 5 | 44 → 90 | 46 → 83 → 83 | 100 → 100 → 100 |
| trampa | 3 | – | 92 → 92 → 92 | 100 → 67 → 100 |

Lo que más mejora es lo que se diagnosticó como «recuperado a medias»: contenidos GRI
(57 → 90), sectoriales (46 → 83), cambios (53 → 80). Lo que menos, las preguntas
coloquiales (47 → 56): el fallo ahí es de vocabulario, y era lo que debía resolver el
planificador (`DEBT-21` en el SPEC).

### Comparación a ciegas, viendo los fragmentos

**Batería: gana H7 37, gana H4 17, empate 6.** Donde gana H4, casi siempre por estilo
(«más concisa», «sin secciones no solicitadas»): con más contexto, H7 escribe más. Dos
son errores de H7: B47 mezcla el requisito E1-5 (energía) con las emisiones de alcance 1,
y B08 omite datos que tenía en el contexto. En B58 H7 citaba la etiqueta interna
«[AVISOS DEL SISTEMA]» como si fuera una fuente; **se corrigió después de la evaluación**
con una limpieza determinista de la respuesta (`harness.clean_answer`), sin tocar las
habilidades.

### Nivel L y segunda pasada

Con artículos enteros delante, el modelo casi nunca dice que algo no consta: los
borradores con abstención pasan de 10 de 60 (H4) a 0 (H5) y 1 (H7). En la batería la
segunda pasada no actuó ninguna vez (la única candidata, B50, ya iba por 14 s) y el
nivel L se aplicó a 3 preguntas, que ya tenían cobertura completa en M. **En la batería,
L y la segunda pasada ni ayudan ni perjudican.**

## 5. Preguntas reales no usadas para diseñar

Las mismas 40 preguntas reales reservadas de la evaluación anterior (muestra aleatoria,
semilla 2026). Configuración H7 contra las respuestas de H4 (`realNew40`, 23/09/2026):

- **Gana la versión nueva 29, H4 8, empate 3.**
- Latencia p50 / p95: **7,8 / 15,4 s** (H4: 8,7 / 18,7 s).
- Segunda pasada en 4 de 40 (10 %); en 2 se sirvió la respuesta con nivel L.
- Datos críticos sin respaldo: 0 tras reparar (0,075 por respuesta en los borradores);
  2 respuestas reparadas, ninguna anotada.
- Donde pierde (8): **dos errores de fidelidad** —atribuye a un libro el contenido de otra
  obra citada en la misma página (R009) y detalla obligaciones de la CSRD sin respaldo en
  sus fragmentos (R040, además la más lenta: 22,7 s con reparación)— y seis de utilidad o
  estilo: cita peor en una abstención correcta (R005, Estatuto de Roma), la antigua daba
  una definición general en la orientación práctica (R016), un caso documentado (R017),
  una respuesta más directa (R032), los ocho principios del estándar antes del detalle
  (R037) y una estructura más clara (R038).

No se midió H5 con preguntas reales: la elección entre H5 y H7 se basa en que H7 incluye
la segunda pasada, que actúa en el 10 % de las preguntas reales sin romper la latencia, y
en que el nivel máximo se puede bajar a M sin reconstruir la imagen.

## 6. Cambios de instrumento

- **El juez ve cada fragmento entero** (hasta 80.000 caracteres; antes 1.500-2.000). Sin
  esto, un artículo completo cortado hacía pasar por inventados datos que el modelo tenía
  delante. No cambia nada de H4, cuyos fragmentos no pasan de 1.800 caracteres.
- **Métricas nuevas deterministas** `hechos_ctx` y `cita_ok`; calculadas para H4 sin
  llamadas al modelo (`eval_battery.py rescore H4`).
- **Comparación por parejas con reintento de errores** (`--retry-errors`): 3 errores del
  juez en la batería y 2 en las reales se repitieron y resolvieron.
- **Una sola pasada** por configuración, cuando la higiene pide dos para las métricas que
  dependen de la generación. H5 y H7 son casi la misma configuración y dan 81,2 y 81,4 %
  de cobertura: funcionan como réplica aproximada. La mejora (+18) es tres veces el ruido
  medido entre pasadas idénticas (±6).

## 7. Coste de esta evaluación

Unas 560 llamadas a Gemini: fase 0 (36), planificador (~90, Flash-Lite), H5 y H7
(2 × 60 respuestas + 60 juicios, más reparaciones), 40 preguntas reales, 100 comparaciones
por parejas. Del orden de 3-4 $ con precios de lista.

## Ficheros

`results/rag_v2/` (fuera del repositorio): `H5`, `H7`, `U_busqueda`, `UP_busqueda`,
`UP2_busqueda`, `realU40`, `pairwise_frag_H4_vs_H7`, `pairwise_frag_realNew40_vs_realU40`,
`compare_H4_vs_H7.md`; `results/contexto/latencia.jsonl`.
