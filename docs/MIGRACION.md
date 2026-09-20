# Migración a los proyectos definitivos

Plan para llevar el sistema de `recava-auditor-dev` / `recava-auditor-prod` a una pareja de proyectos coherentes con el nombre del producto: **`recavai-dev`** y **`recavai-prod`**.

Estado de partida comprobado el 19 de septiembre de 2026.

## 0. Por qué esto es una migración y no un renombrado

En GCP no se puede renombrar casi nada de lo que importa:

| Recurso | ¿Se renombra? | Consecuencia |
|---|---|---|
| ID de proyecto | **No, nunca** | Hay que crear proyecto nuevo y mover todo |
| ID de proyecto de Firebase | **No** (es el mismo que el de GCP) | Los usuarios y Firestore viven en el proyecto viejo |
| Sitio de Hosting (`*.web.app`) | **No** | `recava-auditor-dev.web.app` deja de ser la dirección buena |
| Servicio de Cloud Run | **No** | Cambia la URL del backend |
| Secreto de Secret Manager | **No** | Se recrean |
| Dataset de BigQuery | **No** | Se copia |
| Índice de Pinecone | **No** | Se reindexa o se comparte |

Lo único que sí cambia en sitio es el **nombre visible** del proyecto, que no sirve para nada técnico.

## 1. Lo que hay que decidir antes de empezar

Cuatro decisiones que condicionan el resto y que no son técnicas:

**1.1 ¿Qué pasa con la dirección que ya usa la gente?**
`recava-auditor-dev.web.app` está en marcadores, en correos y quizá incrustada en alguna web ajena. Al migrar, la dirección buena pasa a ser `recavai-dev.web.app`. Hay dos salidas:

- *Dominio propio* (recomendado): apuntar `app.recavai.es` (o el dominio que tengáis) al hosting nuevo. A partir de ahí los cambios de proyecto dejan de afectar a los usuarios para siempre. Es la única solución que no vuelve a romperse.
- *Redirección*: dejar el sitio viejo sirviendo una página que redirija al nuevo. Barato, pero arrastra el nombre viejo indefinidamente.

**1.2 ¿Se migran los usuarios o se empieza limpio?**
Firebase Auth permite exportar e importar usuarios **con sus contraseñas**, pero hay que trasladar también los parámetros del algoritmo de hash del proyecto origen; si se pierden, todo el mundo tiene que restablecer su contraseña. Dado que hoy los usuarios sois el equipo y algunas cuentas de Gmail de pruebas, **empezar limpio es defendible y mucho más simple**. Decidirlo antes, no durante.

**1.3 ¿Se migran las conversaciones?**
Firestore (hilos, mensajes, progreso de auditoría, metadatos de documentos) y BigQuery (histórico de turnos). El histórico de BigQuery tiene valor para el panel de experto; los hilos de Firestore, menos. Se puede migrar solo BigQuery.

**1.4 ¿Un índice de Pinecone o dos?**
Hoy hay uno (`uclm-corpus-roma`) y lo comparten todos. Separar dev y prod de verdad implica dos índices y reindexar, que es justo lo que hará el pipeline nuevo. Aprovechar la migración para reindexar con `corpus_pipeline.py` mata dos pájaros, pero **exige pasar antes el benchmark**: el corpus actual tiene mediana de dos palabras por fragmento y el reindexado cambia la calidad de las respuestas.

**1.5 Facturación**
El proyecto vivo se está facturando hoy a la cuenta **«Alfonso»** (`0131EB-A0BD56-558557`), no a la cuenta **«recava»** (`014698-82BF02-E0EF64`), que está abierta. Los proyectos nuevos deberían nacer ya en la cuenta correcta. Conviene aclarar por qué está así antes de replicarlo.

## 2. Fases

### Fase 0 — Coherencia de nombres sin tocar infraestructura ✅ hecha

- Repositorio renombrado a `recavai` (GitHub redirige la URL antigua).
- Descripción pública corregida: decía «OpenAI Responses API & Agents SDK», falso desde la migración a Gemini.
- `README.md` reescrito.
- `trigger.yaml` retirado: describía un trigger muerto sobre un proyecto inaccesible.
- Referencias al nombre viejo eliminadas del código y la documentación.

### Fase 1 — Preparar los proyectos nuevos (sin usuarios, sin riesgo)

