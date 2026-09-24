# Entorno de desarrollo local

Levanta el sistema completo en tu máquina: backend, widget de chat y —si tienes Java— emuladores de Firebase, de modo que puedas trabajar **sin tocar los datos reales de usuarios ni de conversaciones**.

## Puesta en marcha

```bash
./scripts/dev.sh setup     # crea .venv, instala dependencias, copia .env.example a .env
# rellena GEMINI_API_KEY en .env (obligatoria)
./scripts/dev.sh check     # dice qué falta
```

Para sacar las claves del entorno de desarrollo:

```bash
gcloud secrets versions access latest --secret=GEMINI_API_KEY   --project=recava-auditor-dev
gcloud secrets versions access latest --secret=PINECONE_API_KEY --project=recava-auditor-dev
```

Luego, tres terminales:

```bash
./scripts/dev.sh emulators   # Auth 9099 · Firestore 8085 · interfaz 4000
./scripts/dev.sh api         # backend en http://localhost:8080
./scripts/dev.sh web         # widget  en http://localhost:8000
```

Abre **http://localhost:8000**. El widget detecta que está en `localhost` y apunta solo al backend local y al emulador de Auth: no habla con Cloud Run.

## Cómo encajan los puertos

| Puerto | Qué | Por qué ese |
|---|---|---|
| 8000 | Widget de chat | Es el único `localhost` en la lista blanca de CORS del backend |
| 8080 | Backend | El widget tiene esa URL cableada para local; es también el puerto de producción |
| 8085 | Emulador de Firestore | Estaba en el 8080 y **chocaba con el backend**; se movió aquí |
| 9099 | Emulador de Auth | El widget lo tiene cableado para local |
| 4000 | Interfaz de los emuladores | Para inspeccionar Firestore y los usuarios de prueba |
| 3000 | Panel de administración | `./scripts/dev.sh panel` |

## Con emuladores o sin ellos

`USE_EMULATORS` en `.env` decide con qué hablan Auth y Firestore:

- **`1` (recomendado)** — emuladores. Los usuarios y las conversaciones que crees viven solo en tu máquina y desaparecen al pararlos. Puedes crear usuarios de prueba desde la interfaz del emulador en http://localhost:4000 sin verificación de correo real.
- **`0`** — Firestore real de `recava-auditor-dev`. Los hilos y el progreso de auditoría que generes **son datos de verdad**, mezclados con los de los usuarios. Útil para reproducir un problema concreto; mal sitio para experimentar.

Los emuladores necesitan Java, que ahora mismo no está instalado en esta máquina:

```bash
brew install --cask temurin
```

Sin Java tendrás que trabajar con `USE_EMULATORS=0`, con la precaución que eso implica.

## Qué funciona sin claves

| Falta | Consecuencia |
|---|---|
| `GEMINI_API_KEY` | **El backend no arranca.** Es la única imprescindible |
| `PINECONE_API_KEY` | Arranca, pero sin recuperación: el asistente responde sin corpus y sin citas |
| `RAG_NORMATIVE_GRAPH` vacío | Arranca sin grafo normativo: sin expansión por referencias ni resolución de «artículo N» |
| Credenciales de aplicación | BigQuery y Firestore real fallan al inicializarse. Con `USE_EMULATORS=1` y `DISABLE_BIGQUERY=1` no hacen falta para el día a día |

El modelo de embeddings (`intfloat/multilingual-e5-small`, ~470 MB) se descarga de Hugging Face la primera vez y queda en caché. En la imagen de Docker ya va incluido.

**Índice, modelo y umbral van juntos.** El `.env.example` trae la configuración v2
(`RAG_INDEX_NAME=recavai-corpus-v2`, `EMBEDDING_MODEL_NAME=intfloat/multilingual-e5-small`,
`RAG_MIN_SCORE=0.841`, `RAG_NORMATIVE_GRAPH=data/normative_graph.json`). Un `.env` anterior
al 23/09/2026 apunta al índice v1 con MiniLM: funciona, pero no es lo que corre en producción.

## Pruebas

```bash
./scripts/dev.sh test
```

Ejecuta, **sin red, sin claves y sin Firestore**:

- [`scripts/test_auditor.py`](../scripts/test_auditor.py) — 46 comprobaciones del auditor (doble de Firestore en memoria y asesor simulado): que un bloque no pueda cerrarse a medias, que las preguntas de perfil no consulten al asesor y las evaluables sí, que un fallo del asesor no impida registrar la respuesta, que el bloque activo siga a donde se está trabajando y que el verificador busque con la sustancia de la respuesta, no con sus instrucciones.
- [`scripts/test_agent.py`](../scripts/test_agent.py) — 91 comprobaciones del agente asesor: carga y composición de las habilidades, cada control (datos críticos, citas inexistentes, citas al fragmento equivocado, normas y artículos inexistentes, contexto vacío, abstención) y el bucle del harness con un modelo simulado (reparación que funciona, reparación que no, nota final); el contexto adaptativo (ampliación a la unidad, ventana de vecinos, presupuesto por nivel, orden de lectura, segunda pasada, fuentes citadas) y el planificador.

