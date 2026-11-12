"""Tests del Paso 6 — Rate Limiting Anti-Bots por IP y Huella de Dispositivo.

Valida:
1. Límite estricto de máximo 2 consultas gratuitas por hora y por día desde una misma combinación IP + X-Device-Fingerprint.
2. Bloqueo con HTTP 429 Too Many Requests en la 3ª consulta aunque se alternen distintas cuentas de Google OAuth.
3. Protección de consumo de tokens: el rechazo HTTP 429 ocurre antes de invocar la generación LLM/Gemini.
4. Exención para cuentas con has_paid_pass=True y cuentas en ADMIN_EMAILS.
5. Casos frontera deterministas de _evaluate_antibot_window (hora móvil y día calendario UTC-3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from core.config import ADMIN_EMAILS
import main
from main import (
    ANTIBOT_RATE_LIMIT_MAX_PER_DAY,
    ANTIBOT_RATE_LIMIT_MAX_PER_HOUR,
    _evaluate_antibot_window,
    app,
    db,
)


class TestPaso6AntiBotRateLimit(unittest.TestCase):
    """Pruebas unitarias y de integración para el control de tasa anti-bots (HTTP 429)."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.tag = uuid.uuid4().hex[:8]
        self.test_ips: list[str] = []
        self.test_fps: list[str] = []
        self.test_emails: list[str] = []

    def tearDown(self) -> None:
        scope_keys: list[str] = []
        for ip in self.test_ips:
            scope_keys.append(f"ip:{ip}")
        for fp in self.test_fps:
            scope_keys.append(f"fp:{fp}")
        for ip in self.test_ips:
            for fp in self.test_fps:
                scope_keys.append(f"combo:{ip}|{fp}")
        for email in self.test_emails:
            scope_keys.append(f"acct:{email}")

        if scope_keys:
            placeholders = ",".join("?" for _ in scope_keys)
            with db._connect() as conn:
                conn.execute(
                    f"DELETE FROM antibot_rate_limit_events WHERE scope_key IN ({placeholders})",
                    scope_keys,
                )
                conn.execute(
                    f"DELETE FROM daily_funnel_counters WHERE scope_key IN ({placeholders})",
                    scope_keys,
                )
        if self.test_emails:
            non_admins = [e for e in self.test_emails if e not in {a.lower() for a in ADMIN_EMAILS}]
            if non_admins:
                ep = ",".join("?" for _ in non_admins)
                with db._connect() as conn:
                    conn.execute(f"DELETE FROM user_sessions WHERE user_email IN ({ep})", non_admins)
                    conn.execute(f"DELETE FROM users WHERE email IN ({ep})", non_admins)
        db.reset_non_admin_users(ADMIN_EMAILS)

    def _login_mock_user(self, email: str, name: str = "Bot Test") -> dict[str, str]:
        self.test_emails.append(email.strip().lower())
        resp = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{email}:{name}"},
        )
        self.assertEqual(resp.status_code, 200)
        token = resp.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    def test_window_evaluator_boundaries_and_edge_cases(self) -> None:
        """Valida valores frontera (0, 1, 2, negativos, cruce de medianoche dentro de 1h y privilegios)."""
        ar_tz = timezone(timedelta(hours=-3))
        now_ar = datetime(2026, 11, 12, 23, 50, 0, tzinfo=ar_tz)
        now_epoch = int(now_ar.timestamp())

        # Cero y negativos no bloquean
        s_zero = _evaluate_antibot_window(
            hourly_count=0,
            daily_count=0,
            oldest_hour_epoch=None,
            now_epoch=now_epoch,
            now_ar=now_ar,
            is_privileged=False,
        )
        self.assertFalse(s_zero.is_blocked)
        self.assertEqual(s_zero.retry_after_seconds, 0)

        s_neg = _evaluate_antibot_window(
            hourly_count=-3,
            daily_count=-1,
            oldest_hour_epoch=None,
            now_epoch=now_epoch,
            now_ar=now_ar,
            is_privileged=False,
        )
        self.assertFalse(s_neg.is_blocked)
        self.assertEqual(s_neg.hourly_count, 0)
        self.assertEqual(s_neg.daily_count, 0)

        # Con 1/2 sigue permitido
        s_one = _evaluate_antibot_window(
            hourly_count=1,
            daily_count=1,
            oldest_hour_epoch=now_epoch - 600,
            now_epoch=now_epoch,
            now_ar=now_ar,
            is_privileged=False,
        )
        self.assertFalse(s_one.is_blocked)

        # Límite horario alcanzado tras medianoche (daily_count=0, hourly_count=2) -> bloqueado 429
        s_hour = _evaluate_antibot_window(
            hourly_count=ANTIBOT_RATE_LIMIT_MAX_PER_HOUR,
            daily_count=0,
            oldest_hour_epoch=now_epoch - 1800,
            now_epoch=now_epoch,
            now_ar=now_ar,
            is_privileged=False,
        )
        self.assertTrue(s_hour.is_blocked)
        self.assertEqual(s_hour.retry_after_seconds, 1800)

        # Límite diario alcanzado más de 1h después (hourly_count=0, daily_count=2) -> bloqueado hasta medianoche
        s_day = _evaluate_antibot_window(
            hourly_count=0,
            daily_count=ANTIBOT_RATE_LIMIT_MAX_PER_DAY,
            oldest_hour_epoch=None,
            now_epoch=now_epoch,
            now_ar=now_ar,
            is_privileged=False,
        )
        self.assertTrue(s_day.is_blocked)
        self.assertEqual(s_day.retry_after_seconds, 600)

        # Privilegiado nunca bloquea
        s_priv = _evaluate_antibot_window(
            hourly_count=99,
            daily_count=99,
            oldest_hour_epoch=now_epoch,
            now_epoch=now_epoch,
            now_ar=now_ar,
            is_privileged=True,
        )
        self.assertFalse(s_priv.is_blocked)
        self.assertTrue(s_priv.is_privileged)

    def test_429_blocks_third_request_across_oauth_accounts_before_gemini_call(self) -> None:
        """Verifica HTTP 429 en la 3ª consulta cambiando de cuenta OAuth y sin invocar Gemini."""
        ip = f"203.0.113.{int(self.tag[:2], 16) % 200 + 10}"
        fp = f"dfp_antibot_{self.tag}"
        self.test_ips.append(ip)
        self.test_fps.append(fp)

        common_net_headers = {
            "X-Forwarded-For": ip,
            "X-Device-Fingerprint": fp,
        }
        payload = {
            "query": "Mi vecino pone música a todo volumen de madrugada, ¿cómo intimo?",
            "province_jurisdiction": "PJN_NACION_CABA",
            "city_locality": "CABA",
        }

        h1 = {**self._login_mock_user(f"acct1.{self.tag}@gmail.com", "Uno"), **common_net_headers}
        h2 = {**self._login_mock_user(f"acct2.{self.tag}@gmail.com", "Dos"), **common_net_headers}
        h3 = {**self._login_mock_user(f"acct3.{self.tag}@gmail.com", "Tres"), **common_net_headers}

        with patch("main._generate_dynamic_llm_legal_answer", return_value="Respuesta IA verificada.") as llm_mock:
            # 1ª consulta gratuita desde acct1 -> 200
            r1 = self.client.post("/repository-consult", json=payload, headers=h1)
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(llm_mock.call_count, 1)
            self.assertEqual(r1.headers.get("X-RateLimit-Remaining-Hour"), "1")
            self.assertEqual(r1.headers.get("X-RateLimit-Remaining-Day"), "1")

            # 2ª consulta desde acct2 (requiere desbloqueo social en el embudo antes de consumir la 2ª gratis del IP/FP)
            r_unlock = self.client.post(
                "/payments/social-unlock",
                json={"network": "whatsapp"},
                headers=h2,
            )
            self.assertEqual(r_unlock.status_code, 200)

            r2 = self.client.post("/repository-consult", json=payload, headers=h2)
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(llm_mock.call_count, 2)
            self.assertEqual(r2.headers.get("X-RateLimit-Remaining-Hour"), "0")
            self.assertEqual(r2.headers.get("X-RateLimit-Remaining-Day"), "0")

            # 3ª consulta con cuenta nueva acct3 desde misma IP/FP -> HTTP 429 SIN invocar Gemini
            r3 = self.client.post("/repository-consult", json=payload, headers=h3)
            self.assertEqual(r3.status_code, 429)
            self.assertEqual(llm_mock.call_count, 2)
            self.assertGreaterEqual(int(r3.headers.get("Retry-After", "0")), 1)
            self.assertIn("límite de 2 consultas gratuitas", r3.json()["detail"])

            # También /emergency-scan y /analyze-expediente deben rechazar con HTTP 429 sin procesar
            r_scan = self.client.post(
                "/emergency-scan",
                json={
                    "document_text": "Consulta rápida con el límite ya agotado.",
                    "province_jurisdiction": "PJN_NACION_CABA",
                },
                headers=h3,
            )
            self.assertEqual(r_scan.status_code, 429)

            with patch.object(main.orchestrator, "analyze_full_expediente") as pdf_mock:
                r_pdf = self.client.post(
                    "/analyze-expediente",
                    files={"file": ("consulta.pdf", b"%PDF-1.4\n%test\n", "application/pdf")},
                    data={"user_verbal_statement": "Prueba antibots sobre PDF"},
                    headers=h3,
                )
                self.assertEqual(r_pdf.status_code, 429)
                pdf_mock.assert_not_called()

            # También falla con 429 si rota solo X-Device-Fingerprint manteniendo misma IP
            h_fp_rot = {
                **h3,
                "X-Device-Fingerprint": f"dfp_rotated_{self.tag}",
            }
            self.test_fps.append(h_fp_rot["X-Device-Fingerprint"])
            r_fp_rot = self.client.post("/repository-consult", json=payload, headers=h_fp_rot)
            self.assertEqual(r_fp_rot.status_code, 429)
            self.assertEqual(llm_mock.call_count, 2)

            # Cuentas ADMIN_EMAILS y has_paid_pass=True NO deben ser bloqueadas por 429 en esa misma IP/FP
            admin_email = next(iter(ADMIN_EMAILS))
            h_admin = {**self._login_mock_user(admin_email, "Admin"), **common_net_headers}
            r_admin = self.client.post("/repository-consult", json=payload, headers=h_admin)
            self.assertEqual(r_admin.status_code, 200)
            self.assertEqual(llm_mock.call_count, 3)

            paid_email = f"paid.{self.tag}@gmail.com"
            h_paid = {**self._login_mock_user(paid_email, "Pago"), **common_net_headers}
            db.set_user_paid_pass(paid_email, True)
            r_paid = self.client.post("/repository-consult", json=payload, headers=h_paid)
            self.assertEqual(r_paid.status_code, 200)
            self.assertEqual(llm_mock.call_count, 4)


if __name__ == "__main__":
    unittest.main()
