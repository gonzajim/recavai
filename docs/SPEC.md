# SPEC — RecavAI (repositorio `recavai`)

> **Machine-readable specification for spec-driven development (SDD).**
> Normative document. Every requirement has a stable ID. Agents implementing, modifying or reviewing this system MUST treat this file as the contract and `docs/documentacion.html` as its human narrative.

| Field | Value |
|---|---|
| `spec_version` | 1.0.0 |
| `generated_at` | 2026-09-19 |
| `derived_from_commit` | `7199b2f` (branch `paper/linea1-kb-construction`) |
| `source_of_truth` | The code. This spec describes observed behaviour, not intent. Where they diverge, the code wins and this spec is a defect. |
| `language_of_system` | Spanish (UI, prompts, corpus) |
| `language_of_spec` | English |

## 0. How to use this spec

1. **Before changing code**, locate the requirement IDs the change touches. If no requirement covers it, add one in the same commit.
2. **Requirement keywords** follow RFC 2119: MUST, MUST NOT, SHOULD, MAY.
3. **Invariants** (`INV-*`) are the subset that MUST hold after every change. Breaking one is a release blocker.
4. **Known deviations** (`DEBT-*`) record where the system currently violates what it should be. Do not "fix" a `DEBT-*` silently — it is tracked, and some are load-bearing.
5. **Verification** (§15) lists the commands that check conformance. Any change SHOULD be accompanied by the relevant command's output.
6. This spec does **not** authorise deployment, writes to production data stores, or git pushes. See `INV-OPS-1`.

---

## 1. System identity and scope

**SYS-1** The system is a retrieval-augmented conversational assistant for European sustainability compliance (CSRD, CSDDD, ESRS/NEIS, GRI, OECD), operating in Spanish.

**SYS-2** The system exposes exactly two conversational modes, sharing one knowledge base and one backend: `advisor` (user-led Q&A with citations) and `auditor` (system-led structured interview in 8 blocks).

**SYS-3** Deployed surfaces:

| id | Surface | Host | Artefact |
|---|---|---|---|
| `SURF-CHAT` | End-user chat widget | `recava-auditor-dev.web.app` | `public/chatbot/` (static) |
| `SURF-ADMIN` | Expert/admin panel | `recava-auditor-dev-panel.web.app` | `public/admin-panel/build/` (React) |
| `SURF-API` | Orchestrator REST API | Cloud Run `orchestrator-dev`, `europe-west1` | `app.py` + `src/` |
| `SURF-FN` | History functions | Cloud Functions `europe-west1` | `functions/index.js` |

**SYS-4** Out of scope for the running service: `src/corpus_pipeline.py`, `src/rag_benchmark.py`, `src/kb_experiment.py`. These are offline/research tools and MUST NOT be imported by `app.py` or any module it loads.

---

## 2. Domain glossary

| Term | Definition |
|---|---|
| `thread_id` | UUID identifying one conversation. Owns messages and audit progress. |
| `uid` | Firebase Auth user id. Also the Pinecone namespace for that user's documents. |
| `block` | One of 8 audit sections, `block_1`..`block_8`. |
| `[M] question` | Mandatory question inside a block. A block cannot be closed while any is unanswered. |
| `chunk` | Indexed text unit in Pinecone. |
| `source` | A retrieved chunk presented to the user as a numbered citation `[n]`. |
| `corpus` | The global regulatory document set, Pinecone default namespace. |
| `user document` | A PDF uploaded by a user, stored in namespace `uid`. |
| `golden set` | Fixed evaluation questions with reference answers and expected sources. |

---

## 3. Architecture constraints

**ARC-1** All client→data access MUST pass through `SURF-API`. Clients MUST NOT hold credentials for Gemini, Pinecone, Neo4j or BigQuery.

**ARC-2** Embeddings MUST be computed in-process via `sentence-transformers`. No external embedding API.

**ARC-3** The embedding model MUST be identical to the one used to build the Pinecone index being queried. Changing `EMBEDDING_MODEL_NAME` without reindexing is a correctness break, not a configuration change.

**ARC-4** `src/config.py` is the single place where external clients are constructed. Modules MUST import clients from it, never construct their own.

**ARC-5** `src/corpus_pipeline.embed_texts` is the single embedding entry point for offline tooling; `src/rag_service.generate_embedding` for the online service. Neither may be duplicated.

**ARC-6** Failure isolation: a failure of Pinecone, Neo4j, BigQuery or FAISS MUST degrade the answer, not fail the request. Only Gemini and Firebase Auth failures may produce a 5xx/4xx.

---

## 4. Component inventory

