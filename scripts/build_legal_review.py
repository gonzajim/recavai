#!/usr/bin/env python3
"""
Genera la página de evaluación jurídica (artefacto «Evaluación jurídica de RecavAI»): la
guía del proyecto para juristas y la mesa de anotación con las 60 preguntas de la batería,
las respuestas de una ejecución y los fragmentos que citan.

  python scripts/build_legal_review.py --run H7 --out results/juristas/evaluacion_juridica.html

La ejecución (results/rag_v2/<run>.jsonl) debe haberse hecho con el harness: de ella
salen la respuesta y los fragmentos exactos que tuvo delante el modelo. A la respuesta se
le aplica la misma limpieza que en producción (harness.clean_answer).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import types
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_cfg = types.ModuleType("src.config")                    # sin clientes externos
_cfg.logger = logging.getLogger("build_legal_review")
sys.modules["src.config"] = _cfg

from src.agent import guardrails as gr                    # noqa: E402
from src.agent.harness import clean_answer                # noqa: E402
from src.doc_titles import doc_title                      # noqa: E402

TYPE_LABEL = {
    "definicion": "Definición", "ambito": "Ámbito de aplicación", "articulo": "Contenido de un artículo",
    "coloquial": "Pregunta en lenguaje llano", "cambios": "Cambios normativos", "multiarticulo": "Varios artículos",
    "neis": "NEIS (ESRS)", "gri": "Estándares GRI", "sectorial": "Guías sectoriales", "cruce": "Cruce entre marcos",
    "real": "Caso de un usuario", "auditor": "Verificación de cumplimiento", "trampa": "Premisa falsa",
}
# Bloque prioritario para doble anotación: dos preguntas por tipo, elegidas antes de ver
# ninguna valoración jurídica (incluye aciertos y fallos conocidos del juez automático).
PRIORITY = {"B01", "B03", "B07", "B08", "B11", "B13", "B16", "B19", "B23", "B25", "B27", "B31", "B32",
            "B34", "B37", "B41", "B43", "B46", "B48", "B49", "B50", "B52", "B54", "B55", "B58", "B60"}


def pages(d: dict) -> str:
    p, pe = d.get("page"), d.get("page_end")
    if p is None:
        return ""
    return f"p. {p}" if not pe or pe == p else f"pp. {p}–{pe}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="H7")
    ap.add_argument("--version", default="orchestrator-dev-00062-zah (24/09/2026)")
    ap.add_argument("--template", default=str(ROOT / "scripts" / "legal_review_template.html"))
    ap.add_argument("--out", default=str(ROOT / "results" / "juristas" / "evaluacion_juridica.html"))
    a = ap.parse_args()

    battery = [json.loads(l) for l in (ROOT / "benchmarks" / "bateria_v1.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    run = {r["id"]: r for r in map(json.loads, (ROOT / "results" / "rag_v2" / f"{a.run}.jsonl").read_text(encoding="utf-8").splitlines())}

    preguntas = []
    for it in battery:
        r = run[it["id"]]
        answer = clean_answer(r.get("respuesta") or "")
        cited = gr.cited_indices(answer)
        citados = [{"n": k, "titulo": doc_title(d.get("title") or ""), "unidad": d.get("unit_label") or "",
                    "paginas": pages(d), "texto": d.get("content") or ""}
                   for k, d in enumerate(r.get("recuperados") or [], 1) if k in cited]
        preguntas.append({
            "id": it["id"], "tipo": it["tipo"], "tipoLabel": TYPE_LABEL.get(it["tipo"], it["tipo"]),
            "modo": it["modo"], "real": it["origen"] == "real", "prioritaria": it["id"] in PRIORITY,
            "pregunta": it["pregunta"], "respuesta": answer, "citados": citados,
            "referencia": {
                "fuentes": [{"titulo": doc_title(f["documento"]), "pagina": f.get("pagina"),
                             "articulo": f.get("articulo")} for f in it["fuentes_esperadas"]],
                "hechos": it["hechos_clave"], "debeRechazar": it["debe_rechazar"],
                "veredicto": it.get("veredicto_esperado"), "nota": it.get("nota") or "",
            },
        })

    data = {"version": a.version, "ejecucion": a.run, "generado": date.today().isoformat(), "preguntas": preguntas}
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = Path(a.template).read_text(encoding="utf-8").replace("/*__DATOS__*/", blob)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{out}: {len(preguntas)} preguntas ({sum(q['prioritaria'] for q in preguntas)} prioritarias), "
          f"{sum(len(q['citados']) for q in preguntas)} fragmentos citados, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
