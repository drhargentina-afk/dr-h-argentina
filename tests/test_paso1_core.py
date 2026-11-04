"""Suite de Pruebas Unitarias e Integración del Paso 1 (core/ + main.py).

Verifica con valores frontera:
1. Pre-Cotizador e Higiene PyMuPDF en RAM (hojas en blanco, sellos de 1 línea, numeración real de fojas intacta, tramos de precios con Decimal).
2. Anonimizador Bidireccional en RAM (DNI, CUIL/CUIT con dígito verificador, CBU, patentes, nombres en carátulas) y destrucción de sesión.
3. Ciclo completo de Ida y Vuelta de Agente000Orchestrator (Fases 1 a 4): triage de las 7 áreas judiciales, citas verificadas a foja, auditoría de gratuidad, bandera "¡No Firmes Todavía!", des-anonimización en informe final y purga de memoria.
4. Enrutamiento matutino 07:00 AM (/cron-07am) hacia Sub-Agentes de Fuero y Proyecto Gamuza (Geopolítica + Bolsa y Acciones).
5. Endpoints FastAPI de main.py mediante TestClient.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

import fitz  # PyMuPDF
from fastapi.testclient import TestClient

from core.agente000_orchestrator import Agente000Orchestrator
from core.models import (
    BoletinOficialNormInput,
    EmergencyScanRequest,
    FueroKey,
    ProvinceKey,
    RepositoryConsultRequest,
    SubscriptionTier,
)
from core.privacy_anonymizer import PrivacyAnonymizer
from core.zero_storage_drive import ZeroStorageDriveManager
from main import app
from tests.admin_auth_helper import get_admin_headers


def build_synthetic_expediente_pdf() -> bytes:
    """Construye en memoria RAM un PDF judicial multipágina con fojas reales, hoja en blanco y sello aislado."""
    doc = fitz.open()

    # Foja 1: Carátula y Demanda Laboral en PBA
    p1 = doc.new_page()
    p1.insert_text(
        (50, 70),
        "PROVINCIA DE BUENOS AIRES - PODER JUDICIAL (MEV SCBA)\n"
        "EXPTE: PEREZ JUAN CARLOS C/ METALURGICA DEL SUR S.A. S/ DESPIDO\n"
        "Fecha: 10/03/2026. Promueve demanda laboral por despido sin causa (Art. 245 Ley 20.744).\n"
        "El Sr. Juan Carlos Perez, DNI 30.123.456, CUIL 20-30123456-3, con domicilio real en Calle San Martin 1240, Quilmes,\n"
        "reclama indemnizacion laboral por la suma de $ 4.500.000,00 depositables en CBU 0110599520000012345678.",
    )

    # Foja 2: Hoja completamente en blanco (debe descartarse por higiene pero conservar numeración de Foja 4)
    doc.new_page()

    # Foja 3: Sello digital aislado de 1 sola línea (debe descartarse por higiene)
    p3 = doc.new_page()
    p3.insert_text((50, 70), "Firmado digitalmente por SECRETARIO JUDICIAL SCBA MEV - Página 3 de 4")

    # Foja 4: Proveído del Juzgado y Acta con cláusula delicada
    p4 = doc.new_page()
    p4.insert_text(
        (50, 70),
        "Quilmes, 25/08/2026. Téngase presente la pericia contable agregada.\n"
        "Notifíquese por cédula electrónica en días de nota martes y viernes.\n"
        "Se acompaña propuesta de acuerdo donde el actor declara que una vez percibido nada más tengo que reclamar\n"
        "y suscribe desistimiento de la acción y del derecho.",
    )

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


class TestPaso1DrHCore(unittest.TestCase):
    """Pruebas deterministas del núcleo de Dr. H Argentina (Paso 1)."""

    def test_subscription_tier_boundaries_and_decimal_precision(self) -> None:
        """Verifica valores frontera en el cálculo de abonos por tamaño de expediente."""
        with self.assertRaises(ValueError):
            ZeroStorageDriveManager.calculate_subscription_tier(0)
        with self.assertRaises(ValueError):
            ZeroStorageDriveManager.calculate_subscription_tier(-5)
        with self.assertRaises(ValueError):
            ZeroStorageDriveManager.calculate_subscription_tier(True)  # type: ignore[arg-type]

        tier_15, single_15, monthly_15 = ZeroStorageDriveManager.calculate_subscription_tier(15)
        self.assertEqual(tier_15, SubscriptionTier.NIVEL_0_GRATUITO)
        self.assertEqual(single_15, Decimal("0.00"))
        self.assertEqual(monthly_15, Decimal("0.00"))

        tier_16, single_16, _ = ZeroStorageDriveManager.calculate_subscription_tier(16)
        self.assertEqual(tier_16, SubscriptionTier.PLAN_S_ESTANDAR)
        self.assertGreater(single_16, Decimal("0.00"))

        tier_150, _, _ = ZeroStorageDriveManager.calculate_subscription_tier(150)
        self.assertEqual(tier_150, SubscriptionTier.PLAN_S_ESTANDAR)

        tier_151, _, _ = ZeroStorageDriveManager.calculate_subscription_tier(151)
        self.assertEqual(tier_151, SubscriptionTier.PLAN_M_COMPLEJO)

        tier_600, _, _ = ZeroStorageDriveManager.calculate_subscription_tier(600)
        self.assertEqual(tier_600, SubscriptionTier.PLAN_L_VOLUMINOSO)

        tier_750, single_750, _ = ZeroStorageDriveManager.calculate_subscription_tier(750)
        self.assertEqual(tier_750, SubscriptionTier.PACK_EXTRA_FOJAS)
        self.assertEqual(single_750, Decimal("85000.00"))

    def test_privacy_anonymizer_roundtrip_and_wipe(self) -> None:
        """Comprueba que DNI, CUIL, CBU, domicilio y nombres se anonimicen y restauren sin pérdida."""
        anon = PrivacyAnonymizer()
        sid = "sess-test-001"
        anon.create_session(sid)

        sample = (
            "EXPTE: GOMEZ MARIA LAURA C/ CLINICA DEL SOL S.A. S/ AMPARO DE SALUD. "
            "La Sra. Maria Laura Gomez, DNI 28.456.123, CUIT 27-28456123-4, "
            "con domicilio real en Av. Rivadavia N° 4520 Piso 3, email maria.gomez@mail.com, "
            "CBU 0070099920000098765432, patente AB123CD."
        )
        masked = anon.anonymize_text(sample, session_id=sid, extra_known_names=["Maria Laura Gomez"])
        self.assertNotIn("28.456.123", masked)
        self.assertNotIn("27-28456123-4", masked)
        self.assertNotIn("0070099920000098765432", masked)
        self.assertNotIn("maria.gomez@mail.com", masked)
        self.assertIn("[DNI_", masked)
        self.assertIn("[CUIT_CUIL_", masked)
        self.assertIn("[CBU_", masked)

        restored = anon.deanonymize_text(masked, session_id=sid)
        self.assertEqual(restored, sample)

        self.assertTrue(anon.destroy_session(sid))
        with self.assertRaises(KeyError):
            anon.deanonymize_text(masked, session_id=sid)

    def test_zero_storage_hygiene_preserves_real_foja_numbers(self) -> None:
        """Verifica que al descartar Foja 2 (blanca) y Foja 3 (sello), la Foja 4 conserve foja_number=4."""
        pdf_bytes = build_synthetic_expediente_pdf()
        manager = ZeroStorageDriveManager()
        session = manager.load_and_prequote_in_ram(pdf_bytes, "causa_laboral.pdf")
        try:
            pq = session.pre_quote
            self.assertEqual(pq.total_pages, 4)
            self.assertEqual(pq.useful_pages_after_hygiene, 2)
            self.assertEqual(pq.discarded_junk_pages, 2)
            self.assertEqual(pq.preserved_foja_index, [1, 4])
        finally:
            session.purge_ram()
            self.assertTrue(session.is_purged)

    def test_full_agente000_orchestration_and_api_endpoints(self) -> None:
        """Ejecuta los 5 endpoints de main.py validando la integración completa de Agente000."""
        client = TestClient(app)
        pdf_bytes = build_synthetic_expediente_pdf()

        # Verificar que el acceso no autenticado sea bloqueado (401)
        resp_unauth = client.post(
            "/pre-quote",
            files={"file": ("expediente_pba.pdf", pdf_bytes, "application/pdf")},
        )
        self.assertEqual(resp_unauth.status_code, 401)

        admin_headers = get_admin_headers(client)

        # 1. Endpoint /pre-quote
        resp_quote = client.post(
            "/pre-quote",
            files={"file": ("expediente_pba.pdf", pdf_bytes, "application/pdf")},
            headers=admin_headers,
        )
        self.assertEqual(resp_quote.status_code, 200)
        quote_data = resp_quote.json()
        self.assertEqual(quote_data["total_pages"], 4)
        self.assertEqual(quote_data["preserved_foja_index"], [1, 4])
        self.assertTrue(quote_data["qualifies_for_free_tier"])

        # 2. Endpoint /analyze-expediente
        resp_analyze = client.post(
            "/analyze-expediente",
            files={"file": ("expediente_pba.pdf", pdf_bytes, "application/pdf")},
            data={
                "user_verbal_statement": "Mi abogado me dijo que ya está listo para sentencia y me pidió $50000 para tasa de justicia.",
                "claimed_lawyer_fee_pct": "30.00",
                "upfront_cost_requested_ars": "50000.00",
            },
            headers=admin_headers,
        )
        self.assertEqual(resp_analyze.status_code, 200)
        report = resp_analyze.json()
        self.assertEqual(report["fuero"], "03_laboral")
        self.assertEqual(report["province_jurisdiction"], "PBA_MEV_SCBA")
        self.assertTrue(report["do_not_sign_yet_alert"])
        # Verificar que las fojas citadas sean exactamente la 1 y la 4 (nunca la 2 ni la 3)
        cited_fojas = [c["foja_ref"] for c in report["verified_timeline_cards"]]
        self.assertEqual(cited_fojas, [1, 4])
        # Verificar que la des-anonimización haya restaurado el DNI real en la Foja 1
        self.assertIn("30.123.456", report["verified_timeline_cards"][0]["verbatim_excerpt"])

        # 3. Endpoint /emergency-scan
        resp_scan = client.post(
            "/emergency-scan",
            json={
                "document_text": "Recibí conforme el total y declaro que nada más tengo que reclamar por despido.",
                "province_jurisdiction": "PJN_NACION_CABA",
                "user_context_note": "Me piden firmar esto ahora en una oficina.",
            },
            headers=admin_headers,
        )
        self.assertEqual(resp_scan.status_code, 200)
        scan_data = resp_scan.json()
        self.assertTrue(scan_data["do_not_sign_yet"])
        self.assertEqual(scan_data["urgency_level"], "PELIGRO_NO_FIRMAR")

        # 4. Endpoint /repository-consult
        resp_repo = client.post(
            "/repository-consult",
            json={
                "query": "Indemnización por despido sin causa y beneficio de gratuidad Ley 20.744",
                "province_jurisdiction": "CORDOBA",
            },
            headers=admin_headers,
        )
        self.assertEqual(resp_repo.status_code, 200)
        repo_data = resp_repo.json()
        self.assertEqual(repo_data["classified_fuero"], "03_laboral")

        # 5. Endpoint /cron-07am -> Puente a Proyecto Gamuza (Geopolítica + Bolsa)
        resp_cron = client.post(
            "/cron-07am",
            json=[
                {
                    "norm_id": "Decreto 842/2026",
                    "publication_date": "2026-11-02",
                    "source_organism": "BORA",
                    "title": "Desregulación y libre exportación de hidrocarburos y gas natural",
                    "full_text_excerpt": "Se establece el régimen de libre exportación de petróleo y gas natural con actualización tarifaria eléctrica.",
                    "effective_from": "2026-11-03",
                    "modifies_or_repeals": ["Decreto 105/2021"],
                }
            ],
            headers=admin_headers,
        )
        self.assertEqual(resp_cron.status_code, 200)
        signals = resp_cron.json()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["origin_node"], "DR_H_AR")
        self.assertTrue(signals[0]["privacy_isolation_verified"])
        self.assertEqual(
            signals[0]["market_and_equities_impacts"][0]["economic_sector"],
            "ENERGIA_OIL_GAS",
        )
        self.assertIn(
            "YPFD",
            signals[0]["market_and_equities_impacts"][0]["related_byma_wallstreet_tickers"],
        )


if __name__ == "__main__":
    unittest.main()
