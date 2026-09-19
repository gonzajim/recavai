# src/gemini_service.py
from google.genai import types
from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from src.config import logger
from src.assistant_instructions import AUDITOR_SYSTEM_PROMPT, EXPERT_SYSTEM_PROMPT
from src import audit_catalog
from src.rag_service import (
    generate_embedding,
    search_documents,
    search_user_documents,
    detect_category_filter,
    classify_question,
    get_routing_strategy,
    get_graph_context,
)
from src.context_orchestrator import search_hybrid

_GEMINI_MODEL = "gemini-2.5-flash"

# Context window budget per model (chars, with 0.90 safety factor applied)
_CONTEXT_BUDGET: dict[str, int] = {
    "gemini-2.5-flash":      180_000,
    "gemini-2.5-flash-lite": 180_000,
    "gemini-2.5-pro":        540_000,
}

# ---------------------------------------------------------------------------
# Tool declarations for the auditor
# ---------------------------------------------------------------------------

AUDITOR_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="invoke_sustainability_expert",
            description=(
                "Consulta al experto en sostenibilidad cuando el usuario formula "
                "una pregunta técnica o normativa (CSRD, CSDDD, NEIS, OCDE, "
                "diligencia debida, reporting, etc.). "
                "Devuelve una respuesta precisa y fundamentada."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description="Pregunta concreta que debe responder el experto en sostenibilidad.",
                    )
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="record_block_answers",
            description=(
                "Registra qué preguntas del bloque activo han quedado respondidas. "
                "LLÁMALA EN CADA TURNO en que el usuario responda algo, antes de formular las "
                "siguientes preguntas. Si no registras, el sistema no sabe que has avanzado y "
                "no te dejará cerrar el bloque. "
                "Registra también las preguntas que el usuario haya respondido de pasada al "
                "contestar a otra, y aquellas cuya respuesta real sea 'no aplica' o 'no tenemos eso'."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "block_id": types.Schema(
                        type=types.Type.STRING,
                        description="ID del bloque activo: block_1 a block_8.",
                    ),
                    "question_ids": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description=(
                            "Identificadores de las preguntas respondidas, tal y como aparecen "
                            "entre paréntesis en el catálogo. Ejemplo: [\"block_1_q1\", \"block_1_q3\"]."
                        ),
                    ),
                    "notes": types.Schema(
                        type=types.Type.STRING,
                        description="1-2 frases con lo que el usuario ha contestado.",
                    ),
                },
                required=["block_id", "question_ids"],
            ),
        ),
        types.FunctionDeclaration(
            name="complete_audit_block",
            description=(
                "Cierra el bloque activo y guarda el resumen de hallazgos. "
                "El servidor COMPRUEBA la cobertura: si queda alguna pregunta obligatoria sin "
                "registrar mediante record_block_answers, la llamada se rechaza y devuelve la lista "
                "exacta de lo que falta. En ese caso formula esas preguntas en vez de reintentar."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "block_id": types.Schema(
                        type=types.Type.STRING,
                        description="ID del bloque a cerrar: block_1 a block_8.",
                    ),
                    "summary": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Resumen de 3-5 frases con los hallazgos principales del bloque: "
                            "qué información se ha recogido, qué fortalezas y qué brechas se han detectado, "
                            "y clasificación de brechas (Crítico / Alto / Medio) si las hay."
                        ),
                    ),
                },
                required=["block_id", "summary"],
            ),
        ),
        types.FunctionDeclaration(
            name="defer_block",
            description=(
                "Aplaza el bloque activo dejándolo INCOMPLETO y pasa al siguiente. "
                "Úsala SOLO cuando el usuario pida saltar el bloque, dejarlo para más tarde o diga "
                "que ahora no dispone de esos datos. Nunca por iniciativa propia. "
                "Lo ya registrado se conserva para cuando se retome."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "block_id": types.Schema(
                        type=types.Type.STRING,
                        description="ID del bloque a aplazar: block_1 a block_8.",
                    ),
                    "reason": types.Schema(
                        type=types.Type.STRING,
                        description="Motivo del aplazamiento, en palabras del usuario.",
                    ),
                },
                required=["block_id", "reason"],
            ),
        ),
        types.FunctionDeclaration(
            name="resume_block",
            description=(
                "Retoma un bloque aplazado o pendiente y lo convierte en el bloque activo. "
                "Úsala cuando el usuario quiera volver a él o cuando los demás bloques ya estén "
                "cerrados. Devuelve las preguntas que siguen pendientes en ese bloque."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "block_id": types.Schema(
                        type=types.Type.STRING,
                        description="ID del bloque a retomar: block_1 a block_8.",
                    ),
                },
                required=["block_id"],
            ),
        ),
    ]
)

