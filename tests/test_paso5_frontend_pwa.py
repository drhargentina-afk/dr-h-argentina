"""Suite de Pruebas Unitarias e Integración del Paso 5 (`frontend_pwa/`, `core/config.py` y `core/rate_limiter_queue.py`).

Verifica:
1. Existencia, integridad y servido HTTP de los 4 archivos de la PWA (`index.html`, `app.js`, `sw.js`, `manifest.json`).
2. `ServerSecurityConfig` y `ApiKeyLeakGuardMiddleware`:
   - Carga de la API Key desde `.env` en `SecretStr`.
   - Garantía absoluta de que la clave jamás se expone en el cliente PWA ni en respuestas HTTP (redacción automática).
3. `RateLimitQueueHandler` (`core/rate_limiter_queue.py`):
   - Intercepción de errores `HTTP 429 Too Many Requests` / `RESOURCE_EXHAUSTED`.
   - Enfriamiento exponencial determinista con precisión `Decimal` (`1.50s`, `3.00s`, `6.00s`), reintento automático exitoso y costo $0.00.
4. Integración de punta a punta de `/demo-sample-pdf`, `/analyze-expediente`, `/download-report-pdf` y `/queue-status`.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

import fitz  # PyMuPDF
from fastapi.testclient import TestClient

from core.config import ServerSecurityConfig
from core.rate_limiter_queue import RateLimitExceededError, RateLimitQueueHandler
from main import FRONTEND_PWA_DIR, app
from tests.admin_auth_helper import admin_credentials, get_admin_headers


class TestPaso5FrontendPwaAndRateLimiter(unittest.TestCase):
    """Pruebas deterministas del Cierre Definitivo (Paso 5 de 5)."""

    def test_pwa_files_exist_and_serve_over_http(self) -> None:
        """Verifica que index.html, app.js, sw.js y manifest.json existan y se sirvan en la raíz."""
        for filename in ("index.html", "app.js", "sw.js", "manifest.json"):
            fpath = FRONTEND_PWA_DIR / filename
            self.assertTrue(fpath.exists(), f"Falta el archivo obligatorio de la PWA: {fpath}")
            self.assertGreater(fpath.stat().st_size, 200)

        client = TestClient(app)

        resp_index = client.get("/")
        self.assertEqual(resp_index.status_code, 200)
        self.assertIn("Dr. H Argentina", resp_index.text)
        self.assertIn('meta name="color-scheme" content="light dark"', resp_index.text)
        self.assertIn('id="login-section"', resp_index.text)
        self.assertIn('id="g_id_onload"', resp_index.text)
        self.assertIn('class="g_id_signin"', resp_index.text)
        self.assertNotIn('id="input-google-email"', resp_index.text)
        self.assertNotIn('id="btn-continue-google"', resp_index.text)
        self.assertNotIn('id="btn-demo-pdf"', resp_index.text)
        self.assertNotIn("Cargar Ejemplo Práctico", resp_index.text)
        self.assertNotIn("position: sticky", resp_index.text)
        self.assertIn('id="btn-camera-urgent"', resp_index.text)
        self.assertIn("Escáner de urgencia: No firmes todavía", resp_index.text)
        self.assertIn('id="select-province-jurisdiction"', resp_index.text)
        self.assertIn('id="input-city-locality"', resp_index.text)
        self.assertIn("Ciudad / Localidad:", resp_index.text)
        self.assertIn('id="btn-run-full-analysis"', resp_index.text)
        self.assertIn('id="btn-open-whatsapp"', resp_index.text)
        self.assertIn('id="btn-copy-email"', resp_index.text)
        self.assertIn('id="btn-download-pdf"', resp_index.text)
        self.assertIn('id="cases-dashboard-section"', resp_index.text)
        self.assertIn('id="social-paywall-card"', resp_index.text)
        self.assertIn('id="btn-share-whatsapp"', resp_index.text)
        self.assertIn('id="btn-share-instagram"', resp_index.text)
        self.assertIn('id="btn-pay-external"', resp_index.text)
        self.assertIn('id="btn-primeros-pasos"', resp_index.text)
        self.assertIn('id="modal-primeros-pasos"', resp_index.text)
        self.assertIn(
            '<meta name="google-signin-client_id" content="373505917083-t812fbg0fa29k4q4vd2vcj0vdbgk8kq9.apps.googleusercontent.com">',
            resp_index.text,
        )
        self.assertIn(
            '<button id="btn-modal-entendido" class="close-modal bg-blue-600 text-white px-4 py-2 rounded-lg">Entendido</button>',
            resp_index.text,
        )
        self.assertIn("max-h-[85vh]", resp_index.text)
        self.assertIn("overflow-y-auto", resp_index.text)
        self.assertIn('<meta property="og:title" content="Dr. H Argentina - Asistente Legal">', resp_index.text)
        self.assertIn(
            '<meta property="og:description" content="Tu asistente legal en lenguaje claro. Subí tu expediente y entendelo en criollo.">',
            resp_index.text,
        )
        self.assertIn('property="og:url" content="https://dr-h-argentina.onrender.com', resp_index.text)
        self.assertIn('<meta property="og:type" content="website">', resp_index.text)
        self.assertIn(
            '<meta property="og:image" content="https://dr-h-argentina.onrender.com/icons/icon-512x512.png">',
            resp_index.text,
        )
        self.assertIn(
            '<meta property="og:image:secure_url" content="https://dr-h-argentina.onrender.com/icons/icon-512x512.png">',
            resp_index.text,
        )
        self.assertIn("Pack Micro-Consultas / Recargas: $4.000 ARS", resp_index.text)
        self.assertIn("Pase Mensual por Expediente: $20.000 ARS", resp_index.text)
        self.assertIn(
            "Legislación actualizada automáticamente todos los días a las 07:00 AM",
            resp_index.text,
        )

        # Garantizar que se ocultó toda la jerga técnica de la vista pública
        for forbidden_jargon in (
            "Nodo Capa-País",
            "Zero-Storage",
            "Cola Gratuita",
            "Sin conexión temporal",
            "Páginas Totales",
            "Basura / Blancos",
            "Agente000",
            "Auto-Triage",
            "Contraste Dicho vs. Foja",
            "Puente Gamuza",
        ):
            self.assertNotIn(forbidden_jargon, resp_index.text)

        resp_js = client.get("/app.js")
        self.assertEqual(resp_js.status_code, 200)
        self.assertIn("/pre-quote", resp_js.text)
        self.assertIn("/analyze-expediente", resp_js.text)
        self.assertIn("/emergency-scan", resp_js.text)
        self.assertIn("/repository-consult", resp_js.text)
        self.assertIn("input-city-locality", resp_js.text)
        self.assertIn("city_locality", resp_js.text)
        self.assertIn("Analizando la documentación del caso...", resp_js.text)
        self.assertIn("Revisando jurisprudencia aplicable...", resp_js.text)
        self.assertIn("Redactando el informe final...", resp_js.text)
        self.assertIn("El análisis es profundo, por favor no cierres la pantalla...", resp_js.text)
        self.assertIn("El servidor está muy ocupado, intentá de nuevo en unos segundos", resp_js.text)
        self.assertNotIn("AbortController", resp_js.text)
        self.assertNotIn("120000", resp_js.text)
        self.assertIn("loading-spinner", resp_index.text)
        self.assertIn("¡Copiado!", resp_js.text)
        self.assertIn("navigator.clipboard.writeText", resp_js.text)
        self.assertNotIn("Analizando tu caso con Agente000 en memoria RAM...", resp_js.text)
        self.assertNotIn("loadDemoExample", resp_js.text)

        resp_icon = client.get("/icons/icon-512x512.png")
        self.assertEqual(resp_icon.status_code, 200)
        self.assertEqual(resp_icon.headers.get("content-type"), "image/png")

        resp_icon_head = client.head("/icons/icon-512x512.png")
        self.assertEqual(resp_icon_head.status_code, 200)

        resp_sw = client.get("/sw.js")
        self.assertEqual(resp_sw.status_code, 200)
        self.assertEqual(resp_sw.headers.get("Service-Worker-Allowed"), "/")
        self.assertIn("SKIP_WAITING", resp_sw.text)

        resp_manifest = client.get("/manifest.json")
        self.assertEqual(resp_manifest.status_code, 200)
        manifest_data = resp_manifest.json()
        self.assertEqual(manifest_data["short_name"], "Dr. H AR")
        self.assertEqual(manifest_data["lang"], "es-AR")

    def test_server_security_config_and_api_key_leak_guard(self) -> None:
        """Comprueba que la API Key resida en `.env` del servidor y jamás se filtre al navegador."""
        dotenv_path = Path(__file__).resolve().parent.parent / ".env"
        self.assertTrue(dotenv_path.exists())

        cfg = ServerSecurityConfig.load(dotenv_path)
        self.assertTrue(cfg.is_key_configured())
        raw_secret = cfg.google_ai_studio_api_key.get_secret_value()
        self.assertGreaterEqual(len(raw_secret), 16)
        self.assertEqual(
            cfg.get_masked_key_fingerprint(),
            f"{raw_secret[:4]}...{raw_secret[-4:]}",
        )
        # La representación en texto del modelo nunca debe mostrar la clave en claro
        self.assertNotIn(raw_secret, repr(cfg))

        # Ningún archivo del frontend_pwa puede contener la clave del servidor
        for filename in ("index.html", "app.js", "sw.js", "manifest.json"):
            content = (FRONTEND_PWA_DIR / filename).read_text(encoding="utf-8")
            self.assertNotIn(raw_secret, content)

        # Si por accidente una consulta o error intentara devolver la clave en el cuerpo HTTP,
        # el middleware ApiKeyLeakGuardMiddleware la redacta automáticamente antes de llegar al cliente.
        client = TestClient(app)
        admin_headers = get_admin_headers(client)
        resp = client.post(
            "/repository-consult",
            json={
                "query": f"Consulta sobre indemnización laboral con texto {raw_secret}",
                "province_jurisdiction": "PJN_NACION_CABA",
            },
            headers=admin_headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(raw_secret, resp.text)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")

    def test_rate_limiter_queue_429_exponential_cooling_and_recovery(self) -> None:
        """Verifica que RateLimitQueueHandler intercepte HTTP 429, aplique backoff Decimal y reintente sin fallar."""
        recorded_sleeps: list[float] = []

        handler = RateLimitQueueHandler(
            max_rpm=15,
            max_retries=4,
            base_cooling_seconds=Decimal("1.50"),
            sleep_fn=lambda secs: recorded_sleeps.append(secs),
        )

        call_counter = {"attempts": 0}

        def simulated_gemini_call(payload_text: str) -> str:
            call_counter["attempts"] += 1
            if call_counter["attempts"] <= 2:
                raise RateLimitExceededError("HTTP 429 RESOURCE_EXHAUSTED: Free tier RPM reached")
            return f"DICTAMEN_OK:{payload_text}"

        result = handler.execute_with_cooling(simulated_gemini_call, "Expediente_Laboral")
        self.assertEqual(result, "DICTAMEN_OK:Expediente_Laboral")
        self.assertEqual(call_counter["attempts"], 3)

        # Intento 0 fallido -> espera 1.50s; Intento 1 fallido -> espera 3.00s (Total = 4.50s)
        self.assertEqual(len(recorded_sleeps), 2)
        self.assertAlmostEqual(recorded_sleeps[0], 1.50, places=2)
        self.assertAlmostEqual(recorded_sleeps[1], 3.00, places=2)

        snapshot = handler.get_status_snapshot()
        self.assertEqual(snapshot.total_requests_processed, 1)
        self.assertEqual(snapshot.total_429_intercepted, 2)
        self.assertEqual(snapshot.total_cooling_wait_seconds, Decimal("4.50"))
        self.assertEqual(snapshot.current_queue_depth, 0)
        self.assertIn("HTTP 429 interceptado", str(snapshot.last_cooling_event_reason))

    def test_end_to_end_demo_pdf_analyze_and_pdf_download(self) -> None:
        """Prueba el circuito completo de la PWA: obtener PDF demo -> analizar con Agente000 -> descargar PDF auditado."""
        client = TestClient(app)
        admin_headers = get_admin_headers(client)

        # 1. Descargar PDF de prueba desde /demo-sample-pdf
        resp_demo = client.get("/demo-sample-pdf", headers=admin_headers)
        self.assertEqual(resp_demo.status_code, 200)
        self.assertTrue(resp_demo.content.startswith(b"%PDF-"))

        # 2. Analizar el expediente con Agente000
        resp_analyze = client.post(
            "/analyze-expediente",
            files={"file": ("demo_scba.pdf", resp_demo.content, "application/pdf")},
            data={
                "user_verbal_statement": "Me informaron que ya pidió sentencia y que abone $45000 para tasa.",
                "claimed_lawyer_fee_pct": "30.00",
                "upfront_cost_requested_ars": "45000.00",
            },
            headers=admin_headers,
        )
        self.assertEqual(resp_analyze.status_code, 200)
        final_report_json = resp_analyze.json()
        self.assertTrue(final_report_json["do_not_sign_yet_alert"])

        # 3. Descargar el Informe PDF Auditado desde /download-report-pdf
        resp_pdf = client.post("/download-report-pdf", json=final_report_json, headers=admin_headers)
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf.headers.get("content-type"), "application/pdf")
        self.assertTrue(resp_pdf.content.startswith(b"%PDF-"))

        doc = fitz.open(stream=resp_pdf.content, filetype="pdf")
        try:
            self.assertGreaterEqual(doc.page_count, 1)
            page_text = doc.load_page(0).get_text("text")
            self.assertIn("DR. H ARGENTINA", page_text)
        finally:
            doc.close()

        # 3b. Verificar que el payload sintetizado de consulta verbal rápida nunca devuelva HTTP 422
        synthesized_verbal_payload = {
            "report_id": "drh-12345678",
            "session_id": "ses-12345",
            "generated_at_iso": "2026-11-08T00:00:00Z",
            "fuero": "03_laboral",
            "province_jurisdiction": "PJN_NACION_CABA",
            "citizen_plain_summary": "Consulta verbal sobre indemnización laboral y plazos.",
            "verified_timeline_cards": [],
            "audit_and_deadlines_section": "Revisar plazos antes de firmar.",
            "do_not_sign_yet_alert": True,
            "do_not_sign_alert_box": "No firmes sin revisar.",
            "questions_for_your_lawyer": ["¿Cuál es el estado de la causa?"],
            "whatsapp_ready_message": "Hola Doc, le escribo por mi consulta.",
            "audio_script_90_seconds": "Resumen hablado breve.",
            "zero_storage_confirmation": "Privacidad garantizada.",
            "legal_shield_disclaimer": "Orientación ciudadana.",
        }
        resp_verbal_pdf = client.post(
            "/download-report-pdf",
            json=synthesized_verbal_payload,
            headers=admin_headers,
        )
        self.assertEqual(resp_verbal_pdf.status_code, 200)
        self.assertEqual(resp_verbal_pdf.headers.get("content-type"), "application/pdf")
        self.assertTrue(resp_verbal_pdf.content.startswith(b"%PDF-"))

        # 4. Verificar /queue-status
        resp_queue = client.get("/queue-status", headers=admin_headers)
        self.assertEqual(resp_queue.status_code, 200)
        q_data = resp_queue.json()
        self.assertEqual(q_data["tier_mode"], "GOOGLE_AI_STUDIO_FREE_TIER_GUARD")
        self.assertGreaterEqual(q_data["total_requests_processed"], 1)

    def test_admin_auth_and_docs_lockdown(self) -> None:
        """Verifica cierre total de /docs público y funcionamiento del login y token de administrador."""
        client = TestClient(app)
        admin_user, admin_pass = admin_credentials()

        # 1. /docs, /redoc y /openapi.json deben rechazar acceso no autenticado (401)
        for doc_path in ("/docs", "/redoc", "/openapi.json"):
            resp_doc_unauth = client.get(doc_path)
            self.assertEqual(
                resp_doc_unauth.status_code,
                401,
                f"El endpoint {doc_path} no debe ser público",
            )
            self.assertIn("WWW-Authenticate", resp_doc_unauth.headers)

        # 2. Con credenciales Basic correctas del Admin, /docs y /openapi.json responden 200
        import base64
        basic_b64 = base64.b64encode(f"{admin_user}:{admin_pass}".encode("utf-8")).decode("utf-8")
        basic_headers = {"Authorization": f"Basic {basic_b64}"}

        resp_openapi = client.get("/openapi.json", headers=basic_headers)
        self.assertEqual(resp_openapi.status_code, 200)
        self.assertEqual(resp_openapi.json()["info"]["title"], "Dr. H Argentina — Panel Privado de Administración")

        resp_docs = client.get("/docs", headers=basic_headers)
        self.assertEqual(resp_docs.status_code, 200)
        self.assertIn("swagger-ui", resp_docs.text)

        # 3. Login con credenciales incorrectas debe fallar con 401
        resp_bad_login = client.post("/auth/login", json={"username": admin_user, "password": "WrongPassword123!"})
        self.assertEqual(resp_bad_login.status_code, 401)

        # 4. Login con credenciales correctas emite token Bearer
        resp_good_login = client.post("/auth/login", json={"username": admin_user, "password": admin_pass})
        self.assertEqual(resp_good_login.status_code, 200)
        login_data = resp_good_login.json()
        self.assertIn("access_token", login_data)
        token = login_data["access_token"]
        bearer_headers = {"Authorization": f"Bearer {token}"}

        # 5. /auth/me valida el token
        resp_me = client.get("/auth/me", headers=bearer_headers)
        self.assertEqual(resp_me.status_code, 200)
        self.assertTrue(resp_me.json()["is_admin"])

        # 6. /auth/logout revoca el token
        resp_logout = client.post("/auth/logout", headers=bearer_headers)
        self.assertEqual(resp_logout.status_code, 204)

        # Ahora ese token revocado debe fallar con 401
        resp_after_logout = client.get("/auth/me", headers=bearer_headers)
        self.assertEqual(resp_after_logout.status_code, 401)


if __name__ == "__main__":
    unittest.main()