```bash
# Crear y facturar
gcloud projects create recavai-dev  --name="RecavAI · Desarrollo"
gcloud projects create recavai-prod --name="RecavAI · Producción"
for p in recavai-dev recavai-prod; do
  gcloud billing projects link $p --billing-account=014698-82BF02-E0EF64
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
    secretmanager.googleapis.com firestore.googleapis.com bigquery.googleapis.com \
    artifactregistry.googleapis.com firebase.googleapis.com --project=$p
done
```

Después, en cada proyecto: crear los secretos (`GEMINI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID`, `NEO4J_PASSWORD`), la base de datos de Firestore en `europe-west1`, el dataset de BigQuery, y añadir el proyecto a Firebase con Authentication y Hosting.

Convención de nombres, ya fijada en [`DESPLIEGUE.md`](DESPLIEGUE.md) §1.1: servicios `orchestrator-dev` y `orchestrator-prod`; sitios `recavai-dev`, `recavai-dev-panel`, `recavai-prod`, `recavai-prod-panel`. Nunca un nombre que incluya el número de proyecto.

### Fase 2 — Desplegar en `recavai-dev` y probar en paralelo

El entorno viejo sigue sirviendo a los usuarios mientras tanto. Aquí se prueba de verdad: auditoría completa, subida de documentos, panel de administración, historial.

```bash
./scripts/deploy.sh canary dev    # con dev ya apuntando a recavai-dev
```

Requiere actualizar en `scripts/deploy.sh` y `.firebaserc` los identificadores de proyecto, y en los frontales la URL del backend (`bubble-prod-script.js`, `UserManagement.js`).

### Fase 3 — Datos

Solo lo que se haya decidido migrar en §1.2 y §1.3.

```bash
# Firestore: exportar a GCS e importar
gcloud firestore export gs://BUCKET/backup-YYYYMMDD --project=recava-auditor-dev
gcloud firestore import gs://BUCKET/backup-YYYYMMDD --project=recavai-dev

# BigQuery: copiar el dataset
bq mk --dataset recavai-dev:recava_agent_audit_qa
bq cp recava-auditor-dev:recava_agent_audit_qa.chat_history \
      recavai-dev:recava_agent_audit_qa.chat_history

# Firebase Auth: exportar e importar (ver §1.2 sobre los hashes)
firebase auth:export usuarios.json --project recava-auditor-dev
firebase auth:import usuarios.json --project recavai-dev --hash-algo=... --hash-key=...
```

Hacer el volcado **con el sistema viejo ya en solo lectura**, o se pierden las conversaciones que ocurran entre el volcado y el corte.

### Fase 4 — Corte

1. Avisar a los usuarios con antelación.
2. Desplegar los frontales nuevos apuntando al backend nuevo.
3. Publicar en el sitio viejo una página que redirija al nuevo (o mover el dominio propio, si se eligió esa vía en §1.1).
4. Dejar el entorno viejo **intacto y accesible** al menos dos semanas: es la vuelta atrás.

### Fase 5 — Retirada

Pasado el periodo de gracia, y con una copia verificada de los datos: borrar los servicios, vaciar los sitios de hosting y cerrar los proyectos `recava-auditor-dev` y `recava-auditor-prod`. Un proyecto cerrado se puede recuperar durante 30 días.

## 3. Riesgos

| Riesgo | Cómo se mitiga |
|---|---|
| Los usuarios pierden el acceso porque cambió la dirección | Dominio propio (§1.1); si no, redirección desde el sitio viejo |
| Todo el mundo tiene que restablecer la contraseña | Migrar los parámetros de hash, o decidir de antemano empezar limpio |
| Se pierden conversaciones ocurridas durante el volcado | Poner el sistema viejo en solo lectura antes de exportar |
| El reindexado cambia la calidad de las respuestas | Pasar el benchmark antes y después; no mezclar migración y reindexado si se puede evitar |
| Cuotas y claves de terceros | Pinecone, Neo4j y Gemini tienen límites por clave: comprobar que dos entornos no se estorban |
| Quedan referencias al proyecto viejo | `grep -rn "recava-auditor" .` antes del corte |

## 4. Alternativa más barata, por si cambia la prioridad

Si en algún momento el coste de la migración pesa más que la coherencia de los identificadores: mantener la infraestructura donde está, poner un **dominio propio** delante (`app.recavai.es`), y dejar que los IDs `recava-auditor-*` sean lo que son, un detalle interno documentado. Los usuarios, las publicaciones y las presentaciones verían siempre «RecavAI», que es lo que importa. Queda aquí escrito para que sea una decisión, no un olvido.
