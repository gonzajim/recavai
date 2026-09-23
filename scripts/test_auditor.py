#!/usr/bin/env python3
"""
Pruebas offline de la lógica del auditor.

No usa red, ni claves, ni Firestore: sustituye Firestore por un doble en memoria y el
asesor por una función que devuelve un veredicto fijo. Cubre lo que se rompió en
producción y lo que no debe volver a romperse:

  - el catálogo es íntegro y el prompt se renderiza sin huecos
  - un bloque no puede cerrarse con preguntas obligatorias sin registrar
  - las respuestas registradas se acumulan y las pendientes se calculan bien
  - las preguntas de perfil NO llaman al asesor; las evaluables sí
  - un fallo del asesor no impide registrar la respuesta
  - el bloque activo sigue a donde se trabaja (registrar/cerrar/aplazar/retomar)
  - aplazar conserva lo respondido y se retoma sin repetir

Uso:  ./scripts/dev.sh test      o      python scripts/test_auditor.py
"""
from __future__ import annotations

import ast
import logging
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# --- Dobles de los módulos que arrastran clientes externos -------------------
logging.disable(logging.ERROR)
_cfg = types.ModuleType("src.config")
_cfg.logger = logging.getLogger("test")
sys.modules["src.config"] = _cfg
_rag = types.ModuleType("src.rag_service")
for _n in ("generate_embedding", "search_documents", "search_user_documents",
           "detect_category_filter", "classify_question", "get_routing_strategy"):
    setattr(_rag, _n, lambda *a, **k: None)
sys.modules["src.rag_service"] = _rag
_ng = types.ModuleType("src.normative_graph")
_ng.get_graph = lambda: None
sys.modules["src.normative_graph"] = _ng
_ctx = types.ModuleType("src.context_orchestrator")
_ctx.search_hybrid = lambda *a, **k: ([], [])
sys.modules["src.context_orchestrator"] = _ctx

import src.gemini_service as gs                       # noqa: E402
from src import audit_catalog as ac                   # noqa: E402
from src.assistant_instructions import AUDITOR_SYSTEM_PROMPT   # noqa: E402


# --- Firestore en memoria con merge profundo --------------------------------
class _Snap:
    def __init__(self, d): self._d, self.exists = d, d is not None
    def to_dict(self): return self._d


class _Doc:
    def __init__(self, store, key): self.store, self.key = store, key
    def get(self): return _Snap(self.store.get(self.key))

    def set(self, patch, merge=False):
        cur = self.store.setdefault(self.key, {})

        def deep(dst, src):
            for k, v in src.items():
                if isinstance(v, dict):
                    deep(dst.setdefault(k, {}), v)
                else:
                    dst[k] = v
        deep(cur, patch)


class _Col:
    def __init__(self, store): self.store = store
    def document(self, k): return _Doc(self.store, k)


class FakeFirestore:
    def __init__(self): self.store = {}
    def collection(self, _name): return _Col(self.store)


# --- Funciones reales de app.py, aisladas de sus dependencias ---------------
def _load_app_functions():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    ns = {
        "audit_catalog": ac,
        "AUDIT_BLOCKS": [{"id": b.id, "label": b.label} for b in ac.AUDIT_CATALOG],
        "AUDIT_BLOCK_IDS": set(ac.BLOCK_IDS),
        "_iso_utc": lambda x: x,
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_build_audit_progress_payload", "_format_audit_context"
        }:
            exec(compile(ast.Module([node], []), "app.py", "exec"), ns)
    return ns["_build_audit_progress_payload"], ns["_format_audit_context"]


BUILD, FORMAT = _load_app_functions()

