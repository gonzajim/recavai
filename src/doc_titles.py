"""
Títulos legibles de los documentos del corpus, a partir de su clave `source`.

Módulo mínimo y sin dependencias para que lo use el servicio en ejecución (controles del
asesor) sin cargar las herramientas de indexación (SYS-4: `corpus_pipeline` y
`chunking_v2` son offline).
"""
from __future__ import annotations

import re

DOC_TITLES = {
    "02_NORMATIVAS/01_CSDDD": "CSDDD — Directiva (UE) 2024/1760 sobre diligencia debida de las empresas en materia de sostenibilidad (texto consolidado a 18.03.2026)",
    "02_NORMATIVAS/02_CSDR": "CSRD — Directiva (UE) 2022/2464 sobre información corporativa en materia de sostenibilidad (texto consolidado a 18.03.2026)",
    "02_NORMATIVAS/03_NEIS": "NEIS (ESRS) — Reglamento Delegado (UE) 2023/2772, Normas Europeas de Información sobre Sostenibilidad",
    "02_NORMATIVAS/01_1_análisis cambios CSDDD": "Análisis de los cambios de la CSDDD tras stop-the-clock y Ómnibus I",
    "02_NORMATIVAS/02_1_análisis cambios CSRD": "Análisis de los cambios de la CSRD tras stop-the-clock y Ómnibus I",
    "02_NORMATIVAS/00_Glosario operativo CSRD_CSDDD_NEIS": "Glosario operativo CSRD, CSDDD y NEIS",
    "01_MARCO TEORICO/01_MARCO TEORICO": "Marco teórico RECAVA",
}


def doc_title(source: str) -> str:
    if source in DOC_TITLES:
        return DOC_TITLES[source]
    name = source.split("/")[-1]
    name = re.sub(r"\s*-\s*Spanish$", "", name).replace("_", " ")
    return re.sub(r"\s+", " ", name).strip()
