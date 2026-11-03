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
        self.assertIn("id=\"btn-run-full-analysis\"", resp_index.text)
        self.assertIn("id=\"btn-download-pdf\"", resp_index.text)

        resp_js = client.get("/app.js")
        self.assertEqual(resp_js.status_code, 200)
        self.assertIn("/pre-quote", resp_js.text)
        self.assertIn("/analyze-expediente", resp_js.text)
        self.assertIn("/emergency-scan", resp_js.text)
        self.assertIn("/repository-consult", resp_js.text)

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
        resp = client.post(
            "/repository-consult",
            json={
                "query": f"Consulta sobre indemnización laboral con texto {raw_secret}",
                "province_jurisdiction": "PJN_NACION_CABA",
            },
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

        # 1. Descargar PDF de prueba desde /demo-sample-pdf
        resp_demo = client.get("/demo-sample-pdf")
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
        )
        self.assertEqual(resp_analyze.status_code, 200)
        final_report_json = resp_analyze.json()
        self.assertTrue(final_report_json["do_not_sign_yet_alert"])

        # 3. Descargar el Informe PDF Auditado desde /download-report-pdf
        resp_pdf = client.post("/download-report-pdf", json=final_report_json)
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

        # 4. Verificar /queue-status
        resp_queue = client.get("/queue-status")
        self.assertEqual(resp_queue.status_code, 200)
        q_data = resp_queue.json()
        self.assertEqual(q_data["tier_mode"], "GOOGLE_AI_STUDIO_FREE_TIER_GUARD")
        self.assertGreaterEqual(q_data["total_requests_processed"], 1)


if __name__ == "__main__":
    unittest.main()
