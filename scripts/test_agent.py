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


# ======================================================================================
# Contexto adaptativo (docs/PLAN_CONTEXTO.md)
# ======================================================================================
def _store():
    from src.context_builder import UnitStore
    def c(s, n, u, label, p, t, pe=None):
        return {"s": s, "n": n, "u": u, "l": label, "p": p, "pe": pe or p, "t": t}
    big = "x" * 10_000                                   # ~2.400 tokens por fragmento
    chunks = {
        "d1-0000": c("D1", 0, "D1#art.2", "Artículo 2 · Ámbito", 3, "Primera parte del artículo 2. Frase de solape final."),
        "d1-0001": c("D1", 1, "D1#art.2", "Artículo 2 · Ámbito", 3, "Frase de solape final. Segunda parte: más de 5 000 empleados.", 4),
        "d1-0002": c("D1", 2, "D1#art.3", "Artículo 3 · Definiciones", 4, "Definiciones del artículo 3, con un límite del 3 %."),
        "d1-0003": c("D1", 3, None, "Anexo", 5, "Texto sin unidad A."),
        "d1-0004": c("D1", 4, None, "Anexo", 5, "Texto sin unidad B."),
        "d1-0005": c("D1", 5, None, "Anexo", 6, "Texto sin unidad C."),
        **{f"d2-000{i}": c("D2", i, "D2#art.9", "Artículo 9", 10 + i, big + str(i)) for i in range(5)},
    }
    docs = {"D1": {"total_pages": 20, "category": "CSDDD", "ids": [f"d1-000{i}" for i in range(6)]},
            "D2": {"total_pages": 40, "category": "general", "ids": [f"d2-000{i}" for i in range(5)]}}
    units = {"D1#art.2": {"label": "Artículo 2 · Ámbito", "ids": ["d1-0000", "d1-0001"], "pages": [3, 4]},
             "D1#art.3": {"label": "Artículo 3 · Definiciones", "ids": ["d1-0002"], "pages": [4, 4]},
             "D2#art.9": {"label": "Artículo 9", "ids": [f"d2-000{i}" for i in range(5)], "pages": [10, 14]}}
    return UnitStore({"docs": docs, "units": units, "chunks": chunks})


def test_context_builder():
    import os
    from src import context_builder as cb
    print("\nContexto adaptativo")
    store = _store()
    hit = lambda cid, score=0.9: {"id": cid, "title": store.chunks[cid]["s"], "content": store.chunks[cid]["t"], "score": score}

    b = cb.build_blocks([hit("d1-0001")], "M", store)
    check("un fragmento se amplía a su artículo completo", len(b) == 1 and b[0]["ids"] == ["d1-0000", "d1-0001"])
    check("la unión quita la frase de solape", b[0]["content"].count("Frase de solape final.") == 1
          and b[0]["content"].startswith("Primera parte") and "5 000 empleados" in b[0]["content"])
    check("el bloque lleva unidad, artículo y páginas",
          b[0]["unit_label"] == "Artículo 2 · Ámbito" and b[0]["article"] == "art.2" and (b[0]["page"], b[0]["page_end"]) == (3, 4))
    check("el extracto es el fragmento recuperado", b[0]["excerpt"].startswith("Frase de solape final. Segunda"))

    b = cb.build_blocks([hit("d1-0004")], "M", store)
    check("sin unidad: el fragmento y sus vecinos, en un bloque", len(b) == 1 and b[0]["ids"] == ["d1-0003", "d1-0004", "d1-0005"])

    b = cb.build_blocks([hit("d2-0002")], "M", store)
    check("unidad mayor que el tope del nivel: solo la ventana", b[0]["ids"] == ["d2-0001", "d2-0002", "d2-0003"])
    b = cb.build_blocks([hit("d2-0002")], "L", store)
    check("en el nivel L la misma unidad entra entera", b[0]["ids"] == [f"d2-000{i}" for i in range(5)])

    b = cb.build_blocks([hit("d1-0004"), hit("d2-0002", 0.8), hit("d1-0001", 0.7)], "M", store)
    check("documentos por prioridad y, dentro, en el orden del texto",
          [x["ids"][0] for x in b] == ["d1-0000", "d1-0003", "d2-0001"])
    b = cb.build_blocks([hit("d2-0002"), hit("d1-0001")], "S", store)
    check("el presupuesto del nivel se respeta", sum(len(x["content"]) for x in b) <= cb.LEVELS["S"].budget * cb.CHARS_PER_TOKEN)
    loose = {"title": "mi_politica.pdf", "content": "Nuestra política de proveedores.", "score": 0.5}
    b = cb.build_blocks([loose, hit("d1-0001")], "M", store)
    check("los fragmentos fuera del almacén (PDF del usuario) pasan tal cual y primero",
          b[0]["title"] == "mi_politica.pdf" and b[1]["article"] == "art.2")
    b = cb.build_blocks([], "L", store, whole_documents=["D1"])
    check("documento entero: todas sus unidades, por orden", [x["ids"][0] for x in b] == ["d1-0000", "d1-0002", "d1-0003"])
    check("sin almacén: un bloque por fragmento (comportamiento anterior)",
          len(cb.build_blocks([hit("d1-0001"), hit("d1-0004")], "M", None)) == 2)

    from src.agent.harness import choose_level
    check("verificación del auditor → S", choose_level("verificacion", "x", None, False) == "S")
    check("enumeración → L", choose_level("asesor", "¿Cuáles son los cinco pasos de la guía?", None, False) == "L")
    check("unidad nombrada → S", choose_level("asesor", "¿Qué dice el artículo 9 de la CSDDD?", None, True) == "S")
    check("el auditor no pasa de M", choose_level("herramienta", "¿Cuáles son los pasos?", None, False) == "M")
    os.environ["RAG_CONTEXT_MAX_LEVEL"] = "M"
    try:
        check("RAG_CONTEXT_MAX_LEVEL=M limita el nivel y quita la segunda pasada",
              choose_level("asesor", "¿Cuáles son los pasos?", None, False) == "M" and cb.next_level("M") is None)
    finally:
        del os.environ["RAG_CONTEXT_MAX_LEVEL"]
    check("sin límite, después de M viene L", cb.next_level("M") == "L" and cb.next_level("L") is None)


