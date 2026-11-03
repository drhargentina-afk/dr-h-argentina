"""Monitor de Salud del Sistema, Verificador de Actualización 07:00 AM y Alertas (`system_watchdog.py`).

Ejecuta el Healthcheck profundo del Nodo AR ("Dr. H Argentina"):
1. Verifica la disponibilidad de `Agente000Orchestrator`, los 7 Sub-Agentes de Fuero (`01_penal` a `07_contencioso_tributario`),
   el módulo de auditoría `auditor_revision` (`LawyerWatchdogAuditor`, `ComplianceShield`, `EmergencyScanner`)
   y el aislamiento Zero-Storage en memoria RAM (`active_volatile_sessions == 0`).
2. Controla el timestamp de la última sincronización matutina de las 07:00 AM (`is_normative_db_up_to_date`)
   y expone `platform_version` para la auto-actualización transparente de la PWA en el Paso 5.
3. Emite alertas administrativas estructuradas (`AdminIngestionAlert`) cuando algún conector oficial
   (BORA, InfoLEG, CSJN, BCRA, CNV, ARCA, INDEC) presenta fallas de lectura.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from ingestion_07am.scraper_oficial import SourceFetchStatus

if TYPE_CHECKING:
    from core.agente000_orchestrator import Agente000Orchestrator


class AdminIngestionAlert(BaseModel):
    """Payload de alerta administrativa emitida cuando falla una fuente oficial en la lectura 07:00 AM."""

    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(..., min_length=8, description="Identificador único de la alerta administrativa.")
    created_at_iso: str = Field(..., description="Timestamp ISO-8601 del incidente.")
    severity: Literal["WARNING_FALLBACK_APPLIED", "CRITICAL_SOURCE_DOWN"] = Field(
        ...,
        description="Nivel de severidad operativa.",
    )
    failing_organism: str = Field(..., description="Organismo oficial cuyo conector presentó error.")
    endpoint_url: str = Field(..., description="URL del conector afectado.")
    error_message: str = Field(..., description="Detalle técnico del fallo de lectura matutina.")
    remediation_action: str = Field(..., description="Acción automática adoptada por el Watchdog.")


class SystemHealthReport(BaseModel):
    """Reporte integral de salud operativa, memoria RAM, sincronización 07:00 AM y versión PWA."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["OK", "DEGRADED"] = Field(..., description="Estado general del Nodo AR.")
    node: Literal["DR_H_AR"] = Field(default="DR_H_AR", description="Identificador del nodo nacional.")
    country_code: Literal["AR"] = Field(default="AR", description="Código ISO de Capa-País.")
    platform_version: str = Field(..., description="Versión semántica actual para auto-actualización de la PWA.")
    checked_at_iso: str = Field(..., description="Timestamp ISO-8601 del chequeo de salud.")
    agente000_ready: bool = Field(..., description="True si el cerebro central Agente000 está operativo.")
    loaded_jurisdictions: list[str] = Field(..., description="Jurisdicciones procesales activas.")
    fueros_enabled: list[str] = Field(..., description="Lista de los 7 Sub-Agentes de fuero verificados.")
    auditor_revision_ready: bool = Field(..., description="True si el módulo ISO-Compliance y Watchdog está activo.")
    ram_zero_storage_clean: bool = Field(
        ...,
        description="True si no quedaron sesiones volátiles ni mapas PII residuales en memoria RAM.",
    )
    active_volatile_ram_sessions: int = Field(..., ge=0, description="Cantidad de sesiones activas en RAM en este instante.")
    is_normative_db_up_to_date: bool = Field(
        ...,
        description="True si la base normativa recibió sincronización matutina 07:00 AM en las últimas 24 horas.",
    )
    last_07am_sync_iso: str | None = Field(
        default=None,
        description="Timestamp ISO-8601 de la última ingesta 07:00 AM exitosa.",
    )
    total_norms_ingested_today: int = Field(..., ge=0, description="Total de normas inyectadas en los 7 fueros.")
    total_gamuza_signals_emitted: int = Field(..., ge=0, description="Total de señales regulatorias despachadas a Gamuza.")
    current_economic_parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Constantes económicas vigentes en caliente (SMVM, ICL, IPC, Canasta INDEC).",
    )
    admin_alerts: list[AdminIngestionAlert] = Field(
        default_factory=list,
        description="Alertas administrativas generadas ante fallas en conectores oficiales.",
    )


