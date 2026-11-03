"""Sub-Agente Especializado del Fuero Comercial y Defensa del Consumidor (`05_comercial_consumidor`).

Analiza juicios ejecutivos, pagarés de consumo, embargos de haberes, reclamos por fraude bancario
(phishing) y acciones de la Ley de Defensa del Consumidor N° 24.240.
"""
from __future__ import annotations

from decimal import Decimal
import importlib

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.05_comercial_consumidor.repo_comercial")
RepoComercial = _repo_mod.RepoComercial


class AgentComercial(BaseFueroAgent):
    """Agente especialista del Fuero Comercial y Consumidor (`05_comercial_consumidor`)."""

    def __init__(self) -> None:
        self._repo = RepoComercial()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.COMERCIAL_CONSUMIDOR

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica actuaciones del expediente comercial/consumidor y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("mandamiento de intimación de pago", "citación de remate", "juicio ejecutivo", "pagaré", "pagare")):
            return (
                "Mandamiento de Intimación de Pago / Ejecución de Pagaré",
                "Orden judicial en un juicio ejecutivo para reclamar una deuda; tenés un plazo muy corto (habitualmente 5 días hábiles) para oponer defensas como la prescripción o que se trata de un pagaré de consumo sin contrato adjunto (Art. 36 LDC).",
                "Arts. 520 a 544 CPCCN / Art. 36 Ley 24.240 y Decreto-Ley 5965/63",
            )
        if any(w in low for w in ("embargo sobre haberes", "decreto 484/87", "traba de embargo", "oficio al empleador")):
            return (
                "Orden de Embargo sobre Haberes / Cuentas",
                "Medida ordenada por el juez para retener una porción del sueldo o fondos bancarios. Si recae sobre haberes, por ley nunca puede tocar el Salario Mínimo ni superar el 10% o 20% del excedente.",
                "Decreto 484/87 y Art. 147 Ley 20.744",
            )
        if any(w in low for w in ("coprec", "defensa del consumidor", "ley 24.240", "daño punitivo", "art. 52 bis")):
            return (
                "Demanda / Reclamo de Defensa del Consumidor",
                "Acción protectoria del usuario o consumidor amparada por el beneficio de justicia gratuita (exenta de tasa de justicia).",
                "Arts. 36, 40, 52 bis y 53 Ley 24.240",
            )
        if any(w in low for w in ("concurso preventivo", "quiebra", "verificación de créditos", "síndico", "sindico")):
            return (
                "Actuación de Concurso Preventivo / Quiebra",
                "Proceso universal donde se convocan todos los acreedores ante el Síndico para verificar sus créditos y reestructurar o liquidar el pasivo.",
                "Ley de Concursos y Quiebras N° 24.522 (Arts. 32 y 200)",
            )
        return (
            "Actuación Procesal Comercial",
            "Trámite o resolución dictada en el marco del proceso mercantil o de consumo.",
            "Código Civil y Comercial, Ley 24.240 y Código Procesal aplicable",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Calcula el límite legal de embargo Decreto 484/87 o la proyección del capital comercial."""
        if max_documented_amount is None:
            return None

        low = combined_text.lower()
        if any(w in low for w in ("sueldo", "haberes", "recibo", "decreto 484")) and max_documented_amount <= Decimal("4000000.00"):
            emb = self._repo.calculate_embargo_sueldo_limit(net_monthly_salary_ars=max_documented_amount)
            return PreliminaryMoneyCalculation(
                concept_description=f"Tope Legal Mensual de Embargo de Haberes ({emb['bracket_applied']})",
                base_amount_ars=max_documented_amount,
                estimated_total_ars=Decimal(str(emb["max_legal_monthly_embargo_ars"])),
                calculation_formula_applied=str(emb["legal_basis"]),
            )

        with_accessories = (max_documented_amount * Decimal("1.30")).quantize(Decimal("0.01"))
        return PreliminaryMoneyCalculation(
            concept_description="Capital Ejecutivo / Reclamo Comercial Documentado (+30% presupuestado p/ intereses y costas)",
            base_amount_ars=max_documented_amount,
            estimated_total_ars=with_accessories,
            calculation_formula_applied=(
                "Capital nominal del título/reclamo + 30% orientativo presupuestado judicialmente para intereses y costas (Art. 531 CPCCN)."
            ),
        )
