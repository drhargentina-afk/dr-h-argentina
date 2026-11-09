"""Motor de Configuración Segura y Blindaje de Credenciales en Servidor (`core/config.py`).

Garantiza que las claves y credenciales (`GOOGLE_API_KEY`, `ADMIN_USER`, `ADMIN_PASSWORD`)
residan exclusivamente en la memoria privada del servidor FastAPI:
1. Carga determinista desde variables de entorno o archivo `.env` local (excluido de Git).
2. Encapsulamiento mediante `SecretStr` de Pydantic para impedir su impresión accidental en logs.
3. Ninguna credencial queda escrita en el código fuente: si `ADMIN_PASSWORD` no está definida,
   se genera una clave aleatoria criptográficamente segura válida solo para el proceso actual.
4. Middleware ASGI (`ApiKeyLeakGuardMiddleware`) que inspecciona cabeceras y cuerpos de salida HTTP,
   redactando de inmediato cualquier coincidencia accidental con la clave antes de que llegue al navegador.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import os
from pathlib import Path
import secrets

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_logger = logging.getLogger("dr_h.config")

DEFAULT_ADMIN_USER = "dr.h.argentina@gmail.com"
OFFICIAL_ADMIN_EMAIL = "dr.h.argentina@gmail.com"
ADMIN_EMAILS: tuple[str, ...] = (
    "dr.h.argentina@gmail.com",
    "mendezdamian081@gmail.com",
)
MIN_ADMIN_PASSWORD_LENGTH = 12


def _parse_dotenv_file(dotenv_path: Path) -> dict[str, str]:
    """Lee un archivo `.env` clave=valor sin depender de paquetes externos."""
    values: dict[str, str] = {}
    if not dotenv_path.exists():
        return values
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        clean_key = key.strip()
        clean_val = val.strip().strip('"').strip("'")
        if clean_key:
            values[clean_key] = clean_val
            os.environ.setdefault(clean_key, clean_val)
    return values


def _resolve_admin_password(raw_password: str) -> SecretStr:
    """Devuelve la clave de administrador del entorno o genera una aleatoria segura para el proceso.

    Criterio: si `ADMIN_PASSWORD` está definida debe tener al menos 12 caracteres; si no existe,
    se genera con `secrets.token_urlsafe(24)` (~192 bits de entropía) y no se persiste en disco.
    """
    clean = raw_password.strip()
    if clean:
        if len(clean) < MIN_ADMIN_PASSWORD_LENGTH:
            raise ValueError(
                f"ADMIN_PASSWORD debe tener al menos {MIN_ADMIN_PASSWORD_LENGTH} caracteres."
            )
        return SecretStr(clean)

    _logger.warning(
        "ADMIN_PASSWORD no está definida: se generó una clave aleatoria temporal para este proceso. "
        "Definí ADMIN_PASSWORD en el archivo .env o en las variables de entorno del servidor."
    )
    return SecretStr(secrets.token_urlsafe(24))


class ServerSecurityConfig(BaseModel):
    """Configuración inmutable de servidor para Dr. H Argentina (Nodo AR)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    google_ai_studio_api_key: SecretStr = Field(
        ...,
        description="API Key privada de Google AI Studio (jamás expuesta al cliente PWA).",
    )
    admin_user: str = Field(
        default=DEFAULT_ADMIN_USER,
        min_length=3,
        max_length=64,
        description="Usuario Administrador exclusivo (ADMIN_USER).",
    )
    admin_email: str = Field(
        default=OFFICIAL_ADMIN_EMAIL,
        description="Correo oficial único con privilegios de administrador en Google OAuth.",
    )
    admin_password: SecretStr = Field(
        ...,
        description="Contraseña del Administrador (ADMIN_PASSWORD), nunca escrita en el código fuente.",
    )
    admin_session_ttl_hours: int = Field(default=12, ge=1, le=168)
    google_client_id: str = Field(
        default="373505917083-t812fbg0fa29k4q4vd2vcj0vdbgk8kq9.apps.googleusercontent.com",
        description="Client ID de Google Identity Services OAuth 2.0.",
    )
    external_micro_payment_url: str = Field(
        default="https://dr-h-argentina.onrender.com/#payment-micro",
        description="URL externa Lemon Squeezy para Pack Micro-Consultas / Recargas $4.000 ARS (EXTERNAL_MICRO_PAYMENT_URL).",
    )
    external_monthly_payment_url: str = Field(
        default="https://dr-h-argentina.onrender.com/#payment-monthly",
        description="URL externa Lemon Squeezy para Pase Mensual por Expediente $20.000 ARS (EXTERNAL_MONTHLY_PAYMENT_URL).",
    )
    external_payment_url: str = Field(
        default="https://dr-h-argentina.onrender.com/#payment-monthly",
        description="URL externa de fallback (EXTERNAL_PAYMENT_URL).",
    )
    database_url: str = Field(default="", description="Ruta de base de datos SQLite o DATABASE_URL PostgreSQL.")
    node_country: str = Field(default="AR", min_length=2, max_length=2)
    platform_version: str = Field(default="1.4.0-AR-PWA", min_length=3)
    gemini_model_primary: str = Field(default="gemini-2.5-flash", min_length=3)
    rate_limit_free_rpm: int = Field(default=15, ge=1, le=120)
    rate_limit_max_retries: int = Field(default=5, ge=1, le=10)
    rate_limit_base_cooling_seconds: Decimal = Field(default=Decimal("1.50"), gt=Decimal("0.00"))

    @classmethod
    def load(cls, dotenv_path: str | Path | None = None) -> ServerSecurityConfig:
        """Carga la configuración desde `.env` y variables de entorno con prioridad segura."""
        resolved_path = (
            Path(dotenv_path)
            if dotenv_path is not None
            else Path(__file__).resolve().parent.parent / ".env"
        )
        file_vars = _parse_dotenv_file(resolved_path)

        def _get(name: str, default: str = "") -> str:
            return os.environ.get(name) or file_vars.get(name) or default

        raw_key = (
            os.getenv("GOOGLE_API_KEY")
            or file_vars.get("GOOGLE_API_KEY")
            or _get("GOOGLE_AI_STUDIO_API_KEY", "")
        )
        if not raw_key or len(raw_key.strip()) < 16:
            raise ValueError(
                "GOOGLE_API_KEY / GOOGLE_AI_STUDIO_API_KEY inválida o ausente en el entorno (.env)."
            )

        try:
            ttl_hours = int(_get("ADMIN_SESSION_TTL_HOURS", "12"))
        except ValueError as exc:
            raise ValueError("ADMIN_SESSION_TTL_HOURS debe ser un número entero de horas.") from exc

        raw_monthly_url = _get("EXTERNAL_MONTHLY_PAYMENT_URL", "").strip()
        raw_micro_url = _get("EXTERNAL_MICRO_PAYMENT_URL", "").strip()
        raw_legacy_url = _get("EXTERNAL_PAYMENT_URL", "").strip()

        monthly_url = (
            raw_monthly_url
            or raw_legacy_url
            or "https://dr-h-argentina.onrender.com/#payment-monthly"
        )
        micro_url = (
            raw_micro_url
            or "https://dr-h-argentina.onrender.com/#payment-micro"
        )
        fallback_url = raw_legacy_url or monthly_url

        return cls(
            google_ai_studio_api_key=SecretStr(raw_key.strip()),
            admin_user=_get("ADMIN_USER", DEFAULT_ADMIN_USER).strip(),
            admin_email=OFFICIAL_ADMIN_EMAIL,
            admin_password=_resolve_admin_password(_get("ADMIN_PASSWORD", "")),
            admin_session_ttl_hours=ttl_hours,
            google_client_id=_get(
                "GOOGLE_CLIENT_ID",
                "373505917083-t812fbg0fa29k4q4vd2vcj0vdbgk8kq9.apps.googleusercontent.com",
            ).strip(),
            external_micro_payment_url=micro_url,
            external_monthly_payment_url=monthly_url,
            external_payment_url=fallback_url,
            database_url=_get("DATABASE_URL", "").strip(),
            node_country=_get("DR_H_NODE_COUNTRY", "AR"),
            platform_version=_get("DR_H_PLATFORM_VERSION", "1.4.0-AR-PWA"),
            gemini_model_primary=_get("GEMINI_MODEL_PRIMARY", "gemini-2.5-flash"),
            rate_limit_free_rpm=int(_get("RATE_LIMIT_FREE_RPM", "15")),
            rate_limit_max_retries=int(_get("RATE_LIMIT_MAX_RETRIES", "5")),
            rate_limit_base_cooling_seconds=Decimal(_get("RATE_LIMIT_BASE_COOLING_SECONDS", "1.50")),
        )

    def get_masked_key_fingerprint(self) -> str:
        """Devuelve una huella enmascarada segura para auditoría interna."""
        secret = self.google_ai_studio_api_key.get_secret_value()
        if len(secret) <= 10:
            return "***REDACTED***"
        return f"{secret[:4]}...{secret[-4:]}"

    def is_key_configured(self) -> bool:
        """Indica si la clave de Google AI Studio está cargada en el servidor."""
        return len(self.google_ai_studio_api_key.get_secret_value()) >= 16


class ApiKeyLeakGuardMiddleware:
    """Middleware ASGI que impide cualquier filtración accidental de la API Key hacia el cliente."""

    def __init__(self, app: ASGIApp, config: ServerSecurityConfig) -> None:
        self.app = app
        self._raw_secret_bytes = config.google_ai_studio_api_key.get_secret_value().encode("utf-8")
        self._replacement_bytes = b"[REDACTED_SERVER_API_KEY]"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _guarded_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                # Nunca permitir que la clave aparezca en cabeceras HTTP
                for h_key, h_val in list(headers.items()):
                    if self._raw_secret_bytes.decode("utf-8") in h_val:
                        headers[h_key] = h_val.replace(
                            self._raw_secret_bytes.decode("utf-8"),
                            "[REDACTED_SERVER_API_KEY]",
                        )
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and self._raw_secret_bytes in body:
                    scrubbed = body.replace(self._raw_secret_bytes, self._replacement_bytes)
                    message = {**message, "body": scrubbed}
            await send(message)

        await self.app(scope, receive, _guarded_send)
