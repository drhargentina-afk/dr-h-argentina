"""Tests de cierre de la pasarela Mercado Pago en ARS (`core/mercadopago_gateway.py`).

Se reemplaza el SDK por un doble determinista (`FakeMercadoPagoSDK`) con la misma interfaz que
`mercadopago==3.6.0` (`preference().create`, `payment().get` → `{"status", "response"}`), de modo
que los tests no hacen llamadas de red ni dependen de credenciales reales.

Cubre: carga de MP_ACCESS_TOKEN / MP_PUBLIC_KEY, init_point real, errores de la API (502),
webhook aprobado / pendiente / montos adulterados / moneda incorrecta, idempotencia ante
reintentos, firma x-signature, modo MOCK sin acreditación y bloqueo de /payments/mock-activate.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest import mock
import uuid

from fastapi.testclient import TestClient

from core.config import ServerSecurityConfig
from core.database import DrHDatabase
from core.mercadopago_gateway import (
    MercadoPagoError,
    MercadoPagoGateway,
    WebhookSignatureError,
    build_external_reference,
    normalize_plan,
    parse_external_reference,
)
import main

LIVE_TOKEN = "APP_USR-1234567890123456-110426-abcdefabcdefabcdefabcdefabcdef-123456789"
WEBHOOK_SECRET = "drh-webhook-secret-for-tests"


class _FakePreferenceResource:
    def __init__(self, sdk: "FakeMercadoPagoSDK") -> None:
        self._sdk = sdk

    def create(self, preference_object: dict[str, Any]) -> dict[str, Any]:
        if self._sdk.raise_network_error:
            raise ConnectionError("simulated network failure")
        self._sdk.created_preferences.append(preference_object)
        return self._sdk.preference_reply


class _FakePaymentResource:
    def __init__(self, sdk: "FakeMercadoPagoSDK") -> None:
        self._sdk = sdk

    def get(self, payment_id: str) -> dict[str, Any]:
        if self._sdk.raise_network_error:
            raise ConnectionError("simulated network failure")
        self._sdk.fetched_payment_ids.append(str(payment_id))
        return self._sdk.payments.get(str(payment_id), {"status": 404, "response": {"message": "not found"}})


class FakeMercadoPagoSDK:
    """Doble de prueba con la interfaz pública de `mercadopago.SDK` 3.6.0."""

    def __init__(self) -> None:
        self.created_preferences: list[dict[str, Any]] = []
        self.fetched_payment_ids: list[str] = []
        self.payments: dict[str, dict[str, Any]] = {}
        self.raise_network_error = False
        self.preference_reply: dict[str, Any] = {
            "status": 201,
            "response": {
                "id": "123456789-pref-abc",
                "init_point": "https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id=123456789-pref-abc",
                "sandbox_init_point": "https://sandbox.mercadopago.com.ar/checkout/v1/redirect?pref_id=123456789-pref-abc",
            },
        }

    def preference(self) -> _FakePreferenceResource:
        return _FakePreferenceResource(self)

    def payment(self) -> _FakePaymentResource:
        return _FakePaymentResource(self)

    def add_payment(
        self,
        payment_id: str,
        *,
        status: str,
        email: str,
        plan: str,
        amount: str,
        currency: str = "ARS",
        http_status: int = 200,
    ) -> None:
        self.payments[payment_id] = {
            "status": http_status,
            "response": {
                "id": int(payment_id),
                "status": status,
                "status_detail": "accredited" if status == "approved" else "pending_contingency",
                "currency_id": currency,
                "transaction_amount": float(Decimal(amount)),
                "external_reference": build_external_reference(email, normalize_plan(plan), None),
            },
        }


def _sign(data_id: str, request_id: str, ts: str, secret: str = WEBHOOK_SECRET) -> str:
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    digest = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={digest}"


class TestMercadoPagoConfigInjection(unittest.TestCase):
    """Las variables de Render (MP_*) deben llegar a ServerSecurityConfig."""

    def _load_with_env(self, env: dict[str, str]) -> ServerSecurityConfig:
        base_env = {
            "GOOGLE_API_KEY": "test-google-key-0123456789abcdef",
            "ADMIN_PASSWORD": "admin-password-for-tests-123",
        }
        base_env.update(env)
        missing_dotenv = Path(tempfile.gettempdir()) / f"drh_missing_{uuid.uuid4().hex}.env"
        with mock.patch.dict(os.environ, base_env, clear=True):
            return ServerSecurityConfig.load(missing_dotenv)

    def test_mp_env_vars_are_injected(self) -> None:
        cfg = self._load_with_env(
            {
                "MP_ACCESS_TOKEN": LIVE_TOKEN,
                "MP_PUBLIC_KEY": "APP_USR-public-key-xyz",
                "MP_WEBHOOK_SECRET": WEBHOOK_SECRET,
                "PUBLIC_BASE_URL": "https://dr-h.example.com/",
            }
        )
        self.assertEqual(cfg.mercadopago_access_token.get_secret_value(), LIVE_TOKEN)
        self.assertEqual(cfg.mercadopago_public_key, "APP_USR-public-key-xyz")
        self.assertEqual(cfg.mercadopago_webhook_secret.get_secret_value(), WEBHOOK_SECRET)
        self.assertEqual(cfg.public_base_url, "https://dr-h.example.com")
        self.assertNotIn(LIVE_TOKEN, repr(cfg))

    def test_legacy_mercadopago_names_still_work_as_alias(self) -> None:
        cfg = self._load_with_env({"MERCADOPAGO_ACCESS_TOKEN": LIVE_TOKEN, "MERCADOPAGO_PUBLIC_KEY": "pk-legacy"})
        self.assertEqual(cfg.mercadopago_access_token.get_secret_value(), LIVE_TOKEN)
        self.assertEqual(cfg.mercadopago_public_key, "pk-legacy")

    def test_missing_token_means_mock_mode(self) -> None:
        cfg = self._load_with_env({})
        gateway = MercadoPagoGateway(access_token=cfg.mercadopago_access_token.get_secret_value())
        self.assertEqual(gateway.mode, "MOCK")
        self.assertFalse(gateway.is_live)

    def test_real_sdk_is_built_from_token(self) -> None:
        gateway = MercadoPagoGateway(access_token=LIVE_TOKEN, public_key="pk")
        self.assertTrue(gateway.is_live)
        self.assertEqual(gateway.mode, "PRODUCCION")
        test_gateway = MercadoPagoGateway(access_token="TEST-1234567890123456-110426-abcdef-12345")
        self.assertEqual(test_gateway.mode, "PRUEBA")


class TestMercadoPagoGatewayUnit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = DrHDatabase(Path(self._tmpdir.name) / "payments_test.db")
        self.sdk = FakeMercadoPagoSDK()
        self.gateway = MercadoPagoGateway(
            access_token=LIVE_TOKEN,
            public_key="APP_USR-public-key-xyz",
            db=self.db,
            public_base_url="https://dr-h-argentina.onrender.com",
            sdk=self.sdk,
        )
        self.email = "cliente.pago@gmail.com"
        self.db.upsert_google_user(google_id="gid-pago-1", email=self.email, name="Cliente Pago")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _webhook(self, payment_id: str) -> Any:
        return self.gateway.process_webhook(
            query_params={"data.id": payment_id, "type": "payment"},
            body={"action": "payment.updated", "type": "payment", "id": 99999, "data": {"id": payment_id}},
        )

    # --- preferencias ---------------------------------------------------
    def test_plan_aliases_and_external_reference_roundtrip(self) -> None:
        self.assertEqual(normalize_plan("UNLIMITED_PASS"), "PASE_COMPLETO")
        self.assertEqual(normalize_plan("single_consultation"), "CONSULTA_INDIVIDUAL")
        with self.assertRaises(ValueError):
            normalize_plan("PLAN_INEXISTENTE")
        ref = build_external_reference("A@Gmail.com", "PASE_COMPLETO", "case-1")
        self.assertEqual(parse_external_reference(ref), ("a@gmail.com", "PASE_COMPLETO"))
        self.assertIsNone(parse_external_reference("otro-sistema:123"))
        self.assertIsNone(parse_external_reference(""))

    def test_create_preference_returns_real_init_point_and_correct_payload(self) -> None:
        pref = self.gateway.create_preference(user_email=self.email, plan_type="PASE_COMPLETO")
        self.assertFalse(pref.is_mock)
        self.assertEqual(pref.init_point, self.sdk.preference_reply["response"]["init_point"])
        self.assertEqual(pref.preference_id, "123456789-pref-abc")
        self.assertEqual(pref.amount, "24500.00")
        self.assertEqual(pref.currency, "ARS")
        self.assertEqual(pref.public_key, "APP_USR-public-key-xyz")

        sent = self.sdk.created_preferences[0]
        item = sent["items"][0]
        self.assertEqual(item["currency_id"], "ARS")
        self.assertEqual(item["unit_price"], 24500.0)
        self.assertEqual(item["quantity"], 1)
        self.assertEqual(sent["auto_return"], "approved")
        self.assertEqual(sent["back_urls"]["success"], "https://dr-h-argentina.onrender.com/?payment=success")
        self.assertEqual(parse_external_reference(sent["external_reference"]), (self.email, "PASE_COMPLETO"))
        self.assertNotIn("notification_url", sent)

    def test_create_preference_single_consultation_price(self) -> None:
        pref = self.gateway.create_preference(user_email=self.email, plan_type="SINGLE_CONSULTATION")
        self.assertEqual(pref.amount, "9500.00")
        self.assertEqual(self.sdk.created_preferences[0]["items"][0]["unit_price"], 9500.0)

    def test_create_preference_api_rejection_raises_instead_of_fake_link(self) -> None:
        self.sdk.preference_reply = {"status": 401, "response": {"message": "invalid access token"}}
        with self.assertRaises(MercadoPagoError):
            self.gateway.create_preference(user_email=self.email, plan_type="PASE_COMPLETO")

    def test_create_preference_network_error_raises(self) -> None:
        self.sdk.raise_network_error = True
        with self.assertRaises(MercadoPagoError):
            self.gateway.create_preference(user_email=self.email, plan_type="PASE_COMPLETO")

    def test_create_preference_rejects_invalid_email(self) -> None:
        with self.assertRaises(ValueError):
            self.gateway.create_preference(user_email="sin-arroba", plan_type="PASE_COMPLETO")

    # --- webhook --------------------------------------------------------
    def test_approved_full_pass_activates_and_is_idempotent(self) -> None:
        self.sdk.add_payment("1001", status="approved", email=self.email, plan="PASE_COMPLETO", amount="24500.00")

        first = self._webhook("1001")
        self.assertEqual(first.status, "ACTIVATED")
        self.assertEqual(first.plan_type, "PASE_COMPLETO")
        self.assertEqual(self.sdk.fetched_payment_ids, ["1001"])  # se usó data.id, no el id de la notificación
        self.assertEqual(int(self.db.get_user_by_email(self.email)["has_paid_pass"]), 1)

        second = self._webhook("1001")
        self.assertEqual(second.status, "ALREADY_PROCESSED")
        payment_row = self.db.get_payment("1001")
        self.assertEqual(payment_row["amount_ars"], "24500.00")
        self.assertEqual(int(payment_row["benefit_applied"]), 1)

    def test_approved_single_consultation_grants_exactly_one_credit(self) -> None:
        self.db.increment_free_consultations(self.email)  # ya usó la gratuita
        self.sdk.add_payment("2001", status="approved", email=self.email, plan="CONSULTA_INDIVIDUAL", amount="9500.00")

        self.assertEqual(self._webhook("2001").status, "ACTIVATED")
        user = self.db.get_user_by_email(self.email)
        self.assertEqual(int(user["free_consultations_used"]), 0)
        self.assertEqual(int(user["has_paid_pass"]), 0)

        # Reintento de MP: no debe acreditar un segundo crédito
        self.db.increment_free_consultations(self.email)
        self.assertEqual(self._webhook("2001").status, "ALREADY_PROCESSED")
        self.assertEqual(int(self.db.get_user_by_email(self.email)["free_consultations_used"]), 1)

    def test_pending_payment_does_not_activate(self) -> None:
        self.sdk.add_payment("3001", status="in_process", email=self.email, plan="PASE_COMPLETO", amount="24500.00")
        result = self._webhook("3001")
        self.assertEqual(result.status, "NOT_APPROVED")
        self.assertEqual(result.mp_status, "in_process")
        self.assertEqual(int(self.db.get_user_by_email(self.email)["has_paid_pass"]), 0)

    def test_underpaid_or_wrong_currency_is_rejected(self) -> None:
        self.sdk.add_payment("4001", status="approved", email=self.email, plan="PASE_COMPLETO", amount="9500.00")
        self.sdk.add_payment("4002", status="approved", email=self.email, plan="PASE_COMPLETO", amount="24500.00", currency="USD")
        self.assertEqual(self._webhook("4001").status, "REJECTED")
        self.assertEqual(self._webhook("4002").status, "REJECTED")
        self.assertEqual(int(self.db.get_user_by_email(self.email)["has_paid_pass"]), 0)
        self.assertIsNone(self.db.get_payment("4001"))

    def test_foreign_external_reference_is_rejected(self) -> None:
        self.sdk.payments["5001"] = {
            "status": 200,
            "response": {"status": "approved", "currency_id": "ARS", "transaction_amount": 24500.0,
                         "external_reference": f"{self.email}:UNLIMITED_PASS:none"},
        }
        self.assertEqual(self._webhook("5001").status, "REJECTED")

    def test_unknown_user_is_recorded_without_benefit(self) -> None:
        self.sdk.add_payment("6001", status="approved", email="nadie@gmail.com", plan="PASE_COMPLETO", amount="24500.00")
        self.assertEqual(self._webhook("6001").status, "USER_NOT_FOUND")
        self.assertEqual(int(self.db.get_payment("6001")["benefit_applied"]), 0)

    def test_not_found_payment_is_ignored_and_api_errors_raise(self) -> None:
        self.assertEqual(self._webhook("7777").status, "IGNORED")
        self.sdk.payments["8001"] = {"status": 500, "response": {"message": "internal"}}
        with self.assertRaises(MercadoPagoError):
            self._webhook("8001")
        self.sdk.raise_network_error = True
        with self.assertRaises(MercadoPagoError):
            self._webhook("8002")

    def test_non_payment_topics_and_missing_id_are_ignored(self) -> None:
        merchant = self.gateway.process_webhook(query_params={"topic": "merchant_order", "id": "55"}, body={})
        self.assertEqual(merchant.status, "IGNORED")
        empty = self.gateway.process_webhook(query_params={}, body={})
        self.assertEqual(empty.status, "IGNORED")
        self.assertEqual(self.sdk.fetched_payment_ids, [])

    def test_legacy_ipn_query_format_is_supported(self) -> None:
        self.sdk.add_payment("9001", status="approved", email=self.email, plan="PASE_COMPLETO", amount="24500.00")
        result = self.gateway.process_webhook(query_params={"topic": "payment", "id": "9001"}, body={})
        self.assertEqual(result.status, "ACTIVATED")

    def test_mock_mode_never_grants_benefits(self) -> None:
        mock_gateway = MercadoPagoGateway(access_token="", db=self.db)
        result = mock_gateway.process_webhook(query_params={"data.id": "1", "type": "payment"}, body={})
        self.assertEqual(result.status, "IGNORED")
        self.assertEqual(int(self.db.get_user_by_email(self.email)["has_paid_pass"]), 0)
        pref = mock_gateway.create_preference(user_email=self.email, plan_type="PASE_COMPLETO")
        self.assertTrue(pref.is_mock)

    # --- firma x-signature ---------------------------------------------
    def test_webhook_signature_validation(self) -> None:
        signed_gateway = MercadoPagoGateway(
            access_token=LIVE_TOKEN, db=self.db, webhook_secret=WEBHOOK_SECRET, sdk=self.sdk
        )
        self.sdk.add_payment("10001", status="approved", email=self.email, plan="PASE_COMPLETO", amount="24500.00")
        query = {"data.id": "10001", "type": "payment"}

        with self.assertRaises(WebhookSignatureError):
            signed_gateway.process_webhook(query_params=query, body={}, x_signature=None, x_request_id="req-1")
        with self.assertRaises(WebhookSignatureError):
            signed_gateway.process_webhook(
                query_params=query, body={}, x_signature=_sign("10001", "req-1", "1730000000", "otra-clave"),
                x_request_id="req-1",
            )
        with self.assertRaises(WebhookSignatureError):
            signed_gateway.process_webhook(query_params=query, body={}, x_signature="v1=abc", x_request_id="req-1")
        self.assertEqual(self.sdk.fetched_payment_ids, [])

        ok = signed_gateway.process_webhook(
            query_params=query, body={}, x_signature=_sign("10001", "req-1", "1730000000"), x_request_id="req-1"
        )
        self.assertEqual(ok.status, "ACTIVATED")


class TestMercadoPagoEndpoints(unittest.TestCase):
    """Endpoints FastAPI con la pasarela en modo vivo (SDK doble) sobre la base real de la app."""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.sdk = FakeMercadoPagoSDK()
        self.live_gateway = MercadoPagoGateway(
            access_token=LIVE_TOKEN,
            public_key="APP_USR-public-key-xyz",
            db=main.db,
            webhook_secret=WEBHOOK_SECRET,
            sdk=self.sdk,
        )
        self.email = f"endpoint_{uuid.uuid4().hex[:10]}@gmail.com"
        login = self.client.post("/auth/google", json={"credential": f"mock_google_oauth:{self.email}:Cliente Endpoint"})
        self.assertEqual(login.status_code, 200)
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def test_full_checkout_flow_create_preference_webhook_and_pass(self) -> None:
        payment_id = str(int(uuid.uuid4().int % 10**12))
        with mock.patch.object(main, "mp_gateway", self.live_gateway):
            status_resp = self.client.get("/payments/status", headers=self.headers)
            self.assertEqual(status_resp.status_code, 200)
            self.assertEqual(status_resp.json()["mode"], "PRODUCCION")
            self.assertTrue(status_resp.json()["webhook_signature_enforced"])
            self.assertNotIn(LIVE_TOKEN, status_resp.text)

            pref_resp = self.client.post(
                "/payments/create-preference", json={"plan_type": "PASE_COMPLETO"}, headers=self.headers
            )
            self.assertEqual(pref_resp.status_code, 200)
            pref = pref_resp.json()
            self.assertFalse(pref["is_mock"])
            self.assertTrue(pref["init_point"].startswith("https://www.mercadopago.com.ar/checkout/"))

            self.sdk.add_payment(payment_id, status="approved", email=self.email, plan="PASE_COMPLETO", amount="24500.00")

            unsigned = self.client.post(
                f"/payments/webhook?data.id={payment_id}&type=payment",
                json={"type": "payment", "data": {"id": payment_id}},
            )
            self.assertEqual(unsigned.status_code, 401)
            self.assertFalse(self.client.get("/auth/me", headers=self.headers).json()["has_paid_pass"])

            signed = self.client.post(
                f"/payments/webhook?data.id={payment_id}&type=payment",
                json={"action": "payment.updated", "type": "payment", "data": {"id": payment_id}},
                headers={"x-signature": _sign(payment_id, "req-abc", "1730000000"), "x-request-id": "req-abc"},
            )
            self.assertEqual(signed.status_code, 200)
            self.assertEqual(signed.json()["status"], "ACTIVATED")
            self.assertTrue(self.client.get("/auth/me", headers=self.headers).json()["has_paid_pass"])

            mock_activate = self.client.post("/payments/mock-activate", headers=self.headers)
            self.assertEqual(mock_activate.status_code, 404)

    def test_api_failure_maps_to_502_and_bad_json_to_400(self) -> None:
        self.sdk.preference_reply = {"status": 500, "response": {"message": "boom"}}
        with mock.patch.object(main, "mp_gateway", self.live_gateway):
            pref_resp = self.client.post(
                "/payments/create-preference", json={"plan_type": "PASE_COMPLETO"}, headers=self.headers
            )
            self.assertEqual(pref_resp.status_code, 502)

            bad_plan = self.client.post(
                "/payments/create-preference", json={"plan_type": "NO_EXISTE"}, headers=self.headers
            )
            self.assertEqual(bad_plan.status_code, 400)

            bad_json = self.client.post(
                "/payments/webhook", content=b"{no-json", headers={"Content-Type": "application/json"}
            )
            self.assertEqual(bad_json.status_code, 400)

    def test_create_preference_requires_session(self) -> None:
        resp = self.client.post("/payments/create-preference", json={"plan_type": "PASE_COMPLETO"})
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