| id | Path | Responsibility | Status |
|---|---|---|---|
| `CMP-APP` | `app.py` | HTTP routing, auth, CORS, rate limits, thread ownership, admin endpoints | production |
| `CMP-CFG` | `src/config.py` | Env loading, client construction, embedding model, FAISS store | production |
| `CMP-GEM` | `src/gemini_service.py` | Chat turns, tool-call loop, augmented-message construction | production |
| `CMP-RAG` | `src/rag_service.py` | Embedding, Pinecone search, category filter, question routing, Neo4j, user-PDF chunking | production |
| `CMP-PROMPT` | `src/assistant_instructions.py` | System prompts for auditor and advisor | production |
| `CMP-CAT` | `src/audit_catalog.py` | Single source of truth for audit blocks and questions; coverage and close-gate logic | production |
| `CMP-HIST` | `src/history_service.py` | Firestore history ↔ Gemini format, role sanitisation | production |
| `CMP-PERS` | `src/persistence_service.py`, `src/bigquery_service.py` | BigQuery turn logging and history queries | production |
| `CMP-ORCH` | `src/context_orchestrator.py` | Concurrent Pinecone+FAISS fan-out | partial |
| `CMP-FAISS` | `src/local_vector_store.py` | Per-session in-RAM vector store | legacy |
| `CMP-PIPE` | `src/corpus_pipeline.py` | Offline corpus indexing | offline |
| `CMP-BENCH` | `src/rag_benchmark.py` | Retrieval quality measurement | research |
| `CMP-EXP` | `src/kb_experiment.py` | Factorial KB-construction experiment | research |
| `CMP-UI-CHAT` | `public/chatbot/` | Chat widget (vanilla JS, ~1900 LOC) | production |
| `CMP-UI-ADMIN` | `public/admin-panel/src/` | React + MUI panel | production |
| `CMP-FN` | `functions/index.js` | BigQuery history read/update | production |

---

## 5. API contracts

### 5.0 Envelope

**API-0.1** Success responses MUST be `200` with body `{"ok": true, "data": <object>}`, optionally `"meta"`.

**API-0.2** Failure responses MUST be `{"ok": false, "error": {"message": <string>, ...details}}` with a non-2xx status.

