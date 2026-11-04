"""Integración con Mercado Pago SDK para cobro de consultas y pases en ARS (`core/mercadopago_gateway.py`).

Permite cobrar el desbloqueo de expedientes pesados o consultas subsiguientes sin costo fijo.
Soporta credenciales de prueba (`MERCADOPAGO_ACCESS_TOKEN`) y modo mock/sandbox determinista
para desarrollo local y tests automatizados.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import secrets
from typing import Any

import mercadopago
from pydantic import BaseModel, ConfigDict, Field

from core.database import DrHDatabase

_logger = logging.getLogger("dr_h.mercadopago")

PRICE_DEFAULT_CONSULTA_ARS = Decimal("9500.00")
PRICE_PASE_COMPLETO_ARS = Decimal("24500.00")


class CreatePreferenceRequest(BaseModel):
    """Solicitud de creación de checkout en Mercado Pago."""

    model_config = ConfigDict(extra="forbid")

    plan_type: str = Field(default="CONSULTA_INDIVIDUAL", description="'CONSULTA_INDIVIDUAL' o 'PASE_COMPLETO'")
    case_id: str | None = Field(default=None, description="ID de la causa a desbloquear, si aplica.")


class CreatePreferenceResponse(BaseModel):
    """Respuesta con los links de pago de Mercado Pago."""

    model_config = ConfigDict(extra="forbid")

    preference_id: str
    init_point: str
    sandbox_init_point: str
    amount_ars: str
    amount: str = ""
    currency: str = "ARS"
    title: str
    is_mock: bool = False


class MercadoPagoGateway:
    """Pasarela de pagos en Pesos Argentinos con Mercado Pago."""

    def __init__(self, access_token: str, public_key: str = "", db: DrHDatabase | None = None) -> None:
        self.access_token = access_token.strip()
        self.public_key = public_key.strip()
        self.db = db
        self.sdk: mercadopago.SDK | None = None
        if self.access_token and len(self.access_token) > 10 and not self.access_token.startswith("TEST-MOCK"):
            try:
                self.sdk = mercadopago.SDK(self.access_token)
            except Exception as exc:
                _logger.warning("No se pudo inicializar MercadoPago SDK con el token provisto: %s", exc)

    def create_preference(
        self,
        *,
        user_email: str,
        plan_type: str = "CONSULTA_INDIVIDUAL",
        case_id: str | None = None,
    ) -> CreatePreferenceResponse:
        """Crea una preferencia de pago en Mercado Pago."""
        norm_plan = (plan_type or "").strip().upper()
        if norm_plan in ("PASE_COMPLETO", "UNLIMITED_PASS", "PASE_ILIMITADO"):
            title = "Dr. H Argentina — Pase Completo y Seguimiento Ilimitado"
            price = PRICE_PASE_COMPLETO_ARS
        else:
            title = "Dr. H Argentina — Consulta Jurídica y Auditoría de Expediente"
            price = PRICE_DEFAULT_CONSULTA_ARS

        ext_ref = f"{user_email}:{plan_type}:{case_id or 'none'}"

        if self.sdk is not None:
            preference_data = {
                "items": [
                    {
                        "title": title,
                        "quantity": 1,
                        "unit_price": float(price),
                        "currency_id": "ARS",
                    }
                ],
                "payer": {
                    "email": user_email,
                },
                "back_urls": {
                    "success": "https://dr-h-argentina.onrender.com/?payment=success",
                    "failure": "https://dr-h-argentina.onrender.com/?payment=failure",
                    "pending": "https://dr-h-argentina.onrender.com/?payment=pending",
                },
                "auto_return": "approved",
                "external_reference": ext_ref,
            }
            try:
                pref_result = self.sdk.preference().create(preference_data)
                resp = pref_result.get("response", {})
                return CreatePreferenceResponse(
                    preference_id=str(resp.get("id", "")),
                    init_point=str(resp.get("init_point", "")),
                    sandbox_init_point=str(resp.get("sandbox_init_point", "")),
                    amount_ars=f"{price:.2f}",
                    amount=f"{price:.2f}",
                    currency="ARS",
                    title=title,
                    is_mock=False,
                )
            except Exception as exc:
                _logger.error("Error llamando a la API de Mercado Pago: %s", exc)

        # Fallback de desarrollo / prueba si no hay token real de Mercado Pago configurado
        mock_id = f"mock_pref_{secrets.token_hex(8)}"
        return CreatePreferenceResponse(
            preference_id=mock_id,
            init_point=f"https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id={mock_id}",
            sandbox_init_point=f"https://sandbox.mercadopago.com.ar/checkout/v1/redirect?pref_id={mock_id}",
            amount_ars=f"{price:.2f}",
            amount=f"{price:.2f}",
            currency="ARS",
            title=title,
            is_mock=True,
        )

    def process_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Procesa una notificación webhook de Mercado Pago y activa el pase del usuario."""
        topic = payload.get("type") or payload.get("topic")
        data_id = payload.get("data", {}).get("id") or payload.get("id")

        if not data_id:
            return {"status": "IGNORED", "reason": "No id found in webhook"}

        if self.sdk is not None and topic in ("payment", "payment.created", "payment.updated"):
            try:
                payment_info = self.sdk.payment().get(data_id)
                payment = payment_info.get("response", {})
                status = payment.get("status")
                ext_ref = str(payment.get("external_reference") or "")
                user_email = ext_ref.split(":")[0] if ":" in ext_ref else None

                if status == "approved" and user_email and self.db:
                    self.db.set_user_paid_pass(user_email, True)
                    return {"status": "ACTIVATED", "user_email": user_email}
            except Exception as exc:
                _logger.error("Error consultando pago en Mercado Pago: %s", exc)

        return {"status": "PROCESSED", "topic": topic, "id": data_id}