FAILURES: list[str] = []
THREAD = "test-thread"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  \033[32m✓\033[0m {name}")
    else:
        print(f"  \033[31m✗\033[0m {name}" + (f" — {detail}" if detail else ""))
        FAILURES.append(name)


RETRIEVAL: list[str | None] = []
TASKS: list[str | None] = []


def make_expert(verdict="no cumple", fail=False):
    calls: list[str] = []

    def expert(_gc, _em, _pi, _hist, query, thread_id=None, local_store=None, uid=None,
               retrieval_query=None, task=None, **_kw):
        calls.append(query)
        RETRIEVAL.append(retrieval_query)
        TASKS.append(task)
        if fail:
            raise RuntimeError("asesor no disponible")
        return (f"VEREDICTO: {verdict}\nBRECHA: Canal solo para empleados.\n"
                "RECOMENDACIÓN: Abrirlo a proveedores y comunidades afectadas.\n"
                "BASE: CSDDD art. 14.", [])
    return expert, calls


def tool(db, name, **args):
    return gs._dispatch_tool(types.SimpleNamespace(name=name, args=args),
                             "GENAI", "EMBED", db, "PINECONE", THREAD)


def state(db, block_id):
    return (db.store.get(THREAD, {}).get("blocks") or {}).get(block_id, {})


def active(db):
    return BUILD(THREAD, "uid", db.store.get(THREAD, {}))["active_block_id"]


# =============================================================================
def test_catalogo():
    print("\nCatálogo y prompt")
    ids = [q.id for b in ac.AUDIT_CATALOG for q in b.questions]
    check("ids de pregunta únicos", len(ids) == len(set(ids)))
    check("8 bloques", len(ac.AUDIT_CATALOG) == 8)
    check("42 preguntas obligatorias",
          sum(len(b.mandatory) for b in ac.AUDIT_CATALOG) == 42)
    rendered = (AUDITOR_SYSTEM_PROMPT
                .replace("{audit_context}", "CTX")
                .replace("{block_catalog}", ac.render_catalog()))
    check("prompt sin huecos sin sustituir", "{" not in rendered.replace("{M}", ""))
    check("el prompt lleva los ids de pregunta", "block_1_q1" in rendered)
    check("bloque 1 sin verificación por pregunta",
          not any(q.verify for q in ac.get_block("block_1").mandatory))
    check("bloque 4 con verificación", all(q.verify for q in ac.get_block("block_4").mandatory))


def test_cierre_bloqueado():
    print("\nEl cierre exige cobertura completa")
    db = FakeFirestore()
    gs.chat_with_expert, _ = make_expert()
    r = tool(db, "complete_audit_block", block_id="block_1", summary="x")
    check("rechaza cerrar sin registrar nada", "RECHAZADO" in r)
    check("el bloque no queda cerrado", state(db, "block_1").get("status") != "completed")

    tool(db, "record_block_answers", block_id="block_1",
         question_ids=["block_1_q1", "block_1_q2", "block_1_qINVENTADA"], notes="Textil SA")
    check("ignora ids que no existen",
          set(state(db, "block_1")["answered"]) == {"block_1_q1", "block_1_q2"})
    r = tool(db, "complete_audit_block", block_id="block_1", summary="x")
    check("rechaza cerrar a medias", "RECHAZADO" in r and "block_1_q3" in r)

    tool(db, "record_block_answers", block_id="block_1",
         question_ids=ac.mandatory_ids("block_1"), notes="resto de datos")
    r = tool(db, "complete_audit_block", block_id="block_1", summary="Perfil recogido.")
    check("cierra con todas cubiertas", state(db, "block_1").get("status") == "completed")
    check("anuncia el bloque siguiente", "block_2" in r)


def test_verificacion_normativa():
    print("\nVerificación normativa de las respuestas")
    db = FakeFirestore()
    gs.chat_with_expert, calls = make_expert(verdict="no cumple")

    tool(db, "record_block_answers", block_id="block_1",
         question_ids=["block_1_q1"], notes="Se llama Textil Ejemplo SA")
    check("una pregunta de perfil no consulta al asesor", len(calls) == 0)

    r = tool(db, "record_block_answers", block_id="block_4",
             question_ids=["block_4_q4"], notes="Buzón ético solo para la plantilla")
    check("una pregunta evaluable sí consulta al asesor", len(calls) == 1)
    check("la consulta la construye el servidor con el texto del catálogo",
          "canal de denuncias" in calls[0].lower())
    check("la consulta incluye la respuesta del usuario", "plantilla" in calls[0])
    rq = RETRIEVAL[-1] or ""
    check("la búsqueda usa la respuesta del usuario", "plantilla" in rq)
    check("la búsqueda NO lleva las instrucciones de formato", "VEREDICTO" not in rq and "EXACTAMENTE" not in rq)
    check("el verificador usa la tarea «verificacion» (sus habilidades y controles)", TASKS[-1] == "verificacion")
    finding = (state(db, "block_4").get("findings") or [{}])[0]
    check("guarda el veredicto", finding.get("verdict") == "no cumple")
    check("ordena informar al usuario de la brecha", "DEBES informar al usuario" in r)

    gs.chat_with_expert, _ = make_expert(fail=True)
    r = tool(db, "record_block_answers", block_id="block_4",
             question_ids=["block_4_q2"], notes="Código de conducta de 2021")
    check("si el asesor falla, la respuesta se registra igual",
          "block_4_q2" in state(db, "block_4")["answered"])
    check("y no se inventa un veredicto", "VERIFICACIÓN" not in r)

    ctx = FORMAT(BUILD(THREAD, "uid", db.store[THREAD]))
    check("las brechas se reinyectan en el contexto", "BRECHAS YA DETECTADAS" in ctx)


def test_bloque_activo():
    print("\nEl bloque activo sigue a donde se trabaja")
    db = FakeFirestore()
    gs.chat_with_expert, _ = make_expert(verdict="cumple")

    tool(db, "record_block_answers", block_id="block_2",
         question_ids=["block_2_q1"], notes="Informe anual conforme a GRI")
    check("registrar fija el bloque activo", active(db) == "block_2")

    tool(db, "defer_block", block_id="block_2", reason="no tiene el informe a mano")
    check("aplazar avanza al siguiente", active(db) == "block_3")
    check("el aplazado conserva lo respondido",
          state(db, "block_2")["answered"] == ["block_2_q1"])
    check("queda marcado como aplazado", state(db, "block_2")["status"] == "deferred")

    tool(db, "record_block_answers", block_id="block_3",
         question_ids=ac.mandatory_ids("block_3"), notes="Cadena mapeada")
    tool(db, "complete_audit_block", block_id="block_3", summary="Cadena de valor mapeada.")
    check("cerrar avanza al siguiente", active(db) == "block_4")

    r = tool(db, "resume_block", block_id="block_2")
    check("retomar vuelve al bloque aplazado", active(db) == "block_2")
    check("al retomar informa de lo que falta, no de todo", "1/5" in r)

    ctx = FORMAT(BUILD(THREAD, "uid", db.store[THREAD]))
    check("el contexto no repite las ya respondidas", "YA RESPONDIDAS" in ctx)
    check("el contexto lista las pendientes como guion", "PENDIENTES EN ESTE BLOQUE" in ctx)


def test_contexto_inicial():
    print("\nContexto de una auditoría recién empezada")
    ctx = FORMAT(BUILD(THREAD, "uid", {}))
    check("arranca en el bloque 1", "[block_1]" in ctx)
    check("cobertura 0 de 6", "0 de 6" in ctx)
    check("lista las 6 obligatorias", ctx.count("▸ (block_1_q") == 6)


# =============================================================================
class _FR:
    def __init__(self, n): self.name = n


class _Part:
    def __init__(self, t=None): self.text = t


class _Cand:
    def __init__(self, parts, fr):
        self.content = type("C", (), {"parts": parts})()
        self.finish_reason = _FR(fr)
        self.safety_ratings = []


class _Usage:
    prompt_token_count = 15000
    candidates_token_count = 0
    thoughts_token_count = 8192
    total_token_count = 23192


class FakeResponse:
    """Respuesta de Gemini simulada. parts=None reproduce el fallo real de 2026-09-20."""
    def __init__(self, parts, finish_reason="STOP", function_calls=None):
        self.candidates = [_Cand(parts, finish_reason)]
        self.function_calls = function_calls or []
        self.usage_metadata = _Usage()
        self.prompt_feedback = None

    @property
    def text(self):
        parts = self.candidates[0].content.parts
        if parts is None:
            raise ValueError("sin parts")
        return "".join(p.text or "" for p in parts)


def _client(*responses):
    """Cliente falso que devuelve las respuestas dadas, una por llamada."""
    calls = []

    class Models:
        @staticmethod
        def generate_content(**kw):
            calls.append(kw)
            r = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(r, Exception):
                raise r
            return r

    return type("Cliente", (), {"models": Models}), calls


def test_respuesta_vacia():
    """Gemini devolvió una respuesta sin contenido en producción el 20/09/2026 y el
    usuario vio un error. Estas comprobaciones cubren el diagnóstico y el reintento."""
    print("\nRespuestas vacías de Gemini")
    from google.genai import types as gtypes

    vacia = FakeResponse(None, "MAX_TOKENS")
    buena = FakeResponse([_Part("Respuesta del modelo.")])

    d = gs._diagnose(vacia)
    check("el diagnóstico captura finish_reason", d.get("finish_reason") == "MAX_TOKENS")
    check("y los tokens de razonamiento", d.get("tokens_razonamiento") == 8192)

    cli, calls = _client(buena)
    gs._generate(cli, contents=[], config=gtypes.GenerateContentConfig(), label="t")
    check("con texto no reintenta", len(calls) == 1)

    cli, calls = _client(vacia, buena)
    r = gs._generate(cli, contents=[], config=gtypes.GenerateContentConfig(), label="t")
    check("vacía -> reintenta y recupera", gs._text_of(r) == "Respuesta del modelo.")
    check("el reintento desactiva el razonamiento",
          len(calls) == 2 and calls[1]["config"].thinking_config.thinking_budget == 0)
    check("y fija el presupuesto de salida",
          calls[1]["config"].max_output_tokens == gs._MAX_OUTPUT_TOKENS)

    cli, calls = _client(vacia, vacia)
    msg = gs._extract_text(gs._generate(cli, contents=[], config=gtypes.GenerateContentConfig(), label="t"))
    check("dos vacías -> mensaje accionable al usuario", "Vuelve a enviarme" in msg)

    cli, calls = _client(FakeResponse(None, "STOP", function_calls=[object()]))
    gs._generate(cli, contents=[], config=gtypes.GenerateContentConfig(), label="t")
    check("con llamada a herramienta no reintenta", len(calls) == 1)

    cli, calls = _client(vacia, RuntimeError("cuota agotada"))
    r = gs._generate(cli, contents=[], config=gtypes.GenerateContentConfig(), label="t")
    check("si el reintento falla, degrada sin excepción", r is not None and len(calls) == 2)


if __name__ == "__main__":
    print("Pruebas offline del auditor")
    test_catalogo()
    test_cierre_bloqueado()
    test_verificacion_normativa()
    test_bloque_activo()
    test_contexto_inicial()
    test_respuesta_vacia()
    print()
    if FAILURES:
        print(f"\033[31m{len(FAILURES)} comprobación(es) fallida(s):\033[0m")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("\033[32mTodas las comprobaciones pasan.\033[0m")