**API-0.3** Every response MUST carry headers: `X-Request-Id`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`.

**API-0.4** Every request MUST be logged at start (`evt: request_start`, id, path, method) and end (`evt: request_end`, id, status, ms) as single-line JSON.

**API-0.5** Auth levels: `public` (none), `user` (`require_firebase_user_or_403`), `admin` (`require_admin_or_403`).

**API-0.6** Global rate limit is `120/minute` per remote address; per-endpoint limits below are additional.

### 5.1 Endpoint table

| id | Method | Path | Auth | Rate limit | Success `data` |
|---|---|---|---|---|---|
| `API-CHAT-A` | POST | `/chat_assistant` | user | 20/min; 3/s | `{response, sources[], thread_id, run_id, run_status}` |
| `API-CHAT-B` | POST | `/chat_auditor` | user | 12/min; 2/s | `{response, thread_id, run_id, run_status}` |
| `API-BLOCKS` | GET | `/audit_blocks` | public | default | `{blocks[]}` |
| `API-PROG-G` | GET | `/audit_progress/<thread_id>` | user | default | progress payload |
| `API-PROG-P` | POST | `/audit_progress/<thread_id>` | user | default | progress payload |
| `API-UP` | POST | `/upload_document` | user | 10/min | `{doc_id, filename, chunks_indexed, files[]}` |
| `API-FILES-L` | GET | `/user_files` | user | 30/min | `{files[]}` |
| `API-FILES-D` | DELETE | `/user_files/<doc_id>` | user | 20/min | `{doc_id, deleted, files[]}` |
| `API-HIST-R` | GET | `/chat_history/recents` | user | default | recent conversations |
| `API-HIST-T` | GET | `/chat_history/thread/<thread_id>` | user | default | thread messages |
| `API-ADM-C` | POST | `/admin/users` | admin | 10/min | created user |
| `API-ADM-L` | GET | `/admin/users` | admin | 30/min | `{users[]}` (≤1000) |
| `API-ADM-U` | PATCH | `/admin/users/<uid>` | admin | 20/min | updated user |
| `API-ADM-D` | DELETE | `/admin/users/<uid>` | admin | 10/min | `{uid, deleted}` |
| `API-ADM-ING` | POST | `/admin/ingest_document` | **user** (see `DEBT-2`) | 10/min | ingestion result |
| `API-HEALTH` | GET | `/health` | public | default | liveness |
| `API-READY` | GET | `/readyz` | public | default | readiness |

### 5.2 Chat endpoints

**API-CHAT-1** Request MUST have `Content-Type: application/json`; otherwise `415`.

**API-CHAT-2** Body: `{"message": string, "thread_id"?: string}`. `message` MUST be non-empty after trim (else `400`) and ≤ 4000 characters (else `413`).

**API-CHAT-3** If `thread_id` is absent, the server MUST generate a UUID4 and return it. Clients MUST reuse it for continuity.

**API-CHAT-4** Before processing, `ensure_thread_ownership(thread_id, uid)` MUST run. A thread owned by another uid MUST be rejected.

**API-CHAT-5** After a successful turn, the server MUST (a) append user+model messages to Firestore, (b) persist the turn to BigQuery. A BigQuery failure MUST NOT fail the request.

**API-CHAT-6** On exception the server MUST persist a turn with `assistant_name="Exception"` and return `500`.

**API-CHAT-7** `sources[]` entries have shape `{index, title, category, score, excerpt, page, total_pages}`. `excerpt` is ≤ 220 chars plus ellipsis. Only `/chat_assistant` returns `sources`.

### 5.3 Document endpoints

**API-UP-1** Field name MUST be `file`. Extension MUST be `.pdf` (else `415`). Size MUST be ≤ 20 MiB (else `413`). Empty body → `400`.

**API-UP-2** A user MUST NOT exceed 25 stored documents; the 26th upload returns `429`.

**API-UP-3** If text extraction yields zero chunks → `422`.

**API-UP-4** If `pinecone_index is None` → `503`.

**API-UP-5** Upload MUST write vectors to namespace `uid` and metadata to `user_documents/{uid}/files/{doc_id}`, and MUST return the refreshed full file list in the same response.

**API-FILES-D-1** Delete MUST remove vectors from Pinecone before deleting the Firestore record. Missing record → `404`.

### 5.4 Admin endpoints

**API-ADM-1** All `/admin/users*` endpoints MUST call `require_admin_or_403`.

**API-ADM-2** The system MUST refuse to disable or delete an account whose email is in `_ADMIN_EMAILS`.

**API-ADM-3** User serialisation: `{uid, email, display_name, email_verified, disabled, created_at, last_sign_in_at, is_admin}`.

---

## 6. Data contracts

### 6.1 Firestore

```
threads/{thread_id}                      { uid, created_at }
threads/{thread_id}/messages/{auto_id}   { role: "user"|"model", text: string, created_at: timestamp }
audit_progress/{thread_id}               { uid, updated_at, active_block_id,
                                           blocks: { <block_id>: {
                                             status, summary, answered: [question_id],
                                             notes, deferred_reason,
                                             findings: [{ question_ids, verdict,
                                                          assessment, at }],
                                             completed_at, updated_at } } }
user_documents/{uid}/files/{doc_id}      { doc_id, filename, chunk_count, size_bytes, uploaded_at }
```

**DATA-FS-1** `role` MUST be exactly `"user"` or `"model"`. No other value is read back.

**DATA-FS-2** `status` MUST be one of `pending`, `in_progress`, `completed`, `deferred`.

**DATA-FS-5** `findings` holds one entry per compliance verification (`AGT-AUD-12`): the questions it covered, the parsed verdict, the advisor's full assessment (≤2000 chars) and a UTC timestamp. It is append-only and feeds the block summary and the final executive report.

**DATA-FS-4** `answered` holds question ids from `src/audit_catalog.py` (`block_N_qM`). It is append-only within a block and is the sole basis for the close gate (`AGT-AUD-4`). Ids not present in the block's catalogue MUST be rejected and reported, never stored.

**DATA-FS-3** The user and model messages of one turn MUST be written in one batch, with the model message timestamped 1 µs after the user message, to guarantee ordering.

### 6.2 Pinecone

| Namespace | Content | Vector id | Metadata |
|---|---|---|---|
| `""` (default) | Global corpus, 9176 vectors | `chunk_NNNNNN` | `text, source, page, total_pages, primary_category, block_type, entities, triplets, graph_importance` |
| `<uid>` | That user's uploaded PDFs | `<doc_id>_<chunk_index>` | `text, doc_id, filename, chunk_index, uploaded` |

**DATA-PC-1** Dimensionality is 384 and MUST match the loaded embedding model.

**DATA-PC-2** A user's documents MUST live only in their own namespace. Cross-namespace retrieval is forbidden.

**DATA-PC-3** The corpus namespace MUST NOT be written to by the online service except through `API-ADM-ING`.

### 6.3 BigQuery

**DATA-BQ-1** One row per conversation turn: `id, timestamp, thread_id, user_message, assistant_response, expert_response, endpoint_source, run_id, assistant_name, user_id, uid, email, email_verified`.

**DATA-BQ-2** `expert_response` is written only by `SURF-FN` (`updateExpertResponse`), never by the orchestrator.

**DATA-BQ-3** BigQuery writes MUST be best-effort: failures are logged, never raised.

### 6.4 Neo4j

**DATA-N4-1** Nodes labelled `Entity` with property `name`; relationships `RELATED` with property `predicate`.

**DATA-N4-2** Query is capped at 40 triples per call.

---

## 7. RAG pipeline specification

### 7.1 Constants (normative — these exact values)

```python
# src/rag_service.py
_CANDIDATE_K          = 12     # corpus candidates fetched
_MIN_SCORE            = 0.55   # corpus cosine cutoff
_MAX_RESULTS          = 6      # corpus chunks passed to the LLM
_USER_DOCS_MIN_SCORE  = 0.25   # user-namespace cutoff
_MAX_USER_FILES       = 25
_CHUNK_WORDS          = 400    # user-PDF fixed window
_CHUNK_OVERLAP        = 50
_MAX_CHUNKS           = 500

