"""Motor de Configuración Segura y Blindaje de Credenciales en Servidor (`core/config.py`).

Garantiza que la API Key de Google AI Studio (`GOOGLE_AI_STUDIO_API_KEY`) resida exclusivamente
en la memoria privada del servidor FastAPI:
1. Carga determinista desde el archivo `.env` del proyecto o variables de entorno del sistema.
2. Encapsulamiento mediante `SecretStr` de Pydantic para impedir su impresión accidental en logs o tracebacks.
3. Middleware ASGI (`ApiKeyLeakGuardMiddleware`) que inspecciona cabeceras y cuerpos de salida HTTP,
   redactando de inmediato cualquier coincidencia accidental con la clave antes de que llegue al navegador.
"""
from __future__ import annotations

from decimal import Decimal
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


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


class ServerSecurityConfig(BaseModel):
    """Configuración inmutable de servidor para Dr. H Argentina (Nodo AR)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    google_ai_studio_api_key: SecretStr = Field(
        ...,
        description="API Key privada de Google AI Studio (jamás expuesta al cliente PWA).",
    )
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

        return cls(
            google_ai_studio_api_key=SecretStr(raw_key.strip()),
            node_country=_get("DR_H_NODE_COUNTRY", "AR"),
            platform_version=_get("DR_H_PLATFORM_VERSION", "1.4.0-AR-PWA"),
            gemini_model_primary=_get("GEMINI_MODEL_PRIMARY", "gemini-2.5-flash"),
            rate_limit_free_rpm=int(_get("RATE_LIMIT_FREE_RPM", "15")),
            rate_limit_max_retries=int(_get("RATE_LIMIT_MAX_RETRIES", "5")),
            rate_limit_base_cooling_seconds=Decimal(_get("RATE_LIMIT_BASE_COOLING_SECONDS", "1.50")),
        )

    def get_masked_key_fingerprint(self) -> str:
        """Devuelve una huella enmascarada segura para auditoría interna (ej. 'AQ.A...DgQ')."""
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