_AUDITOR_TOOL_CONFIG = types.ToolConfig(
    function_calling_config=types.FunctionCallingConfig(mode="AUTO")
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chat_with_auditor(
    genai_client,
    embed_model,
    firestore_db,
    pinecone_index,
    history: list[dict],
    user_message: str,
    thread_id: str,
    audit_context: str,
    local_store=None,
    uid: str | None = None,
) -> str:
    """
    Runs one auditor turn with tool-call looping.
    Handles: invoke_sustainability_expert and complete_audit_block.
    Returns the final text response.
    """
    system = (
        AUDITOR_SYSTEM_PROMPT
        .replace("{audit_context}", audit_context)
        .replace("{block_catalog}", audit_catalog.render_catalog())
    )

    contents: list = list(history) + [
        types.Content(role="user", parts=[types.Part(text=user_message)])
    ]

    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[AUDITOR_TOOLS],
        tool_config=_AUDITOR_TOOL_CONFIG,
    )

    # Un turno normal encadena record_block_answers → complete_audit_block → (consulta al
    # experto). 8 rondas dan margen para eso más un reintento tras un cierre rechazado.
    max_rounds = 8
    for _ in range(max_rounds):
        response = genai_client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=contents,
            config=config,
        )

        if not response.function_calls:
            break

        function_responses = []
        for fc in response.function_calls:
            result = _dispatch_tool(
                fc, genai_client, embed_model, firestore_db, pinecone_index, thread_id,
                local_store=local_store, uid=uid,
            )
            function_responses.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    )
                )
            )

        contents.append(response.candidates[0].content)
        contents.append(types.Content(role="user", parts=function_responses))

    return _extract_text(response)


