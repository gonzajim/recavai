# app.py
import os
import time
import json
import uuid
import datetime
from flask import request, jsonify, abort

# --- Configuración base y clientes externos ---
from src.config import app, logger, genai_client, pinecone_index, embed_model, local_store

# --- Servicios ---
from src.persistence_service import persist_conversation_turn
from src.history_service import get_thread_history, append_messages
from src.gemini_service import chat_with_auditor, chat_with_expert
from src.rag_service import ingest_document, extract_pdf_chunks, upsert_user_file_chunks, delete_user_file_chunks
from src.bigquery_service import (
    fetch_recent_conversations_for_user,
    fetch_conversation_thread,
)
from src import audit_catalog

# --- Firebase Admin / Firestore ---
import firebase_admin
from firebase_admin import credentials, auth as fb_auth, firestore
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

# --- CORS y Rate Limiting ---
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


# =============================================================================
# 0) Inicialización de Firebase Admin
# =============================================================================
if not firebase_admin._apps:
    if os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        firebase_admin.initialize_app()
    else:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if cred_path and os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        else:
            logger.info("Firebase Admin: using application default credentials.")
            cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred)

firestore_db = firestore.client()

# =============================================================================
# 1) CORS y Rate Limiting
# =============================================================================
_DEFAULT_ALLOWED_ORIGINS = [
    "https://recava-auditor-dev.web.app",
    "https://recava-auditor.web.app",
    # Admin panels (React app en public/admin-panel) — orígenes separados del chatbot.
    "https://recava-auditor-dev-panel.web.app",
    "https://recava-auditor-panel.web.app",
    "http://localhost:8000",
]
_allowed_origins_str = os.getenv("CORS_ORIGINS", ",".join(_DEFAULT_ALLOWED_ORIGINS))
_allowed_origins = [o.strip() for o in _allowed_origins_str.split(",") if o.strip()]

if not _allowed_origins or "*" in _allowed_origins:
    _allowed_origins = _DEFAULT_ALLOWED_ORIGINS
    logger.warning("CORS_ORIGINS no definida o '*', usando defaults seguros: %s", _allowed_origins)

CORS(
    app,
    origins=_allowed_origins,
    supports_credentials=True,
    methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["X-Request-Id"],
    max_age=86400,
)
logger.info("CORS configured for origins: %s", _allowed_origins)

limiter = Limiter(get_remote_address, app=app, default_limits=["120/minute"])


# =============================================================================
# 2) Utilidades de respuesta y logging
# =============================================================================
def ok(data, **meta):
    resp = {"ok": True, "data": data}
    if meta:
        resp["meta"] = meta
    return jsonify(resp), 200


def fail(message, status=400, **details):
    return jsonify({"ok": False, "error": {"message": message, **details}}), status


@app.before_request
def _req_start():
    request._id = uuid.uuid4().hex[:12]
    request._t0 = time.time()
    logger.info(
        json.dumps(
            {"evt": "request_start", "id": request._id, "path": request.path, "method": request.method}
        )
    )


@app.after_request
def _req_end(resp):
    dur_ms = int((time.time() - getattr(request, "_t0", time.time())) * 1000)
    resp.headers["X-Request-Id"] = getattr(request, "_id", "")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    logger.info(
        json.dumps({"evt": "request_end", "id": request._id, "status": resp.status_code, "ms": dur_ms})
    )
    return resp


