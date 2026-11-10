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
        """Verifica que compartir en cualquiera de las redes (WhatsApp, Facebook, Instagram, X) otorgue el pase."""
        for network in ("whatsapp", "facebook", "instagram", "x", "threads"):
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
        self.assertIn('id="btn-share-instagram"', html_text)
        self.assertIn('id="btn-share-threads"', html_text)
        self.assertIn('id="btn-pay-micro"', html_text)
        self.assertIn('id="btn-pay-monthly"', html_text)
        self.assertIn('id="btn-pay-external"', html_text)
        self.assertIn("Pack Micro-Consultas / Recargas: $4.000 ARS", html_text)
        self.assertIn("Pase Mensual por Expediente: $20.000 ARS", html_text)

        # Modal Primeros Pasos y portales judiciales oficiales
        self.assertIn('id="modal-primeros-pasos"', html_text)
        self.assertIn('id="btn-primeros-pasos"', html_text)
        self.assertIn("scw.pjn.gov.ar", html_text)
        self.assertIn("mev.scba.gov.ar", html_text)
        self.assertIn("eje.juscaba.gob.ar", html_text)
        self.assertIn("Mesa de Entradas", html_text)

        # Autenticación Google oficial GIS sin campos manuales duplicados
        self.assertIn('id="g_id_onload"', html_text)
        self.assertIn('class="g_id_signin"', html_text)
        self.assertNotIn('id="input-google-email"', html_text)
        self.assertNotIn('id="btn-continue-google"', html_text)
        self.assertIn('id="report-locked-alert"', html_text)

        # Eliminación total de Mercado Pago en HTML y JS
        self.assertNotIn("Mercado Pago", html_text)
        self.assertNotIn("btn-pay-mercadopago", html_text)

        resp_js = self.client.get("/app.js")
        self.assertEqual(resp_js.status_code, 200)
        js_text = resp_js.text
        self.assertIn("/payments/social-unlock", js_text)
        self.assertIn("/payments/config", js_text)
        self.assertIn("/payments/checkout", js_text)
        self.assertIn("unlockDeliverablesBySocialShare", js_text)
        self.assertIn("social_share_unlocked = true", js_text)
        self.assertIn(
            '"https://api.whatsapp.com/send?text=" + encodeURIComponent(shareText + " " + shareUrl)',
            js_text,
        )
        self.assertIn(
            "¡Texto copiado! Pegalo en tu Historia o publicación de Instagram",
            js_text,
        )
        self.assertIn('window.open("https://www.instagram.com/", "_blank")', js_text)
        self.assertIn("Redirigiendo al pago...", js_text)
        self.assertNotIn("network.capitalize()", js_text)
        self.assertIn("initPrimerosPasosModal", js_text)
        self.assertIn("instagram", js_text)
        self.assertNotIn("mercadopago", js_text.lower())
        self.assertNotIn("create-preference", js_text)

    def test_social_buttons_and_commercial_checkout_hide_paywall_and_unlock_deliverables(self) -> None:
        """Verifica que el clic en cualquiera de los botones sociales o el checkout comercial
        oculta el muro de pago y desbloquea los entregables del informe."""
        resp_js = self.client.get("/app.js")
        self.assertEqual(resp_js.status_code, 200)
        js_text = resp_js.text

        # 1. Verificar que los 5 botones sociales disparan triggerSocialShare -> unlockDeliverablesBySocialShare
        for net_id, net_key in (
            ("btn-share-whatsapp", "whatsapp"),
            ("btn-share-facebook", "facebook"),
            ("btn-share-instagram", "instagram"),
            ("btn-share-x", "x"),
            ("btn-share-threads", "threads"),
        ):
            self.assertIn(net_id, js_text)
            self.assertIn(f'triggerSocialShare("{net_key}")', js_text)

        # 2. Verificar que unlockDeliverablesBySocialShare oculta el muro y revela el informe y botones
        self.assertIn('paywallCard.classList.add("hidden")', js_text)
        self.assertIn('paywallCard.style.display = "none"', js_text)
        self.assertIn('reportSection.classList.remove("hidden")', js_text)
        self.assertIn('reportSection.style.display = "block"', js_text)
        self.assertIn('unlockedDetails.classList.remove("hidden")', js_text)
        self.assertIn('unlockedDetails.style.display = "flex"', js_text)

        # 3. Verificar que POST /payments/checkout funciona para ambos planes ($4.000 ARS micro y $20.000 ARS monthly)
        for plan_type, expected_amount in (("micro", "4000.00"), ("monthly", "20000.00")):
            chk_tag = uuid.uuid4().hex[:6]
            chk_email = f"checkout_{plan_type}_{chk_tag}@gmail.com"
            chk_login = self.client.post(
                "/auth/google",
                json={"credential": f"mock_google_oauth:{chk_email}:Cliente Checkout"},
            )
            chk_headers = {"Authorization": f"Bearer {chk_login.json()['access_token']}"}

            resp_chk = self.client.post(
                "/payments/checkout",
                json={"plan_type": plan_type},
                headers=chk_headers,
            )
            self.assertEqual(resp_chk.status_code, 200)
            chk_data = resp_chk.json()
            self.assertEqual(chk_data["plan_type"], plan_type)
            self.assertEqual(chk_data["amount_ars"], expected_amount)
            self.assertTrue(chk_data["has_paid_pass"])

            # Confirmar que el usuario quedó con pase desbloqueado y puede descargar PDF
            resp_me = self.client.get("/auth/me", headers=chk_headers)
            self.assertTrue(resp_me.json()["has_paid_pass"])


if __name__ == "__main__":
    unittest.main()
