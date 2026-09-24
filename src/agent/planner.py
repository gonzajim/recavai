"""
Planificador de búsqueda (docs/PLAN_CONTEXTO.md, fase 2).

Una llamada corta a un modelo rápido, sin razonamiento, que traduce la pregunta al
vocabulario de la norma y dice qué tipo de pregunta es. Resuelve el fallo que más
contexto no arregla: en la batería, las preguntas coloquiales y las reales se quedaban en
torno al 50 % de hechos clave en contexto aunque se recuperasen 50 fragmentos, porque el
usuario pregunta con palabras que no están en la norma («cortar con un proveedor» frente
a «suspensión o terminación de la relación comercial»).

Lo que devuelve SOLO sirve para buscar y para elegir el nivel de contexto: nunca llega al
modelo que responde ni al usuario. Las normas que nombre se filtran contra una lista
cerrada. Si falla o tarda más de PLANNER_TIMEOUT_S, el turno sigue sin él (ARC-6).

Se activa con RAG_PLANNER=1. Instrucciones: habilidad `planificador` (tarea planificacion).
"""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from google.genai import types

from src.config import logger
from src.agent.skills import compose

PLANNER_MODEL = "gemini-2.5-flash-lite"
PLANNER_TIMEOUT_S = 2.0
KINDS = ("puntual", "enumeracion", "multiunidad", "cambios", "panorama")
NORMS = ("CSDDD", "CSRD", "NEIS", "GRI", "OCDE")
MAX_QUERIES = 4

_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "tipo": types.Schema(type=types.Type.STRING, enum=list(KINDS)),
        "normas": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        "busquedas": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
    },
    required=["tipo", "normas", "busquedas"],
)

# Un solo ejecutor para todo el proceso: si el planificador se pasa de tiempo, su hilo
# termina en segundo plano sin bloquear el turno.
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="planner")


@dataclass
class Plan:
    tipo: str = "puntual"
    normas: list[str] = field(default_factory=list)
    busquedas: list[str] = field(default_factory=list)
    ms: int = 0
    version: str = ""

    def as_dict(self) -> dict:
        return {"tipo": self.tipo, "normas": self.normas, "busquedas": self.busquedas, "ms": self.ms}


def enabled() -> bool:
    return os.getenv("RAG_PLANNER", "0").strip().lower() in ("1", "true", "si", "sí", "on")


_GREETING = re.compile(r"^\s*(hola|buen[oa]s|gracias|muchas gracias|hello|hi|adi[óo]s|ok|vale)\b[\s!.,¿?]*$", re.I)


def should_plan(question: str, task: str, explicit_units: bool) -> bool:
    """Se omite cuando no aporta: verificación del auditor (su consulta ya la construye el
    servidor), saludos, y preguntas cortas que ya nombran la unidad («artículo 9 de la
    CSDDD»), que resuelve el grafo."""
    if not enabled() or task == "verificacion":
        return False
    if _GREETING.match(question or "") or len((question or "").split()) < 3:
        return False
    if explicit_units and len(question.split()) <= 15:
        return False
    return True


def parse(raw: str) -> Plan:
    data = json.loads(raw)
    tipo = data.get("tipo") if data.get("tipo") in KINDS else "puntual"
    normas = [n.upper() for n in data.get("normas") or [] if isinstance(n, str) and n.upper() in NORMS]
    busquedas = [b.strip() for b in data.get("busquedas") or [] if isinstance(b, str) and b.strip()]
    return Plan(tipo=tipo, normas=list(dict.fromkeys(normas)), busquedas=busquedas[:MAX_QUERIES])


def _call(genai_client, question: str) -> Plan:
    prompt = compose("planificacion", question)
    t0 = time.time()
    r = genai_client.models.generate_content(
        model=PLANNER_MODEL, contents=f"Pregunta: {question}",
        config=types.GenerateContentConfig(
            system_instruction=prompt.text, temperature=0.0, max_output_tokens=512,
            response_mime_type="application/json", response_schema=_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_budget=0)))
    plan = parse(r.text)
    plan.ms, plan.version = round((time.time() - t0) * 1000), prompt.version
    return plan


def start(genai_client, question: str):
    """Lanza el planificador en segundo plano. Devuelve un futuro (o None)."""
    try:
        return _EXECUTOR.submit(_call, genai_client, question)
    except Exception:                                    # noqa: BLE001
        logger.error("Planificador: no se pudo lanzar", exc_info=True)
        return None


def wait(future, deadline: float) -> Plan | None:
    """Espera al planificador como mucho hasta `deadline` (time.time()). None si falla."""
    if future is None:
        return None
    try:
        return future.result(timeout=max(0.0, deadline - time.time()))
    except FutureTimeout:
        logger.warning("Planificador: sin respuesta en %.1f s, se sigue sin él", PLANNER_TIMEOUT_S)
    except Exception:                                    # noqa: BLE001
        logger.error("Planificador fallido", exc_info=True)
    return None