# =============================================================================
# 3) Autenticación y helpers
# =============================================================================
def require_firebase_user_or_403():
    """Verifica ID token Firebase; exige email verificado."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        abort(401, description="Falta Authorization Bearer token")
    id_token = auth_header.split(" ", 1)[1]
    try:
        decoded = fb_auth.verify_id_token(id_token)
    except Exception as e:
        logger.warning("Auth: token inválido: %s", e)
        abort(401, description="Token inválido")
    if not decoded.get("email_verified", False):
        abort(403, description="Email no verificado")
    return decoded


# Único administrador autorizado a gestionar usuarios. Cambiar aquí si se añaden más.
_ADMIN_EMAILS = {"gonzalo.jimenez.martin@gmail.com"}


def require_admin_or_403():
    """Como require_firebase_user_or_403, pero exige además que el email
    esté en _ADMIN_EMAILS. Usar para cualquier endpoint de administración."""
    decoded = require_firebase_user_or_403()
    email = (decoded.get("email") or "").lower()
    if email not in _ADMIN_EMAILS:
        logger.warning("Admin: acceso denegado a %s", email)
        abort(403, description="Acceso restringido a administradores.")
    return decoded


_MAX_ADMIN_USERS_LISTED = 1000


def _serialize_fb_user(u) -> dict:
    meta = u.user_metadata
    return {
        "uid": u.uid,
        "email": u.email,
        "display_name": u.display_name,
        "email_verified": u.email_verified,
        "disabled": u.disabled,
        "created_at": getattr(meta, "creation_timestamp", None),
        "last_sign_in_at": getattr(meta, "last_sign_in_timestamp", None),
        "is_admin": (u.email or "").lower() in _ADMIN_EMAILS,
    }


def _build_user_metadata(decoded_user: dict) -> dict:
    user_id = decoded_user.get("user_id") or decoded_user.get("uid")
    return {
        "user_id": user_id,
        "uid": decoded_user.get("uid"),
        "email": decoded_user.get("email"),
        "email_verified": decoded_user.get("email_verified"),
    }


def _iso_utc(ts):
    if ts is None:
        return None
    if isinstance(ts, datetime.datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return ts.astimezone(datetime.timezone.utc).isoformat()
    try:
        return ts.isoformat()
    except Exception:
        pass
    try:
        return datetime.datetime.fromisoformat(str(ts)).astimezone(datetime.timezone.utc).isoformat()
    except Exception:
        return str(ts)


# =============================================================================
# 4) Bloques y progreso de auditoría
# =============================================================================
# Los bloques y sus preguntas viven en src/audit_catalog.py (fuente única de verdad,
# compartida con el prompt del auditor y con la validación de cierre del servidor).
AUDIT_BLOCKS = [{"id": b.id, "label": b.label} for b in audit_catalog.AUDIT_CATALOG]
AUDIT_BLOCK_IDS = set(audit_catalog.BLOCK_IDS)
# "deferred": el usuario pidió saltar el bloque; queda incompleto y se puede retomar.
VALID_STATUSES = {"pending", "in_progress", "completed", "deferred"}


def _default_audit_progress_state(uid=None):
    return {"uid": uid, "blocks": {}, "updated_at": SERVER_TIMESTAMP}


def _get_audit_progress_doc(thread_id: str):
    return firestore_db.collection("audit_progress").document(thread_id)


def _build_audit_progress_payload(thread_id, uid, doc_data):
    """
    Estado de la auditoría, con cobertura de preguntas por bloque.

    Elección del bloque activo, en este orden:
      1. el que esté marcado `in_progress`;
      2. el primer `pending` (los aplazados se saltan);
      3. si ya no quedan pendientes, el primer `deferred` (hay que volver a él);
      4. si no queda nada, el último bloque.
    """
    data = doc_data or {}
    blocks_state = data.get("blocks") or {}
    blocks_payload = []
    completed = 0
    active_block_id = None
    first_pending = None
    first_deferred = None

    for block in AUDIT_BLOCKS:
        bid = block["id"]
        stored = blocks_state.get(bid, {}) or {}
        status = stored.get("status", "pending")
        answered = [q for q in (stored.get("answered") or []) if isinstance(q, str)]
        done_m, total_m = audit_catalog.coverage(bid, answered)

        if status == "completed":
            completed += 1
        if status == "in_progress" and active_block_id is None:
            active_block_id = bid
        if status == "pending" and first_pending is None:
            first_pending = bid
        if status == "deferred" and first_deferred is None:
            first_deferred = bid

        blocks_payload.append(
            {
                "id": bid,
                "label": block["label"],
                "status": status,
                "summary": stored.get("summary"),
                "answered": answered,
                "answered_count": done_m,
                "mandatory_count": total_m,
                "pending_questions": [
                    {"id": q.id, "text": q.text}
                    for q in audit_catalog.missing_mandatory(bid, answered)
                ],
                "deferred_reason": stored.get("deferred_reason"),
                "completed_at": _iso_utc(stored.get("completed_at")),
                "updated_at": _iso_utc(stored.get("updated_at")),
            }
        )

    if active_block_id is None:
        active_block_id = (
            first_pending
            or first_deferred
            or (AUDIT_BLOCKS[-1]["id"] if AUDIT_BLOCKS else None)
        )

    total = len(AUDIT_BLOCKS)
    percent = int(round((completed / total) * 100)) if total else 0

    return {
        "thread_id": thread_id,
        "uid": uid,
        "blocks": blocks_payload,
        "active_block_id": active_block_id,
        "completed_count": completed,
        "total_blocks": total,
        "percent": percent,
        "updated_at": _iso_utc(data.get("updated_at")),
    }


def _format_audit_context(progress: dict) -> str:
    """
    Convierte el progreso en el texto que se inyecta en AUDITOR_SYSTEM_PROMPT.

    Lo importante es el bloque "PENDIENTES EN ESTE BLOQUE": es el guion literal del
    modelo. Sin él, el auditor tenía que deducir de la conversación qué preguntas ya
    había hecho, y por eso saltaba preguntas y no cerraba nunca.
    """
    blocks = progress.get("blocks", [])
    active = progress.get("active_block_id", "block_1")
    completed_count = progress.get("completed_count", 0)
    total = progress.get("total_blocks", len(AUDIT_BLOCKS))
    by_id = {b["id"]: b for b in blocks}
    ab = by_id.get(active)

    lines = [f"Progreso global: {completed_count} de {total} bloques cerrados."]

    completed_blocks = [b for b in blocks if b["status"] == "completed"]
    if completed_blocks:
        lines.append("")
        lines.append("BLOQUES YA CERRADOS (no vuelvas a preguntar sobre ellos):")
        for b in completed_blocks:
            lines.append(f"  • {b['label']}: {b.get('summary') or '(sin resumen)'}")

    deferred = [b for b in blocks if b["status"] == "deferred"]
    if deferred:
        lines.append("")
        lines.append("BLOQUES APLAZADOS (incompletos; retómalos con resume_block cuando proceda):")
        for b in deferred:
            reason = b.get("deferred_reason") or "sin motivo registrado"
            lines.append(
                f"  • {b['label']} [{b['id']}] — {b['answered_count']}/{b['mandatory_count']} "
                f"obligatorias respondidas. Motivo: {reason}"
            )

    if not ab:
        lines.append("")
        lines.append("No hay bloque activo: la auditoría está completa.")
        return "\n".join(lines)

    lines.append("")
    lines.append("══ BLOQUE ACTIVO ══")
    lines.append(f"{ab['label']}  [{ab['id']}]")
    lines.append(
        f"Cobertura: {ab['answered_count']} de {ab['mandatory_count']} preguntas obligatorias registradas."
    )

    answered_ids = set(ab.get("answered") or [])
    block_def = audit_catalog.get_block(ab["id"])
    if block_def and answered_ids:
        done = [q for q in block_def.questions if q.id in answered_ids]
        if done:
            lines.append("")
            lines.append("YA RESPONDIDAS (NO las repitas):")
            for q in done:
                lines.append(f"  ✓ ({q.id}) {q.text}")

    pending_q = ab.get("pending_questions") or []
    if pending_q:
        lines.append("")
        lines.append("PENDIENTES EN ESTE BLOQUE — este es tu guion, en este orden:")
        for q in pending_q:
            lines.append(f"  ▸ ({q['id']}) {q['text']}")
        lines.append("")
        lines.append(
            f"Faltan {len(pending_q)}. Formula las siguientes 1-3 de esta lista, registra con "
            "record_block_answers lo que el usuario responda, y no cierres el bloque hasta vaciarla."
        )
    else:
        lines.append("")
        lines.append(
            "PENDIENTES: ninguna. Todas las obligatorias están registradas: cierra el bloque "
            "ahora con complete_audit_block y pasa al siguiente."
        )

    return "\n".join(lines)


# =============================================================================
# 5) Propiedad de hilos (security)
# =============================================================================
def ensure_thread_ownership(thread_id: str, uid: str):
    doc_ref = firestore_db.collection("threads").document(thread_id)
    snap = doc_ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        owner = data.get("uid")
        if owner and owner != uid:
            abort(403, description="No tienes acceso a este hilo.")
    else:
        doc_ref.set({"uid": uid, "created_at": SERVER_TIMESTAMP}, merge=True)


# =============================================================================
# 6) Endpoints
# =============================================================================

@app.route("/audit_blocks", methods=["GET"])
def audit_blocks():
    return ok({"blocks": AUDIT_BLOCKS})


@limiter.limit("12/minute; 2/second")
@app.route("/chat_auditor", methods=["POST"])
def chat_with_main_audit_orchestrator():
    decoded_user = require_firebase_user_or_403()
    persistence_metadata = _build_user_metadata(decoded_user)
    uid = decoded_user["uid"]

    if request.content_type != "application/json":
        return fail("Content-Type must be application/json", 415)
    data = request.get_json(silent=True) or {}

    user_message = (data.get("message") or "").strip()
    thread_id = data.get("thread_id") or str(uuid.uuid4())
    if not user_message:
        return fail("message is required", 400)
    if len(user_message) > 4000:
        return fail("message too long", 413)

    endpoint_name = "/chat_auditor"
    run_id = uuid.uuid4().hex  # synthetic; maintains API contract

    ensure_thread_ownership(thread_id, uid)

    try:
        logger.info(
            "%s: uid=%s thread_id=%s", endpoint_name, uid, thread_id
        )

        # Load audit progress and conversation history
        progress_doc = _get_audit_progress_doc(thread_id).get()
        progress = _build_audit_progress_payload(
            thread_id, uid, progress_doc.to_dict() if progress_doc.exists else {}
        )
        audit_context = _format_audit_context(progress)
        # La auditoría necesita más memoria que el asesor: un bloque con 6 obligatorias
        # consume del orden de 12-16 mensajes, y hay 8 bloques.
        history = get_thread_history(firestore_db, thread_id, limit=80)

        response_text = chat_with_auditor(
            genai_client,
            embed_model,
            firestore_db,
            pinecone_index,
            history,
            user_message,
            thread_id,
            audit_context,
            local_store=local_store,
            uid=uid,
        )

        append_messages(firestore_db, thread_id, user_message, response_text)

        persist_conversation_turn(
            thread_id,
            user_message,
            response_text,
            endpoint_name,
            run_id=run_id,
            assistant_name="AuditorGemini",
            **persistence_metadata,
        )

        return ok(
            {
                "response": response_text,
                "thread_id": thread_id,
                "run_id": run_id,
                "run_status": "completed",
            }
        )

    except Exception as e:
        logger.error("%s: error: %s", endpoint_name, e, exc_info=True)
        persist_conversation_turn(
            thread_id,
            user_message,
            f"API Error: {e}",
            endpoint_name,
            run_id=run_id,
            assistant_name="Exception",
            **persistence_metadata,
        )
        return fail("Internal server error", status=500, details=str(e))


@limiter.limit("20/minute; 3/second")
@app.route("/chat_assistant", methods=["POST"])
def chat_with_sustainability_expert():
    decoded_user = require_firebase_user_or_403()
    persistence_metadata = _build_user_metadata(decoded_user)
    uid = decoded_user["uid"]

    if request.content_type != "application/json":
        return fail("Content-Type must be application/json", 415)
    data = request.get_json(silent=True) or {}

    user_message = (data.get("message") or "").strip()
    thread_id = data.get("thread_id") or str(uuid.uuid4())
    if not user_message:
        return fail("message is required", 400)
    if len(user_message) > 4000:
        return fail("message too long", 413)

    endpoint_name = "/chat_assistant"
    run_id = uuid.uuid4().hex

    ensure_thread_ownership(thread_id, uid)

    try:
        logger.info("%s: uid=%s thread_id=%s", endpoint_name, uid, thread_id)

        history = get_thread_history(firestore_db, thread_id)

        response_text, sources = chat_with_expert(
            genai_client,
            embed_model,
            pinecone_index,
            history,
            user_message,
            thread_id=thread_id,
            local_store=local_store,
            uid=uid,
        )

        append_messages(firestore_db, thread_id, user_message, response_text)

        persist_conversation_turn(
            thread_id,
            user_message,
            response_text,
            endpoint_name,
            run_id=run_id,
            assistant_name="AsesorGemini",
            **persistence_metadata,
        )

        return ok(
            {
                "response": response_text,
                "sources": sources,
                "thread_id": thread_id,
                "run_id": run_id,
                "run_status": "completed",
            }
        )

    except Exception as e:
        logger.error("%s: error: %s", endpoint_name, e, exc_info=True)
        persist_conversation_turn(
            thread_id,
            user_message,
            f"API Error: {e}",
            endpoint_name,
            run_id=run_id,
            assistant_name="Exception",
            **persistence_metadata,
        )
        return fail("Internal server error", status=500, details=str(e))


def _user_files_ref(uid: str):
    return firestore_db.collection("user_documents").document(uid).collection("files")


def _get_user_files(uid: str) -> list[dict]:
    docs = _user_files_ref(uid).order_by("uploaded_at").stream()
    files = []
    for doc in docs:
        d = doc.to_dict()
        uploaded_at = d.get("uploaded_at")
        files.append({
            "doc_id": d["doc_id"],
            "filename": d["filename"],
            "chunk_count": d["chunk_count"],
            "size_bytes": d.get("size_bytes", 0),
            "uploaded_at": uploaded_at.isoformat() if hasattr(uploaded_at, "isoformat") else str(uploaded_at or ""),
        })
    return files


_MAX_USER_FILES = 25


@limiter.limit("10/minute")
@app.route("/upload_document", methods=["POST"])
def upload_document():
    """
    Accepts a PDF upload, chunks + embeds its text, stores vectors permanently
    in the user's Pinecone namespace, and records metadata in Firestore.
    Enforces a 25-file limit per user.
    Returns {doc_id, filename, chunks_indexed, files: [...]}.
    """
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user["uid"]

    if "file" not in request.files:
        return fail("No file field in request", 400)
    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return fail("Empty filename", 400)
    if not uploaded_file.filename.lower().endswith(".pdf"):
        return fail("Solo se admiten archivos PDF.", 415)

    file_bytes = uploaded_file.read()
    if not file_bytes:
        return fail("El archivo está vacío.", 400)
    if len(file_bytes) > 20 * 1024 * 1024:
        return fail("El archivo supera el límite de 20 MB.", 413)

    if pinecone_index is None:
        return fail("El servicio de almacenamiento de documentos no está disponible.", 503)

    # Enforce 25-file limit
    existing_files = _get_user_files(uid)
    if len(existing_files) >= _MAX_USER_FILES:
        return fail(
            f"Has alcanzado el límite de {_MAX_USER_FILES} documentos. "
            "Elimina alguno antes de subir uno nuevo.", 429
        )

    try:
        chunks = extract_pdf_chunks(file_bytes)
        if not chunks:
            return fail("No se pudo extraer texto del PDF.", 422)

        doc_id = str(uuid.uuid4())
        filename = uploaded_file.filename

        upsert_user_file_chunks(embed_model, pinecone_index, uid, doc_id, filename, chunks)

        _user_files_ref(uid).document(doc_id).set({
            "doc_id": doc_id,
            "filename": filename,
            "chunk_count": len(chunks),
            "size_bytes": len(file_bytes),
            "uploaded_at": SERVER_TIMESTAMP,
        })

        logger.info("/upload_document: uid=%s doc_id=%s file=%s chunks=%d", uid, doc_id, filename, len(chunks))

        # Return updated file list so frontend can refresh in one round-trip
        all_files = _get_user_files(uid)
        return ok({
            "doc_id": doc_id,
            "filename": filename,
            "chunks_indexed": len(chunks),
            "files": all_files,
        })
    except Exception as e:
        logger.error("/upload_document: error: %s", e, exc_info=True)
        return fail("Error al procesar el documento.", 500, details=str(e))


@limiter.limit("30/minute")
@app.route("/user_files", methods=["GET"])
def list_user_files():
    """Returns the list of files the authenticated user has uploaded."""
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user["uid"]
    try:
        return ok({"files": _get_user_files(uid)})
    except Exception as e:
        logger.error("/user_files GET: uid=%s error=%s", uid, e, exc_info=True)
        return fail("Error al obtener los documentos.", 500)


@limiter.limit("20/minute")
@app.route("/user_files/<doc_id>", methods=["DELETE"])
def delete_user_file(doc_id: str):
    """Deletes a user's uploaded file from Pinecone and Firestore."""
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user["uid"]

    if pinecone_index is None:
        return fail("El servicio de almacenamiento no está disponible.", 503)

    file_ref = _user_files_ref(uid).document(doc_id)
    file_doc = file_ref.get()
    if not file_doc.exists:
        return fail("Documento no encontrado.", 404)

    data = file_doc.to_dict()
    chunk_count = data.get("chunk_count", 0)
    filename = data.get("filename", doc_id)

    try:
        delete_user_file_chunks(pinecone_index, uid, doc_id, chunk_count)
        file_ref.delete()
        logger.info("/user_files DELETE: uid=%s doc_id=%s file=%s chunks=%d", uid, doc_id, filename, chunk_count)
        return ok({"doc_id": doc_id, "deleted": True, "files": _get_user_files(uid)})
    except Exception as e:
        logger.error("/user_files DELETE: uid=%s doc_id=%s error=%s", uid, doc_id, e, exc_info=True)
        return fail("Error al eliminar el documento.", 500, details=str(e))


