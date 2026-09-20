# src/history_service.py
from datetime import datetime, timedelta
from src.config import logger


def get_thread_history(firestore_db, thread_id: str, limit: int = 40) -> list[dict]:
    """
    Retrieves the MOST RECENT `limit` messages from Firestore, in chronological order,
    in Gemini Content format. Ensures roles alternate (user/model), starting with user.

    Se ordena DESCENDENTE y se invierte a propósito: con `order_by("created_at").limit(n)`
    (ascendente) Firestore devuelve los n mensajes MÁS ANTIGUOS, de modo que a partir del
    turno n/2 el modelo dejaba de ver la conversación reciente y repetía preguntas ya
    respondidas. Es lo que rompía el modo auditor.
    """
    try:
        from google.cloud.firestore_v1 import Query
        messages_ref = (
            firestore_db
            .collection("threads")
            .document(thread_id)
            .collection("messages")
            .order_by("created_at", direction=Query.DESCENDING)
            .limit(limit)
        )
        docs = list(messages_ref.stream())[::-1]          # más recientes, en orden cronológico
        history = []
        for doc in docs:
            data = doc.to_dict()
            role = data.get("role")
            text = data.get("text", "")
            if role in ("user", "model") and text:
                history.append({"role": role, "parts": [{"text": text}]})

        # Enforce alternating roles starting with "user"
        history = _sanitize_history(history)
        return history

    except Exception:
        logger.error("Failed to load thread history for %s", thread_id, exc_info=True)
        return []


def append_messages(
    firestore_db, thread_id: str, user_text: str, model_text: str
) -> None:
    """Batch-writes one user turn and one model turn to the thread's messages subcollection."""
    try:
        messages_ref = (
            firestore_db
            .collection("threads")
            .document(thread_id)
            .collection("messages")
        )
        batch = firestore_db.batch()
        now = datetime.utcnow()
        batch.create(messages_ref.document(), {
            "role": "user",
            "text": user_text,
            "created_at": now,
        })
        batch.create(messages_ref.document(), {
            "role": "model",
            "text": model_text,
            "created_at": now + timedelta(microseconds=1),
        })
        batch.commit()
    except Exception:
        logger.error("Failed to append messages for thread %s", thread_id, exc_info=True)


def _sanitize_history(history: list[dict]) -> list[dict]:
    """
    Ensures history is safe to pass to Gemini:
    - Starts with a user turn.
    - Alternates user/model throughout.
    - Never ends with a user turn (drop trailing user turns so the next live
      message is always the first after a model turn, or the very first).
    """
    if not history:
        return []

    sanitized = []
    expected_role = "user"
    for turn in history:
        if turn["role"] == expected_role:
            sanitized.append(turn)
            expected_role = "model" if expected_role == "user" else "user"

    # Drop trailing user turns — Gemini requires history to end on a model turn
    while sanitized and sanitized[-1]["role"] == "user":
        sanitized.pop()

    return sanitized
