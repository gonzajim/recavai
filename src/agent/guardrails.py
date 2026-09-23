"""
Controles (guardrails) del asesor, en tres puntos del turno.

  entrada   antes de generar, sobre la pregunta
            · ReferenciaDesconocida: la pregunta nombra una norma que no figura en la base
              documental («Ley 11/2018») o un artículo que la norma no tiene («artículo 52
              de la CSDDD»). Produce un AVISO para el modelo.
  contexto  antes de generar, sobre lo recuperado
            · ContextoVacio: no ha entrado ningún fragmento. Produce un AVISO.
  salida    después de generar, sobre la respuesta
            · DatosCriticos: cifras, fechas, años, artículos, números de norma y códigos
              de requisito que no aparecen ni en los fragmentos ni en la pregunta.
            · CitasInexistentes: citas [n] a fragmentos que el modelo no ha recibido.
            Producen VIOLACIONES, que el harness intenta reparar una vez.

Todo es determinista: expresiones regulares y comparación de valores normalizados, sin
un segundo modelo. Por eso es barato (milisegundos), reproducible y explicable: cada
violación dice exactamente qué dato no se encontró.

Qué NO detecta: obligaciones inventadas sin cifra ni artículo («la empresa debe
documentar…»). Para eso haría falta verificar frase a frase con un modelo (capa 4).

Criterio de respaldo: un dato está respaldado si aparece en CUALQUIERA de los
fragmentos que llegaron al modelo o en la pregunta (el usuario puede dar sus propias
cifras). No se exige que esté en el fragmento concreto que la frase cita: sería más
estricto, pero con muchas falsas alarmas por citas imprecisas. Los números de norma se
aceptan también si figuran en cualquier documento del corpus (registro
data/normas_corpus.json): así se admite «Directiva 2013/34/UE» aunque no haya salido en
esta consulta, pero se detecta una «Directiva 2024/1109» que no existe en el corpus.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NORM_REGISTRY = ROOT / "data" / "normas_corpus.json"


@dataclass
class Finding:
    guard: str
    stage: str                 # entrada | contexto | salida
    kind: str                  # aviso | violacion
    detail: str                # texto para el modelo (aviso) o para el registro
    items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"guard": self.guard, "stage": self.stage, "kind": self.kind,
                "detail": self.detail, "items": self.items}


@dataclass
class TurnState:
    question: str
    search_text: str
    used_docs: list[dict]
    answer: str = ""


# ======================================================================================
# Extracción de datos críticos
# ======================================================================================
_MONTHS = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], 1)}
_NUM = r"\d{1,3}(?:[.   ]\d{3})+(?:,\d+)?|\d+(?:,\d+)?"
_UNIT = (r"euros?|eur\b|€|%|por\s+ciento|empleados|trabajadores|personas|toneladas|"
         r"megalitros|°\s*c\b|grados")
_QTY = re.compile(rf"(?<![\w/.,])({_NUM})\s*(?:(mil\s+millones|millones|millón)\s*(?:de\s+)?)?({_UNIT})", re.I)
_QTY_MULT = re.compile(rf"(?<![\w/.,])({_NUM})\s*(mil\s+millones|millones|millón)\b", re.I)
_ANY_NUM = re.compile(rf"(?<![\w])({_NUM})(?:\s*(mil\s+millones|millones|millón)\b)?", re.I)
_DATE_TXT = re.compile(r"\b(\d{1,2})\s+de\s+(" + "|".join(_MONTHS) + r")\s+de\s+(\d{4})\b", re.I)
_DATE_NUM = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_YEAR = re.compile(r"(?<![\d/.,])(19[5-9]\d|20[0-4]\d)(?![\d/])")
_ART = re.compile(r"\bart(?:[íi]culos?|s?\.)\s*(\d+(?:\s*(?:bis|ter|qu[áa]ter|quinquies))?"
                  r"(?:\s*(?:,|y|e|o|a)\s*\d+(?:\s*(?:bis|ter|qu[áa]ter|quinquies))?)*)", re.I)
_NORM_ID = re.compile(r"(?<![\d./])(\d{1,4})/(\d{2,4})(?:/(?:UE|CE|CEE))?(?![\d/])")
_ESRS = re.compile(r"\b((?:[EGS][1-5]|SBM|IRO|GOV|BP|MDR)-[A-Z]?\d{0,2})\b")
# En la RESPUESTA solo cuentan como código GRI los de tres cifras (305-1) o los que van
# precedidos de «GRI»/«Contenido» (2-6, 3-3): un «3-5» suelto suele ser un intervalo.
_GRI_ANSWER = re.compile(r"\b(\d{3}-\d{1,2})\b|\b(?:GRI|[Cc]ontenidos?)\s+(\d{1,2}-\d{1,2})\b")
_GRI = re.compile(r"\b(\d{1,3}-\d{1,2})\b")
_PAGE_BEFORE = re.compile(r"(?:\bp\.?|p[áa]g(?:ina)?s?\.?)\s*$", re.I)


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _value(num: str, mult: str | None) -> float:
    s = num.replace(" ", " ").replace(" ", " ")
    if re.fullmatch(r"\d{1,3}(?:[. ]\d{3})+(?:,\d+)?", s):
        s = re.sub(r"[. ]", "", s)
    s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return float("nan")
    m = (mult or "").lower()
    if re.match(r"mil\s+mill", m):                   # «mil millones» (¡«millones» también contiene «mil»!)
        v *= 1e9
    elif m.startswith("mill"):
        v *= 1e6
    return round(v, 4)


def _articles(text: str) -> set[str]:
    out = set()
    for m in _ART.finditer(text):
        for n in re.findall(r"\d+(?:\s*(?:bis|ter|qu[áa]ter|quinquies))?", m.group(1), re.I):
            out.add(_fold(re.sub(r"\s+", "", n)))
    return out


def _dates(text: str) -> set[tuple[int, int, int]]:
    out = {(int(y), _MONTHS[mo.lower()], int(d)) for d, mo, y in _DATE_TXT.findall(text)}
    out |= {(int(y), int(mo), int(d)) for d, mo, y in _DATE_NUM.findall(text)}
    return out


@dataclass
class CriticalData:
    """Lo que la respuesta afirma con precisión, cada dato con su forma escrita."""
    quantities: dict[float, str] = field(default_factory=dict)
    dates: dict[tuple, str] = field(default_factory=dict)
    years: dict[str, str] = field(default_factory=dict)
    articles: dict[str, str] = field(default_factory=dict)
    norms: dict[str, str] = field(default_factory=dict)
    codes: dict[str, str] = field(default_factory=dict)


def extract_critical(text: str) -> CriticalData:
    cd = CriticalData()
    for m in _QTY.finditer(text):
        cd.quantities.setdefault(_value(m.group(1), m.group(2)), m.group(0).strip())
    for m in _QTY_MULT.finditer(text):
        cd.quantities.setdefault(_value(m.group(1), m.group(2)), m.group(0).strip())
    masked = text
    for rx in (_DATE_TXT, _DATE_NUM):
        for m in rx.finditer(text):
            key = (_dates(m.group(0)) or {None}).pop()
            cd.dates.setdefault(key, m.group(0))
            masked = masked.replace(m.group(0), " ")
    for m in _NORM_ID.finditer(masked):
        if _PAGE_BEFORE.search(masked[max(0, m.start() - 8):m.start()]):
            continue                                     # «p. 17/51»: página, no norma
        cd.norms.setdefault(f"{int(m.group(1))}/{int(m.group(2))}", m.group(0))
    no_norms = _NORM_ID.sub(" ", masked)
    for m in _YEAR.finditer(no_norms):
        cd.years.setdefault(m.group(1), m.group(1))
    for a in _articles(text):
        cd.articles.setdefault(a, f"artículo {a}")
    for m in _ESRS.finditer(text):
        if re.search(r"\d", m.group(1)):
            cd.codes.setdefault(m.group(1).upper(), m.group(1))
    for m in _GRI_ANSWER.finditer(no_norms):
        code = m.group(1) or m.group(2)
        cd.codes.setdefault(code, code)
    return cd


@dataclass
class Evidence:
    values: set[float]
    dates: set[tuple]
    years: set[str]
    articles: set[str]
    norms: set[str]
    codes: set[str]


def build_evidence(texts: list[str]) -> Evidence:
    blob = "\n".join(t for t in texts if t)
    values = {_value(m.group(1), m.group(2)) for m in _ANY_NUM.finditer(blob)}
    return Evidence(
        values=values,
        dates=_dates(blob),
        years=set(_YEAR.findall(blob)) | {y for y in re.findall(r"(19[5-9]\d|20[0-4]\d)", blob)},
        articles=_articles(blob),
        norms={f"{int(a)}/{int(b)}" for a, b in _NORM_ID.findall(blob)},
        codes={c.upper() for c in _ESRS.findall(blob)} | set(_GRI.findall(blob)),
    )


@lru_cache(maxsize=1)
def norm_registry() -> frozenset[str]:
    """Números de norma que aparecen en algún documento del corpus."""
    path = Path(os.getenv("RAG_NORM_REGISTRY", str(NORM_REGISTRY)))
    try:
        return frozenset(json.loads(path.read_text(encoding="utf-8"))["normas"])
    except Exception:                                    # noqa: BLE001
        return frozenset()


def unsupported(answer: str, evidence: Evidence, registry: frozenset[str] = frozenset()) -> list[str]:
    """Datos críticos de la respuesta que no aparecen en la evidencia, como se escribieron."""
    cd = extract_critical(answer)
    out = []
    out += [txt for v, txt in cd.quantities.items() if v == v and v not in evidence.values]
    out += [txt for k, txt in cd.dates.items() if k and k not in evidence.dates]
    out += [txt for y, txt in cd.years.items() if y not in evidence.years]
    out += [txt for a, txt in cd.articles.items() if a not in evidence.articles]
    out += [txt for n, txt in cd.norms.items() if n not in evidence.norms and n not in registry]
    out += [txt for c, txt in cd.codes.items() if c not in evidence.codes]
    return list(dict.fromkeys(out))


def evidence_texts(state: TurnState) -> list[str]:
    from src.chunking_v2 import doc_title
    texts = [state.question, state.search_text]
    for d in state.used_docs:
        src = d.get("title") or ""
        texts += [src, doc_title(src) if src else "", d.get("unit_label") or "", d.get("content") or ""]
    return texts


# ======================================================================================
# Controles
# ======================================================================================
_Q_NORM = re.compile(r"\b(?:ley|real\s+decreto|rd|directiva|reglamento|decisi[óo]n)\b[^.?\n]{0,25}?"
                     r"(\d{1,4}/\d{2,4}(?:/(?:UE|CE|CEE))?)", re.I)
_DOC_SHORT = {"02_NORMATIVAS/01_CSDDD": "CSDDD", "02_NORMATIVAS/02_CSDR": "CSRD"}


class ReferenciaDesconocida:
    name, stage = "referencia_desconocida", "entrada"

    def check(self, state: TurnState) -> list[Finding]:
        out = []
        reg = norm_registry()
        if reg:
            for m in _Q_NORM.finditer(state.question):
                a, b = m.group(1).split("/")[:2]
                if f"{int(a)}/{int(b)}" not in reg:
                    ref = m.group(0).strip()
                    out.append(Finding(self.name, self.stage, "aviso",
                                       f"La pregunta menciona «{ref}», que no figura en ningún documento "
                                       f"de la base documental. Dilo expresamente al principio y no "
                                       f"describas su contenido de memoria.", [ref]))
        try:
            from src.normative_graph import get_graph
            graph = get_graph()
        except Exception:                                # noqa: BLE001
            graph = None
        if graph is not None:
            for key in graph.missing_units(state.question):
                doc, unit = key.split("#")
                n = unit.replace("art.", "").replace(".", " ")
                out.append(Finding(self.name, self.stage, "aviso",
                                   f"El texto consolidado de la {_DOC_SHORT.get(doc, doc)} en la base "
                                   f"documental no contiene el artículo {n} (no existe o fue suprimido). "
                                   f"Dilo y corrige la premisa; si procede, indica qué artículo regula "
                                   f"lo que se pregunta, según los fragmentos.", [f"{_DOC_SHORT.get(doc, doc)} art. {n}"]))
        return out


class ContextoVacio:
    name, stage = "contexto_vacio", "contexto"

    def check(self, state: TurnState) -> list[Finding]:
        if state.used_docs:
            return []
        return [Finding(self.name, self.stage, "aviso",
                        "No se ha recuperado ningún fragmento de la base documental para esta "
                        "pregunta. Dilo al principio. Puedes orientar en la sección de orientación "
                        "práctica, sin cifras, fechas, artículos, números de norma ni obligaciones.")]


class DatosCriticos:
    name, stage = "datos_criticos", "salida"

    def check(self, state: TurnState) -> list[Finding]:
        bad = unsupported(state.answer, build_evidence(evidence_texts(state)), norm_registry())
        if not bad:
            return []
        return [Finding(self.name, self.stage, "violacion",
                        "Datos que no aparecen en los fragmentos ni en la pregunta", bad)]


class CitasInexistentes:
    name, stage = "citas_inexistentes", "salida"

    def check(self, state: TurnState) -> list[Finding]:
        valid = {d.get("index") for d in state.used_docs}
        cited = set()
        for grp in re.findall(r"\[(\d{1,3}(?:\s*[,;–-]\s*\d{1,3})*)\]", state.answer):
            cited |= {int(x) for x in re.findall(r"\d+", grp)}
        bad = sorted(c for c in cited if c not in valid)
        if not bad:
            return []
        return [Finding(self.name, self.stage, "violacion",
                        "Citas a fragmentos que no existen", [f"[{b}]" for b in bad])]


INPUT_GUARDS = (ReferenciaDesconocida(),)
CONTEXT_GUARDS = (ContextoVacio(),)
OUTPUT_GUARDS = (DatosCriticos(), CitasInexistentes())


def run(guards, state: TurnState) -> list[Finding]:
    out: list[Finding] = []
    for g in guards:
        try:
            out += g.check(state)
        except Exception:                                # noqa: BLE001
            # Un control que falla no puede tumbar la respuesta, pero se registra.
            from src.config import logger
            logger.error("Guardrail %s falló", getattr(g, "name", g), exc_info=True)
    return out
