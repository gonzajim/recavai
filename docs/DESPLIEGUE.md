# Despliegue

Estado comprobado el 19 de septiembre de 2026 contra la infraestructura real; actualizado el 23 de septiembre con el índice v2, el grafo en memoria y el agente asesor.

## 1. Qué entornos existen de verdad

| Entorno | Proyecto GCP | Cloud Run | Hosting | Estado |
|---|---|---|---|---|
| **dev** | `recava-auditor-dev` (370417116045) | `orchestrator-dev` | `recava-auditor-dev` · `recava-auditor-dev-panel` | **Vivo. Es lo que usan hoy los usuarios.** |
| prod | `recava-auditor-prod` (69664655415) | — | — | **No operativo**: sin facturación habilitada, sin Secret Manager |

Conviene decirlo sin rodeos: **hoy no hay dos entornos, hay uno**. Lo que se llama «desarrollo» es el sistema en producción. Cualquier despliegue afecta a usuarios reales, y por eso el procedimiento por defecto es un despliegue canario.

En `recava-auditor-dev` el único servicio del orquestador es **`orchestrator-dev`**, con dos URL equivalentes: `https://orchestrator-dev-370417116045.europe-west1.run.app` (formato nuevo, la que tienen cableada los frontales) y `https://orchestrator-dev-ks5tq3h2xq-ew.a.run.app` (formato antiguo). Los otros dos servicios de Cloud Run que aparecen ahí, `getchathistory` y `updateexpertresponse`, son las Cloud Functions de `functions/index.js`.

## 1.1 Convención de nombres

Cloud Run publica cada servicio en dos URL, y una de ellas incluye el número de proyecto:

```
https://{servicio}-{número-de-proyecto}.{región}.run.app     ← formato nuevo
https://{servicio}-{hash}-{región}.a.run.app                 ← formato antiguo
```

De ahí salió la confusión que se limpió el 19 de septiembre de 2026: alguien creó servicios **cuyo nombre era una URL copiada**, de modo que `orchestrator-dev-370417116045` parecía el servicio real cuando en realidad el real era `orchestrator-dev` y aquello era un duplicado vacío.

Reglas para no repetirlo:

- El nombre del servicio lleva **siempre** el sufijo del entorno y **nunca** el número de proyecto: `orchestrator-dev`, `orchestrator-prod`. Un `orchestrator` a secas es ambiguo y está prohibido.
- Al copiar una URL para configurar un frontal, se copia como URL, jamás como nombre de servicio.
- Antes de desplegar, `./scripts/deploy.sh status ENTORNO` dice qué servicio y qué revisión se van a tocar.

## 1.2 Qué se retiró en la limpieza del 19/09/2026

| Recurso | Por qué |
|---|---|
| Cloud Run `orchestrator-dev-370417116045` (dev) | Duplicado por nombre copiado de una URL. Revisión única de mayo de 2026, sin referencias en el código |
| Cloud Run `orchestrator` (dev) | Fósil anterior a la migración a Gemini: montaba `OPENAI_API_KEY`, política IAM vacía, respondía 403 |
| Secretos `OPENAI_API_KEY`, `ASISTENTE_ID`, `AUDITOR_ID`, `ORCHESTRATOR_ASSISTANT_ID` | De la etapa con OpenAI Assistants. Ningún módulo del código los lee |
| 9 ramas remotas | `agent-builder` `c082b71`, `antigravity` `8aef5f2`, `auditorv2` `cca3f1c`, `dev-audit-progress` `8003494`, `dev-email` `e4e05b5`, `dev-historic` `ea9812c`, `dev-stable` `cae064e`, `fix/options-500` `9026010`, `dev-gemini-migration` `d230891`. Recuperables con `git push origin SHA:refs/heads/NOMBRE` |
| `temp.js`, `bubble_tmp.bin`, `firestore-debug.log`, `test.orchestrator.http`, `test_api.ps1` | Ficheros de trabajo en la raíz del repositorio. Siguen en el historial de git |

