"""Tests de Integración y Validación — Refactorización MVP Dr. H Argentina.

Valida:
1. Autenticación Google OAuth 2.0 (Cero contraseñas) y validación de perfiles.
2. Filtro Criptográfico Anti-Abuso (SHA-256): rechazo HTTP 403 con mensaje exacto
   "Este documento ya fue procesado bajo una prueba gratuita".
3. Regla de Gratuidad (1 sola consulta inicial gratis): rechazo HTTP 402 al intentar una segunda.
4. Dashboard de Causas y Seguimiento Asíncrono ("Agregar Movimiento" de 1-2 fojas en RAM).
5. Pasarela de Pagos Mercado Pago en ARS (Preferencia, Webhook y Mock-Activate).
"""
from __future__ import annotations

import hashlib
import unittest
import uuid

from fastapi.testclient import TestClient
import fitz

from main import app, db


def _create_sample_pdf(title: str, text: str) -> bytes:
    doc = fitz.open()
    try:
        p1 = doc.new_page()
        p1.insert_text((50, 70), f"{title}\n{text}")
        return doc.tobytes()
    finally:
        doc.close()


class TestMvpRefactorRoadmap(unittest.TestCase):
    """Batería de pruebas para las directrices de la Refactorización MVP."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_google_oauth_login_profile_and_logout(self) -> None:
        """Verifica el flujo completo de Google OAuth 2.0 con token de sesión."""
        u_tag = uuid.uuid4().hex[:6]
        user_email = f"carlos.{u_tag}@gmail.com"

        # 1. Login con credencial sintética de Google
        resp_login = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{user_email}:Carlos Perez"},
        )
        self.assertEqual(resp_login.status_code, 200)
        auth_data = resp_login.json()
        self.assertIn("access_token", auth_data)
        token = auth_data["access_token"]
        user_info = auth_data["user"]
        self.assertEqual(user_info["email"], user_email)
        self.assertEqual(user_info["free_consultations_used"], 0)
        self.assertTrue(user_info["can_consult_free"])

        # 2. Consultar perfil con Bearer token
        bearer_header = {"Authorization": f"Bearer {token}"}
        resp_me = self.client.get("/auth/me", headers=bearer_header)
        self.assertEqual(resp_me.status_code, 200)
        self.assertEqual(resp_me.json()["email"], user_email)

        # 3. Logout y verificación de revocación
        resp_logout = self.client.post("/auth/logout", headers=bearer_header)
        self.assertEqual(resp_logout.status_code, 204)

        resp_me_after = self.client.get("/auth/me", headers=bearer_header)
        self.assertEqual(resp_me_after.status_code, 401)

    def test_cryptographic_sha256_anti_abuse_filter(self) -> None:
        """Verifica que si un documento ya fue procesado gratis, otro usuario reciba HTTP 403."""
        u_tag = uuid.uuid4().hex[:8]
        user1_email = f"user1_{u_tag}@gmail.com"
        user2_email = f"user2_{u_tag}@gmail.com"

        # Crear un PDF con contenido único para esta ejecución
        pdf_bytes = _create_sample_pdf(
            f"PODER JUDICIAL SCBA - RUN_{u_tag}",
            "Demanda por despido sin causa. Perez c/ Textil SA. Se intima al pago de liquidacion.",
        )
        sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()

        # Usuario 1 inicia sesión con Google
        login1 = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{user1_email}:Usuario Uno"},
        )
        self.assertEqual(login1.status_code, 200)
        token1 = login1.json()["access_token"]
        headers1 = {"Authorization": f"Bearer {token1}"}

        # Usuario 1 procesa su consulta gratuita con el documento
        resp_analyze_1 = self.client.post(
            "/analyze-expediente",
            files={"file": ("expediente_1.pdf", pdf_bytes, "application/pdf")},
            data={"user_verbal_statement": "Me despidieron y quiero saber si lo que me ofrecen está bien."},
            headers=headers1,
        )
        self.assertEqual(resp_analyze_1.status_code, 200)
        self.assertEqual(resp_analyze_1.json()["fuero"], "03_laboral")

        # Verificar que el hash quedó registrado en la base de datos
        self.assertTrue(db.is_document_already_processed_free(sha256_hash))

        # Usuario 2 (otra cuenta de Google distinta) intenta subir el MISMO archivo exacto
        login2 = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{user2_email}:Usuario Dos"},
        )
        self.assertEqual(login2.status_code, 200)
        token2 = login2.json()["access_token"]
        headers2 = {"Authorization": f"Bearer {token2}"}

        resp_analyze_2 = self.client.post(
            "/analyze-expediente",
            files={"file": ("mismo_expediente.pdf", pdf_bytes, "application/pdf")},
            data={"user_verbal_statement": "Quiero revisar el mismo caso gratis con otra cuenta."},
            headers=headers2,
        )

        # Debe ser bloqueado por el filtro criptográfico con HTTP 403 y el mensaje exacto exigido
        self.assertEqual(resp_analyze_2.status_code, 403)
        self.assertEqual(
            resp_analyze_2.json()["detail"],
            "Este documento ya fue procesado bajo una prueba gratuita",
        )

    def test_single_free_consultation_rule_enforcement(self) -> None:
        """Verifica que un usuario gratuito sea bloqueado con HTTP 402 al intentar una segunda consulta."""
        u_tag = uuid.uuid4().hex[:8]
        user_email = f"mariana_{u_tag}@gmail.com"

        login = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{user_email}:Mariana Gomez"},
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Consulta 1: PDF nuevo
        pdf1 = _create_sample_pdf(f"Expediente Mariana 1 - {u_tag}", "Reclamo cuota alimentaria provisoria.")
        resp1 = self.client.post(
            "/analyze-expediente",
            files={"file": ("caso_alimentos.pdf", pdf1, "application/pdf")},
            data={"user_verbal_statement": "Quiero saber cuánto me corresponde por la canasta de crianza."},
            headers=headers,
        )
        self.assertEqual(resp1.status_code, 200)

        # Consulta 2: Mismo usuario intenta subir un segundo PDF distinto sin abonar el pase
        pdf2 = _create_sample_pdf(f"Expediente Mariana 2 - {u_tag}", "Reclamo contrato de locacion y desalojo.")
        resp2 = self.client.post(
            "/analyze-expediente",
            files={"file": ("caso_alquiler.pdf", pdf2, "application/pdf")},
            data={"user_verbal_statement": "Ahora quiero que me revisen este otro contrato."},
            headers=headers,
        )
        self.assertEqual(resp2.status_code, 402)
        self.assertIn("Ya utilizaste tu consulta gratuita de prueba", resp2.json()["detail"])

    def test_case_tracking_and_add_movement_in_ram(self) -> None:
        """Verifica la persistencia en el Dashboard y la actualización del semáforo con novedades procesales."""
        u_tag = uuid.uuid4().hex[:8]
        user_email = f"roberto_{u_tag}@gmail.com"

        login = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{user_email}:Roberto Seguimiento"},
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Crear caso inicial
        pdf_inicial = _create_sample_pdf(
            f"SCBA - EXPTE ROBERTO C/ AUTOMOTORES SA - {u_tag}",
            "Demanda por accidente de trabajo ART. Fecha: 01/02/2026.",
        )
        resp_init = self.client.post(
            "/analyze-expediente",
            files={"file": ("accidente_art.pdf", pdf_inicial, "application/pdf")},
            data={"user_verbal_statement": "Tuve un accidente en la fábrica."},
            headers=headers,
        )
        self.assertEqual(resp_init.status_code, 200)

        # 2. Consultar el Dashboard (/cases)
        resp_cases = self.client.get("/cases", headers=headers)
        self.assertEqual(resp_cases.status_code, 200)
        cases_list = resp_cases.json()
        self.assertGreaterEqual(len(cases_list), 1)

        case_obj = cases_list[0]
        case_id = case_obj["case_id"]
        self.assertIn(case_obj["current_status"], ("VERDE", "AMARILLO", "ROJO"))
        self.assertGreaterEqual(case_obj["movements_count"], 1)

        # 3. Subir una novedad procesal de 1 foja (Agregar Movimiento)
        pdf_novedad = _create_sample_pdf(
            f"CÉDULA DE NOTIFICACIÓN ELECTRÓNICA - FOJA 1 - {u_tag}",
            "Quilmes, 20/04/2026. Se corre traslado de la pericia médica por el término de cinco días bajo apercibimiento.\n"
            "Se acompaña propuesta de acuerdo con renuncia expresa de derechos.",
        )
        resp_mov = self.client.post(
            f"/cases/{case_id}/movement",
            files={"file": ("novedad_foja_nueva.pdf", pdf_novedad, "application/pdf")},
            headers=headers,
        )
        self.assertEqual(resp_mov.status_code, 200)
        mov_data = resp_mov.json()
        self.assertEqual(mov_data["status"], "SUCCESS")
        self.assertIn("new_status", mov_data)
        self.assertIn("movement", mov_data)

        # 4. Verificar detalle de la causa con su movimiento agregado
        resp_detail = self.client.get(f"/cases/{case_id}", headers=headers)
        self.assertEqual(resp_detail.status_code, 200)
        detail = resp_detail.json()
        self.assertGreaterEqual(len(detail["movements"]), 1)

    def test_mercadopago_preference_creation_and_paid_pass(self) -> None:
        """Verifica la pasarela de pagos de Mercado Pago en ARS y la activación de pase."""
        u_tag = uuid.uuid4().hex[:8]
        user_email = f"elena_{u_tag}@gmail.com"

        login = self.client.post(
            "/auth/google",
            json={"credential": f"mock_google_oauth:{user_email}:Elena Pagos"},
        )
        self.assertEqual(login.status_code, 200)
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Crear preferencia para consulta individual ($9.500 ARS)
        resp_pref_single = self.client.post(
            "/payments/create-preference",
            json={"plan_type": "SINGLE_CONSULTATION"},
            headers=headers,
        )
        self.assertEqual(resp_pref_single.status_code, 200)
        pref1 = resp_pref_single.json()
        self.assertEqual(pref1["currency"], "ARS")
        self.assertEqual(pref1["amount"], "9500.00")
        self.assertTrue(pref1["init_point"].startswith("http"))

        # 2. Crear preferencia para pase ilimitado ($24.500 ARS)
        resp_pref_pass = self.client.post(
            "/payments/create-preference",
            json={"plan_type": "UNLIMITED_PASS"},
            headers=headers,
        )
        self.assertEqual(resp_pref_pass.status_code, 200)
        pref2 = resp_pref_pass.json()
        self.assertEqual(pref2["amount"], "24500.00")

        # 3. Webhook en modo MOCK: sin verificación contra la API de MP nunca se acredita nada
        resp_webhook = self.client.post(
            "/payments/webhook",
            json={
                "type": "payment",
                "data": {"id": "1234567890"},
            },
        )
        self.assertEqual(resp_webhook.status_code, 200)
        self.assertEqual(resp_webhook.json()["status"], "IGNORED")
        self.assertFalse(self.client.get("/auth/me", headers=headers).json()["has_paid_pass"])

        # 4. Activar pase y verificar que el usuario pueda consultar sin límite
        resp_activate = self.client.post("/payments/mock-activate", headers=headers)
        self.assertEqual(resp_activate.status_code, 200)

        # Ahora el perfil muestra has_paid_pass=True
        resp_me = self.client.get("/auth/me", headers=headers)
        self.assertTrue(resp_me.json()["has_paid_pass"])


if __name__ == "__main__":
    unittest.main()