def chat_with_expert(
    genai_client,
    embed_model,
    pinecone_index,
    history: list[dict],
    user_message: str,
    thread_id: str | None = None,
    local_store=None,
    uid: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Runs one advisor turn with RAG augmentation.
    Searches user's Pinecone namespace first (if uid provided), then global corpus.
    Returns (response_text, sources).
    """
    augmented_message, sources = _build_rag_message(
        embed_model, pinecone_index, user_message,
        thread_id=thread_id, local_store=local_store, uid=uid,
    )

    contents: list = list(history) + [
        types.Content(role="user", parts=[types.Part(text=augmented_message)])
    ]

    config = types.GenerateContentConfig(system_instruction=EXPERT_SYSTEM_PROMPT)

    response = genai_client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    return _extract_text(response), sources


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dispatch_tool(
    fc, genai_client, embed_model, firestore_db, pinecone_index, thread_id: str,
    local_store=None, uid: str | None = None,
) -> str:
    name = fc.name
    args = dict(fc.args) if fc.args else {}

    if name == "invoke_sustainability_expert":
        query = args.get("query", "")
        logger.info("Tool invoke_sustainability_expert: thread=%s query=%r", thread_id, query[:80])
        try:
            text, _ = chat_with_expert(
                genai_client, embed_model, pinecone_index, [], query,
                thread_id=thread_id, local_store=local_store, uid=uid,
            )
            return text
        except Exception:
            logger.error("invoke_sustainability_expert failed", exc_info=True)
            return "El experto no pudo procesar la consulta en este momento."

    if name == "record_block_answers":
        return _record_block_answers(
            firestore_db, thread_id, args.get("block_id", ""),
            args.get("question_ids") or [], args.get("notes", ""),
        )

    if name == "complete_audit_block":
        return _complete_audit_block(
            firestore_db, thread_id, args.get("block_id", ""), args.get("summary", ""),
        )

    if name == "defer_block":
        return _defer_block(
            firestore_db, thread_id, args.get("block_id", ""), args.get("reason", ""),
        )

    if name == "resume_block":
        return _resume_block(firestore_db, thread_id, args.get("block_id", ""))

    logger.warning("Unknown tool called: %s", name)
    return f"Herramienta desconocida: {name}"


# ---------------------------------------------------------------------------
# Estado de la auditoría — el servidor es quien decide, no el modelo
# ---------------------------------------------------------------------------
def _progress_doc(firestore_db, thread_id: str):
    return firestore_db.collection("audit_progress").document(thread_id)


def _block_state(firestore_db, thread_id: str, block_id: str) -> dict:
    """Estado guardado de un bloque. Devuelve {} si no hay nada aún."""
    try:
        snap = _progress_doc(firestore_db, thread_id).get()
        data = snap.to_dict() if snap.exists else {}
        return ((data or {}).get("blocks") or {}).get(block_id, {}) or {}
    except Exception:
        logger.error("No se pudo leer el progreso thread=%s", thread_id, exc_info=True)
        return {}


def _write_block(firestore_db, thread_id: str, block_id: str, patch: dict) -> bool:
    try:
        _progress_doc(firestore_db, thread_id).set(
            {"blocks": {block_id: {**patch, "updated_at": SERVER_TIMESTAMP}},
             "updated_at": SERVER_TIMESTAMP},
            merge=True,
        )
        return True
    except Exception:
        logger.error("No se pudo escribir el progreso thread=%s block=%s",
                     thread_id, block_id, exc_info=True)
        return False


def _pending_text(block_id: str, answered: list[str]) -> str:
    missing = audit_catalog.missing_mandatory(block_id, answered)
    if not missing:
        return "No queda ninguna pregunta obligatoria pendiente en este bloque."
    lines = [f"Pendientes en {block_id} ({len(missing)}):"]
    lines += [f"  ▸ ({q.id}) {q.text}" for q in missing]
    return "\n".join(lines)


def _record_block_answers(
    firestore_db, thread_id: str, block_id: str, question_ids, notes: str
) -> str:
    if not audit_catalog.get_block(block_id):
        return f"Bloque desconocido: {block_id}. Usa block_1..block_8."

    valid = audit_catalog.valid_question_ids(block_id)
    incoming = [q for q in question_ids if isinstance(q, str)]
    accepted = sorted({q for q in incoming if q in valid})
    rejected = sorted({q for q in incoming if q not in valid})

    state = _block_state(firestore_db, thread_id, block_id)
    answered = sorted(set(state.get("answered") or []) | set(accepted))

    patch = {"answered": answered}
    if state.get("status") not in ("completed",):
        patch["status"] = "in_progress"
    if notes:
        patch["notes"] = ((state.get("notes") or "") + " " + notes).strip()[:2000]

    if not _write_block(firestore_db, thread_id, block_id, patch):
        return ("No se pudo guardar el avance. Continúa la entrevista y vuelve a registrarlo "
                "en el siguiente turno.")

    done, total = audit_catalog.coverage(block_id, answered)
    out = [f"Registradas {len(accepted)} respuesta(s). Cobertura {block_id}: {done}/{total} obligatorias."]
    if rejected:
        out.append(f"Ids no reconocidos e ignorados: {', '.join(rejected)}. Usa los del catálogo.")
    out.append(_pending_text(block_id, answered))
    if done >= total:
        out.append("Todas las obligatorias están cubiertas: cierra el bloque con complete_audit_block.")
    logger.info("record_block_answers: thread=%s block=%s +%d -> %d/%d",
                thread_id, block_id, len(accepted), done, total)
    return "\n".join(out)


def _complete_audit_block(firestore_db, thread_id: str, block_id: str, summary: str) -> str:
    """Cierra un bloque SOLO si todas sus obligatorias están registradas.

    Esta comprobación está aquí, en el servidor, y no confiada al prompt: antes el modelo
    podía cerrar un bloque tras dos respuestas y la auditoría quedaba vacía."""
    if not audit_catalog.get_block(block_id):
        return f"Bloque desconocido: {block_id}. Usa block_1..block_8."

    state = _block_state(firestore_db, thread_id, block_id)
    answered = state.get("answered") or []
    missing = audit_catalog.missing_mandatory(block_id, answered)

    if missing:
        done, total = audit_catalog.coverage(block_id, answered)
        logger.info("complete_audit_block RECHAZADO: thread=%s block=%s %d/%d",
                    thread_id, block_id, done, total)
        return (
            f"CIERRE RECHAZADO. El bloque {block_id} tiene {len(missing)} pregunta(s) obligatoria(s) "
            f"sin registrar ({done}/{total} cubiertas).\n"
            + _pending_text(block_id, answered)
            + "\nFormula ahora esas preguntas. Si el usuario ya las respondió, regístralas primero "
              "con record_block_answers. Si el usuario quiere saltarse el bloque, usa defer_block."
        )

    if not _write_block(firestore_db, thread_id, block_id, {
        "status": "completed", "summary": summary, "completed_at": SERVER_TIMESTAMP,
    }):
        return f"Bloque {block_id} cubierto, pero no se pudo persistir el cierre. Reinténtalo."

    nxt = _next_block_id(firestore_db, thread_id, after=block_id)
    logger.info("complete_audit_block OK: thread=%s block=%s next=%s", thread_id, block_id, nxt)
    if nxt:
        nb = audit_catalog.get_block(nxt)
        return (f"Bloque {block_id} cerrado correctamente. Siguiente bloque activo: {nxt} — "
                f"{nb.label if nb else nxt}. Anúncialo y empieza por sus primeras preguntas obligatorias.")
    return (f"Bloque {block_id} cerrado. Era el último bloque pendiente: presenta ahora el "
            "resumen ejecutivo de toda la auditoría.")


def _defer_block(firestore_db, thread_id: str, block_id: str, reason: str) -> str:
    if not audit_catalog.get_block(block_id):
        return f"Bloque desconocido: {block_id}. Usa block_1..block_8."

    state = _block_state(firestore_db, thread_id, block_id)
    if state.get("status") == "completed":
        return f"El bloque {block_id} ya está cerrado; no hace falta aplazarlo."

    answered = state.get("answered") or []
    if not _write_block(firestore_db, thread_id, block_id,
                        {"status": "deferred", "deferred_reason": reason or "a petición del usuario"}):
        return "No se pudo aplazar el bloque. Continúa con él."

    done, total = audit_catalog.coverage(block_id, answered)
    nxt = _next_block_id(firestore_db, thread_id, after=block_id)
    logger.info("defer_block: thread=%s block=%s %d/%d next=%s", thread_id, block_id, done, total, nxt)
    msg = (f"Bloque {block_id} aplazado con {done}/{total} obligatorias registradas. "
           "Se conserva lo respondido y podrá retomarse con resume_block.")
    if nxt:
        nb = audit_catalog.get_block(nxt)
        return msg + f" Siguiente bloque activo: {nxt} — {nb.label if nb else nxt}."
    return msg + " No quedan más bloques por delante: confirma con el usuario si desea retomarlo ahora."


def _resume_block(firestore_db, thread_id: str, block_id: str) -> str:
    if not audit_catalog.get_block(block_id):
        return f"Bloque desconocido: {block_id}. Usa block_1..block_8."

    state = _block_state(firestore_db, thread_id, block_id)
    if state.get("status") == "completed":
        return f"El bloque {block_id} ya está cerrado. No procede retomarlo."

    if not _write_block(firestore_db, thread_id, block_id, {"status": "in_progress"}):
        return "No se pudo retomar el bloque."

    answered = state.get("answered") or []
    done, total = audit_catalog.coverage(block_id, answered)
    b = audit_catalog.get_block(block_id)
    logger.info("resume_block: thread=%s block=%s %d/%d", thread_id, block_id, done, total)
    return (f"Bloque {block_id} — {b.label} retomado ({done}/{total} obligatorias ya registradas).\n"
            + _pending_text(block_id, answered)
            + "\nContinúa por esas preguntas; no repitas las ya registradas.")


def _next_block_id(firestore_db, thread_id: str, after: str) -> str | None:
    """Primer bloque posterior que no esté cerrado ni aplazado; si no hay, el primero
    pendiente desde el principio; si tampoco, el primero aplazado."""
    try:
        snap = _progress_doc(firestore_db, thread_id).get()
        blocks = ((snap.to_dict() if snap.exists else {}) or {}).get("blocks") or {}
    except Exception:
        blocks = {}

    def status_of(bid: str) -> str:
        return (blocks.get(bid) or {}).get("status", "pending")

    order = list(audit_catalog.BLOCK_IDS)
    start = order.index(after) + 1 if after in order else 0
    for bid in order[start:] + order[:start]:
        if status_of(bid) in ("pending", "in_progress"):
            return bid
    for bid in order:
        if status_of(bid) == "deferred":
            return bid
    return None


def _build_rag_message(
    embed_model,
    pinecone_index,
    user_message: str,
    thread_id: str | None = None,
    local_store=None,
    model_name: str = _GEMINI_MODEL,
    uid: str | None = None,
) -> tuple[str, list[dict]]:
    """
    Retrieves relevant chunks and builds the augmented message for the LLM.

    Search order:
      1. User's Pinecone namespace (uid) — permanent uploaded files
      2. FAISS (local_store) — ephemeral session uploads (legacy, kept for running instances)
      3. Global Pinecone corpus (CSRD/CSDDD/NEIS/OCDE)
    Results from uploaded documents appear first; corpus follows.
    augmented_message prepends numbered excerpts so the model can cite [1]…[N].
    """
    char_budget = _CONTEXT_BUDGET.get(model_name, 180_000)
    question_type = classify_question(user_message)
    strategy = get_routing_strategy(question_type)
    logger.info(
        "RAG routing: question_type=%s strategy=%s model=%s budget=%d",
        question_type, strategy, model_name, char_budget,
    )

    use_hybrid_faiss = (
        local_store is not None
        and thread_id is not None
        and local_store.chunk_count(thread_id) > 0
    )

    try:
        # Always compute the embedding once — reused for both user-namespace and corpus search
        embedding = generate_embedding(embed_model, user_message)

        # 1. User's permanent files in their Pinecone namespace
        user_docs: list[dict] = []
        if uid and pinecone_index is not None:
            user_docs = search_user_documents(pinecone_index, embedding, uid)
            user_docs = sorted(user_docs, key=lambda d: d.get("score", 0), reverse=True)

        # 2. Legacy FAISS (ephemeral; will be empty for new uploads)
        local_docs: list[dict] = []
        if use_hybrid_faiss:
            _, local_docs = search_hybrid(
                thread_id, user_message, embed_model, pinecone_index, local_store
            )
            local_docs = sorted(local_docs, key=lambda d: d.get("score", 0), reverse=True)

        # 3. Global corpus
        if pinecone_index is None:
            pine_docs: list[dict] = []
        else:
            cat_filter = detect_category_filter(user_message)
            pine_docs = search_documents(pinecone_index, embedding, metadata_filter=cat_filter)
            if cat_filter and not pine_docs:
                logger.info("RAG: category filter returned 0 results, retrying without filter")
                pine_docs = search_documents(pinecone_index, embedding, metadata_filter=None)

        docs = user_docs + local_docs + pine_docs

        if not docs and pinecone_index is None:
            return user_message, []

    except Exception:
        logger.error("RAG retrieval failed", exc_info=True)
        return user_message, []

    context_parts = []
    sources = []
    used_chars = len(user_message)

    for i, doc in enumerate(docs, 1):
        title = doc.get("title") or "Documento"
        category = doc.get("category", "")
        score = doc.get("score", 0.0)
        page = doc.get("page")
        total_pages = doc.get("total_pages")
        content = doc.get("content", "").strip()

        meta_parts = [category] if category else []
        if page is not None:
            meta_parts.append(f"p.{page}/{total_pages}" if total_pages else f"p.{page}")
        meta_parts.append(f"relevancia={score:.2f}")
        header = f"[{i}] {title} ({', '.join(meta_parts)})"

        chunk = f"{header}\n{content}"
        if content and used_chars + len(chunk) <= char_budget:
            context_parts.append(chunk)
            used_chars += len(chunk)

        sources.append({
            "index": i,
            "title": title,
            "category": category,
            "score": round(score, 3),
            "excerpt": content[:220] + ("…" if len(content) > 220 else ""),
            "page": page,
            "total_pages": total_pages,
        })

    # Graph context for operational/resource questions (Neo4j)
    graph_section = ""
    if strategy == "hybrid":
        graph_budget = max(0, char_budget - used_chars - 500)
        if graph_budget > 0:
            graph_ctx = get_graph_context(user_message, char_budget=graph_budget)
            if graph_ctx:
                graph_section = f"\n\n{graph_ctx}"
                used_chars += len(graph_ctx)

    if not context_parts and not graph_section:
        return user_message, []

    semantic_block = "\n\n".join(context_parts)

    if semantic_block and graph_section:
        augmented = (
            f"## CONTEXTO SEMÁNTICO\n"
            f"Fragmentos relevantes de la base documental "
            f"(cítalos inline como [1], [2]… cuando los uses en tu respuesta):\n\n"
            f"{semantic_block}"
            f"\n\n## RELACIONES DE GRAFO{graph_section}"
            f"\n\n---\n\nPregunta: {user_message}"
        )
    elif semantic_block:
        augmented = (
            f"Fragmentos relevantes de la base documental "
            f"(cítalos inline como [1], [2]… cuando los uses en tu respuesta):\n\n"
            f"{semantic_block}\n\n---\n\nPregunta: {user_message}"
        )
    else:
        augmented = (
            f"{graph_section.strip()}\n\n---\n\nPregunta: {user_message}"
        )

    logger.info(
        "RAG message built: %d semantic chunks, graph=%s, total_chars=%d",
        len(context_parts), bool(graph_section), used_chars,
    )
    return augmented, sources


def _extract_text(response) -> str:
    try:
        text = response.text
        if text:
            return text.strip()
    except Exception:
        pass
    try:
        parts = response.candidates[0].content.parts
        return "\n".join(p.text for p in parts if hasattr(p, "text") and p.text).strip()
    except Exception:
        logger.error("Could not extract text from Gemini response", exc_info=True)
        return "No se pudo obtener una respuesta del modelo."
