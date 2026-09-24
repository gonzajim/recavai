"""
Harness del asesor: el bucle que ejecuta un turno completo.

    planificar (en paralelo) + recuperar ─► elegir nivel de contexto y construir bloques
        ─► controles de entrada y contexto ─► componer habilidades ─► generar
        ─► (si se abstiene) segunda pasada con más contexto ─► controles de salida
        ─► (si hay violaciones) reparar UNA vez ─► anotar lo que siga sin respaldo
        ─► registrar el turno

Es el único punto por el que pasa el asesor, en sus tres usos:
  asesor        el chat (/chat_assistant)
  herramienta   el auditor consulta al asesor (invoke_sustainability_expert)
  verificacion  el auditor contrasta una respuesta de la empresa con la normativa

y el mismo que usa el banco de evaluación (scripts/eval_battery.py), así que lo que se
mide es lo que se sirve.

Política de reparación. Si los controles de salida encuentran datos sin respaldo o citas
inexistentes, se pide al modelo UNA reescritura indicándole exactamente qué datos no
constan. Se sirve la versión con menos violaciones. Si aun así queda alguna, se añade al
final una nota visible que dice qué datos no se han podido verificar. Nunca se bloquea
la respuesta: un asistente que no contesta tampoco es útil, pero uno que contesta con un
dato inventado sin avisar es peligroso.

Contexto adaptativo (docs/PLAN_CONTEXTO.md). Con almacén de unidades (RAG_UNIT_STORE),
cada pregunta recibe el contexto de su nivel (src/context_builder.py): S si nombra la
unidad, L si pide una enumeración o un panorama, M en otro caso. El planificador
(src/agent/planner.py, RAG_PLANNER=1) corre en paralelo con la búsqueda. Si el borrador
dice que algo no consta y el turno va rápido, se genera UNA vez más con el nivel
siguiente (Self-Route, Li et al., 2024). Llamadas al modelo por turno: como mucho
planificador + borrador + segunda pasada + reparación.

Registro. Cada turno deja una línea JSON (evt=agent_turn) con la tarea, la versión de
las habilidades, los avisos, las violaciones antes y después de reparar, el nivel y los
tokens de contexto, el planificador, la segunda pasada, los tokens del modelo y los tiempos.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from google.genai import types

from src.config import logger
from src.agent import guardrails as gr
from src.agent import planner
from src.agent.skills import compose
from src import context_builder as cb

TASKS = ("asesor", "herramienta", "verificacion")


@dataclass(frozen=True)
class GenerationPolicy:
    temperature: float = 0.2           # antes no se fijaba: el modelo usaba la suya por defecto
    max_output_tokens: int = 8192
    max_repairs: int = 1
    second_pass_max_ms: int = 8000     # solo si el primer borrador llegó antes de esto


DEFAULT_POLICY = GenerationPolicy()


def thinking_budget() -> int | None:
    """Razonamiento interno de Gemini 2.5: RAG_THINKING_BUDGET vacío = dinámico (lo decide
    el modelo). Medido el 24/09/2026 (scripts/probe_latency.py): con 12.000 tokens de
    contexto, dinámico 10,4 s y sin razonamiento 4,4 s; con 40.000, 23,0 s frente a 5,9 s."""
    v = os.getenv("RAG_THINKING_BUDGET", "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None


# Enumeraciones y panoramas piden nivel L aunque no haya planificador.
_WIDE = re.compile(r"\b(cu[áa]les son|qu[ée] (?:temas|pasos|requisitos|contenidos|sectores|obligaciones|fases)|"
                   r"enumer|lista(?:do)? de|tod[oa]s l[oa]s|los \w+ pasos|resum)", re.I)
_WHOLE_DOC = {"CSDDD": "02_NORMATIVAS/01_CSDDD", "CSRD": "02_NORMATIVAS/02_CSDR"}


def choose_level(task: str, question: str, plan, explicit: bool) -> str:
    if task == "verificacion":
        return cb.clamp("S")
    kind = plan.tipo if plan else None
    if kind in ("enumeracion", "panorama") or (plan is None and _WIDE.search(question or "")):
        level = "L"
    elif explicit and kind in (None, "puntual"):
        level = "S"
    else:
        level = "M"
    if task == "herramienta" and level == "L":
        level = "M"                                    # una auditoría hace ~70 llamadas
    return cb.clamp(level)


def whole_documents(plan, level: str) -> list[str]:
    """Panorama de una norma que cabe entera en el nivel L (CSDDD ~36k, CSRD ~41k tokens)."""
    if level == "L" and plan and plan.tipo == "panorama" and len(plan.normas) == 1:
        doc = _WHOLE_DOC.get(plan.normas[0])
        return [doc] if doc else []
    return []


# El modelo cita a veces el aviso como si fuera una fuente («… no existe [AVISOS DEL
# SISTEMA]»): es una etiqueta interna que el usuario no debe ver (1 de 60 en la batería).
_NOTICE_TAG = re.compile(r"\s*\[\s*AVISOS? DEL SISTEMA\s*\]", re.I)


def clean_answer(text: str) -> str:
    return _NOTICE_TAG.sub("", text or "")


def _usage(resp) -> dict:
    um = getattr(resp, "usage_metadata", None)
    g = lambda k: (getattr(um, k, None) or 0) if um is not None else 0
    return {"entrada": g("prompt_token_count"), "salida": g("candidates_token_count"),
            "razonamiento": g("thoughts_token_count")}


@dataclass
class TurnResult:
    text: str
    sources: list[dict]
    task: str
    skills: tuple[str, ...]
    skills_version: str
    notices: list[dict] = field(default_factory=list)
    violations_draft: list[dict] = field(default_factory=list)
    violations_final: list[dict] = field(default_factory=list)
    repaired: bool = False
    annotated: bool = False
    draft: str = ""
    used_docs: list[dict] = field(default_factory=list)
    ms: dict = field(default_factory=dict)
    level: str | None = None
    context_tokens: int = 0
    plan: dict | None = None
    second_pass: bool = False
    tokens: dict = field(default_factory=dict)

    def report(self) -> dict:
        n = lambda fs: sum(len(f["items"]) or 1 for f in fs)
        return {"task": self.task, "skills_version": self.skills_version, "skills": list(self.skills),
                "avisos": [f["guard"] for f in self.notices],
                "violaciones_borrador": n(self.violations_draft), "violaciones_final": n(self.violations_final),
                "reparada": self.repaired, "anotada": self.annotated,
                "nivel": self.level, "tokens_contexto": self.context_tokens, "bloques": len(self.used_docs),
                "planificador": self.plan, "segunda_pasada": self.second_pass, "tokens": self.tokens,
                "ms": self.ms}


def _repair_request(violations: list[gr.Finding], task: str) -> str:
    missing = [f for f in violations if f.guard != "cita_sin_respaldo"]
    misplaced = [f for f in violations if f.guard == "cita_sin_respaldo"]
    formato = ("Mantén EXACTAMENTE el formato VEREDICTO / BRECHA / RECOMENDACIÓN / BASE. "
               if task == "verificacion" else "")
    parts = ["REVISIÓN AUTOMÁTICA DE TU RESPUESTA ANTERIOR."]
    if missing:
        items = "; ".join(f"«{i}»" for f in missing for i in f.items)
        parts.append(f"Estos datos no aparecen en los fragmentos de la base documental ni en la "
                     f"pregunta: {items}. Elimínalos o sustitúyelos por lo que sí digan los fragmentos, "
                     "con su cita [n]; si algo no consta, dilo.")
    if misplaced:
        items = "; ".join(i for f in misplaced for i in f.items)
        parts.append(f"Estas citas apuntan a un fragmento que no contiene el dato: {items}. "
                     "Cita el fragmento que sí lo contiene.")
    return " ".join(parts) + (
        " Reescribe la respuesta COMPLETA. No añadas cifras, fechas, artículos ni normas nuevos. "
        f"{formato}Devuelve solo la respuesta reescrita, sin mencionar esta revisión.")


def _annotation(violations: list[gr.Finding]) -> str:
    items = ", ".join(f"«{i}»" for f in violations for i in f.items)
    return ("\n\n---\n*Verificación automática: no he podido localizar en la base documental "
            f"{items}. Compruébalo en el texto oficial antes de usarlo.*")


@dataclass
class Context:
    docs: list[dict]
    blocks: list[dict]
    plan: "planner.Plan | None"
    level: str | None
    whole: list[str]


def gather_context(genai_client, embed_model, pinecone_index, user_message: str, *, task: str = "asesor",
                   thread_id: str | None = None, local_store=None, uid: str | None = None,
                   retrieval_query: str | None = None) -> Context:
    """Planificador (en paralelo con la búsqueda), recuperación, nivel y bloques. Es la
    primera fase de run_turn; la batería la llama sola para medir la recuperación con la
    misma ruta que producción."""
    from src import gemini_service as gs
    from src.normative_graph import get_graph
    t0 = time.time()
    search_text = retrieval_query or user_message
    store = cb.get_unit_store()
    graph = get_graph()
    explicit = bool(graph and graph.explicit_units(search_text))
    plan_future = (planner.start(genai_client, search_text)
                   if store is not None and planner.should_plan(search_text, task, explicit) else None)
    docs = gs.retrieve_documents(embed_model, pinecone_index, user_message, thread_id=thread_id,
                                 local_store=local_store, uid=uid, retrieval_query=retrieval_query,
                                 **({"k": cb.CANDIDATE_K, "plan_future": plan_future,
                                     "plan_deadline": t0 + planner.PLANNER_TIMEOUT_S} if store is not None else {}))
    # retrieve_documents ya esperó al planificador hasta el plazo; si terminó, su tipo
    # decide el nivel (aunque sus búsquedas llegaran tarde).
    plan = (plan_future.result() if plan_future is not None and plan_future.done()
            and plan_future.exception() is None else None)
    level = choose_level(task, search_text, plan, explicit) if store is not None else None
    whole = whole_documents(plan, level) if level else []
    return Context(docs=docs, blocks=cb.build_blocks(docs, level or "M", store, whole),
                   plan=plan, level=level, whole=whole)


def run_turn(
    genai_client,
    embed_model,
    pinecone_index,
    history: list,
    user_message: str,
    *,
    task: str = "asesor",
    thread_id: str | None = None,
    local_store=None,
    uid: str | None = None,
    retrieval_query: str | None = None,
    policy: GenerationPolicy = DEFAULT_POLICY,
) -> TurnResult:
    from src import gemini_service as gs              # import tardío: gemini_service importa este módulo

    if task not in TASKS:
        raise ValueError(f"tarea desconocida: {task}")
    t0 = time.time()
    search_text = retrieval_query or user_message
    store = cb.get_unit_store()
    tokens = {"entrada": 0, "salida": 0, "razonamiento": 0}

    # 1. Planificar (en paralelo), recuperar y construir el contexto del nivel
    ctx = gather_context(genai_client, embed_model, pinecone_index, user_message, task=task,
                         thread_id=thread_id, local_store=local_store, uid=uid,
                         retrieval_query=retrieval_query)
    plan, level, whole, blocks = ctx.plan, ctx.level, ctx.whole, ctx.blocks
    _, _, used = gs.format_context(blocks, user_message)
    t_ret = time.time()

    # 2. Controles de entrada y contexto → avisos para el modelo
    state = gr.TurnState(question=user_message, search_text=search_text, used_docs=used)
    notices = gr.run(gr.INPUT_GUARDS, state) + gr.run(gr.CONTEXT_GUARDS, state)
    augmented, sources, used = gs.format_context(blocks, user_message, notices=[f.detail for f in notices])
    state.used_docs = used

    # 3. Componer habilidades y generar
    prompt = compose(task, search_text)
    cfg = dict(system_instruction=prompt.text, temperature=policy.temperature,
               max_output_tokens=policy.max_output_tokens)
    tb = thinking_budget()
    if tb is not None:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=tb)
    config = types.GenerateContentConfig(**cfg)

    def generate(contents_, label):
        resp = gs._generate(genai_client, contents=contents_, config=config, label=label, thread_id=thread_id)
        for k_, v_ in _usage(resp).items():
            tokens[k_] += v_
        return clean_answer(gs._extract_text(resp))

    contents = list(history) + [types.Content(role="user", parts=[types.Part(text=augmented)])]
    draft = generate(contents, task)
    t_gen = time.time()

    # 4. Segunda pasada: el borrador dice que algo no consta y hay margen de nivel y tiempo.
    #    No se hace si la pregunta tiene una premisa falsa: ahí abstenerse es lo correcto.
    second = False
    nxt = cb.next_level(level) if level else None
    if (nxt and gr.abstentions(draft) and (t_gen - t0) * 1000 < policy.second_pass_max_ms
            and not any(f.guard == "referencia_desconocida" for f in notices)):
        try:
            docs2 = gs.retrieve_documents(embed_model, pinecone_index, user_message, thread_id=thread_id,
                                          local_store=local_store, uid=uid, retrieval_query=retrieval_query,
                                          k=cb.SECOND_PASS_K, extra_queries=plan.busquedas if plan else None)
            blocks2 = cb.build_blocks(docs2, nxt, store, whole)
            augmented2, sources2, used2 = gs.format_context(blocks2, user_message,
                                                            notices=[f.detail for f in notices])
            contents2 = list(history) + [types.Content(role="user", parts=[types.Part(text=augmented2)])]
            draft2 = generate(contents2, f"{task}-segunda")
            second = True
            if gr.abstentions(draft2) < gr.abstentions(draft):
                draft, contents, sources, used, level = draft2, contents2, sources2, used2, nxt
                state.used_docs = used
        except Exception:                                # noqa: BLE001
            logger.error("Segunda pasada fallida [%s] thread=%s", task, thread_id, exc_info=True)
    t_sec = time.time()

    # 5. Controles de salida
    state.answer = draft
    v_draft = gr.run(gr.OUTPUT_GUARDS, state)
    final, v_final, repaired = draft, v_draft, False

    # 6. Reparación (una vez)
    if v_draft and policy.max_repairs > 0:
        repair_contents = contents + [
            types.Content(role="model", parts=[types.Part(text=draft)]),
            types.Content(role="user", parts=[types.Part(text=_repair_request(v_draft, task))]),
        ]
        try:
            fixed = generate(repair_contents, f"{task}-reparacion")
            state.answer = fixed
            v_fixed = gr.run(gr.OUTPUT_GUARDS, state)
            count = lambda fs: sum(len(f.items) or 1 for f in fs)
            if count(v_fixed) <= count(v_draft):
                final, v_final, repaired = fixed, v_fixed, True
        except Exception:                                # noqa: BLE001
            logger.error("Reparación fallida [%s] thread=%s", task, thread_id, exc_info=True)
    t_rep = time.time()

    # 7. Anotar lo que siga sin respaldo (no las citas imprecisas: el dato sí consta)
    to_note = [f for f in v_final if f.annotate]
    annotated = bool(to_note)
    if annotated:
        final = final + _annotation(to_note)

    # 8. Fuentes para la interfaz: solo las citadas, con su número. Con bloques de contexto
    #    la lista completa (10-15 unidades) deja de ser útil.
    if store is not None:
        cited = gr.cited_indices(final)
        sources = [s_ for s_ in sources if s_["index"] in cited]

    result = TurnResult(
        text=final, sources=sources, task=task, skills=prompt.skills, skills_version=prompt.version,
        notices=[f.as_dict() for f in notices], violations_draft=[f.as_dict() for f in v_draft],
        violations_final=[f.as_dict() for f in v_final], repaired=repaired, annotated=annotated,
        draft=draft, used_docs=used, level=level,
        context_tokens=cb.tokens_of(sum(len(d.get("content") or "") for d in used)),
        plan=plan.as_dict() if plan else None, second_pass=second, tokens=tokens,
        ms={"recuperacion": round((t_ret - t0) * 1000), "generacion": round((t_gen - t_ret) * 1000),
            "segunda_pasada": round((t_sec - t_gen) * 1000), "reparacion": round((t_rep - t_sec) * 1000),
            "planificador": plan.ms if plan else 0, "total": round((time.time() - t0) * 1000)},
    )
    logger.info(json.dumps({"evt": "agent_turn", "thread": thread_id, **result.report()}, ensure_ascii=False))
    return result
