"""Pasarela de cobro Mercado Pago en Pesos Argentinos (`core/mercadopago_gateway.py`).

SDK oficial `mercadopago==3.6.0` (Checkout Pro):
- Credenciales inyectadas por entorno (Render): `MP_ACCESS_TOKEN`, `MP_PUBLIC_KEY` y,
  opcionalmente, `MP_WEBHOOK_SECRET` para validar la firma `x-signature` de los Webhooks.
- `create_preference`: crea la preferencia y devuelve el `init_point` real del checkout.
  Con credenciales configuradas, un fallo de la API se informa como error (nunca se inventa un link).
- `process_webhook`: jamás confía en el cuerpo de la notificación. Re-consulta el pago en la API
  de Mercado Pago con el Access Token privado y solo acredita si `status == "approved"`, la moneda
  es ARS y el monto cubre el precio del plan. La acreditación es idempotente por `payment_id`.
- Modo MOCK (sin token): solo para desarrollo local y tests; nunca acredita beneficios.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import logging
import secrets
from typing import Any, Literal

import mercadopago
from pydantic import BaseModel, ConfigDict, Field

from core.database import DrHDatabase

_logger = logging.getLogger("dr_h.mercadopago")

PlanCode = Literal["PASE_COMPLETO", "CONSULTA_INDIVIDUAL"]
GatewayMode = Literal["PRODUCCION", "PRUEBA", "MOCK"]

PRICE_DEFAULT_CONSULTA_ARS = Decimal("9500.00")
PRICE_PASE_COMPLETO_ARS = Decimal("24500.00")

PLAN_CATALOG: dict[PlanCode, tuple[str, Decimal]] = {
    "PASE_COMPLETO": ("Dr. H Argentina — Pase Completo y Seguimiento Ilimitado", PRICE_PASE_COMPLETO_ARS),
    "CONSULTA_INDIVIDUAL": ("Dr. H Argentina — Consulta Jurídica y Auditoría de Expediente", PRICE_DEFAULT_CONSULTA_ARS),
}

_PLAN_ALIASES: dict[str, PlanCode] = {
    "PASE_COMPLETO": "PASE_COMPLETO",
    "UNLIMITED_PASS": "PASE_COMPLETO",
    "PASE_ILIMITADO": "PASE_COMPLETO",
    "CONSULTA_INDIVIDUAL": "CONSULTA_INDIVIDUAL",
    "SINGLE_CONSULTATION": "CONSULTA_INDIVIDUAL",
}

_PAYMENT_TOPICS = frozenset({"payment", "payment.created", "payment.updated"})
_EXT_REF_PREFIX = "drh"
_EXT_REF_SEP = "|"


class MercadoPagoError(Exception):
    """Fallo de comunicación o respuesta inválida de la API de Mercado Pago (reintentable)."""


class WebhookSignatureError(Exception):
    """La firma `x-signature` del Webhook no coincide con `MP_WEBHOOK_SECRET`."""


def normalize_plan(raw_plan: str | None) -> PlanCode:
    """Traduce los alias de plan aceptados por la API al código interno canónico."""
    key = (raw_plan or "CONSULTA_INDIVIDUAL").strip().upper()
    if key not in _PLAN_ALIASES:
        raise ValueError(
            f"Plan de pago desconocido: '{raw_plan}'. Valores válidos: {', '.join(sorted(_PLAN_ALIASES))}."
        )
    return _PLAN_ALIASES[key]


def build_external_reference(user_email: str, plan: PlanCode, case_id: str | None) -> str:
    """Formato: `drh|<email>|<plan>|<case_id|none>` (el email nunca contiene '|')."""
    return _EXT_REF_SEP.join((_EXT_REF_PREFIX, user_email.strip().lower(), plan, (case_id or "none").strip()))


def parse_external_reference(ext_ref: str) -> tuple[str, PlanCode] | None:
    """Devuelve (email, plan) si la referencia fue emitida por Dr. H; None en caso contrario."""
    parts = (ext_ref or "").split(_EXT_REF_SEP)
    if len(parts) != 4 or parts[0] != _EXT_REF_PREFIX or "@" not in parts[1]:
        return None
    try:
        return parts[1], normalize_plan(parts[2])
    except ValueError:
        return None


class CreatePreferenceRequest(BaseModel):
    """Solicitud de creación de checkout en Mercado Pago."""

    model_config = ConfigDict(extra="forbid")

    plan_type: str = Field(
        default="CONSULTA_INDIVIDUAL",
        description="'CONSULTA_INDIVIDUAL' / 'SINGLE_CONSULTATION' o 'PASE_COMPLETO' / 'UNLIMITED_PASS'.",
    )
    case_id: str | None = Field(default=None, max_length=64, description="ID de la causa a desbloquear, si aplica.")


class CreatePreferenceResponse(BaseModel):
    """Respuesta con los links de pago de Mercado Pago."""

    model_config = ConfigDict(extra="forbid")

    preference_id: str
    init_point: str
    sandbox_init_point: str
    amount_ars: str
    amount: str = ""
    currency: str = "ARS"
    plan_type: str = ""
    title: str
    public_key: str = ""
    is_mock: bool = False


class WebhookResult(BaseModel):
    """Resultado del procesamiento de una notificación de Mercado Pago."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ACTIVATED", "ALREADY_PROCESSED", "USER_NOT_FOUND", "NOT_APPROVED", "REJECTED", "IGNORED"]
    payment_id: str | None = None
    user_email: str | None = None
    plan_type: str | None = None
    mp_status: str | None = None
    reason: str = ""