@limiter.limit("10/minute")
@app.route("/admin/users", methods=["POST"])
def admin_create_user():
    """Creates a new Firebase Auth user (admin only)."""
    decoded_admin = require_admin_or_403()

    if request.content_type != "application/json":
        return fail("Content-Type must be application/json", 415)
    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    display_name = (data.get("display_name") or "").strip() or None

    if not email or "@" not in email:
        return fail("Email inválido.", 400)
    if len(password) < 6:
        return fail("La contraseña debe tener al menos 6 caracteres.", 400)

    try:
        kwargs = {
            "email": email,
            "password": password,
            "email_verified": bool(data.get("email_verified", False)),
        }
        if display_name:
            kwargs["display_name"] = display_name
        new_user = fb_auth.create_user(**kwargs)
        logger.info(
            "/admin/users POST: admin=%s created uid=%s email=%s",
            decoded_admin.get("email"), new_user.uid, email,
        )
        return ok({"user": _serialize_fb_user(fb_auth.get_user(new_user.uid))})
    except fb_auth.EmailAlreadyExistsError:
        return fail("Ya existe una cuenta con ese correo.", 409)
    except Exception as e:
        logger.error("/admin/users POST: error=%s", e, exc_info=True)
        return fail("Error al crear el usuario.", 500, details=str(e))