def test_planner():
    import os
    from src.agent import planner
    print("\nPlanificador")
    p = planner.parse('{"tipo": "enumeracion", "normas": ["csddd", "Ley 11/2018"], "busquedas": ["a", "b", "c", "d", "e"]}')
    check("solo normas de la lista cerrada", p.normas == ["CSDDD"])
    check("como mucho 4 búsquedas", len(p.busquedas) == 4 and p.tipo == "enumeracion")
    check("un tipo desconocido pasa a «puntual»", planner.parse('{"tipo": "otro", "normas": [], "busquedas": []}').tipo == "puntual")
    check("la habilidad del planificador solo se carga en su tarea",
          compose("planificacion", "x").skills == ("planificador",) and "planificador" not in compose("asesor", "x").skills)
    os.environ["RAG_PLANNER"] = "1"
    try:
        check("se planifica una pregunta normal", planner.should_plan("Si un proveedor usa trabajo infantil, ¿qué hago?", "asesor", False))
        check("no se planifica un saludo", not planner.should_plan("hola", "asesor", False))
        check("no se planifica la verificación del auditor", not planner.should_plan("canal de quejas de la empresa", "verificacion", False))
        check("no se planifica una pregunta corta con la unidad nombrada",
              not planner.should_plan("¿Qué dice el artículo 9 de la CSDDD?", "asesor", True))
    finally:
        del os.environ["RAG_PLANNER"]
    check("desactivado por defecto", not planner.should_plan("Si un proveedor usa trabajo infantil, ¿qué hago?", "asesor", False))


