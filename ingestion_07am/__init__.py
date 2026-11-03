"""Módulo de Ingesta Normativa Diaria (07:00 AM), Monitor de Salud y Puente a Proyecto Gamuza (`ingestion_07am`)."""
from ingestion_07am.gamuza_bridge import (
    GamuzaKnowledgeBridge,
    GamuzaPrivacyViolationError,
)
from ingestion_07am.repository_updater import (
    FueroUpdateSummary,
    NormativeRepositoryUpdater,
)
from ingestion_07am.scraper_oficial import (
    OfficialGazetteCollector,
    SourceFetchStatus,
)
from ingestion_07am.system_watchdog import (
    AdminIngestionAlert,
    SystemHealthReport,
    SystemHealthWatchdog,
)

__all__ = [
    "AdminIngestionAlert",
    "FueroUpdateSummary",
    "GamuzaKnowledgeBridge",
    "GamuzaPrivacyViolationError",
    "NormativeRepositoryUpdater",
    "OfficialGazetteCollector",
    "SourceFetchStatus",
    "SystemHealthReport",
    "SystemHealthWatchdog",
]
