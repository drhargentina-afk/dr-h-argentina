"""Consultor Contencioso Administrativo y Tributario: Prescripción de Multas/Impuestos y Ley 19.549 (`07_contencioso_tributario`).

Incluye:
- Calculadora Determinista de Prescripción de Multas de Tránsito (Ley Nacional de Tránsito 24.449
  Art. 89: 2 años faltas leves y 5 años faltas graves) e Impuestos Nacionales/Provinciales
  (Ley 11.683 Art. 56 y Art. 2560 CCyCN: 5 años para contribuyentes inscriptos y tributos locales).
- Guía de plazos para impugnar actos administrativos (Ley 19.549 reformada por Ley 27.742:
  Recurso de Reconsideración 20 días hábiles, Recurso Jerárquico 30 días hábiles y Plazo de Demanda Judicial Art. 25).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoContencioso(BaseRepositoryConsultant):
    """Consultor normativo y calculadora de prescripción tributaria/administrativa (`07_contencioso_tributario`)."""

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.CONTENCIOSO_TRIBUTARIO

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Ley Nacional de Procedimientos Administrativos N° 19.549 (con las reformas de la Ley 27.742: ampliación de plazos de recursos, silencio positivo Art. 10 y eliminación del pago previo 'solve et repete' desproporcionado)",
            "Decreto Reglamentario 1759/72 (t.o. 2017 y reformas 2024 - Recursos de Reconsideración y Jerárquico)",
            "Ley de Procedimiento Tributario N° 11.683 (Arts. 56 y 92 - Prescripción de 5 años y Juicio de Ejecución Fiscal ARCA/AFIP)",
            "Ley Nacional de Tránsito N° 24.449 - Art. 89 (Prescripción de Multas de Tránsito: 2 años faltas leves y 5 años faltas graves) y Art. 70 (Garantías del procedimiento contravencional)",
            "Doctrina CSJN 'Filcrosa S.A.' y Art. 2560 CCyCN (Tope de 5 años para prescripción de tributos provinciales y tasas municipales)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "multa de tránsito|multa de transito|fotomulta|registro de conducir|infracción vieja": {
                "situation": "Fotomultas o infracciones de tránsito antiguas o notificadas sin cumplir recaudos legales",
                "law_article": "Arts. 70, 71 y 89 de la Ley Nacional de Tránsito N° 24.449",
                "explanation": (
                    "Por el Art. 89 de la Ley 24.449, las faltas leves prescriben a los 2 años y las faltas graves a "
                    "los 5 años. Además, si vivís a más de 60 km del juzgado de faltas, tenés derecho (Art. 71) a "
                    "presentar tu descargo por carta documento/correo o tramitarlo en el juzgado de tu propio domicilio."
                ),
                "protection_tip": (
                    "Si te exigen pagar multas prescriptas o sin sentencia firme como condición para renovar el "
                    "registro de conducir, los tribunales lo declaran inconstitucional (podés presentar descargo de prescripción)."
                ),
                "mirror_topic": "multas_y_prescripcion_tributaria",
            },
            "ejecución fiscal|ejecucion fiscal|arca|afip|arba|agip|boleta de deuda|embargo cuenta afip": {
                "situation": "Intimación o Juicio de Ejecución Fiscal de ARCA (ex AFIP), ARBA, AGIP o Municipalidad",
                "law_article": "Arts. 56 y 92 Ley 11.683 y Doctrina CSJN 'Administración Federal de Ingresos Públicos c/ Intercorp S.R.L.'",
                "explanation": (
                    "Desde el fallo 'Intercorp' de la Corte Suprema, los agentes fiscales NO pueden trabar embargos "
                    "bancarios por sí solos sin orden firmada por un Juez. Al recibir el mandamiento de intimación "
                    "de pago tenés 5 días hábiles para oponer excepciones (pago total documentado, espera, prescripción de 5 años o inhabilidad de boleta de deuda)."
                ),
                "protection_tip": (
                    "Revisá siempre los períodos fiscales reclamados en la Boleta de Deuda: si tienen más de 5 años "
                    "(contados desde el 1° de enero del año siguiente al vencimiento), oponé Excepción de Prescripción."
                ),
                "mirror_topic": "multas_y_prescripcion_tributaria",
            },
            "acto administrativo|clausura|sumario|empleo público|recurso jerárquico|rechazo administrativo": {
                "situation": "Impugnación de una resolución, sanción, clausura o acto del Estado",
                "law_article": "Arts. 7, 14 y 25 Ley 19.549 (reformada por Ley 27.742) y Decreto 1759/72",
                "explanation": (
                    "Con la reforma de la Ley 19.549 se ampliaron los plazos a favor del ciudadano: el Recurso "
                    "Jerárquico ahora cuenta con 30 días hábiles administrativos y el plazo para iniciar demanda "
                    "judicial contenciosa (Art. 25) es de 180 días hábiles judiciales."
                ),
                "protection_tip": (
                    "Pedí siempre 'Vista de las Actuaciones Administrativas': la presentación del pedido de vista "
                    "suspende automáticamente el curso de los plazos para recurrir (Art. 76 Decreto 1759/72)."
                ),
                "mirror_topic": "impugnacion_acto_estatal",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "multas_y_prescripcion_tributaria": RealCourtMirrorInfo(
                topic_key="multas_y_prescripcion_tributaria",
                how_courts_actually_rule=(
                    "Los Juzgados Contencioso Administrativos y Federales declaran la prescripción quinquenal (5 años) "
                    "de impuestos y tasas municipales aplicando la doctrina de la Corte Suprema ('Filcrosa' y 'Alpha Shipping') "
                    "y el plazo de 2/5 años para multas de tránsito cuando no existió secuela de juicio notificada."
                ),
                leading_cases=[
                    "CSJN, 'Filcrosa S.A. s/ quiebra' (Fallos 326:3899 - Plazo máximo de 5 años para tributos locales)",
                    "CSJN, 'AFIP c/ Intercorp S.R.L.' (Fallos 333:935 - Inconstitucionalidad de embargos administrativos sin orden judicial)",
                ],
                average_duration_months_range=(3, 12),
                anti_lie_warning_for_citizen=(
                    "No pagues a gestores informales que prometen 'borrar multas del sistema por izquierda'; el descargo "
                    "de prescripción por Art. 89 Ley 24.449 es un derecho gratuito que se presenta por escrito."
                ),
            ),
            "impugnacion_acto_estatal": RealCourtMirrorInfo(
                topic_key="impugnacion_acto_estatal",
                how_courts_actually_rule=(
                    "Los tribunales contenciosos analizan si el acto estatal cumplió los requisitos esenciales del Art. 7 "
                    "de la Ley 19.549 (competencia, causa, motivación suficiente y dictamen jurídico previo) y otorgan "
                    "medidas cautelares de suspensión de efectos (Art. 13 Ley 26.854) cuando el vicio es manifiesto."
                ),
                leading_cases=[
                    "CSJN, 'Los Lagos S.A. Ganadera' y 'Pustelnik' (Nulidad absoluta de actos administrativos irregulares)",
                ],
                average_duration_months_range=(8, 24),
                anti_lie_warning_for_citizen=(
                    "Recordá que pedir 'Vista del Expediente Administrativo' por escrito frena el reloj del plazo "
                    "que tenés para presentar el recurso."
                ),
            ),
        }

    @staticmethod
    def check_tax_or_fine_prescription(
        infraction_or_tax_due_date: date,
        obligation_type: str = "MULTA_TRANSITO_GRAVE",
        reference_today: date | None = None,
    ) -> dict[str, object]:
        """Calcula determinísticamente la prescripción de multas de tránsito o tributos.

        Reglas legales:
        - `MULTA_TRANSITO_LEVE`: 2 años (730 días corridos, Art. 89 Ley 24.449)
        - `MULTA_TRANSITO_GRAVE`: 5 años (1825 días corridos, Art. 89 Ley 24.449)
        - `IMPUESTO_NACIONAL_O_LOCAL`: 5 años computados desde el 1° de enero del año siguiente al vencimiento
          (Art. 56 y 57 Ley 11.683 / Art. 2560 CCyCN).
        """
        today = reference_today or date.today()
        if infraction_or_tax_due_date > today:
            raise ValueError("La fecha de la infracción o vencimiento fiscal no puede ser futura.")

        otype = obligation_type.upper().strip()
        if otype == "MULTA_TRANSITO_LEVE":
            start_count_date = infraction_or_tax_due_date
            limit_years = 2
            law_ref = "Art. 89 inc. a Ley Nacional de Tránsito 24.449 (2 años faltas leves)"
        elif otype == "MULTA_TRANSITO_GRAVE":
            start_count_date = infraction_or_tax_due_date
            limit_years = 5
            law_ref = "Art. 89 inc. b Ley Nacional de Tránsito 24.449 (5 años faltas graves)"
        else:
            # En impuestos (Art. 57 Ley 11.683), el cómputo de 5 años inicia el 1 de enero del año siguiente al vencimiento
            otype = "IMPUESTO_NACIONAL_O_LOCAL"
            start_count_date = date(infraction_or_tax_due_date.year + 1, 1, 1)
            limit_years = 5
            law_ref = "Arts. 56 y 57 Ley 11.683 y Art. 2560 CCyCN (5 años desde el 1° de enero siguiente)"

        expiration_date = date(
            start_count_date.year + limit_years,
            start_count_date.month,
            min(start_count_date.day, 28) if start_count_date.month == 2 else start_count_date.day,
        )
        is_prescribed = today >= expiration_date
        elapsed_days = max(0, (today - infraction_or_tax_due_date).days)
        elapsed_years = (Decimal(elapsed_days) / Decimal("365")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "obligation_type": otype,
            "origin_date": infraction_or_tax_due_date.isoformat(),
            "computation_start_date": start_count_date.isoformat(),
            "prescription_expiration_date": expiration_date.isoformat(),
            "elapsed_years": elapsed_years,
            "is_prescribed": is_prescribed,
            "applicable_law": law_ref,
            "citizen_verdict": (
                f"OBLIGACIÓN PRESCRIPTA ({law_ref}): Venció el {expiration_date.isoformat()}. Podés presentar descargo de prescripción."
                if is_prescribed
                else f"OBLIGACIÓN VIGENTE ({law_ref}): Prescribe el {expiration_date.isoformat()} (salvo acto interruptivo válido)."
            ),
        }
