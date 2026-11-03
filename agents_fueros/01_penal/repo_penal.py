"""Consultor de Repositorios del Fuero Penal (`01_penal`).

Incluye:
- Protocolo "Primeras 48hs de Detenido" (anti-estafas en comisaría, prohibición de declarar
  en sede policial sin abogado defensor, gratuidad absoluta del Defensor Oficial).
- Traductor de problema penal cotidiano ("criollo") a garantías constitucionales y procesales.
- Espejo de fallos de Casación Penal / CSJN ("Diaz Bessone", "Arriola", "Loyo Fraire") y
  tiempos reales de resolución de excarcelaciones e instrucción penal.
"""
from __future__ import annotations

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoPenal(BaseRepositoryConsultant):
    """Consultor normativo y jurisprudencial del Fuero Penal Argentino."""

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.PENAL

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Constitución Nacional Art. 18 (Principio de Inocencia, Defensa en Juicio e Inviolabilidad del Domicilio)",
            "Pacto de San José de Costa Rica Art. 7 y 8 (Garantías Judiciales y Plazo Razonable - Art. 75 inc. 22 CN)",
            "Código Penal de la Nación Argentina (Ley 11.179 t.o. vigente)",
            "Código Procesal Penal Federal (Ley 27.063 t.o. 2019 - Arts. 210, 221 y 222 sobre Medidas de Coerción)",
            "Código Procesal Penal de la Nación (Ley 23.984 - Arts. 280, 294 Indagatoria y 316/317 Excarcelación)",
            "Ley 27.149 del Ministerio Público de la Defensa (Asistencia Técnica Penal Pública y Gratuita)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "detenido|comisaría|comisaria|preso|demorado|declarar en la policía": {
                "situation": "Persona detenida o demorada en comisaría en las primeras 48 horas",
                "law_article": "Art. 18 CN, Art. 294 CPPN y Protocolo Primeras 48hs (Ley 27.149)",
                "explanation": (
                    "En Argentina NADIE está obligado a declarar en la comisaría ni frente a la policía. "
                    "Toda declaración indagatoria solo es válida ante el Juez o Fiscal y con la presencia "
                    "previa y entrevista privada con su abogado defensor (particular o Defensor Oficial gratuito)."
                ),
                "protection_tip": (
                    "ALERTA ANTI-ESTAFAS EN COMISARÍA: Desconfiá de cualquier 'abogado de pasillo' o intermediario "
                    "que pida dinero urgente en efectivo en la puerta de la comisaría prometiendo 'sacarlo en 2 horas'. "
                    "El control de detención y la asistencia del Defensor Oficial de turno son 100% GRATUITOS."
                ),
                "mirror_topic": "excarcelacion_primeras_48hs",
            },
            "allanamiento|entraron a mi casa|secuestro de celular|requisaron": {
                "situation": "Allanamiento domiciliario o requisa personal/vehicular",
                "law_article": "Art. 18 CN y Arts. 224 a 231 CPPN (Nulidad de actas sin orden fundada o testigos)",
                "explanation": (
                    "Un domicilio solo puede ser allanado con orden escrita y fundada de un Juez competente "
                    "(salvo flagrancia extrema) y en presencia de testigos civiles hábiles que no pertenezcan a la fuerza policial."
                ),
                "protection_tip": (
                    "Verificá en el acta de allanamiento la hora exacta, si firmaron dos testigos civiles ajenos a la policía "
                    "y la cadena de custodia de celulares o elementos secuestrados."
                ),
                "mirror_topic": "nulidades_acta_policial",
            },
            "excarcelación|excarcelacion|prisión preventiva|prision preventiva|tobillera": {
                "situation": "Pedido de libertad durante el proceso (Excarcelación o Morigeración)",
                "law_article": "Arts. 210, 221 y 222 CPPF / Plenario 'Díaz Bessone' Cámara Nac. de Casación Penal",
                "explanation": (
                    "La regla constitucional es transitar el proceso en libertad. La prisión preventiva solo procede "
                    "si existen pruebas concretas de peligro de fuga o entorpecimiento de la investigación, y luego de "
                    "descartar medidas menos gravosas (promesa, caución, prohibición de salida o arresto domiciliario)."
                ),
                "protection_tip": (
                    "Para fortalecer una excarcelación es clave acreditar arraigo real: certificado de domicilio, "
                    "constancia laboral o de estudios y grupo familiar conviviente."
                ),
                "mirror_topic": "excarcelacion_primeras_48hs",
            },
            "denuncia falsa|me denunciaron|causa armada|sobreseimiento": {
                "situation": "Imputación penal en etapa de instrucción e investigación fiscal",
                "law_article": "Arts. 73, 279 y 336 CPPN (Derecho a presentarse espontáneamente y Sobreseimiento)",
                "explanation": (
                    "Cualquier persona que tome conocimiento de una denuncia en su contra puede presentarse "
                    "espontáneamente en la fiscalía/juzgado, designar defensor, pedir vista de la causa y ofrecer descargo."
                ),
                "protection_tip": (
                    "Nunca contactes ni confrontes directamente al denunciante (puede interpretarse como entorpecimiento "
                    "o violación de restricción de acercamiento); canalizá toda prueba por escrito en el expediente."
                ),
                "mirror_topic": "instruccion_sobreseimiento",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "excarcelacion_primeras_48hs": RealCourtMirrorInfo(
                topic_key="excarcelacion_primeras_48hs",
                how_courts_actually_rule=(
                    "Los juzgados de garantías y de instrucción resuelven los pedidos de excarcelación dentro de las "
                    "24 a 72 horas desde su presentación formal (tras certificar antecedentes en el Registro Nacional "
                    "de Reincidencia). Aplican la doctrina del Plenario 'Díaz Bessone' y el catálogo gradual del Art. 210 CPPF."
                ),
                leading_cases=[
                    "Cámara Federal de Casación Penal, Plenario N° 13 'Díaz Bessone, Ramón Genaro' (30/10/2008)",
                    "CSJN, 'Loyo Fraire, Gabriel Eduardo' (Fallos 337:199 - Riesgo procesal concreto)",
                    "CSJN, 'Napoli, Erika Elizabeth' (Inconstitucionalidad de restricciones automáticas a la excarcelación)",
                ],
                average_duration_months_range=(1, 2),
                anti_lie_warning_for_citizen=(
                    "Ningún abogado puede garantizar por adelantado la libertad a cambio de 'pagos para el juzgado o la fiscalía'. "
                    "El trámite de excarcelación es estrictamente escrito y gratuito ante el tribunal."
                ),
            ),
            "nulidades_acta_policial": RealCourtMirrorInfo(
                topic_key="nulidades_acta_policial",
                how_courts_actually_rule=(
                    "Los tribunales anulan procedimientos policiales cuando los testigos de actuación fueron convocados "
                    "después del secuestro del material, cuando existen contradicciones horarias entre el parte preventivo "
                    "y el acta de secuestro, o cuando se requisó sin urgencia ni motivos objetivos previos."
                ),
                leading_cases=[
                    "CSJN, 'Daray, Carlos Ángel' (Fallos 317:1985 - Exigencia de motivación en detenciones)",
                    "CSJN, 'Fernández Prieto' y 'Waltta' (Estándar de sospecha razonable en requisas)",
                    "CSJN, 'Arriola, Sebastián' (Fallos 332:1963 - Tenencia para consumo personal y ámbito de privacidad)",
                ],
                average_duration_months_range=(3, 8),
                anti_lie_warning_for_citizen=(
                    "Los planteos de nulidad requieren comparar palabra por palabra el acta policial inicial (Fojas 1 a 10) "
                    "con las declaraciones de los testigos civiles."
                ),
            ),
            "instruccion_sobreseimiento": RealCourtMirrorInfo(
                topic_key="instruccion_sobreseimiento",
                how_courts_actually_rule=(
                    "La etapa de investigación penal preparatoria (IPP) o instrucción suele demorar entre 6 y 18 meses "
                    "dependiendo de pericias telefónicas, cámaras y testimoniales antes de dictar sobreseimiento o procesamiento."
                ),
                leading_cases=[
                    "CSJN, 'Mattei, Ángel' (Fallos 272:188 - Derecho a obtener un pronunciamiento rápido)",
                    "CSJN, 'Kipperband, Benjamín' (Prescripción y plazo razonable del proceso penal)",
                ],
                average_duration_months_range=(6, 18),
                anti_lie_warning_for_citizen=(
                    "En sede penal rige el impulso fiscal/de oficio; exigí ver el cargo electrónico de cada escrito "
                    "de proposición de diligencias presentado por tu defensa."
                ),
            ),
        }

    def get_48hs_detention_protocol(self, province: str = "PJN_NACION_CABA") -> dict[str, object]:
        """Devuelve el Protocolo Operativo 'Primeras 48hs de Detenido' para familiares y ciudadanos."""
        return {
            "protocol_title": "Protocolo Ciudadano de Urgencia: Primeras 48 Horas de Detención",
            "jurisdiction_applied": province,
            "golden_rules": [
                "1. NO DECLARAR EN COMISARÍA: El detenido tiene derecho constitucional (Art. 18 CN) a guardar silencio sin que el silencio implique culpabilidad.",
                "2. ENTREVISTA PRIVADA PREVIA: Antes de la audiencia indagatoria ante Juez o Fiscal, el detenido debe hablar a solas con su abogado o con el Defensor Oficial.",
                "3. DEFENSORÍA OFICIAL 100% GRATUITA: Todo ciudadano tiene asignado por turno un Defensor Público Oficial matriculado sin pagar un solo peso.",
                "4. CERO PAGOS EN COMISARÍA: Ni la policía, ni la fiscalía, ni el juzgado cobran dinero para otorgar la libertad o recibir una denuncia.",
                "5. REVISACIÓN MÉDICA OBLIGATORIA: Exigir que conste el informe del médico legista al momento de ingreso para resguardar la integridad física.",
            ],
            "documents_family_must_gather": [
                "Certificado de domicilio o facturas de servicios a nombre del detenido o conviviente (para acreditar arraigo).",
                "Recibo de sueldo, monotributo o constancia de trabajo/estudios.",
                "Partidas de nacimiento de hijos menores o historia clínica si requiere medicación urgente.",
            ],
        }