class SystemHealthWatchdog:
    """Monitor de salud profunda, control de actualización 07:00 AM y versión de plataforma."""

    PLATFORM_VERSION: str = "1.4.0-AR-PWA"

    def __init__(self) -> None:
        self._last_07am_sync: datetime | None = None
        self._admin_alerts: list[AdminIngestionAlert] = []

    def record_07am_sync(
        self,
        sync_time: datetime | None = None,
        source_statuses: list[SourceFetchStatus] | None = None,
    ) -> list[AdminIngestionAlert]:
        """Registra la ejecución de la ingesta 07:00 AM y genera alertas si falló alguna fuente oficial."""
        now = sync_time or datetime.now(timezone.utc)
        self._last_07am_sync = now
        new_alerts: list[AdminIngestionAlert] = []

        if source_statuses:
            for st in source_statuses:
                if not st.success:
                    alert = AdminIngestionAlert(
                        alert_id=f"alrt-07am-{uuid.uuid4().hex[:8]}",
                        created_at_iso=now.isoformat(timespec="seconds"),
                        severity="WARNING_FALLBACK_APPLIED",
                        failing_organism=st.source_organism,
                        endpoint_url=st.endpoint_url,
                        error_message=st.error_detail or f"Fallo de conexión con {st.source_organism}.",
                        remediation_action=(
                            f"Se mantuvo la última versión verificada del repositorio para {st.source_organism} "
                            f"y se programó reintento automático."
                        ),
                    )
                    self._admin_alerts.append(alert)
                    new_alerts.append(alert)
        return new_alerts

    def clear_alerts(self) -> None:
        """Limpia el historial de alertas administrativas."""
        self._admin_alerts.clear()

    def build_health_report(
        self,
        orchestrator: Agente000Orchestrator,
        reference_now: datetime | None = None,
    ) -> SystemHealthReport:
        """Ejecuta el diagnóstico profundo de todos los componentes de Dr. H Argentina."""
        import importlib

        now = reference_now or datetime.now(timezone.utc)
        jurisdictions_cfg = orchestrator.provinces_config.get("jurisdictions", {})
        loaded_jurisdictions = (
            list(jurisdictions_cfg.keys()) if isinstance(jurisdictions_cfg, dict) else []
        )

        registered_fueros = [f.value for f in orchestrator._fuero_handlers.keys()]
        all_7_fueros_ok = len(registered_fueros) == 7
        auditor_ok = orchestrator._auditor_handler is not None

        active_drive_ram = len(orchestrator.drive_manager._volatile_sessions)
        active_anon_ram = len(orchestrator.anonymizer._active_sessions)
        total_active_ram = active_drive_ram + active_anon_ram
        ram_clean = total_active_ram == 0

        if self._last_07am_sync is not None:
            elapsed_hours = (now - self._last_07am_sync).total_seconds() / 3600.0
            is_up_to_date = elapsed_hours <= 24.5
            last_sync_iso: str | None = self._last_07am_sync.isoformat(timespec="seconds")
        else:
            is_up_to_date = False
            last_sync_iso = None

        total_norms = sum(len(v) for v in orchestrator._daily_norm_updates.values())
        total_signals = len(orchestrator._emitted_gamuza_signals)

        # Lectura en vivo de los parámetros matemáticos vigentes en los repositorios
        mod_fam = importlib.import_module("agents_fueros.02_familia.repo_familia")
        mod_lab = importlib.import_module("agents_fueros.03_laboral.repo_laboral")
        mod_civ = importlib.import_module("agents_fueros.04_civil.repo_civil")
        mod_com = importlib.import_module("agents_fueros.05_comercial_consumidor.repo_comercial")

        canasta_6_12 = mod_fam.RepoFamilia._CANASTA_CRIANZA_INDEC_ARS["6_A_12_ANIOS"]
        econ_snapshot = {
            "smvm_decreto_484_87_ars": str(mod_com.RepoComercial._SMVM_CURRENT_ARS),
            "smvm_laboral_ars": str(mod_lab.RepoLaboral._SMVM_CURRENT_ARS),
            "ripte_coefficient": str(mod_lab.RepoLaboral._RIPTE_COEFFICIENT),
            "icl_bcra_index": str(mod_civ.RepoCivil._LATEST_ICL_BCRA_INDEX),
            "ipc_indec_monthly_pct": str(mod_civ.RepoCivil._LATEST_IPC_INDEC_MONTHLY_PCT),
            "canasta_indec_6_a_12_total_ars": str(canasta_6_12[0] + canasta_6_12[1]),
        }

        overall_status: Literal["OK", "DEGRADED"] = (
            "OK" if (all_7_fueros_ok and auditor_ok and ram_clean) else "DEGRADED"
        )

        return SystemHealthReport(
            status=overall_status,
            node="DR_H_AR",
            country_code="AR",
            platform_version=self.PLATFORM_VERSION,
            checked_at_iso=now.isoformat(timespec="seconds"),
            agente000_ready=all_7_fueros_ok and auditor_ok,
            loaded_jurisdictions=loaded_jurisdictions,
            fueros_enabled=registered_fueros,
            auditor_revision_ready=auditor_ok,
            ram_zero_storage_clean=ram_clean,
            active_volatile_ram_sessions=total_active_ram,
            is_normative_db_up_to_date=is_up_to_date,
            last_07am_sync_iso=last_sync_iso,
            total_norms_ingested_today=total_norms,
            total_gamuza_signals_emitted=total_signals,
            current_economic_parameters=econ_snapshot,
            admin_alerts=list(self._admin_alerts),
        )
