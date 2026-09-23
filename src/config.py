# src/config.py
import os
import logging
from flask import Flask
from google.cloud import bigquery
from google import genai
from pinecone import Pinecone as PineconeClient
from sentence_transformers import SentenceTransformer

# --- 1. Flask ---
app = Flask(__name__)
# CORS is configured in app.py with specific allowed origins (supports_credentials=True
# is incompatible with wildcard origin, so it must use an explicit list)

# --- 2. Logging ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    stream_handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(process)d - %(filename)s:%(lineno)d - %(message)s'
    )
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

logger.info("Application configuration starting...")

# --- 3. Variables de entorno ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
BIGQUERY_DATASET_ID = os.getenv("BIGQUERY_DATASET_ID")
BIGQUERY_TABLE_ID = os.getenv("BIGQUERY_TABLE_ID")

if not GEMINI_API_KEY:
    logger.critical("Missing GEMINI_API_KEY environment variable.")
    raise ValueError("Missing GEMINI_API_KEY environment variable.")
if not all([BIGQUERY_DATASET_ID, BIGQUERY_TABLE_ID]):
    logger.critical("Missing BigQuery environment variables (BIGQUERY_DATASET_ID, BIGQUERY_TABLE_ID).")
    raise ValueError("Missing BigQuery environment variables.")

logger.info("Environment variables loaded.")

# --- 4. Clientes externos ---
try:
    genai_client = genai.Client(api_key=GEMINI_API_KEY)
    logger.info("Gemini client initialized.")

    bq_client = bigquery.Client()
    logger.info("BigQuery client initialized.")

    # RAG_INDEX_NAME (variable normal, por revisión) tiene prioridad sobre el secreto
    # PINECONE_INDEX_NAME. Así cada revisión de Cloud Run lleva fijado su índice: si el
    # índice se cambiara en el secreto (que se lee como «latest»), al volver a una revisión
    # anterior esta leería el índice NUEVO con el modelo de embeddings VIEJO.
    _INDEX_NAME = os.getenv("RAG_INDEX_NAME") or PINECONE_INDEX_NAME
    if PINECONE_API_KEY and _INDEX_NAME:
        _pc = PineconeClient(api_key=PINECONE_API_KEY)
        # El nombre puede ser una URL de host (https://...) o el nombre del índice.
        if _INDEX_NAME.startswith("http"):
            pinecone_index = _pc.Index(host=_INDEX_NAME)
        else:
            pinecone_index = _pc.Index(_INDEX_NAME)
        logger.info("Pinecone index connected via '%s'.", _INDEX_NAME)
    else:
        pinecone_index = None
        logger.warning("Pinecone not configured (PINECONE_API_KEY or PINECONE_INDEX_NAME missing). RAG disabled.")

    # Modelo de embeddings: TIENE que ser el mismo con el que se construyó el índice
    # (ARC-3). Índice v1 (uclm-corpus-roma): all-MiniLM-L6-v2. Índice v2
    # (recavai-corpus-v2): intfloat/multilingual-e5-small, que exige prefijos
    # 'query: '/'passage: ' (rag_service._e5_prefix). Ambos son de 384 dimensiones.
    _EMBEDDING_MODEL_NAME = os.getenv(
        "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
    )
    embed_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    embed_model.recava_model_name = _EMBEDDING_MODEL_NAME
    logger.info("SentenceTransformer '%s' loaded.", _EMBEDDING_MODEL_NAME)

    # In-memory FAISS store — one per session, keyed by thread_id.
    # Imported here after sentence-transformers to keep model warm before FAISS init.
    from src.local_vector_store import LocalVectorStore
    local_store = LocalVectorStore(dim=384)
    logger.info("LocalVectorStore (FAISS) initialised (max_sessions=%d).", local_store._max)

except Exception as e:
    logger.critical("Failed to initialize external clients: %s", e, exc_info=True)
    raise
