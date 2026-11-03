"""Consultor de Repositorios, Calculadora Art. 245 LCT y Baremos ART (`03_laboral`).

Incluye:
- Calculadora Determinista con `Decimal` de Indemnización por Despido sin Causa:
  * Art. 245 LCT (1 sueldo por año de servicio o fracción mayor de 3 meses, tomando la mejor
    remuneración mensual, normal y habitual devengada y controlando el fallo 'Vizzotti' CSJN).
  * Art. 232 LCT (Indemnización sustitutiva de preaviso: 1 mes si antigüedad <= 5 años, 2 meses si > 5 años + SAC).
  * Art. 233 LCT (Integración del mes de despido + SAC cuando el despido no coincide con el último día del mes).
  * Comparador contra "Acuerdo a la Baja" (evalúa qué porcentaje real de la liquidación legal representa la oferta).
- Calculadora Orientativa de Indemnización por Incapacidad Laboral ART (Fórmula Art. 14 inc. 2.a Ley 24.557 +
  20% adicional Art. 3 Ley 26.773 para accidentes en ocasión del trabajo).
- Generador de Modelos de Telegrama Obrero 100% Gratuito (Ley 23.789 - Correo Argentino).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoLaboral(BaseRepositoryConsultant):
    """Consultor normativo, jurisprudencial y calculadora LCT/ART del Fuero Laboral (`03_laboral`)."""

    _SMVM_CURRENT_ARS: Decimal = Decimal("310000.00")
    _RIPTE_COEFFICIENT: Decimal = Decimal("1.0000")

    @classmethod
    def update_laboral_parameters(
        cls,
        smvm_ars: Decimal | None = None,
        ripte_coeff: Decimal | None = None,
    ) -> dict[str, Decimal]:
        """Actualiza en caliente las constantes salariales y de actualización del Fuero Laboral."""
        if smvm_ars is not None:
            val_smvm = Decimal(str(smvm_ars)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if val_smvm <= Decimal("0.00"):
                raise ValueError("El SMVM debe ser mayor a cero.")
            cls._SMVM_CURRENT_ARS = val_smvm
        if ripte_coeff is not None:
            val_ripte = Decimal(str(ripte_coeff)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if val_ripte <= Decimal("0.0000"):
                raise ValueError("El coeficiente RIPTE debe ser mayor a cero.")
            cls._RIPTE_COEFFICIENT = val_ripte
        return {
            "smvm_current_ars": cls._SMVM_CURRENT_ARS,
            "ripte_coefficient": cls._RIPTE_COEFFICIENT,
        }

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.LABORAL

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Constitución Nacional Art. 14 bis (Protección contra el Despido Arbitrario y Condiciones Dignas de Labor)",
            "Ley de Contrato de Trabajo N° 20.744 - Art. 20 (Beneficio de Gratuidad Absoluta e Inafectabilidad de la Vivienda)",
            "Ley de Contrato de Trabajo N° 20.744 - Arts. 231, 232, 233 y 245 (Preaviso, Integración e Indemnización por Antigüedad)",
            "Ley 23.789 de Telegrama y Carta Documento Gratuita para el Trabajador (Correo Oficial)",
            "Ley de Riesgos del Trabajo N° 24.557, Ley 26.773 y Ley 27.348 (Comisiones Médicas SRT y Baremo Decreto 659/96)",
            "Ley 27.742 (Reforma Laboral - Vigente desde 08/07/2024: derogación de multas Ley 24.013/25.323 y nuevo régimen de registración)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "me echaron|me despidieron|no me dejan entrar|despido verbal|en negro": {
                "situation": "Despido verbal, negativa de tareas o trabajo no registrado",
                "law_article": "Arts. 57, 242, 243 y 245 Ley 20.744 + Ley 23.789 (Telegrama Gratuito)",
                "explanation": (
                    "El despido verbal NO tiene validez legal. Si te dicen de palabra 'no vengas más' y dejás de ir "
                    "sin mandar un Telegrama Laboral Gratuito intimando a que aclaren tu situación laboral en 48 hs, "
                    "el empleador puede acusarte de 'abandono de trabajo' (Art. 244 LCT) para no pagarte indemnización."
                ),
                "protection_tip": (
                    "NUNCA firmes un formulario de renuncia en el Correo ni hojas en blanco. Acercate con tu DNI al "
                    "Correo Argentino y enviá gratis un Telegrama Ley 23.789 intimando dación de tareas."
                ),
                "mirror_topic": "despido_e_indemnizacion_245",
            },
            "acuerdo|seclo|me ofrecen|arreglo|quieren que firme por menos": {
                "situation": "Propuesta de acuerdo conciliatorio en SECLO o Ministerio de Trabajo",
                "law_article": "Art. 15 y Art. 277 Ley 20.744 (Irrenunciabilidad y Pago en Juicio en Cuenta del Trabajador)",
                "explanation": (
                    "En una conciliación laboral rápida (SECLO) es habitual negociar una quita moderada por pago inmediato, "
                    "pero el acuerdo requiere homologación fundada (Art. 15 LCT). Además, los honorarios de tu abogado "
                    "por pacto de cuota litis no pueden superar el 20% y la empresa suele abonar aparte el 10%-20% de honorarios letrados."
                ),
                "protection_tip": (
                    "Usá la Calculadora Art. 245 de Dr. H antes de ir a la audiencia para saber cuánto es tu 100% real "
                    "y exigí que el pago se deposite directamente en tu cuenta bancaria (CBU propio, Art. 277 LCT)."
                ),
                "mirror_topic": "despido_e_indemnizacion_245",
            },
            "accidente de trabajo|art|in itinere|comisión médica|incapacidad": {
                "situation": "Accidente laboral (en el trabajo o yendo/volviendo) o enfermedad profesional rechazada por la ART",
                "law_article": "Art. 6 y 14 Ley 24.557, Art. 3 Ley 26.773 y Ley 27.348 (Comisiones Médicas SRT)",
                "explanation": (
                    "La ART debe cubrir al 100% la atención médica, traslados, prótesis y salarios caídos (ILT). "
                    "Si te dan el alta con secuelas, la Comisión Médica de la SRT debe fijar el porcentaje de incapacidad "
                    "según el Baremo Oficial (Decreto 659/96); si te dan 0% o menos de lo real, es apelable ante la Justicia."
                ),
                "protection_tip": (
                    "No aceptes el alta médica sin dejar asentada tu disconformidad y realizarte estudios particulares "
                    "(resonancia/electromiograma) para contrastar el porcentaje del perito de la ART."
                ),
                "mirror_topic": "reclamo_art_incapacidad",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "despido_e_indemnizacion_245": RealCourtMirrorInfo(
                topic_key="despido_e_indemnizacion_245",
                how_courts_actually_rule=(
                    "En etapa pre-judicial (SECLO en CABA o Ministerio de Trabajo en Provincias) los acuerdos se cierran "
                    "en 30 a 60 días habitualmente entre el 65% y el 85% del capital puro del Art. 245 + preaviso. "
                    "Si va a juicio laboral completo, los tribunales aplican el fallo 'Vizzotti' (el tope de convenio no "
                    "puede reducir más del 33% del mejor sueldo bruto) y condenan con actualización e intereses."
                ),
                leading_cases=[
                    "CSJN, 'Vizzotti, Carlos Alberto c/ AMSA S.A.' (Fallos 327:3677 - Límite del 33% al tope del Art. 245 LCT)",
                    "CSJN, 'Aquino, Isacio c/ Cargo Servicios Industriales S.A.' (Reparación integral e indemnización justa)",
                ],
                average_duration_months_range=(18, 36),
                anti_lie_warning_for_citizen=(
                    "Si te ofrecen un acuerdo inmediato por el 25% o 30% de tu liquidación diciendo que 'si no firmás hoy "
                    "no cobrás nunca', están presionándote a la baja. Tampoco debés pagar tasa de justicia (Art. 20 LCT)."
                ),
            ),
            "reclamo_art_incapacidad": RealCourtMirrorInfo(
                topic_key="reclamo_art_incapacidad",
                how_courts_actually_rule=(
                    "El trámite ante la Comisión Médica SRT dura entre 60 y 90 días hábiles. Si se apela a la Justicia "
                    "Laboral, el juez designa un Perito Médico Oficial imparcial que evalúa las secuelas físicas y psicológicas "
                    "según el Baremo Decreto 659/96, reconociendo habitualmente puntos de incapacidad que la ART omitió."
                ),
                leading_cases=[
                    "CSJN, 'Pogonza, Jonathan Jesús c/ Galeno ART S.A.' (Validez del paso previo por Comisiones Médicas con revisión judicial amplia)",
                    "CSJN, 'Espósito, Dardo Luis c/ Provincia ART S.A.' (Aplicación del índice RIPTE a las prestaciones de ley)",
                ],
                average_duration_months_range=(3, 18),
                anti_lie_warning_for_citizen=(
                    "El patrocinio jurídico ante las Comisiones Médicas es obligatorio y los honorarios del abogado ante "
                    "la SRT en acuerdos homologados están regulados a cargo de la ART."
                ),
            ),
        }

    @staticmethod
    def calculate_indemnizacion_art245(
        best_monthly_gross_salary_ars: Decimal,
        years_of_service: int,
        months_fraction: int = 0,
        remaining_days_in_dismissal_month: int = 0,
        offered_settlement_ars: Decimal | None = None,
    ) -> dict[str, object]:
        """Calcula con exactitud Decimal la liquidación por despido incausado (Arts. 245, 232 y 233 LCT).

        Criterio matemático:
        - Antigüedad computable Art. 245: 1 período por cada año o fracción mayor de 3 meses (con piso mínimo de 1 sueldo).
        - Preaviso Art. 232: 1 sueldo si antigüedad <= 5 años; 2 sueldos si > 5 años (+ 8.33% de SAC s/ preaviso).
        - Integración mes de despido Art. 233: (Sueldo / 30) * días restantes del mes (+ SAC s/ integración).
        - Si se informa `offered_settlement_ars`, calcula el porcentaje exacto que representa y emite diagnóstico.
        """
        if not isinstance(best_monthly_gross_salary_ars, Decimal) or best_monthly_gross_salary_ars <= Decimal("0.00"):
            raise ValueError("best_monthly_gross_salary_ars debe ser un Decimal positivo.")
        if type(years_of_service) is not int or years_of_service < 0:
            raise ValueError("years_of_service debe ser un entero >= 0.")
        if type(months_fraction) is not int or not (0 <= months_fraction <= 11):
            raise ValueError("months_fraction debe ser un entero entre 0 y 11.")
        if type(remaining_days_in_dismissal_month) is not int or not (0 <= remaining_days_in_dismissal_month <= 30):
            raise ValueError("remaining_days_in_dismissal_month debe estar entre 0 y 30.")

        periods = years_of_service + (1 if months_fraction > 3 else 0)
        if periods == 0:
            periods = 1  # Piso legal Art. 245 LCT (1 mes de sueldo)

        salary = best_monthly_gross_salary_ars.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        indem_245 = (salary * Decimal(periods)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Preaviso Art. 232 + SAC sobre preaviso
        preaviso_months = 2 if (years_of_service > 5 or (years_of_service == 5 and months_fraction > 0)) else 1
        indem_preaviso = (salary * Decimal(preaviso_months)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        sac_preaviso = (indem_preaviso / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Integración mes de despido Art. 233 + SAC
        if remaining_days_in_dismissal_month > 0:
            integracion = ((salary / Decimal("30")) * Decimal(remaining_days_in_dismissal_month)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            sac_integracion = (integracion / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            integracion = Decimal("0.00")
            sac_integracion = Decimal("0.00")

        total_statutory = (indem_245 + indem_preaviso + sac_preaviso + integracion + sac_integracion).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Límite Vizzotti (67% de la remuneración real como piso contra cualquier tope convencional)
        vizzotti_floor_245 = (indem_245 * Decimal("0.67")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        offer_evaluation = "No se ingresó oferta de acuerdo para comparar."
        offer_ratio_pct: Decimal | None = None
        is_abusive_lowball = False

        if offered_settlement_ars is not None:
            if offered_settlement_ars < Decimal("0.00"):
                raise ValueError("offered_settlement_ars no puede ser negativo.")
            offer_ratio_pct = ((offered_settlement_ars * Decimal("100.00")) / total_statutory).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if offer_ratio_pct < Decimal("55.00"):
                is_abusive_lowball = True
                offer_evaluation = (
                    f"ALERTA DE ACUERDO A LA BAJA: La oferta (${offered_settlement_ars} ARS) equivale apenas al "
                    f"{offer_ratio_pct}% de tu liquidación legal por despido (${total_statutory} ARS). "
                    f"Está muy por debajo del estándar conciliatorio razonable (65% a 85%)."
                )
            else:
                offer_evaluation = (
                    f"La oferta (${offered_settlement_ars} ARS) representa el {offer_ratio_pct}% de la liquidación "
                    f"legal completa (${total_statutory} ARS)."
                )

        return {
            "computable_periods_art245": periods,
            "indemnizacion_art245_ars": indem_245,
            "vizzotti_constitutional_floor_art245_ars": vizzotti_floor_245,
            "indemnizacion_preaviso_art232_ars": indem_preaviso,
            "sac_sobre_preaviso_ars": sac_preaviso,
            "integracion_mes_despido_art233_ars": integracion,
            "sac_sobre_integracion_ars": sac_integracion,
            "total_liquidacion_despido_ars": total_statutory,
            "offered_settlement_ratio_pct": offer_ratio_pct,
            "is_abusive_lowball_offer": is_abusive_lowball,
            "offer_evaluation": offer_evaluation,
        }

    @staticmethod
    def calculate_baremo_art_incapacidad(
        ibm_salary_ars: Decimal,
        disability_pct: Decimal,
        worker_age: int,
        in_workplace_accident: bool = True,
    ) -> dict[str, object]:
        """Calcula la indemnización tarifada ART (Art. 14 inc. 2.a Ley 24.557 + Art. 3 Ley 26.773).

        Fórmula legal exacta:
        - Prestación Base = 53 * IBM * (Porcentaje Incapacidad / 100) * (65 / Edad del Trabajador)
        - Si ocurrió en el lugar de trabajo o en ocasión del trabajo (`in_workplace_accident=True`, no in itinere),
          se adiciona el +20% de indemnización de pago único del Art. 3 de la Ley 26.773.
        """
        if ibm_salary_ars <= Decimal("0.00"):
            raise ValueError("El Ingreso Base Mensual (IBM) debe ser mayor a cero.")
        if disability_pct <= Decimal("0.00") or disability_pct > Decimal("100.00"):
            raise ValueError("El porcentaje de incapacidad debe estar entre 0.01% y 100.00%.")
        if type(worker_age) is not int or worker_age < 16 or worker_age > 90:
            raise ValueError("La edad del trabajador debe ser un entero entre 16 y 90 años.")

        age_coeff = Decimal("65") / Decimal(worker_age)
        base_formula = (
            Decimal("53") * ibm_salary_ars * (disability_pct / Decimal("100.00")) * age_coeff
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        additional_20pct = (
            (base_formula * Decimal("0.20")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if in_workplace_accident
            else Decimal("0.00")
        )
        total_art = (base_formula + additional_20pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "formula_art14_base_ars": base_formula,
            "additional_20pct_art3_ley26773_ars": additional_20pct,
            "total_indemnizacion_art_ars": total_art,
            "legal_basis": "Art. 14 inc. 2.a Ley 24.557 + Art. 3 Ley 26.773 (Baremo Decreto 659/96)",
        }

    @staticmethod
    def get_telegrama_obrero_template(scenario: str = "NEGATIVA_TAREAS") -> dict[str, str]:
        """Devuelve modelos listos para completar en el formulario de Telegrama Ley 23.789 del Correo Argentino."""
        templates = {
            "NEGATIVA_TAREAS": (
                "Ante negativa verbal de trabajo producida el día [FECHA], intimo plazo dos (2) días hábiles "
                "aclare situación laboral y otorgue tareas efectivas en mi puesto y horario habitual, bajo "
                "apercibimiento de considerarme gravemente injuriado y despedido por su exclusiva culpa "
                "(Arts. 57, 242 y 246 Ley 20.744). Queda Ud. debidamente notificado."
            ),
            "RECLAMO_LIQUIDACION_DESPIDO": (
                "Habiendo sido notificado del despido sin justa causa, intimo plazo legal de cuatro (4) días hábiles "
                "(Art. 128 y 255 bis LCT) abone liquidación final, indemnización por antigüedad (Art. 245 LCT), "
                "preaviso (Art. 232 LCT) e integración del mes de despido (Art. 233 LCT), y haga entrega de los "
                "certificados del Art. 80 LCT, bajo apercibimiento de iniciar acciones judiciales."
            ),
        }
        chosen = templates.get(scenario.upper(), templates["NEGATIVA_TAREAS"])
        return {
            "law": "Ley 23.789 (Envío 100% Gratuito en Correo Argentino presentando DNI)",
            "scenario": scenario.upper(),
            "telegram_text": chosen,
        }
