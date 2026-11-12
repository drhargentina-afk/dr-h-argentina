"""Tests del Paso 7 — Registro de Métricas Diarias Anónimas (Zero PII) y Panel Admin.

Valida:
1. Tabla daily_metrics indexada por fecha (YYYY-MM-DD) sin columnas de IP, nombre o email.
2. Transiciones exactas de contadores: visita sin consulta -> 1 consulta -> 2 consultas (social) -> 3+ (comercial) y total IA.
3. Restricción estricta de GET /admin/metrics/daily únicamente para cuentas en ADMIN_EMAILS (403 a estándar y pagos no admin).
4. Validación de entrada de fechas y resumen de hoy + últimos 7 días.
5. Presencia de controles colapsables en #admin-toolbar (frontend_pwa/index.html y frontend_pwa/app.js).
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from core.config import ADMIN_EMAILS
from core.database import DrHDatabase
from main import _current_ar_day_key, app, db


class TestPaso7DailyMetrics(unittest.TestCase):
    """Pruebas unitarias e integrales de telemetría anónima diaria."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.tag = uuid.uuid4().hex[:8]
        self.created_emails: list[str] = []

    def tearDown(self) -> None:
        today_key = _current_ar_day_key()
        with db._connect() as conn:
            conn.execute("DELETE FROM daily_metrics WHERE metric_date = ?", (today_key,))
            scope_keys = [f"acct:{e}" for e in self.created_emails]
            if scope_keys:
                ph = ",".join("?" for _ in scope_keys)
                conn.execute(f"DELETE FROM daily_funnel_counters WHERE scope_key IN ({ph})", scope_keys)
            non_admins = [e for e in self.created_emails if e not in {a.lower() for a in ADMIN_EMAILS}]
            if non_admins:
                ep = ",".join("?" for _ in non_admins)
                conn.execute(f"DELETE FROM user_sessions WHERE user_email IN ({ep})", non_admins)
                conn.execute(f"DELETE FROM users WHERE email IN ({ep})", non_admins)
        db.reset_non_admin_users(ADMIN_EMAILS)

    def _login(self, email: str, name: str = "User") -> dict[str, str]:
        normalized = email.strip().lower()
        self.created_emails.append(normalized)
        resp = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{normalized}:{name}"},
        )
        self.assertEqual(resp.status_code, 200)
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_database_daily_metrics_schema_has_no_pii_and_tracks_buckets(self) -> None:
        """Verifica que daily_metrics carezca de PII y transicione buckets sin valores negativos."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            temp_db = DrHDatabase(Path(tmpdir) / "metrics_test.db")
            with temp_db._connect() as conn:
                cols = {r["name"].lower() for r in conn.execute("PRAGMA table_info(daily_metrics);").fetchall()}

            self.assertEqual(
                cols,
                {
                    "metric_date",
                    "visits_without_query",
                    "users_single_query",
                    "users_two_queries",
                    "users_three_plus_queries",
                    "total_queries_executed",
                    "updated_at_iso",
                },
            )
            self.assertFalse(cols & {"email", "user_email", "ip", "client_ip", "name", "device_fingerprint"})

            with self.assertRaises(ValueError):
                temp_db.record_daily_metric_visit("12-11-2026")

            d = "2026-11-12"
            temp_db.record_daily_metric_visit(d)
            temp_db.record_daily_metric_transition(d, prev_bucket=0, new_bucket=1, ai_query_executed=True)
            temp_db.record_daily_metric_transition(d, prev_bucket=1, new_bucket=2, ai_query_executed=True)
            row = temp_db.record_daily_metric_transition(d, prev_bucket=2, new_bucket=3, ai_query_executed=False)

            self.assertEqual(row["visits_without_query"], 0)
            self.assertEqual(row["users_single_query"], 0)
            self.assertEqual(row["users_two_queries"], 0)
            self.assertEqual(row["users_three_plus_queries"], 1)
            self.assertEqual(row["total_queries_executed"], 2)

            summary = temp_db.get_daily_metrics_summary(d, days=7)
            self.assertEqual(summary["today"]["metric_date"], d)
            self.assertEqual(len(summary["last_7_days"]), 7)
            self.assertEqual(summary["totals_7_days"]["users_three_plus_queries"], 1)
            self.assertEqual(summary["totals_7_days"]["total_queries_executed"], 2)

    def test_admin_metrics_endpoint_permissions_and_anonymous_funnel_flow(self) -> None:
        """Valida acceso exclusivo ADMIN_EMAILS (403 a no-admin/pagos) y flujo completo anónimo."""
        today_key = _current_ar_day_key()
        with db._connect() as conn:
            conn.execute("DELETE FROM daily_metrics WHERE metric_date = ?", (today_key,))

        # Sin sesión -> 401/403
        unauth_resp = self.client.get("/admin/metrics/daily")
        self.assertIn(unauth_resp.status_code, (401, 403))

        # Usuario estándar -> 403
        std_email = f"std.metrics.{self.tag}@gmail.com"
        h_std = self._login(std_email, "Estandar")
        resp_std = self.client.get("/admin/metrics/daily", headers=h_std)
        self.assertEqual(resp_std.status_code, 403)

        # Usuario pago NO incluido en ADMIN_EMAILS -> 403
        paid_email = f"paid.nonadmin.{self.tag}@gmail.com"
        h_paid = self._login(paid_email, "Pago No Admin")
        db.set_user_paid_pass(paid_email, True)
        resp_paid = self.client.get("/admin/metrics/daily", headers=h_paid)
        self.assertEqual(resp_paid.status_code, 403)

        # Visita anónima sin consulta + 1ª consulta + 2ª consulta (social) + 3ª bloqueo comercial
        visit_resp = self.client.post("/metrics/visit")
        self.assertEqual(visit_resp.status_code, 200)
        self.assertTrue(visit_resp.json()["recorded"])

        query_payload = {
            "query": "Consulta anónima de prueba por ruidos molestos de un vecino",
            "province_jurisdiction": "PJN_NACION_CABA",
            "city_locality": "CABA",
        }
        with patch(
            "main._generate_dynamic_llm_legal_answer",
            return_value="Respuesta IA ciudadana completa y verificada conforme normativa vigente.",
        ):
            q1 = self.client.post("/repository-consult", json=query_payload, headers=h_std)
            self.assertEqual(q1.status_code, 200)

            unlock_resp = self.client.post("/payments/social-unlock", json={"network": "whatsapp"}, headers=h_std)
            self.assertEqual(unlock_resp.status_code, 200)

            q2 = self.client.post("/repository-consult", json=query_payload, headers=h_std)
            self.assertEqual(q2.status_code, 200)

            q3 = self.client.post("/repository-consult", json=query_payload, headers=h_std)
            self.assertEqual(q3.status_code, 402)

        # Administrador whitelist en ADMIN_EMAILS -> 200 con resumen de hoy y 7 días
        admin_email = next(iter(ADMIN_EMAILS))
        h_admin = self._login(admin_email, "Admin")
        resp_admin = self.client.get("/admin/metrics/daily", headers=h_admin)
        self.assertEqual(resp_admin.status_code, 200)
        body = resp_admin.json()
        self.assertEqual(body["today"]["metric_date"], today_key)
        self.assertEqual(body["today"]["visits_without_query"], 0)
        self.assertEqual(body["today"]["users_three_plus_queries"], 1)
        self.assertEqual(body["today"]["total_queries_executed"], 2)
        self.assertEqual(len(body["last_7_days"]), 7)

    def test_frontend_admin_metrics_panel_markup_and_hooks(self) -> None:
        """Verifica que el panel colapsable dentro de #admin-toolbar y los hooks JS existan."""
        html_text = Path("frontend_pwa/index.html").read_text(encoding="utf-8")
        js_text = Path("frontend_pwa/app.js").read_text(encoding="utf-8")

        self.assertIn('id="btn-admin-toggle-metrics"', html_text)
        self.assertIn('id="admin-metrics-panel"', html_text)
        self.assertIn('id="admin-metrics-7d-body"', html_text)
        self.assertIn("/admin/metrics/daily", js_text)
        self.assertIn("/metrics/visit", js_text)
        self.assertIn("loadAdminDailyMetrics", js_text)
        self.assertIn("trackAnonymousDailyVisit", js_text)


if __name__ == "__main__":
    unittest.main()
