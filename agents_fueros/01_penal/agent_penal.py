"""Sub-Agente Especializado del Fuero Penal (`01_penal`).

Analiza actas policiales, declaraciones testimoniales/indagatorias, pedidos de excarcelación,
autos de procesamiento, prisión preventiva y sobreseimientos con trazabilidad estricta a fojas.
"""
from __future__ import annotations

from decimal import Decimal
import importlib

from agents_fueros.base_fuero_agent import BaseFueroAgent, BaseRepositoryConsultant
from core.models import FueroKey, PreliminaryMoneyCalculation

_repo_mod = importlib.import_module("agents_fueros.01_penal.repo_penal")
RepoPenal = _repo_mod.RepoPenal


class AgentPenal(BaseFueroAgent):
    """Agente especialista del Fuero Penal Argentino (`01_penal`)."""

    def __init__(self) -> None:
        self._repo = RepoPenal()

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.PENAL

    @property
    def repository(self) -> BaseRepositoryConsultant:
        return self._repo

    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Clasifica piezas del expediente penal y genera su traducción ciudadana."""
        low = text.lower()
        if any(w in low for w in ("acta de procedimiento", "acta de detenci", "preventivo policial", "comisaría", "comisaria")):
            return (
                "Acta de Procedimiento / Prevención Policial",
                "Registro inicial labrado por la fuerza policial sobre cómo ocurrió el operativo, requisa o detención. Es clave verificar su horario y si firmaron testigos civiles ajenos a la policía.",
                "Arts. 138, 184 y 224 CPPN / Art. 18 Constitución Nacional",
            )
        if any(w in low for w in ("indagatoria", "art. 294", "art 294", "derecho a negarse a declarar")):
            return (
                "Acta de Declaración Indagatoria",
                "Audiencia formal ante el Juez o Fiscal donde se le informa a la persona el hecho que se le atribuye y las pruebas existentes, pudiendo declarar o guardar silencio sin que la perjudique.",
                "Art. 294 CPPN / Art. 18 Constitución Nacional (Garantía de Defensa)",
            )
        if any(w in low for w in ("excarcelaci", "cese de prisi", "morigeraci", "caucion", "caución")):
            return (
                "Incidente de Excarcelación / Libertad durante el Proceso",
                "Trámite urgente donde la defensa solicita que la persona transite el proceso en libertad bajo promesa jurada, caución o arresto domiciliario.",
                "Arts. 316/317 CPPN y Arts. 210, 221 y 222 CPPF (Plenario 'Díaz Bessone')",
            )
        if any(w in low for w in ("procesamiento", "prisión preventiva", "prision preventiva", "embargo penal")):
            return (
                "Auto de Procesamiento / Medida de Coerción",
                "Resolución provisoria donde el juez estima que hay elementos iniciales para avanzar hacia el juicio oral; es apelable ante la Cámara en un plazo estricto de días hábiles.",
                "Arts. 306, 311 y 312 CPPN",
            )
        if any(w in low for w in ("sobreseimiento", "art. 336", "archivo de las actuaciones", "desestimaci")):
            return (
                "Resolución de Sobreseimiento / Archivo",
                "Decisión judicial que cierra definitiva e irrevocablemente la causa a favor del imputado, dejando a salvo su buen nombre y honor.",
                "Arts. 334 a 336 CPPN",
            )
        if any(w in low for w in ("requerimiento de elevaci", "elevación a juicio", "citación a juicio")):
            return (
                "Requerimiento de Elevación a Juicio Oral",
                "Pedido del Fiscal o la Querella para que el caso pase de la etapa de investigación al Tribunal Oral donde se realizará el debate.",
                "Arts. 346 y 347 CPPN",
            )
        return (
            "Actuación de Instrucción / Investigación Penal (IPP)",
            "Diligencia o constancia incorporada al legajo fiscal/judicial en el marco de la investigación penal.",
            "Código Procesal Penal aplicable y Art. 18 CN",
        )

    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Si consta caución real o embargo penal (Art. 518 CPPN), informa el monto nominal documentado."""
        if max_documented_amount is None:
            return None
        return PreliminaryMoneyCalculation(
            concept_description="Caución Real / Embargo Preventivo Penal documentado (Art. 324 / Art. 518 CPPN)",
            base_amount_ars=max_documented_amount,
            estimated_total_ars=max_documented_amount,
            calculation_formula_applied=(
                "Monto nominal fijado en resolución judicial en concepto de caución o embargo para responder "
                "a costas e indemnización civil eventual."
            ),
        )