Borrar `OPENAI_API_KEY` de Secret Manager **no revoca la clave en OpenAI**. Si sigue activa, hay que revocarla en `platform.openai.com`.

El proyecto `recava-auditor-prod` conserva dos servicios muertos (`orchestrator-520199812528` y `recava-auditor-prod`) que no se pudieron tocar porque el proyecto no tiene facturación habilitada. Habrá que borrarlos al activarlo.

## 2. Procedimiento normal

Tres pasos. El primero no puede romper nada porque la revisión nueva no recibe tráfico.

```bash
./scripts/deploy.sh canary dev      # 1. construye y despliega SIN tráfico
                                    #    devuelve una URL etiquetada para probar
./scripts/deploy.sh promote dev     # 2. envía el 100% del tráfico a esa revisión
./scripts/deploy.sh hosting dev     # 3. publica widget de chat y panel
```

Y si algo va mal:

```bash
./scripts/deploy.sh rollback dev                  # lista las revisiones
./scripts/deploy.sh rollback dev orchestrator-dev-00051-mlh
./scripts/deploy.sh status dev                    # tráfico y salud
```

### Qué probar en la URL canaria antes de promover

1. `GET /health` responde.
2. En los registros de arranque de la revisión: `Grafo normativo cargado`, `Almacén de unidades cargado: 381 unidades, 8127 fragmentos`, `Pinecone index connected via 'recavai-corpus-v2'` y `SentenceTransformer 'intfloat/multilingual-e5-small' loaded`. El servicio carga las habilidades, el grafo y el almacén al arrancar: si faltan las habilidades, la instancia no arranca; si el grafo o el almacén están activados y no cargan, sale un ERROR en el arranque y el servicio sigue sin ellos.
3. Asesor: «¿Qué establece el artículo 10 de la CSDDD?» cita el artículo 10 con su página y lo recorre entero; «¿Qué dice la Ley 11/2018?» responde que no la tiene en la documentación. En el registro, cada turno lleva `"nivel"` y `"tokens_contexto"` (`./scripts/logs.sh agente`).
4. Modo auditor: recorre las seis preguntas obligatorias del bloque 1 sin repetir ninguna.
5. En el bloque 4, responder *«tenemos buzón ético pero solo para empleados»* debe producir un aviso de brecha con la norma (art. 14 de la CSDDD) y la recomendación.
6. El bloque no se cierra mientras queden preguntas pendientes.

Además, antes de cualquier cambio en la recuperación o en las habilidades, la batería de
evaluación (`benchmarks/README.md`) contra la configuración anterior. Es la regla `QA-5`
del SPEC.

### Índice, modelo y umbral se fijan por revisión

El índice de Pinecone, el modelo de *embeddings* y el umbral de similitud **van juntos**:
un índice construido con un modelo solo se puede consultar con ese modelo, y el umbral
depende de la escala de similitudes del modelo. Por eso los tres se fijan como variables
de entorno de cada revisión (`RAG_INDEX_NAME`, `EMBEDDING_MODEL_NAME`, `RAG_MIN_SCORE`) y
no en el secreto `PINECONE_INDEX_NAME`, que las revisiones leen como «latest»: si se
cambiara el índice en el secreto, **una vuelta atrás a una revisión anterior leería el
índice nuevo con el modelo viejo** y la búsqueda devolvería basura sin dar error.

| Configuración | Índice | Modelo | Umbral | Revisiones |
|---|---|---|---|---|
| v1 | `uclm-corpus-roma` (9.176 vectores) | `all-MiniLM-L6-v2` | 0,55 (por defecto) | hasta `00056-loh` |
| v2 | `recavai-corpus-v2` (8.127 vectores) | `intfloat/multilingual-e5-small` | 0,841 | desde `00060-rax` (23/09/2026) |

Los dos índices tienen activada la protección contra borrado. El v1 se conserva para
poder volver atrás.

### Contexto adaptativo

Desde el 24/09/2026 ([PLAN_CONTEXTO.md](PLAN_CONTEXTO.md)) cada revisión fija además
cómo se construye el contexto. El almacén de unidades (`data/unidades.json.gz`) se generó
desde el mismo índice v2 y comparte sus ids: **con el índice v1 hay que vaciar
`_RAG_UNITS`**.

