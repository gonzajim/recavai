# Dockerfile

# ---- Builder Stage ----
# Digest fijado: evita que actualizaciones de Docker Hub invaliden el cache de capas.
# Para actualizar Python: ejecuta `docker pull python:3.10-slim`, copia el nuevo digest.
FROM python:3.10-slim@sha256:09304d54d98baaa86d14fce52a168ee32b712e20e4e82f7ec168799aa6060fd1 as builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app-build

# --- Capa 1: torch CPU-only (pesada, raramente cambia) ---
# --index-url fuerza CPU wheels del servidor de PyTorch (~200MB vs ~532MB CUDA en PyPI).
# --extra-index-url PyPI: el índice de PyTorch no aloja paquetes de build-backend
# (p.ej. flit_core, requerido por typing_extensions cuando no hay wheel disponible
# para esa combinación exacta de versión/plataforma) — sin esto, pip falla al no
# poder resolver esa dependencia transitiva de compilación.
# Capa separada: si requirements.txt cambia, esta capa sigue cacheada.
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir torch \
      --index-url https://download.pytorch.org/whl/cpu \
      --extra-index-url https://pypi.org/simple

# --- Capa 2: resto de dependencias (cambia con más frecuencia) ---
COPY requirements.txt requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- Final Stage ----
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

ARG APP_USER_UID=1000
ARG APP_USER_GID=1000

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid ${APP_USER_GID} appgroup && \
    useradd --uid ${APP_USER_UID} --gid ${APP_USER_GID} --create-home --shell /sbin/nologin appuser

COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv

WORKDIR /app

COPY --chown=appuser:appgroup app.py ./
COPY --chown=appuser:appgroup src/ ./src/

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["sh", "-c", "/opt/venv/bin/gunicorn app:app --bind \"0.0.0.0:${PORT}\" --workers 4 --timeout 120 --access-logfile - --error-logfile -"]
