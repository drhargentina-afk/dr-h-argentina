"""Consultor de Repositorios, Calculadora de Alquileres (ICL/IPC) y Baremo Civil (`04_civil`).

Incluye:
- Calculadora Determinista de Actualización de Alquileres por coeficiente ICL (BCRA) o IPC (INDEC)
  con `Decimal` exacto, distinguiendo el régimen vigente post-DNU 70/2023 (libertad contractual
  Arts. 1187-1226 CCyCN) de contratos históricos anteriores.
- Calculadora Orientativa de Indemnización por Incapacidad Sobreviniente en Accidentes de Tránsito
  (Art. 1746 CCyCN — aproximación matemática de renta capitalizada tipo Vuoto/Méndez + Daño Moral Art. 1741).
- Guía de etapas de Sucesiones (Declaratoria de Herederos e Inscripción) y Beneficio de Litigar sin Gastos (Art. 78 CPCCN).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoCivil(BaseRepositoryConsultant):
    """Consultor normativo, jurisprudencial y calculadora civil (`04_civil`)."""

    _LATEST_ICL_BCRA_INDEX: Decimal = Decimal("18.4500")
    _LATEST_IPC_INDEC_MONTHLY_PCT: Decimal = Decimal("3.20")

    @classmethod
    def update_civil_indices(
        cls,
        icl_bcra: Decimal | None = None,
        ipc_monthly_pct: Decimal | None = None,
    ) -> dict[str, Decimal]:
        """Actualiza en caliente el Índice para Contratos de Locación (ICL BCRA) y el IPC mensual (INDEC)."""
        if icl_bcra is not None:
            val_icl = Decimal(str(icl_bcra)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if val_icl <= Decimal("0.0000"):
                raise ValueError("El índice ICL BCRA debe ser mayor a cero.")
            cls._LATEST_ICL_BCRA_INDEX = val_icl
        if ipc_monthly_pct is not None:
            val_ipc = Decimal(str(ipc_monthly_pct)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if val_ipc < Decimal("-50.00") or val_ipc > Decimal("500.00"):
                raise ValueError("La variación mensual IPC está fuera de rango razonable.")
            cls._LATEST_IPC_INDEC_MONTHLY_PCT = val_ipc
        return {
            "latest_icl_bcra_index": cls._LATEST_ICL_BCRA_INDEX,
            "latest_ipc_indec_monthly_pct": cls._LATEST_IPC_INDEC_MONTHLY_PCT,
        }

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.CIVIL

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Código Civil y Comercial de la Nación (Ley 26.994) - Arts. 1708 a 1780 (Responsabilidad Civil, Daños e Incapacidad Art. 1746)",
            "Código Civil y Comercial de la Nación - Arts. 1187 a 1226 (Contratos de Locación / Alquileres con reformas del DNU 70/2023)",
            "Código Civil y Comercial de la Nación - Arts. 2277 a 2531 (Proceso Sucesorio, Declaratoria de Herederos y Partición)",
            "Código Procesal Civil y Comercial de la Nación (Ley 17.454) - Arts. 78 a 86 (Beneficio de Litigar sin Gastos)",
            "Ley Nacional de Tránsito N° 24.449 y Art. 68 (Seguro Obligatorio de Responsabilidad Civil Automotor)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "alquiler|locación|desalojo|aumento de alquiler|depósito en garantía|rescisión|contrato": {
                "situation": "Consulta contractual civil, locación o responsabilidad patrimonial",
                "law_article": "Arts. 957 a 1226 y 1708 a 1780 del Código Civil y Comercial de la Nación",
                "explanation": (
                    "Para contratos firmados desde el 29/12/2023 rige el índice de actualización pactado en el contrato "
                    "(habitualmente IPC del INDEC o ICL del BCRA) y el inquilino puede rescindir en cualquier momento "
                    "abonando el 10% del saldo futuro del contrato (Art. 1221 CCyCN reformado)."
                ),
                "protection_tip": (
                    "Verificá siempre la fecha de firma de tu contrato: si fue firmado antes del 29/12/2023, sigue "
                    "rigiendo hasta su vencimiento la ley vigente al día en que se firmó."
                ),
                "mirror_topic": "alquileres_y_desalojo",
            },
            "sucesión|sucesion|herencia|declaratoria de herederos|falleció mi papá|propiedad heredada": {
                "situation": "Trámite sucesorio para transmitir bienes inmuebles, autos o cuentas bancarias",
                "law_article": "Arts. 2277, 2335 y 2363 del Código Civil y Comercial de la Nación",
                "explanation": (
                    "Una sucesión ab-intestato (sin testamento) tiene 3 etapas claras: 1) Apertura y publicación de "
                    "edictos por 30 días; 2) Dictado de la Declaratoria de Herederos; 3) Denuncia de bienes, pago de "
                    "tasa de justicia y orden de inscripción (o tracto abreviado si se vende el inmueble)."
                ),
                "protection_tip": (
                    "NUNCA firmes una 'Cesión de Derechos Hereditarios' en escribanía sin asesoramiento propio, porque "
                    "estás entregando tu parte de la herencia. Tampoco hace falta escriturar dos veces: podés vender por 'Tracto Abreviado'."
                ),
                "mirror_topic": "sucesion_etapas",
            },
            "choque|siniestro vial|poliza automotor|latigazo cervical": {
                "situation": "Reclamo civil por daños materiales o responsabilidad extracontractual",
                "law_article": "Arts. 1741, 1746 y 1757 del Código Civil y Comercial + Art. 68 Ley 24.449",
                "explanation": (
                    "En un reclamo de responsabilidad civil se acciona contra el responsable, el titular registral y su "
                    "compañía aseguradora citada en garantía (Art. 118 Ley 17.418). La indemnización se determina conforme "
                    "a las pautas del Art. 1746 del Código Civil y Comercial."
                ),
                "protection_tip": (
                    "Si no podés pagar la tasa de justicia (3% en Nación/CABA o 2.2% en PBA), tu abogado debe iniciar "
                    "simultáneamente el 'Beneficio de Litigar sin Gastos' (Art. 78 CPCCN), que es 100% gratuito."
                ),
                "mirror_topic": "accidente_transito_danos",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "accidente_transito_danos": RealCourtMirrorInfo(
                topic_key="accidente_transito_danos",
                how_courts_actually_rule=(
                    "Los juzgados civiles dan valor determinante al dictamen del Perito Médico y Perito Ingeniero Mecánico "
                    "sorteados de oficio. Aplican la fórmula matemática del Art. 1746 CCyCN (variantes 'Vuoto' o 'Méndez') "
                    "más daño moral (Art. 1741) y gastos médicos presumidos (Art. 1746 in fine)."
                ),
                leading_cases=[
                    "CSJN, 'Ontiveros, Stella Maris' y 'Grippo, Guillermo Oscar' (Reparación plena e integral en accidentes)",
                    "CNCiv, Sala A / M (Aplicación de fórmulas de valor presente Art. 1746 CCyCN)",
                ],
                average_duration_months_range=(20, 42),
                anti_lie_warning_for_citizen=(
                    "Las aseguradoras suelen ofrecer en mediación previa montos muy inferiores al valor de sentencia; "
                    "sin embargo, todo acuerdo requiere tu firma expresa o poder especial con facultad de transigir."
                ),
            ),
            "sucesion_etapas": RealCourtMirrorInfo(
                topic_key="sucesion_etapas",
                how_courts_actually_rule=(
                    "Desde el inicio de la sucesión hasta obtener la Declaratoria de Herederos el tiempo real promedio "
                    "es de 3 a 6 meses (considerando los 30 días corridos de edictos en el Boletín Oficial y vista fiscal)."
                ),
                leading_cases=[
                    "CCyCN Art. 2337 (Investidura de pleno derecho de descendientes, ascendientes y cónyuge, requerida declaratoria para bienes registrables)",
                ],
                average_duration_months_range=(3, 8),
                anti_lie_warning_for_citizen=(
                    "Si una sucesión sin conflicto entre herederos lleva más de 1 año sin tener siquiera la Declaratoria "
                    "de Herederos, revisá en el expediente si se publicaron los edictos y se diligenciaron los oficios."
                ),
            ),
            "alquileres_y_desalojo": RealCourtMirrorInfo(
                topic_key="alquileres_y_desalojo",
                how_courts_actually_rule=(
                    "En juicios de desalojo por falta de pago o vencimiento de contrato (Art. 684 bis CPCCN), previa "
                    "intimación fehaciente por 10 días corridos (Art. 1222 CCyCN), el proceso demora entre 6 y 14 meses."
                ),
                leading_cases=[
                    "CPCCN Art. 680 bis y 684 bis (Desocupación inmediata bajo caución real en supuestos de falta de pago o vencimiento)",
                ],
                average_duration_months_range=(6, 14),
                anti_lie_warning_for_citizen=(
                    "Ningún propietario puede desalojar por mano propia ni cambiar la cerradura sin orden judicial "
                    "(constituiría delito penal), y todo pago de alquiler debe hacerse contra recibo o transferencia bancaria trazable."
                ),
            ),
        }

    @staticmethod
    def calculate_alquiler_update(
        base_rent_ars: Decimal,
        index_start_value: Decimal,
        index_end_value: Decimal,
        index_type: str = "ICL_BCRA",
    ) -> dict[str, object]:
        """Calcula con exactitud Decimal la actualización del canon locativo por ICL o IPC.

        Fórmula matemática:
        - Nuevo Alquiler = Alquiler Base * (Índice Fecha Actualización / Índice Fecha Inicio)
        - Variación Porcentual = ((Índice Final / Índice Inicial) - 1) * 100
        """
        if base_rent_ars <= Decimal("0.00"):
            raise ValueError("El monto base de alquiler debe ser positivo.")
        if index_start_value <= Decimal("0.00") or index_end_value <= Decimal("0.00"):
            raise ValueError("Los valores de índice inicial y final deben ser mayores a cero.")

        ratio = index_end_value / index_start_value
        updated_rent = (base_rent_ars * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        variation_pct = ((ratio - Decimal("1.00")) * Decimal("100.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        return {
            "index_type": index_type.upper(),
            "base_rent_ars": base_rent_ars.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "coefficient_applied": ratio.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            "variation_pct": variation_pct,
            "updated_rent_ars": updated_rent,
            "legal_basis": "Arts. 1187 a 1221 CCyCN (Régimen de actualización locativa verificable)",
        }

    @staticmethod
    def calculate_baremo_civil_accidente(
        monthly_income_ars: Decimal,
        victim_age: int,
        disability_pct: Decimal,
    ) -> dict[str, object]:
        """Calcula una estimación determinista de incapacidad civil sobreviniente (Art. 1746 CCyCN).

        Fórmula aplicada:
        - Se proyectan los ingresos anuales afectados por el porcentaje de incapacidad hasta la edad
          productiva de referencia (75 años según criterio 'Méndez'), aplicando factor de descuento financiero
          del 4% anual y sumando un 25% estimativo por daño moral (Art. 1741 CCyCN).
        """
        if monthly_income_ars <= Decimal("0.00"):
            raise ValueError("El ingreso mensual debe ser mayor a cero.")
        if type(victim_age) is not int or not (1 <= victim_age < 75):
            raise ValueError("La edad de la víctima debe estar entre 1 y 74 años para la proyección a 75 años.")
        if disability_pct <= Decimal("0.00") or disability_pct > Decimal("100.00"):
            raise ValueError("El porcentaje de incapacidad debe estar entre 0.01% y 100.00%.")

        remaining_years = 75 - victim_age
        annual_loss = monthly_income_ars * Decimal("13") * (disability_pct / Decimal("100.00"))

        # Factor de valor actual de una renta constante al 4% anual: (1 - (1.04)^(-n)) / 0.04
        discount_rate = Decimal("0.04")
        one_plus_i_pow_n = (Decimal("1.04") ** Decimal(remaining_years))
        present_value_factor = (Decimal("1") - (Decimal("1") / one_plus_i_pow_n)) / discount_rate

        incapacidad_capital = (annual_loss * present_value_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        dano_moral_est = (incapacidad_capital * Decimal("0.25")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_civil = (incapacidad_capital + dano_moral_est).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return {
            "remaining_productive_years_to_75": remaining_years,
            "incapacidad_art1746_capital_ars": incapacidad_capital,
            "dano_moral_art1741_estimado_ars": dano_moral_est,
            "total_estimado_reclamo_civil_ars": total_civil,
            "legal_basis": "Arts. 1741 y 1746 Código Civil y Comercial de la Nación (Renta Capitalizada)",
        }
