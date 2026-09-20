# Documentación del Proyecto — Asistente de Auditoría y Asesoría en Sostenibilidad

## Índice

1. [Visión general](#1-visión-general)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Modos de uso](#3-modos-de-uso)
4. [Interfaz de usuario (Frontend)](#4-interfaz-de-usuario-frontend)
5. [API REST (Backend)](#5-api-rest-backend)
6. [Sistema RAG](#6-sistema-rag)
7. [Modelo de datos](#7-modelo-de-datos)
8. [Gestión de documentos del usuario](#8-gestión-de-documentos-del-usuario)
9. [Autenticación y seguridad](#9-autenticación-y-seguridad)
10. [Infraestructura y despliegue](#10-infraestructura-y-despliegue)
11. [Variables de entorno](#11-variables-de-entorno)

---

## 1. Visión general

Herramienta web de IA especializada en sostenibilidad empresarial y cumplimiento normativo europeo, con dos funcionalidades principales:

- **Modo Asesor** — Responde preguntas sobre CSRD, CSDDD, NEIS/ESRS, estándares GRI y marcos OCDE/ONU mediante un sistema de RAG sobre corpus documental propio y documentos subidos por el usuario.
- **Modo Auditor** — Conduce una entrevista estructurada de auditoría de diligencia debida en 8 bloques, siguiendo un protocolo de preguntas obligatorias derivado de la CSDDD y normativa conexa, con herramientas de IA para escalar preguntas técnicas al asesor.

---

## 2. Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────────────┐
│  Firebase Hosting                                               │
│  public/chatbot/ (HTML + CSS + JS estático)                    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS / REST
┌───────────────────────────▼─────────────────────────────────────┐
│  Cloud Run — Orquestador Flask (app.py)                         │
│  4 workers gunicorn · 2 CPU · 4 GB RAM · timeout 600s           │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ gemini_service│  │  rag_service │  │  context_orchestrator│  │
│  │ (LLM + tools)│  │  (embed+search)│  │  (búsqueda paralela) │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘  │
└─────────┼─────────────────┼────────────────────────────────────┘
          │                 │
   ┌──────▼──────┐   ┌──────▼──────┐   ┌─────────────┐
   │ Gemini 2.5  │   │  Pinecone   │   │   Neo4j     │
   │ Flash (API) │   │  (vectores) │   │  (grafos)   │
   └─────────────┘   └──────┬──────┘   └─────────────┘
                            │
                    ┌───────▼───────┐
                    │  Namespaces:  │
                    │  "" → corpus  │
                    │  uid → usuario│
                    └───────────────┘

   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
   │  Firestore  │   │  BigQuery   │   │  Firebase   │
   │  (historial │   │  (analytics)│   │  Auth       │
   │  + progreso │   │             │   │             │
   │  + user docs│   │             │   │             │
   └─────────────┘   └─────────────┘   └─────────────┘
```

### Componentes principales

| Componente | Tecnología | Función |
|---|---|---|
| Frontend | HTML / CSS / JavaScript vanilla | UI del chatbot |
| Hosting | Firebase Hosting | Servicio de archivos estáticos |
| Backend | Python 3.10 · Flask · Gunicorn | API REST orquestadora |
| Contenedor | Docker (multi-stage, CPU-only) | Empaquetado del backend |
| Compute | Google Cloud Run (europe-west1) | Ejecución serverless |
| LLM | Gemini 2.5 Flash (google-genai SDK) | Generación de respuestas |
| Embeddings | all-MiniLM-L6-v2 (SentenceTransformers, 384 dims) | Vectorización de texto |
| Búsqueda semántica | Pinecone (IndexFlatIP) | RAG del corpus y docs de usuario |
| Grafo de conocimiento | Neo4j AuraDB | GraphRAG para entidades normativas |
| Historial de conversación | Cloud Firestore | Persistencia de mensajes por thread |
| Progreso de auditoría | Cloud Firestore | Estado de bloques por thread |
| Documentos del usuario | Cloud Firestore + Pinecone | Metadatos + vectores de PDFs subidos |
| Analytics | Google BigQuery | Trazabilidad de conversaciones |
| Autenticación | Firebase Authentication | JWT + verificación de email |
| Secretos | Secret Manager | API keys y credenciales |
| CI/CD | Cloud Build | Build Docker + despliegue Cloud Run + Firebase |

---

## 3. Modos de uso

### 3.1 Modo Asesor

Activa el endpoint `/chat_assistant`. El modelo actúa como asesor jurídico-técnico en normativa europea de sostenibilidad.

**Ámbito de conocimiento:**
- CSRD (Corporate Sustainability Reporting Directive) y NEIS/ESRS
- CSDDD (Corporate Sustainability Due Diligence Directive)
- Estándares GRI (Global Reporting Initiative)
- Marcos OCDE y ONU sobre conducta empresarial responsable

**Tipos de pregunta y formato de respuesta:**
- **Conceptual** ("qué es", "cómo funciona"): definición → contexto normativo → relaciones → implicaciones
- **Operacional** ("cómo hago", "qué pasos"): pasos ordenados + evidencias + documentos requeridos
- **De recursos** ("qué herramientas", "qué certificaciones"): listas estructuradas de opciones prácticas

**RAG:** Para cada turno, el sistema busca en paralelo en (1) los documentos subidos por el usuario (namespace de Pinecone del uid), y (2) el corpus documental global (namespace por defecto de Pinecone), y también consulta triples de entidades en Neo4j para preguntas operacionales y de recursos.

### 3.2 Modo Auditor

Activa el endpoint `/chat_auditor`. El modelo conduce una auditoría de diligencia debida estructurada en **8 bloques obligatorios**, en orden estricto (1→8).

**Bloques de auditoría:**

| Bloque | Nombre | Preguntas [M] |
|---|---|---|
| 1 | Contexto y Alcance | 6 |
| 2 | Información Corporativa | 5 |
| 3 | Cadena de Valor | 5 |
| 4 | Gobernanza y Compliance | 6 |
| 5 | Impacto Ambiental | 5 |
| 6 | Personas y Derechos Humanos | 6 |
| 7 | Riesgos y Controles | 5 |
| 8 | Conclusiones y Roadmap | 4 |

**Reglas de comportamiento del auditor:**
- Formula 1-3 preguntas por turno (ritmo conversacional)
- Las preguntas marcadas `[M]` son obligatorias; las opcionales se adaptan al perfil de empresa
- No cierra un bloque hasta tener respuesta explícita a todas las `[M]`
- Reutiliza información ya proporcionada, no repite preguntas
- Al detectar brechas, las clasifica como Crítico / Alto / Medio
- Al completar el bloque 8, genera un resumen ejecutivo de la auditoría completa

**Herramientas del auditor (function calling):**

- `invoke_sustainability_expert(query)` — Escala al asesor cuando el usuario hace una pregunta técnica o normativa. El asesor hace RAG sobre el corpus y los documentos del usuario y devuelve una respuesta fundamentada.
- `complete_audit_block(block_id, summary)` — Marca el bloque como completado y guarda el resumen en Firestore. Solo se llama cuando todas las `[M]` del bloque tienen respuesta.

**Progreso:** El estado de los 8 bloques se persiste en Firestore por `thread_id`. En cada turno, el backend carga el progreso actual y lo inyecta en el system prompt del auditor como contexto.

---

## 4. Interfaz de usuario (Frontend)

El frontend es una SPA (Single Page Application) en JavaScript vanilla, servida desde Firebase Hosting en `public/chatbot/`.

### 4.1 Flujo general

```
Login (email + contraseña Firebase)
  → Verificación de email
  → Pantalla de selección de modo
  → Chat (modo Asesor o Auditor)
```

### 4.2 Autenticación

- Login con email y contraseña (Firebase Auth SDK en el cliente)
- Registro de nuevos usuarios
- Banner de verificación de email (el backend rechaza tokens de emails no verificados)
- Gestión de errores de auth traducida al español

### 4.3 Pantalla de selección de modo

Muestra dos tarjetas:
- **Modo Asesor** — Consultas libres sobre normativa de sostenibilidad
- **Modo Auditor** — Inicio de una auditoría estructurada de diligencia debida

Cada tarjeta tiene el botón de selección en la parte superior y el texto explicativo debajo.

El historial de conversaciones recientes aparece debajo de las tarjetas (cargado desde BigQuery, máximo 5 conversaciones). El usuario puede reanudar cualquier conversación previa con un clic.

### 4.4 Layout del chat — Modo Asesor

Panel único con:
- **Zona de mensajes** (scrollable, sticky input en la parte inferior)
- **Barra de input** siempre visible con: botón de adjuntar PDF, área de texto (auto-expansible), botón de envío
- **Vista previa de archivo** al seleccionar un PDF, con botón "Subir PDF"
- **Badge de documento activo** — aparece encima del input cuando hay un PDF subido en la sesión
- **Panel "Mis documentos"** — lista de PDFs permanentes del usuario con botón de borrado, visible al entrar en modo asesor si el usuario tiene documentos subidos

### 4.5 Layout del chat — Modo Auditor (3 paneles)

Layout de rejilla CSS (`display: grid`) con 3 columnas, cada una plegable independientemente:

```
┌──────────────────┬────────────────────────┬────────────────────┐
│  Panel izquierdo │   Panel central         │  Panel derecho     │
│  (240px)         │   (flex: 1)             │  (280px)           │
│                  │                         │                    │
│  Proceso de      │   Historial de Q&A      │  [Informe] [Archivos]│
│  auditoría:      │                         │                    │
│  · % progreso    │   Mensajes del usuario  │  Informe: resúmenes│
│  · Barra mini    │   y del auditor         │  por bloque        │
│  · Bloques 1-8   │                         │  completado        │
│    (expandibles) │   ─────────────────     │                    │
│                  │   Input sticky          │  Archivos: PDFs    │
│  [◀ Plegar]      │                         │  subidos con       │
│                  │                         │  botón de borrado  │
└──────────────────┴────────────────────────┴────────────────────┘
```

**Panel izquierdo — Proceso de auditoría:**
- Cabecera con botón para plegar/desplegar
- Barra de progreso mini (porcentaje completado)
- Lista de bloques 1-8 con estado visual (pendiente / activo / completado)
- Cada bloque es expandible para ver el resumen de hallazgos
- Bloques completados muestran checkmark y pueden marcarse manualmente

**Panel central — Conversación:**
- Mensajes alternados usuario / auditor con soporte Markdown
- Citas con badges de fuente (modo asesor)
- Input siempre visible (sticky) con las mismas opciones que el modo asesor

**Panel derecho — Informe y Archivos:**
- Dos pestañas: "Informe" y "Archivos"
- **Informe**: resúmenes de hallazgos de cada bloque completado (actualizado en tiempo real)
- **Archivos**: lista de PDFs subidos por el usuario con fecha, número de fragmentos y botón de borrado

Los tres paneles pueden plegarse individualmente con botones de cabecera, adaptando la rejilla CSS dinámicamente. En móvil (<720px), el layout cambia a columnas verticales apiladas.

### 4.6 Historial de conversaciones

- Se cargan las 5 conversaciones más recientes al iniciar sesión (desde BigQuery)
- Al hacer clic en una conversación previa, se recuperan todos los mensajes desde BigQuery y se reproduce la conversación en el chat
- El estado de progreso de auditoría de la conversación reanudada se carga desde Firestore
- El `thread_id` se mantiene durante toda la sesión y se pasa en cada petición al backend

### 4.7 Gestión de archivos en el frontend

- El botón "📎" abre un selector de archivos (solo PDF, máximo 20 MB)
- Al seleccionar un archivo, aparece una vista previa con el nombre y el botón "Subir PDF"
- Al subir, el backend indexa el PDF en Pinecone (namespace del usuario) y devuelve la lista actualizada de documentos
- El badge "activo en esta sesión" indica que el documento está disponible para consultas
- La lista completa de documentos permanentes se carga desde `/user_files` al iniciar sesión
- Máximo 25 documentos por usuario; al alcanzar el límite se muestra un mensaje de error

---

## 5. API REST (Backend)

Todos los endpoints (excepto `/health` y `/readyz`) requieren cabecera:
```
Authorization: Bearer <Firebase ID token>
```
El token debe corresponder a un usuario con email verificado.

Formato de respuesta estándar:
```json
{ "ok": true, "data": { ... } }
{ "ok": false, "error": { "message": "...", "details": "..." } }
```

### Endpoints de chat

#### `POST /chat_auditor`
Envía un mensaje al auditor. Crea un nuevo `thread_id` si no se proporciona.

**Rate limit:** 12/min, 2/s

**Request:**
```json
{ "message": "texto del usuario", "thread_id": "uuid (opcional)" }
```

**Response:**
```json
{
  "response": "texto del auditor",
  "thread_id": "uuid",
  "run_id": "hex",
  "run_status": "completed"
}
```

**Flujo interno:**
1. Carga el progreso de auditoría del thread desde Firestore
2. Carga el historial de conversación desde Firestore (últimos 20 turnos)
3. Formatea el contexto de auditoría e inyecta en el system prompt
4. Llama a `chat_with_auditor` (Gemini 2.5 Flash con tool calling)
5. Persiste el turno en Firestore y BigQuery

---

#### `POST /chat_assistant`
Envía un mensaje al asesor de sostenibilidad con RAG.

**Rate limit:** límite global (120/min)

**Request:**
```json
{ "message": "texto del usuario", "thread_id": "uuid (opcional)" }
```

**Response:**
```json
{
  "response": "texto del asesor",
  "sources": [
    { "index": 1, "title": "...", "category": "CSDDD", "score": 0.82, "excerpt": "..." }
  ],
  "thread_id": "uuid",
  "run_id": "hex",
  "run_status": "completed"
}
```

**Flujo interno:**
1. Carga historial de Firestore
2. Busca en Pinecone (namespace del usuario + corpus global) y Neo4j
3. Construye mensaje aumentado con contexto documental numerado ([1], [2]...)
4. Llama a `chat_with_expert` (Gemini 2.5 Flash)
5. Persiste el turno en Firestore y BigQuery

---

### Endpoints de gestión de documentos

#### `POST /upload_document`
Sube un PDF, lo chunquea, vectoriza e indexa en Pinecone bajo el namespace del usuario.

**Rate limit:** 10/min

**Request:** `multipart/form-data`
- `file`: archivo PDF (máximo 20 MB)
- `thread_id`: uuid (opcional)

**Response:**
```json
{
  "doc_id": "uuid",
  "filename": "informe.pdf",
  "chunks_indexed": 42,
  "files": [ { "doc_id": "...", "filename": "...", "chunk_count": 42, "size_bytes": 123456, "uploaded_at": "ISO8601" } ]
}
```

**Restricciones:** Máximo 25 documentos por usuario. El límite se comprueba antes de indexar.

---

#### `GET /user_files`
Devuelve la lista de documentos permanentes del usuario.

**Rate limit:** 30/min

**Response:**
```json
{
  "files": [
    { "doc_id": "uuid", "filename": "nombre.pdf", "chunk_count": 42, "size_bytes": 123456, "uploaded_at": "ISO8601" }
  ]
}
```

---

#### `DELETE /user_files/<doc_id>`
Elimina un documento del usuario de Pinecone y Firestore.

**Rate limit:** 20/min

**Response:**
```json
{ "doc_id": "uuid", "deleted": true, "files": [ ... ] }
```

---

### Endpoints de progreso de auditoría

#### `GET /audit_blocks`
Devuelve la lista de los 8 bloques de auditoría con su id y etiqueta.

**Autenticación:** No requerida.

**Response:**
```json
{
  "blocks": [
    { "id": "block_1", "label": "1. Contexto y Alcance" },
    ...
  ]
}
```

---

#### `GET /audit_progress/<thread_id>`
Devuelve el estado actual de los 8 bloques para un thread.

**Response:**
```json
{
  "thread_id": "uuid",
  "uid": "firebase_uid",
  "blocks": [
    { "id": "block_1", "label": "...", "status": "completed", "summary": "...", "completed_at": "ISO8601" },
    { "id": "block_2", "label": "...", "status": "in_progress", "summary": null }
  ],
  "active_block_id": "block_2",
  "completed_count": 1,
  "total_blocks": 8,
  "percent": 12
}
```

---

#### `POST /audit_progress/<thread_id>`
Actualiza el estado de un bloque (usado por el frontend cuando el usuario marca un bloque manualmente).

**Request:**
```json
{ "block_id": "block_3", "status": "completed", "summary": "Texto del resumen" }
```

Usa una transacción Firestore para garantizar consistencia. El campo `status` puede ser `pending`, `in_progress` o `completed`.

---

### Endpoints de historial

#### `GET /chat_history/recents`
Devuelve las conversaciones más recientes del usuario desde BigQuery.

**Query params:** `limit` (1-20, default 5)

**Response:**
```json
{
  "conversations": [
    { "thread_id": "uuid", "endpoint_source": "/chat_auditor", "last_timestamp": "ISO8601", "summary": "primer mensaje..." }
  ]
}
```

---

#### `GET /chat_history/thread/<thread_id>`
Devuelve todos los mensajes de una conversación desde BigQuery.

**Response:**
```json
{
  "thread_id": "uuid",
  "endpoint_source": "/chat_assistant",
  "messages": [
    { "role": "user", "text": "...", "timestamp": "ISO8601" },
    { "role": "assistant", "text": "...", "timestamp": "ISO8601" }
  ],
  "total_messages": 12
}
```

---

### Endpoint de administración

#### `POST /admin/ingest_document`
Añade un fragmento de texto al corpus global de Pinecone (namespace por defecto). Solo accesible por usuarios autenticados.

**Request:**
```json
{
  "content": "texto del documento (máx. 50.000 chars)",
  "doc_id": "uuid (opcional, se genera si no se proporciona)",
  "title": "título",
  "source_url": "https://...",
  "doc_type": "CSDDD | GRI | general"
}
```

---

### Endpoints de salud

#### `GET /health`
Devuelve `{"status": "healthy"}`. Usado por el HEALTHCHECK de Docker.

#### `GET /readyz`
Comprueba conectividad con Firestore y disponibilidad del modelo Gemini 2.5 Flash. Devuelve 503 si alguno falla.

---

## 6. Sistema RAG

### 6.1 Pipeline de recuperación

Para cada mensaje del usuario en modo asesor (o cuando el auditor llama a `invoke_sustainability_expert`), el pipeline ejecuta:

```
Mensaje usuario
    │
    ├─► generate_embedding(embed_model, mensaje)  → vector 384 dims
    │
    ├─► [1] search_user_documents(pinecone, embedding, uid)
    │         Namespace: uid del usuario · min_score: 0.25 · top_k: 6
    │         Documentos PDF subidos permanentemente por el usuario
    │
    ├─► [2] local_store.search(thread_id, embedding)   [legacy, FAISS en RAM]
    │         Documentos subidos en sesión actual (instancias previas a Jun-2025)
    │
    ├─► [3] search_documents(pinecone, embedding, filter?)
    │         Namespace: default (corpus global) · min_score: 0.55 · top_k: 12
    │         Corpus documental: CSRD, CSDDD, NEIS, GRI, OCDE
    │
    └─► [4] get_graph_context(query)  [solo estrategia "hybrid"]
              Neo4j: triples de entidades normativas (relaciones entre conceptos)
              Activado para preguntas operacionales y de recursos
    │
    ▼
Merge: docs_usuario + docs_faiss + docs_corpus + contexto_grafo
    │
    ▼
Mensaje aumentado con fragmentos numerados [1], [2], ... + pregunta
    │
    ▼
Gemini 2.5 Flash → respuesta citando [1], [2], ...
```

### 6.2 Clasificación de preguntas y routing

El sistema clasifica cada pregunta y elige la estrategia de búsqueda:

| Tipo | Señales | Estrategia | Fuentes |
|---|---|---|---|
| `resource` | "qué herramientas", "qué certificaciones", "principales sellos" | hybrid | Pinecone + Neo4j |
| `operational` | "cómo hago", "qué pasos", "para cumplir" | hybrid | Pinecone + Neo4j |
| `conceptual` | "qué es", "cómo funciona", "explica" | semantic | Pinecone únicamente |
| `unknown` | sin señales claras | semantic | Pinecone únicamente |

### 6.3 Filtrado por categoría documental

Para búsquedas en el corpus global, el sistema detecta señales de categoría:

- **GRI** (≥1 término GRI, 0 CSDDD) → filtra `primary_category ∈ {GRI, general}`
- **CSDDD** (≥2 términos CSDDD, 0 GRI) → filtra `primary_category ∈ {CSDDD, general}`
- **Mixto/desconocido** → sin filtro (busca en todo el corpus)

Si el filtrado retorna 0 resultados, reintenta sin filtro.

### 6.4 Budget de contexto

El mensaje aumentado respeta un límite de caracteres para no exceder la ventana de Gemini 2.5 Flash (180.000 caracteres con factor de seguridad 0.90). Los fragmentos se añaden hasta agotar el budget.

### 6.5 Búsqueda en paralelo (context_orchestrator)

Cuando hay chunks en FAISS (legado), la búsqueda en Pinecone y FAISS se ejecuta concurrentemente mediante un `ThreadPoolExecutor` de 4 workers, con timeout de 12 segundos por fuente.

### 6.6 Corpus de Pinecone

El corpus global (namespace por defecto) contiene documentos normativos indexados con `all-MiniLM-L6-v2` (384 dims, cosine similarity). Se ingesta vía `/admin/ingest_document`.

Categorías actuales (`primary_category`): `CSDDD`, `GRI`, `general`.

Los documentos del usuario (namespace = uid) usan el mismo modelo de embeddings.

---

## 7. Modelo de datos

### Firestore

#### `threads/{thread_id}/messages/{auto_id}`
Historial de conversación por thread. Ordenado por `created_at`.

```json
{
  "role": "user" | "model",
  "text": "contenido del mensaje",
  "created_at": "datetime (cliente UTC, offset +1 µs para model)"
}
```

> **Nota:** El mensaje de usuario y el de modelo del mismo turno se escriben en un batch con timestamps `now` y `now + 1µs` respectivamente, garantizando orden estable en `order_by("created_at")`.

---

#### `audit_progress/{thread_id}`
Estado de los bloques de auditoría para un thread.

```json
{
  "uid": "firebase_uid",
  "updated_at": "SERVER_TIMESTAMP",
  "blocks": {
    "block_1": {
      "status": "completed" | "in_progress" | "pending",
      "summary": "resumen de hallazgos",
      "completed_at": "SERVER_TIMESTAMP",
      "updated_at": "SERVER_TIMESTAMP"
    }
  }
}
```

---

#### `threads/{thread_id}`
Propiedad del thread (ownership).

```json
{ "uid": "firebase_uid", "created_at": "SERVER_TIMESTAMP" }
```

---

#### `user_documents/{uid}/files/{doc_id}`
Metadatos de cada PDF subido por el usuario.

```json
{
  "doc_id": "uuid",
  "filename": "informe-sostenibilidad.pdf",
  "chunk_count": 42,
  "size_bytes": 1234567,
  "uploaded_at": "SERVER_TIMESTAMP"
}
```

### Pinecone

#### Namespace por defecto (`""`) — Corpus global
Vectores 384 dims. Metadata:
```json
{
  "text": "fragmento de texto",
  "source": "URL o título",
  "primary_category": "CSDDD" | "GRI" | "general",
  "block_type": "text",
  "entities": [],
  "triplets": [],
  "graph_importance": 0
}
```

#### Namespace usuario (`uid`) — Documentos del usuario
IDs con formato `{doc_id}_{idx:05d}`. Metadata:
```json
{
  "text": "fragmento de texto",
  "source": "nombre-archivo.pdf",
  "doc_id": "uuid",
  "filename": "nombre-archivo.pdf",
  "chunk_idx": 0,
  "primary_category": "uploaded"
}
```

### BigQuery

Tabla `{DATASET}.{TABLE}` — una fila por turno de conversación:

| Campo | Tipo | Descripción |
|---|---|---|
| `timestamp` | TIMESTAMP | Momento del turno (UTC) |
| `thread_id` | STRING | ID del hilo |
| `user_message` | STRING | Mensaje del usuario |
| `assistant_response` | STRING | Respuesta del asistente |
| `endpoint_source` | STRING | `/chat_auditor` o `/chat_assistant` |
| `run_id` | STRING | ID sintético del turno |
| `assistant_name` | STRING | `AuditorGemini` o `AsesorGemini` |
| `user_id` | STRING | Firebase UID |
| `uid` | STRING | Firebase UID (alias) |
| `email` | STRING | Email del usuario |
| `email_verified` | BOOLEAN | Estado de verificación |

### FAISS (en memoria, legado)

Índice `IndexFlatIP` con L2-normalización (equivalente a cosine similarity). Un índice por `thread_id`. Máximo 50 sesiones en RAM con política LRU de evicción. Se elimina al reiniciar la instancia.

---

## 8. Gestión de documentos del usuario

### Ciclo de vida de un documento

```
Usuario selecciona PDF
    │
    ▼
Frontend valida (tipo PDF, ≤20 MB)
    │
    ▼
POST /upload_document (multipart/form-data)
    │
    ├─► Backend comprueba límite 25 documentos en Firestore
    │
    ├─► extract_pdf_chunks(bytes) → lista de fragmentos de texto
    │
    ├─► Para cada fragmento: generate_embedding(embed_model, texto) → vector 384 dims
    │
    ├─► pinecone_index.upsert(vectors, namespace=uid)
    │     IDs: {doc_id}_{00000}, {doc_id}_{00001}, ...
    │     Lotes de 100 vectores
    │
    ├─► Firestore: user_documents/{uid}/files/{doc_id}.set(metadatos)
    │
    └─► Response: {doc_id, filename, chunks_indexed, files: [...lista actualizada...]}
    │
    ▼
Frontend muestra badge "activo en esta sesión" + actualiza lista de archivos
```

### Eliminación de un documento

```
Usuario pulsa ✕ en la lista de archivos
    │
    ▼
DELETE /user_files/{doc_id}
    │
    ├─► Carga metadatos desde Firestore (para obtener chunk_count)
    │
    ├─► Reconstruye IDs: [{doc_id}_00000, ..., {doc_id}_{chunk_count-1:05d}]
    │
    ├─► pinecone_index.delete(ids, namespace=uid)   [lotes de 1000]
    │
    └─► Firestore: user_documents/{uid}/files/{doc_id}.delete()
    │
    ▼
Response: {deleted: true, files: [...lista actualizada...]}
```

### Uso en el RAG

Los documentos del usuario se buscan en cada petición a `/chat_assistant` y también cuando el auditor llama a `invoke_sustainability_expert`. Los resultados del namespace del usuario tienen prioridad y aparecen antes en el contexto del LLM (umbral mínimo de similaridad: 0.25).

---

## 9. Autenticación y seguridad

### Firebase Authentication

- **Login/Registro:** email y contraseña mediante Firebase Auth SDK en el cliente
- **Verificación de email:** obligatoria; el backend rechaza tokens de usuarios no verificados con HTTP 403
- **ID Token:** el cliente obtiene un token JWT firmado por Firebase que envía en cada petición (`Authorization: Bearer <token>`)
- **Verificación en backend:** `firebase_admin.auth.verify_id_token(token)` en cada endpoint protegido

### Propiedad de threads

Cada `thread_id` tiene un propietario (`uid`) registrado en Firestore. El backend valida que el usuario autenticado sea el propietario antes de leer o escribir el progreso o historial del thread:

```python
def ensure_thread_ownership(thread_id, uid):
    doc = threads/{thread_id}.get()
    if doc.exists and doc.uid != uid:
        abort(403)
    elif not doc.exists:
        threads/{thread_id}.set({uid: uid})
```

### Rate limiting

Límites por IP mediante Flask-Limiter:
- Global: 120 peticiones/minuto
- `/chat_auditor`: 12/min, 2/s
- `/upload_document`: 10/min
- `/user_files GET`: 30/min
- `/user_files DELETE`: 20/min
- `/admin/ingest_document`: 10/min

### CORS

Solo los orígenes explícitamente permitidos pueden acceder a la API (configurado mediante variable de entorno `CORS_ORIGINS`). Orígenes por defecto:
- `https://recava-auditor-dev.web.app`
- `https://recava-auditor.web.app`
- `http://localhost:8000`

### Cabeceras de seguridad

Cada respuesta incluye:
- `Cache-Control: no-store`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `X-Request-Id: <uuid>` (para trazabilidad)

---

## 10. Infraestructura y despliegue

### Cloud Run

- **Región:** europe-west1
- **CPU:** 2 vCPU · **RAM:** 4 GB
- **Concurrencia:** 40 peticiones por instancia
- **Timeout:** 600 segundos
- **Autoscaling:** Cloud Run gestiona el escalado automático; el número de instancias varía según la carga

### Docker (multi-stage)

```
Stage 1 (builder):
  python:3.10-slim (digest fijado)
  ├── pip install torch --index-url cpu-only   [~200 MB, capa separada]
  └── pip install -r requirements.txt           [resto de dependencias]

Stage 2 (final):
  python:3.10-slim
  ├── curl (para HEALTHCHECK)
  ├── usuario no-root appuser (uid 1000)
  ├── /opt/venv (copiado del stage 1)
  └── /app/{app.py, src/}

CMD: gunicorn app:app --bind 0.0.0.0:8080 --workers 4 --timeout 120
```

Las capas de Docker están cacheadas mediante BuildKit (`BUILDKIT_INLINE_CACHE=1`). La imagen se guarda en Artifact Registry con la etiqueta `:latest`.

### Cloud Build (CI/CD)

Fichero: `cloudbuild.yaml`. Pasos:

1. **Build-Backend-Image** — `docker build` con caché de BuildKit
2. **Push-Backend-Image** — `docker push` a Artifact Registry
3. **Deploy-Backend-to-Cloud-Run** — `gcloud run deploy` con secretos de Secret Manager
4. **Install-Firebase-Tools** — `npm install firebase-tools`
5. **Install-Admin-Panel-Dependencies** — `npm install` en `public/admin-panel`
6. **Build-Admin-Panel** — `npm run build`
7. **Deploy-Frontend-to-Firebase** — `firebase deploy --only hosting` (con `allowFailure: true`)

Para despliegues solo de frontend (sin rebuild del backend), existe un fichero alternativo `cloudbuild-hosting-only.yaml` que ejecuta solo los pasos 4 y 7 con `--only hosting:chatbot`.

### Secret Manager

Los secretos se montan en Cloud Run como variables de entorno:

| Secret | Descripción |
|---|---|
| `GEMINI_API_KEY` | Clave de la API de Google Gemini |
| `PINECONE_API_KEY` | Clave de la API de Pinecone |
| `PINECONE_INDEX_NAME` | Nombre o URL del índice Pinecone |
| `BIGQUERY_DATASET_ID` | Dataset de BigQuery |
| `BIGQUERY_TABLE_ID` | Tabla de historial en BigQuery |
| `NEO4J_PASSWORD` | Contraseña de Neo4j AuraDB |

Las variables no sensibles (URIs, nombres de modelos) se configuran como `--set-env-vars` en `cloudbuild.yaml`.

---

## 11. Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `GEMINI_API_KEY` | Sí | Clave API de Google GenAI |
| `PINECONE_API_KEY` | Sí* | Clave API de Pinecone |
| `PINECONE_INDEX_NAME` | Sí* | Nombre o URL host del índice Pinecone |
| `BIGQUERY_DATASET_ID` | Sí | Dataset de BigQuery |
| `BIGQUERY_TABLE_ID` | Sí | Tabla de historial |
| `NEO4J_URI` | No | URI de Neo4j AuraDB (GraphRAG desactivado si no se configura) |
| `NEO4J_USERNAME` | No | Usuario de Neo4j (default: `neo4j`) |
| `NEO4J_PASSWORD` | No | Contraseña de Neo4j |
| `NEO4J_DATABASE` | No | Base de datos de Neo4j (default: `neo4j`) |
| `EMBEDDING_MODEL_NAME` | No | Modelo de embeddings (default: `sentence-transformers/all-MiniLM-L6-v2`) |
| `CORS_ORIGINS` | No | Orígenes CORS separados por comas (default: producción + localhost) |
| `FAISS_MAX_SESSIONS` | No | Máximo de sesiones FAISS en RAM (default: 50) |
| `RAG_SEARCH_WORKERS` | No | Workers del ThreadPoolExecutor de RAG (default: 4) |
| `DISABLE_BIGQUERY` | No | `1` para deshabilitar escrituras en BigQuery (desarrollo local) |
| `GOOGLE_APPLICATION_CREDENTIALS` | No | Ruta a credenciales GCP (local) |
| `FIREBASE_AUTH_EMULATOR_HOST` | No | Para uso con Firebase Emulator Suite |
| `PORT` | No | Puerto de escucha (default: 8080) |
| `FLASK_DEBUG` | No | `true` para modo debug de Flask |

*El RAG se desactiva graciosamente si Pinecone no está configurado; el sistema sigue funcionando con respuestas del LLM base.