# src/context_orchestrator.py
_TOP_K_LOCAL          = 6
_MIN_SCORE_LOCAL      = 0.30
_MAX_EACH             = 6
_WORKERS              = int(env RAG_SEARCH_WORKERS, default 4)

# src/gemini_service.py
_GEMINI_MODEL         = "gemini-2.5-flash"
_CONTEXT_BUDGET       = {"gemini-2.5-flash": 180_000,
                         "gemini-2.5-flash-lite": 180_000,
                         "gemini-2.5-pro": 540_000}   # characters
max_rounds            = 6      # tool-call loop cap
```

### 7.2 Retrieval order

**RAG-1** `_build_rag_message` MUST search in this order and concatenate results in this order: (1) user namespace `uid`, (2) FAISS local store, (3) global corpus. User documents MUST precede corpus chunks in the numbered list.

**RAG-2** The query embedding MUST be computed once per turn and reused across all sources.

**RAG-3** Category filter: if the query matches CSDDD or GRI term sets, the corpus search MUST be filtered by `primary_category`. If a filtered search returns zero results, it MUST be retried without the filter.

**RAG-4** Question classification MUST return one of `resource`, `operational`, `conceptual`, `unknown`, by first match against the signal lists in `src/rag_service.py`. Routing: `resource`/`operational` → `hybrid`; `conceptual`/`unknown` → `semantic`.

**RAG-5** Graph context MUST be fetched only when strategy is `hybrid`, and only with the character budget remaining after semantic chunks (minus a 500-char reserve).

**RAG-6** Chunks are appended to the context while `used_chars + len(chunk) <= char_budget`. A chunk that does not fit MUST still appear in `sources[]` (so citation numbering stays stable) but MUST NOT be added to the prompt text.

**RAG-7** Chunk header format in the prompt: `[{i}] {title} ({category}, p.{page}/{total_pages}, relevancia={score:.2f})`.

**RAG-8** If no semantic chunks and no graph context are available, the original user message MUST be sent unaugmented.

**RAG-9** Retrieval failure MUST be caught and degrade to the unaugmented message (`ARC-6`).

### 7.3 User-PDF chunking (current behaviour)

**RAG-10** `extract_pdf_chunks` flattens all pages into one token stream before windowing. Consequence: chunks carry no page number. This is `DEBT-5`, documented as observed behaviour, not endorsed.

---

## 8. Agent behaviour contracts

### 8.1 Advisor

**AGT-ADV-1** System prompt is `EXPERT_SYSTEM_PROMPT`. The advisor MUST cite retrieved fragments inline as `[n]`.

**AGT-ADV-2** The advisor has no tools.

### 8.2 Auditor

**AGT-AUD-1** System prompt is `AUDITOR_SYSTEM_PROMPT` with `{audit_context}` replaced by the formatted current progress.

**AGT-AUD-2** Tools exposed, function-calling mode `AUTO`:

| Tool | Parameters | Effect |
|---|---|---|
| `record_block_answers` | `block_id`, `question_ids[]`, `notes` (all required) | Unions valid ids into `blocks.<id>.answered`, sets status `in_progress` and the active block, **runs the compliance check** (`AGT-AUD-12`), returns coverage + remaining questions + verdict |
| `complete_audit_block` | `block_id`, `summary` (required) | Closes the block **only if** no mandatory question is missing; otherwise rejects and returns the missing list |
| `defer_block` | `block_id`, `reason` (required) | Sets status `deferred`, preserving `answered`; advances to the next block |
| `resume_block` | `block_id` (required) | Sets a deferred/pending block to `in_progress` and returns its remaining questions |
| `invoke_sustainability_expert` | `query` (required) | Runs `chat_with_expert` with empty history; returns its text |

**AGT-AUD-3** The tool loop MUST terminate after at most 8 rounds (a normal turn chains `record_block_answers` → `complete_audit_block`, plus room for one rejected-close retry).

**AGT-AUD-4** **Close gate (server-enforced).** `complete_audit_block` MUST verify coverage against `audit_catalog.missing_mandatory(block_id, answered)` and MUST refuse to close while any mandatory question is unrecorded. The refusal MUST name the missing questions verbatim. This check MUST live in the server, never be delegated to the prompt.

**AGT-AUD-5** The auditor MUST ask every `[M]` question of the active block. A question that does not apply to the company profile MUST still be asked and recorded with its "not applicable" answer, never silently skipped. A single-word answer does not satisfy an `[M]` question requiring detail, except where the real answer is "we don't have that" / "not applicable".

**AGT-AUD-6** A tool failure MUST return a human-readable fallback string to the model, never raise.

**AGT-AUD-7** `src/audit_catalog.py` is the **single source of truth** for blocks and questions. `app.py` (`AUDIT_BLOCKS`), the rendered prompt (`{block_catalog}`) and the close gate all derive from it. Questions MUST NOT be added in the prompt text. Question ids are stable and MUST NOT be renumbered or reused.

**AGT-AUD-8** Deferral is **user-initiated only**. The model MUST NOT defer a block on its own initiative. A deferred block keeps its recorded answers and remains resumable.

**AGT-AUD-9** Active-block selection order: the `in_progress` block; else the first `pending`; else the first `deferred`; else the last block. Deferred blocks are skipped while pending blocks remain, and revisited once none do.

**AGT-AUD-10** The injected `{audit_context}` MUST list, for the active block, the answered questions and the remaining mandatory questions **verbatim with their ids**. This list is the model's script.

**AGT-AUD-11** The manual `POST /audit_progress/<thread_id>` endpoint is a deliberate **human override** and is not subject to `AGT-AUD-4`. The UI MUST display coverage so the override is informed.

**AGT-AUD-12 (compliance verification).** `record_block_answers` MUST contrast the recorded answer against the corpus by calling the advisor, for every recorded question whose catalogue entry has `verify = True`. Requirements:

- The verification query is **built by the server** from the catalogue question text plus the user's answer, never phrased by the model, so verification does not depend on how the auditor chooses to word it.
- The advisor is asked for a fixed shape: `VEREDICTO` (`cumple` | `cumple parcialmente` | `no cumple` | `no evaluable`), `BRECHA`, `RECOMENDACIÓN`, `BASE`.
- The parsed finding is appended to `blocks.<id>.findings` and returned to the model with an explicit instruction to relay gap and remediation to the user **before** asking further questions.
- Verification MUST be skipped when no recorded question is verifiable (saves a call) and when `notes` is empty.
- A verification failure MUST NOT fail the recording (`ARC-6`); the answer is still stored and the turn continues.

**AGT-AUD-13** `Question.verify` is `False` for descriptive/profile questions (company name, headcount, turnover, budget, external support). Assessing those against a directive is meaningless. Block 1's normative applicability is assessed at block close, per its catalogue note, not question by question.

**AGT-AUD-14** The active block is stored explicitly in `audit_progress/{thread_id}.active_block_id` and set by `record_block_answers`, `complete_audit_block`, `defer_block` and `resume_block`. It MUST NOT be inferred from block statuses alone: two blocks could hold `in_progress` simultaneously and the active one would then resolve by catalogue order instead of by where work is happening. Inference remains only as a fallback for threads predating the field.

### 8.3 History

**AGT-HIST-1** History passed to Gemini MUST start with a `user` turn, alternate strictly, and MUST NOT end with a `user` turn.

**AGT-HIST-2** History load MUST return the **most recent** `limit` messages in chronological order (query descending, then reverse). A plain ascending `order_by(...).limit(n)` returns the *oldest* n and is a defect: it made the auditor re-read the start of the conversation forever, repeating answered questions and never accumulating enough coverage to close a block.

**AGT-HIST-3** Default limit is 40 messages; `/chat_auditor` MUST request 80, since one block of six mandatory questions consumes 12–16 messages.

---

## 9. Frontend specification

### 9.1 Chat widget (`SURF-CHAT`)

**UI-1** View states, in order: `login` → (`verify-banner` if `!emailVerified`) → `mode-selection` → `chat`.

**UI-2** `mode-selection` MUST offer exactly two modes and MUST list recent conversations with timestamp and mode, resumable in place.

**UI-3** Advisor layout: single column. Auditor layout: three panes — left progress (collapsible), centre chat, right tabs (`files`, `report`).

**UI-4** Citations: every `[n]` in the response MUST render as an activatable badge that expands the sources list and highlights source `n`.

**UI-5** File attachment MUST enforce client-side: PDF only, ≤ 20 MB. The server limit is authoritative (`API-UP-1`).

**UI-6** A Firebase initialisation failure MUST render a visible message inside the login box and abort, never fail silently.

**UI-7** Requests MUST carry `Authorization: Bearer <ID token>` and an `Idempotency-Key` header where applicable; timeout default 90 s.

**UI-8** Design tokens are defined in `public/chatbot/bubble-prod-style.css` and are authoritative for brand colour: `--uclm-rojo-principal: #8C1B3A`. Responsive breakpoint: 480 px.

