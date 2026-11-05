"""Tests de Integración y Validación — Modelo Comercial Dual: Pago Social & MoR Externo.

Verifica:
1. Endpoint `GET /payments/config` con lectura segura de `EXTERNAL_PAYMENT_URL`.
2. Desbloqueo mediante Pago Social (`POST /payments/social-unlock`) en WhatsApp, X, Facebook y Threads.
3. El pase desbloqueado por Pago Social habilita consultas posteriores sin bloqueo HTTP 402.
4. Presencia de los componentes del Muro Social y Pago Externo en el frontend.
5. Ausencia total de residuos de la pasarela de Mercado Pago.
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest
import uuid

from fastapi.testclient import TestClient
import fitz

from core.config import ServerSecurityConfig
import main


def _create_sample_pdf(title: str, text: str) -> bytes:
    doc = fitz.open()
    try:
        p1 = doc.new_page()
        p1.insert_text((50, 70), f"{title}\n{text}")
        return doc.tobytes()
    finally:
        doc.close()


class TestPaymentSocialAndExternal(unittest.TestCase):
    """Suite de pruebas para el Muro de Pago Social y el enlace de pago internacional."""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.u_tag = uuid.uuid4().hex[:8]
        self.email = f"social_{self.u_tag}@gmail.com"
        login = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{self.email}:Usuario Social"},
        )
        self.assertEqual(login.status_code, 200)
        self.token = login.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_payment_config_endpoint_and_env_injection(self) -> None:
        """Comprueba que /payments/config devuelva micro_payment_url, monthly_payment_url y textos de difusión."""
        resp = self.client.get("/payments/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("micro_payment_url", data)
        self.assertIn("monthly_payment_url", data)
        self.assertIn("external_payment_url", data)
        self.assertIn("social_share_text", data)
        self.assertIn("app_url", data)
        self.assertIn("@DrHArgentina", data["social_share_text"])
        self.assertTrue(data["micro_payment_url"].startswith("http"))
        self.assertTrue(data["monthly_payment_url"].startswith("http"))

    def test_security_config_env_variable_overrides(self) -> None:
        """Verifica que las variables EXTERNAL_MICRO_PAYMENT_URL y EXTERNAL_MONTHLY_PAYMENT_URL se inyecten."""
        old_micro = os.environ.get("EXTERNAL_MICRO_PAYMENT_URL")
        old_monthly = os.environ.get("EXTERNAL_MONTHLY_PAYMENT_URL")
        try:
            os.environ["EXTERNAL_MICRO_PAYMENT_URL"] = "https://lemon-squeezy.com/buy/micro-test"
            os.environ["EXTERNAL_MONTHLY_PAYMENT_URL"] = "https://lemon-squeezy.com/buy/monthly-test"
            cfg = ServerSecurityConfig.load()
            self.assertEqual(cfg.external_micro_payment_url, "https://lemon-squeezy.com/buy/micro-test")
            self.assertEqual(cfg.external_monthly_payment_url, "https://lemon-squeezy.com/buy/monthly-test")
            self.assertEqual(cfg.external_payment_url, "https://lemon-squeezy.com/buy/monthly-test")
        finally:
            if old_micro is not None:
                os.environ["EXTERNAL_MICRO_PAYMENT_URL"] = old_micro
            else:
                os.environ.pop("EXTERNAL_MICRO_PAYMENT_URL", None)
            if old_monthly is not None:
                os.environ["EXTERNAL_MONTHLY_PAYMENT_URL"] = old_monthly
            else:
                os.environ.pop("EXTERNAL_MONTHLY_PAYMENT_URL", None)

    def test_social_unlock_all_networks_and_database_persistence(self) -> None:
        """Verifica que compartir en cualquiera de las 4 redes otorgue el pase completo."""
        for network in ("whatsapp", "x", "facebook", "threads"):
            sub_tag = uuid.uuid4().hex[:6]
            sub_email = f"net_{network}_{sub_tag}@gmail.com"
            sub_login = self.client.post(
                "/auth/google",
                json={"credential": f"mock_google_oauth:{sub_email}:User {network}"},
            )
            sub_headers = {"Authorization": f"Bearer {sub_login.json()['access_token']}"}

            # Antes de compartir, no tiene pase pago
            resp_pre = self.client.get("/auth/me", headers=sub_headers)
            self.assertFalse(resp_pre.json()["has_paid_pass"])

            # Clic en botón de compartir red social
            resp_unlock = self.client.post(
                "/payments/social-unlock",
                json={"network": network},
                headers=sub_headers,
            )
            self.assertEqual(resp_unlock.status_code, 200)
            res_json = resp_unlock.json()
            self.assertEqual(res_json["status"], "UNLOCKED")
            self.assertTrue(res_json["has_paid_pass"])
            self.assertIn(network.capitalize(), res_json["message"])

            # Después de compartir, tiene pase activo en base de datos
            resp_post = self.client.get("/auth/me", headers=sub_headers)
            self.assertTrue(resp_post.json()["has_paid_pass"])

    def test_social_unlock_bypasses_402_on_subsequent_consultations(self) -> None:
        """Verifica que un usuario que agotó su cuota gratuita pueda seguir consultando tras el desbloqueo social."""
        # Consulta 1: Consumir la prueba gratuita
        pdf1 = _create_sample_pdf(f"Caso Inicial {self.u_tag}", "Reclamo despido laboral sin causa.")
        resp1 = self.client.post(
            "/analyze-expediente",
            files={"file": ("caso_1.pdf", pdf1, "application/pdf")},
            data={"user_verbal_statement": "Primera consulta gratuita."},
            headers=self.headers,
        )
        self.assertEqual(resp1.status_code, 200)

        # Consulta 2 sin pase: Bloqueado con HTTP 402
        pdf2 = _create_sample_pdf(f"Caso Segundo {self.u_tag}", "Reclamo liquidacion final rubros adeudados.")
        resp2 = self.client.post(
            "/analyze-expediente",
            files={"file": ("caso_2.pdf", pdf2, "application/pdf")},
            data={"user_verbal_statement": "Segunda consulta sin pase."},
            headers=self.headers,
        )
        self.assertEqual(resp2.status_code, 402)

        # Usuario ejecuta el Pago Social (compartir en X)
        resp_unlock = self.client.post(
            "/payments/social-unlock",
            json={"network": "x"},
            headers=self.headers,
        )
        self.assertEqual(resp_unlock.status_code, 200)

        # Consulta 2 reintentada: Ahora se procesa exitosamente (HTTP 200) gracias al pase social
        pdf3 = _create_sample_pdf(f"Caso Tercero {self.u_tag}", "Reclamo pericia médica contable.")
        resp3 = self.client.post(
            "/analyze-expediente",
            files={"file": ("caso_3.pdf", pdf3, "application/pdf")},
            data={"user_verbal_statement": "Tercera consulta con pase social desbloqueado."},
            headers=self.headers,
        )
        self.assertEqual(resp3.status_code, 200)
        self.assertIn("fuero", resp3.json())

    def test_frontend_has_social_wall_and_no_mercadopago_remnants(self) -> None:
        """Verifica que la PWA contenga los 4 botones sociales, el botón MoR y nada de Mercado Pago."""
        resp_index = self.client.get("/")
        self.assertEqual(resp_index.status_code, 200)
        html_text = resp_index.text

        # Elementos del Muro Social y Pago Externo presentes
        self.assertIn('id="social-paywall-card"', html_text)
        self.assertIn('id="btn-share-whatsapp"', html_text)
        self.assertIn('id="btn-share-x"', html_text)
        self.assertIn('id="btn-share-facebook"', html_text)
        self.assertIn('id="btn-share-threads"', html_text)
        self.assertIn('id="btn-pay-micro"', html_text)
        self.assertIn('id="btn-pay-monthly"', html_text)
        self.assertIn('id="btn-pay-external"', html_text)
        self.assertIn("Pack Micro-Consultas / Multas (USD 2.00)", html_text)
        self.assertIn("Pase Mensual por Expediente (USD 5.00)", html_text)

        # Eliminación de Mercado Pago en HTML y JS
        self.assertNotIn("Mercado Pago", html_text)
        self.assertNotIn("btn-pay-mercadopago", html_text)

        resp_js = self.client.get("/app.js")
        self.assertEqual(resp_js.status_code, 200)
        js_text = resp_js.text
        self.assertIn("/payments/social-unlock", js_text)
        self.assertIn("/payments/config", js_text)
        self.assertNotIn("mercadopago", js_text.lower())
        self.assertNotIn("create-preference", js_text)


if __name__ == "__main__":
    unittest.main()
