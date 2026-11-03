"""Sub-Agente Especializado del Fuero Contencioso Administrativo y Tributario (`07_contencioso_tributario`).

Analiza ejecuciones fiscales (ARCA/AFIP, ARBA, AGIP, Rentas), actas de infracción/clausura,
multas administrativas o de tránsito y recursos/demandas contra el Estado (Ley 19.549).
"""
from __future__ import annotations

from decimal import Decimal
import importlib

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.07_contencioso_tributario.repo_contencioso")
RepoContencioso = _repo_mod.RepoContencioso


class AgentContencioso(BaseFueroAgent):
    """Agente especialista del Fuero Contencioso Administrativo y Tributario (`07_contencioso_tributario`)."""

    def __init__(self) -> None:
        self._repo = RepoContencioso()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.CONTENCIOSO_TRIBUTARIO

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica piezas del expediente contencioso o tributario y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("boleta de deuda", "ejecución fiscal", "ejecucion fiscal", "art. 92 ley 11.683", "agente fiscal")):
            return (
                "Boleta de Deuda / Mandamiento de Ejecución Fiscal",
                "Título con el que el organismo recaudador (ARCA/AFIP, ARBA, AGIP o Municipio) inicia el cobro judicial. Tenés 5 días hábiles para oponer excepción de pago, espera o prescripción de 5 años.",
                "Arts. 56 y 92 Ley 11.683 / Doctrina CSJN 'Intercorp'",
            )
        if any(w in low for w in ("acta de infracción", "fotomulta", "juzgado de faltas", "ley 24.449", "clausura")):
            return (
                "Acta de Comprobación / Infracción Administrativa o de Tránsito",
                "Constancia labrada por inspector o cámara sobre una supuesta falta; podés presentar descargo verificando si cumple los requisitos formales y si no está prescripta (2 o 5 años).",
                "Arts. 70, 71 y 89 Ley 24.449 / Ley 19.549",
            )
        if any(w in low for w in ("acto administrativo", "recurso jerárquico", "recurso de reconsideración", "ley 19.549", "dictamen jurídico")):
            return (
                "Resolución Administrativa / Recurso contra el Estado",
                "Decisión de un organismo público o presentación recursiva del ciudadano cuestionando su validez por falta de motivación o vicio en el procedimiento.",
                "Arts. 7, 14 y 25 Ley 19.549 (reformada por Ley 27.742) y Decreto 1759/72",
            )
        return (
            "Actuación Contencioso Administrativa / Fiscal",
            "Trámite procesal dentro de una causa contencioso administrativa o de ejecución tributaria.",
            "Ley 19.549, Ley 11.683 y Código Contencioso Administrativo aplicable",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Calcula el monto nominal de la boleta de deuda/multa más el 15% presupuestado para costas fiscales."""
        if max_documented_amount is None:
            return None

        with_fiscal_costs = (max_documented_amount * Decimal("1.15")).quantize(Decimal("0.01"))
        return PreliminaryMoneyCalculation(
            concept_description="Capital de Boleta de Deuda / Multa Administrativa Documentada (+15% presupuestado p/ costas)",
            base_amount_ars=max_documented_amount,
            estimated_total_ars=with_fiscal_costs,
            calculation_formula_applied=(
                "Capital nominal de la boleta de deuda o multa + 15% orientativo de intereses resarcitorios/punitorios y costas (Art. 92 Ley 11.683)."
            ),
        )