### 9.2 Admin panel (`SURF-ADMIN`)

**UI-9** Tabs: `history` (always) and `users` (only when `user.email === ADMIN_EMAIL`). Hiding is cosmetic; `API-ADM-1` is the real control.

**UI-10** History view MUST support free-text search and editing of `expert_response` per row.

**UI-11** `ORCHESTRATOR_BASE_URL` in `UserManagement.js` MUST point at the deployed Cloud Run service.

---

## 10. Security specification

**SEC-1** Every non-public endpoint MUST verify a Firebase ID token AND require `email_verified == true`. Unverified → `403`.

**SEC-2** Admin authorisation MUST be re-checked server-side on every admin request.

**SEC-3** Thread ownership MUST be enforced on every thread-scoped read or write.

**SEC-4** CORS MUST use an explicit origin allow-list with `supports_credentials=True`. Wildcard origin is forbidden (incompatible with credentials). If `CORS_ORIGINS` is empty or contains `*`, the code MUST fall back to the safe default list:
`https://recava-auditor-dev.web.app`, `https://recava-auditor.web.app`, `https://recava-auditor-dev-panel.web.app`, `https://recava-auditor-panel.web.app`, `http://localhost:8000`.

**SEC-5** Allowed methods: `GET, POST, PATCH, DELETE, OPTIONS`. Allowed headers: `Authorization, Content-Type, Idempotency-Key`. Exposed: `X-Request-Id`.

