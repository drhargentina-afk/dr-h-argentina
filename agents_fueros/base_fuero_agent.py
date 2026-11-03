"""Clase base abstracta para los 7 Sub-Agentes de Fuero y sus Consultores de Repositorios.

Provee herramientas deterministas comunes:
- Extracción de fechas y montos en ARS con precisión `Decimal` foja por foja.
- Construcción trazable de `FojaCitation` asegurando que `foja_ref` coincida con el número real de foja.
- Validador de Vigencia Normativa (`validate_citations`) que detecta leyes derogadas, sustituidas
  o inexistentes en escritos judiciales argentinos (ej. Ley 27.551 de Alquileres derogada por DNU 70/2023,
  multas Ley 24.013 arts. 8-15 y Ley 25.323 derogadas por Ley 27.742, Código Civil de Vélez Sarsfield
  Ley 340 derogado por Ley 26.994).
- Contrato estándar para el Traductor de "Problema Real (Criollo) a Ley" y el "Espejo de Fallos y Tiempos Reales".
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re

from core.models import (
    CaseContext,
    FojaCitation,
    FojaPageContent,
    FueroAnalysisResult,
    FueroKey,
    PreliminaryMoneyCalculation,
)


@dataclass(frozen=True)
class CitationValidationReport:
    """Resultado de auditar la vigencia temporal de leyes citadas en un texto o escrito."""

    valid_laws_detected: list[str]
    repealed_or_modified_warnings: list[str]
    hallucinated_or_unknown_warnings: list[str]
    is_fully_current: bool


@dataclass(frozen=True)
class RealCourtMirrorInfo:
    """Espejo de jurisprudencia real y tiempos promedio de tribunales argentinos por tema."""

    topic_key: str
    how_courts_actually_rule: str
    leading_cases: list[str]
    average_duration_months_range: tuple[int, int]
    anti_lie_warning_for_citizen: str


class BaseRepositoryConsultant(ABC):
    """Interfaz base para el Consultor de Repositorios de cada una de las 7 áreas judiciales."""

    # Catálogo transversal de normas derogadas o reformadas frecuentemente mal citadas en escritos
    _COMMON_REPEALED_LAWS: dict[str, str] = {
        "ley 27.551": (
            "DEROGADA: La Ley 27.551 (y su modificatoria 27.737) de Alquileres fue derogada por el Art. 249 del "
            "DNU 70/2023 a partir del 29/12/2023. Solo rige para contratos firmados antes de esa fecha; los nuevos "
            "se rigen por los Arts. 1187 a 1226 del CCyCN reformado (libertad de índice y plazo)."
        ),
        "ley 27551": (
            "DEROGADA: La Ley 27.551 de Alquileres fue derogada por el DNU 70/2023 (vigente desde 29/12/2023)."
        ),
        "ley 27.737": (
            "DEROGADA: La Ley 27.737 de Alquileres fue derogada por el DNU 70/2023."
        ),
        "ley 25.323": (
            "DEROGADA: La Ley 25.323 (duplicación de indemnizaciones por falta de registración o mora) fue derogada "
            "por el Art. 100 de la Ley 27.742 (Ley Bases, B.O. 08/07/2024)."
        ),
        "ley 25323": (
            "DEROGADA: La Ley 25.323 fue derogada por el Art. 100 de la Ley 27.742."
        ),
        "art. 8 ley 24.013": (
            "DEROGADO: Los Arts. 8 a 17 de la Ley 24.013 (multas por empleo no registrado) fueron derogados por el "
            "Art. 99 de la Ley 27.742 (vigente desde julio 2024)."
        ),
        "art. 80 lct multa": (
            "REFORMADO: La indemnización/multa del Art. 80 LCT (incorporada por Art. 45 Ley 25.345) fue derogada "
            "por la Ley 27.742, aunque subsiste la obligación de entregar los certificados de trabajo."
        ),
        "ley 340": (
            "DEROGADA: El antiguo Código Civil de Vélez Sarsfield (Ley 340) fue derogado el 01/08/2015 por la "
            "Ley 26.994 (Código Civil y Comercial de la Nación)."
        ),
        "ley 19.551": (
            "DEROGADA: La antigua Ley de Concursos 19.551 fue reemplazada por la Ley 24.522 de Concursos y Quiebras."
        ),
        "ley 2.393": (
            "DEROGADA: La antigua Ley de Matrimonio Civil 2.393 y la Ley 23.515 están derogadas; rigen los Arts. "
            "401 a 445 del Código Civil y Comercial de la Nación (divorcio incausado)."
        ),
    }

    _RE_LAW_NUMBER = re.compile(r"\b[Ll]ey\s*(?:[Nn]acional\s*)?[Nn°º.]*\s*(\d{1,2}\.?\d{3})\b")

    def inject_normative_update(
        self,
        norm_entry: str,
        repealed_laws: list[str] | None = None,
    ) -> None:
        """Inyecta en caliente una nueva norma matutina (07:00 AM) en el repositorio del fuero."""
        if not hasattr(self, "_dynamic_ingested_norms"):
            self._dynamic_ingested_norms: list[str] = []
        if norm_entry not in self._dynamic_ingested_norms:
            self._dynamic_ingested_norms.append(norm_entry)
        if repealed_laws:
            for rep in repealed_laws:
                rep_key = rep.lower().strip()
                if rep_key:
                    self._COMMON_REPEALED_LAWS[rep_key] = (
                        f"MODIFICADA/DEROGADA EN INGESTA 07:00 AM: '{rep}' fue alcanzada por {norm_entry}."
                    )

    def get_ingested_norms(self) -> list[str]:
        """Devuelve la lista de normas inyectadas en caliente desde las 07:00 AM."""
        return list(getattr(self, "_dynamic_ingested_norms", []))

    @property
    @abstractmethod
    def fuero_key(self) -> FueroKey:
        """Devuelve la clave del fuero al que pertenece este repositorio."""

    @property
    @abstractmethod
    def current_normative_catalog(self) -> list[str]:
        """Listado de normas vigentes verificadas del fuero."""

    @property
    @abstractmethod
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        """Mapa de frases cotidianas del ciudadano a encuadre legal, artículo y consejo inmediato."""

    @property
    @abstractmethod
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        """Catálogo de espejo de fallos y tiempos reales de resolución por subtema."""

    def translate_criollo_problem(self, citizen_query: str) -> dict[str, object]:
        """Traduce una consulta en lenguaje cotidiano a la ley exacta y al espejo de fallos real."""
        q_low = citizen_query.lower().strip()
        best_match_key: str | None = None
        best_score = 0

        for key, data in self.criollo_to_law_map.items():
            keywords = [w.strip() for w in key.lower().split("|") if w.strip()]
            score = sum(2 if kw in q_low else 0 for kw in keywords)
            if score > best_score:
                best_score = score
                best_match_key = key

        if best_match_key is None:
            # Tomar la primera entrada representativa del fuero como guía base
            first_key = next(iter(self.criollo_to_law_map.keys()))
            matched_data = self.criollo_to_law_map[first_key]
        else:
            matched_data = self.criollo_to_law_map[best_match_key]

        mirror_topic = matched_data.get("mirror_topic", "")
        mirror_info = self.court_mirror_catalog.get(mirror_topic) or next(
            iter(self.court_mirror_catalog.values())
        )

        return {
            "fuero": self.fuero_key.value,
            "criollo_situation_detected": matched_data.get("situation", ""),
            "applicable_law_and_article": matched_data.get("law_article", ""),
            "plain_explanation": matched_data.get("explanation", ""),
            "immediate_citizen_protection": matched_data.get("protection_tip", ""),
            "court_mirror": {
                "how_courts_rule": mirror_info.how_courts_actually_rule,
                "leading_cases": mirror_info.leading_cases,
                "average_duration_months": list(mirror_info.average_duration_months_range),
                "anti_lie_warning": mirror_info.anti_lie_warning_for_citizen,
            },
        }

    def validate_citations(self, legal_text: str, reference_date: date | None = None) -> CitationValidationReport:
        """Audita un escrito o texto detectando normas derogadas o números de ley inverosímiles."""
        if not isinstance(legal_text, str):
            raise TypeError("legal_text debe ser una cadena de texto.")

        low = legal_text.lower()
        valid_found: list[str] = []
        repealed_warnings: list[str] = []
        unknown_warnings: list[str] = []

        # 1. Revisar catálogo de normas derogadas o reformadas (considerando fecha histórica si se suministra)
        for pattern_key, warning_desc in self._COMMON_REPEALED_LAWS.items():
            if pattern_key in low:
                # Si la consulta es sobre hechos anteriores a la derogación, aclararlo
                if reference_date is not None and reference_date < date(2023, 12, 29) and "27.551" in pattern_key:
                    valid_found.append(f"Ley 27.551 (vigente en la fecha histórica consultada {reference_date.isoformat()})")
                elif reference_date is not None and reference_date < date(2024, 7, 8) and (
                    "25.323" in pattern_key or "24.013" in pattern_key
                ):
                    valid_found.append(
                        f"{pattern_key.upper()} (aplicable por ultraactividad temporal a hechos del {reference_date.isoformat()})"
                    )
                else:
                    repealed_warnings.append(warning_desc)

        # 2. Extraer todas las menciones a "Ley XX.XXX" y validar rango histórico argentino (Ley 1 a Ley 27.850)
        for match in self._RE_LAW_NUMBER.finditer(legal_text):
            raw_num = match.group(1)
            digits_only = int(raw_num.replace(".", ""))
            if digits_only > 28500 or digits_only <= 0:
                unknown_warnings.append(
                    f"ALERTA DE CITA INEXISTENTE: Se cita 'Ley {raw_num}', cuyo número excede la numeración "
                    f"histórica del Congreso de la Nación Argentina (posible alucinación o error de tipeo)."
                )
            else:
                norm_label = f"Ley {raw_num}"
                if norm_label not in valid_found and not any(raw_num in r for r in repealed_warnings):
                    valid_found.append(norm_label)

        is_current = len(repealed_warnings) == 0 and len(unknown_warnings) == 0
        return CitationValidationReport(
            valid_laws_detected=valid_found,
            repealed_or_modified_warnings=repealed_warnings,
            hallucinated_or_unknown_warnings=unknown_warnings,
            is_fully_current=is_current,
        )


class BaseFueroAgent(ABC):
    """Clase base para los 7 Sub-Agentes de Fuero convocados en la Fase 2 de Agente000."""

    _RE_DATE = re.compile(
        r"\b(?:(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})|(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2}))\b"
    )
    _RE_MONEY_ARS = re.compile(r"\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{4,12}(?:\.\d{1,2})?)")

    @property
    @abstractmethod
    def fuero_key(self) -> FueroKey:
        """Identificador del fuero."""

    @property
    @abstractmethod
    def repository(self) -> BaseRepositoryConsultant:
        """Instancia del Consultor de Repositorios asociado a este fuero."""

    @abstractmethod
    def classify_fuero_piece(self, text: str) -> tuple[str, str, str]:
        """Devuelve (tipo_pieza, traduccion_ciudadana, norma_especifica_aplicada) para una foja."""

    @abstractmethod
    def build_specialized_calculation(
        self,
        combined_text: str,
        max_documented_amount: Decimal | None,
    ) -> PreliminaryMoneyCalculation | None:
        """Construye un cálculo determinista con Decimal propio del fuero si aplica."""

    def extract_dates_from_text(self, text: str) -> list[date]:
        """Extrae todas las fechas válidas (YYYY-MM-DD o DD/MM/YYYY) presentes en una foja."""
        dates: list[date] = []
        for match in self._RE_DATE.finditer(text):
            try:
                if match.group(1):
                    d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
                else:
                    y, m, d = int(match.group(4)), int(match.group(5)), int(match.group(6))
                dt = date(y, m, d)
                if 1990 <= dt.year <= 2030:
                    dates.append(dt)
            except ValueError:
                continue
        return dates

    def extract_max_amount_from_text(self, text: str) -> Decimal | None:
        """Extrae el mayor importe monetario expresado en pesos ($) con precisión Decimal."""
        found: list[Decimal] = []
        for match in self._RE_MONEY_ARS.finditer(text):
            raw = match.group(1).strip()
            if "," in raw and "." in raw:
                norm = raw.replace(".", "").replace(",", ".")
            elif "." in raw and len(raw.split(".")[-1]) == 3:
                norm = raw.replace(".", "")
            else:
                norm = raw.replace(",", ".")
            try:
                dec = Decimal(norm).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if dec > Decimal("0.00"):
                    found.append(dec)
            except Exception:
                continue
        return max(found) if found else None

    def analyze_case(
        self,
        context: CaseContext,
        province_rules: dict[str, object],
    ) -> FueroAnalysisResult:
        """Procesa las fojas útiles del expediente y emite el FueroAnalysisResult trazable."""
        useful_fojas: list[FojaPageContent] = [
            f for f in context.fojas_content if not f.is_discarded_hygiene
        ]
        if not useful_fojas and context.fojas_content:
            useful_fojas = [context.fojas_content[0]]

        citations: list[FojaCitation] = []
        dates_by_foja: dict[int, str] = {}
        all_dates: list[date] = []
        max_amount: Decimal | None = None
        full_corpus_lines: list[str] = []

        for foja in useful_fojas:
            full_corpus_lines.append(foja.cleaned_text)
            piece_type, citizen_translation, specific_norm = self.classify_fuero_piece(foja.cleaned_text)

            excerpt = foja.cleaned_text[:250].strip()
            if len(excerpt) < 5:
                excerpt = f"Constancia procesal verificada en Foja {foja.foja_number}."

            f_dates = self.extract_dates_from_text(foja.cleaned_text)
            if f_dates:
                latest_f = max(f_dates)
                dates_by_foja[foja.foja_number] = latest_f.isoformat()
                all_dates.extend(f_dates)

            amt = self.extract_max_amount_from_text(foja.cleaned_text)
            if amt is not None and (max_amount is None or amt > max_amount):
                max_amount = amt

            citations.append(
                FojaCitation(
                    foja_ref=foja.foja_number,
                    document_piece_type=piece_type,
                    verbatim_excerpt=excerpt,
                    citizen_plain_translation=citizen_translation,
                    normative_ref=specific_norm,
                )
            )

        combined_text = "\n".join(full_corpus_lines)
        citation_audit = self.repository.validate_citations(combined_text)

        applied_laws = list(self.repository.current_normative_catalog)
        applied_laws.extend(self.repository.get_ingested_norms())
        if citation_audit.repealed_or_modified_warnings:
            applied_laws.extend(citation_audit.repealed_or_modified_warnings)

        prelim_calc = self.build_specialized_calculation(combined_text, max_amount)
        last_act_date = max(all_dates) if all_dates else None

        prov_name = str(province_rules.get("name", context.province_jurisdiction.value))
        elec_sys = str(province_rules.get("electronic_system", "Portal Judicial"))
        nota_rule = str(province_rules.get("nota_rule", "Días de nota martes y viernes."))
        grace_rule = str(province_rules.get("grace_period_hours", "Dos primeras horas hábiles."))

        deadlines_summary = (
            f"[{self.fuero_key.value.upper()} - {prov_name}] Sistema: {elec_sys}. "
            f"{nota_rule} Plazo de gracia: {grace_rule}"
        )

        mirror_sample = next(iter(self.repository.court_mirror_catalog.values()))
        min_m, max_m = mirror_sample.average_duration_months_range

        diagnosis = (
            f"Dictamen Especializado ({self.fuero_key.value}): Se auditaron {len(citations)} fojas útiles "
            f"en jurisdicción {prov_name}. {mirror_sample.how_courts_actually_rule} "
            f"Tiempo promedio real en tribunales para este tipo de proceso: {min_m} a {max_m} meses. "
            f"{mirror_sample.anti_lie_warning_for_citizen}"
        )

        return FueroAnalysisResult(
            fuero_agent_id=self.fuero_key,
            normative_framework=[
                "Constitución de la Nación Argentina (Arts. 14 bis, 18, 42, 43 y 75 inc. 22)",
                str(province_rules.get("procedural_code", "Código Procesal aplicable")),
            ],
            applied_current_laws=applied_laws,
            foja_citations=citations,
            procedural_deadlines_summary=deadlines_summary,
            last_documented_act_date=last_act_date,
            detected_dates_by_foja=dates_by_foja,
            preliminary_calculation=prelim_calc,
            technical_diagnosis=diagnosis,
        )
