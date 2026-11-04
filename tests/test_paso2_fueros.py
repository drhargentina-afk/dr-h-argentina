"""Suite de Pruebas Unitarias e Integración del Paso 2 (`agents_fueros/` + Calculadoras + `Agente000`).

Verifica:
1. Los 7 Sub-Agentes por Área Judicial (`01_penal` a `07_contencioso_tributario`) despachados vía `FuerosRouter`.
2. Calculadora Laboral Art. 245 LCT + Preaviso + Integración + detección de "Acuerdo a la Baja" (<55%) + Baremo ART + Telegrama Ley 23.789.
3. Calculadora Familia Oficial Canasta de Crianza INDEC por tramos de edad con `Decimal`.
4. Calculadora Civil de actualización de alquileres (ICL/IPC) y Baremo de Incapacidad Civil Art. 1746 CCyCN.
5. Detector Comercial de Deudas Prescriptas (Art. 2560 CCyCN y Ley 25.065) y Calculadora de Tope Legal de Embargo de Sueldo (Decreto 484/87).
6. Protocolo Penal "Primeras 48hs de Detenido", Nota de Intimación 48hs Amparo de Salud (Ley 24.901/PMO) y Prescripción de Multas/Impuestos (Ley 24.449 / Ley 11.683).
7. Validador de Vigencia Normativa (`validate_citations`) detectando leyes derogadas (Ley 27.551, Ley 25.323) y leyes inexistentes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import unittest

from fastapi.testclient import TestClient

from agents_fueros import (
    FuerosRouter,
    RepoCivil,
    RepoComercial,
    RepoContencioso,
    RepoFamilia,
    RepoLaboral,
    RepoPenal,
    RepoSeguridadSocial,
)
from core.models import CaseContext, FojaPageContent, FueroKey, ProvinceKey, UserRole
from main import app
from tests.admin_auth_helper import get_admin_headers, orchestrator


class TestPaso2FuerosAndCalculators(unittest.TestCase):
    """Pruebas deterministas de los 7 agentes de fuero y sus calculadoras oficiales."""

    def setUp(self) -> None:
        self.router = FuerosRouter()

    def test_all_7_fuero_agents_dispatch_and_analyze(self) -> None:
        """Verifica que los 7 Sub-Agentes procesen fojas y emitan FueroAnalysisResult válido."""
        sample_texts: dict[FueroKey, str] = {
            FueroKey.PENAL: "Acta de procedimiento policial de fecha 12/05/2026. Declaración indagatoria Art. 294 CPPN. Excarcelación bajo caución de $ 500.000,00.",
            FueroKey.FAMILIA: "Demanda de alimentos provisorios Art. 544 CCyCN para menor de 8 años de edad. Fecha 15/06/2026. Cuota $ 350.000,00.",
            FueroKey.LABORAL: "Telegrama Ley 23.789 de fecha 20/07/2026. Despido sin causa Art. 245 LCT con 6 años de antigüedad. Mejor sueldo $ 1.200.000,00.",
            FueroKey.CIVIL: "Declaratoria de herederos y daños y perjuicios por accidente de tránsito con citación en garantía de fecha 10/08/2026 por $ 8.500.000,00.",
            FueroKey.COMERCIAL_CONSUMIDOR: "Mandamiento de intimación de pago en juicio ejecutivo por pagaré y embargo sobre haberes Decreto 484/87 de fecha 01/09/2026. Sueldo $ 900.000,00.",
            FueroKey.SEGURIDAD_SOCIAL_SALUD: "Acción de amparo de salud Ley 24.901 y reajuste de haberes ANSES fallo Badaro y Blanco de fecha 05/09/2026 por $ 2.400.000,00.",
            FueroKey.CONTENCIOSO_TRIBUTARIO: "Boleta de deuda ejecución fiscal ARCA Art. 92 Ley 11.683 y acta de infracción fotomulta Ley 24.449 de fecha 12/09/2026 por $ 1.100.000,00.",
        }

        prov_rules = orchestrator.provinces_config["jurisdictions"]["PJN_NACION_CABA"]  # type: ignore[index]

        for fuero, text in sample_texts.items():
            ctx = CaseContext(
                session_id=f"sess-{fuero.value}",
                country_code="AR",
                province_jurisdiction=ProvinceKey.PJN_NACION_CABA,
                user_role=UserRole.ACTOR_RECLAMANTE,
                detected_fuero=fuero,
                caratula_detected=f"EXPTE TEST {fuero.value.upper()}",
                fojas_content=[
                    FojaPageContent(
                        foja_number=5,
                        raw_char_count=len(text),
                        image_count=0,
                        requires_ocr=False,
                        is_discarded_hygiene=False,
                        cleaned_text=text,
                    )
                ],
            )
            res = self.router.dispatch_case_to_fuero(ctx, prov_rules)
            self.assertEqual(res.fuero_agent_id, fuero)
            self.assertEqual(len(res.foja_citations), 1)
            self.assertEqual(res.foja_citations[0].foja_ref, 5)
            self.assertIsNotNone(res.preliminary_calculation)
            self.assertGreater(res.preliminary_calculation.estimated_total_ars, Decimal("0.00"))  # type: ignore[union-attr]

    def test_laboral_calculator_art245_and_lowball_agreement_detection(self) -> None:
        """Verifica la calculadora Art. 245 LCT, preaviso, integración, Baremo ART y acuerdo a la baja."""
        res = RepoLaboral.calculate_indemnizacion_art245(
            best_monthly_gross_salary_ars=Decimal("1000000.00"),
            years_of_service=4,
            months_fraction=5,  # > 3 meses -> computa 5 períodos Art. 245
            remaining_days_in_dismissal_month=15,
            offered_settlement_ars=Decimal("2000000.00"),  # Oferta abusiva (< 55%)
        )
        self.assertEqual(res["computable_periods_art245"], 5)
        self.assertEqual(res["indemnizacion_art245_ars"], Decimal("5000000.00"))
        self.assertEqual(res["indemnizacion_preaviso_art232_ars"], Decimal("1000000.00"))
        self.assertEqual(res["integracion_mes_despido_art233_ars"], Decimal("500000.00"))
        self.assertTrue(res["is_abusive_lowball_offer"])
        self.assertIn("ALERTA DE ACUERDO A LA BAJA", str(res["offer_evaluation"]))

        # Baremo ART
        art_res = RepoLaboral.calculate_baremo_art_incapacidad(
            ibm_salary_ars=Decimal("1000000.00"),
            disability_pct=Decimal("10.00"),
            worker_age=32,
            in_workplace_accident=True,
        )
        self.assertGreater(art_res["total_indemnizacion_art_ars"], Decimal("10000000.00"))

        # Modelo de Telegrama Obrero Gratuito Ley 23.789
        tcl = RepoLaboral.get_telegrama_obrero_template("NEGATIVA_TAREAS")
        self.assertIn("Ley 20.744", tcl["telegram_text"])

    def test_familia_canasta_crianza_indec_calculator(self) -> None:
        """Verifica el cálculo de la Canasta de Crianza INDEC para múltiples hijos."""
        res = RepoFamilia.calculate_canasta_crianza_indec(
            children_ages=[2, 8],
            non_conviviente_share_pct=Decimal("50.00"),
        )
        self.assertEqual(res["children_count"], 2)
        self.assertEqual(res["total_canasta_crianza_indec_ars"], Decimal("1085300.00"))
        self.assertEqual(res["suggested_monthly_quota_ars"], Decimal("542650.00"))

        with self.assertRaises(ValueError):
            RepoFamilia.calculate_canasta_crianza_indec([])

    def test_comercial_debt_prescription_and_decreto_484_87_embargo_limits(self) -> None:
        """Verifica prescripción de tarjetas/préstamos y topes de embargo sobre haberes (Decreto 484/87)."""
        # Deuda de tarjeta con más de 3 años -> Prescripta
        presc = RepoComercial.check_debt_prescription(
            default_mora_date=date(2021, 5, 10),
            debt_type="TARJETA_CREDITO_ORDINARIA",
            reference_today=date(2026, 11, 2),
        )
        self.assertTrue(presc["is_prescribed"])
        self.assertTrue(presc["qualifies_for_veraz_deletion_art26_ley25326"])

        # Embargo Decreto 484/87:
        # 1) Sueldo igual al SMVM ($310.000) -> $0 embargable
        emb_zero = RepoComercial.calculate_embargo_sueldo_limit(
            net_monthly_salary_ars=Decimal("310000.00"),
            smvm_current_ars=Decimal("310000.00"),
        )
        self.assertEqual(emb_zero["max_legal_monthly_embargo_ars"], Decimal("0.00"))

        # 2) Sueldo entre 1 y 2 SMVM ($510.000 -> excedente $200.000 -> 10% = $20.000)
        emb_10 = RepoComercial.calculate_embargo_sueldo_limit(
            net_monthly_salary_ars=Decimal("510000.00"),
            smvm_current_ars=Decimal("310000.00"),
        )
        self.assertEqual(emb_10["max_legal_monthly_embargo_ars"], Decimal("20000.00"))

        # 3) Sueldo mayor a 2 SMVM ($1.310.000 -> excedente $1.000.000 -> 20% = $200.000)
        emb_20 = RepoComercial.calculate_embargo_sueldo_limit(
            net_monthly_salary_ars=Decimal("1310000.00"),
            smvm_current_ars=Decimal("310000.00"),
        )
        self.assertEqual(emb_20["max_legal_monthly_embargo_ars"], Decimal("200000.00"))

    def test_civil_alquileres_penal_48hs_segsocial_and_contencioso(self) -> None:
        """Verifica calculadoras Civil, protocolo Penal 48hs, intimación Salud y prescripción de multas."""
        alq = RepoCivil.calculate_alquiler_update(
            base_rent_ars=Decimal("400000.00"),
            index_start_value=Decimal("100.00"),
            index_end_value=Decimal("150.00"),
        )
        self.assertEqual(alq["updated_rent_ars"], Decimal("600000.00"))
        self.assertEqual(alq["variation_pct"], Decimal("50.00"))

        civ_bar = RepoCivil.calculate_baremo_civil_accidente(
            monthly_income_ars=Decimal("800000.00"),
            victim_age=35,
            disability_pct=Decimal("15.00"),
        )
        self.assertGreater(civ_bar["total_estimado_reclamo_civil_ars"], Decimal("15000000.00"))

        proto_penal = RepoPenal().get_48hs_detention_protocol()
        self.assertEqual(len(proto_penal["golden_rules"]), 5)  # type: ignore[arg-type]

        nota_salud = RepoSeguridadSocial.build_health_intimacion_48hs_note(
            health_provider_name="OSDE",
            medical_prescription_detail="Medicación biológica urgente",
            has_cud_disability=True,
        )
        self.assertIn("Ley 24.901", nota_salud["intimacion_text"])

        multa = RepoContencioso.check_tax_or_fine_prescription(
            infraction_or_tax_due_date=date(2023, 1, 15),
            obligation_type="MULTA_TRANSITO_LEVE",
            reference_today=date(2026, 11, 2),
        )
        self.assertTrue(multa["is_prescribed"])

    def test_citation_validator_detects_repealed_and_hallucinated_laws(self) -> None:
        """Verifica que validate_citations detecte leyes derogadas (Ley 27.551, 25.323) y números falsos."""
        repo = RepoCivil()
        report = repo.validate_citations(
            "Solicito aplicación de la Ley 27.551 de Alquileres, la Ley 25.323 y la Ley 99.999 sobre contratos."
        )
        self.assertFalse(report.is_fully_current)
        self.assertGreaterEqual(len(report.repealed_or_modified_warnings), 2)
        self.assertEqual(len(report.hallucinated_or_unknown_warnings), 1)

        # Endpoint /repository-consult integrado con FuerosRouter
        client = TestClient(app)
        # 401 si no está autenticado como admin
        resp_unauth = client.post(
            "/repository-consult",
            json={
                "query": "Me llaman de un estudio de cobranza por una deuda vieja de tarjeta de crédito y amenazan con embargar.",
                "province_jurisdiction": "PJN_NACION_CABA",
            },
        )
        self.assertEqual(resp_unauth.status_code, 401)

        # 200 con cabecera de admin
        resp = client.post(
            "/repository-consult",
            json={
                "query": "Me llaman de un estudio de cobranza por una deuda vieja de tarjeta de crédito y amenazan con embargar.",
                "province_jurisdiction": "PJN_NACION_CABA",
            },
            headers=get_admin_headers(client),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["classified_fuero"], "05_comercial_consumidor")
        self.assertIn("estudios de cobranza", data["citizen_clear_answer"].lower())


if __name__ == "__main__":
    unittest.main()
