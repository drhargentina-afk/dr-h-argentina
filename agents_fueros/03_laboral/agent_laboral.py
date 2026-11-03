"""Sub-Agente Especializado del Fuero Laboral (`03_laboral`).

Analiza demandas de despido, intercambios telegráficos (TCL/CD), actas del SECLO,
pericias contables/médicas ART y acuerdos conciliatorios aplicando la Ley 20.744 (LCT).
"""
from __future__ import annotations

from decimal import Decimal
import importlib
import re

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.03_laboral.repo_laboral")
RepoLaboral = _repo_mod.RepoLaboral


class AgentLaboral(BaseFueroAgent):
    """Agente especialista del Fuero Laboral Argentino (`03_laboral`)."""

    _RE_YEARS_ANTIGUEDAD = re.compile(r"\b(\d{1,2})\s+a[ñn]os\s+de\s+antig[üu]edad\b", re.IGNORECASE)

    def __init__(self) -> None:
        self._repo = RepoLaboral()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.LABORAL

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica piezas del expediente laboral y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("telegrama", "tcl", "carta documento", "correo argentino", "ley 23.789")):
            return (
                "Intercambio Telegráfico (Telegrama Laboral / Carta Documento)",
                "Comunicaciones fehacientes entre el trabajador y el empleador donde se fijó la causa del despido o intimación; lo que se escribió allí define el marco del juicio (Art. 243 LCT).",
                "Arts. 57, 242 y 243 Ley 20.744 / Ley 23.789",
            )
        if any(w in low for w in ("seclo", "acta de cierre", "conciliación laboral", "homologación art. 15")):
            return (
                "Acta de Audiencia SECLO / Conciliación Laboral",
                "Constancia de la audiencia obligatoria previa donde las partes intentaron llegar a un acuerdo o quedó habilitada la vía para iniciar el juicio laboral.",
                "Ley 24.635 (SECLO) y Art. 15 Ley 20.744",
            )
        if any(w in low for w in ("pericia contable", "libros laborales", "art. 52 lct", "liquidación practicada")):
            return (
                "Pericia Contable Laboral",
                "Informe del contador oficial designado por el juez que revisó los recibos y libros de la empresa para calcular el mejor sueldo y la indemnización exacta.",
                "Arts. 52, 55 y 245 Ley 20.744",
            )
        if any(w in low for w in ("comisión médica", "comision medica", "srt", "incapacidad", "baremo", "pericia médica")):
            return (
                "Dictamen Médico / Pericia de Incapacidad Laboral (ART)",
                "Evaluación médica oficial que determina qué porcentaje de incapacidad física o psicológica dejó el accidente o enfermedad laboral según el Baremo Oficial.",
                "Art. 14 Ley 24.557, Ley 26.773 y Decreto 659/96",
            )
        if any(w in low for w in ("promueve demanda", "inicia demanda", "art. 245", "despido")):
            return (
                "Demanda Laboral por Despido / Cobro de Pesos",
                "Escrito principal donde se detallan la fecha de ingreso, categoría, remuneración y los rubros indemnizatorios reclamados bajo beneficio de gratuidad.",
                "Arts. 20, 232, 233 y 245 Ley 20.744",
            )
        return (
            "Actuación Procesal Laboral",
            "Trámite o proveído del juzgado del trabajo (protegido por el beneficio de gratuidad del Art. 20 LCT).",
            "Ley 20.744 y Ley Procesal Laboral aplicable",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Ejecuta el cálculo determinista Art. 245 LCT sobre el monto o salario documentado."""
        if max_documented_amount is None:
            return None

        match_years = self._RE_YEARS_ANTIGUEDAD.search(combined_text)
        years = int(match_years.group(1)) if match_years else 3

        # Si el monto documentado es un salario mensual típico (< $3.500.000), calculamos la liquidación Art. 245 completa
        if max_documented_amount <= Decimal("3500000.00"):
            calc = self._repo.calculate_indemnizacion_art245(
                best_monthly_gross_salary_ars=max_documented_amount,
                years_of_service=max(1, years),
                months_fraction=4,
                remaining_days_in_dismissal_month=15,
            )
            total_lct = Decimal(str(calc["total_liquidacion_despido_ars"]))
            return PreliminaryMoneyCalculation(
                concept_description=f"Liquidación Legal por Despido (Arts. 245, 232 y 233 LCT - {years} años antigüedad)",
                base_amount_ars=max_documented_amount,
                estimated_total_ars=total_lct,
                calculation_formula_applied=(
                    "Indemnización Art. 245 + Preaviso Art. 232 (+SAC) + Integración Mes de Despido Art. 233 (+SAC) "
                    "con control de piso constitucional 'Vizzotti'."
                ),
            )

        # Si el monto documentado ya es el total reclamado en la demanda, calculamos el piso conciliatorio razonable (75%)
        projected = (max_documented_amount * Decimal("1.15")).quantize(Decimal("0.01"))
        return PreliminaryMoneyCalculation(
            concept_description="Capital Indemnizatorio Laboral Documentado en Expediente (Ley 20.744)",
            base_amount_ars=max_documented_amount,
            estimated_total_ars=projected,
            calculation_formula_applied=(
                "Capital de demanda laboral documentado + proyección base del 15% por accesorios (Art. 20 y 245 LCT)."
            ),
        )
