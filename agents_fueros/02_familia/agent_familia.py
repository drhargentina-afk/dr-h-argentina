"""Sub-Agente Especializado del Fuero de Familia (`02_familia`).

Analiza expedientes de alimentos, cuidado personal, régimen de comunicación,
medidas protectorias por violencia familiar y divorcios/compensación económica.
"""
from __future__ import annotations

from decimal import Decimal
import importlib
import re

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.02_familia.repo_familia")
RepoFamilia = _repo_mod.RepoFamilia


class AgentFamilia(BaseFueroAgent):
    """Agente especialista del Fuero de Familia Argentino (`02_familia`)."""

    _RE_CHILD_AGE = re.compile(r"\b(\d{1,2})\s+a[ñn]os\s+de\s+edad\b", re.IGNORECASE)

    def __init__(self) -> None:
        self._repo = RepoFamilia()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.FAMILIA

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica actuaciones del expediente de familia y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("alimentos provisorios", "art. 544", "cuota provisoria", "retención directa")):
            return (
                "Resolución de Alimentos Provisorios",
                "Orden urgente del juez fijando una cuota alimentaria inmediata mientras dura el juicio, para cubrir las necesidades diarias de los hijos sin esperar la sentencia final.",
                "Arts. 544, 658 y 660 Código Civil y Comercial de la Nación",
            )
        if any(w in low for w in ("prohibición de acercamiento", "perimetral", "exclusión del hogar", "ley 26.485", "ovd")):
            return (
                "Medida Cautelar de Protección / Perimetral",
                "Orden judicial de protección inmediata que prohíbe al denunciado acercarse o tomar contacto por cualquier vía, bajo apercibimiento de delito de desobediencia penal (Art. 239 CP).",
                "Arts. 26 y 27 Ley 26.485 / Ley 24.417",
            )
        if any(w in low for w in ("plan de parentalidad", "cuidado personal", "régimen de comunicación", "regimen de comunicacion")):
            return (
                "Actuación sobre Cuidado Personal y Régimen de Comunicación",
                "Trámite o acuerdo que organiza con quién conviven los hijos, los días y horarios de contacto, vacaciones y distribución de tareas de cuidado.",
                "Arts. 648 a 656 Código Civil y Comercial de la Nación",
            )
        if any(w in low for w in ("audiencia art. 636", "consejero de familia", "etapa previa", "defensor de menores")):
            return (
                "Audiencia de Familia / Dictamen del Ministerio Público de Menores",
                "Instancia obligatoria donde interviene el Consejero de Familia o el Defensor de Menores para resguardar el interés superior de los niños involucrados.",
                "Art. 103 y 706 CCyCN / Ley 26.061",
            )
        if any(w in low for w in ("divorcio", "convenio regulador", "compensación económica", "compensacion economica")):
            return (
                "Petición / Sentencia de Divorcio y Convenio Regulador",
                "Trámite de disolución del vínculo matrimonial y propuesta sobre vivienda, bienes y eventual compensación económica.",
                "Arts. 437 a 445 Código Civil y Comercial de la Nación",
            )
        return (
            "Actuación Procesal de Familia",
            "Constancia incorporada al expediente de familia bajo principios de tutela judicial efectiva e inmediación.",
            "Arts. 706 a 711 Código Civil y Comercial de la Nación",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Calcula el piso de cuota alimentaria según Canasta de Crianza INDEC para los hijos detectados."""
        detected_ages: list[int] = []
        for match in self._RE_CHILD_AGE.finditer(combined_text):
            age_val = int(match.group(1))
            if 0 <= age_val <= 18:
                detected_ages.append(age_val)

        if not detected_ages:
            detected_ages = [7]  # Edad promedio representativa (tramo 6 a 12 años INDEC)

        indec_res = self._repo.calculate_canasta_crianza_indec(
            children_ages=detected_ages,
            non_conviviente_share_pct=Decimal("55.00"),
        )
        total_canasta = Decimal(str(indec_res["total_canasta_crianza_indec_ars"]))
        suggested_quota = Decimal(str(indec_res["suggested_monthly_quota_ars"]))

        if max_documented_amount is not None and max_documented_amount > suggested_quota:
            suggested_quota = max_documented_amount

        return PreliminaryMoneyCalculation(
            concept_description=(
                f"Piso Alimentario Canasta de Crianza INDEC ({len(detected_ages)} hijo/s, edades: {detected_ages})"
            ),
            base_amount_ars=total_canasta,
            estimated_total_ars=suggested_quota,
            calculation_formula_applied=(
                "Suma de Costo de Bienes y Servicios INDEC + Costo de Cuidado (Art. 660 CCyCN) por tramo de edad, "
                "aplicando 55% de aporte líquido al progenitor no conviviente."
            ),
        )