@limiter.limit("30/minute")
@app.route("/admin/users", methods=["GET"])
def admin_list_users():
    """Lists Firebase Auth users (admin only)."""
    require_admin_or_403()
    try:
        users = []
        for u in fb_auth.list_users().iterate_all():
            users.append(_serialize_fb_user(u))
            if len(users) >= _MAX_ADMIN_USERS_LISTED:
                break
        users.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
        return ok({"users": users})
    except Exception as e:
        logger.error("/admin/users GET: error=%s", e, exc_info=True)
        return fail("Error al listar usuarios.", 500, details=str(e))


@limiter.limit("20/minute")
@app.route("/admin/users/<uid>", methods=["PATCH"])
def admin_update_user(uid: str):
    """Updates an existing Firebase Auth user (admin only): email, password,
    display_name, disabled, email_verified. Only the provided fields change."""
    decoded_admin = require_admin_or_403()

    if request.content_type != "application/json":
        return fail("Content-Type must be application/json", 415)
    data = request.get_json(silent=True) or {}

    kwargs = {}
    if "email" in data:
        email = (data.get("email") or "").strip().lower()
        if not email or "@" not in email:
            return fail("Email inválido.", 400)
        kwargs["email"] = email
    if data.get("password"):
        if len(data["password"]) < 6:
            return fail("La contraseña debe tener al menos 6 caracteres.", 400)
        kwargs["password"] = data["password"]
    if "display_name" in data:
        kwargs["display_name"] = data.get("display_name") or None
    if "disabled" in data:
        kwargs["disabled"] = bool(data["disabled"])
    if "email_verified" in data:
        kwargs["email_verified"] = bool(data["email_verified"])

    if not kwargs:
        return fail("No se especificaron cambios.", 400)

    try:
        target = fb_auth.get_user(uid)
    except fb_auth.UserNotFoundError:
        return fail("Usuario no encontrado.", 404)

    # No se puede deshabilitar la cuenta de administrador por accidente.
    if (target.email or "").lower() in _ADMIN_EMAILS and kwargs.get("disabled") is True:
        return fail("No se puede deshabilitar la cuenta de administrador.", 400)

    try:
        updated = fb_auth.update_user(uid, **kwargs)
        logger.info(
            "/admin/users PATCH: admin=%s uid=%s fields=%s",
            decoded_admin.get("email"), uid, list(kwargs.keys()),
        )
        return ok({"user": _serialize_fb_user(updated)})
    except fb_auth.EmailAlreadyExistsError:
        return fail("Ya existe una cuenta con ese correo.", 409)
    except Exception as e:
        logger.error("/admin/users PATCH: uid=%s error=%s", uid, e, exc_info=True)
        return fail("Error al actualizar el usuario.", 500, details=str(e))


