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
| `NEO4J_PASSWORD` | Arranca; se omiten las consultas al grafo (solo afecta a preguntas operativas) |
| Credenciales de aplicación | BigQuery y Firestore real fallan al inicializarse. Con `USE_EMULATORS=1` y `DISABLE_BIGQUERY=1` no hacen falta para el día a día |

El modelo de embeddings se descarga de Hugging Face la primera vez (~90 MB) y queda en caché.

## Pruebas

```bash
./scripts/dev.sh test
```

Ejecuta [`scripts/test_auditor.py`](../scripts/test_auditor.py): 33 comprobaciones de la lógica del auditor **sin red, sin claves y sin Firestore** (usa un doble en memoria y un asesor simulado). Cubre lo que se rompió en producción: que un bloque no pueda cerrarse a medias, que las preguntas de perfil no consulten al asesor y las evaluables sí, que un fallo del asesor no impida registrar la respuesta, y que el bloque activo siga a donde se está trabajando.

Ejecútalo antes de cada despliegue. Tarda unos segundos.

Otras comprobaciones útiles:

```bash
# Nivel de autenticación de cada endpoint
grep -n "@app.route\|require_.*_or_403()" app.py

# Calidad de la recuperación frente a la línea base
set -a; source .env; set +a
python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl \
  --embedding-model sentence-transformers/all-MiniLM-L6-v2 \
  --baseline benchmarks/baseline.jsonl --out results/run.jsonl
```

## Trabajo habitual

**Cambiar preguntas del auditor** → solo en [`src/audit_catalog.py`](../src/audit_catalog.py). El prompt, el progreso y la validación de cierre se generan de ahí. No añadas preguntas en el texto del prompt: dejarían de contar para el cierre.

**Cambiar el comportamiento del auditor** → reglas en `AUDITOR_SYSTEM_PROMPT` ([`src/assistant_instructions.py`](../src/assistant_instructions.py)). Si la regla debe cumplirse siempre, no la dejes en el prompt: impleméntala en el servidor, como el control de cobertura de `complete_audit_block`.

**Cambiar la recuperación** → [`src/rag_service.py`](../src/rag_service.py). Mide antes y después con el benchmark; las constantes están en `SPEC.md` §7.1.

**Tocar el índice del corpus** → `src/corpus_pipeline.py` es un proceso por lotes que reindexa por completo. Úsalo siempre contra un *namespace* de pruebas, nunca contra el de producción sin pasar antes el benchmark.

## Avisos

- `.env` nunca se commitea. Contiene claves reales.
- Con `USE_EMULATORS=0` estás escribiendo en la base de datos de los usuarios.
- El panel de administración (`./scripts/dev.sh panel`) llama al Cloud Run de desarrollo, no a tu backend local, porque tiene la URL cableada en `UserManagement.js`.
- Las reglas de Firestore del repositorio permiten lectura y escritura a cualquiera (`DEBT-1` en `SPEC.md`). Sirven para el emulador; no deben desplegarse.

## Documentos relacionados

| Documento | Para qué |
|---|---|
| [`DESPLIEGUE.md`](DESPLIEGUE.md) | Subir cambios al entorno vivo |
| [`SPEC.md`](SPEC.md) | Especificación normativa del sistema |
| [`documentacion.html`](documentacion.html) | Explicación del sistema para personas |
| [`../benchmarks/README.md`](../benchmarks/README.md) | Benchmark y experimento de la base de conocimiento |