| Sustitución | Variable | Valor | Para volver al comportamiento anterior |
|---|---|---|---|
| `_RAG_UNITS` | `RAG_UNIT_STORE` | `/app/data/unidades.json.gz` | vacío: un bloque por fragmento, 6 como máximo |
| `_RAG_MAX_LEVEL` | `RAG_CONTEXT_MAX_LEVEL` | ver `cloudbuild.yaml` | `M` quita el nivel L y la segunda pasada |
| `_RAG_THINKING` | `RAG_THINKING_BUDGET` | `0` | vacío: razonamiento dinámico (2-4 veces más lento con contexto grande) |
| `_RAG_PLANNER` | `RAG_PLANNER` | `0` | — (no se ha activado nunca) |

Se pueden cambiar sin reconstruir la imagen:
`gcloud run services update orchestrator-dev --region europe-west1 --update-env-vars RAG_CONTEXT_MAX_LEVEL=M`
(crea una revisión nueva con el 100 % del tráfico: hacerlo fuera de horas de uso).

### Capacidad

e5-small ocupa unos 1.000 MB por proceso (MiniLM, 480). Con 4 procesos no cabía en los
4 GiB del servicio, así que la imagen arranca **2 procesos × 8 hilos** (`gthread`): 16
peticiones simultáneas, frente a 4 con los 4 procesos anteriores, porque casi todo el
tiempo de una petición es espera a Gemini. `--concurrency=16` en Cloud Run para que
escale en instancias en vez de encolar.

El modelo va **dentro de la imagen** (`HF_HOME=/opt/hf`, `HF_HUB_OFFLINE=1`): no se
descarga de Hugging Face al arrancar.

**Limitación conocida del arranque.** Cloud Run da la revisión por lista en cuanto
gunicorn abre el puerto, antes de que Python termine de importar. Una instancia recién
creada sin tráfico tiene la CPU estrangulada y la carga puede tardar minutos; la primera
petición real tarda unos 35-45 s. Se mitigaría con una sonda de arranque HTTP contra
`/health` o con `--min-instances=1` (con coste).

### Por qué el hosting va después y aparte

Si se publicase el frontend a la vez que se despliega la revisión canaria, el 100 % de los usuarios recibiría el frontend nuevo mientras el backend nuevo todavía no atiende tráfico. Quedarían descompasados. Por eso `cloudbuild.yaml` omite el paso de hosting en modo canario.

## 3. Si algún día se levanta producción de verdad

`recava-auditor-prod` existe pero está vacío. Para usarlo hay que, por este orden:

1. Habilitar facturación en el proyecto.
2. Crear los secretos: `GEMINI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID`. (Neo4j se retiró el 23/09/2026.)
3. Crear el índice de Pinecone de producción e indexarlo con el troceado v2 (`scripts/build_memory_index.py --chunker-v2` y `scripts/upsert_index_v2.py`) — o decidir explícitamente que ambos entornos comparten índice. El plan gratuito de Pinecone solo admite `us-east-1`.
4. Crear el dataset y la tabla de BigQuery.
5. Crear el servicio Cloud Run `orchestrator-prod` y los sitios de Firebase Hosting, y corregir el alias: `.firebaserc` apuntaba el alias `prod` al proyecto `recava-auditor`, **que no existe**; ahora apunta a `recava-auditor-prod`.
6. Configurar Firebase Authentication con su propio conjunto de usuarios.
7. Añadir los orígenes de producción a `CORS_ORIGINS`.
8. Publicar `firestore.rules` del repositorio (`allow read, write: if false`: todo el acceso va por el Admin SDK del orquestador). Hasta el 23/09/2026 el fichero decía `if true`, aunque lo publicado en dev era `if false`.

Hasta que eso exista, `./scripts/deploy.sh canary prod` pide confirmación y avisa de que el entorno no está operativo.

## 4. Despliegue automático