@limiter.limit("10/minute")
@app.route("/admin/users/<uid>", methods=["DELETE"])
def admin_delete_user(uid: str):
    """Deletes a Firebase Auth user (admin only). Refuses to delete admin accounts."""
    decoded_admin = require_admin_or_403()

    try:
        target = fb_auth.get_user(uid)
    except fb_auth.UserNotFoundError:
        return fail("Usuario no encontrado.", 404)

    if (target.email or "").lower() in _ADMIN_EMAILS:
        return fail("No se puede eliminar la cuenta de administrador.", 400)

    try:
        fb_auth.delete_user(uid)
        logger.info(
            "/admin/users DELETE: admin=%s uid=%s email=%s",
            decoded_admin.get("email"), uid, target.email,
        )
        return ok({"uid": uid, "deleted": True})
    except Exception as e:
        logger.error("/admin/users DELETE: uid=%s error=%s", uid, e, exc_info=True)
        return fail("Error al eliminar el usuario.", 500, details=str(e))


@limiter.limit("10/minute")
@app.route("/admin/ingest_document", methods=["POST"])
def admin_ingest_document():
    """Ingests a document into the Pinecone RAG index."""
    decoded_user = require_firebase_user_or_403()

    if request.content_type != "application/json":
        return fail("Content-Type must be application/json", 415)
    data = request.get_json(silent=True) or {}

    content = (data.get("content") or "").strip()
    if not content:
        return fail("content is required", 400)
    if len(content) > 50_000:
        return fail("content too long (max 50 000 chars)", 413)

    doc_id = data.get("doc_id") or None
    title = data.get("title") or ""
    source_url = data.get("source_url") or ""
    doc_type = data.get("doc_type") or ""

    try:
        used_id = ingest_document(
            embed_model, pinecone_index, doc_id, content, title, source_url, doc_type
        )
        logger.info(
            "/admin/ingest_document: uid=%s doc_id=%s", decoded_user.get("uid"), used_id
        )
        return ok({"doc_id": used_id, "status": "ingested"})
    except RuntimeError as e:
        return fail(str(e), status=503)
    except Exception as e:
        logger.error("/admin/ingest_document: error: %s", e, exc_info=True)
        return fail("Failed to ingest document", status=500, details=str(e))


