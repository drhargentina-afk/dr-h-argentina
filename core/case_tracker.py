"""Servicio de Historial de Causas y Seguimiento Asíncrono de Movimientos (`core/case_tracker.py`).

Permite al ciudadano mantener sus causas registradas y subir novedades (PDF de 1 o 2 fojas)
asociadas a expedientes anteriores, contrastándolas en memoria RAM con Agente000 y actualizando
el semáforo procesal (VERDE, AMARILLO, ROJO) sin requerir contraseñas judiciales.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Literal

from core.agente000_orchestrator import Agente000Orchestrator
from core.database import DrHDatabase
from core.models import EmergencyScanRequest, FinalCitizenReport, FueroKey, ProvinceKey


class CaseTrackerService:
    """Administra el ciclo de vida de causas y sus movimientos procesales."""

    def __init__(self, db: DrHDatabase, orchestrator: Agente000Orchestrator) -> None:
        self.db = db
        self.orchestrator = orchestrator

    def list_user_cases(self, user_email: str) -> list[dict[str, Any]]:
        """Devuelve el listado de causas pertenecientes al usuario."""
        cases = self.db.get_user_cases(user_email)
        enriched: list[dict[str, Any]] = []
        for c in cases:
            case_item = dict(c)
            case_id = case_item.get("case_id", "")
            movements = self.db.get_case_movements(case_id)
            case_item["caratula"] = case_item.get("title", "")
            case_item["fuero"] = case_item.get("detected_fuero", "")
            case_item["current_status"] = case_item.get("status_color", "VERDE")
            case_item["summary_snippet"] = case_item.get("last_summary", "")
            case_item["movements_count"] = len(movements) + 1
            enriched.append(case_item)
        return enriched

    def get_case_detail(self, case_id: str, user_email: str) -> dict[str, Any]:
        """Obtiene una causa con todos sus movimientos verificando pertenencia."""
        case = self.db.get_case_by_id(case_id)
        if not case or case["user_email"].lower() != user_email.lower():
            raise ValueError("La causa solicitada no existe o no pertenece a tu cuenta.")

        movements = self.db.get_case_movements(case_id)
        report_data = {}
        try:
            report_data = json.loads(case["report_json"])
        except Exception:
            pass

        return {
            "case_id": case["case_id"],
            "title": case["title"],
            "detected_fuero": case["detected_fuero"],
            "province_jurisdiction": case["province_jurisdiction"],
            "status_color": case["status_color"],
            "do_not_sign_yet": bool(case["do_not_sign_yet"]),
            "last_summary": case["last_summary"],
            "created_at_iso": case["created_at_iso"],
            "updated_at_iso": case["updated_at_iso"],
            "movements": movements,
            "report_summary": report_data.get("citizen_plain_summary", ""),
            "questions_for_your_lawyer": report_data.get("questions_for_your_lawyer", []),
        }

    def record_case_from_final_report(
        self,
        *,
        user_email: str,
        report: FinalCitizenReport,
        custom_title: str | None = None,
    ) -> dict[str, Any]:
        """Guarda una nueva causa a partir de un informe final emitido por Agente000."""
        status_color: Literal["VERDE", "AMARILLO", "ROJO"]
        if report.do_not_sign_yet_alert:
            status_color = "ROJO"
        elif "caducidad" in report.audit_and_deadlines_section.lower() or "plazo" in report.audit_and_deadlines_section.lower():
            status_color = "AMARILLO"
        else:
            status_color = "VERDE"

        fuero_label = report.fuero.value if hasattr(report.fuero, "value") else str(report.fuero)
        prov_label = report.province_jurisdiction.value if hasattr(report.province_jurisdiction, "value") else str(report.province_jurisdiction)

        title = custom_title or f"Causa {fuero_label.replace('_', ' ').capitalize()} ({report.report_id})"
        report_json = report.model_dump_json()

        return self.db.create_case(
            case_id=report.report_id,
            user_email=user_email,
            title=title,
            detected_fuero=fuero_label,
            province_jurisdiction=prov_label,
            status_color=status_color,
            do_not_sign_yet=report.do_not_sign_yet_alert,
            last_summary=report.citizen_plain_summary,
            report_json=report_json,
        )

    def process_case_movement(
        self,
        *,
        case_id: str,
        user_email: str,
        file_bytes: bytes,
        file_name: str,
    ) -> dict[str, Any]:
        """Procesa una novedad procesal (1 o 2 fojas), la contrasta y actualiza el semáforo."""
        case = self.db.get_case_by_id(case_id)
        if not case or case["user_email"].lower() != user_email.lower():
            raise ValueError("La causa indicada no existe o no pertenece a tu cuenta.")

        # Carga e higiene en RAM con Zero-Storage
        session = self.orchestrator.drive_manager.load_and_prequote_in_ram(file_bytes, file_name)
        try:
            pages_count = session.pre_quote.total_pages
            delta_text = "\n".join(
                foja.cleaned_text for foja in session.fojas if not foja.is_discarded_hygiene
            ).strip()

            if not delta_text:
                delta_text = f"Movimiento recibido ({file_name}) sin texto seleccionable."

            # Escaneo de urgencia y detección de actos dispositivos
            prov_key = ProvinceKey.PJN_NACION_CABA
            try:
                prov_key = ProvinceKey(case["province_jurisdiction"])
            except Exception:
                pass

            scan_res = self.orchestrator.run_emergency_scan(
                EmergencyScanRequest(
                    document_text=delta_text[:2500],
                    province_jurisdiction=prov_key,
                    user_context_note=f"Novedad procesal agregada a la causa {case_id}",
                )
            )

            # Determinación del nuevo semáforo
            prev_color = case["status_color"]
            new_color: Literal["VERDE", "AMARILLO", "ROJO"]

            if scan_res.do_not_sign_yet or scan_res.urgency_level == "PELIGRO_NO_FIRMAR":
                new_color = "ROJO"
            elif any(w in delta_text.lower() for w in ("traslado", "vista", "intímase", "bajo apercibimiento", "plazo")):
                new_color = "AMARILLO"
            else:
                new_color = "VERDE"

            # Resumen de la novedad en lenguaje claro
            novedad_summary = (
                f"Novedad procesal de {pages_count} fojas agregada: {scan_res.plain_explanation} "
                f"Guía de acción: {scan_res.immediate_action_guide}"
            )

            today_str = datetime.now(timezone.utc).strftime("%d/%m/%Y")
            questions = [
                f"Doc, se agregó una novedad ({pages_count} pág.): ¿Qué plazo tenemos para contestar?",
                "¿Es necesario que firme algún escrito o presentación respecto a esta resolución?",
            ]

            movement_row = self.db.add_case_movement(
                case_id=case_id,
                movement_date=today_str,
                pages_count=pages_count,
                novedad_summary=novedad_summary,
                previous_status_color=prev_color,
                new_status_color=new_color,
                risk_alert_text=scan_res.plain_explanation if new_color == "ROJO" else "",
                questions_generated=questions,
            )

            return {
                "status": "SUCCESS",
                "new_status": new_color,
                "movement": movement_row,
                "case_id": case_id,
                "previous_status_color": prev_color,
                "new_status_color": new_color,
                "novedad_summary": novedad_summary,
                "questions_for_lawyer": questions,
                "do_not_sign_yet": (new_color == "ROJO"),
            }
        finally:
            self.orchestrator.drive_manager._volatile_sessions.pop(session.session_id, None)
            session.purge_ram()
