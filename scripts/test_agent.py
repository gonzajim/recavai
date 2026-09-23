#!/usr/bin/env python3
"""
Pruebas offline del agente asesor: habilidades, controles y harness.

Sin red: el modelo, la recuperación y el grafo se sustituyen por dobles. Se ejecutan en
segundos y deben pasar antes de cualquier despliegue.

  python scripts/test_agent.py
"""
from __future__ import annotations

import logging
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.ERROR)
_cfg = types.ModuleType("src.config")
_cfg.logger = logging.getLogger("test")
sys.modules["src.config"] = _cfg
_rag = types.ModuleType("src.rag_service")
for _n in ("generate_embedding", "search_documents", "search_user_documents",
           "detect_category_filter", "classify_question", "get_routing_strategy"):
    setattr(_rag, _n, lambda *a, **k: None)
sys.modules["src.rag_service"] = _rag
_ctx = types.ModuleType("src.context_orchestrator")
_ctx.search_hybrid = lambda *a, **k: ([], [])
sys.modules["src.context_orchestrator"] = _ctx

import src.gemini_service as gs                          # noqa: E402
from src.agent import guardrails as gr                   # noqa: E402
from src.agent import harness                            # noqa: E402
from src.agent.skills import compose, load_skills, parse_skill   # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'✓' if cond else '✗'} {name}")
    if not cond:
        FAILURES.append(name)


# ======================================================================================
def test_skills():
    print("\nHabilidades")
    skills = load_skills()
    check("se cargan todas y tienen nombre único", len(skills) >= 10 and len({s.name for s in skills}) == len(skills))
    p = compose("asesor", "¿qué es la doble materialidad?")
    for name in ("identidad", "fundamentacion", "citas", "abstencion", "formato_asesor", "orientacion_practica"):
        check(f"asesor incluye «{name}»", name in p.skills)
    check("asesor no incluye «verificacion» ni «herramienta_auditor»",
          "verificacion" not in p.skills and "herramienta_auditor" not in p.skills)
    check("una pregunta de NEIS carga csrd_neis y no csddd",
          "csrd_neis" in p.skills and "csddd" not in p.skills)
    q = compose("asesor", "¿Qué pide el contenido GRI 305-1?")
    check("una pregunta GRI carga estandares y no csrd_neis", "estandares" in q.skills and "csrd_neis" not in q.skills)
    n = compose("asesor", "hola, ¿me ayudas?")
    check("sin disparadores se cargan todas las de dominio",
          {"csrd_neis", "csddd", "estandares"} <= set(n.skills))
    v = compose("verificacion", "canal de reclamaciones de la CSDDD")
    check("verificacion incluye su habilidad y no la orientación práctica",
          "verificacion" in v.skills and "orientacion_practica" not in v.skills)
    check("el orden respeta `order`", list(p.skills).index("identidad") < list(p.skills).index("fundamentacion"))
    check("la versión es estable", compose("asesor", "¿qué es la doble materialidad?").version == p.version)
    with tempfile.TemporaryDirectory() as d:
        for s in skills:
            (pathlib.Path(d) / f"{s.order:02d}_{s.name}.md").write_text(
                f"---\nname: {s.name}\ndescription: x\nversion: {s.version}\ntasks: [{', '.join(s.tasks)}]\n"
                + (f"triggers: [{', '.join(s.triggers)}]\n" if s.triggers else "")
                + f"order: {s.order}\n---\n{s.body}" + (" CAMBIO" if s.name == "fundamentacion" else ""),
                encoding="utf-8")
        load_skills.cache_clear()
        changed = compose("asesor", "¿qué es la doble materialidad?", directory=d)
        load_skills.cache_clear()
    check("cambiar una habilidad cambia la versión", changed.version != p.version)
    try:
        parse_skill("---\nname: x\n---\ncuerpo", "roto.md")
        check("una habilidad sin campos obligatorios se rechaza", False)
    except ValueError:
        check("una habilidad sin campos obligatorios se rechaza", True)


# ======================================================================================
def test_guardrails():
    print("\nControles")
    ev = gr.build_evidence(["más de 5 000 empleados y un volumen de negocios superior a 1 500 000 000 EUR",
                            "a más tardar el 26 de julio de 2028", "Artículo 2 Ámbito de aplicación",
                            "Directiva (UE) 2024/1760; límite del 3 % del volumen de negocios", "Contenido 305-1"])
    ok = ("Aplica a más de 5.000 empleados y 1.500 millones de euros (artículo 2 de la Directiva (UE) 2024/1760), "
          "con transposición el 26 de julio de 2028, sanción de hasta el 3 % y el contenido 305-1. Ver p. 17/51; de 3-5.")
    check("una respuesta respaldada no tiene violaciones", gr.unsupported(ok, ev) == [])
    bad = gr.unsupported("Antes eran 1.000 empleados y 450 millones (Directiva 2024/1109, artículo 7), desde 2027, "
                         "2 mil millones de euros, el 1 de enero de 2030 y GRI 2-6.", ev)
    for item in ("1.000 empleados", "450 millones", "2024/1109", "artículo 7", "2027", "2 mil millones de euros",
                 "1 de enero de 2030", "2-6"):
        check(f"detecta «{item}»", item in bad)
    check("un número de norma del registro del corpus no es violación",
          gr.unsupported("la Directiva 2013/34/UE", ev, frozenset({"2013/34"})) == [])
    check("«mil millones» y «millones» no se confunden",
          gr.unsupported("1.500 millones de euros", ev) == [] and gr.unsupported("1.500 mil millones de euros", ev) != [])

    st = gr.TurnState(question="q", search_text="q", used_docs=[{"index": 1}, {"index": 2}],
                      answer="Según [1] y [2, 5] y [7]…")
    f = gr.CitasInexistentes().check(st)
    check("detecta citas a fragmentos inexistentes", f and set(f[0].items) == {"[5]", "[7]"})
    check("contexto vacío produce un aviso",
          bool(gr.ContextoVacio().check(gr.TurnState("q", "q", []))))
    check("con contexto no hay aviso", gr.ContextoVacio().check(gr.TurnState("q", "q", [{"index": 1}])) == [])

    gr.norm_registry.cache_clear()
    real_registry = gr.norm_registry
    gr.norm_registry = lambda: frozenset({"2024/1760", "2013/34"})
    import src.normative_graph as ng
    real_get = ng.get_graph
    ng.get_graph = lambda: None
    try:
        a = gr.ReferenciaDesconocida().check(gr.TurnState("¿Qué impone la Ley 11/2018 de información no financiera?", "", []))
        check("avisa de una ley que no está en el corpus", a and "11/2018" in a[0].detail)
        b = gr.ReferenciaDesconocida().check(gr.TurnState("¿Qué dice la Directiva (UE) 2024/1760?", "", []))
        check("no avisa de una norma que sí está", b == [])
    finally:
        gr.norm_registry = real_registry
        ng.get_graph = real_get


