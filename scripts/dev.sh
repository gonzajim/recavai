#!/usr/bin/env bash
# =============================================================================
# scripts/dev.sh — entorno de desarrollo local
# =============================================================================
#   ./scripts/dev.sh setup       Crea el venv e instala dependencias
#   ./scripts/dev.sh check       Comprueba que todo lo necesario está listo
#   ./scripts/dev.sh emulators   Arranca Firebase Auth (9099) y Firestore (8085)
#   ./scripts/dev.sh api         Arranca el backend en http://localhost:8080
#   ./scripts/dev.sh web         Sirve el widget de chat en http://localhost:8000
#   ./scripts/dev.sh panel       Arranca el panel de administración (React, 3000)
#   ./scripts/dev.sh test        Pruebas offline del auditor (sin red ni claves)
#
# Se usan tres terminales: emulators, api y web. El widget detecta `localhost`
# y apunta solo al backend local y al emulador de Auth.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
bad()  { printf '  \033[31mfalta\033[0m %s\n' "$*"; }
warn() { printf '  \033[33maviso\033[0m %s\n' "$*"; }

load_env() {
  [[ -f "$ROOT/.env" ]] || { echo "No hay .env. Ejecuta: cp .env.example .env y rellénalo."; exit 1; }
  set -a; . "$ROOT/.env"; set +a
  # El fichero de credenciales por defecto es de tipo authorized_user y
  # firebase_admin exige service_account: sin esto, la inicialización falla.
  unset GOOGLE_APPLICATION_CREDENTIALS
  if [[ "${USE_EMULATORS:-0}" == "1" ]]; then
    export FIREBASE_AUTH_EMULATOR_HOST="localhost:9099"
    export FIRESTORE_EMULATOR_HOST="localhost:8085"
  fi
}

case "${1:-check}" in

  setup)
    say "Creando entorno virtual"
    [[ -d "$VENV" ]] || python3 -m venv "$VENV"
    "$PY" -m pip install --quiet --upgrade pip
    say "Instalando dependencias (la primera vez tarda: torch pesa ~200 MB)"
    "$PY" -m pip install -r requirements.txt
    [[ -f "$ROOT/.env" ]] || { cp "$ROOT/.env.example" "$ROOT/.env"; warn "Creado .env desde la plantilla: rellena GEMINI_API_KEY"; }
    say "Listo. Siguiente: ./scripts/dev.sh check"
    ;;

  check)
    say "Requisitos del entorno local"
    [[ -d "$VENV" ]] && ok "entorno virtual" || bad "entorno virtual (ejecuta: ./scripts/dev.sh setup)"
    [[ -f "$ROOT/.env" ]] && ok ".env" || bad ".env (ejecuta: cp .env.example .env)"
    if [[ -f "$ROOT/.env" ]]; then
      . "$ROOT/.env"
      [[ -n "${GEMINI_API_KEY:-}" ]] && ok "GEMINI_API_KEY" || bad "GEMINI_API_KEY (obligatoria: sin ella el backend no arranca)"
      [[ -n "${PINECONE_API_KEY:-}" ]] && ok "PINECONE_API_KEY" || warn "PINECONE_API_KEY vacía: el RAG quedará desactivado"
      [[ -n "${NEO4J_PASSWORD:-}" ]] && ok "NEO4J_PASSWORD" || warn "NEO4J_PASSWORD vacía: sin consultas al grafo"
      [[ "${USE_EMULATORS:-0}" == "1" ]] && ok "emuladores activados (no toca datos reales)" \
        || warn "USE_EMULATORS=0: escribirás en el Firestore REAL de desarrollo"
    fi
    # macOS trae un /usr/bin/java que no es un runtime: hay que ejecutarlo para saberlo.
    if java -version >/dev/null 2>&1; then ok "java (emuladores)"
    else bad "java — los emuladores no arrancarán. Instala: brew install --cask temurin"; fi
    command -v firebase >/dev/null 2>&1 && ok "firebase-tools" || warn "firebase-tools (npm i -g firebase-tools)"
    command -v node >/dev/null 2>&1 && ok "node $(node --version)" || bad "node"
    gcloud auth application-default print-access-token >/dev/null 2>&1 \
      && ok "credenciales de aplicación (ADC)" \
      || bad "ADC — ejecuta: gcloud auth application-default login"
    "$PY" - <<'PY' 2>/dev/null && ok "modelo de embeddings en caché" || warn "modelo de embeddings: se descargará al primer arranque (~90 MB)"
import os, pathlib, sys
p = pathlib.Path.home()/".cache/huggingface/hub"
sys.exit(0 if any(p.glob("models--sentence-transformers--*")) else 1)
PY
    ;;

  emulators)
    java -version >/dev/null 2>&1 || {
      echo "Los emuladores de Firebase necesitan Java y no está instalado."
      echo "  brew install --cask temurin"
      echo
      echo "Alternativa sin Java: pon USE_EMULATORS=0 en .env y trabajarás contra el"
      echo "Firestore real de desarrollo (los datos que crees serán reales)."
      exit 1
    }
    say "Auth en 9099, Firestore en 8085, interfaz en http://localhost:4000"
    firebase emulators:start --only auth,firestore --project recava-auditor-dev
    ;;

  api)
    load_env
    say "Backend en http://localhost:${PORT:-8080}"
    [[ "${USE_EMULATORS:-0}" == "1" ]] \
      && echo "   Auth y Firestore: EMULADORES (los datos no salen de tu máquina)" \
      || echo "   Auth y Firestore: REALES en recava-auditor-dev"
    echo "   Prueba:  curl localhost:${PORT:-8080}/health"
    exec "$VENV/bin/gunicorn" app:app --bind "0.0.0.0:${PORT:-8080}" \
      --workers 1 --threads 4 --timeout 120 --reload --access-logfile -
    ;;

  web)
    say "Widget de chat en http://localhost:8000"
    echo "   Detecta localhost y apunta solo al backend local y al emulador de Auth."
    echo "   Ese origen es el único localhost permitido por CORS."
    exec "$VENV/bin/python" -m http.server 8000 --directory public/chatbot
    ;;

  panel)
    say "Panel de administración en http://localhost:3000"
    warn "El panel llama al Cloud Run de desarrollo, no a tu backend local:"
    warn "cambia ORCHESTRATOR_BASE_URL en public/admin-panel/src/UserManagement.js si lo necesitas."
    cd public/admin-panel && npm install && exec npm start
    ;;

  test)
    say "Pruebas offline del auditor y del agente asesor (sin red, sin claves, sin Firestore)"
    "$PY" scripts/test_auditor.py && exec "$PY" scripts/test_agent.py
    ;;

  *)
    sed -n '2,17p' "$0"
    exit 1
    ;;
esac
