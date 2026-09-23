"""
Harness del asesor: el bucle que ejecuta un turno completo.

    recuperar ─► controles de entrada y contexto ─► componer habilidades ─► generar
        ─► controles de salida ─► (si hay violaciones) reparar UNA vez ─► anotar lo que
        siga sin respaldo ─► registrar el turno

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

Registro. Cada turno deja una línea JSON (evt=agent_turn) con la tarea, la versión de
las habilidades, los avisos, las violaciones antes y después de reparar y los tiempos.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from google.genai import types

from src.config import logger
from src.agent import guardrails as gr
from src.agent.skills import compose

TASKS = ("asesor", "herramienta", "verificacion")


@dataclass(frozen=True)
class GenerationPolicy:
    temperature: float = 0.2           # antes no se fijaba: el modelo usaba la suya por defecto
    max_output_tokens: int = 8192
    max_repairs: int = 1


DEFAULT_POLICY = GenerationPolicy()


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

    def report(self) -> dict:
        n = lambda fs: sum(len(f["items"]) or 1 for f in fs)
        return {"task": self.task, "skills_version": self.skills_version, "skills": list(self.skills),
                "avisos": [f["guard"] for f in self.notices],
                "violaciones_borrador": n(self.violations_draft), "violaciones_final": n(self.violations_final),
                "reparada": self.repaired, "anotada": self.annotated, "ms": self.ms}


def _repair_request(violations: list[gr.Finding], task: str) -> str:
    items = "; ".join(f"«{i}»" for f in violations for i in f.items)
    formato = ("Mantén EXACTAMENTE el formato VEREDICTO / BRECHA / RECOMENDACIÓN / BASE. "
               if task == "verificacion" else "")
    return (
        "REVISIÓN AUTOMÁTICA DE TU RESPUESTA ANTERIOR. Estos datos no aparecen en los fragmentos "
        f"de la base documental ni en la pregunta: {items}. "
        "Reescribe la respuesta COMPLETA: elimina esos datos o sustitúyelos por lo que sí digan "
        "los fragmentos, con su cita [n]; si algo no consta, dilo. No añadas cifras, fechas, "
        f"artículos ni normas nuevos. {formato}"
        "Devuelve solo la respuesta reescrita, sin mencionar esta revisión."
    )


def _annotation(violations: list[gr.Finding]) -> str:
    items = ", ".join(f"«{i}»" for f in violations for i in f.items)
    return ("\n\n---\n*Verificación automática: no he podido localizar en la base documental "
            f"{items}. Compruébalo en el texto oficial antes de usarlo.*")


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

    # 1. Recuperar
    docs = gs.retrieve_documents(embed_model, pinecone_index, user_message, thread_id=thread_id,
                                 local_store=local_store, uid=uid, retrieval_query=retrieval_query)
    _, _, used = gs.format_context(docs, user_message)
    t_ret = time.time()

    # 2. Controles de entrada y contexto → avisos para el modelo
    state = gr.TurnState(question=user_message, search_text=search_text, used_docs=used)
    notices = gr.run(gr.INPUT_GUARDS, state) + gr.run(gr.CONTEXT_GUARDS, state)
    augmented, sources, used = gs.format_context(docs, user_message, notices=[f.detail for f in notices])
    state.used_docs = used

    # 3. Componer habilidades y generar
    prompt = compose(task, search_text)
    config = types.GenerateContentConfig(
        system_instruction=prompt.text,
        temperature=policy.temperature,
        max_output_tokens=policy.max_output_tokens,
    )
    contents = list(history) + [types.Content(role="user", parts=[types.Part(text=augmented)])]
    draft = gs._extract_text(gs._generate(genai_client, contents=contents, config=config,
                                          label=task, thread_id=thread_id))
    t_gen = time.time()

    # 4. Controles de salida
    state.answer = draft
    v_draft = gr.run(gr.OUTPUT_GUARDS, state)
    final, v_final, repaired = draft, v_draft, False

    # 5. Reparación (una vez)
    if v_draft and policy.max_repairs > 0:
        repair_contents = contents + [
            types.Content(role="model", parts=[types.Part(text=draft)]),
            types.Content(role="user", parts=[types.Part(text=_repair_request(v_draft, task))]),
        ]
        try:
            fixed = gs._extract_text(gs._generate(genai_client, contents=repair_contents, config=config,
                                                  label=f"{task}-reparacion", thread_id=thread_id))
            state.answer = fixed
            v_fixed = gr.run(gr.OUTPUT_GUARDS, state)
            count = lambda fs: sum(len(f.items) or 1 for f in fs)
            if count(v_fixed) <= count(v_draft):
                final, v_final, repaired = fixed, v_fixed, True
        except Exception:                                # noqa: BLE001
            logger.error("Reparación fallida [%s] thread=%s", task, thread_id, exc_info=True)
    t_rep = time.time()

    # 6. Anotar lo que siga sin respaldo
    annotated = bool(v_final)
    if annotated:
        final = final + _annotation(v_final)

    result = TurnResult(
        text=final, sources=sources, task=task, skills=prompt.skills, skills_version=prompt.version,
        notices=[f.as_dict() for f in notices], violations_draft=[f.as_dict() for f in v_draft],
        violations_final=[f.as_dict() for f in v_final], repaired=repaired, annotated=annotated,
        draft=draft, used_docs=used,
        ms={"recuperacion": round((t_ret - t0) * 1000), "generacion": round((t_gen - t_ret) * 1000),
            "reparacion": round((t_rep - t_gen) * 1000), "total": round((time.time() - t0) * 1000)},
    )
    logger.info(json.dumps({"evt": "agent_turn", "thread": thread_id, **result.report()}, ensure_ascii=False))
    return result