class MercadoPagoStatus(BaseModel):
    """Diagnóstico de la configuración de la pasarela (sin exponer secretos)."""

    model_config = ConfigDict(extra="forbid")

    mode: GatewayMode
    live: bool
    public_key: str
    webhook_signature_enforced: bool
    back_url_base: str


class MercadoPagoGateway:
    """Pasarela de pagos en Pesos Argentinos con Mercado Pago (Checkout Pro)."""

    def __init__(
        self,
        access_token: str,
        public_key: str = "",
        db: DrHDatabase | None = None,
        *,
        webhook_secret: str = "",
        public_base_url: str = "https://dr-h-argentina.onrender.com",
        sdk: Any | None = None,
    ) -> None:
        self.access_token = (access_token or "").strip()
        self.public_key = (public_key or "").strip()
        self.db = db
        self.webhook_secret = (webhook_secret or "").strip()
        self.public_base_url = (public_base_url or "").strip().rstrip("/")
        self.sdk: Any | None = sdk

        if self.sdk is None and self.access_token:
            if len(self.access_token) < 20 or self.access_token.startswith("TEST-MOCK"):
                _logger.warning("MP_ACCESS_TOKEN con formato inválido: la pasarela queda en modo MOCK.")
            else:
                self.sdk = mercadopago.SDK(self.access_token)

        _logger.info(
            "Pasarela Mercado Pago inicializada en modo %s (firma webhook %s).",
            self.mode,
            "obligatoria" if self.webhook_secret else "no configurada",
        )

    # ------------------------------------------------------------------ estado
    @property
    def is_live(self) -> bool:
        return self.sdk is not None

    @property
    def mode(self) -> GatewayMode:
        if self.sdk is None:
            return "MOCK"
        return "PRUEBA" if self.access_token.startswith("TEST-") else "PRODUCCION"

    def status_snapshot(self) -> MercadoPagoStatus:
        return MercadoPagoStatus(
            mode=self.mode,
            live=self.is_live,
            public_key=self.public_key,
            webhook_signature_enforced=bool(self.webhook_secret),
            back_url_base=self.public_base_url,
        )

    # ------------------------------------------------------------- checkout
    def build_preference_payload(self, *, user_email: str, plan: PlanCode, case_id: str | None) -> dict[str, Any]:
        """Arma el cuerpo de la preferencia. El webhook se configura en el panel de MP (notification_url está obsoleto en el SDK 3.6.0)."""
        title, price = PLAN_CATALOG[plan]
        return {
            "items": [
                {
                    "id": plan,
                    "title": title,
                    "quantity": 1,
                    "unit_price": float(price),
                    "currency_id": "ARS",
                }
            ],
            "payer": {"email": user_email},
            "back_urls": {
                "success": f"{self.public_base_url}/?payment=success",
                "failure": f"{self.public_base_url}/?payment=failure",
                "pending": f"{self.public_base_url}/?payment=pending",
            },
            "auto_return": "approved",
            "external_reference": build_external_reference(user_email, plan, case_id),
            "metadata": {"user_email": user_email.strip().lower(), "plan_type": plan},
            "statement_descriptor": "DRH ARGENTINA",
        }

    def create_preference(
        self,
        *,
        user_email: str,
        plan_type: str = "CONSULTA_INDIVIDUAL",
        case_id: str | None = None,
    ) -> CreatePreferenceResponse:
        """Crea la preferencia y devuelve el `init_point` al que el frontend redirige al usuario."""
        if not user_email or "@" not in user_email:
            raise ValueError("Se requiere un email válido del pagador.")
        plan = normalize_plan(plan_type)
        title, price = PLAN_CATALOG[plan]
        amount_str = f"{price:.2f}"

        if self.sdk is None:
            mock_id = f"mock_pref_{secrets.token_hex(8)}"
            return CreatePreferenceResponse(
                preference_id=mock_id,
                init_point=f"https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id={mock_id}",
                sandbox_init_point=f"https://sandbox.mercadopago.com.ar/checkout/v1/redirect?pref_id={mock_id}",
                amount_ars=amount_str,
                amount=amount_str,
                currency="ARS",
                plan_type=plan,
                title=title,
                public_key=self.public_key,
                is_mock=True,
            )

        payload = self.build_preference_payload(user_email=user_email, plan=plan, case_id=case_id)
        try:
            result = self.sdk.preference().create(payload)
        except Exception as exc:
            _logger.error("Error de red llamando a Mercado Pago (preference.create): %s", exc)
            raise MercadoPagoError("No se pudo conectar con Mercado Pago. Intentá nuevamente.") from exc

        http_status = int(result.get("status") or 0)
        body = result.get("response") or {}
        init_point = str(body.get("init_point") or "")
        if http_status not in (200, 201) or not init_point:
            _logger.error(
                "Mercado Pago rechazó la preferencia (HTTP %s): %s",
                http_status,
                body.get("message") or body.get("error") or "sin detalle",
            )
            raise MercadoPagoError(f"Mercado Pago rechazó la creación del checkout (HTTP {http_status}).")

        return CreatePreferenceResponse(
            preference_id=str(body.get("id") or ""),
            init_point=init_point,
            sandbox_init_point=str(body.get("sandbox_init_point") or ""),
            amount_ars=amount_str,
            amount=amount_str,
            currency="ARS",
            plan_type=plan,
            title=title,
            public_key=self.public_key,
            is_mock=False,
        )

    # -------------------------------------------------------------- webhook
    @staticmethod
    def extract_notification(
        query_params: Mapping[str, str], body: Mapping[str, Any]
    ) -> tuple[str, str | None]:
        """Obtiene (tópico, id de pago) de un Webhook o de una IPN legacy.

        Importante: el `id` de primer nivel del cuerpo de un Webhook es el id de la NOTIFICACIÓN,
        no del pago; el id del pago está en `data.id` (cuerpo o query string).
        """
        data_obj = body.get("data") if isinstance(body.get("data"), Mapping) else {}
        topic = str(
            body.get("type") or body.get("topic") or query_params.get("type") or query_params.get("topic") or ""
        ).strip().lower()
        data_id = data_obj.get("id") or query_params.get("data.id")
        if not data_id and topic == "payment":
            data_id = query_params.get("id")
        return topic, (str(data_id).strip() if data_id else None)

    def verify_webhook_signature(self, *, x_signature: str | None, x_request_id: str | None, data_id: str) -> None:
        """Valida `x-signature` (HMAC-SHA256) según el esquema de Mercado Pago.

        Manifiesto firmado: `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`
        (data.id en minúsculas si es alfanumérico; se omiten las partes ausentes).
        Si `MP_WEBHOOK_SECRET` no está configurado, la validación no se exige.
        """
        if not self.webhook_secret:
            return
        if not x_signature:
            raise WebhookSignatureError("Falta la cabecera x-signature.")

        parts: dict[str, str] = {}
        for chunk in x_signature.split(","):
            key, sep, value = chunk.partition("=")
            if sep:
                parts[key.strip()] = value.strip()
        ts = parts.get("ts")
        received_hash = parts.get("v1")
        if not ts or not received_hash:
            raise WebhookSignatureError("Cabecera x-signature mal formada (faltan ts o v1).")

        manifest = ""
        if data_id:
            manifest += f"id:{data_id.lower() if data_id.isalnum() else data_id};"
        if x_request_id:
            manifest += f"request-id:{x_request_id};"
        manifest += f"ts:{ts};"

        expected = hmac.new(self.webhook_secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise WebhookSignatureError("La firma del Webhook no es válida.")

    def process_webhook(
        self,
        *,
        query_params: Mapping[str, str],
        body: Mapping[str, Any],
        x_signature: str | None = None,
        x_request_id: str | None = None,
    ) -> WebhookResult:
        """Procesa la notificación y acredita el beneficio solo ante un pago aprobado y verificado."""
        topic, data_id = self.extract_notification(query_params, body)
        if not data_id:
            return WebhookResult(status="IGNORED", reason="La notificación no contiene id de pago.")

        self.verify_webhook_signature(x_signature=x_signature, x_request_id=x_request_id, data_id=data_id)

        if topic not in _PAYMENT_TOPICS:
            return WebhookResult(status="IGNORED", payment_id=data_id, reason=f"Tópico no gestionado: '{topic}'.")

        if self.sdk is None:
            return WebhookResult(
                status="IGNORED",
                payment_id=data_id,
                reason="Pasarela en modo MOCK: no se acreditan pagos sin verificación contra Mercado Pago.",
            )

        try:
            result = self.sdk.payment().get(data_id)
        except Exception as exc:
            _logger.error("Error de red consultando el pago %s en Mercado Pago: %s", data_id, exc)
            raise MercadoPagoError("No se pudo consultar el pago en Mercado Pago.") from exc

        http_status = int(result.get("status") or 0)
        if http_status == 404:
            return WebhookResult(status="IGNORED", payment_id=data_id, reason="El pago no existe en Mercado Pago.")
        if http_status != 200:
            raise MercadoPagoError(f"Mercado Pago respondió HTTP {http_status} al consultar el pago {data_id}.")

        payment = result.get("response") or {}
        mp_status = str(payment.get("status") or "")
        if mp_status != "approved":
            return WebhookResult(
                status="NOT_APPROVED",
                payment_id=data_id,
                mp_status=mp_status,
                reason=f"Pago en estado '{mp_status}' ({payment.get('status_detail') or 'sin detalle'}).",
            )

        parsed = parse_external_reference(str(payment.get("external_reference") or ""))
        if parsed is None:
            _logger.error("Pago aprobado %s sin external_reference válida de Dr. H.", data_id)
            return WebhookResult(
                status="REJECTED", payment_id=data_id, mp_status=mp_status,
                reason="external_reference ausente o no emitida por Dr. H.",
            )
        user_email, plan = parsed
        _, expected_price = PLAN_CATALOG[plan]

        currency = str(payment.get("currency_id") or "")
        try:
            paid_amount = Decimal(str(payment.get("transaction_amount")))
        except (InvalidOperation, ValueError) as exc:
            raise MercadoPagoError(f"Monto inválido en el pago {data_id}.") from exc

        if currency != "ARS" or paid_amount < expected_price:
            _logger.error(
                "Pago %s inconsistente: %s %s para plan %s (esperado ARS %s).",
                data_id, currency, paid_amount, plan, expected_price,
            )
            return WebhookResult(
                status="REJECTED", payment_id=data_id, user_email=user_email, plan_type=plan, mp_status=mp_status,
                reason=f"Monto/moneda no coinciden con el plan ({currency} {paid_amount} vs ARS {expected_price}).",
            )

        if self.db is None:
            raise MercadoPagoError("Base de datos no disponible para acreditar el pago.")

        outcome = self.db.apply_approved_payment(
            payment_id=data_id,
            user_email=user_email,
            plan_type=plan,
            amount_ars=f"{paid_amount:.2f}",
            currency_id=currency,
        )
        if outcome == "USER_NOT_FOUND":
            _logger.error("Pago aprobado %s para un usuario inexistente (%s): registrado para revisión.", data_id, user_email)
        return WebhookResult(
            status=outcome, payment_id=data_id, user_email=user_email, plan_type=plan, mp_status=mp_status,
        )
