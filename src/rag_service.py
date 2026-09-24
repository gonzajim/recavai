# src/rag_service.py
# Embedding uses sentence-transformers/all-MiniLM-L6-v2 (384 dims)
# to match the existing Pinecone corpus (uclm-corpus-roma).
# The embed_model instance is created once in config.py and passed here.
#
# Migration path (when ready to re-index corpus):
#   Set EMBEDDING_MODEL_NAME=paraphrase-multilingual-MiniLM-L12-v2 (still 384 dims,
#   multilingual, better Spanish quality) and re-run the ingestion pipeline.
import io
import os
import uuid
from src.config import logger


def _e5_prefix(embed_model, is_query: bool) -> str:
    """Los modelos de la familia e5 exigen prefijar el texto: 'query: ' en las consultas y
    'passage: ' en los documentos. Sin prefijo rinden muy por debajo de lo que deben.
    MiniLM y el resto no llevan prefijo. El nombre del modelo sale de EMBEDDING_MODEL_NAME,
    que es también lo que carga src/config.py."""
    name = (getattr(embed_model, "recava_model_name", None)
            or os.getenv("EMBEDDING_MODEL_NAME", "")).lower()
    if "e5" not in name:
        return ""
    return "query: " if is_query else "passage: "


def generate_embedding(embed_model, text: str, is_query: bool = True) -> list[float]:
    """Encodes text using the pre-loaded SentenceTransformer model.

    is_query=False para texto que se va a INDEXAR (fragmentos de documentos): con e5 cambia
    el prefijo, y un fragmento indexado como consulta queda en otra zona del espacio."""
    vector = embed_model.encode(_e5_prefix(embed_model, is_query) + text, normalize_embeddings=True)
    return vector.tolist()


# Umbral de similitud. Depende del modelo: 0,55 está ajustado a all-MiniLM-L6-v2; con e5
# las similitudes se concentran en 0,85-0,90 y hay que calibrarlo
# (scripts/calibrate_threshold.py). Se fija por revisión con RAG_MIN_SCORE.
_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.55"))
_CANDIDATE_K = 12     # Retrieve this many candidates before score-filtering
_MAX_RESULTS = 6      # Cap on chunks passed to the LLM after filtering
# Con almacén de unidades (src/context_builder.py) se llama con max_results=None: no hay
# tope de fragmentos, manda el presupuesto de tokens del nivel.


def search_documents(
    pinecone_index,
    query_embedding: list[float],
    top_k: int = _CANDIDATE_K,
    metadata_filter: dict | None = None,
    min_score: float = _MIN_SCORE,
    namespace: str | None = None,
    max_results: int | None = _MAX_RESULTS,
) -> list[dict]:
    """
    Queries Pinecone and returns matching document excerpts.

    Corpus categories: 'CSDDD', 'GRI', 'general' (includes CSRD/NEIS/OCDE docs).
    Pass metadata_filter to narrow by category, e.g.:
      {"primary_category": {"$in": ["CSDDD", "general"]}}
    Pass namespace to restrict to a specific Pinecone namespace (e.g. uid for user docs).
    Returns [] if pinecone_index is None or on any exception.
    """
    if pinecone_index is None:
        return []
    try:
        query_kwargs = dict(
            vector=query_embedding,
            top_k=top_k,
            include_metadata=True,
        )
        if metadata_filter:
            query_kwargs["filter"] = metadata_filter
        if namespace:
            query_kwargs["namespace"] = namespace

        response = pinecone_index.query(**query_kwargs)

        results = []
        for match in response.get("matches", []):
            score = match.get("score", 0.0)
            if score < min_score:
                continue
            meta = match.get("metadata", {})
            results.append({
                "id": match.get("id"),
                "content": meta.get("text") or meta.get("content", ""),
                "title": meta.get("source") or meta.get("title", ""),
                "category": meta.get("primary_category", "uploaded"),
                "score": score,
                "page": meta.get("page"),
                "total_pages": meta.get("total_pages"),
                # Solo en el índice v2 (src/chunking_v2.py): unidad normativa y página final.
                "page_end": meta.get("page_end"),
                "article": meta.get("article"),
                "unit_label": meta.get("unit_label"),
            })

        return results[:max_results] if max_results else results
    except Exception:
        logger.error("Pinecone search failed", exc_info=True)
        return []


