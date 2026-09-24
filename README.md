# RecavAI

Asistente conversacional de cumplimiento en sostenibilidad para el marco normativo europeo: CSRD, CSDDD, ESRS/NEIS, estándares GRI y guías de la OCDE. El usuario pregunta en lenguaje natural y el sistema responde citando los documentos concretos en los que se apoya.

Proyecto RECAVA-IA, Universidad de Castilla-La Mancha.

## Dos modos sobre la misma base documental

**Asesor** — preguntas y respuestas sobre normativa. El usuario lleva la iniciativa y cada respuesta llega con citas numeradas y desplegables a los fragmentos de origen. Cada dato normativo (cifra, plazo, artículo, obligación) tiene que estar respaldado por un fragmento; si no lo está, el asesor dice que no consta, y un control automático revisa la respuesta antes de servirla.

**Auditor** — entrevista guiada en ocho bloques. El sistema lleva la iniciativa: formula todas las preguntas obligatorias de cada bloque, contrasta cada respuesta con la normativa, señala las brechas con su severidad y no cierra un bloque hasta haberlo cubierto.

## Arquitectura

```
Navegador (widget de chat · panel de experto)
        │  HTTPS + token de Firebase
        ▼
Cloud Run · Flask + Gunicorn (2 procesos × 8 hilos) · europe-west1
        │
        ├── Agente asesor (src/agent)  habilidades · controles · harness · contexto adaptativo
        ├── Gemini 2.5 Flash           generación y llamadas a herramientas
        ├── Pinecone recavai-corpus-v2 corpus troceado por unidad normativa + documentos del usuario
        ├── Grafo normativo            en memoria (data/normative_graph.json): referencias entre artículos
        ├── Almacén de unidades        en memoria (data/unidades.json.gz): artículos completos para el contexto
        ├── Firestore                  hilos, progreso de auditoría, documentos
        └── BigQuery                   trazas de conversación
```

Los *embeddings* se calculan dentro del contenedor con `intfloat/multilingual-e5-small` (384 dimensiones, 512 tokens), que va incluido en la imagen. El modelo, el índice y el umbral de similitud van siempre juntos: cambiar uno sin los otros rompe la búsqueda.

La búsqueda localiza y el contexto se construye por **unidades completas** (artículo, requisito NEIS, contenido GRI) con un presupuesto según la pregunta: 6.000, 12.000 o 45.000 tokens ([docs/PLAN_CONTEXTO.md](docs/PLAN_CONTEXTO.md)).

## Documentación

| Documento | Para qué |
|---|---|
| [docs/documentacion.html](docs/documentacion.html) | Explicación completa del sistema para personas: arquitectura, modos, interfaz, motor RAG, datos, seguridad |
| [docs/SPEC.md](docs/SPEC.md) | Especificación normativa legible por máquina, con identificadores estables, invariantes y deuda conocida |
| [docs/AGENTE.md](docs/AGENTE.md) | El agente asesor: habilidades, controles, harness y registro |
| [docs/ARQUITECTURA_DATOS.md](docs/ARQUITECTURA_DATOS.md) | Estado medido del corpus, el índice, el modelo y el grafo |
| [docs/RAG_V2_RESULTADOS.md](docs/RAG_V2_RESULTADOS.md) | Evaluación del cambio de índice, modelo y grafo |
| [benchmarks/RESULTADOS_FIDELIDAD.md](benchmarks/RESULTADOS_FIDELIDAD.md) | Evaluación de la fidelidad (habilidades y controles) |
| [docs/PLAN_CONTEXTO.md](docs/PLAN_CONTEXTO.md) · [benchmarks/RESULTADOS_CONTEXTO.md](benchmarks/RESULTADOS_CONTEXTO.md) | Contexto adaptativo: plan y evaluación |
| [docs/DESARROLLO.md](docs/DESARROLLO.md) | Montar el entorno local y trabajar en el código |
| [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) | Subir cambios al entorno vivo, con despliegue canario y vuelta atrás |
| [docs/MIGRACION.md](docs/MIGRACION.md) | Plan de migración a los proyectos GCP definitivos |
| [benchmarks/README.md](benchmarks/README.md) | Medir la calidad: batería de 60 preguntas, juez, comparaciones a ciegas |
| [paper/](paper/) | Línea de investigación sobre construcción de la base de conocimiento |

## Empezar

```bash
./scripts/dev.sh setup     # entorno virtual y dependencias
./scripts/dev.sh check     # dice qué falta por configurar
./scripts/dev.sh test      # pruebas del auditor y del agente, sin red ni claves
```

Y en tres terminales:

```bash
./scripts/dev.sh emulators   # Firebase Auth y Firestore locales
./scripts/dev.sh api         # backend en http://localhost:8080
./scripts/dev.sh web         # widget  en http://localhost:8000
```

## Estructura

```
app.py                     rutas HTTP, autenticación, límites, endpoints de administración
src/
  config.py                variables de entorno y clientes externos
  agent/                   el asesor: skills/*.md (habilidades), guardrails.py, harness.py, planner.py
  context_builder.py       contexto adaptativo: unidades completas, niveles S/M/L, orden de lectura
  gemini_service.py        turnos del auditor y sus herramientas; recuperación y contexto
  rag_service.py           embeddings, búsqueda en Pinecone, documentos del usuario
  normative_graph.py       grafo normativo en memoria (referencia explícita y expansión)
  audit_catalog.py         bloques y preguntas de la auditoría (fuente única de verdad)
  assistant_instructions.py  prompt del auditor
  doc_titles.py            títulos legibles de los documentos del corpus
  chunking_v2.py           troceado por unidad normativa (fuera del servicio)
  corpus_pipeline.py       indexación antigua (fuera del servicio)
  rag_benchmark.py         medición de calidad
  kb_experiment.py         experimento factorial de la línea de investigación
data/                      grafo normativo, registro de normas y almacén de unidades (van en la imagen)
benchmarks/                batería de evaluación v1 y resultados
public/chatbot/            widget de chat
public/admin-panel/        panel de experto (React)
functions/                 funciones de historial sobre BigQuery
scripts/                   desarrollo local, despliegue y pruebas
```

## Licencia y uso

Software del proyecto RECAVA-IA. El corpus normativo incluye documentos de terceros (GRI, OCDE) cuyas condiciones de uso hay que respetar por separado: no se redistribuyen en este repositorio.