def test_citations():
    print("\nCitas y abstención")
    check("citas con intervalos y agrupadas", gr.cited_indices("Ver [2-4] y [7][8], y [10, 12].") == {2, 3, 4, 7, 8, 10, 12})
    used = [{"index": 1, "title": "D1", "content": "más de 5 000 empleados", "unit_label": "Artículo 2"},
            {"index": 2, "title": "D1", "content": "un límite del 3 %", "unit_label": "Artículo 3"}]
    c = gr.citation_check("Aplica a más de 5.000 empleados [2]. El límite es del 3 % [2].", used)
    check("dato citado en el fragmento equivocado", len(c["wrong"]) == 1 and "5.000 empleados" in c["wrong"][0] and c["ok"] == 1)
    check("el artículo de la cabecera respalda la cita", gr.citation_check("Lo regula el artículo 2 [1].", used)["wrong"] == [])
    check("un dato que da la pregunta no exige cita",
          gr.citation_check("Con 800 empleados [2] no aplica.", used, "Tengo 800 empleados")["wrong"] == [])
    for t in ("La base documental no especifica los umbrales.", "Este punto no consta en los fragmentos.",
              "No se detalla en la base documental qué ocurre."):
        check(f"abstención: «{t[:32]}…»", gr.abstentions(t) >= 1)
    check("la etiqueta interna del aviso no llega al usuario",
          harness.clean_answer("No existe el artículo 52 [AVISOS DEL SISTEMA]. El art. 27 [6].") == "No existe el artículo 52. El art. 27 [6].")
    check("una respuesta normal no es abstención",
          gr.abstentions("Aplica a más de 5.000 empleados [1]. La empresa no debe esperar a 2028 [2].") == 0)


def test_harness_units():
    from src import context_builder as cb
    print("\nHarness con contexto adaptativo")
    store = _store()
    cb._STORE, cb._LOADED = store, True
    hit = lambda cid, score=0.9: {"id": cid, "title": store.chunks[cid]["s"], "content": store.chunks[cid]["t"], "score": score}
    try:
        calls = _harness_with(["Aplica a más de 5 000 empleados [1]."], [hit("d1-0001"), hit("d1-0002", 0.8)])
        r = harness.run_turn(None, None, None, [], "¿A qué empresas se aplica?")
        check("nivel M por defecto y bloque con el artículo entero", r.level == "M" and r.used_docs[0]["ids"] == ["d1-0000", "d1-0001"])
        check("solo se devuelven las fuentes citadas", [s["index"] for s in r.sources] == [1])
        check("el registro lleva nivel, tokens de contexto y bloques",
              r.report()["nivel"] == "M" and r.report()["tokens_contexto"] > 0 and r.report()["bloques"] == 2)

        calls = _harness_with(["La base documental no especifica a qué empresas se aplica.",
                               "Aplica a más de 5 000 empleados [1]."], [hit("d1-0001")])
        r = harness.run_turn(None, None, None, [], "¿A qué empresas se aplica?")
        check("si se abstiene, segunda pasada con el nivel siguiente",
              r.second_pass and r.level == "L" and [c["label"] for c in calls] == ["asesor", "asesor-segunda"])
        check("se sirve la segunda respuesta", r.text.startswith("Aplica a más de 5 000"))

        calls = _harness_with(["La Ley 11/2018 no consta en la base documental."], [hit("d1-0001")])
        r = harness.run_turn(None, None, None, [], "¿Qué impone la Ley 11/2018 de información no financiera?")
        check("con una premisa falsa no hay segunda pasada", not r.second_pass and len(calls) == 1)

        calls = _harness_with(["Aplica a más de 5.000 empleados [2].", "Aplica a más de 5.000 empleados [1]."],
                              [hit("d1-0001"), hit("d1-0002", 0.8)])
        r = harness.run_turn(None, None, None, [], "¿A qué empresas se aplica?")
        req = calls[1]["contents"][-1].parts[0].text
        check("cita al fragmento equivocado → se repara", r.repaired and "Cita el fragmento que sí lo contiene" in req)
        _harness_with(["Aplica a más de 5.000 empleados [2]."] * 2, [hit("d1-0001"), hit("d1-0002", 0.8)])
        r = harness.run_turn(None, None, None, [], "¿A qué empresas se aplica?")
        check("una cita imprecisa que persiste se registra pero no se anota al usuario",
              r.violations_final and not r.annotated and "Verificación automática" not in r.text)
    finally:
        cb._STORE, cb._LOADED = None, False


if __name__ == "__main__":
    print("Pruebas offline del agente asesor")
    test_skills()
    test_guardrails()
    test_harness()
    test_context_builder()
    test_planner()
    test_citations()
    test_harness_units()
    print()
    if FAILURES:
        print(f"\033[31m{len(FAILURES)} fallan:\033[0m " + "; ".join(FAILURES))
        sys.exit(1)
    print("\033[32mTodas las comprobaciones pasan.\033[0m")
