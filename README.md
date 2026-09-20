# RecavAI

Asistente conversacional de cumplimiento en sostenibilidad para el marco normativo europeo: CSRD, CSDDD, ESRS/NEIS, estándares GRI y guías de la OCDE. El usuario pregunta en lenguaje natural y el sistema responde citando los documentos concretos en los que se apoya.

Proyecto RECAVA-IA, Universidad de Castilla-La Mancha.

## Dos modos sobre la misma base documental

**Asesor** — preguntas y respuestas sobre normativa. El usuario lleva la iniciativa y cada respuesta llega con citas numeradas y desplegables a los fragmentos de origen.

**Auditor** — entrevista guiada en ocho bloques. El sistema lleva la iniciativa: formula todas las preguntas obligatorias de cada bloque, contrasta cada respuesta con la normativa, señala las brechas con su severidad y no cierra un bloque hasta haberlo cubierto.

## Arquitectura

```
Navegador (widget de chat · panel de experto)
        │  HTTPS + token de Firebase
        ▼
Cloud Run · Flask + Gunicorn · europe-west1
        │
        ├── Gemini 2.5 Flash          generación y llamadas a herramientas
        ├── Pinecone                  corpus normativo + documentos del usuario
        ├── Neo4j AuraDB              grafo de entidades
        ├── Firestore                 hilos, progreso de auditoría, documentos
        └── BigQuery                  trazas de conversación
```

Los *embeddings* se calculan dentro del contenedor con `sentence-transformers`; no hay API externa de *embeddings*. El modelo debe coincidir siempre con el que construyó el índice.

## Documentación

| Documento | Para qué |
|---|---|
| [docs/documentacion.html](docs/documentacion.html) | Explicación completa del sistema para personas: arquitectura, modos, interfaz, motor RAG, datos, seguridad |
| [docs/SPEC.md](docs/SPEC.md) | Especificación normativa legible por máquina, con identificadores estables, invariantes y deuda conocida |
| [docs/DESARROLLO.md](docs/DESARROLLO.md) | Montar el entorno local y trabajar en el código |
| [docs/DESPLIEGUE.md](docs/DESPLIEGUE.md) | Subir cambios al entorno vivo, con despliegue canario y vuelta atrás |
| [docs/MIGRACION.md](docs/MIGRACION.md) | Plan de migración a los proyectos GCP definitivos |
| [benchmarks/README.md](benchmarks/README.md) | Medir la calidad de la recuperación |
| [paper/](paper/) | Línea de investigación sobre construcción de la base de conocimiento |

## Empezar

```bash
./scripts/dev.sh setup     # entorno virtual y dependencias
./scripts/dev.sh check     # dice qué falta por configurar
./scripts/dev.sh test      # pruebas del auditor, sin red ni claves
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
  gemini_service.py        turnos de conversación, herramientas del auditor
  rag_service.py           recuperación, enrutado de preguntas, grafo
  audit_catalog.py         bloques y preguntas de la auditoría (fuente única de verdad)
  assistant_instructions.py  prompts de asesor y auditor
  corpus_pipeline.py       indexación del corpus (proceso por lotes, fuera del servicio)
  rag_benchmark.py         medición de calidad
  kb_experiment.py         experimento factorial de la línea de investigación
public/chatbot/            widget de chat
public/admin-panel/        panel de experto (React)
functions/                 funciones de historial sobre BigQuery
scripts/                   desarrollo local, despliegue y pruebas
```

## Licencia y uso

Software del proyecto RECAVA-IA. El corpus normativo incluye documentos de terceros (GRI, OCDE) cuyas condiciones de uso hay que respetar por separado: no se redistribuyen en este repositorio.