@app.route("/chat_history/recents", methods=["GET"])
def get_recent_chat_history():
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user.get("uid")
    try:
        limit = request.args.get("limit", default=5, type=int)
    except (TypeError, ValueError):
        limit = 5

    try:
        conversations = fetch_recent_conversations_for_user(uid=uid, limit=limit)
        return ok({"conversations": conversations})
    except ValueError as err:
        return fail(str(err), status=400)
    except Exception as exc:
        logger.error("Failed to fetch recent chat history for uid=%s: %s", uid, exc, exc_info=True)
        return fail("No se pudo obtener el historial reciente.", status=500)


@app.route("/chat_history/thread/<thread_id>", methods=["GET"])
def get_chat_history_thread(thread_id: str):
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user.get("uid")
    ensure_thread_ownership(thread_id, uid)

    try:
        conversation = fetch_conversation_thread(uid=uid, thread_id=thread_id)
        return ok(conversation)
    except ValueError as err:
        return fail(str(err), status=400)
    except Exception as exc:
        logger.error("Failed to fetch chat thread %s for uid=%s: %s", thread_id, uid, exc, exc_info=True)
        return fail("No se pudo obtener la conversacion solicitada.", status=500)


@app.route("/audit_progress/<thread_id>", methods=["GET"])
def get_audit_progress(thread_id: str):
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user.get("uid")
    ensure_thread_ownership(thread_id, uid)

    try:
        doc = _get_audit_progress_doc(thread_id).get()
        if doc.exists:
            data = doc.to_dict() or {}
            stored_uid = data.get("uid")
            if stored_uid and stored_uid != uid:
                abort(403, description="No tienes acceso a este progreso de auditoria.")
        else:
            data = _default_audit_progress_state(uid=uid)
    except Exception as exc:
        logger.error("Failed to fetch audit progress for thread=%s: %s", thread_id, exc, exc_info=True)
        return fail("No se pudo obtener el progreso de auditoria.", status=500)

    payload = _build_audit_progress_payload(thread_id, uid, data)
    return ok(payload)