**SEC-6** Secrets MUST come from Secret Manager at deploy time: `GEMINI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID`, `NEO4J_PASSWORD`. `.env` MUST remain gitignored.

**SEC-7** Logs MUST NOT contain full message bodies or tokens. Query logging is truncated (80 chars).

---

## 11. Deployment specification

**OPS-1** Cloud Run: `--cpu=2 --memory=4Gi --concurrency=40 --timeout=600s --port=8080 --region=europe-west1 --allow-unauthenticated`. Platform auth is open because application auth is mandatory (`SEC-1`).

**OPS-2** Container: multi-stage. `torch` installed in its own layer from `https://download.pytorch.org/whl/cpu` with `--extra-index-url https://pypi.org/simple` (required for build backends such as `flit_core`). Final image runs as non-root `appuser`, exposes 8080, has a `HEALTHCHECK` against `/health`, and runs `gunicorn --workers 4 --timeout 120`.

**OPS-3** Cloud Build steps in order: build image (BuildKit, inline cache) → push → deploy Cloud Run → install firebase-tools → npm install + build admin panel → firebase deploy hosting (`allowFailure: true`).

**OPS-4** Firebase Hosting targets: `chatbot` → `public/chatbot`; `admin-panel` → `public/admin-panel/build` with SPA rewrite to `/index.html`.

**OPS-5** Environment variables set at deploy: `EMBEDDING_MODEL_NAME`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_DATABASE`.

**OPS-6** Recognised env vars: `GEMINI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `BIGQUERY_DATASET_ID`, `BIGQUERY_TABLE_ID`, `EMBEDDING_MODEL_NAME`, `CORS_ORIGINS`, `RAG_SEARCH_WORKERS`, `FAISS_MAX_SESSIONS`, `DISABLE_BIGQUERY`, `NEO4J_*`, `PORT`, `FLASK_DEBUG`, `GOOGLE_APPLICATION_CREDENTIALS`, `FIREBASE_AUTH_EMULATOR_HOST`.