_MAX_USER_FILES = 25
_USER_DOCS_MIN_SCORE = 0.25


def upsert_user_file_chunks(
    embed_model,
    pinecone_index,
    uid: str,
    doc_id: str,
    filename: str,
    chunks: list[str],
) -> None:
    """Embeds and upserts all chunks of a user file into the user's Pinecone namespace."""
    vectors = []
    for i, chunk in enumerate(chunks):
        embedding = generate_embedding(embed_model, chunk, is_query=False)
        vectors.append({
            "id": f"{doc_id}_{i:05d}",
            "values": embedding,
            "metadata": {
                "text": chunk,
                "source": filename,
                "doc_id": doc_id,
                "filename": filename,
                "chunk_idx": i,
                "primary_category": "uploaded",
            },
        })
    # Upsert in batches of 100 to avoid Pinecone request size limits
    for start in range(0, len(vectors), 100):
        pinecone_index.upsert(vectors=vectors[start:start + 100], namespace=uid)
    logger.info("Upserted %d chunks for doc=%s uid=%s", len(vectors), doc_id, uid)


def delete_user_file_chunks(pinecone_index, uid: str, doc_id: str, chunk_count: int) -> None:
    """Deletes all chunks of a user file from their Pinecone namespace."""
    ids = [f"{doc_id}_{i:05d}" for i in range(chunk_count)]
    # Pinecone delete accepts up to 1000 ids per call
    for start in range(0, len(ids), 1000):
        pinecone_index.delete(ids=ids[start:start + 1000], namespace=uid)
    logger.info("Deleted %d chunks for doc=%s uid=%s", len(ids), doc_id, uid)


def search_user_documents(
    pinecone_index,
    query_embedding: list[float],
    uid: str,
    top_k: int = 6,
) -> list[dict]:
    """Searches the user's Pinecone namespace for relevant chunks from their uploaded files."""
    return search_documents(
        pinecone_index,
        query_embedding,
        top_k=top_k,
        min_score=_USER_DOCS_MIN_SCORE,
        namespace=uid,
    )


def ingest_document(
    embed_model,
    pinecone_index,
    doc_id: str | None,
    content: str,
    title: str = "",
    source_url: str = "",
    doc_type: str = "",
) -> str:
    """
    Generates an embedding for content and upserts it to Pinecone
    using the same metadata schema as the existing corpus.
    Returns the doc_id used (auto-generated UUID if not supplied).
    """
    if pinecone_index is None:
        raise RuntimeError("Pinecone index is not configured.")

    doc_id = doc_id or str(uuid.uuid4())
    embedding = generate_embedding(embed_model, content, is_query=False)
    pinecone_index.upsert(vectors=[{
        "id": doc_id,
        "values": embedding,
        "metadata": {
            "text": content,
            "source": source_url or title,
            "primary_category": doc_type,
            "block_type": "text",
            "entities": [],
            "triplets": [],
            "graph_importance": 0,
        },
    }])
    logger.info("Ingested document '%s' into Pinecone.", doc_id)
    return doc_id


# ---------------------------------------------------------------------------
# Category filter (shared with context_orchestrator to avoid circular import)
# ---------------------------------------------------------------------------

_CSDDD_TERMS = {
    "csddd", "diligencia debida", "cadena de actividades", "impactos adversos",
    "impacto adverso", "reparación", "reclamacion", "reclamación", "socio comercial",
    "due diligence", "conducta empresarial responsable",
}
_GRI_TERMS = {
    "gri", "global reporting initiative", "estándar gri", "estandar gri",
    "contenido gri", "indicador gri",
}