No hay ninguno. Existía un `trigger.yaml` que describía un trigger disparado por *push* a `main` sobre un proyecto GCP que ya no es accesible: llevaba muerto meses y se retiró el 19/09/2026. Los despliegues de los últimos meses se han hecho a mano.

Para restaurarlo, en un proyecto que sí exista:

```bash
gcloud builds triggers create github \
  --project=recava-auditor-dev --region=europe-west1 \
  --name=orchestrator-dev-canary \
  --repo-name=recavai --repo-owner=gonzajim \
  --branch-pattern='^main$' --build-config=cloudbuild.yaml \
  --substitutions=_ENV=dev,_SERVICE_NAME=orchestrator-dev,_FIREBASE_PROJECT_ALIAS=dev,_TRAFFIC=canary
```

Obsérvese `_TRAFFIC=canary`: un *push* a `main` construiría y dejaría la revisión preparada, pero **la promoción seguiría siendo un acto humano**. Con un solo entorno vivo, desplegar automáticamente a los usuarios lo que se acaba de mergear no es aceptable.

## 5. Consideraciones de datos al desplegar los cambios del auditor

- Las auditorías **en curso** no tienen los campos `answered` ni `active_block_id`, así que aparecerán con cobertura 0/N y el auditor volverá a preguntar lo ya respondido en esos hilos. Si hay alguna a medias que importe, conviene terminarla antes de promover.
- Cada respuesta evaluable añade una llamada a Gemini con recuperación: una auditoría completa pasa de unas 40 llamadas a unas 70. Sube la latencia por turno y el coste por auditoría.
- El widget publicado es anterior a HEAD: al publicar hosting suben también el aviso de fallo de inicio de sesión y la gestión de documentos persistentes, no solo los cambios del auditor.

## 5.1 Coste de Gemini y crédito compartido

La clave de Gemini de producción y la del `.env` local son distintas pero **gastan del
mismo crédito prepago de AI Studio**. El 23/09/2026 una evaluación con ~1.100 llamadas lo
agotó y producción respondió 402 hasta que se recargó. Para evitarlo:

- Activar la recarga automática del crédito en AI Studio (facturación del proyecto).
- Usar para evaluaciones una clave de **otro proyecto**, con su propio saldo.
- Antes de lanzar una evaluación grande, calcular las llamadas: una pasada de la batería
  son 60 generaciones + 60 juicios; una comparación por parejas, 60 juicios más.
- `gemini-3.1-pro-preview` tiene además un límite duro de 250 peticiones al día por modelo.

## 6. Variables y secretos

Los secretos se inyectan desde Secret Manager en el despliegue (`--update-secrets`), nunca se escriben en el repositorio. Las variables no sensibles van en `--set-env-vars` desde las sustituciones de `cloudbuild.yaml`. Para el entorno local, ver [`DESARROLLO.md`](DESARROLLO.md).

