"""
Habilidades (skills) del asesor: instrucciones modulares, versionadas y seleccionadas
por tarea.

Sustituyen al prompt monolítico EXPERT_SYSTEM_PROMPT. Cada habilidad es un fichero
Markdown en `skills/` con una cabecera:

    ---
    name: fundamentacion
    description: Contrato de fundamentación
    version: 1
    tasks: [asesor, herramienta, verificacion]
    triggers: [csrd, neis]        # opcional
    order: 10
    ---
    (instrucciones)

- `tasks`: en qué tareas se carga. Tareas: asesor (chat), herramienta (el auditor
  consulta al asesor), verificacion (contraste de una respuesta con la normativa) y
  planificacion (el planificador de búsqueda, src/agent/planner.py).
- `triggers`: si la habilidad los tiene, solo se carga cuando la consulta contiene
  alguno (divulgación progresiva: el modelo no recibe reglas de la CSRD para una
  pregunta de la guía OCDE de minerales). Si ninguna habilidad con disparadores encaja,
  se cargan todas: es preferible contexto de más que reglas de menos.
- `order`: posición en el prompt compuesto.

Cada composición lleva una versión (hash del contenido de las habilidades elegidas)
que el harness registra con cada turno: se puede saber con qué instrucciones exactas se
generó una respuesta concreta.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"
TASKS = ("asesor", "herramienta", "verificacion", "planificacion")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: str
    tasks: tuple[str, ...]
    triggers: tuple[str, ...]
    order: int
    body: str

    def applies_to(self, task: str) -> bool:
        return task in self.tasks


@dataclass(frozen=True)
class ComposedPrompt:
    text: str
    skills: tuple[str, ...]
    version: str


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _parse_list(value: str) -> tuple[str, ...]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return tuple(v.strip() for v in value.split(",") if v.strip())


def parse_skill(text: str, origin: str = "") -> Skill:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError(f"habilidad sin cabecera: {origin}")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    missing = {"name", "description", "version", "tasks", "order"} - set(meta)
    if missing:
        raise ValueError(f"habilidad {origin}: faltan {sorted(missing)}")
    tasks = _parse_list(meta["tasks"])
    unknown = set(tasks) - set(TASKS)
    if unknown:
        raise ValueError(f"habilidad {origin}: tareas desconocidas {sorted(unknown)}")
    return Skill(
        name=meta["name"], description=meta["description"], version=meta["version"],
        tasks=tasks, triggers=tuple(_fold(t) for t in _parse_list(meta.get("triggers", ""))),
        order=int(meta["order"]), body=m.group(2).strip(),
    )


@lru_cache(maxsize=4)
def load_skills(directory: str = str(SKILLS_DIR)) -> tuple[Skill, ...]:
    skills = [parse_skill(p.read_text(encoding="utf-8"), p.name)
              for p in sorted(Path(directory).glob("*.md"))]
    names = [s.name for s in skills]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ValueError(f"habilidades duplicadas: {sorted(dup)}")
    if not skills:
        raise ValueError(f"no hay habilidades en {directory}")
    return tuple(sorted(skills, key=lambda s: s.order))


def compose(task: str, query: str, directory: str = str(SKILLS_DIR)) -> ComposedPrompt:
    if task not in TASKS:
        raise ValueError(f"tarea desconocida: {task}")
    q = _fold(query)
    candidates = [s for s in load_skills(directory) if s.applies_to(task)]
    always = [s for s in candidates if not s.triggers]
    triggered = [s for s in candidates if s.triggers]
    matched = [s for s in triggered if any(t in q for t in s.triggers)]
    chosen = sorted(always + (matched or triggered), key=lambda s: s.order)
    text = "\n\n".join(s.body for s in chosen)
    digest = hashlib.sha1("\n".join(f"{s.name}@{s.version}\n{s.body}" for s in chosen).encode()).hexdigest()[:10]
    return ComposedPrompt(text=text, skills=tuple(s.name for s in chosen), version=digest)
