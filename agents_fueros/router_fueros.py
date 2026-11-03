"""Router Central de los 7 Sub-Agentes de Fuero y sus Consultores de Repositorios (`router_fueros.py`).

Conecta directamente la Primera Derivación de `Agente000Orchestrator` (Fase 2) y el endpoint
`/repository-consult` con las 7 áreas judiciales especializadas de Argentina:
- `01_penal`                   -> `AgentPenal` / `RepoPenal`
- `02_familia`                 -> `AgentFamilia` / `RepoFamilia`
- `03_laboral`                 -> `AgentLaboral` / `RepoLaboral`
- `04_civil`                   -> `AgentCivil` / `RepoCivil`
- `05_comercial_consumidor`    -> `AgentComercial` / `RepoComercial`
- `06_seguridad_social_salud`  -> `AgentSeguridadSocial` / `RepoSeguridadSocial`
- `07_contencioso_tributario`  -> `AgentContencioso` / `RepoContencioso`
"""
from __future__ import annotations

from datetime import date
import importlib

from agents_fueros.base_fuero_agent import (
    BaseFueroAgent,
    BaseRepositoryConsultant,
    CitationValidationReport,
)
from core.models import CaseContext, FueroAnalysisResult, FueroKey

# Importación explícita de los 7 paquetes numerados
_mod_penal_agent = importlib.import_module("agents_fueros.01_penal.agent_penal")
_mod_penal_repo = importlib.import_module("agents_fueros.01_penal.repo_penal")

_mod_familia_agent = importlib.import_module("agents_fueros.02_familia.agent_familia")
_mod_familia_repo = importlib.import_module("agents_fueros.02_familia.repo_familia")

_mod_laboral_agent = importlib.import_module("agents_fueros.03_laboral.agent_laboral")
_mod_laboral_repo = importlib.import_module("agents_fueros.03_laboral.repo_laboral")

_mod_civil_agent = importlib.import_module("agents_fueros.04_civil.agent_civil")
_mod_civil_repo = importlib.import_module("agents_fueros.04_civil.repo_civil")

_mod_comercial_agent = importlib.import_module("agents_fueros.05_comercial_consumidor.agent_comercial")
_mod_comercial_repo = importlib.import_module("agents_fueros.05_comercial_consumidor.repo_comercial")

_mod_segsoc_agent = importlib.import_module("agents_fueros.06_seguridad_social_salud.agent_seguridad_social")
_mod_segsoc_repo = importlib.import_module("agents_fueros.06_seguridad_social_salud.repo_seguridad_social")

_mod_contencioso_agent = importlib.import_module("agents_fueros.07_contencioso_tributario.agent_contencioso")
_mod_contencioso_repo = importlib.import_module("agents_fueros.07_contencioso_tributario.repo_contencioso")

AgentPenal = _mod_penal_agent.AgentPenal
RepoPenal = _mod_penal_repo.RepoPenal

AgentFamilia = _mod_familia_agent.AgentFamilia
RepoFamilia = _mod_familia_repo.RepoFamilia

AgentLaboral = _mod_laboral_agent.AgentLaboral
RepoLaboral = _mod_laboral_repo.RepoLaboral

AgentCivil = _mod_civil_agent.AgentCivil
RepoCivil = _mod_civil_repo.RepoCivil

AgentComercial = _mod_comercial_agent.AgentComercial
RepoComercial = _mod_comercial_repo.RepoComercial

AgentSeguridadSocial = _mod_segsoc_agent.AgentSeguridadSocial
RepoSeguridadSocial = _mod_segsoc_repo.RepoSeguridadSocial

AgentContencioso = _mod_contencioso_agent.AgentContencioso
RepoContencioso = _mod_contencioso_repo.RepoContencioso


class FuerosRouter:
    """Enrutador determinista hacia los 7 Sub-Agentes de Fuero y sus Consultores de Repositorios."""

    def __init__(self) -> None:
        self._agents: dict[FueroKey, BaseFueroAgent] = {
            FueroKey.PENAL: AgentPenal(),
            FueroKey.FAMILIA: AgentFamilia(),
            FueroKey.LABORAL: AgentLaboral(),
            FueroKey.CIVIL: AgentCivil(),
            FueroKey.COMERCIAL_CONSUMIDOR: AgentComercial(),
            FueroKey.SEGURIDAD_SOCIAL_SALUD: AgentSeguridadSocial(),
            FueroKey.CONTENCIOSO_TRIBUTARIO: AgentContencioso(),
        }

    def get_agent(self, fuero: FueroKey) -> BaseFueroAgent:
        """Devuelve la instancia del Sub-Agente especializado para el fuero solicitado."""
        if fuero not in self._agents:
            raise KeyError(f"Fuero '{fuero}' no registrado en FuerosRouter.")
        return self._agents[fuero]

    def get_repository(self, fuero: FueroKey) -> BaseRepositoryConsultant:
        """Devuelve el Consultor de Repositorios y Calculadoras asociado al fuero."""
        return self.get_agent(fuero).repository

    def all_agents(self) -> dict[FueroKey, BaseFueroAgent]:
        """Devuelve el diccionario completo de los 7 agentes para registro en Agente000."""
        return dict(self._agents)

    def dispatch_case_to_fuero(
        self,
        context: CaseContext,
        province_rules: dict[str, object],
    ) -> FueroAnalysisResult:
        """Recibe la Primera Derivación de Agente000, ejecuta el Sub-Agente del fuero y devuelve FueroAnalysisResult."""
        agent = self.get_agent(context.detected_fuero)
        return agent.analyze_case(context=context, province_rules=province_rules)

    def consult_fuero_repository(
        self,
        fuero: FueroKey,
        query: str,
        reference_date: date | None = None,
    ) -> tuple[dict[str, object], CitationValidationReport, list[str]]:
        """Ejecuta el Traductor Criollo -> Ley, Espejo de Fallos y Validador de Vigencia del fuero."""
        repo = self.get_repository(fuero)
        translation = repo.translate_criollo_problem(query)
        validation = repo.validate_citations(query, reference_date=reference_date)
        laws = list(repo.current_normative_catalog)
        laws.extend(repo.get_ingested_norms())
        return translation, validation, laws
