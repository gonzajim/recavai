# RAG v2 — resultados de la evaluación del 2026-09-23

Evaluación antes/después de tres cambios: modelo de vectorización, troceado por unidad
normativa y grafo normativo en memoria. Batería de 60 preguntas en 13 tipos
([BATERIA_V1.md](../benchmarks/BATERIA_V1.md)), congelada en el commit `2c90e90`
**antes** de construir nada nuevo.

## Estado al cerrar el día

| | |
|---|---|
| Producción | revisión `orchestrator-dev-00056-loh` (v1), 100 % del tráfico. **Sin cambios.** |
| Versión de prueba | revisión `orchestrator-dev-00059-guh` (commit `8523966`, con la corrección del auditor), etiqueta `canary`, 0 % del tráfico. Responde. **Lista para promover.** |
| Índice v2 | `recavai-corpus-v2`, 8.127 vectores, us-east-1 (el plan gratuito de Pinecone no admite regiones europeas), protección de borrado activada. |
| Índice v1 | `uclm-corpus-roma`, intacto, protección de borrado activada hoy. |
| Gemini | El crédito prepago se agotó durante la evaluación (error 402, también en producción) y se recargó el mismo día; la clave de producción vuelve a responder. |
| Decisión | **Promover C+G.** Se cumplen las condiciones 1-4; la 5 (latencia) es indeterminada con 60 preguntas y se acepta (ver abajo). Falta ejecutar `./scripts/deploy.sh promote dev`. |

## Configuraciones

| | Modelo | Troceado | Grafo | Umbral |
|---|---|---|---|---:|
| **A** | all-MiniLM-L6-v2 (producción) | v1 (el índice actual) | — | 0,55 |
| **B** | multilingual-e5-small | v1 (vectores recalculados) | — | 0,851 |
| **C** | multilingual-e5-small | v2 (por unidad normativa) | — | 0,841 |
| **C+G** | multilingual-e5-small | v2 | expansión 1 salto + referencia explícita | 0,841 |

Umbrales calibrados con las 226 preguntas reales de BigQuery (no con la batería) para
que quede sin contexto la misma fracción que hoy: 8,4 %.

## Resultados

Mismo juez para todas (`gemini-3.5-flash`). La búsqueda es determinista; la generación no.

| Métrica | A | B | C | **C+G** | C+G − A (IC 95 %) |
|---|---:|---:|---:|---:|---|
| Documento correcto entre los recuperados | 45 % | 40 % | 71 % | **71 %** | +26 [+12, +40] |
| Pasaje de la respuesta recuperado | 35 % | 47 % | 61 % | **68 %** | +33 [+18, +47] |
| Página correcta | 3 % | 5 % | 50 % | **60 %** | +57 [+43, +69] |
| Fuentes esperadas encontradas | 1 % | 3 % | 46 % | **57 %** | +55 [+43, +67] |
| Datos clave en la respuesta | 48 % | 55 % | 60 % | **65 %** | +17 [+7, +28] |
| Respuestas sin afirmaciones no respaldadas | 17 % | 27 % | 27 % | **28 %** | +12 [−3, +25] |
| Cita el artículo o requisito correcto | 28 % | 45 % | 60 % | **66 %** | +38 [+26, +51] |
| Trampas detectadas | 1/3 | 2/3 | 2/3 | **2/3** | |
| Veredicto correcto (auditor) | 4/4 | 4/4 | 4/4 | 4/4 | |

**Comparación a ciegas de respuestas** (juez `gemini-3.7-flash`, orden aleatorio), con la
corrección del auditor: C+G gana **43**, A gana **15**, 2 empates. En el modo auditor,
4–0 (sin la corrección, 1–3). En las 4 preguntas reales de la batería gana A 3 a 1, por
redacción salvo B52, donde C+G cita un número de directiva erróneo (2024/1109).

**Contexto recuperado en las 226 preguntas reales** (juez `gemini-3.8-flash`, a ciegas):
C+G gana **132**, A gana **28**, empate 66. Sustituye a la semana en sombra del plan
original.

**Auditor con la corrección** (4 comprobaciones): veredicto 4/4, cita el artículo 4/4,
sin afirmaciones no respaldadas 4/4 (antes 1/4).

**Ruido** (A medido dos veces): búsqueda idéntica; datos clave ±6 puntos; fidelidad
hasta ±13; trampas 1→2 de 3 sin cambiar nada.

**Lectura.** El modelo nuevo solo (B) mejora poco y sin significación en la búsqueda.
La mejora grande aparece al cambiar a la vez modelo y troceado (C), y el grafo suma
sobre C de forma significativa: pasaje +7, página +10, fuentes +10 (IC excluyen 0).
La fidelidad sigue baja en todas las configuraciones: es un problema de generación
(el modelo completa con lo que sabe), no de búsqueda, y queda para otro día.

## Regla de decisión (fijada antes de medir)