**OPS-7** Local run: `set -a; source .env; set +a; unset GOOGLE_APPLICATION_CREDENTIALS` (ADC file is `authorized_user` type; `credentials.Certificate` requires `service_account`, so the ApplicationDefault path must be used).

---

## 12. Offline pipeline specification

**PIPE-1** `src/corpus_pipeline.py` is a **batch job, executed once per corpus version**, not a service. It fully reindexes the target namespace.

**PIPE-2** Chunking: semantic breakpoint at percentile 80 (cut at the lowest 20 % of consecutive-sentence similarities), sized to [350, 1800] characters, no overlap, never crossing a heading. Tables and figures are atomic.

**PIPE-3** Heading grammar levels: 1 `TÍTULO|ANEXO`, 2 `CAPÍTULO|MÓDULO`, 3 `SECCIÓN`/all-caps, 4 `Artículo N[ bis]`, 4 `Contenido|Disclosure NNN-N` (GRI), 4 `[EGS]N-NN` (ESRS), 5–8 numbered.

**PIPE-4** The contextual header `[title · type year issuer · section]` is prepended **only for embedding**. Stored `text` MUST remain the literal source text so citations are verbatim.

**PIPE-5** `--enrich` and `--index-propositions` make LLM calls and are optional. `--wipe` deletes the namespace first.

**PIPE-6** The pipeline MUST NOT be pointed at the production namespace without passing the benchmark quality gate first.

**PIPE-7** Each index build SHOULD be recorded with: date, embedding model + revision, chunking parameters, git commit, vector count.

---

## 13. Quality and evaluation

**QA-1** `src/rag_benchmark.py` measures `hit@k`, `recall@k`, `precision@k`, `mrr` against a golden set, optionally adding LLM-judged `groundedness`, `correctness`, `citation_ok`.

**QA-2** Production baseline (10 seed questions, production index): `hit@k 0.80`, `recall@k 0.65`, `precision@k 0.42`, `mrr 0.51`. Conceptual questions are weakest (`hit@k 0.50`, `mrr 0.29`).

**QA-3** Golden set v1 is produced by the IDPEI workshop protocol: ~50 T1 questions with document + page + **article**, double independent annotation, κ ≥ 0.70 target, plus T9 paraphrases.

**QA-4** `src/kb_experiment.py` runs the factorial study (chunking × embedding × lexical distance × graph) with `citation@article` metrics and pre-registered contrasts H1.1–H1.4. Its hypotheses and decision rules MUST NOT be changed without the project owner.

**QA-5** Any architectural change to retrieval MUST be measured against the baseline before and after.

---

## 14. Invariants

| id | Invariant |
|---|---|
| `INV-1` | No endpoint other than `/health`, `/readyz`, `/audit_blocks` may respond without a verified Firebase token with `email_verified == true`. |
| `INV-2` | A user can never retrieve, read or delete another user's documents, threads or audit progress. |
| `INV-3` | The embedding model in `config.py` matches the model that built the queried index. |
| `INV-4` | The stored `text` of a chunk is verbatim source text; enrichment never mutates it. |
| `INV-5` | Citation numbering in `sources[]` matches the `[n]` markers in the prompt context. |
| `INV-6` | Degradation over failure: Pinecone/Neo4j/BigQuery/FAISS errors never turn into request failures. |
| `INV-7` | The auditor cannot mark a block complete with unanswered `[M]` questions. Enforced server-side in `complete_audit_block`, not by the prompt. |
| `INV-10` | The model never sees a stale conversation window: history is always the most recent messages, never the oldest. |
| `INV-11` | A block is only ever left incomplete by explicit user request (`defer_block`), and what was recorded survives for when it is resumed. |
| `INV-8` | Gemini history alternates and never ends on a `user` turn. |
| `INV-9` | `.env`, `results/`, `benchmarks/baseline.jsonl`, `benchmarks/run_*.jsonl` are never committed. |
| `INV-OPS-1` | No agent deploys, pushes to a remote, writes to the production Pinecone namespace, or runs `--wipe` without explicit human instruction in the current session. |
| `INV-OPS-2` | `main` is a protected branch; changes land via pull request. |

---

## 15. Known deviations (debt)

