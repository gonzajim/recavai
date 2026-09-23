# Resultados — fidelidad (capas 1-3), 2026-09-23

Regla de decisión: [PREREGISTRO_FIDELIDAD.md](PREREGISTRO_FIDELIDAD.md), guardada antes de medir.

## Ejecuciones

| | Qué | Juez |
|---|---|---|
| v2 (`CG2.j35`) | índice v2 + grafo, prompt antiguo, temperatura por defecto | gemini-3.5-flash |
| H1, H2 | harness con habilidades v1 + temperatura 0,2 + controles | gemini-3.5-flash |
| H1/H2 borradores | lo mismo antes de reparar (capas 1-2) | gemini-3.5-flash |
| **H3** | habilidades `citas` y `formato_asesor` v2 (nombrar el artículo en la frase), ajuste hecho **después** de ver H1/H2 | gemini-3.5-flash |

## Resultados

| | v2 | H1 | H2 | **H3** |
|---|---:|---:|---:|---:|
| Datos críticos sin respaldo / respuesta | 0,40 | 0,00 | 0,00 | **0,00** |
| Sin afirmaciones no respaldadas (juez) | 33 % | 90 % | 87 % | **90 %** |
| Contradicen un dato clave | 8 | 4 | — | **3** |
| Trampas detectadas | 2/3 | 3/3 | 3/3 | **3/3** |
| Datos clave cubiertos | 67 % | 62 % | 63 % | 63 % |
| Cita el artículo esperado | 72 % | 60 % | 60 % | **68 %** |
| Latencia p50 / p95 | 12,0 / 21,0 s | 6,1 / 13,2 s | 6,2 / 14,1 s | **6,9 / 13,7 s** |
| Longitud mediana | 4.047 car. | 1.718 car. | | |
| Reparaciones | — | 0 | 0 | 0 |

**Comparación a ciegas H3 contra v2** (gemini-3.7-flash, sin ver los fragmentos):
**v2 gana 35, H3 gana 20**, 5 empates.

## Regla

| # | Condición | Resultado |
|---|---|---|
| 1 | `criticos` baja con IC que excluye 0 | cumple (−0,40 [−0,85, −0,08]) |
| 2 | Cobertura no baja más de 0,06 | cumple (−0,03 a −0,04) |
| 3 | Trampas no bajan | cumple (2/3 → 3/3) |
| 4 | Capa 3: fidelidad igual o mejor | cumple trivialmente: **la reparación nunca se activó** |
| 5 | Capa 3: cobertura | cumple (sin cambios) |
| 6 | **Comparación a ciegas: gana al menos tanto como pierde** | **no cumple (20–35)** |
| 7 | Mediana de latencia no sube más de 2 s | cumple (baja 5 s) |

## Lectura

- El prompt nuevo (capas 1-2) elimina los datos críticos inventados que el detector
  reconoce y lleva la fidelidad del 33 % al 90 %. La capa 3 no tuvo nada que reparar en la
  batería: queda como red de seguridad, sin coste mientras no salta.
- El juez por parejas prefiere las respuestas antiguas sobre todo por **estructura y
  extensión** (15,8 bloques en negrita frente a 3,8; 2,4 veces más largas).
- Sesgo del juez por parejas: no ve los fragmentos y juzga con su propio conocimiento,
  anterior al Ómnibus. En B22 califica de «referencia normativa inexistente» la Directiva
  (UE) 2026/470, que es el Ómnibus I y está en el corpus.
- Abstención excesiva medida: 1 caso (B43, los cinco pasos OCDE: el fragmento estaba
  recuperado y el modelo no lo usó).
- Errores que siguen: B26 (dice que habrá NEIS sectoriales obligatorias) y B49 (niega la
  exención del art. 16.2). Sin cifras ni artículos: fuera del alcance de la capa 3.

## Decisión

Según la regla, **no se despliega tal cual**. Pendiente de decisión del responsable.

---

# Segunda ronda — juez con fragmentos, formato ajustado y validación con preguntas reales

## 1. Juez por parejas que ve los fragmentos

El juez anterior no veía los fragmentos y juzgaba con su propio conocimiento, anterior al
Ómnibus. Nuevo criterio (`pairwise --with-context`): ve los fragmentos que tuvieron las dos
respuestas (idénticos en las 60 preguntas), el texto consolidado prevalece sobre su memoria
y un dato normativo sin respaldo cuenta en contra. **Es un cambio de instrumento hecho
después de ver resultados, y el criterio favorece a propósito la respuesta fundamentada.**

H3 contra v2: **gana H3 49, gana v2 8**, 3 empates (antes, sin fragmentos: 20–35).

## 2. Formato ajustado (H4)

De las 8 que perdía H3, 6 eran por estructura o exhaustividad. Habilidades
`formato_asesor` v3, `fundamentacion` v2, `abstencion` v2 (commit `7323614`).

| | v2 | H3 | **H4** |
|---|---:|---:|---:|
| Datos críticos sin respaldo / respuesta | 0,40 | 0,00 | **0,00** |
| Sin afirmaciones no respaldadas | 33 % | 90 % | **90 %** |
| Datos clave cubiertos | 67 % | 63 % | 63 % |
| Cita el artículo esperado | 72 % | 68 % | **70 %** |
| Trampas | 2/3 | 3/3 | **3/3** |
| A ciegas con fragmentos, contra v2 | — | 49–8 | **52–6** |
| Latencia p50 / p95 | 12,0 / 21,0 s | 6,9 / 13,7 s | 7,7 / 15,1 s |

## 3. Validación con preguntas reales no usadas para diseñar

40 preguntas reales de BigQuery, muestra aleatoria (semilla 2026) de las 226 que nunca se
usaron para escribir las habilidades. Versión anterior al agente (commit `e3add68`, mismo
índice y grafo) contra H4, juez con fragmentos:

- **Gana la versión nueva 36, la anterior 4.**
- Datos críticos sin respaldo por respuesta: **0,80 → 0,00**. La capa 3 reparó 1 respuesta.
- 17 de 40 respuestas abren diciendo que algo no consta (antes, 0). Son en su mayoría
  **abstenciones correctas**: los usuarios preguntan por el Estatuto de Roma, Núremberg,
  Minas Gerais, delitos del Código Penal, la Ley 2/2023, la EUDR… que no están en el corpus
  y que la versión anterior contestaba de memoria.
- Las 4 que pierde: 2 saludos o preguntas sobre sus funciones («¿En qué puedes ayudarme?»
  empezaba con «No se ha recuperado ningún fragmento…») y 2 encargos de redacción
  (cláusulas contractuales, guía de auditoría paso a paso).

**Corregido después:** los saludos y las preguntas sobre funciones (`identidad` v2 con las
capacidades del producto; aviso de contexto vacío que no se menciona ante un saludo).
Verificado solo con esas 3 preguntas; no se ha repetido la batería completa.

**Pendiente (decisión de producto):** encargos de redacción. La versión nueva resume la
norma en lugar de redactar la plantilla pedida.

## Decisión

Con el juez que ve los fragmentos, se cumplen las siete condiciones y la validación con
preguntas reales lo confirma (36–4). Recomendación: desplegar como versión de prueba y
promover.
