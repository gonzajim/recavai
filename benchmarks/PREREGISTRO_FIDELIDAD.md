# Prerregistro — fidelidad (capas 1-3), 2026-09-23

Escrito y guardado en git ANTES de ejecutar la evaluación.

## Qué se compara

| Nombre | Qué es |
|---|---|
| **v2** (`CG2.j35`) | Índice v2 + grafo, prompt monolítico anterior, temperatura por defecto, sin controles. Línea base. |
| **capas 1-2** (borradores de H) | Habilidades nuevas (contrato de fundamentación, abstención, orientación rotulada) + temperatura 0,2. |
| **capas 1-3** (respuestas finales de H) | Lo anterior + controles de entrada/contexto/salida y una reparación. |

Una sola generación produce las dos últimas: el borrador (antes de reparar) y la respuesta
final. Dos pasadas completas (H1, H2), porque la fidelidad varía ±13 puntos entre pasadas
idénticas. Juez `gemini-3.5-flash` para todo; comparación por parejas con `gemini-3.7-flash`.

## Métricas

- **criticos**: datos críticos (cifras, fechas, años, artículos, números de norma, códigos)
  sin respaldo en los fragmentos ni en la pregunta, por respuesta. Determinista.
  Línea base: v2 0,40; A 0,42.
- Del juez: fidelidad (sin afirmaciones no respaldadas), contradicciones con los datos
  clave, cobertura de datos clave, trampas.

**Advertencia de circularidad.** La capa 3 repara precisamente lo que mide `criticos`, así
que esa métrica NO sirve para juzgar la capa 3: la mejoraría por construcción. Para la
capa 3 manda el juez.

## Regla de decisión

**Capas 1-2 frente a v2** (las dos pasadas deben cumplir):
1. `criticos` baja, con IC 95 % que excluye 0.
2. La cobertura de datos clave no baja más de 0,06 (el ruido medido).
3. Las trampas detectadas no bajan.

**Capas 1-3 frente a capas 1-2:**
4. Fidelidad del juez igual o mejor, y contradicciones con los datos clave iguales o menos.
5. La cobertura no baja más de 0,06.

**Capas 1-3 frente a v2 (lo que se desplegaría):**
6. Comparación a ciegas por parejas: gana al menos tantas veces como pierde.
7. Latencia: la mediana no sube más de 2 s. El p95 se informa (la reparación añade una
   generación completa a las respuestas que la necesitan).

Si 1-3 no se cumplen, no se despliegan las habilidades. Si 4-5 no se cumplen, se despliegan
las capas 1-2 sin reparación (`max_repairs=0`) y los controles solo registran.