Ejecútalo antes de cada despliegue. Tarda unos segundos.

Otras comprobaciones útiles:

```bash
# Nivel de autenticación de cada endpoint
grep -n "@app.route\|require_.*_or_403()" app.py

# Solo búsqueda sobre la batería de 60 preguntas (sin llamadas a Gemini, ~1 min)
python scripts/eval_battery.py run --name prueba --index recavai-corpus-v2 \
  --model intfloat/multilingual-e5-small --min-score 0.841 \
  --graph data/normative_graph.json --no-generate

# Evaluación completa (60 generaciones + 60 juicios: cuesta crédito de Gemini)
python scripts/eval_battery.py run --name prueba ... --judge-model gemini-3.5-flash
python scripts/eval_battery.py compare H4 prueba
```

Ver [`../benchmarks/README.md`](../benchmarks/README.md). La clave de Gemini local gasta
del mismo crédito que producción: antes de una evaluación completa, lee
[`DESPLIEGUE.md` §5.1](DESPLIEGUE.md).

## Trabajo habitual

**Cambiar preguntas del auditor** → solo en [`src/audit_catalog.py`](../src/audit_catalog.py). El prompt, el progreso y la validación de cierre se generan de ahí. No añadas preguntas en el texto del prompt: dejarían de contar para el cierre.

**Cambiar el comportamiento del auditor** → reglas en `AUDITOR_SYSTEM_PROMPT` ([`src/assistant_instructions.py`](../src/assistant_instructions.py)). Si la regla debe cumplirse siempre, no la dejes en el prompt: impleméntala en el servidor, como el control de cobertura de `complete_audit_block`.

**Cambiar el comportamiento del asesor** → sus habilidades, en [`src/agent/skills/`](../src/agent/skills/). Una por fichero, con versión: súbela al cambiar el texto. Las reglas que deban cumplirse siempre no van en una habilidad sino en un control de [`src/agent/guardrails.py`](../src/agent/guardrails.py). Ver [`AGENTE.md`](AGENTE.md).

**Cambiar la recuperación** → `retrieve_documents` en [`src/gemini_service.py`](../src/gemini_service.py) y [`src/rag_service.py`](../src/rag_service.py). Mide antes y después con la batería; las constantes están en `SPEC.md` §7.1.

**Tocar el índice del corpus** → troceado en [`src/chunking_v2.py`](../src/chunking_v2.py); se construye en local y se carga en un índice **nuevo**, nunca sobre el que está sirviendo:

```bash
python scripts/extract_corpus_text.py --corpus "<carpeta>" --out .cache/corpus_txt
python scripts/build_memory_index.py --chunker-v2 .cache/corpus_txt \
  --reembed intfloat/multilingual-e5-small --out .cache/idx_nuevo.npz
python scripts/build_normative_graph.py      # los ids de los vectores cambian: regenerar
python scripts/build_norm_registry.py
python scripts/calibrate_threshold.py ...    # umbral para el índice nuevo
python scripts/upsert_index_v2.py --npz .cache/idx_nuevo.npz --index <índice-nuevo>
```

Los ids son por documento (`01-csddd-069c7ea5-0037`), así que reindexar un documento no
toca los demás.

## Avisos

- `.env` nunca se commitea. Contiene claves reales.
- Con `USE_EMULATORS=0` estás escribiendo en la base de datos de los usuarios.
- El panel de administración (`./scripts/dev.sh panel`) llama al Cloud Run de desarrollo, no a tu backend local, porque tiene la URL cableada en `UserManagement.js`.
- Las reglas de Firestore del repositorio deniegan todo acceso directo (`if false`), igual que las publicadas: el backend usa el Admin SDK, que no está sujeto a ellas, también contra el emulador.

## Documentos relacionados

| Documento | Para qué |
|---|---|
| [`DESPLIEGUE.md`](DESPLIEGUE.md) | Subir cambios al entorno vivo |
| [`SPEC.md`](SPEC.md) | Especificación normativa del sistema |
| [`documentacion.html`](documentacion.html) | Explicación del sistema para personas |
| [`AGENTE.md`](AGENTE.md) | Habilidades, controles y harness del asesor |
| [`../benchmarks/README.md`](../benchmarks/README.md) | Benchmark y experimento de la base de conocimiento |
