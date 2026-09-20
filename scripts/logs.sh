#!/usr/bin/env bash
# =============================================================================
# scripts/logs.sh — consultar los logs del orquestador
# =============================================================================
#   ./scripts/logs.sh tail              Sigue los logs en vivo
#   ./scripts/logs.sh errors [horas]    Solo errores y trazas (por defecto 24 h)
#   ./scripts/logs.sh req  <id>         Todo lo de una petición (X-Request-Id)
#   ./scripts/logs.sh thread <id>       Todo lo de una conversación
#   ./scripts/logs.sh auditor [horas]   Actividad del auditor: registros, cierres, veredictos
#   ./scripts/logs.sh rag [horas]       Enrutado y recuperación
#   ./scripts/logs.sh slow [ms] [horas] Peticiones más lentas de N ms (por defecto 5000)
#   ./scripts/logs.sh resumen [horas]   Cuántas peticiones, cuántos errores, latencias
#   ./scripts/logs.sh consola           Abre el visor web con el filtro puesto
#
# Segundo argumento opcional en todos: el entorno (dev por defecto).
# =============================================================================
set -euo pipefail

ACTION="${1:-resumen}"
ENVIRONMENT="${ENV:-dev}"

case "$ENVIRONMENT" in
  dev)  PROJECT="recava-auditor-dev";  SERVICE="orchestrator-dev" ;;
  prod) PROJECT="recava-auditor-prod"; SERVICE="orchestrator-prod" ;;
  *) echo "Entorno desconocido: $ENVIRONMENT" >&2; exit 1 ;;
esac

BASE="resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$SERVICE\""
FMT="value(timestamp.date('%m-%d %H:%M:%S'), textPayload)"
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# El log de acceso de gunicorn duplica cada petición y estorba al leer.
strip_access() { grep -v "169.254.169.126\|GET /health\|GET /readyz" || true; }

read_logs() {  # $1 filtro extra, $2 frescura, $3 límite
  gcloud logging read "$BASE${1:+ AND $1}" --project="$PROJECT" \
    --freshness="${2:-24h}" --limit="${3:-200}" --format="$FMT" 2>/dev/null
}

case "$ACTION" in

  tail)
    say "Siguiendo $SERVICE en vivo (Ctrl+C para salir)"
    gcloud beta logging tail "$BASE" --project="$PROJECT" \
      --format="value(timestamp.date('%H:%M:%S'), severity, textPayload)" 2>/dev/null \
      || { echo "Necesita el componente beta: gcloud components install beta"; exit 1; }
    ;;

  errors)
    say "Errores de las últimas ${2:-24}h"
    read_logs 'severity>=ERROR' "${2:-24}h" 100 | strip_access
    ;;

  req)
    ID="${2:?Falta el X-Request-Id. Lo devuelve cada respuesta en esa cabecera}"
    say "Petición $ID"
    read_logs "textPayload:\"$ID\"" "${3:-24}h" 50 | strip_access | sort
    ;;

  thread)
    ID="${2:?Falta el thread_id de la conversación}"
    say "Conversación $ID"
    read_logs "textPayload:\"$ID\"" "${3:-72}h" 200 | strip_access | sort
    ;;

  auditor)
    say "Actividad del auditor, últimas ${2:-24}h"
    echo "  (+N -> x/y = respuestas registradas y cobertura; verdict = veredicto normativo)"
    read_logs '(textPayload:"record_block_answers" OR textPayload:"complete_audit_block" OR
                textPayload:"defer_block" OR textPayload:"resume_block" OR
                textPayload:"Tool invoke_sustainability_expert")' "${2:-24}h" 200 | sort
    ;;

  rag)
    say "Recuperación, últimas ${2:-24}h"
    read_logs '(textPayload:"RAG routing" OR textPayload:"RAG message built" OR
                textPayload:"category filter")' "${2:-24}h" 100 | sort
    ;;

  slow)
    MS="${2:-5000}"
    say "Peticiones de más de ${MS} ms, últimas ${3:-24}h"
    read_logs 'textPayload:"request_end"' "${3:-24}h" 500 \
      | awk -v ms="$MS" '{ if (match($0, /"ms": [0-9]+/)) {
            v = substr($0, RSTART+6, RLENGTH-6) + 0; if (v >= ms) print v "ms\t" $0 } }' \
      | sort -rn | head -25
    ;;

  resumen)
    say "Resumen de las últimas ${2:-24}h en $SERVICE"
    TMP=$(mktemp)
    read_logs 'textPayload:"request_end"' "${2:-24}h" 1000 > "$TMP"
    TOTAL=$(grep -c "request_end" "$TMP" || echo 0)
    echo "  peticiones:        $TOTAL"
    if [ "$TOTAL" -gt 0 ]; then
      echo "  por código:"
      grep -o '"status": [0-9]*' "$TMP" | awk '{print $2}' | sort | uniq -c \
        | awk '{printf "      %s  ->  %s\n", $2, $1}'
      echo "  latencia (ms):"
      grep -o '"ms": [0-9]*' "$TMP" | awk '{print $2}' | sort -n | awk '
        { a[NR]=$1; s+=$1 }
        END { if (NR) printf "      mediana %d · p95 %d · máx %d · media %d\n",
              a[int(NR*0.5)+0==0?1:int(NR*0.5)], a[int(NR*0.95)==0?1:int(NR*0.95)], a[NR], s/NR }'
    fi
    rm -f "$TMP"
    ERR=$(read_logs 'severity>=ERROR' "${2:-24}h" 500 | grep -c . || true)
    echo "  líneas de error:   $ERR"
    [ "$ERR" -gt 0 ] && echo "      míralas con:  ./scripts/logs.sh errors"
    TURNS=$(read_logs 'textPayload:"/chat_auditor: uid="' "${2:-24}h" 500 | grep -c . || true)
    ADV=$(read_logs 'textPayload:"/chat_assistant: uid="' "${2:-24}h" 500 | grep -c . || true)
    echo "  turnos de auditor: $TURNS"
    echo "  turnos de asesor:  $ADV"
    ;;

  consola)
    Q=$(printf '%s' "$BASE" | sed 's/ /%20/g; s/"/%22/g; s/=/%3D/g')
    echo "https://console.cloud.google.com/logs/query;query=$Q?project=$PROJECT"
    ;;

  *)
    sed -n '2,20p' "$0"
    exit 1
    ;;
esac
