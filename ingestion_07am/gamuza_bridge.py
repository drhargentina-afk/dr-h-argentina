"""Puente de Conocimiento Jurídico y Regulatorio Nacional hacia Proyecto Gamuza (`gamuza_bridge.py`).

ARQUITECTURA DE SEPARACIÓN ESTRICTA DR. H <-> PROYECTO GAMUZA:
- Gamuza JAMÁS recibe ni procesa expedientes privados de ciudadanos.
- Este puente valida determinísticamente que cero datos de usuarios, fojas privadas o tokens de
  anonimización circulen hacia el exterior (`assert_zero_private_data_leakage`).
- Transforma cada norma pública argentina (BORA, InfoLEG, CSJN, BCRA, CNV, ARCA, Energía e INDEC)
  en un objeto `GamuzaRegulatorySignal` con inteligencia geopolítica, recursos estratégicos
  (Vaca Muerta, Litio, Agro), sectores GICS, señal de mercado (`BULLISH` / `BEARISH` / `NEUTRAL`),
  tickers directos de BYMA y NYSE/Wall Street (`YPF`, `YPFD`, `PAM`, `PAMP`, `TGS`, `GGAL`, `BMA`,
  `CEPU`, `TXAR`, `ALUA`) e impacto sobre bonos soberanos (`AL30`, `GD30`, `GD35`).
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Literal
import uuid

from core.models import (
    BoletinOficialNormInput,
    FueroKey,
    GamuzaMarketImpact,
    GamuzaRegulatorySignal,
)


class GamuzaPrivacyViolationError(ValueError):
    """Excepción crítica disparada si se intenta filtrar información de un expediente privado hacia Gamuza."""


class GamuzaKnowledgeBridge:
    """Constructor y despachador del Conocimiento Normativo Nacional hacia Proyecto Gamuza."""

    # Patrones prohibidos en el canal público hacia Gamuza (datos de expedientes privados o PII)
    _FORBIDDEN_PRIVATE_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"\[(?:DNI|CUIL_CUIT|CBU|PERSONA|DOMICILIO|EMAIL|TELEFONO|PATENTE)_\d+\]", re.IGNORECASE),
        re.compile(r"\bgdrive://user/", re.IGNORECASE),
        re.compile(r"\bsess-[a-f0-9]{6,}\b", re.IGNORECASE),
        re.compile(r"\bfoja_ref\b|\bexpediente\s+privado\b", re.IGNORECASE),
        re.compile(r"\b\d{2}-\d{8}-\d\b"),  # CUIT/CUIL privado sin anonimizar
    )

    @classmethod
    def assert_zero_private_data_leakage(cls, norm: BoletinOficialNormInput) -> None:
        """Verifica que la entrada normativa sea 100% pública y no contenga rastros de expedientes privados."""
        inspection_blob = f"{norm.norm_id}\n{norm.title}\n{norm.full_text_excerpt}"
        for pattern in cls._FORBIDDEN_PRIVATE_PATTERNS:
            match = pattern.search(inspection_blob)
            if match:
                raise GamuzaPrivacyViolationError(
                    f"Bloqueo de Aislamiento Dr. H <-> Gamuza: Se detectó un marcador o dato privado "
                    f"('{match.group(0)}') intentando cruzar el puente regulatorio público."
                )

    @classmethod
    def build_regulatory_signal(
        cls,
        norm: BoletinOficialNormInput,
        target_fuero: FueroKey,
        emitted_at: datetime | None = None,
    ) -> GamuzaRegulatorySignal:
        """Construye y valida la señal `GamuzaRegulatorySignal` para Geopolítica y Bolsa de Gamuza."""
        cls.assert_zero_private_data_leakage(norm)

        combined_text = f"{norm.norm_id}\n{norm.title}\n{norm.full_text_excerpt}\n{norm.source_organism}"
        low = combined_text.lower()

        market_impacts: list[GamuzaMarketImpact] = []
        geopolitical_cats: list[str] = []
        strategic_res: list[str] = []
        gics_sectors: list[str] = []
        direct_tickers: list[str] = []

        def _add_unique(target_list: list[str], items: list[str]) -> None:
            for item in items:
                if item not in target_list:
                    target_list.append(item)

        # 1. Sector Energía, Hidrocarburos, Vaca Muerta y Utilities (YPF, PAM, TGS, CEPU)
        if any(
            w in low
            for w in (
                "energía",
                "energia",
                "hidrocarburos",
                "vaca muerta",
                "gnl",
                "gas",
                "petróleo",
                "petroleo",
                "gasoducto",
                "tarifa eléctrica",
                "enargas",
                "enre",
            )
        ):
            is_expansive = any(
                w in low
                for w in (
                    "libre export",
                    "desregul",
                    "estabilidad fiscal",
                    "libre disponibilidad",
                    "actualización tarifaria",
                    "inversión",
                    "expansión",
                )
            )
            direction: Literal["DESREGULACION_EXPANSIVA", "RESTRICCION_COSTO_MAYOR", "NEUTRAL_PROCEDIMENTAL"] = (
                "DESREGULACION_EXPANSIVA" if is_expansive else "RESTRICCION_COSTO_MAYOR"
            )
            energy_tickers = ["YPF", "YPFD", "PAM", "PAMP", "TGS", "TGSU2", "CEPU", "VISTA", "TRAN", "EDN"]
            market_impacts.append(
                GamuzaMarketImpact(
                    economic_sector="ENERGIA_OIL_GAS",
                    related_byma_wallstreet_tickers=energy_tickers,
                    regulatory_direction=direction,
                    severity_score=9 if is_expansive else 8,
                    rationale=(
                        f"Impacto regulatorio directo de {norm.norm_id} ({norm.source_organism}) sobre "
                        f"exportación de hidrocarburos/GNL, transporte troncal y tarifas energéticas."
                    ),
                )
            )
            _add_unique(geopolitical_cats, ["ENERGIA_SOBERANA", "SEGURIDAD_JURIDICA_INVERSORA", "INFRAESTRUCTURA_EXPORTADORA"])
            _add_unique(strategic_res, ["VACA_MUERTA_HIDROCARBUROS", "GASODUCTOS_GNL"])
            _add_unique(gics_sectors, ["Energy (GICS 10)", "Utilities (GICS 55)"])
            _add_unique(direct_tickers, ["YPF", "YPFD", "PAM", "PAMP", "TGS", "TGSU2", "CEPU"])

        # 2. Sector Bancos, Mercado de Capitales y Política Monetaria (BCRA / CNV -> GGAL, BMA, BBAR, SUPV)
        if any(
            w in low
            for w in (
                "bcra",
                "cnv",
                "encaje",
                "tasa de interés",
                "bancos",
                "mercado de cambios",
                "cambiario",
                "crédito",
                "icl",
                "lefi",
                "bonos",
            )
        ):
            is_expansive = any(
                w in low
                for w in (
                    "flexibiliz",
                    "reduce los encajes",
                    "reduce encaje",
                    "elimina restricci",
                    "habilita",
                    "expansión crediticia",
                )
            )
            direction = "DESREGULACION_EXPANSIVA" if is_expansive else "RESTRICCION_COSTO_MAYOR"
            bank_tickers = ["GGAL", "BMA", "BBAR", "SUPV", "BYMA", "VALO"]
            market_impacts.append(
                GamuzaMarketImpact(
                    economic_sector="BANCOS_FINANZAS",
                    related_byma_wallstreet_tickers=bank_tickers,
                    regulatory_direction=direction,
                    severity_score=8,
                    rationale=(
                        f"Disposición monetaria/cambiaria {norm.norm_id} ({norm.source_organism}) con impacto directo "
                        f"en liquidez bancaria, spread de tasas y operatoria de mercado de capitales."
                    ),
                )
            )
            _add_unique(geopolitical_cats, ["POLITICA_MONETARIA_CAMBIARIA", "MERCADO_DE_CAPITALES"])
            _add_unique(strategic_res, ["SISTEMA_FINANCIERO_BCRA", "RESERVAS_INTERNACIONALES"])
            _add_unique(gics_sectors, ["Financials (GICS 40)"])
            _add_unique(direct_tickers, ["GGAL", "BMA", "BBAR", "SUPV", "BYMA"])

        # 3. Sector Industria, Siderurgia, Aluminio y Litio/Minería (TXAR, ALUA, LOMA)
        if any(
            w in low
            for w in (
                "siderúrgic",
                "siderurgic",
                "acero",
                "aluminio",
                "litio",
                "minería",
                "mineria",
                "industrial",
                "percepción tributaria",
                "arancel fiscal",
            )
        ):
            is_restrictive = any(
                w in low
                for w in (
                    "aumento de percepción",
                    "incrementa la alícuota",
                    "mayores controles",
                    "recargo",
                    "retención",
                )
            )
            direction = "RESTRICCION_COSTO_MAYOR" if is_restrictive else "DESREGULACION_EXPANSIVA"
            ind_tickers = ["TXAR", "ALUA", "LOMA"]
            sector_key: Literal["MINERIA_LITIO_MATERIALES", "INDUSTRIA_CONSTRUCCION"] = (
                "MINERIA_LITIO_MATERIALES" if "litio" in low or "miner" in low else "INDUSTRIA_CONSTRUCCION"
            )
            market_impacts.append(
                GamuzaMarketImpact(
                    economic_sector=sector_key,
                    related_byma_wallstreet_tickers=ind_tickers,
                    regulatory_direction=direction,
                    severity_score=7,
                    rationale=(
                        f"Norma fiscal/industrial {norm.norm_id} ({norm.source_organism}) que altera la carga "
                        f"impositiva o arancelaria sobre materiales básicos, acero y aluminio."
                    ),
                )
            )
            _add_unique(geopolitical_cats, ["POLITica_TRIBUTARIA_INDUSTRIAL", "CADENAS_SUMINISTRO_MATERIALES"])
            if "litio" in low or "miner" in low:
                _add_unique(strategic_res, ["LITIO_MINERIA_CRITICA"])
            else:
                _add_unique(strategic_res, ["ACERO_ALUMINIO_INDUSTRIA"])
            _add_unique(gics_sectors, ["Materials (GICS 15)", "Industrials (GICS 20)"])
            _add_unique(direct_tickers, ["TXAR", "ALUA", "LOMA"])

        # 4. Sector Agroexportador (CRES, AGRO, MOLI, SEMI)
        if any(w in low for w in ("retenciones", "derechos de exportación", "agro", "granos", "soja", "maíz", "trigo", "senasa")):
            is_expansive = any(w in low for w in ("baja de retenciones", "reducción", "elimina", "alícuota 0"))
            direction = "DESREGULACION_EXPANSIVA" if is_expansive else "RESTRICCION_COSTO_MAYOR"
            agro_tickers = ["CRES", "AGRO", "MOLI", "SEMI"]
            market_impacts.append(
                GamuzaMarketImpact(
                    economic_sector="AGRO_EXPORTACION",
                    related_byma_wallstreet_tickers=agro_tickers,
                    regulatory_direction=direction,
                    severity_score=7,
                    rationale=f"Modificación regulatoria agroexportadora dispuesta por {norm.norm_id}.",
                )
            )
            _add_unique(geopolitical_cats, ["COMERCIO_EXTERIOR_AGRO", "BALANZA_COMERCIAL"])
            _add_unique(strategic_res, ["AGRO_BIOTECNOLOGIA_GRANOS"])
            _add_unique(gics_sectors, ["Consumer Staples (GICS 30)"])
            _add_unique(direct_tickers, agro_tickers)

        # 5. Fallback Macroeconómico / Institucional (INDEC, SMVM, CSJN)
        if not market_impacts:
            is_expansive = any(w in low for w in ("simplific", "deroga", "desregul", "promoción", "desinflación"))
            direction = "DESREGULACION_EXPANSIVA" if is_expansive else "NEUTRAL_PROCEDIMENTAL"
            fallback_tickers = ["YPF", "GGAL", "BMA", "PAM", "TGS", "CEPU", "TXAR", "ALUA"]
            market_impacts.append(
                GamuzaMarketImpact(
                    economic_sector="MACRO_IMPUESTOS_GENERAL",
                    related_byma_wallstreet_tickers=fallback_tickers,
                    regulatory_direction=direction,
                    severity_score=5,
                    rationale=(
                        f"Actualización normativa o paramétrica oficial ({norm.norm_id} - {norm.source_organism}) "
                        f"registrada en el fuero {target_fuero.value}."
                    ),
                )
            )
            _add_unique(geopolitical_cats, ["INSTITUCIONALIDAD_NORMATIVA", "PARAMETROS_MACROECONOMICOS"])
            _add_unique(strategic_res, ["SEGURIDAD_JURIDICA_NACIONAL"])
            _add_unique(gics_sectors, ["Multi-Sector Sovereign (MERVAL/BYMA)"])
            _add_unique(direct_tickers, fallback_tickers)

        # Cálculo determinista de señal de mercado agregada (BULLISH / BEARISH / NEUTRAL) y Shock Score
        max_shock = max(m.severity_score for m in market_impacts)
        expansive_count = sum(1 for m in market_impacts if m.regulatory_direction == "DESREGULACION_EXPANSIVA")
        restrictive_count = sum(1 for m in market_impacts if m.regulatory_direction == "RESTRICCION_COSTO_MAYOR")

        if expansive_count > restrictive_count:
            overall_signal: Literal["BULLISH", "BEARISH", "NEUTRAL"] = "BULLISH"
            bonds_impact = (
                "POSITIVO_COMPRESION_RIESGO_PAIS: Fortalece generación de divisas o liquidez para bonos soberanos "
                "Hard-Dollar (AL30, GD30, AL35, GD35)."
            )
        elif restrictive_count > expansive_count:
            overall_signal = "BEARISH"
            bonds_impact = (
                "CAUTELA_COSTO_REGULATORIO: Mayor presión impositiva/regulatoria sectorial; impacto neutro-defensivo "
                "sobre curva soberana (AL30/GD30) y bonos CER."
            )
        else:
            overall_signal = "NEUTRAL"
            bonds_impact = (
                "NEUTRAL_CURVA_SOBERANA: Actualización de índices oficiales (INDEC/SMVM/CSJN) computable para "
                "instrumentos CER/UVA sin alteración directa de paridades Hard-Dollar."
            )

        ts_iso = (emitted_at or datetime.now(timezone.utc)).isoformat(timespec="seconds")

        return GamuzaRegulatorySignal(
            signal_id=f"gam-ar-{uuid.uuid4().hex[:10]}",
            emitted_at_iso=ts_iso,
            origin_node="DR_H_AR",
            country_code="AR",
            norm_id=norm.norm_id,
            norm_type=norm.norm_type,
            source_organism=norm.source_organism,
            target_fuero_updated=target_fuero,
            regulatory_shock_score=max_shock,
            geopolitical_categories=geopolitical_cats,
            strategic_resources=strategic_res,
            market_signal=overall_signal,
            gics_sectors_affected=gics_sectors,
            direct_tickers_byma_nyse=direct_tickers,
            sovereign_bonds_impact=bonds_impact,
            geopolitical_institutional_summary=(
                f"[Nodo AR -> Gamuza] Publicación oficial de {norm.norm_id} ({norm.norm_type}) por {norm.source_organism}: "
                f"'{norm.title}' (vigencia: {norm.effective_from.isoformat()}). "
                f"Jerarquía: {norm.constitutional_hierarchy}. "
                f"Modifica/Deroga: {', '.join(norm.modifies_or_repeals) if norm.modifies_or_repeals else 'Sin derogaciones expresas'}."
            ),
            market_and_equities_impacts=market_impacts,
            privacy_isolation_verified=True,
        )