# ======================================================================================
class _Resp:
    def __init__(self, text):
        self.text, self.function_calls = text, []


def _harness_with(answers: list[str], docs: list[dict]):
    """Harness con recuperación y modelo simulados. Devuelve (resultado, llamadas)."""
    calls = []
    gs.retrieve_documents = lambda *a, **k: [dict(d) for d in docs]

    def fake_generate(_client, *, contents, config, label, thread_id=None):
        calls.append({"contents": contents, "config": config, "label": label})
        return _Resp(answers[min(len(calls) - 1, len(answers) - 1)])
    gs._generate = fake_generate
    gs._extract_text = lambda r: r.text
    return calls


DOCS = [{"title": "02_NORMATIVAS/01_CSDDD", "content": "tener una media de más de 5 000 empleados y un volumen "
         "de negocios mundial neto superior a 1 500 000 000 EUR", "page": 3, "unit_label": "Artículo 2 · Ámbito",
         "category": "CSDDD", "score": 0.9, "article": "art.2"}]


def test_harness():
    print("\nHarness")
    calls = _harness_with(["Aplica a más de 5.000 empleados y 1.500 millones de euros [1]."], DOCS)
    r = harness.run_turn(None, None, None, [], "¿A qué empresas aplica la CSDDD?")
    check("respuesta respaldada: una sola llamada, sin reparación", len(calls) == 1 and not r.repaired)
    check("temperatura 0,2", calls[0]["config"].temperature == 0.2)
    check("las habilidades van en system_instruction", "Fundamentación" in calls[0]["config"].system_instruction)
    check("registra versión de habilidades", len(r.skills_version) == 10)

    calls = _harness_with(["Aplica desde 2027 a más de 1.000 empleados [1] [4].",
                           "Aplica a más de 5.000 empleados [1]."], DOCS)
    r = harness.run_turn(None, None, None, [], "¿A qué empresas aplica la CSDDD?")
    check("dato inventado → se repara una vez", len(calls) == 2 and r.repaired)
    req = calls[1]["contents"][-1].parts[0].text
    check("la petición de reparación nombra los datos", "2027" in req and "1.000 empleados" in req and "[4]" in req)
    check("se sirve la versión reparada, sin nota", r.text == "Aplica a más de 5.000 empleados [1]." and not r.annotated)
    check("guarda el borrador y sus violaciones", "2027" in r.draft and r.violations_draft and not r.violations_final)

    calls = _harness_with(["Aplica desde 2027 [1].", "Sigue aplicando desde 2027 [1]."], DOCS)
    r = harness.run_turn(None, None, None, [], "¿Desde cuándo aplica la CSDDD?")
    check("si la reparación no lo arregla, nota visible", r.annotated and "no he podido localizar" in r.text and "2027" in r.text)
    check("no hay más de una reparación", len(calls) == 2)

    calls = _harness_with(["VEREDICTO: no cumple\nBASE: art. 99 [1]", "VEREDICTO: no cumple\nBASE: art. 2 [1]"], DOCS)
    r = harness.run_turn(None, None, None, [], "VERIFICACIÓN…", task="verificacion", retrieval_query="canal de quejas")
    check("en verificación, la reparación exige mantener el formato",
          "VEREDICTO / BRECHA / RECOMENDACIÓN / BASE" in calls[1]["contents"][-1].parts[0].text)
    check("en verificación se cargan sus habilidades", "verificacion" in r.skills)

    calls = _harness_with(["La base documental no cubre esta pregunta."], [])
    r = harness.run_turn(None, None, None, [], "¿Qué es ZORROVIOLETA42?")
    msg = calls[0]["contents"][-1].parts[0].text
    check("sin fragmentos, el modelo recibe el aviso de contexto vacío",
          "AVISOS DEL SISTEMA" in msg and "ningún fragmento" in msg)
    check("el aviso queda en el resultado", any(n["guard"] == "contexto_vacio" for n in r.notices))

    try:
        harness.run_turn(None, None, None, [], "x", task="otra")
        check("una tarea desconocida se rechaza", False)
    except ValueError:
        check("una tarea desconocida se rechaza", True)


if __name__ == "__main__":
    print("Pruebas offline del agente asesor")
    test_skills()
    test_guardrails()
    test_harness()
    print()
    if FAILURES:
        print(f"\033[31m{len(FAILURES)} fallan:\033[0m " + "; ".join(FAILURES))
        sys.exit(1)
    print("\033[32mTodas las comprobaciones pasan.\033[0m")