@app.route("/audit_progress/<thread_id>", methods=["POST"])
def update_audit_progress(thread_id: str):
    decoded_user = require_firebase_user_or_403()
    uid = decoded_user.get("uid")
    ensure_thread_ownership(thread_id, uid)

    body = request.get_json(silent=True) or {}
    block_id = body.get("block_id")
    status = (body.get("status") or "completed").strip().lower()
    summary = body.get("summary")

    if not block_id or block_id not in AUDIT_BLOCK_IDS:
        return fail("block_id invalido. Debe corresponderse con un bloque del proceso.", status=400)
    if status not in VALID_STATUSES:
        return fail(
            f"status invalido. Valores permitidos: {', '.join(sorted(VALID_STATUSES))}", status=400
        )

    doc_ref = _get_audit_progress_doc(thread_id)

    @firestore.transactional
    def _tx_update_progress(tx, ref, _uid, _block_id, _status, _summary):
        snap = ref.get(transaction=tx)
        if snap.exists:
            data = snap.to_dict() or {}
            stored_uid = data.get("uid")
            if stored_uid and stored_uid != _uid:
                abort(403, description="No tienes acceso a este progreso de auditoria.")
        else:
            data = _default_audit_progress_state(uid=_uid)

        block_state = (data.setdefault("blocks", {}).get(_block_id) or {})
        block_state["status"] = _status
        block_state["updated_at"] = SERVER_TIMESTAMP
        if _summary is not None:
            block_state["summary"] = _summary
        block_state["completed_at"] = SERVER_TIMESTAMP if _status == "completed" else None

        data["blocks"][_block_id] = block_state
        data["uid"] = _uid
        data["updated_at"] = SERVER_TIMESTAMP

        tx.set(ref, data, merge=True)
        return data

    try:
        tx = firestore_db.transaction()
        data = _tx_update_progress(tx, doc_ref, uid, block_id, status, summary)
    except Exception as exc:
        logger.error(
            "Failed to update audit progress thread=%s block=%s: %s", thread_id, block_id, exc, exc_info=True
        )
        return fail("No se pudo actualizar el progreso de auditoria.", status=500)

    payload = _build_audit_progress_payload(thread_id, uid, data)
    return ok(payload)


@app.route("/health", methods=["GET"])
def health_check():
    return ok({"status": "healthy"})


@app.route("/readyz", methods=["GET"])
def readyz():
    """Checks Firestore connectivity and Gemini model availability."""
    try:
        firestore_db.collection("_ready").document("ping").get()
        genai_client.models.get(model="gemini-2.5-flash")
        return ok({"status": "ready"})
    except Exception as e:
        return fail("degraded", status=503, details=str(e))


# =============================================================================
# 7) Entry point
# =============================================================================
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
    )
