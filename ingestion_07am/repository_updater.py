"""Clasificador y Actualizador en Caliente de Repositorios y Calculadoras por Fuero (`repository_updater.py`).

Recibe el lote normativo de las 07:00 AM y:
1. Determina con exactitud qué Sub-Agente(s) de las 7 áreas judiciales (`01_penal` a `07_contencioso_tributario`)
   están alcanzados por cada norma o acordada, inyectándola exclusivamente en el fuero afectado sin contaminar
   el contexto de los demás agentes.
2. Cuando ingresa un índice oficial o parámetro económico (Canasta de Crianza INDEC, Salario Mínimo Vital y Móvil
   SMVM para el tope de embargo del Decreto 484/87, coeficiente RIPTE o índices ICL/IPC), actualiza en caliente
   con precisión `Decimal` las constantes matemáticas de las calculadoras de `02_familia`, `03_laboral`,
   `04_civil` y `05_comercial_consumidor`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import importlib

from agents_fueros.router_fueros import FuerosRouter
from core.models import BoletinOficialNormInput, FueroKey

_mod_familia_repo = importlib.import_module("agents_fueros.02_familia.repo_familia")
_mod_laboral_repo = importlib.import_module("agents_fueros.03_laboral.repo_laboral")
_mod_civil_repo = importlib.import_module("agents_fueros.04_civil.repo_civil")
_mod_comercial_repo = importlib.import_module("agents_fueros.05_comercial_consumidor.repo_comercial")

RepoFamilia = _mod_familia_repo.RepoFamilia
RepoLaboral = _mod_laboral_repo.RepoLaboral
RepoCivil = _mod_civil_repo.RepoCivil
RepoComercial = _mod_comercial_repo.RepoComercial


@dataclass(frozen=True)
class FueroUpdateSummary:
    """Resumen trazable de la inyección normativa y actualización de calculadoras para una norma."""

    norm_id: str
    primary_fuero: FueroKey
    all_fueros_updated: list[FueroKey]
    hot_updated_calculators: list[str] = field(default_factory=list)
    updated_decimal_parameters: dict[str, str] = field(default_factory=dict)


class NormativeRepositoryUpdater:
    """Enrutador normativo matutino e inyector en caliente de constantes matemáticas por fuero."""

    _FUERO_ROUTING_RULES: dict[FueroKey, tuple[str, ...]] = {
        FueroKey.PENAL: (
            "código penal",
            "codigo penal",
            "cppn",
            "cppf",
            "prisión preventiva",
            "prision preventiva",
            "excarcelación",
            "excarcelacion",
            "casación penal",
            "ejecución penal",
            "delito",
            "indagatoria",
        ),
        FueroKey.FAMILIA: (
            "canasta de crianza",
            "cuota alimentaria",
            "alimentos",
            "primera infancia",
            "niñez y la adolescencia",
            "cuidado personal",
            "art. 660",
            "violencia familiar",
            "ley 26.485",
            "divorcio",
        ),
        FueroKey.LABORAL: (
            "salario mínimo",
            "salario minimo",
            "smvm",
            "ripte",
            "ley 20.744",
            "contrato de trabajo",
            "despido",
            "art. 245",
            "riesgos del trabajo",
            "ley 24.557",
            "comisiones médicas",
            "cnepysmv",
        ),
        FueroKey.CIVIL: (
            "contratos de locación",
            "alquiler",
            "icl",
            "ipc",
            "variación mensual del ipc",
            "daños y perjuicios",
            "responsabilidad civil",
            "sucesiones",
            "propiedad horizontal",
            "unidad de medida arancelaria",
            "uma",
        ),
        FueroKey.COMERCIAL_CONSUMIDOR: (
            "bcra",
            "cnv",
            "encajes",
            "mercado de cambios",
            "decreto 484/87",
            "inembargabilidad salarial",
            "defensa del consumidor",
            "ley 24.240",
            "tarjetas de crédito",
            "concursos y quiebras",
            "pagaré",
        ),
        FueroKey.SEGURIDAD_SOCIAL_SALUD: (
            "anses",
            "movilidad jubilatoria",
            "haberes previsionales",
            "obra social",
            "medicina prepaga",
            "pami",
            "discapacidad",
            "ley 24.901",
            "pmo",
            "amparo de salud",
        ),
        FueroKey.CONTENCIOSO_TRIBUTARIO: (
            "arca",
            "afip",
            "aduanero",
            "ejecuciones fiscales",
            "boletas de deuda",
            "impuesto a las ganancias",
            "percepción tributaria",
            "hidrocarburos",
            "vaca muerta",
            "gnl",
            "gasoductos",
            "enargas",
            "enre",
            "tarifaria",
            "procedimiento administrativo",
        ),
    }

    @classmethod
    def classify_affected_fueros(cls, norm: BoletinOficialNormInput) -> list[FueroKey]:
        """Determina exactamente qué áreas judiciales deben recibir la nueva norma o índice."""
        text = f"{norm.norm_id} {norm.title} {norm.full_text_excerpt} {norm.source_organism}".lower()
        scores: dict[FueroKey, int] = {f: 0 for f in FueroKey}

        for fuero, keywords in cls._FUERO_ROUTING_RULES.items():
            for kw in keywords:
                if kw in text:
                    scores[fuero] += 3

        # Reglas explícitas por tipo de norma y parámetros económicos
        if norm.norm_type == "INDICE_INDEC":
            if any(k.startswith("canasta_") for k in norm.economic_parameters_update):
                scores[FueroKey.FAMILIA] += 10
            if "ipc_mensual_pct" in norm.economic_parameters_update:
                scores[FueroKey.CIVIL] += 6
        elif norm.norm_type == "RESOLUCION_SMVM" or "smvm_ars" in norm.economic_parameters_update:
            scores[FueroKey.LABORAL] += 10
            scores[FueroKey.COMERCIAL_CONSUMIDOR] += 8
        elif norm.norm_type in ("COMUNICACION_BCRA", "RESOLUCION_CNV"):
            scores[FueroKey.COMERCIAL_CONSUMIDOR] += 8
            if "icl_bcra" in norm.economic_parameters_update:
                scores[FueroKey.CIVIL] += 6
        elif norm.norm_type in ("RESOLUCION_ARCA", "RESOLUCION_ENERGIA_MINERIA"):
            scores[FueroKey.CONTENCIOSO_TRIBUTARIO] += 10
        elif norm.norm_type == "ACORDADA_CSJN":
            if "prisión preventiva" in text or "penal" in text:
                scores[FueroKey.PENAL] += 6
            scores[FueroKey.CIVIL] += 5

        affected = [f for f, sc in sorted(scores.items(), key=lambda x: x[1], reverse=True) if sc > 0]
        if not affected:
            if norm.source_organism in ("ARCA", "ENARGAS_ENRE"):
                return [FueroKey.CONTENCIOSO_TRIBUTARIO]
            if norm.source_organism in ("BCRA", "CNV"):
                return [FueroKey.COMERCIAL_CONSUMIDOR]
            return [FueroKey.CIVIL]

        # Limitar a los fueros con impacto directo real (máximo 2 cuando comparten índice como SMVM o INDEC)
        top_score = scores[affected[0]]
        filtered = [f for f in affected if scores[f] >= max(3, int(top_score * 0.5))]
        return filtered[:2]

    @classmethod
    def apply_hot_calculator_updates(
        cls,
        norm: BoletinOficialNormInput,
    ) -> tuple[list[str], dict[str, str]]:
        """Actualiza en caliente con precisión Decimal las constantes de las calculadoras oficiales de los fueros."""
        params = norm.economic_parameters_update
        if not params:
            return [], {}

        updated_calculators: list[str] = []
        applied_values: dict[str, str] = {}

        # 1. Actualización en caliente de la Canasta de Crianza INDEC (`02_familia`)
        canasta_brackets_map = {
            "MENOR_1_ANIO": ("canasta_menor_1_bienes_ars", "canasta_menor_1_cuidado_ars"),
            "1_A_3_ANIOS": ("canasta_1_a_3_bienes_ars", "canasta_1_a_3_cuidado_ars"),
            "4_A_5_ANIOS": ("canasta_4_a_5_bienes_ars", "canasta_4_a_5_cuidado_ars"),
            "6_A_12_ANIOS": ("canasta_6_a_12_bienes_ars", "canasta_6_a_12_cuidado_ars"),
        }
        bracket_updates: dict[str, tuple[Decimal, Decimal]] = {}
        for bracket_key, (g_field, c_field) in canasta_brackets_map.items():
            if g_field in params and c_field in params:
                g_dec = Decimal(params[g_field]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                c_dec = Decimal(params[c_field]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                bracket_updates[bracket_key] = (g_dec, c_dec)
                applied_values[g_field] = str(g_dec)
                applied_values[c_field] = str(c_dec)

        if bracket_updates:
            RepoFamilia.update_canasta_crianza_indec(bracket_updates)
            updated_calculators.append("02_familia.RepoFamilia.calculate_canasta_crianza_indec")

        # 2. Actualización en caliente de SMVM y RIPTE (`03_laboral` y `05_comercial_consumidor` Decreto 484/87)
        smvm_dec: Decimal | None = None
        ripte_dec: Decimal | None = None
        if "smvm_ars" in params:
            smvm_dec = Decimal(params["smvm_ars"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            applied_values["smvm_ars"] = str(smvm_dec)
        if "ripte_coeff" in params:
            ripte_dec = Decimal(params["ripte_coeff"]).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            applied_values["ripte_coeff"] = str(ripte_dec)

        if smvm_dec is not None or ripte_dec is not None:
            RepoLaboral.update_laboral_parameters(smvm_ars=smvm_dec, ripte_coeff=ripte_dec)
            updated_calculators.append("03_laboral.RepoLaboral.update_laboral_parameters")
        if smvm_dec is not None:
            RepoComercial.update_smvm_reference(smvm_dec)
            updated_calculators.append("05_comercial_consumidor.RepoComercial.calculate_embargo_sueldo_limit")

        # 3. Actualización en caliente de ICL (BCRA) e IPC (INDEC) (`04_civil`)
        icl_dec: Decimal | None = None
        ipc_dec: Decimal | None = None
        if "icl_bcra" in params:
            icl_dec = Decimal(params["icl_bcra"]).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            applied_values["icl_bcra"] = str(icl_dec)
        if "ipc_mensual_pct" in params:
            ipc_dec = Decimal(params["ipc_mensual_pct"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            applied_values["ipc_mensual_pct"] = str(ipc_dec)

        if icl_dec is not None or ipc_dec is not None:
            RepoCivil.update_civil_indices(icl_bcra=icl_dec, ipc_monthly_pct=ipc_dec)
            updated_calculators.append("04_civil.RepoCivil.calculate_alquiler_update")

        return updated_calculators, applied_values

    @classmethod
    def update_repositories_for_norm(
        cls,
        norm: BoletinOficialNormInput,
        fueros_router: FuerosRouter,
        daily_norm_store: dict[FueroKey, list[BoletinOficialNormInput]],
    ) -> FueroUpdateSummary:
        """Inyecta la norma únicamente en los repositorios de los fueros afectados y actualiza calculadoras."""
        affected_fueros = cls.classify_affected_fueros(norm)
        primary_fuero = affected_fueros[0]

        formatted_norm_entry = (
            f"{norm.norm_id} [{norm.norm_type} - {norm.source_organism}, vigente desde {norm.effective_from.isoformat()}]: "
            f"{norm.title} ({norm.constitutional_hierarchy})"
        )

        for target_fuero in affected_fueros:
            if norm not in daily_norm_store[target_fuero]:
                daily_norm_store[target_fuero].append(norm)
            repo = fueros_router.get_repository(target_fuero)
            repo.inject_normative_update(
                norm_entry=formatted_norm_entry,
                repealed_laws=norm.modifies_or_repeals,
            )

        hot_calcs, applied_decimals = cls.apply_hot_calculator_updates(norm)

        return FueroUpdateSummary(
            norm_id=norm.norm_id,
            primary_fuero=primary_fuero,
            all_fueros_updated=affected_fueros,
            hot_updated_calculators=hot_calcs,
            updated_decimal_parameters=applied_decimals,
        )