| Variable | Origen | Nota |
|---|---|---|
| `GEMINI_API_KEY` | Secret Manager | Obligatoria: sin ella el servicio no arranca |
| `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | Secret Manager | Sin ellas el RAG queda desactivado. El secreto del índice solo se usa si falta `RAG_INDEX_NAME` |
| `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID` | Secret Manager | Obligatorias al arrancar |
| `RAG_INDEX_NAME` | Sustitución `_RAG_INDEX` | Índice de Pinecone de esta revisión (`recavai-corpus-v2`) |
| `EMBEDDING_MODEL_NAME` | Sustitución `_EMBEDDING_MODEL` | **Debe coincidir con el modelo que construyó el índice**; también se hornea en la imagen |
| `RAG_MIN_SCORE` | Sustitución `_RAG_MIN_SCORE` | Umbral de similitud calibrado para el modelo (0,841 con e5-small) |
| `RAG_NORMATIVE_GRAPH` | Sustitución `_RAG_GRAPH` | Ruta del grafo en la imagen; vacío lo desactiva |
| `RAG_NORM_REGISTRY` | Opcional | Ruta alternativa del registro de normas (por defecto `data/normas_corpus.json`) |
| `RAG_UNIT_STORE` | Sustitución `_RAG_UNITS` | Almacén de unidades del contexto adaptativo; vacío lo desactiva |
| `RAG_CONTEXT_MAX_LEVEL` | Sustitución `_RAG_MAX_LEVEL` | Nivel de contexto máximo: `S`, `M` o `L` |
| `RAG_THINKING_BUDGET` | Sustitución `_RAG_THINKING` | Razonamiento interno de Gemini; `0` lo quita, vacío = dinámico |
| `RAG_PLANNER` | Sustitución `_RAG_PLANNER` | `1` activa el planificador de búsqueda |
| `CORS_ORIGINS` | Opcional | Si falta, se usa la lista segura por defecto |

`cloudbuild.yaml` retira el secreto `NEO4J_PASSWORD` de la revisión (`--remove-secrets`) y
sustituye todas las variables normales (`--set-env-vars`), así que las `NEO4J_*` de las
revisiones antiguas no pasan a las nuevas.

## 7. Diagnosticar un problema con los logs

```bash
./scripts/logs.sh resumen        # peticiones, códigos, latencias y errores de 24 h
./scripts/logs.sh errors         # solo errores y trazas
./scripts/logs.sh tail           # en vivo
./scripts/logs.sh auditor        # registros, cierres y veredictos del auditor
./scripts/logs.sh rag            # enrutado, recuperación y grafo normativo
./scripts/logs.sh agente         # turnos del asesor: avisos, reparaciones, respuestas anotadas
./scripts/logs.sh slow 5000      # peticiones de más de 5 s
./scripts/logs.sh req  7e3287bd68d1                              # una petición
./scripts/logs.sh thread d839f31f-4e8f-4583-9d2c-b72623dd13b5    # una conversación
./scripts/logs.sh consola        # enlace al visor web con el filtro puesto
```

### Cómo seguir el rastro de un problema concreto

Cada respuesta del backend lleva la cabecera **`X-Request-Id`**. Si alguien reporta un fallo, pídele esa cabecera (se ve en la pestaña Red de las herramientas de desarrollo) y con `./scripts/logs.sh req <id>` sale todo lo que ocurrió en esa petición.

Si no la tienes, sirve el `thread_id` de la conversación, que aparece en la propia respuesta de la API y en el panel de experto. `./scripts/logs.sh thread <id>` reconstruye la conversación entera: qué turnos hubo, qué preguntas se registraron, qué veredictos se emitieron y dónde falló.

### Qué significan las líneas del auditor

```
record_block_answers: thread=abc block=block_1 +3 -> 3/6 verdict=—
```
Se registraron 3 respuestas, el bloque va por 3 de 6 obligatorias, y no hubo veredicto porque las preguntas del bloque 1 son de perfil y no se verifican. Un `verdict=no cumple` indica que el asesor detectó una brecha.

```
complete_audit_block RECHAZADO: thread=abc block=block_1 3/6
```
El modelo intentó cerrar un bloque a medias y el servidor se lo impidió. **Esto es el sistema funcionando**, no un error.

### Qué significan las líneas del asesor

```
{"evt": "agent_turn", "task": "asesor", "skills_version": "f4faec66b2", "avisos": ["referencia_desconocida"],
 "violaciones_borrador": 2, "violaciones_final": 0, "reparada": true, "anotada": false, "ms": {...}}
```
Una por turno del asesor (chat, herramienta del auditor o verificación). `avisos`: la
pregunta nombraba una norma o un artículo que no existe, o no se recuperó nada.
`reparada`: el control de salida encontró datos sin respaldo y se reescribió la respuesta.
`anotada`: **se sirvió con una nota de datos no verificados** — es la que conviene revisar.
`skills_version` identifica las instrucciones exactas con que se generó (ver `docs/AGENTE.md`).

### Respuestas vacías de Gemini

Cuando Gemini devuelve una respuesta sin texto, el log registra la causa (`finish_reason`,
tokens de razonamiento, bloqueo de seguridad) y el servicio reintenta una vez sin
razonamiento interno. Buscar `Respuesta vacía de Gemini`.
