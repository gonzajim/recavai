# Despliegue

Estado comprobado el 19 de septiembre de 2026 contra la infraestructura real.

## 1. Qué entornos existen de verdad

| Entorno | Proyecto GCP | Cloud Run | Hosting | Estado |
|---|---|---|---|---|
| **dev** | `recava-auditor-dev` (370417116045) | `orchestrator-dev` | `recava-auditor-dev` · `recava-auditor-dev-panel` | **Vivo. Es lo que usan hoy los usuarios.** |
| prod | `recava-auditor-prod` (69664655415) | — | — | **No operativo**: sin facturación habilitada, sin Secret Manager |

Conviene decirlo sin rodeos: **hoy no hay dos entornos, hay uno**. Lo que se llama «desarrollo» es el sistema en producción. Cualquier despliegue afecta a usuarios reales, y por eso el procedimiento por defecto es un despliegue canario.

En `recava-auditor-dev` conviven tres servicios Cloud Run con nombres parecidos. El que sirve el tráfico es **`orchestrator-dev`** (URL `https://orchestrator-dev-370417116045.europe-west1.run.app`, la que tiene cableada el panel de administración). Los otros dos —`orchestrator` y `orchestrator-dev-370417116045`— son restos de despliegues antiguos y no reciben tráfico; conviene borrarlos algún día para no desplegar sobre el equivocado por error.

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
2. Modo auditor: recorre las seis preguntas obligatorias del bloque 1 sin repetir ninguna.
3. En el bloque 4, responder *«tenemos buzón ético pero solo para empleados»* debe producir un aviso de brecha con la norma y la recomendación.
4. El bloque no se cierra mientras queden preguntas pendientes.

### Por qué el hosting va después y aparte

Si se publicase el frontend a la vez que se despliega la revisión canaria, el 100 % de los usuarios recibiría el frontend nuevo mientras el backend nuevo todavía no atiende tráfico. Quedarían descompasados. Por eso `cloudbuild.yaml` omite el paso de hosting en modo canario.

## 3. Si algún día se levanta producción de verdad

`recava-auditor-prod` existe pero está vacío. Para usarlo hay que, por este orden:

1. Habilitar facturación en el proyecto.
2. Crear los secretos: `GEMINI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID`, `NEO4J_PASSWORD`.
3. Crear el índice de Pinecone de producción e indexarlo con `src/corpus_pipeline.py` — o decidir explícitamente que ambos entornos comparten índice.
4. Crear el dataset y la tabla de BigQuery.
5. Crear los sitios de Firebase Hosting y corregir el alias: `.firebaserc` apuntaba el alias `prod` al proyecto `recava-auditor`, **que no existe**; ahora apunta a `recava-auditor-prod`.
6. Configurar Firebase Authentication con su propio conjunto de usuarios.
7. Añadir los orígenes de producción a `CORS_ORIGINS`.
8. Revisar `firestore.rules`: las reglas actuales conceden lectura y escritura a cualquiera (ver `DEBT-1` en `SPEC.md`). No deben llegar a producción tal cual.

Hasta que eso exista, `./scripts/deploy.sh canary prod` pide confirmación y avisa de que el entorno no está operativo.

## 4. Despliegue automático

No hay ninguno, y conviene saberlo: [`trigger.yaml`](../trigger.yaml) describe un trigger que dispara con *push* a `main` sobre el proyecto `recava-agent-audit`, **que ya no es accesible**. Ese trigger está muerto desde hace tiempo; los despliegues de los últimos meses se han hecho a mano.

Para restaurarlo, en un proyecto que sí exista:

```bash
gcloud builds triggers create github \
  --project=recava-auditor-dev --region=europe-west1 \
  --name=orchestrator-dev-canary \
  --repo-name=recava-agent-audit --repo-owner=gonzajim \
  --branch-pattern='^main$' --build-config=cloudbuild.yaml \
  --substitutions=_ENV=dev,_SERVICE_NAME=orchestrator-dev,_FIREBASE_PROJECT_ALIAS=dev,_TRAFFIC=canary
```

Obsérvese `_TRAFFIC=canary`: un *push* a `main` construiría y dejaría la revisión preparada, pero **la promoción seguiría siendo un acto humano**. Con un solo entorno vivo, desplegar automáticamente a los usuarios lo que se acaba de mergear no es aceptable.

## 5. Consideraciones de datos al desplegar los cambios del auditor

- Las auditorías **en curso** no tienen los campos `answered` ni `active_block_id`, así que aparecerán con cobertura 0/N y el auditor volverá a preguntar lo ya respondido en esos hilos. Si hay alguna a medias que importe, conviene terminarla antes de promover.
- Cada respuesta evaluable añade una llamada a Gemini con recuperación: una auditoría completa pasa de unas 40 llamadas a unas 70. Sube la latencia por turno y el coste por auditoría.
- El widget publicado es anterior a HEAD: al publicar hosting suben también el aviso de fallo de inicio de sesión y la gestión de documentos persistentes, no solo los cambios del auditor.

## 6. Variables y secretos

Los secretos se inyectan desde Secret Manager en el despliegue (`--update-secrets`), nunca se escriben en el repositorio. Las variables no sensibles van en `--set-env-vars` desde las sustituciones de `cloudbuild.yaml`. Para el entorno local, ver [`DESARROLLO.md`](DESARROLLO.md).

| Variable | Origen | Nota |
|---|---|---|
| `GEMINI_API_KEY` | Secret Manager | Obligatoria: sin ella el servicio no arranca |
| `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` | Secret Manager | Sin ellas el RAG queda desactivado |
| `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID` | Secret Manager | Obligatorias al arrancar |
| `NEO4J_PASSWORD` | Secret Manager | Sin ella se omiten las consultas al grafo |
| `EMBEDDING_MODEL_NAME` | Sustitución | **Debe coincidir con el modelo que construyó el índice** |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_DATABASE` | Sustitución | |
| `CORS_ORIGINS` | Opcional | Si falta, se usa la lista segura por defecto |
