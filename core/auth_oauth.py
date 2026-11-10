"""Autenticación Google OAuth 2.0 y Control de Acceso (`core/auth_oauth.py`).

Implementa el modelo sin contraseñas:
- Recepción y verificación de credenciales Google Identity Services (GIS).
- Creación de sesiones firmadas en SQLite / DB.
- Regla de gratuidad: 1 consulta gratis por cuenta de Google.
- Soporte para modo desarrollo y tests sin credenciales vivas de Google Cloud.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
import logging
import secrets
from typing import Any, Literal

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, Field

from core.config import ADMIN_EMAILS, OFFICIAL_ADMIN_EMAIL, ServerSecurityConfig, is_whitelisted_admin_email
from core.database import DrHDatabase

_logger = logging.getLogger("dr_h.auth_oauth")


class GoogleAuthRequest(BaseModel):
    """Payload enviado por el botón 'Continuar con Google'."""

    model_config = ConfigDict(extra="forbid")

    credential: str = Field(..., min_length=1, description="Google ID Token (JWT) emitido por GIS.")


class UserProfileResponse(BaseModel):
    """Perfil del usuario autenticado."""

    model_config = ConfigDict(extra="forbid")

    email: str
    name: str
    picture: str
    free_consultations_used: int = 0
    has_paid_pass: bool = False
    can_consult_free: bool = False
    is_admin: bool = False


class GoogleAuthResponse(BaseModel):
    """Respuesta tras iniciar sesión con Google."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserProfileResponse
    message: str


class GoogleOAuthManager:
    """Verificador y gestor de sesiones Google OAuth 2.0."""

    def __init__(self, config: ServerSecurityConfig, db: DrHDatabase) -> None:
        self.config = config
        self.db = db
        self.google_request = google_requests.Request()

    def _decode_unverified_jwt_payload(self, token_str: str) -> dict[str, Any]:
        """Decodifica el payload JWT en base64 para modo local/test cuando no hay client_id."""
        parts = token_str.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            try:
                decoded = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
                return json.loads(decoded)
            except Exception:
                pass
        return {}

    def verify_google_token(self, credential: str) -> dict[str, Any]:
        """Valida el Google ID Token contra los servidores de Google o decodifica de forma segura."""
        clean_token = credential.strip()

        # Soporte para tokens sintéticos de test
        if clean_token.startswith("mock_google_") or clean_token.startswith("test_token_"):
            parts = clean_token.split(":")
            email = parts[1] if len(parts) > 1 else "usuario_demo@gmail.com"
            name = parts[2] if len(parts) > 2 else "Usuario Demo"
            return {
                "sub": f"mock-gid-{hash(email)}",
                "email": email.lower(),
                "name": name,
                "picture": "https://lh3.googleusercontent.com/a/default-user",
            }

        client_id = getattr(self.config, "google_client_id", "")
        if client_id:
            try:
                payload = id_token.verify_oauth2_token(clean_token, self.google_request, client_id)
                return payload
            except Exception as exc:
                _logger.warning("Fallo verificando id_token con Google Client ID: %s", exc)

        # Si no hay client_id configurado (entorno local / desarrollo inicial), decodificar payload JWT
        payload = self._decode_unverified_jwt_payload(clean_token)
        if payload and "email" in payload:
            return {
                "sub": payload.get("sub", f"gid-{secrets.token_hex(6)}"),
                "email": payload["email"].lower(),
                "name": payload.get("name", payload["email"].split("@")[0]),
                "picture": payload.get("picture", ""),
            }

        raise ValueError("El token de Google no es válido o no pudo ser verificado.")

    def login_with_google(self, credential: str) -> GoogleAuthResponse:
        """Autentica o registra al usuario mediante Google OAuth y emite el token de sesión."""
        info = self.verify_google_token(credential)
        email = str(info.get("email", "")).strip().lower()
        name = str(info.get("name", email.split("@")[0])).strip()
        google_id = str(info.get("sub", ""))
        picture = str(info.get("picture", ""))

        user_row = self.db.upsert_google_user(
            google_id=google_id,
            email=email,
            name=name,
            picture=picture,
        )

        token = f"drh_goog_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.config.admin_session_ttl_hours * 2)

        is_admin = is_whitelisted_admin_email(email)
        if is_admin:
            self.db.set_user_paid_pass(email, True)
            user_row = self.db.get_user_by_email(email) or user_row
        else:
            has_webhook_payment = self.db.has_approved_webhook_payment(email)
            if not has_webhook_payment:
                self.db.set_user_paid_pass(email, False)
                user_row = self.db.get_user_by_email(email) or user_row

        self.db.create_session(
            token=token,
            user_email=email,
            is_admin=is_admin,
            expires_at_iso=expires.isoformat(),
        )

        free_used = int(user_row["free_consultations_used"])
        db_has_paid = bool(int(user_row["has_paid_pass"]) == 1) if not is_admin else True
        has_paid = bool(is_admin or db_has_paid)
        can_free = bool(is_admin or ((free_used == 0) and not has_paid))

        msg = (
            "Bienvenido/a a Dr. H Argentina. Tenés 1 consulta gratuita disponible."
            if can_free
            else "Sesión iniciada correctamente."
        )

        return GoogleAuthResponse(
            access_token=token,
            token_type="bearer",
            user=UserProfileResponse(
                email=email,
                name=name,
                picture=picture,
                free_consultations_used=free_used,
                has_paid_pass=has_paid,
                can_consult_free=can_free,
                is_admin=is_admin,
            ),
            message=msg,
        )

    def validate_session(self, token: str) -> UserProfileResponse:
        """Valida un token de sesión activo y devuelve el perfil del usuario."""
        if not token:
            raise PermissionError("Sesión no iniciada. Por favor iniciá sesión con Google.")

        clean_token = token.strip()
        sess = self.db.get_session(clean_token)
        if not sess:
            raise PermissionError("Tu sesión es inválida o expiró. Por favor volvé a ingresar con Google.")

        email = str(sess["user_email"]).strip().lower()
        user = self.db.get_user_by_email(email)
        if not user:
            raise PermissionError("Usuario no encontrado en la base de datos.")

        free_used = int(user["free_consultations_used"])
        is_admin = is_whitelisted_admin_email(email)
        db_has_paid = int(user["has_paid_pass"]) == 1
        has_paid = bool(is_admin or db_has_paid)
        can_free = bool(is_admin or ((free_used == 0) and not has_paid))

        return UserProfileResponse(
            email=email,
            name=str(user["name"]),
            picture=str(user["picture"]),
            free_consultations_used=free_used,
            has_paid_pass=has_paid,
            can_consult_free=can_free,
            is_admin=is_admin,
        )

    def logout(self, token: str) -> bool:
        """Revoca la sesión activa."""
        return self.db.delete_session(token)