| # | Condición | C+G |
|---|---|---|
| 1 | Documento correcto ≥ A y artículo exacto +10 pts | **cumple** (+26 y +38) |
| 2 | Ningún tipo con ≥ 5 preguntas empeora en más de 1 | **cumple** |
| 3 | Ninguna invención nueva en las trampas | **cumple** (B58 mejora; B60 falla en ambas) |
| 4 | El juez prefiere la nueva al menos tanto como la vieja y la fidelidad no baja | **cumple** (43–15; contexto real 132–28) |
| 5 | Latencia p95 no sube más de 500 ms | **indeterminada**: +1,3 s, IC [−0,9; +4,1] s; el ruido (A vs A2) es [−1,8; +3,1] s. Por pregunta, mediana +340 ms. La búsqueda añade 22 ms; el resto es generación con un 70 % más de contexto |

## Hallazgos del día que no estaban en el plan

- **Páginas del índice v1 mal (DEBT-15).** El 64 % de los fragmentos dice estar en la
  página 1 (NEIS: 966 de 1.205). Las citas que ven los usuarios llevan páginas falsas.
- **El verificador del modo auditor buscaba con sus propias instrucciones.** Embebía la
  consulta entera («VERIFICACIÓN… Responde EXACTAMENTE con este formato…»), de modo que
  recuperaba el glosario en vez del artículo aplicable, en v1 y en v2. Es la razón de
  que A gane 3 de 4 comprobaciones del auditor en la comparación por parejas. Corregido
  (`build_verification_retrieval_query`): con la corrección, la búsqueda encuentra el
  art. 7, 15 y 16 de la CSDDD y el 100 % de las fuentes, y las 4 respuestas pasan a ser
  fieles y citar el artículo correcto (antes 1 de 4).
- **Referencia explícita.** Los vectores no distinguen números («artículo 9» traía los
  arts. 2, 3 y 38). El grafo resuelve «artículo N de la CSDDD», «E1-6», «GRI 305-1».
  Se añadió **después** de ver la batería: 13 de sus 60 preguntas nombran una unidad;
  **0 de las 226 preguntas reales** lo hacen. Ayuda a expertos, no a los usuarios actuales.
- **Arranque.** Cloud Run da la revisión por lista en cuanto gunicorn abre el puerto,
  antes de que Python termine de importar; sin peticiones, la CPU se estrangula y la
  carga se alarga minutos. Arranque real ≈ 35 s (v1 ≈ 41 s). Problema previo, no de v2.
- **Memoria.** e5-small ocupa ~1 GB por proceso: 4 procesos no caben en 4 GiB. La
  imagen pasa a 2 procesos × 8 hilos (16 peticiones simultáneas frente a 4).
- **Cuotas.** `gemini-3.1-pro-preview`: 250 peticiones/día por modelo. El juez se
  cambió a `gemini-3.5-flash` y todas las configuraciones se volvieron a juzgar con él.

## Qué falta

1. `./scripts/deploy.sh promote dev` — pasa el 100 % del tráfico a la revisión 00059.
2. Comprobar en el widget una pregunta del asesor y una respuesta del auditor.
3. Volver atrás, si algo va mal: `./scripts/deploy.sh rollback dev` (la revisión 00056 y
   el índice v1 siguen intactos).
4. Pendientes que no bloquean: fidelidad de la generación (el modelo completa con lo que
   sabe: ~28 % de respuestas sin afirmaciones no respaldadas); B60 (Ley 11/2018) sigue
   inventando; la sonda de arranque de Cloud Run da la instancia por lista antes de que
   la aplicación cargue.

## Reproducir

```bash
python scripts/extract_corpus_text.py --corpus "<carpeta>" --out .cache/corpus_txt
python benchmarks/bateria_v1.py --verify .cache/corpus_txt           # 175/175
python scripts/build_memory_index.py --out .cache/idx_A.npz
python scripts/build_memory_index.py --cache-from .cache/idx_A.npz --reembed intfloat/multilingual-e5-small --out .cache/idx_B.npz
python scripts/build_memory_index.py --chunker-v2 .cache/corpus_txt --reembed intfloat/multilingual-e5-small --out .cache/idx_C.npz
python scripts/build_normative_graph.py
python scripts/calibrate_threshold.py --ref-index .cache/idx_A.npz --ref-model sentence-transformers/all-MiniLM-L6-v2 \
    --new-index .cache/idx_C.npz --new-model intfloat/multilingual-e5-small
python scripts/eval_battery.py run --name CG --index recavai-corpus-v2 --model intfloat/multilingual-e5-small \
    --min-score 0.841 --graph data/normative_graph.json --judge-model gemini-3.5-flash
python scripts/eval_battery.py compare A CG
python scripts/eval_battery.py pairwise A CG --judge-model gemini-3.7-flash
```

Las preguntas reales (`data/real_queries.jsonl`) no están en el repositorio porque son
datos de usuarios. Se regeneran con:

```sql
SELECT user_message AS q, MIN(timestamp) AS t
FROM `recava-auditor-dev.recava_agent_audit_qa.chat_history`
WHERE endpoint_source = '/chat_assistant' AND user_message IS NOT NULL
GROUP BY user_message
```

y el filtro de `scripts/eval_battery.py` (longitud ≥ 15, sin preguntas de prueba ni
las que ya están en la batería).

**Coste.** La evaluación completa (5 configuraciones con generación, 2 jueces, 2
comparaciones por parejas) hizo ~1.100 llamadas a Gemini sobre la misma cuenta de
facturación que producción. Antes de repetirla, comprobar el saldo o usar una clave con
facturación separada.
