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
            conn.execute("DELETE FROM daily_metric_visitors WHERE metric_date = ?", (today_key,))
            conn.execute(
                "DELETE FROM antibot_rate_limit_events WHERE scope_key LIKE ?",
                (f"%{self.tag}%",),
            )
            conn.execute(
                "DELETE FROM daily_funnel_counters WHERE scope_key LIKE ?",
                (f"%{self.tag}%",),
            )
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
            # Visita inicial (count == 0) -> visits_without_query += 1
            v_row = temp_db.record_daily_metric_visit(d, visitor_hash="vis_a")
            self.assertEqual(v_row["visits_without_query"], 1)
            # Duplicado de visita mismo día no vuelve a sumar
            v_dup = temp_db.record_daily_metric_visit(d, visitor_hash="vis_a")
            self.assertEqual(v_dup["visits_without_query"], 1)

            # 1ª consulta (count == 1): visits_without_query - 1, users_single_query + 1, total + 1
            r1 = temp_db.record_daily_user_consultation(
                d, "usr_a", endpoint="/emergency-scan", now_epoch=1000
            )
            self.assertEqual(r1["visits_without_query"], 0)
            self.assertEqual(r1["users_single_query"], 1)
            self.assertEqual(r1["total_queries_executed"], 1)

            # Coalescencia de consulta unificada (/emergency-scan + /repository-consult en <15s)
            r1_coalesced = temp_db.record_daily_user_consultation(
                d, "usr_a", endpoint="/repository-consult", now_epoch=1005
            )
            self.assertEqual(r1_coalesced["users_single_query"], 1)
            self.assertEqual(r1_coalesced["total_queries_executed"], 1)

            # 2ª consulta (count == 2): users_single_query - 1, users_two_queries + 1, total + 1
            r2 = temp_db.record_daily_user_consultation(
                d, "usr_a", endpoint="/repository-consult", now_epoch=1100
            )
            self.assertEqual(r2["users_single_query"], 0)
            self.assertEqual(r2["users_two_queries"], 1)
            self.assertEqual(r2["total_queries_executed"], 2)

            # 3ª consulta (count == 3): users_two_queries - 1, users_three_plus_queries + 1, total + 1
            r3 = temp_db.record_daily_user_consultation(
                d, "usr_a", endpoint="/repository-consult", now_epoch=1200
            )
            self.assertEqual(r3["users_two_queries"], 0)
            self.assertEqual(r3["users_three_plus_queries"], 1)
            self.assertEqual(r3["total_queries_executed"], 3)

            # 4ª consulta (count >= 4): mantiene users_three_plus_queries == 1, total + 1
            r4 = temp_db.record_daily_user_consultation(
                d, "usr_a", endpoint="/analyze-expediente", now_epoch=1300
            )
            self.assertEqual(r4["users_three_plus_queries"], 1)
            self.assertEqual(r4["total_queries_executed"], 4)

            summary = temp_db.get_daily_metrics_summary(d, days=7)
            self.assertEqual(summary["today"]["metric_date"], d)
            self.assertEqual(len(summary["last_7_days"]), 7)
            self.assertEqual(summary["totals_7_days"]["users_three_plus_queries"], 1)
            self.assertEqual(summary["totals_7_days"]["total_queries_executed"], 4)

    def test_admin_metrics_endpoint_permissions_and_anonymous_funnel_flow(self) -> None:
        """Valida acceso ADMIN_EMAILS y simulación de 2 visitas -> 2 consultas reales en /repository-consult."""
        today_key = _current_ar_day_key()
        with db._connect() as conn:
            conn.execute("DELETE FROM daily_metrics WHERE metric_date = ?", (today_key,))
            conn.execute("DELETE FROM daily_metric_visitors WHERE metric_date = ?", (today_key,))

        # Sin sesión -> 401/403
        unauth_resp = self.client.get("/admin/metrics/daily")
        self.assertIn(unauth_resp.status_code, (401, 403))

        # Usuario estándar -> 403
        u1_email = f"std1.metrics.{self.tag}@gmail.com"
        u2_email = f"std2.metrics.{self.tag}@gmail.com"
        h_u1 = {**self._login(u1_email, "Usuario 1"), "X-Device-Fingerprint": f"fp_u1_{self.tag}"}
        h_u2 = {**self._login(u2_email, "Usuario 2"), "X-Device-Fingerprint": f"fp_u2_{self.tag}"}
        resp_std = self.client.get("/admin/metrics/daily", headers=h_u1)
        self.assertEqual(resp_std.status_code, 403)

        # Usuario pago NO incluido en ADMIN_EMAILS -> 403
        paid_email = f"paid.nonadmin.{self.tag}@gmail.com"
        h_paid = {**self._login(paid_email, "Pago No Admin"), "X-Device-Fingerprint": f"fp_paid_{self.tag}"}
        db.set_user_paid_pass(paid_email, True)
        resp_paid = self.client.get("/admin/metrics/daily", headers=h_paid)
        self.assertEqual(resp_paid.status_code, 403)

        admin_email = next(iter(ADMIN_EMAILS))
        h_admin = self._login(admin_email, "Admin")

        # 1) Simular 2 usuarios que entran a la web sin consultar aún -> visits_without_query == 2
        v1 = self.client.post("/metrics/visit", headers={"X-Device-Fingerprint": f"fp_u1_{self.tag}"})
        v2 = self.client.post("/metrics/visit", headers={"X-Device-Fingerprint": f"fp_u2_{self.tag}"})
        self.assertEqual(v1.status_code, 200)
        self.assertEqual(v2.status_code, 200)

        pre_metrics = self.client.get("/admin/metrics/daily", headers=h_admin).json()["today"]
        self.assertEqual(pre_metrics["visits_without_query"], 2)
        self.assertEqual(pre_metrics["users_single_query"], 0)
        self.assertEqual(pre_metrics["total_queries_executed"], 0)

        # 2) Ambos usuarios ejecutan 1 consulta real en /repository-consult
        query_payload = {
            "query": "Consulta ciudadana de prueba por ruidos molestos de un vecino lindero",
            "province_jurisdiction": "PJN_NACION_CABA",
            "city_locality": "CABA",
        }
        with patch(
            "main._generate_dynamic_llm_legal_answer",
            return_value="Respuesta IA ciudadana completa y verificada conforme normativa vigente.",
        ):
            q_u1 = self.client.post("/repository-consult", json=query_payload, headers=h_u1)
            q_u2 = self.client.post("/repository-consult", json=query_payload, headers=h_u2)
            self.assertEqual(q_u1.status_code, 200)
            self.assertEqual(q_u2.status_code, 200)

        post_2_queries = self.client.get("/admin/metrics/daily", headers=h_admin).json()["today"]
        self.assertEqual(post_2_queries["metric_date"], today_key)
        self.assertEqual(post_2_queries["visits_without_query"], 0)
        self.assertEqual(post_2_queries["users_single_query"], 2)
        self.assertEqual(post_2_queries["users_two_queries"], 0)
        self.assertEqual(post_2_queries["users_three_plus_queries"], 0)
        self.assertEqual(post_2_queries["total_queries_executed"], 2)

        # 3) Usuario 1 desbloquea muro social y realiza su 2ª consulta -> pasa a users_two_queries == 1
        unlock_u1 = self.client.post("/payments/social-unlock", json={"network": "whatsapp"}, headers=h_u1)
        self.assertEqual(unlock_u1.status_code, 200)
        with patch(
            "main._generate_dynamic_llm_legal_answer",
            return_value="Respuesta IA ciudadana completa y verificada conforme normativa vigente.",
        ):
            q_u1_second = self.client.post("/repository-consult", json=query_payload, headers=h_u1)
            self.assertEqual(q_u1_second.status_code, 200)

        after_u1_second = self.client.get("/admin/metrics/daily", headers=h_admin).json()["today"]
        self.assertEqual(after_u1_second["visits_without_query"], 0)
        self.assertEqual(after_u1_second["users_single_query"], 1)
        self.assertEqual(after_u1_second["users_two_queries"], 1)
        self.assertEqual(after_u1_second["total_queries_executed"], 3)

        # 4) Admin realiza 3 consultas reales -> suma en users_three_plus_queries == 1 y total == 6
        with patch(
            "main._generate_dynamic_llm_legal_answer",
            return_value="Respuesta IA ciudadana completa y verificada conforme normativa vigente.",
        ):
            for _ in range(3):
                r_adm = self.client.post("/repository-consult", json=query_payload, headers=h_admin)
                self.assertEqual(r_adm.status_code, 200)

        after_admin_3 = self.client.get("/admin/metrics/daily", headers=h_admin).json()["today"]
        self.assertEqual(after_admin_3["visits_without_query"], 0)
        self.assertEqual(after_admin_3["users_single_query"], 1)
        self.assertEqual(after_admin_3["users_two_queries"], 1)
        self.assertEqual(after_admin_3["users_three_plus_queries"], 1)
        self.assertEqual(after_admin_3["total_queries_executed"], 6)

    def test_frontend_admin_metrics_panel_markup_and_hooks(self) -> None:
        """Verifica que el panel colapsable dentro de #admin-toolbar y el scroll móvil existan."""
        html_text = Path("frontend_pwa/index.html").read_text(encoding="utf-8")
        js_text = Path("frontend_pwa/app.js").read_text(encoding="utf-8")

        self.assertIn('id="btn-admin-toggle-metrics"', html_text)
        self.assertIn('id="admin-metrics-panel"', html_text)
        self.assertIn('id="admin-metrics-table-scroll"', html_text)
        self.assertIn("overflow-x:auto", html_text)
        self.assertIn('id="admin-metrics-7d-body"', html_text)
        self.assertIn("/admin/metrics/daily", js_text)
        self.assertIn("/metrics/visit", js_text)
        self.assertIn("loadAdminDailyMetrics", js_text)
        self.assertIn("trackAnonymousDailyVisit", js_text)


if __name__ == "__main__":
    unittest.main()
