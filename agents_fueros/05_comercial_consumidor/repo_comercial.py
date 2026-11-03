"""Consultor Comercial/Consumidor, Detector de Deudas Prescriptas y Tope de Embargo (`05_comercial_consumidor`).

Incluye:
- Detector Determinista de Deudas Prescriptas (Art. 2560 CCyCN — 5 años plazo genérico,
  Art. 2562 inc. c — 2 años deudas de consumo/servicios periódicos, Art. 47 Ley 25.065 —
  1 año acción ejecutiva y 3 años acción ordinaria en Tarjetas de Crédito, y 3 años pagaré
  Decreto-Ley 5965/63) diseñado para frenar hostigamiento de estudios de cobranza extrajudicial.
- Calculadora Exacta con `Decimal` del Tope Legal de Embargo de Sueldo (Decreto 484/87 reglamentario
  de los Arts. 120 y 147 LCT): inembargabilidad absoluta hasta 1 Salario Mínimo Vital y Móvil (SMVM),
  10% sobre el excedente si no supera 2 SMVM, y 20% sobre el excedente si supera 2 SMVM.
- Guía de reclamos ante Defensa del Consumidor / COPREC con beneficio de justicia gratuita (Art. 53 Ley 24.240).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoComercial(BaseRepositoryConsultant):
    """Consultor normativo, detector de prescripción y calculadora Decreto 484/87 (`05_comercial_consumidor`)."""

    _SMVM_CURRENT_ARS: Decimal = Decimal("310000.00")

    @classmethod
    def update_smvm_reference(cls, new_smvm_ars: Decimal) -> Decimal:
        """Actualiza en caliente el Salario Mínimo Vital y Móvil (SMVM) para topes de embargo Decreto 484/87."""
        val = Decimal(str(new_smvm_ars)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if val <= Decimal("0.00"):
            raise ValueError("El valor del SMVM debe ser mayor a cero.")
        cls._SMVM_CURRENT_ARS = val
        return cls._SMVM_CURRENT_ARS

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.COMERCIAL_CONSUMIDOR

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Constitución Nacional Art. 42 (Protección de Consumidores y Usuarios y Trato Digno)",
            "Ley de Defensa del Consumidor N° 24.240 - Art. 8 bis (Prohibición de Prácticas Abusivas y Cobranzas Vergonzantes), Art. 52 bis (Daño Punitivo) y Art. 53 (Beneficio de Justicia Gratuita)",
            "Código Civil y Comercial de la Nación - Arts. 2560 y 2562 (Plazos de Prescripción Liberatoria de Deudas: 5 años general y 2 años devengamientos periódicos)",
            "Ley de Tarjetas de Crédito N° 25.065 - Art. 47 (Prescripción de 1 año para vía ejecutiva y 3 años para vía ordinaria)",
            "Decreto 484/87 reglamentario de los Arts. 120 y 147 Ley 20.744 (Límite Legal de Embargo sobre Remuneraciones)",
            "Decreto-Ley 5965/63 (Pagaré de Consumo y doctrina plenaria sobre integración con contrato de consumo)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "estudio de cobranza|deuda vieja|me amenazan con embargar|veraz|tarjeta de crédito|fideicomiso": {
                "situation": "Hostigamiento de estudios de cobranza por deudas antiguas de tarjetas, préstamos o servicios",
                "law_article": "Art. 8 bis Ley 24.240, Art. 47 Ley 25.065 y Arts. 2560/2562 CCyCN + Art. 47 Ley 25.326 (Derecho al Olvido Veraz 5 años)",
                "explanation": (
                    "Los estudios de cobranza compran carteras de deudas prescriptas por centavos y llaman asustando con "
                    "'embargos inmediatos en tu domicilio'. Si la deuda de tarjeta lleva más de 3 años sin juicio iniciado "
                    "(o 5 años en préstamos comunes), está PRESCRIPTA. Además, ningún estudio privado puede embargar sin orden de un Juez."
                ),
                "protection_tip": (
                    "¡NO FIRMES REFINANCIACIONES NI PAGUES 'UN PESO PARA ENTRAR EN PLAN' EN UNA DEUDA PRESCRIPTA! "
                    "Si firmás un reconocimiento de deuda hoy, renunciás a la prescripción ganada. Además, llamar a tu "
                    "trabajo o vecinos viola el Art. 8 bis de la Ley 24.240."
                ),
                "mirror_topic": "prescripcion_y_pagare_consumo",
            },
            "embargo de sueldo|me descuentan del recibo|pagaré|ejecución|cuánto me pueden embargar": {
                "situation": "Embargo judicial sobre recibo de sueldo o cuenta sueldo",
                "law_article": "Decreto 484/87 y Art. 147 Ley 20.744 (Inembargabilidad del SMVM y topes del 10% y 20%)",
                "explanation": (
                    "Por deudas comerciales o financieras NUNCA pueden embargarte el total del sueldo. El Salario Mínimo "
                    "Vital y Móvil (SMVM) es 100% inembargable. Sobre lo que exceda 1 SMVM solo pueden retener el 10% "
                    "(si el excedente no supera un segundo SMVM) o el 20% (si supera dos SMVM)."
                ),
                "protection_tip": (
                    "Si un banco o financiera te debita el 50% o el 100% del sueldo al acreditarse en la Cuenta Sueldo, "
                    "podés presentar un pedido urgente de 'Stop Debit' y límite Decreto 484/87."
                ),
                "mirror_topic": "embargo_haberes_tope",
            },
            "estafa bancaria|phishing|sacaron un préstamo a mi nombre|vaciamiento de cuenta|compra desconocida": {
                "situation": "Fraude bancario digital (phishing), préstamos preaprobados no consentidos o consumos desconocidos",
                "law_article": "Arts. 5, 40 y 53 Ley 24.240 + Comunicaciones 'A' 7319 y 7724 del BCRA",
                "explanation": (
                    "Los bancos y billeteras virtuales tienen obligación objetiva de seguridad (Art. 5 y 40 LDC) sobre "
                    "sus canales digitales. Ante préstamos sacados en minutos por estafadores sin validación biométrica "
                    "robusta, los tribunales dictan medidas cautelares suspendiendo las cuotas y restituyendo fondos."
                ),
                "protection_tip": (
                    "Hacé el desconocimiento dentro de los 30 días, radicá denuncia policial/fiscal de ciberdelito y "
                    "pedí medida cautelar de 'No Innovar' con beneficio de justicia gratuita (Art. 53 Ley 24.240)."
                ),
                "mirror_topic": "fraude_bancario_consumidor",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "prescripcion_y_pagare_consumo": RealCourtMirrorInfo(
                topic_key="prescripcion_y_pagare_consumo",
                how_courts_actually_rule=(
                    "Los Juzgados Comerciales y Civiles rechazan la habilidad ejecutiva de los 'pagarés de consumo' "
                    "cuando el banco o financiera no acompaña el contrato original que cumpla el Art. 36 de la Ley 24.240 "
                    "(detalle de tasa, CFT y capital), y declaran de oficio o a pedido de parte la prescripción."
                ),
                leading_cases=[
                    "CNCom, En Pleno, 'Autoconvocatoria a plenario s/ competencia del fuero comercial en ejecución de pagarés de consumo'",
                    "SCBA, 'Asociación Mutual Asís c/ Cubilla' y 'Cuevas c/ Salcedo' (Integración del pagaré de consumo con el Art. 36 LDC)",
                ],
                average_duration_months_range=(4, 14),
                anti_lie_warning_for_citizen=(
                    "En un juicio ejecutivo tenés solo 5 días hábiles desde que te llega la cédula de 'intimación de pago "
                    "y citación de remate' para oponer la excepción de prescripción o inhabilidad de título."
                ),
            ),
            "embargo_haberes_tope": RealCourtMirrorInfo(
                topic_key="embargo_haberes_tope",
                how_courts_actually_rule=(
                    "Los tribunales ordenan en 48 a 72 horas el levantamiento o reducción de cualquier embargo o débito "
                    "automático que perfore el límite de orden público del Decreto 484/87 sobre haberes alimentarios."
                ),
                leading_cases=[
                    "CSJN, 'Etcheverry, Roberto E.' (Carácter alimentario e intangible del salario frente a embargos comerciales)",
                ],
                average_duration_months_range=(1, 3),
                anti_lie_warning_for_citizen=(
                    "Los topes del Decreto 484/87 (10% y 20% sobre el excedente del SMVM) rigen para todas las deudas "
                    "comerciales y financieras (la única excepción legal son las cuotas alimentarias de hijos)."
                ),
            ),
            "fraude_bancario_consumidor": RealCourtMirrorInfo(
                topic_key="fraude_bancario_consumidor",
                how_courts_actually_rule=(
                    "La jurisprudencia comercial y civil/comercial federal otorga en 5 a 15 días medidas cautelares "
                    "ordenando al banco abstenerse de descontar las cuotas del préstamo fraudulento y no informar al "
                    "usuario como moroso en la Central de Deudores del BCRA ni en Veraz."
                ),
                leading_cases=[
                    "CNCom, Sala D y Sala F (2022-2026): Nulidad de mutuos preaprobados por phishing y condena por Daño Punitivo Art. 52 bis LDC",
                ],
                average_duration_months_range=(1, 18),
                anti_lie_warning_for_citizen=(
                    "Por el Art. 53 de la Ley 24.240 el consumidor goza de Justicia Gratuita: no tenés que pagar tasa "
                    "de justicia para iniciar la demanda contra el banco."
                ),
            ),
        }

    @staticmethod
    def check_debt_prescription(
        default_mora_date: date,
        debt_type: str = "PRESTAMO_GENERAL_CCYCN",
        reference_today: date | None = None,
    ) -> dict[str, object]:
        """Determina matemáticamente si una deuda comercial/financiera se encuentra prescripta.

        Plazos legales exactos aplicados:
        - `TARJETA_CREDITO_EJECUTIVA`: 1 año (365 días, Art. 47 inc. a Ley 25.065)
        - `TARJETA_CREDITO_ORDINARIA`: 3 años (1095 días, Art. 47 inc. b Ley 25.065)
        - `PAGARE_VISTA_O_VENCIMIENTO`: 3 años (1095 días, Art. 96 Decreto-Ley 5965/63)
        - `SERVICIOS_PERIODICOS_EXPENSAS`: 2 años (730 días, Art. 2562 inc. c CCyCN)
        - `PRESTAMO_GENERAL_CCYCN`: 5 años (1825 días, Art. 2560 CCyCN)
        """
        today = reference_today or date.today()
        if default_mora_date > today:
            raise ValueError("La fecha de mora no puede ser futura respecto a la fecha de cálculo.")

        elapsed_days = (today - default_mora_date).days
        elapsed_years = (Decimal(elapsed_days) / Decimal("365")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        rules_map: dict[str, tuple[int, str]] = {
            "TARJETA_CREDITO_EJECUTIVA": (365, "Art. 47 inc. a Ley 25.065 (1 año vía ejecutiva)"),
            "TARJETA_CREDITO_ORDINARIA": (1095, "Art. 47 inc. b Ley 25.065 (3 años vía ordinaria)"),
            "PAGARE_VISTA_O_VENCIMIENTO": (1095, "Art. 96 Decreto-Ley 5965/63 (3 años acción cambiaria directa)"),
            "SERVICIOS_PERIODICOS_EXPENSAS": (730, "Art. 2562 inc. c CCyCN (2 años obligaciones periódicas)"),
            "PRESTAMO_GENERAL_CCYCN": (1825, "Art. 2560 CCyCN (5 años plazo genérico de prescripción)"),
        }

        dtype_key = debt_type.upper().strip()
        if dtype_key not in rules_map:
            dtype_key = "PRESTAMO_GENERAL_CCYCN"

        limit_days, law_citation = rules_map[dtype_key]
        is_prescribed = elapsed_days > limit_days
        veraz_right_to_be_forgotten = elapsed_days > 1825  # Art. 26 inc. 4 Ley 25.326 (5 años)

        return {
            "debt_type": dtype_key,
            "default_mora_date": default_mora_date.isoformat(),
            "evaluation_date": today.isoformat(),
            "elapsed_days": elapsed_days,
            "elapsed_years": elapsed_years,
            "legal_limit_days": limit_days,
            "applicable_law": law_citation,
            "is_prescribed": is_prescribed,
            "qualifies_for_veraz_deletion_art26_ley25326": veraz_right_to_be_forgotten,
            "citizen_verdict": (
                f"DEUDA PRESCRIPTA ({law_citation}): Pasaron {elapsed_years} años ({elapsed_days} días) desde la mora. "
                f"Si no se inició demanda judicial interruptiva, la deuda perdió exigibilidad legal. ¡No firmes refinanciación!"
                if is_prescribed
                else f"DEUDA AÚN EN PLAZO ({law_citation}): Pasaron {elapsed_years} años ({elapsed_days} de {limit_days} días)."
            ),
        }

    @classmethod
    def calculate_embargo_sueldo_limit(
        cls,
        net_monthly_salary_ars: Decimal,
        smvm_current_ars: Decimal | None = None,
    ) -> dict[str, object]:
        """Calcula con precisión Decimal el monto máximo embargable de un sueldo según Decreto 484/87.

        Fórmula legal (Decreto 484/87 Art. 1 y 2):
        - Si `net_monthly_salary_ars <= smvm_current_ars`: Embargo = $0.00 (100% inembargable).
        - Si el excedente sobre 1 SMVM es `<= 1 SMVM` (es decir, sueldo total <= 2 SMVM):
          Embargo = 10% sobre (`net_monthly_salary_ars - smvm_current_ars`).
        - Si el excedente sobre 1 SMVM es `> 1 SMVM` (es decir, sueldo total > 2 SMVM):
          Embargo = 20% sobre (`net_monthly_salary_ars - smvm_current_ars`).
        """
        effective_smvm = smvm_current_ars if smvm_current_ars is not None else cls._SMVM_CURRENT_ARS
        if net_monthly_salary_ars < Decimal("0.00"):
            raise ValueError("El salario mensual no puede ser negativo.")
        if effective_smvm <= Decimal("0.00"):
            raise ValueError("El valor del SMVM debe ser mayor a cero.")

        salary = net_monthly_salary_ars.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        smvm = effective_smvm.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if salary <= smvm:
            max_embargo = Decimal("0.00")
            rate_applied = Decimal("0.00")
            bracket_desc = "INEMBARGABLE (Sueldo menor o igual a 1 SMVM)"
        else:
            surplus = salary - smvm
            if surplus <= smvm:
                rate_applied = Decimal("10.00")
                max_embargo = (surplus * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                bracket_desc = "TOPE 10% SOBRE EXCEDENTE DE 1 SMVM (Sueldo entre 1 y 2 SMVM)"
            else:
                rate_applied = Decimal("20.00")
                max_embargo = (surplus * Decimal("0.20")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                bracket_desc = "TOPE 20% SOBRE EXCEDENTE DE 1 SMVM (Sueldo superior a 2 SMVM)"

        untouchable_pocket_salary = (salary - max_embargo).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "net_monthly_salary_ars": salary,
            "smvm_reference_ars": smvm,
            "bracket_applied": bracket_desc,
            "percentage_on_surplus": rate_applied,
            "max_legal_monthly_embargo_ars": max_embargo,
            "untouchable_net_salary_for_worker_ars": untouchable_pocket_salary,
            "legal_basis": "Decreto Nacional 484/87 (Arts. 1 y 2) reglamentario de los Arts. 120 y 147 Ley 20.744",
        }
