#!/usr/bin/env python3
"""
Batería de evaluación v1 — 60 preguntas en 13 tipos.

Sirve para medir el sistema ANTES y DESPUÉS de un cambio (modelo de embeddings,
troceado, grafo) sin esperar al golden set del IDPEI. No lo sustituye: es un
instrumento de desarrollo, no de publicación.

Cada pregunta lleva:
  - fuentes_esperadas: documento (clave `source` del índice), página y unidad
    normativa (artículo, anexo, requisito NEIS, contenido GRI).
  - hechos_clave: 2-4 afirmaciones casi literales del corpus que una buena
    respuesta tiene que transmitir. Son la rúbrica del juez.
  - debe_rechazar: la respuesta correcta es decir que no está en el corpus o
    corregir la premisa.
  - veredicto_esperado: solo en las comprobaciones del modo auditor.

Los hechos clave NO están escritos de memoria: salen de pasajes localizados en el
texto del corpus. `--verify` lo comprueba automáticamente: cada hecho tiene que
aparecer (al menos el 80 % de sus palabras con contenido) en alguna de las
páginas declaradas, con tolerancia de ±1 página.

Uso:
  python benchmarks/bateria_v1.py                          # escribe bateria_v1.jsonl
  python benchmarks/bateria_v1.py --verify .cache/corpus_txt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

# --- claves `source` del índice ---------------------------------------------------
CSDDD = "02_NORMATIVAS/01_CSDDD"
CSRD = "02_NORMATIVAS/02_CSDR"
NEIS = "02_NORMATIVAS/03_NEIS"
AN_CSDDD = "02_NORMATIVAS/01_1_análisis cambios CSDDD"
AN_CSRD = "02_NORMATIVAS/02_1_análisis cambios CSRD"
MARCO = "01_MARCO TEORICO/01_MARCO TEORICO"


def GRI(name: str) -> str:
    return f"03_ESTANDARES/PROCESOS GRI/{name}"


def OCDE(name: str) -> str:
    return f"03_ESTANDARES/GUIAS OCDE/{name}"


GRI1 = GRI("GRI 1_ Fundamentos 2021 - Spanish")
GRI3 = GRI("GRI 3_ Temas Materiales 2021 - Spanish")
GRI303 = GRI("GRI 303_ Agua y efluentes 2018 - Spanish")
GRI305 = GRI("GRI 305_ Emisiones 2016 - Spanish")
GRI306 = GRI("GRI 306_ Residuos 2020 - Spanish")
GRI408 = GRI("GRI 408_ Trabajo infantil 2016 - Spanish")
GRI14 = GRI("GRI 14_ Sector Minería 2024 - Spanish")
OCDE_TEXTIL = OCDE("OCDE_03_Textil_Calzado_2021_ES")
OCDE_MINERALES = OCDE("OCDE_05_Minerales_Conflicto_Alto_Riesgo_ES")
OCDE_AGRI = OCDE("OCDE_02_Agricultura_OCDE_FAO_2017_ES")
OCDE_BANCA = OCDE("OCDE_07_Finanzas_Prestamos_Aseguramiento_ES")

TIPOS = {
    "definicion": "Definiciones",
    "ambito": "A quién aplica y umbrales",
    "articulo": "Artículo concreto",
    "coloquial": "Preguntas con palabras cotidianas",
    "cambios": "Cambios y fechas (Ómnibus, stop-the-clock)",
    "multiarticulo": "Varios artículos a la vez",
    "neis": "Requisitos de las NEIS",
    "gri": "Contenidos GRI",
    "sectorial": "Sectoriales (OCDE y GRI)",
    "cruce": "Cruce entre marcos",
    "real": "Preguntas reales de usuarios",
    "auditor": "Comprobaciones del modo auditor",
    "trampa": "Trampas y fuera de corpus",
}

ITEMS: list[dict] = []


def q(id_, tipo, pregunta, fuentes, hechos, *, modo="asesor", origen="corpus",
      debe_rechazar=False, veredicto=None, nota=""):
    assert tipo in TIPOS, tipo
    ITEMS.append({
        "id": id_, "tipo": tipo, "modo": modo, "origen": origen, "pregunta": pregunta,
        "fuentes_esperadas": [{"documento": d, "pagina": p, "articulo": u} for d, p, u in fuentes],
        "hechos_clave": hechos, "debe_rechazar": debe_rechazar,
        "veredicto_esperado": veredicto, "nota": nota,
    })


# ======================================================================================
# 1. Definiciones (5)
# ======================================================================================
q("B01", "definicion", "¿Qué entiende la CSDDD por «cadena de actividades»?",
  [(CSDDD, 9, "art. 3")],
  ["las actividades de los socios comerciales que intervienen en los eslabones anteriores de la cadena",
   "incluidos el diseño, la extracción, el abastecimiento, la fabricación, el transporte, el almacenamiento y el suministro de materias primas",
   "los eslabones posteriores relacionadas con la distribución, el transporte y el almacenamiento de un producto de dicha empresa"])

q("B02", "definicion", "¿Qué diferencia hay entre un socio comercial directo y uno indirecto según la CSDDD?",
  [(CSDDD, 9, "art. 3")],
  ["socio comercial directo: entidad con la que la empresa tenga un acuerdo comercial relacionado con las operaciones, productos o servicios de la empresa",
   "socio comercial indirecto: no es socio comercial directo pero realiza operaciones comerciales relacionadas con las operaciones, productos o servicios de la empresa"])

q("B03", "definicion", "¿Cuándo se considera «grave» un efecto adverso según la CSDDD?",
  [(CSDDD, 10, "art. 3")],
  ["un efecto adverso que sea especialmente significativo por su naturaleza",
   "como un efecto que conlleve daños a la vida, la salud o la libertad humanas",
   "o por su magnitud, alcance o carácter irreparable",
   "incluido el número de personas que se vean o puedan verse afectadas"])

q("B04", "definicion", "¿Qué es la doble materialidad (doble importancia relativa) en las NEIS?",
  [(NEIS, 10, None), (NEIS, 318, None)],
  ["tiene dos dimensiones: la importancia relativa en términos de incidencia y la importancia relativa financiera",
   "una cuestión cumple el criterio si tiene importancia relativa desde el punto de vista de la incidencia, la perspectiva financiera o ambas"])

q("B05", "definicion", "¿Qué son los temas materiales según los Estándares GRI?",
  [(GRI3, 4, None), (GRI1, 8, None)],
  ["temas que representan los impactos más significativos sobre la economía, el medio ambiente y las personas",
   "incluidos los impactos que afectan a los derechos humanos"])

# ======================================================================================
# 2. A quién aplica y umbrales (5)
# ======================================================================================
q("B06", "ambito", "A qué empresas aplica la CSDDD?",
  [(CSDDD, 3, "art. 2")],
  ["tener una media de más de 5 000 empleados y un volumen de negocios mundial neto superior a 1 500 000 000 EUR",
   "ser la empresa matriz última de un grupo que haya alcanzado dichos umbrales",
   "acuerdos de franquicia o de licencia con cánones de más de 75 000 000 EUR",
   "también a empresas constituidas de conformidad con la legislación de un tercer país"],
  origen="real")

q("B07", "ambito", "Una empresa española con 3.000 empleados y 2.000 millones de euros de facturación mundial, ¿está obligada por la CSDDD?",
  [(CSDDD, 3, "art. 2")],
  ["el umbral es tener una media de más de 5 000 empleados y un volumen de negocios mundial neto superior a 1 500 000 000 EUR",
   "aun no habiendo alcanzado los umbrales, puede quedar incluida si es la empresa matriz última de un grupo que haya alcanzado dichos umbrales"],
  nota="Respuesta correcta: por sí sola no, porque no supera los 5.000 empleados; sí si es matriz última de un grupo que los supere.")

q("B08", "ambito", "¿A partir de qué tamaño una empresa tiene que presentar información de sostenibilidad según la CSRD tras el Ómnibus I?",
  [(CSRD, 55, "art. 5"), (AN_CSRD, 2, None)],
  ["empresas que hayan generado un volumen de negocios neto superior a 450 000 000 EUR",
   "y superen un número medio de 1 000 empleados durante el ejercicio",
   "para los ejercicios que comiencen a partir del 1 de enero de 2027"])

q("B09", "ambito", "¿Siguen obligadas las pymes cotizadas a presentar información de sostenibilidad según la CSRD?",
  [(AN_CSRD, 3, None)],
  ["la reforma suprime el régimen obligatorio específico para pymes cotizadas",
   "y el art. 29 quater sobre normas limitadas",
   "sin perjuicio del reporting voluntario"])

q("B10", "ambito", "Tenemos una red de franquicias en la UE. ¿Cuándo nos afectaría la CSDDD?",
  [(CSDDD, 3, "art. 2")],
  ["acuerdos de franquicia o de licencia en la Unión a cambio de cánones con empresas terceras independientes",
   "cánones que hayan ascendido a más de 75 000 000 EUR en el último ejercicio",
   "volumen de negocios mundial neto superior a 275 000 000 EUR",
   "una identidad común, un concepto empresarial común y la aplicación de métodos empresariales uniformes"])

# ======================================================================================
# 3. Artículo concreto (5)
# ======================================================================================
q("B11", "articulo", "¿Qué establece el artículo 10 de la CSDDD?",
  [(CSDDD, 17, "art. 10")],
  ["adoptar medidas adecuadas para prevenir o mitigar suficientemente los efectos adversos potenciales detectados en aplicación del artículo 8",
   "elaborar y aplicar un plan de acción preventiva con plazos de actuación razonables y claramente definidos",
   "tener en cuenta la capacidad de la empresa para influir en el socio comercial"])

q("B12", "articulo", "¿Qué regula el artículo 14 de la CSDDD?",
  [(CSDDD, 24, "art. 14")],
  ["mecanismo de notificación y procedimiento de reclamación",
   "las personas afectadas o con motivos fundados para pensar que podrían verse afectadas y sus representantes legítimos",
   "los sindicatos y otros representantes de los trabajadores que trabajen en la cadena de actividades",
   "las organizaciones de la sociedad civil"])

q("B13", "articulo", "¿Qué dice el artículo 27 de la CSDDD sobre las sanciones?",
  [(CSDDD, 36, "art. 27")],
  ["las sanciones serán efectivas, proporcionadas y disuasorias",
   "el límite máximo para las sanciones pecuniarias se fije en el 3 % del volumen de negocios mundial neto",
   "si la empresa incumple una sanción pecuniaria, una declaración pública en la que se indique la empresa responsable y la naturaleza de la infracción"])

q("B14", "articulo", "¿Qué establece el artículo 37 de la CSDDD sobre transposición y aplicación?",
  [(CSDDD, 44, "art. 37")],
  ["adoptarán y publicarán las disposiciones a más tardar el 26 de julio de 2028",
   "aplicarán dichas disposiciones a partir del 26 de julio de 2029",
   "las del artículo 16 durante los ejercicios que comiencen el 1 de enero de 2030 o después de esa fecha"])

q("B15", "articulo", "¿Qué contiene el artículo 9 de la CSDDD?",
  [(CSDDD, 16, "art. 9")],
  ["la priorización se basará en la gravedad y la probabilidad de los efectos adversos",
   "una vez abordados los más graves y más probables, la empresa abordará los menos graves y menos probables",
   "no haber abordado un efecto adverso menos significativo no expondrá a la empresa a sanciones con arreglo al artículo 27"])

# ======================================================================================
# 4. Preguntas con palabras cotidianas (6)
# ======================================================================================
q("B16", "coloquial", "Si descubro que un proveedor está usando trabajo infantil, ¿qué tengo que hacer?",
  [(CSDDD, 19, "art. 11"), (CSDDD, 21, "art. 11"), (CSDDD, 47, "anexo I")],
  ["adoptar medidas adecuadas para eliminar los efectos adversos reales o minimizar su alcance",
   "como último recurso, abstenerse de entablar nuevas relaciones o de ampliar las ya existentes con el socio comercial",
   "suspender la relación comercial cuando el Derecho por el que se rijan sus relaciones así lo permita",
   "la prohibición de las peores formas de trabajo infantil, Convenio de la OIT n.º 182"])

q("B17", "coloquial", "¿Tengo que contar con mis trabajadores y con las comunidades afectadas para hacer la diligencia debida?",
  [(CSDDD, 23, "art. 13"), (CSDDD, 10, "art. 3")],
  ["tomar medidas adecuadas para colaborar de forma efectiva con las partes interesadas",
   "partes interesadas: los empleados, los sindicatos y representantes de los trabajadores y las personas o comunidades cuyos derechos o intereses se vean afectados",
   "proporcionarán información pertinente y exhaustiva a las partes interesadas durante las consultas"])

q("B18", "coloquial", "¿Cada cuánto tiempo tengo que revisar si mis medidas de diligencia debida funcionan?",
  [(CSDDD, 26, "art. 15")],
  ["evaluaciones periódicas de sus propias operaciones y medidas y las de sus filiales y socios comerciales",
   "sin demora injustificada cuando tenga lugar un cambio significativo",
   "al menos cada cinco años"])

q("B19", "coloquial", "¿Puedo cortar con un proveedor que no cumple con los derechos humanos?",
  [(CSDDD, 18, "art. 10"), (CSDDD, 19, "art. 10"), (CSDDD, 21, "art. 11")],
  ["como último recurso y hasta que se hayan abordado los efectos",
   "abstenerse de entablar nuevas relaciones o de ampliar las ya existentes con ese socio comercial",
   "suspender la relación comercial con respecto a las actividades de que se trate cuando el Derecho aplicable lo permita",
   "adoptar un plan de acción preventiva mejorado para los efectos adversos específicos"],
  nota="El texto consolidado habla de suspender, no de poner fin a la relación.")

q("B20", "coloquial", "Si alguien sufre daños por culpa de mi cadena de suministro, ¿me pueden demandar?",
  [(CSDDD, 39, "art. 29")],
  ["cuando en virtud del Derecho nacional una empresa sea considerada responsable de los daños, la persona tendrá derecho a una indemnización íntegra",
   "la indemnización íntegra no conllevará una compensación excesiva, como indemnizaciones punitivas",
   "el plazo de prescripción para presentar demandas será de al menos cinco años"],
  nota="El Ómnibus suprimió el apartado 1: la responsabilidad se remite al Derecho nacional.")

q("B21", "coloquial", "¿Tengo que publicar algo en mi web sobre la diligencia debida que hago?",
  [(CSDDD, 26, "art. 16")],
  ["publicación en su sitio web de una declaración anual",
   "no más tarde de doce meses a partir de la fecha de cierre del balance del ejercicio",
   "no se aplicará a empresas sujetas a requisitos de información sobre sostenibilidad de los artículos 19 bis, 29 bis o 40 bis de la Directiva 2013/34/UE"])

# ======================================================================================
# 5. Cambios y fechas (5)
# ======================================================================================
q("B22", "cambios", "¿Qué ha pasado con la obligación de tener un plan de transición climática en la CSDDD?",
  [(AN_CSDDD, 5, "art. 22"), (AN_CSDDD, 2, None)],
  ["se suprime el artículo 22",
   "la CSDDD deja de contener una obligación autónoma de adoptar y aplicar plan de transición climática"])

q("B23", "cambios", "¿Qué diferencia hay entre la Directiva 2025/794 y la Directiva 2026/470 en lo que afecta a la CSDDD?",
  [(AN_CSDDD, 2, None)],
  ["la Directiva (UE) 2025/794 tuvo una función eminentemente temporal: modificar las fechas de aplicación y transposición",
   "la Directiva (UE) 2026/470 introduce una reconfiguración material del régimen: eleva umbrales y reduce el perímetro de empresas obligadas",
   "elimina el artículo 22 sobre plan de transición climática",
   "sustituye el régimen europeo armonizado de responsabilidad civil por una remisión más intensa al Derecho nacional"])

q("B24", "cambios", "¿Cómo cambió el Ómnibus la forma de identificar los impactos en la cadena de actividades?",
  [(CSDDD, 15, "art. 8"), (AN_CSDDD, 4, "art. 8")],
  ["un ejercicio exploratorio, únicamente sobre la base de la información razonablemente disponible",
   "evaluación en profundidad solo en ámbitos de mayor probabilidad y gravedad",
   "añade límites a solicitudes de información a socios comerciales"])

q("B25", "cambios", "¿Qué hizo la directiva «stop-the-clock» con los plazos de la CSRD?",
  [(AN_CSRD, 2, None)],
  ["aplazó dos años la entrada en aplicación de los requisitos de información",
   "para las empresas que aún no habían empezado a reportar",
   "grandes empresas de la segunda ola y pymes cotizadas de la tercera ola"])

q("B26", "cambios", "¿Habrá normas NEIS sectoriales obligatorias?",
  [(AN_CSRD, 3, None), (MARCO, 97, None)],
  ["se elimina la facultad de adoptar normas sectoriales obligatorias",
   "posible sustitución por orientaciones sectoriales",
   "el Ómnibus I sustituyó la obligación de elaborar normas sectoriales por una opción voluntaria"])

# ======================================================================================
# 6. Varios artículos a la vez (5)
# ======================================================================================
q("B27", "multiarticulo", "Una vez detectados los efectos adversos, ¿cómo decido cuáles atender primero y qué hago con ellos?",
  [(CSDDD, 16, "art. 9"), (CSDDD, 17, "art. 10"), (CSDDD, 19, "art. 11")],
  ["cuando no sea posible abordarlos todos a la vez, se da prioridad a los efectos detectados con arreglo al artículo 8",
   "la priorización se basará en la gravedad y la probabilidad",
   "a efectos del cumplimiento de las obligaciones establecidas en el artículo 10 o el artículo 11",
   "una vez abordados los más graves y probables, la empresa abordará los efectos adversos menos graves y menos probables"])

q("B28", "multiarticulo", "¿Puede la empresa matriz hacer la diligencia debida por sus filiales? ¿Qué responsabilidad conservan las filiales?",
  [(CSDDD, 13, "art. 6")],
  ["las empresas matrices pueden cumplir las obligaciones de los artículos 7 a 16 en nombre de sus filiales, si esto garantiza el cumplimiento efectivo",
   "las filiales siguen sujetas a las competencias de la autoridad de control, de conformidad con el artículo 25",
   "y a su responsabilidad civil, de conformidad con el artículo 29"])

q("B29", "multiarticulo", "¿Qué relación hay entre la política de diligencia debida y las medidas de prevención y eliminación de efectos?",
  [(CSDDD, 14, "art. 7"), (CSDDD, 17, "art. 10")],
  ["integrar la diligencia debida en todas sus políticas pertinentes y en sus sistemas de gestión de riesgos",
   "un código de conducta aplicable a la empresa, sus filiales y sus socios comerciales directos o indirectos",
   "de conformidad con el artículo 10, apartado 2, letra b), el artículo 10, apartado 4, el artículo 11, apartado 3, letra c), o el artículo 11, apartado 5",
   "la política se elaborará previa consulta a los empleados de la empresa y sus representantes"])

q("B30", "multiarticulo", "¿Qué se tiene en cuenta para sancionar a una empresa que no previno efectos adversos en su cadena?",
  [(CSDDD, 36, "art. 27")],
  ["la naturaleza, gravedad y duración de la infracción",
   "las inversiones realizadas y cualquier apoyo específico prestado de conformidad con los artículos 10 y 11",
   "la medida en que se tomaron decisiones de priorización de conformidad con el artículo 9",
   "la medida en que la empresa ha llevado a cabo cualquier medida de reparación"])

q("B31", "multiarticulo", "¿Qué obligación de reparación tiene la empresa y cómo se conecta con el procedimiento de reclamación?",
  [(CSDDD, 22, "art. 12"), (CSDDD, 24, "art. 14")],
  ["una empresa reparará un efecto adverso real que haya causado por sí misma o conjuntamente",
   "cuando el efecto haya sido causado únicamente por un socio comercial, la empresa podrá repararlo voluntariamente",
   "las empresas permitirán presentar reclamaciones cuando existan inquietudes legítimas en cuanto a los efectos adversos"])

# ======================================================================================
# 7. Requisitos de las NEIS (5)
# ======================================================================================
q("B32", "neis", "¿Qué información pide el requisito de divulgación E1-6 de las NEIS?",
  [(NEIS, 91, "E1-6")],
  ["la empresa divulgará en toneladas métricas equivalentes de CO2",
   "emisiones de GEI brutas de alcance 1, de alcance 2 y de alcance 3",
   "y emisiones de GEI totales"])

q("B33", "neis", "¿Qué debe divulgar una empresa en el requisito E1-1 de las NEIS?",
  [(NEIS, 84, "E1-1")],
  ["la empresa divulgará su plan de transición para la mitigación del cambio climático",
   "compatibles con la limitación del calentamiento global a 1,5 °C en consonancia con el Acuerdo de París",
   "el objetivo de lograr la neutralidad climática de aquí a 2050"])

q("B34", "neis", "¿Qué pide el requisito IRO-1 de las NEIS?",
  [(NEIS, 60, "IRO-1")],
  ["la empresa divulgará su proceso para determinar sus incidencias, riesgos y oportunidades",
   "y para evaluar cuáles son de importancia relativa",
   "como base para determinar la información de su estado de sostenibilidad"])

q("B35", "neis", "¿Qué hay que informar sobre las políticas relativas a los trabajadores de la cadena de valor según las NEIS?",
  [(NEIS, 245, "S2-1")],
  ["describirá sus políticas adoptadas para gestionar sus incidencias de importancia relativa sobre los trabajadores de la cadena de valor",
   "así como los riesgos y oportunidades de importancia relativa conexos"])

q("B36", "neis", "¿Qué pide el requisito G1-1 de las NEIS?",
  [(NEIS, 296, "G1-1")],
  ["la empresa divulgará sus políticas con respecto a las cuestiones de conducta empresarial",
   "y la forma en que fomenta su cultura corporativa"])

# ======================================================================================
# 8. Contenidos GRI (5)
# ======================================================================================
q("B37", "gri", "¿Qué hay que informar en el contenido GRI 305-1?",
  [(GRI305, 9, "305-1")],
  ["valor bruto de las emisiones directas de GEI (alcance 1) en toneladas métricas de CO2 equivalente",
   "gases incluidos en el cálculo",
   "emisiones biogénicas de CO2",
   "año base para el cálculo, si procede"])

q("B38", "gri", "¿Qué pide el contenido GRI 303-3 sobre el agua?",
  [(GRI303, 12, "303-3")],
  ["la extracción de agua total de todas las áreas en megalitros",
   "desglose según las fuentes: aguas superficiales, aguas subterráneas, aguas marinas, agua producida, agua de terceros",
   "la extracción total de agua de todas las zonas sometidas a estrés hídrico"])

q("B39", "gri", "¿Cómo se informa de los residuos generados según los Estándares GRI?",
  [(GRI306, 12, "306-3")],
  ["peso total de los residuos generados en toneladas métricas",
   "desglose de este total en función de la composición de los residuos",
   "información contextual necesaria para entender los datos y la manera en que se recopilaron"])

q("B40", "gri", "¿Qué pide el contenido GRI 408-1 sobre trabajo infantil?",
  [(GRI408, 8, "408-1")],
  ["operaciones y proveedores que se consideran con riesgo significativo de presentar casos de trabajo infantil",
   "trabajadores jóvenes expuestos a trabajo peligroso",
   "en cuanto a tipo de operación y proveedor, y países o áreas geográficas"])

q("B41", "gri", "Según GRI 3-3, ¿qué debe explicar una organización para cada tema material?",
  [(GRI3, 21, "3-3")],
  ["describir los impactos reales y potenciales, negativos y positivos, sobre la economía, el medio ambiente y las personas",
   "indicar si la organización está relacionada con un impacto negativo mediante sus actividades o como resultado de sus relaciones comerciales",
   "describir sus políticas o compromisos en relación al tema material",
   "las medidas adoptadas para prevenir o mitigar impactos negativos potenciales"])

# ======================================================================================
# 9. Sectoriales (5)
# ======================================================================================
q("B42", "sectorial", "Compramos prendas de algodón: ¿qué riesgos específicos del producto señala la guía OCDE de textil y calzado?",
  [(OCDE_TEXTIL, 47, None)],
  ["los productos de algodón pueden entrañar un mayor riesgo de que se utilicen insecticidas peligrosos",
   "tales como Paratión, Aldicarb y Metamidofos",
   "los productos de poliéster pueden entrañar un mayor riesgo de contribuir a las emisiones de GEI"])

q("B43", "sectorial", "¿Cuáles son los cinco pasos de la guía de la OCDE para minerales de zonas de conflicto?",
  [(OCDE_MINERALES, 6, None)],
  ["Paso 1: establecer sistemas robustos de gestión empresarial",
   "Paso 2: identificar y evaluar riesgos dentro de la cadena de suministro",
   "Paso 3: diseñar e implementar una estrategia para responder a los riesgos identificados",
   "Paso 4: llevar a cabo auditorías de terceros independientes de las prácticas de debida diligencia de los fundidores y refinadores",
   "Paso 5: informar sobre la debida diligencia de la cadena de suministros"])

q("B44", "sectorial", "¿Qué se considera una zona de conflicto o de alto riesgo según la guía de la OCDE sobre minerales?",
  [(OCDE_MINERALES, 13, None)],
  ["se identifican por la presencia de conflictos armados, la violencia generalizada u otros riesgos que puedan causar daño a la gente",
   "los conflictos armados pueden ser de carácter internacional o no internacional, guerras de liberación, insurgencias, guerras civiles"])

q("B45", "sectorial", "¿Qué temas recoge el estándar sectorial GRI 14 de minería?",
  [(GRI14, 3, None), (GRI14, 16, None)],
  ["cierre y rehabilitación",
   "derechos sobre la tierra y los recursos",
   "minería artesanal y a pequeña escala",
   "áreas de conflicto y de alto riesgo"])

q("B46", "sectorial", "Somos un banco que concede préstamos a empresas: ¿qué recomienda la OCDE para vigilar nuestra cartera?",
  [(OCDE_BANCA, 43, None)],
  ["la debida diligencia es un proceso continuo",
   "los bancos deberían asegurarse de que conocen los problemas de CER reales o potenciales de sus carteras de préstamo",
   "definir la frecuencia y el alcance de las revisiones periódicas de las actividades de sus clientes",
   "recurrir a servicios de terceras partes que faciliten alertas sobre controversias importantes"])

# ======================================================================================
# 10. Cruce entre marcos (3)
# ======================================================================================
q("B47", "cruce", "Cómo puedo identificar y cuantificar mis emisiones?",
  [(GRI305, 9, "305-1"), (NEIS, 91, "E1-6")],
  ["emisiones de GEI brutas de alcance 1, alcance 2 y alcance 3",
   "en toneladas métricas de CO2 equivalente",
   "gases incluidos en el cálculo y año base"],
  origen="real")

q("B48", "cruce", "¿Cómo se relaciona la prohibición del trabajo infantil de la CSDDD con lo que pide informar el GRI 408?",
  [(CSDDD, 47, "anexo I"), (GRI408, 4, None), (GRI408, 8, "408-1")],
  ["el anexo de la CSDDD cita el Convenio de la Organización Internacional del Trabajo sobre la edad mínima, 1973 (n.º 138)",
   "y a la prohibición de las peores formas de trabajo infantil del Convenio n.º 182",
   "GRI 408 toma la definición de trabajo infantil del Convenio n.º 138 de la OIT",
   "GRI 408-1 pide informar de operaciones y proveedores con riesgo significativo de trabajo infantil"])

q("B49", "cruce", "Si ya presento el informe de sostenibilidad de la CSRD, ¿tengo que publicar además la declaración anual de la CSDDD?",
  [(CSDDD, 26, "art. 16")],
  ["el apartado 1 no se aplicará a empresas sujetas a requisitos de información en materia de sostenibilidad",
   "de conformidad con los artículos 19 bis, 29 bis o 40 bis de la Directiva 2013/34/UE"])

# ======================================================================================
# 11. Preguntas reales de usuarios (4) — literales de BigQuery
# ======================================================================================
q("B50", "real",
  "Somos una pyme española que importa ropa laboral y uniformes desde Bangladesh, Vietnam, Turquía, Pakistán y Marruecos. Nuestros clientes nos están exigiendo garantías sobre derechos laborales, trazabilidad y sostenibilidad. ¿Qué normas europeas o españolas pueden afectarnos directa o indirectamente?",
  [(CSDDD, 3, "art. 2"), (AN_CSRD, 3, None), (OCDE_TEXTIL, 47, None)],
  ["la CSDDD se aplica a empresas de más de 5 000 empleados y más de 1 500 millones de volumen de negocios, así que no directamente a una pyme",
   "se protege a las empresas de la cadena de valor con menos de 1.000 empleados frente a solicitudes superiores al estándar voluntario",
   "la guía de la OCDE para el sector textil y del calzado recoge los riesgos del sector"],
  origen="real", nota="La parte de normas españolas no está en el corpus: debe decirlo, no inventarla.")

q("B51", "real",
  "¿Qué riesgos sociales, laborales y ambientales deberíamos priorizar en una empresa que importa productos textiles de terceros países? Necesitamos saber qué riesgos son más relevantes en nuestra cadena de suministro.",
  [(OCDE_TEXTIL, 47, None), (CSDDD, 16, "art. 9")],
  ["riesgos sectoriales: trabajo infantil, trabajo forzoso, discriminación, seguridad y salud en el trabajo",
   "químicos peligrosos, consumo de agua, contaminación del agua, emisiones de GEI",
   "salarios insuficientes para cubrir las necesidades básicas de los trabajadores y de sus familias",
   "la priorización se basa en la gravedad y la probabilidad de los efectos"],
  origen="real")

q("B52", "real",
  "mi pregunta es la siguiente existe en la directiva actual de diligencia debida una prevision para las empresas que suministran armas que pueden lesionar derechos humanos y el medio ambiente",
  [(CSDDD, 9, "art. 3")],
  ["la cadena de actividades excluye la distribución, el transporte y el almacenamiento de un producto sujeto a controles de las exportaciones",
   "con arreglo al Reglamento (UE) 2021/821 o a controles relacionados con armas, municiones o materiales de guerra",
   "tras la autorización de la exportación del producto"],
  origen="real")

q("B53", "real",
  "He detectado que tengo proveedores de melones de diferentes partes de marruecos y Argelia, por lo que quiero realizar un análisis de riesgos. Cómo puedo hacerlo?",
  [(OCDE_AGRI, 5, None), (CSDDD, 15, "art. 8")],
  ["la guía OCDE-FAO para cadenas de suministro agrícola cubre derechos humanos, derechos laborales, salud y seguridad",
   "derechos de tenencia y acceso a recursos naturales, protección ambiental y uso sostenible de los recursos naturales",
   "un ejercicio exploratorio sobre la base de la información razonablemente disponible para determinar dónde es más probable que se produzcan efectos adversos"],
  origen="real")

# ======================================================================================
# 12. Comprobaciones del modo auditor (4) — la respuesta del usuario convertida en
#     pregunta de verificación, como hace _verify_compliance
# ======================================================================================
q("B54", "auditor",
  "Una empresa sujeta a la CSDDD no tiene ningún canal para que los trabajadores de sus proveedores o sus sindicatos presenten quejas. ¿Cumple con la normativa?",
  [(CSDDD, 24, "art. 14")],
  ["las empresas deben permitir presentar reclamaciones cuando existan inquietudes legítimas sobre efectos adversos en su cadena de actividades",
   "los sindicatos y otros representantes de los trabajadores que trabajen en la cadena de actividades",
   "y las personas afectadas y las organizaciones de la sociedad civil"],
  modo="auditor", veredicto="no cumple")

q("B55", "auditor",
  "Una empresa sujeta a la CSDDD revisa la eficacia de sus medidas de diligencia debida cada siete años, salvo que haya cambios. ¿Cumple con la normativa?",
  [(CSDDD, 26, "art. 15")],
  ["las evaluaciones periódicas se llevarán a cabo al menos cada cinco años",
   "y sin demora injustificada cuando tenga lugar un cambio significativo"],
  modo="auditor", veredicto="no cumple")

q("B56", "auditor",
  "La política de diligencia debida de una empresa sujeta a la CSDDD la redactó la dirección sin consultar a la plantilla ni a sus representantes. ¿Cumple con la normativa?",
  [(CSDDD, 14, "art. 7")],
  ["la política de diligencia debida se elaborará previa consulta a los empleados de la empresa y sus representantes"],
  modo="auditor", veredicto="no cumple")

q("B57", "auditor",
  "Una empresa sujeta a la CSDDD, que no está sujeta a la CSRD, publica su declaración anual de diligencia debida en su web 18 meses después del cierre del ejercicio. ¿Cumple con la normativa?",
  [(CSDDD, 26, "art. 16")],
  ["la declaración anual se publicará no más tarde de doce meses a partir de la fecha de cierre del balance"],
  modo="auditor", veredicto="no cumple")

# ======================================================================================
# 13. Trampas y fuera de corpus (3)
# ======================================================================================
q("B58", "trampa", "¿Qué sanción fija el artículo 52 de la CSDDD?",
  [(CSDDD, 36, "art. 27")],
  ["la CSDDD no tiene artículo 52: su último artículo es el 39",
   "las sanciones están reguladas en el artículo 27"],
  debe_rechazar=True, nota="Premisa falsa: debe corregirla, no inventar el contenido.")

q("B59", "trampa", "¿Qué significa ZORROVIOLETA42?",
  [], ["debe decir que no dispone de información sobre ese término"],
  debe_rechazar=True, origen="real")

q("B60", "trampa", "¿Qué obligaciones impone la Ley española 11/2018 de información no financiera?",
  [], ["debe indicar que esa ley no está en su base documental y no inventar su contenido"],
  debe_rechazar=True, nota="La Ley 11/2018 no aparece en ningún documento del corpus (comprobado).")


# ======================================================================================
# Verificación y salida
# ======================================================================================
_STOP = set("""a al ante bajo con contra de del desde durante en entre hacia hasta mediante para por
según sin sobre tras el la los las lo un una unos unas y o u e ni que se su sus como más menos
cuando este esta estos estas ese esa dicho dicha dichos dichas cual cuales si no ya es son ser
debe deben será serán haya hayan""".split())


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s)


def _words(s: str) -> list[str]:
    return [w for w in _fold(s).split() if len(w) > 2 and w not in _STOP]


def verify(txt_dir: Path, threshold: float = 0.8) -> int:
    """Cada hecho debe estar (≥ threshold de sus palabras con contenido) en alguna de las
    páginas declaradas ±1. Los hechos de las preguntas trampa sin fuentes no se verifican."""
    cache: dict[str, dict[int, str]] = {}

    def pages(doc: str) -> dict[int, str]:
        if doc not in cache:
            f = txt_dir / (doc.replace("/", "__") + ".txt")
            if not f.exists():
                raise SystemExit(f"Falta el texto de {doc} en {txt_dir}")
            t = f.read_text(encoding="utf-8")
            parts = re.split(r"=== p\.(\d+) ===", t)
            cache[doc] = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
        return cache[doc]

    fails = 0
    for it in ITEMS:
        if not it["fuentes_esperadas"] or it["debe_rechazar"]:
            continue   # las trampas llevan instrucciones al juez, no hechos del corpus
        window = []
        for f in it["fuentes_esperadas"]:
            pg = pages(f["documento"])
            for p in (f["pagina"] - 1, f["pagina"], f["pagina"] + 1):
                window.append(pg.get(p, ""))
        bag = set(_fold(" ".join(window)).split())
        for h in it["hechos_clave"]:
            ws = _words(h)
            cov = sum(1 for w in ws if w in bag) / max(1, len(ws))
            if cov < threshold:
                fails += 1
                missing = [w for w in ws if w not in bag]
                print(f"  ✗ {it['id']} ({cov:.0%}) {h[:80]}…  faltan: {missing[:8]}")
    return fails


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", metavar="TXT_DIR", help="caché de texto de scripts/extract_corpus_text.py")
    ap.add_argument("--out", default=str(HERE / "bateria_v1.jsonl"))
    a = ap.parse_args()

    ids = [it["id"] for it in ITEMS]
    assert len(ids) == len(set(ids)), "ids duplicados"
    counts = Counter(it["tipo"] for it in ITEMS)
    print(f"{len(ITEMS)} preguntas · " + " · ".join(f"{TIPOS[k]} {v}" for k, v in counts.items()))

    if a.verify:
        fails = verify(Path(a.verify))
        n_h = sum(len(it["hechos_clave"]) for it in ITEMS if it["fuentes_esperadas"] and not it["debe_rechazar"])
        print(f"verificación: {n_h - fails}/{n_h} hechos clave localizados en sus páginas")
        if fails:
            sys.exit(1)

    with open(a.out, "w", encoding="utf-8") as fh:
        for it in ITEMS:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"escrito {a.out}")


if __name__ == "__main__":
    main()
