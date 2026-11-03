"""Paquete de Segunda Derivación y Auditoría Procesal ISO-Compliance (`auditor_revision`)."""
from auditor_revision.compliance_shield import ComplianceShield, PromptInjectionBlockedError
from auditor_revision.emergency_scanner import DispositiveClauseAlert, EmergencyScanner
from auditor_revision.lawyer_watchdog import LawyerWatchdogAuditor
from auditor_revision.report_builder import CitizenReportBuilder

__all__ = [
    "CitizenReportBuilder",
    "ComplianceShield",
    "DispositiveClauseAlert",
    "EmergencyScanner",
    "LawyerWatchdogAuditor",
    "PromptInjectionBlockedError",
]
