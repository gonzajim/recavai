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


def generate_embedding(embed_model, text: str) -> list[float]:
    """Encodes text using the pre-loaded SentenceTransformer model."""
    vector = embed_model.encode(text, normalize_embeddings=True)
    return vector.tolist()


_MIN_SCORE = 0.55      # Discard chunks below this cosine similarity
_CANDIDATE_K = 12     # Retrieve this many candidates before score-filtering
_MAX_RESULTS = 6      # Cap on chunks passed to the LLM after filtering


def search_documents(
    pinecone_index,
    query_embedding: list[float],
    top_k: int = _CANDIDATE_K,
    metadata_filter: dict | None = None,
    min_score: float = _MIN_SCORE,
    namespace: str | None = None,
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
                "content": meta.get("text") or meta.get("content", ""),
                "title": meta.get("source") or meta.get("title", ""),
                "category": meta.get("primary_category", "uploaded"),
                "score": score,
                "page": meta.get("page"),
                "total_pages": meta.get("total_pages"),
            })

        return results[:_MAX_RESULTS]
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
        embedding = generate_embedding(embed_model, chunk)
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
    embedding = generate_embedding(embed_model, content)
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
    """Returns 'hybrid' (Pinecone + Neo4j) or 'semantic' (Pinecone only)."""
    return _ROUTING.get(question_type, "semantic")


# ---------------------------------------------------------------------------
# Neo4j graph retrieval (GraphRAG)
# ---------------------------------------------------------------------------

_CANONICAL_ENTITIES = sorted([
    "CSDDD", "CSRD", "EUDR", "ESRS", "REACH", "EMAS",
    "GRI", "OIT", "OCDE", "ISO 26000", "Basilea",
    "Directiva de Debida Diligencia", "Directiva de Deforestación",
    "due diligence", "diligencia debida",
    "cadena de suministro", "cadena de valor",
    "derechos humanos", "trabajo forzoso",
    "sostenibilidad", "greenwashing",
    "deforestación", "biodiversidad",
    "huella de carbono", "emisiones GEI", "neutralidad carbono",
    "comercio justo", "ecodiseño",
    "certificación", "auditoría social",
    "salario adecuado", "protección social",
    "brecha salarial", "igualdad de género",
    "economía circular", "residuos peligrosos",
    "pesca sostenible", "productos orgánicos",
    "sustancias químicas", "embalajes",
], key=len, reverse=True)

_neo4j_driver = None


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is not None:
        return _neo4j_driver
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    pwd = os.getenv("NEO4J_PASSWORD")
    if not uri or not pwd:
        return None
    try:
        from neo4j import GraphDatabase
        _neo4j_driver = GraphDatabase.driver(uri, auth=(user, pwd))
        logger.info("Neo4j driver initialized (uri=%s).", uri)
    except Exception as exc:
        logger.error("Neo4j driver init failed: %s", exc)
    return _neo4j_driver


def _extract_entities(query: str) -> list:
    q = query.lower()
    return [e for e in _CANONICAL_ENTITIES if e.lower() in q]


_GRAPH_CYPHER = """
MATCH (n:Entity)
WHERE any(e IN $entities WHERE toLower(n.name) CONTAINS toLower(e))
OPTIONAL MATCH (n)-[r:RELATED]->(related:Entity)
RETURN n.name AS subject, r.predicate AS relation, related.name AS object
ORDER BY n.name
LIMIT 40
"""


def get_graph_context(query: str, char_budget: int = 6000) -> str:
    """
    Retrieves relationship triples from Neo4j for canonical entities found in query.
    Returns empty string if Neo4j is not configured or no entities match.
    """
    driver = _get_neo4j_driver()
    if not driver:
        return ""

    entities = _extract_entities(query)
    if not entities:
        logger.info("Graph RAG: no canonical entities detected in query.")
        return ""

    db = os.getenv("NEO4J_DATABASE", "neo4j")
    logger.info("Graph RAG: entities=%s db=%s", entities, db)
    try:
        with driver.session(database=db) as session:
            results = session.run(_GRAPH_CYPHER, entities=entities)
            seen: set = set()
            triples: list = []
            for record in results:
                subj = record["subject"] or ""
                rel = record["relation"] or ""
                obj = record["object"] or ""
                line = f"{subj} --[{rel}]--> {obj}" if rel else subj
                if line not in seen:
                    seen.add(line)
                    triples.append(line)

        if not triples:
            logger.info("Graph RAG: no triples found for entities=%s", entities)
            return ""

        body = "RELACIONES JURÍDICAS EN EL GRAFO DE CONOCIMIENTO:\n" + "\n".join(triples)
        logger.info("Graph RAG: %d triples recovered.", len(triples))
        return body[:char_budget]

    except Exception:
        logger.error("Graph RAG query failed", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# PDF extraction + chunking (for /upload_document endpoint)
# ---------------------------------------------------------------------------

_CHUNK_WORDS = 400
_CHUNK_OVERLAP = 50
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