def detect_category_filter(query: str) -> dict | None:
    """
    Returns a Pinecone metadata filter based on keyword signals.
    Corpus categories: 'CSDDD', 'GRI', 'general'.

    - Clear GRI query → ['GRI', 'general']
    - Clear CSDDD query (≥2 hits) → ['CSDDD', 'general']
    - Mixed/CSRD/unknown → None (search all)
    """
    q = query.lower()
    hits_csddd = sum(1 for t in _CSDDD_TERMS if t in q)
    hits_gri = sum(1 for t in _GRI_TERMS if t in q)
    if hits_gri >= 1 and hits_csddd == 0:
        return {"primary_category": {"$in": ["GRI", "general"]}}
    if hits_csddd >= 2 and hits_gri == 0:
        return {"primary_category": {"$in": ["CSDDD", "general"]}}
    return None


# ---------------------------------------------------------------------------
# PDF extraction + chunking (for /upload_document endpoint)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Question-type routing
# ---------------------------------------------------------------------------

_RESOURCE_SIGNALS = [
    "cuáles son los", "qué herramientas", "qué sellos", "qué iniciativas",
    "qué certificaciones", "principales sellos", "herramientas informáticas",
    "qué beneficios ofrece", "qué parámetros",
]
_OPERATIONAL_SIGNALS = [
    "cómo hago", "qué pasos", "qué documentos", "documentos debo", "cómo evalúo",
    "cómo creo", "qué requisitos", "qué normativa debo", "qué obligaciones tengo",
    "cómo manejar", "cómo introduc", "cómo puedo introduc", "cómo redu",
    "qué condiciones", "para cumplir", "para lograr", "qué obligaciones",
    "qué requisitos impone", "cómo se aplica", "qué pasos se requieren",
    "mapa de riesgos", "cuestionario de auditoría",
]
_CONCEPTUAL_SIGNALS = [
    "qué es", "qué son", "qué implica", "qué introduce", "qué cambios introduce",
    "cómo funciona", "en qué consiste", "explica", "explicar", "explicame",
    "podrías explicar", "podrías detallar", "describe", "visión general",
    "relación con", "se alinea con",
]
_ROUTING = {
    "resource": "hybrid",
    "operational": "hybrid",
    "conceptual": "semantic",
    "unknown": "semantic",
}


def classify_question(query: str) -> str:
    """Returns 'resource', 'operational', 'conceptual', or 'unknown'."""
    q = query.lower()
    if any(s in q for s in _RESOURCE_SIGNALS):
        return "resource"
    if any(s in q for s in _OPERATIONAL_SIGNALS):
        return "operational"
    if any(s in q for s in _CONCEPTUAL_SIGNALS):
        return "conceptual"
    return "unknown"


def get_routing_strategy(question_type: str) -> str:
    """Returns 'hybrid' or 'semantic'. Solo informativo desde que se retiró Neo4j: el grafo
    normativo (src/normative_graph.py) se aplica a todas las preguntas cuando está activado."""
    return _ROUTING.get(question_type, "semantic")


# ---------------------------------------------------------------------------
# PDF extraction + chunking (for /upload_document endpoint)
# ---------------------------------------------------------------------------

# 250 palabras ≈ 1.600 caracteres ≈ 350 tokens de e5: cabe en su límite de 512. Con 400
# (≈ 800 tokens en español) los documentos subidos se truncaban al embeberlos (DEBT-13).
_CHUNK_WORDS = 250
_CHUNK_OVERLAP = 40
_MAX_CHUNKS = 500   # guard against very large PDFs


def extract_pdf_chunks(file_bytes: bytes, chunk_words: int = _CHUNK_WORDS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """
    Extracts text from a PDF byte string and splits it into overlapping chunks.
    Uses pypdf (pure Python, no system deps).
    Returns a list of non-empty text strings (up to _MAX_CHUNKS).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    all_words: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        all_words.extend(text.split())

    if not all_words:
        return []

    chunks = []
    step = chunk_words - overlap
    for start in range(0, len(all_words), step):
        chunk = " ".join(all_words[start : start + chunk_words])
        if chunk.strip():
            chunks.append(chunk)
        if len(chunks) >= _MAX_CHUNKS:
            break

    return chunks
