"""Sub-Agente Especializado del Fuero Civil (`04_civil`).

Analiza sucesiones, demandas de daños y perjuicios por accidentes de tránsito,
juicios de desalojo/ejecución de alquileres, escrituración y beneficio de litigar sin gastos.
"""
from __future__ import annotations

from decimal import Decimal
import importlib

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.04_civil.repo_civil")
RepoCivil = _repo_mod.RepoCivil


class AgentCivil(BaseFueroAgent):
    """Agente especialista del Fuero Civil Argentino (`04_civil`)."""

    def __init__(self) -> None:
        self._repo = RepoCivil()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.CIVIL

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica actuaciones del expediente civil y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("declaratoria de herederos", "edictos", "sucesión ab-intestato", "acervo hereditario")):
            return (
                "Actuación de Proceso Sucesorio / Declaratoria de Herederos",
                "Trámite o resolución judicial que reconoce quiénes son los herederos legales del causante y habilita la transmisión o venta por tracto abreviado de los bienes.",
                "Arts. 2277, 2337 y 2340 Código Civil y Comercial de la Nación",
            )
        if any(w in low for w in ("beneficio de litigar sin gastos", "art. 78", "art. 83", "exención de tasa")):
            return (
                "Incidente de Beneficio de Litigar sin Gastos",
                "Trámite paralelo donde se demuestra la falta de recursos económicos suficientes para quedar exento de pagar la tasa de justicia y las costas del juicio.",
                "Arts. 78 a 86 Código Procesal Civil y Comercial",
            )
        if any(w in low for w in ("citación en garantía", "aseguradora", "art. 118 ley 17.418", "daños y perjuicios", "accidente")):
            return (
                "Demanda / Actuación de Daños y Perjuicios y Citación de Seguro",
                "Presentación donde se reclaman las lesiones y daños del siniestro, incorporando obligatoriamente a la compañía de seguros del responsable para que responda con su patrimonio.",
                "Arts. 1741, 1746 y 1757 CCyCN / Art. 118 Ley de Seguros 17.418",
            )
        if any(w in low for w in ("desalojo", "locación", "alquileres", "art. 684 bis", "lanzamiento")):
            return (
                "Actuación de Juicio de Desalojo / Locación",
                "Trámite judicial tendiente a recuperar la tenencia del inmueble locado o discutir el cumplimiento del contrato de alquiler.",
                "Arts. 1187 a 1226 CCyCN y Arts. 679 a 688 CPCCN",
            )
        if any(w in low for w in ("pericia mecánica", "pericia médica", "apertura a prueba", "audiencia art. 360")):
            return (
                "Etapa Probatoria Civil (Audiencia Art. 360 / Pericias)",
                "Fase central del juicio donde el juez ordena producir las pruebas de testigos, informes y peritos oficiales (médico, mecánico, psicólogo).",
                "Arts. 360 a 485 Código Procesal Civil y Comercial",
            )
        return (
            "Proveído / Escrito Procesal Civil",
            "Actuación incorporada al expediente civil ordinario o sumarísimo.",
            "Código Civil y Comercial (Ley 26.994) y Código Procesal aplicable",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Calcula proyección sobre el reclamo civil o acervo documentado con precisión Decimal."""
        if max_documented_amount is None:
            return None

        low = combined_text.lower()
        if any(w in low for w in ("alquiler", "locación", "canon locativo")) and max_documented_amount <= Decimal("2500000.00"):
            upd = self._repo.calculate_alquiler_update(
                base_rent_ars=max_documented_amount,
                index_start_value=Decimal("100.00"),
                index_end_value=Decimal("142.50"),
                index_type="ICL_IPC_TRIMESTRAL",
            )
            return PreliminaryMoneyCalculation(
                concept_description="Simulación de Actualización Locativa Trimestral (ICL/IPC)",
                base_amount_ars=max_documented_amount,
                estimated_total_ars=Decimal(str(upd["updated_rent_ars"])),
                calculation_formula_applied=str(upd["legal_basis"]),
            )

        projected = (max_documented_amount * Decimal("1.20")).quantize(Decimal("0.01"))
        return PreliminaryMoneyCalculation(
            concept_description="Capital Civil / Daños Documentado en Expediente (Art. 1746 CCyCN)",
            base_amount_ars=max_documented_amount,
            estimated_total_ars=projected,
            calculation_formula_applied=(
                "Capital nominal documentado + estimación preventiva del 20% por intereses moratorios desde la mora (Art. 1748 CCyCN)."
            ),
        )
