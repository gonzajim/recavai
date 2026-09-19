# src/audit_catalog.py
"""
Catálogo de la auditoría: fuente ÚNICA de verdad de bloques y preguntas.

Lo consumen tres sitios, y por eso vive aquí y no duplicado en cada uno:
  1. `assistant_instructions.AUDITOR_SYSTEM_PROMPT` — renderiza el catálogo con los ids
     de pregunta, para que el modelo pueda referenciarlas al registrar avance.
  2. `app.py` — construye el estado de progreso y el contexto que se inyecta al prompt.
  3. `gemini_service.py` — valida en SERVIDOR que no se cierre un bloque con preguntas
     obligatorias sin responder.

Regla de oro: si cambias una pregunta obligatoria aquí, cambia el comportamiento del
auditor en los tres sitios a la vez. No añadas preguntas en el texto del prompt.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str            # estable: b<N>_q<M>. Nunca se reutiliza ni se renumera.
    text: str
    mandatory: bool = True
    # ¿La respuesta a esta pregunta es evaluable contra la normativa?
    # False para las puramente descriptivas o de perfil ("¿cómo se llama la empresa?",
    # "¿qué presupuesto tiene?"): verificarlas contra la CSDDD no significa nada y solo
    # gastaría una llamada al experto. La aplicabilidad normativa del perfil se evalúa
    # al cerrar el bloque 1, no pregunta a pregunta.
    verify: bool = True


@dataclass(frozen=True)
class Block:
    id: str
    label: str
    objective: str
    questions: tuple[Question, ...]
    note: str = ""          # nota de cierre o adaptativa que se muestra al modelo

    @property
    def mandatory(self) -> tuple[Question, ...]:
        return tuple(q for q in self.questions if q.mandatory)

    @property
    def optional(self) -> tuple[Question, ...]:
        return tuple(q for q in self.questions if not q.mandatory)


def _q(bid: str, n: int, text: str, mandatory: bool = True, verify: bool = True) -> Question:
    return Question(id=f"{bid}_q{n}", text=" ".join(text.split()),
                    mandatory=mandatory, verify=verify)


AUDIT_CATALOG: tuple[Block, ...] = (
    Block(
        id="block_1",
        label="1. Contexto y Alcance",
        objective="Identificar la empresa, su perfil y sus obligaciones normativas aplicables.",
        questions=(
            _q("block_1", 1, "Nombre de la empresa y actividad principal (sector/CNAE).", verify=False),
            _q("block_1", 2, "Países en los que opera (sede, filiales, mercados principales).", verify=False),
            _q("block_1", 3, "Número total de empleados (en la empresa y, si procede, en el grupo).", verify=False),
            _q("block_1", 4, "Facturación anual aproximada (intervalo orientativo es suficiente).", verify=False),
            _q("block_1", 5, "Estructura jurídica: ¿es empresa independiente, filial de un grupo, o matriz?", verify=False),
            _q("block_1", 6, "¿Tiene experiencia previa en auditorías o reporting de sostenibilidad?", verify=False),
            _q("block_1", 7, "¿Forma parte de la cadena de valor de una empresa obligada por CSRD o CSDDD?", False),
        ),
        note=("Antes de cerrar, informa al usuario de su situación normativa: ¿ámbito CSDDD directo "
              "(>1000 empleados y >450M€)? ¿CSRD (>250 empleados y >40M€)? ¿Afectada indirectamente "
              "como proveedora?"),
    ),
    Block(
        id="block_2",
        label="2. Información Corporativa",
        objective="Evaluar el estado actual de reporting y compromisos voluntarios de sostenibilidad.",
        questions=(
            _q("block_2", 1, "¿Publica la empresa algún informe o memoria de sostenibilidad? ¿Con qué periodicidad?"),
            _q("block_2", 2, "¿Sigue algún estándar reconocido de reporting (GRI, SASB, TCFD, EINF/NEIS, Pacto Mundial)?"),
            _q("block_2", 3, "¿El informe incluye métricas cuantificables (emisiones, energía, agua, residuos)?"),
            _q("block_2", 4, "¿Ha definido objetivos de sostenibilidad a corto, medio y largo plazo?"),
            _q("block_2", 5, "¿El informe se somete a verificación o auditoría externa?"),
            _q("block_2", 6, "¿El informe es público y accesible en la web corporativa?", False),
            _q("block_2", 7, "¿Ha comunicado los resultados a inversores u otros grupos de interés clave?", False),
            _q("block_2", 8, "¿Tiene certificaciones ambientales (ISO 14001, EMAS) o sociales (SA8000, B Corp)?", False),
            _q("block_2", 9, "¿Tiene acceso a financiación verde o préstamos ESG-linked?", False),
        ),
    ),
    Block(
        id="block_3",
        label="3. Cadena de Valor",
        objective="Mapear la cadena de suministro e identificar exposición a riesgos en proveedores.",
        questions=(
            _q("block_3", 1, "Descripción de la cadena de valor: ¿qué actividades realiza upstream (proveedores) "
                             "y downstream (distribución, clientes)?"),
            _q("block_3", 2, "¿Cuántos proveedores directos tiene aproximadamente? ¿En qué países están?", verify=False),
            _q("block_3", 3, "¿Tiene proveedores en países o regiones con alto riesgo en derechos humanos "
                             "o medioambiente (zonas de gobernanza débil)?"),
            _q("block_3", 4, "¿Incluye cláusulas de derechos humanos y sostenibilidad en contratos con proveedores?"),
            _q("block_3", 5, "¿Ha establecido mecanismos de trazabilidad en la cadena de suministro?"),
            _q("block_3", 6, "¿Conoce a proveedores más allá del Tier 1 (Tier 2, Tier 3)?", False),
            _q("block_3", 7, "¿Qué porcentaje de proveedores directos tienen riesgo significativo en DDHH?", False),
            _q("block_3", 8, "¿Extiende la política de DDHH a proveedores indirectos a través de los directos?", False),
            _q("block_3", 9, "¿Pueden los consumidores finales conocer la cadena de suministro de la empresa?", False),
        ),
    ),
    Block(
        id="block_4",
        label="4. Gobernanza y Compliance",
        objective="Evaluar el marco de gobierno, políticas y códigos en materia de DDHH.",
        questions=(
            _q("block_4", 1, "¿Tiene la empresa una política de Derechos Humanos aprobada formalmente? "
                             "Si es sí: ¿en qué año? ¿cuándo fue la última revisión?"),
            _q("block_4", 2, "¿Tiene Código de Conducta? ¿Incluye referencia a DDHH?"),
            _q("block_4", 3, "¿Existe un responsable o comité de sostenibilidad con rango directivo?"),
            _q("block_4", 4, "¿Tiene canal de denuncias (whistleblowing)? ¿Es accesible también para externos "
                             "(trabajadores de proveedores, comunidades afectadas)?"),
            _q("block_4", 5, "¿Se ha comunicado la política de DDHH a los empleados? ¿Qué formación reciben?"),
            _q("block_4", 6, "¿Cómo obliga a sus proveedores a cumplir su Código de Conducta?"),
            _q("block_4", 7, "¿Se ha elaborado la política con asesoramiento especializado interno o externo?", False),
            _q("block_4", 8, "¿La política recoge expresamente los DDHH mínimos reconocidos internacionalmente?", False),
            _q("block_4", 9, "¿Con qué periodicidad informa el responsable al Consejo sobre DDHH y sostenibilidad?", False),
            _q("block_4", 10, "¿Los documentos están disponibles en la web corporativa?", False),
            _q("block_4", 11, "¿Las infracciones conllevan medidas disciplinarias para empleados y proveedores?", False),
        ),
    ),
    Block(
        id="block_5",
        label="5. Impacto Ambiental",
        objective="Evaluar el desempeño ambiental y los compromisos climáticos.",
        questions=(
            _q("block_5", 1, "¿Ha calculado su huella de carbono? ¿Qué alcances cubre (1, 2, 3)?"),
            _q("block_5", 2, "¿Tiene objetivos de reducción de emisiones? ¿Alineados con SBTi o net-zero 2050?"),
            _q("block_5", 3, "¿Qué porcentaje de su energía proviene de fuentes renovables?"),
            _q("block_5", 4, "¿Cómo gestiona sus residuos? ¿Genera residuos peligrosos?"),
            _q("block_5", 5, "¿Tiene plan de transición climática con hitos y recursos definidos?"),
            _q("block_5", 6, "¿El agua es un recurso crítico en su proceso productivo?", False),
            _q("block_5", 7, "¿Sus operaciones afectan a ecosistemas o biodiversidad?", False),
            _q("block_5", 8, "¿Reporta métricas cuantificables de reducción de emisiones?", False),
            _q("block_5", 9, "¿Implementa estrategias de economía circular?", False),
        ),
        note=("Adaptativa: en sector servicios u hotelero, prioriza consumo energético e hídrico y "
              "simplifica residuos peligrosos y biodiversidad. Adaptar NO es omitir una [M]: si no "
              "aplica, hazla igualmente y registra la respuesta 'no aplica' con su motivo."),
    ),
    Block(
        id="block_6",
        label="6. Personas y Derechos Humanos",
        objective="Evaluar la gestión de riesgos en DDHH laborales en empresa y cadena de valor.",
        questions=(
            _q("block_6", 1, "¿Define la empresa lo que entiende por condiciones de trabajo dignas? ¿Incluye "
                             "prohibición de discriminación y acoso, derechos sindicales, jornada, salario digno?"),
            _q("block_6", 2, "¿Cuáles son los principales riesgos laborales significativos identificados en la "
                             "empresa, filiales y cadena de suministro?"),
            _q("block_6", 3, "¿Existe un programa de formación anual en seguridad y salud laboral? "
                             "¿Cuál es el índice de accidentalidad del último año?"),
            _q("block_6", 4, "¿La política de DDHH recoge expresamente la prohibición del trabajo infantil? "
                             "¿Ha identificado riesgo de trabajo infantil en su cadena de suministro?"),
            _q("block_6", 5, "¿Prevé la política mecanismos de reparación para daños causados por la empresa? "
                             "¿Qué mecanismos existen (disculpa, compensación, vías extrajudiciales)?"),
            _q("block_6", 6, "¿Ha habido incidencias laborales o reclamaciones de DDHH en el último año? "
                             "¿Existe un plan de medidas correctoras?"),
            _q("block_6", 7, "¿Realiza evaluación previa de proveedores con riesgo significativo?", False),
            _q("block_6", 8, "¿Tiene programa de RSE para mitigar riesgos laborales?", False),
            _q("block_6", 9, "¿Tiene cláusulas contractuales de seguridad y salud con proveedores?", False),
            _q("block_6", 10, "¿Colabora con ONG o agencias internacionales (UNICEF, OIT)?", False),
        ),
    ),
    Block(
        id="block_7",
        label="7. Riesgos y Controles",
        objective="Evaluar la metodología de análisis de riesgos y el sistema de gestión en DDHH.",
        questions=(
            _q("block_7", 1, "¿Qué metodología de análisis de riesgos en DDHH utiliza? ¿Considera escala "
                             "(gravedad del impacto), alcance (número de afectados) y posibilidad de reparación?"),
            _q("block_7", 2, "¿En qué año se realizó el último análisis de riesgos? ¿Con qué periodicidad se revisa?"),
            _q("block_7", 3, "¿Las conclusiones del análisis están integradas en procesos internos y decisiones? "
                             "¿Existen asignaciones presupuestarias para responder a impactos negativos?"),
            _q("block_7", 4, "¿Existe canal de reclamaciones en materia de DDHH? ¿Cuántas reclamaciones se "
                             "recibieron el último año? ¿Qué porcentaje se resolvió?"),
            _q("block_7", 5, "¿Ha realizado un análisis de doble materialidad? ¿Tiene identificados sus "
                             "principales IROs (impactos, riesgos y oportunidades) ESG?"),
            _q("block_7", 6, "¿Han participado expertos en DDHH internos o externos en el análisis?", False),
            _q("block_7", 7, "¿Se consultó a grupos potencialmente afectados?", False),
            _q("block_7", 8, "¿Los riesgos climáticos están integrados en la planificación financiera?", False),
            _q("block_7", 9, "¿Los indicadores de supervisión son cualitativos y cuantitativos?", False),
        ),
    ),
    Block(
        id="block_8",
        label="8. Conclusiones y Roadmap",
        objective="Sintetizar los hallazgos de la auditoría y definir un plan de acción.",
        questions=(
            _q("block_8", 1, "De los gaps identificados durante la auditoría, ¿cuáles son más urgentes de abordar?", verify=False),
            _q("block_8", 2, "¿Tiene ya un plan de acción o roadmap de sostenibilidad aprobado? "
                             "Si es sí: ¿qué hitos y calendario contempla?"),
            _q("block_8", 3, "¿Qué recursos humanos y presupuesto puede destinar a la implementación?", verify=False),
            _q("block_8", 4, "¿Qué tipo de apoyo externo necesita? (formación, consultoría, herramientas "
                             "tecnológicas, asesoramiento jurídico)", verify=False),
            _q("block_8", 5, "¿Cuáles considera sus principales fortalezas en sostenibilidad y DDHH?", False),
            _q("block_8", 6, "¿Cuál es el calendario estimado para cumplir con las obligaciones normativas?", False),
        ),
        note=("Al cerrar este bloque, genera el resumen ejecutivo de la auditoría: perfil y obligaciones "
              "aplicables; hallazgos por bloque (fortalezas y brechas); brechas Crítico/Alto/Medio; "
              "recomendaciones prioritarias; próximos pasos y calendario orientativo."),
    ),
)

# ---------------------------------------------------------------------------
# Índices y consultas
# ---------------------------------------------------------------------------
BLOCKS_BY_ID: dict[str, Block] = {b.id: b for b in AUDIT_CATALOG}
BLOCK_IDS: tuple[str, ...] = tuple(b.id for b in AUDIT_CATALOG)
BLOCK_ORDER: dict[str, int] = {b.id: i for i, b in enumerate(AUDIT_CATALOG)}


def get_block(block_id: str) -> Block | None:
    return BLOCKS_BY_ID.get(block_id)


def mandatory_ids(block_id: str) -> list[str]:
    b = get_block(block_id)
    return [q.id for q in b.mandatory] if b else []


def valid_question_ids(block_id: str) -> set[str]:
    b = get_block(block_id)
    return {q.id for q in b.questions} if b else set()


def missing_mandatory(block_id: str, answered: list[str] | set[str] | None) -> list[Question]:
    """Preguntas [M] del bloque que siguen sin responder. Es la función que decide
    si un bloque puede cerrarse; la usa el servidor, no el modelo."""
    b = get_block(block_id)
    if not b:
        return []
    done = set(answered or ())
    return [q for q in b.mandatory if q.id not in done]


def verifiable(block_id: str, question_ids: list[str] | set[str] | None) -> list[Question]:
    """De los ids dados, los que son evaluables contra normativa (`verify=True`).
    Si la lista sale vacía no se llama al experto: no hay nada que verificar."""
    b = get_block(block_id)
    if not b:
        return []
    wanted = set(question_ids or ())
    return [q for q in b.questions if q.id in wanted and q.verify]


def coverage(block_id: str, answered: list[str] | set[str] | None) -> tuple[int, int]:
    """(obligatorias respondidas, total obligatorias)."""
    b = get_block(block_id)
    if not b:
        return (0, 0)
    total = len(b.mandatory)
    done = set(answered or ())
    return (sum(1 for q in b.mandatory if q.id in done), total)


# ---------------------------------------------------------------------------
# Render para el prompt
# ---------------------------------------------------------------------------
_LINE = "─" * 65


def render_block(block: Block) -> str:
    out = [_LINE, f"{block.label}  [{block.id}]", _LINE, f"Objetivo: {block.objective}"]
    n_m = len(block.mandatory)
    out.append(f"Obligatorias: {n_m}. El bloque NO puede cerrarse sin las {n_m}.")
    out.append("")
    for q in block.questions:
        tag = "[M]" if q.mandatory else "[ ]"
        out.append(f"{tag} ({q.id}) {q.text}")
    if block.note:
        out.append("")
        out.append(f"Nota: {block.note}")
    return "\n".join(out)


def render_catalog() -> str:
    """Catálogo completo con ids, tal y como lo ve el modelo en su prompt."""
    header = (
        "[M] = OBLIGATORIA. Hay que formularla y obtener respuesta explícita antes de cerrar.\n"
        "[ ] = Opcional. Aplícala según el perfil de la empresa.\n"
        "El identificador entre paréntesis es el que debes pasar a record_block_answers."
    )
    return "\n\n".join([header] + [render_block(b) for b in AUDIT_CATALOG])
