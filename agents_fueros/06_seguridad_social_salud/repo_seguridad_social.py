"""Consultor de Seguridad Social y Salud: Fallos CSJN (Badaro, Blanco), PMO y Ley 24.901 (`06_seguridad_social_salud`).

Incluye:
- Espejo de fallos de la Corte Suprema para reajustes jubilatorios ANSES ("Badaro", "Elliff", "Blanco", "Sánchez").
- Buscador de cobertura obligatoria del PMO (Programa Médico Obligatorio) y Ley 24.901 de Discapacidad (100% integral).
- Generador de Nota de Intimación Administrativa Previa en 48 horas a Obras Sociales y Prepagas (requisito para Amparo de Salud).
"""
from __future__ import annotations

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoSeguridadSocial(BaseRepositoryConsultant):
    """Consultor normativo, previsional y de Amparos de Salud (`06_seguridad_social_salud`)."""

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.SEGURIDAD_SOCIAL_SALUD

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Constitución Nacional Art. 14 bis (Jubilaciones y Pensiones Móviles) y Art. 43 (Acción Expedita de Amparo de Salud)",
            "Ley 24.241 (Sistema Integrado Previsional Argentino - SIPA) y Ley 24.463 (Solidaridad Previsional - Exención de Tasa de Justicia)",
            "Ley 24.901 de Sistema de Prestaciones Básicas en Habilitación y Rehabilitación Integral a favor de las Personas con Discapacidad (Cobertura 100%)",
            "Ley 23.660 (Obras Sociales), Ley 26.682 (Marco Regulatorio de Medicina Prepaga) y Resolución 201/2002 MS (Programa Médico Obligatorio - PMO)",
            "Ley 16.986 de Acción de Amparo (Medidas Cautelares de Salud en 48 a 72 horas)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "obra social|prepaga|pami|ioma|no me cubre|medicación|medicacion|cirugía|discapacidad|cud": {
                "situation": "Negativa o demora de Obra Social, Prepaga o PAMI en cubrir medicamentos, cirugía, internación o prestaciones por Discapacidad (CUD)",
                "law_article": "Art. 43 CN, Ley 24.901 (Discapacidad 100%), Ley 26.682 y Resolución PMO 201/2002",
                "explanation": (
                    "Si contás con Certificado Único de Discapacidad (CUD), la Ley 24.901 obliga a cubrir el 100% de "
                    "tratamientos, medicación, acompañante terapéutico, transporte e integración escolar. Para el resto de "
                    "afiliados, el PMO (Programa Médico Obligatorio) garantiza cobertura del 100% en medicamentos oncológicos, "
                    "internación, prótesis nacionales y enfermedades crónicas graves."
                ),
                "protection_tip": (
                    "Presentá una nota de intimación escrita por duplicado (con sello de recepción) adjuntando la orden "
                    "médica que diga 'URGENTE'. Si en 48 hs no autorizan, se presenta un Amparo de Salud con medida cautelar."
                ),
                "mirror_topic": "amparo_salud_cautelar",
            },
            "jubilación|jubilacion|anses|reajuste de haberes|mal liquidado|badaro|blanco|pensión": {
                "situation": "Reclamo de reajuste de haberes jubilatorios o pensión mal calculada por ANSES",
                "law_article": "Art. 14 bis CN, Ley 24.241 y Doctrina CSJN ('Badaro', 'Elliff' y 'Blanco')",
                "explanation": (
                    "Cuando ANSES calcula el haber inicial usando índices desactualizados o aplica topes que confiscan "
                    "más del 15% del haber ('Actis Caporale'), el jubilado puede iniciar reclamo administrativo previo en "
                    "ANSES y luego demanda judicial de reajuste cobrando retroactivos desde 2 años hacia atrás del reclamo."
                ),
                "protection_tip": (
                    "Los juicios de reajuste previsional gozan de exención de tasa de justicia para el jubilado. Nunca "
                    "pagues sumas elevadas por adelantado sin antes realizar el cálculo técnico actuarial de si tu haber realmente da diferencia."
                ),
                "mirror_topic": "reajuste_previsional_anses",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "amparo_salud_cautelar": RealCourtMirrorInfo(
                topic_key="amparo_salud_cautelar",
                how_courts_actually_rule=(
                    "En Amparos de Salud con riesgo de vida, medicación oncológica, cirugía urgente o prestaciones bajo "
                    "CUD (Ley 24.901), los Juzgados Federales y Contenciosos dictan la Medida Cautelar ordenando la "
                    "entrega o cobertura al 100% en un plazo real de 48 a 96 horas hábiles bajo apercibimiento de astreintes (multas diarias) y embargo."
                ),
                leading_cases=[
                    "CSJN, 'Campodónico de Beviacqua c/ Ministerio de Salud' (Fallos 323:3229 - Derecho a la salud e infancia)",
                    "CSJN, 'Cambiaso Péres de Nealón c/ CEMIC' (Fallos 330:3725 - Obligaciones de empresas de medicina prepaga)",
                ],
                average_duration_months_range=(1, 6),
                anti_lie_warning_for_citizen=(
                    "Aunque la sentencia definitiva de un amparo demore unos meses, la Medida Cautelar que te entrega "
                    "el medicamento o autoriza la operación sale en los primeros 2 a 5 días hábiles."
                ),
            ),
            "reajuste_previsional_anses": RealCourtMirrorInfo(
                topic_key="reajuste_previsional_anses",
                how_courts_actually_rule=(
                    "La Justicia Federal de la Seguridad Social aplica estrictamente los precedentes de la Corte Suprema: "
                    "'Elliff' y 'Blanco' (actualización de remuneraciones históricas por índice ISBIC sin límite temporal) "
                    "y 'Badaro' para la movilidad entre 2002 y 2006."
                ),
                leading_cases=[
                    "CSJN, 'Badaro, Adolfo Valentín c/ ANSES' (Fallos 329:3089 y 330:4866 - Garantía de movilidad Art. 14 bis CN)",
                    "CSJN, 'Elliff, Alberto José c/ ANSES' (Fallos 332:1914 - Actualización de haberes iniciales)",
                    "CSJN, 'Blanco, Lucio Orlando c/ ANSES' (18/12/2018 - Ratificación del índice ISBIC frente al RIPTE)",
                    "CSJN, 'Actis Caporale, Loredano' (Fallos 323:4216 - Inconstitucionalidad de topes que quiten más del 15%)",
                ],
                average_duration_months_range=(24, 48),
                anti_lie_warning_for_citizen=(
                    "Antes de iniciar un juicio de reajuste es indispensable hacer el cálculo matemático previo: no todos "
                    "los beneficios tienen diferencia favorable según su fecha de adquisición del derecho."
                ),
            ),
        }

    @staticmethod
    def build_health_intimacion_48hs_note(
        health_provider_name: str,
        medical_prescription_detail: str,
        has_cud_disability: bool = False,
    ) -> dict[str, str]:
        """Genera la nota administrativa formal de intimación en 48 hs previa al Amparo de Salud."""
        provider = health_provider_name.strip() or "[NOMBRE DE OBRA SOCIAL / PREPAGA]"
        prescription = medical_prescription_detail.strip() or "[DETALLE DE MEDICACIÓN / TRATAMIENTO RECETADO]"
        law_invoked = (
            "Ley 24.901 de Discapacidad (Cobertura Integral 100%) y Programa Médico Obligatorio (PMO)"
            if has_cud_disability
            else "Programa Médico Obligatorio (Res. 201/2002 MS), Ley 23.660 / Ley 26.682 y Art. 42 y 43 CN"
        )
        note_body = (
            f"AL DIRECTOR MÉDICO / AUDITORÍA DE {provider.upper()}:\n"
            f"Por medio de la presente INTIMO en el plazo perentorio e improrrogable de CUARENTA Y OCHO (48) HORAS "
            f"proceda a autorizar y proveer con cobertura del 100% la prestación indicada por mi médico tratante: "
            f"'{prescription}', conforme orden médica que se adjunta, todo ello al amparo de la {law_invoked}. "
            f"Caso contrario, ante el grave riesgo para la salud y la vida que implica la demora, iniciaré de inmediato "
            f"Acción de Amparo Judicial con pedido de Medida Cautelar y denuncia ante la Superintendencia de Servicios de Salud."
        )
        return {
            "provider": provider,
            "legal_framework": law_invoked,
            "deadline_hours": "48",
            "intimacion_text": note_body,
        }