| id | Severity | Deviation | Location | Violates |
|---|---|---|---|---|
| `DEBT-1` | high | Firestore rules grant `read, write: if true` to everyone, labelled as a local-testing placeholder | `firestore.rules` | `INV-2`, `SEC-3` |
| `DEBT-2` | high | `/admin/ingest_document` authorises with `require_firebase_user_or_403`, not `require_admin_or_403`; any verified user can write to the global corpus | `app.py` | `API-ADM-1`, `DATA-PC-3` |
| `DEBT-3` | medium | Production corpus is chunked per layout block (median 2 words/chunk, 79 % ≤ 5 words); `entities`/`triplets` empty; generating script absent from the repo | Pinecone `uclm-corpus-roma` | — |
| `DEBT-4` | medium | English monolingual embedding model over a Spanish corpus | `src/config.py` | `ARC-3` (locked-in) |
| `DEBT-5` | medium | User-PDF chunks carry no page number (pages flattened before windowing) | `src/rag_service.py` | `RAG-10` |
| `DEBT-6` | medium | Deployed chat widget predates HEAD; the login-failure visibility fix is not live | `public/chatbot/` | `UI-6` |
| `DEBT-7` | low | `README.md` describes an OpenAI Assistants architecture that no longer exists | `README.md` | `SYS-1` |
| `DEBT-8` | low | Admin list hardcoded in source; adding an admin requires a redeploy | `app.py` `_ADMIN_EMAILS` | — |
| `DEBT-9` | low | FAISS store still initialised and searched although uploads are permanent in Pinecone | `src/local_vector_store.py` | — |
| `DEBT-10` | low | `@limiter.limit` is declared above `@app.route`; limits are registered by function name so they likely apply, but this is unverified by test | `app.py` | `API-0.6` |
| `DEBT-11` | low | Working files in repo root: `firestore-debug.log`, `bubble_tmp.bin`, `temp.js`, `test_api.ps1` | repo root | — |

**Remediation order:** `DEBT-1`, `DEBT-2` (security, minutes) → `DEBT-3` with benchmark gate → the rest.

---

## 16. Verification

```bash
# Static: routes, auth level and rate limit per endpoint
grep -n "@limiter.limit\|@app.route\|require_.*_or_403()" app.py

# Static: RAG constants match §7.1
grep -n "^_\(MIN_SCORE\|CANDIDATE_K\|MAX_RESULTS\|USER_DOCS_MIN_SCORE\|CHUNK_WORDS\|CHUNK_OVERLAP\|MAX_CHUNKS\)" src/rag_service.py

# Offline pipeline, dry run (no writes)
python -m src.corpus_pipeline --input ./corpus --dry-run --limit 3

# Retrieval quality against the baseline
set -a; source .env; set +a
python -m src.rag_benchmark --golden benchmarks/golden_set.jsonl \
  --embedding-model sentence-transformers/all-MiniLM-L6-v2 \
  --baseline benchmarks/baseline.jsonl --out results/run.jsonl

# Research harness instrument check (~1 min, no Pinecone)
python -m src.kb_experiment --corpus benchmarks/smoke/corpus \
  --golden benchmarks/smoke/golden_smoke.jsonl \
  --embedding-models sentence-transformers/all-MiniLM-L6-v2 \
  --chunking fixed semantic_struct --graph none skeleton --k 4 --out results/smoke

# Health of the deployed service
curl -s https://<cloud-run-host>/health
```

**VER-1** A change to `app.py` routing MUST be accompanied by the first command's output.
**VER-2** A change to retrieval MUST be accompanied by the benchmark comparison (`QA-5`).
**VER-3** A change to chunking MUST be accompanied by a dry run and, if reindexing, by the index manifest of `PIPE-7`.

---

## 17. Change protocol for agents

1. **Read before writing.** Locate the affected `CMP-*` and requirement IDs.
2. **Do not widen access.** Any change touching `SEC-*` or `INV-*` requires explicit human confirmation in-session.
3. **Do not touch production state.** No deploys, no pushes, no writes to the default Pinecone namespace, no `--wipe` (`INV-OPS-1`).
4. **Keep the two documents in sync.** A behavioural change updates this spec and `docs/documentacion.html` in the same commit.
5. **Report honestly.** If a verification command fails, report the failure and its output; do not present unrun commands as run.
6. **Prefer additive.** Legacy components (`CMP-FAISS`, `CMP-ORCH`) are removable only with a migration note, because running instances may still hold session state.

### Related documents

| Path | Purpose |
|---|---|
| `docs/documentacion.html` | Human narrative of this spec |
| `DOCUMENTACION.md` | Earlier long-form documentation (still largely accurate; superseded here for API and RAG details) |
| `benchmarks/README.md` | How to run benchmark and experiment |
| `paper/AGENT_CONTEXT_LINEA1.md` | Research-line context (Línea 1) |
| `paper/linea1_kb_construction_es_regulatory_rag.md` | Article draft |
| `paper/EXPLICACION_LINEA1.md` | Article explained (Spanish) |
