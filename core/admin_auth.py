"""Gestor de Sesiones del Administrador — Modo Privado Exclusivo (`core/admin_auth.py`).

Responsabilidades (sin dependencia de FastAPI, para mantener la lógica desacoplada de la capa HTTP):
1. Validar credenciales contra `ADMIN_USER` / `ADMIN_PASSWORD` con comparación en tiempo constante
   (`hmac.compare_digest`) para evitar ataques de temporización.
2. Emitir tokens de sesión opacos (`secrets.token_urlsafe`, 256 bits) con vencimiento configurable.
3. Validar y revocar tokens. Los tokens se guardan en memoria del proceso únicamente como hash SHA-256,
   de modo que un volcado de memoria no expone tokens reutilizables.
4. Bloqueo temporal ante fuerza bruta: tras 5 intentos fallidos consecutivos desde un mismo origen,
   se rechazan nuevos intentos durante 5 minutos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import threading
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 5


class AdminLoginRequest(BaseModel):
    """Credenciales enviadas desde la pantalla de ingreso del Administrador."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class AdminSessionResponse(BaseModel):
    """Token de sesión emitido al Administrador tras un ingreso válido."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    username: str
    expires_at_iso: str


class AdminSessionStatus(BaseModel):
    """Estado de una sesión de Administrador válida."""

    model_config = ConfigDict(extra="forbid")

    username: str
    is_admin: Literal[True] = True
    expires_at_iso: str


class AdminAuthError(PermissionError):
    """Credenciales o token inválidos."""


class AdminLockoutError(PermissionError):
    """Origen bloqueado temporalmente por exceso de intentos fallidos."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"Demasiados intentos fallidos. Esperá {retry_after_seconds} segundos antes de reintentar."
        )
        self.retry_after_seconds = retry_after_seconds


def _hash_token(token: str) -> str:
    """Hash SHA-256 del token para almacenamiento en memoria sin exponer el valor original."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AdminSessionManager:
    """Emite, valida y revoca sesiones del Administrador exclusivo."""

    def __init__(
        self,
        admin_user: str,
        admin_password: SecretStr,
        ttl_hours: int = 12,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if not admin_user or not admin_user.strip():
            raise ValueError("ADMIN_USER no puede estar vacío.")
        if not admin_password.get_secret_value():
            raise ValueError("ADMIN_PASSWORD no puede estar vacía.")
        if ttl_hours < 1:
            raise ValueError("La duración de la sesión debe ser de al menos 1 hora.")

        self._admin_user = admin_user.strip()
        self._admin_password = admin_password
        self._ttl = timedelta(hours=ttl_hours)
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self._sessions: dict[str, datetime] = {}
        self._failed_attempts: dict[str, int] = {}
        self._locked_until: dict[str, datetime] = {}
        self._lock = threading.Lock()

    @property
    def admin_user(self) -> str:
        """Nombre del usuario Administrador configurado."""
        return self._admin_user

    def verify_credentials(self, username: str, password: str) -> bool:
        """Compara usuario y contraseña en tiempo constante (ambas comparaciones se evalúan siempre)."""
        user_ok = hmac.compare_digest(username.strip().encode("utf-8"), self._admin_user.encode("utf-8"))
        pass_ok = hmac.compare_digest(
            password.encode("utf-8"),
            self._admin_password.get_secret_value().encode("utf-8"),
        )
        return user_ok and pass_ok

    def _check_lockout(self, origin: str, now: datetime) -> None:
        locked_until = self._locked_until.get(origin)
        if locked_until is None:
            return
        if now < locked_until:
            raise AdminLockoutError(int((locked_until - now).total_seconds()) + 1)
        del self._locked_until[origin]
        self._failed_attempts.pop(origin, None)

    def _authenticate_locked(self, username: str, password: str, origin: str, now: datetime) -> None:
        """Valida credenciales con control de fuerza bruta. Debe invocarse con `self._lock` tomado."""
        self._check_lockout(origin, now)
        if not self.verify_credentials(username, password):
            attempts = self._failed_attempts.get(origin, 0) + 1
            self._failed_attempts[origin] = attempts
            if attempts >= MAX_FAILED_ATTEMPTS:
                self._locked_until[origin] = now + timedelta(minutes=LOCKOUT_MINUTES)
            raise AdminAuthError("Usuario o contraseña de Administrador incorrectos.")
        self._failed_attempts.pop(origin, None)

    def authenticate(self, username: str, password: str, origin: str = "unknown") -> None:
        """Valida credenciales sin emitir token (usado por el acceso Basic a /docs), con bloqueo anti fuerza bruta."""
        now = self._now_fn()
        with self._lock:
            self._authenticate_locked(username, password, origin, now)

    def login(self, request: AdminLoginRequest, origin: str = "unknown") -> AdminSessionResponse:
        """Valida credenciales y emite un token de sesión; aplica bloqueo ante fuerza bruta."""
        now = self._now_fn()
        with self._lock:
            self._authenticate_locked(request.username, request.password, origin, now)
            self._purge_expired(now)

            token = f"drh_admin_{secrets.token_urlsafe(32)}"
            expires_at = now + self._ttl
            self._sessions[_hash_token(token)] = expires_at

        return AdminSessionResponse(
            access_token=token,
            username=self._admin_user,
            expires_at_iso=expires_at.isoformat(),
        )

    def validate_token(self, token: str | None) -> AdminSessionStatus:
        """Devuelve el estado de sesión si el token es válido y no venció; si no, lanza AdminAuthError."""
        if not token or not token.strip():
            raise AdminAuthError("Acceso privado: iniciá sesión como Administrador.")

        now = self._now_fn()
        token_hash = _hash_token(token.strip())
        with self._lock:
            expires_at = self._sessions.get(token_hash)
            if expires_at is None:
                raise AdminAuthError("Sesión de Administrador inválida. Volvé a iniciar sesión.")
            if now >= expires_at:
                del self._sessions[token_hash]
                raise AdminAuthError("La sesión de Administrador venció. Volvé a iniciar sesión.")

        return AdminSessionStatus(username=self._admin_user, expires_at_iso=expires_at.isoformat())

    def revoke_token(self, token: str | None) -> bool:
        """Revoca un token activo. Devuelve True si existía."""
        if not token or not token.strip():
            return False
        with self._lock:
            return self._sessions.pop(_hash_token(token.strip()), None) is not None

    def active_sessions_count(self) -> int:
        """Cantidad de sesiones vigentes (purga las vencidas)."""
        with self._lock:
            self._purge_expired(self._now_fn())
            return len(self._sessions)

    def _purge_expired(self, now: datetime) -> None:
        expired = [h for h, exp in self._sessions.items() if now >= exp]
        for h in expired:
            del self._sessions[h]
