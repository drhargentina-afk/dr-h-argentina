"""Sub-Agente Especializado del Fuero de Seguridad Social y Salud (`06_seguridad_social_salud`).

Analiza demandas de reajuste de haberes contra ANSES, expedientes previsionales,
pensiones y acciones de Amparo de Salud / Discapacidad (Ley 24.901 y PMO).
"""
from __future__ import annotations

from decimal import Decimal
import importlib

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.06_seguridad_social_salud.repo_seguridad_social")
RepoSeguridadSocial = _repo_mod.RepoSeguridadSocial


class AgentSeguridadSocial(BaseFueroAgent):
    """Agente especialista del Fuero de Seguridad Social y Salud (`06_seguridad_social_salud`)."""

    def __init__(self) -> None:
        self._repo = RepoSeguridadSocial()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.SEGURIDAD_SOCIAL_SALUD

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica actuaciones de Seguridad Social o Amparos de Salud y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("medida cautelar", "amparo de salud", "ley 24.901", "pmo", "cobertura 100%", "astreintes")):
            return (
                "Acción / Medida Cautelar de Amparo de Salud",
                "Orden judicial urgente que obliga a la Obra Social, Prepaga o PAMI a entregar el medicamento, prótesis o tratamiento de inmediato bajo pena de multa diaria (astreintes) por cada día de demora.",
                "Art. 43 CN, Ley 16.986, Ley 24.901 y Res. PMO 201/2002",
            )
        if any(w in low for w in ("badaro", "elliff", "blanco", "reajuste de haberes", "anses", "movilidad")):
            return (
                "Demanda / Sentencia de Reajuste Previsional (ANSES)",
                "Trámite judicial donde se ordena a la ANSES recalcular el haber jubilatorio inicial y su movilidad aplicando los fallos de la Corte Suprema y pagar las diferencias retroactivas.",
                "Art. 14 bis CN, Ley 24.241 y Ley 24.463 (Fallos 'Badaro', 'Elliff' y 'Blanco' CSJN)",
            )
        if any(w in low for w in ("liquidación", "ejecución de sentencia previsional", "plazo 120 días", "embargo anses")):
            return (
                "Liquidación y Ejecución de Sentencia contra ANSES",
                "Etapa final donde, vencido el plazo legal de 120 días hábiles para que ANSES pague voluntariamente la sentencia firme, el contador aprueba la liquidación y el juez ordena el cobro por embargo.",
                "Art. 22 Ley 24.463 y CPCCN",
            )
        return (
            "Actuación de Seguridad Social / Salud",
            "Trámite incorporado al expediente previsional o sanitario federal.",
            "Ley 24.241, Ley 24.463 y Ley 16.986",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Proyecta diferencia de haber o retroactivo previsional/cobertura documentada con Decimal."""
        if max_documented_amount is None:
            return None

        projected = (max_documented_amount * Decimal("1.24")).quantize(Decimal("0.01"))
        return PreliminaryMoneyCalculation(
            concept_description="Haber / Retroactivo Previsional o Prestación Médica Documentada",
            base_amount_ars=max_documented_amount,
            estimated_total_ars=projected,
            calculation_formula_applied=(
                "Monto base documentado en liquidación/presupuesto + proyección orientativa del 24% por movilidad/accesorios legales."
            ),
        )
