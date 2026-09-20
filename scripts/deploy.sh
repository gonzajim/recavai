#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh — despliegue del orquestador con canary, promoción y rollback
# =============================================================================
#   ./scripts/deploy.sh canary  [dev|prod]   Revisión nueva SIN tráfico, con URL de prueba
#   ./scripts/deploy.sh promote [dev|prod]   Envía el 100% del tráfico a la última revisión
#   ./scripts/deploy.sh hosting [dev|prod]   Publica widget de chat y panel de administración
#   ./scripts/deploy.sh rollback [dev|prod] [REVISION]   Vuelve a la revisión anterior
#   ./scripts/deploy.sh status  [dev|prod]   Revisiones, reparto de tráfico y salud
#
# El flujo seguro es: canary -> probar en la URL etiquetada -> promote -> hosting.
# Nada de esto se ejecuta solo: no hay trigger automático (ver docs/DESPLIEGUE.md).
# =============================================================================
set -euo pipefail

REGION="europe-west1"
ACTION="${1:-status}"
ENVIRONMENT="${2:-dev}"

case "$ENVIRONMENT" in
  dev)
    PROJECT="recava-auditor-dev"
    SERVICE="orchestrator-dev"
    FIREBASE_ALIAS="dev"
    ;;
  prod)
    PROJECT="recava-auditor-prod"
    SERVICE="orchestrator-prod"      # nunca "orchestrator" a secas: ver convención en docs/DESPLIEGUE.md
    FIREBASE_ALIAS="prod"
    echo "AVISO: a 2026-09-19 el proyecto $PROJECT NO tiene facturación habilitada,"
    echo "       no tiene Secret Manager y no hay índice de Pinecone propio."
    echo "       Lee docs/DESPLIEGUE.md §3 antes de continuar."
    read -r -p "¿Seguir de todas formas? [s/N] " answer
    [[ "$answer" == "s" || "$answer" == "S" ]] || { echo "Cancelado."; exit 1; }
    ;;
  *)
    echo "Entorno desconocido: $ENVIRONMENT (usa dev o prod)" >&2
    exit 1
    ;;
esac

TAG="canary"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

canary_url() {
  # Cloud Run expone la revisión etiquetada en la URL del servicio con el prefijo
  # "TAG---". Se compone a mano porque las proyecciones de --format no filtran listas.
  local base
  base=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
           --format='value(status.url)' 2>/dev/null)
  [[ -n "$base" ]] && echo "https://$TAG---${base#https://}"
}

case "$ACTION" in
  canary)
    say "Construyendo y desplegando SIN tráfico en $PROJECT/$SERVICE"
    gcloud builds submit --config=cloudbuild.yaml --project="$PROJECT" \
      --substitutions="_ENV=$ENVIRONMENT,_SERVICE_NAME=$SERVICE,_FIREBASE_PROJECT_ALIAS=$FIREBASE_ALIAS,_TRAFFIC=canary,_CANARY_TAG=$TAG"
    say "URL de prueba (no la ve ningún usuario):"
    canary_url
    cat <<'EOF'

Qué probar antes de promover:
  1. GET  /health  responde ok
  2. Modo auditor: recorre las 6 preguntas obligatorias del bloque 1 sin repetir
  3. Responde "tenemos buzón ético solo para empleados" en el bloque 4:
     debe avisar de la brecha y de qué hacer, citando la norma
  4. No cierra el bloque hasta que no quedan pendientes

Cuando convenza:  ./scripts/deploy.sh promote ENTORNO
EOF
    ;;

  promote)
    say "Revisiones actuales de $SERVICE"
    gcloud run revisions list --service "$SERVICE" --region "$REGION" --project "$PROJECT" \
      --format="table(metadata.name, status.conditions[0].lastTransitionTime.date('%Y-%m-%d %H:%M'), spec.containers[0].image.basename())" \
      --limit 5
    PREVIOUS=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
      --format="value(status.traffic.filter(percent=100).revisionName)" | head -1)
    say "Enviando el 100% del tráfico a la última revisión"
    gcloud run services update-traffic "$SERVICE" --region "$REGION" --project "$PROJECT" --to-latest
    echo
    echo "Revisión anterior: ${PREVIOUS:-desconocida}"
    echo "Rollback:  ./scripts/deploy.sh rollback $ENVIRONMENT ${PREVIOUS:-REVISION}"
    ;;

  hosting)
    say "Compilando el panel de administración"
    # npm ci exige lock sincronizado y hoy no lo está; se cae a npm install.
    (cd public/admin-panel && { npm ci || { echo "AVISO: package-lock desincronizado; usando npm install"; npm install; }; } && npm run build)
    say "Publicando hosting en el proyecto $FIREBASE_ALIAS"
    echo "AVISO: el widget publicado es anterior a HEAD; esto sube también el fix de login"
    echo "       y la gestión de documentos, no solo los cambios del auditor."
    if command -v firebase >/dev/null 2>&1; then
      firebase deploy --only hosting --project "$FIREBASE_ALIAS" --non-interactive
    else
      npx --yes firebase-tools deploy --only hosting --project "$FIREBASE_ALIAS" --non-interactive
    fi
    ;;

  rollback)
    TARGET="${3:-}"
    if [[ -z "$TARGET" ]]; then
      say "Revisiones disponibles (elige una y vuelve a lanzar con su nombre)"
      gcloud run revisions list --service "$SERVICE" --region "$REGION" --project "$PROJECT" \
        --format="table(metadata.name, status.conditions[0].lastTransitionTime.date('%Y-%m-%d %H:%M'))" --limit 10
      exit 0
    fi
    say "Devolviendo el 100% del tráfico a $TARGET"
    gcloud run services update-traffic "$SERVICE" --region "$REGION" --project "$PROJECT" \
      --to-revisions="$TARGET=100"
    ;;

  status)
    say "Servicio $SERVICE en $PROJECT"
    gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
      --format="value[separator='
'](status.url, status.latestReadyRevisionName)"
    say "Reparto de tráfico"
    gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
      --format="table(status.traffic[].revisionName, status.traffic[].percent, status.traffic[].tag)"
    URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format="value(status.url)")
    say "Salud (el arranque en frío tarda ~30 s: carga el modelo de embeddings)"
    curl -fsS --max-time 90 "$URL/health" && echo || echo "  /health no responde"
    ;;

  *)
    sed -n '2,18p' "$0"
    exit 1
    ;;
esac
