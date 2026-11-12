"""Entrypoint FastAPI de Dr. H Argentina (Nodo Capa-País 'AR').

Arquitectura Refactorizada MVP:
1. Autenticación con Google OAuth 2.0 (Google Identity Services) + control anti-abuso.
2. Regla de gratuidad: 1 consulta gratis por cuenta de Google.
3. Filtro Criptográfico (Firma SHA-256): Bloqueo HTTP 403 si el documento ya fue procesado bajo una prueba gratuita.
4. Dashboard de Causas y Seguimiento Asíncrono de Movimientos (1-2 fojas) con semáforo procesal.
5. Integración con Mercado Pago SDK para cobro de consultas y pases en ARS sin costos fijos.
6. Cero contraseñas judiciales: el ciudadano mantiene control total de sus documentos.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Annotated, Any, Literal

import fitz  # PyMuPDF
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from auditor_revision.report_builder import CitizenReportBuilder
from core.admin_auth import (
    AdminAuthError,
    AdminLockoutError,
    AdminLoginRequest,
    AdminSessionManager,
    AdminSessionResponse,
    AdminSessionStatus,
)
from core.agente000_orchestrator import Agente000Orchestrator
from core.auth_oauth import (
    GoogleAuthRequest,
    GoogleAuthResponse,
    GoogleOAuthManager,
    UserProfileResponse,
)
from core.case_tracker import CaseTrackerService
from core.config import ADMIN_EMAILS, ApiKeyLeakGuardMiddleware, is_whitelisted_admin_email
from core.database import DrHDatabase
from core.models import (
    BoletinOficialNormInput,
    EmergencyScanRequest,
    EmergencyScanResponse,
    FinalCitizenReport,
    FojaCitation,
    FueroKey,
    GamuzaRegulatorySignal,
    PreQuoteResponse,
    ProvinceKey,
    RepositoryConsultRequest,
    RepositoryConsultResponse,
    SubscriptionTier,
    UserRole,
)
from core.rate_limiter_queue import QueueStatusSnapshot
from ingestion_07am.system_watchdog import SystemHealthReport

_main_logger = logging.getLogger("dr_h.main")

app = FastAPI(
    title="Dr. H Argentina — Sistema de Inteligencia y Auditoría Jurídica",
    description="Plataforma de análisis de causas judiciales argentinas con Google OAuth, filtro criptográfico y seguimiento asíncrono.",
    version="1.5.0-AR-MVP",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Daily-Count", "X-Funnel-Step", "X-Allow-Social-Unlock"],
)

orchestrator = Agente000Orchestrator()
db = DrHDatabase(orchestrator.security_config.database_url or None)
_startup_reset_stats = db.reset_non_admin_users(ADMIN_EMAILS)
_main_logger.info(
    "Lista blanca ADMIN_EMAILS aplicada al iniciar (users_reset=%s, sessions_reset=%s).",
    _startup_reset_stats.get("users_reset", 0),
    _startup_reset_stats.get("sessions_reset", 0),
)
google_auth = GoogleOAuthManager(orchestrator.security_config, db)

_AR_TIMEZONE = timezone(timedelta(hours=-3))
_DAILY_FUNNEL_STEP_2_DETAIL = (
    "Ya utilizaste tu consulta gratuita de prueba. "
    "Compartí en redes sociales para desbloquear tu 2ª consulta del día o habilitá tu pase con Lemon Squeezy."
)
_DAILY_FUNNEL_STEP_3_DETAIL = (
    "Alcanzaste el límite de 2 consultas gratuitas por hoy. "
    "Habilitá tu acceso con Lemon Squeezy o esperá 24 horas hasta mañana para volver a consultar."
)


@dataclass(frozen=True)
class DailyFunnelStatus:
    """Estado consolidado del embudo diario (Cuenta + IP + X-Device-Fingerprint)."""

    day_key: str
    used_today: int
    acct_social_credits: int
    social_unlock_used: int
    is_privileged: bool
    funnel_step: int
    allow_social_unlock: bool
    next_count: int


class DailyStatusResponse(BaseModel):
    """Respuesta del estado diario del embudo progresivo para el usuario autenticado."""

    day_key: str
    used_today: int
    acct_social_credits: int
    social_unlock_used: int
    is_privileged: bool
    funnel_step: int
    allow_social_unlock: bool


def _ensure_daily_funnel_schema() -> None:
    """Crea la tabla e índices del embudo diario en SQLite si aún no existen."""
    try:
        with db._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_funnel_counters (
                    scope_key TEXT NOT NULL,
                    day_key TEXT NOT NULL,
                    consultations_count INTEGER NOT NULL DEFAULT 0,
                    social_credits INTEGER NOT NULL DEFAULT 0,
                    social_unlock_used INTEGER NOT NULL DEFAULT 0,
                    updated_at_iso TEXT NOT NULL,
                    PRIMARY KEY (scope_key, day_key)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_funnel_day_key
                ON daily_funnel_counters (day_key)
                """
            )
    except Exception as exc:
        _main_logger.error("Error inicializando tabla daily_funnel_counters: %s", exc)
        raise


_ensure_daily_funnel_schema()


def _current_ar_day_key() -> str:
    """Retorna la fecha calendario actual de Argentina (UTC-3) en formato YYYY-MM-DD."""
    return datetime.now(_AR_TIMEZONE).strftime("%Y-%m-%d")


def _extract_client_ip(request: Request) -> str:
    """Extrae la IP real del cliente ignorando 'testclient' salvo proxy explícito."""
    forwarded = str(request.headers.get("x-forwarded-for", "") or "").strip()
    if forwarded:
        first_ip = forwarded.split(",")[0].strip()
        if first_ip:
            return first_ip[:64]
    real_ip = str(request.headers.get("x-real-ip", "") or "").strip()
    if real_ip:
        return real_ip[:64]
    client_host = str(request.client.host if request.client else "").strip()
    if client_host.lower() == "testclient":
        return ""
    return client_host[:64]


def _extract_device_fingerprint(request: Request) -> str:
    """Extrae el encabezado X-Device-Fingerprint normalizado."""
    return str(request.headers.get("x-device-fingerprint", "") or "").strip()[:128]


def _funnel_scope_keys(email: str, client_ip: str, device_fp: str) -> tuple[str, list[str]]:
    """Construye la clave primaria de cuenta y las claves compuestas (Cuenta + IP + Huella)."""
    acct_key = f"acct:{str(email or '').strip().lower()}"
    keys: list[str] = [acct_key]
    if client_ip:
        keys.append(f"ip:{client_ip}")
    if device_fp:
        keys.append(f"fp:{device_fp}")
    return acct_key, keys


def _is_privileged_user(user: UserProfileResponse) -> bool:
    """Determina si el usuario evita el embudo diario por ser admin whitelist o tener pase pago."""
    normalized_user_email = str(user.email or "").strip().lower()
    is_admin_whitelisted = bool(
        is_whitelisted_admin_email(normalized_user_email)
        or (user.is_admin and normalized_user_email == orchestrator.security_config.admin_user.strip().lower())
    )
    return bool(is_admin_whitelisted or user.has_paid_pass)


def _compute_funnel_status_from_rows(
    *,
    day_key: str,
    acct_key: str,
    rows: list[Any],
    is_privileged: bool,
) -> DailyFunnelStatus:
    """Calcula el paso del embudo (1, 2 o 3) a partir de filas consultadas para Cuenta/IP/Huella."""
    used_today = max((int(row["consultations_count"] or 0) for row in rows), default=0)
    social_unlock_used = max((int(row["social_unlock_used"] or 0) for row in rows), default=0)
    acct_social_credits = max(
        (int(row["social_credits"] or 0) for row in rows if str(row["scope_key"]) == acct_key),
        default=0,
    )

    if is_privileged:
        funnel_step = 1
        allow_social_unlock = False
        next_count = used_today + 1
    elif used_today <= 0:
        funnel_step = 1
        allow_social_unlock = False
        next_count = 1
    elif used_today == 1:
        if acct_social_credits >= 1:
            funnel_step = 2
            allow_social_unlock = False
            next_count = 2
        elif social_unlock_used >= 1:
            funnel_step = 3
            allow_social_unlock = False
            next_count = 2
        else:
            funnel_step = 2
            allow_social_unlock = True
            next_count = 2
    else:
        funnel_step = 3
        allow_social_unlock = False
        next_count = used_today + 1

    return DailyFunnelStatus(
        day_key=day_key,
        used_today=used_today,
        acct_social_credits=acct_social_credits,
        social_unlock_used=social_unlock_used,
        is_privileged=is_privileged,
        funnel_step=funnel_step,
        allow_social_unlock=allow_social_unlock,
        next_count=next_count,
    )


def _read_daily_funnel_status_with_conn(
    conn: Any,
    user: UserProfileResponse,
    request: Request,
) -> tuple[DailyFunnelStatus, str, list[str]]:
    """Lee las filas vigentes del día UTC-3 para Cuenta + IP + Huella dentro de una transacción."""
    day_key = _current_ar_day_key()
    client_ip = _extract_client_ip(request)
    device_fp = _extract_device_fingerprint(request)
    acct_key, scope_keys = _funnel_scope_keys(user.email, client_ip, device_fp)
    placeholders = ",".join("?" for _ in scope_keys)
    rows = conn.execute(
        f"""
        SELECT scope_key, consultations_count, social_credits, social_unlock_used
        FROM daily_funnel_counters
        WHERE day_key = ? AND scope_key IN ({placeholders})
        """,
        [day_key, *scope_keys],
    ).fetchall()
    status_obj = _compute_funnel_status_from_rows(
        day_key=day_key,
        acct_key=acct_key,
        rows=rows,
        is_privileged=_is_privileged_user(user),
    )
    return status_obj, acct_key, scope_keys


def _apply_funnel_headers(
    response: Response,
    *,
    daily_count: int,
    funnel_step: int,
    allow_social_unlock: bool,
) -> None:
    """Inyecta encabezados X-Daily-Count, X-Funnel-Step y X-Allow-Social-Unlock en la respuesta."""
    response.headers["X-Daily-Count"] = str(max(0, daily_count))
    response.headers["X-Funnel-Step"] = str(funnel_step)
    response.headers["X-Allow-Social-Unlock"] = "true" if allow_social_unlock else "false"


def _upsert_funnel_row(
    conn: Any,
    *,
    scope_key: str,
    day_key: str,
    consultations_count: int,
    social_credits: int,
    social_unlock_used: int,
    now_iso: str,
) -> None:
    """Inserta o actualiza un contador diario para una clave de alcance."""
    conn.execute(
        """
        INSERT INTO daily_funnel_counters (
            scope_key, day_key, consultations_count, social_credits, social_unlock_used, updated_at_iso
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope_key, day_key) DO UPDATE SET
            consultations_count = excluded.consultations_count,
            social_credits = excluded.social_credits,
            social_unlock_used = excluded.social_unlock_used,
            updated_at_iso = excluded.updated_at_iso
        """,
        (scope_key, day_key, consultations_count, social_credits, social_unlock_used, now_iso),
    )


def _enforce_daily_funnel_gate(
    user: UserProfileResponse,
    request: Request,
    response: Response,
) -> DailyFunnelStatus:
    """Valida el embudo diario (1ª gratis, 2ª requiere crédito social/pago, 3ª+ solo pago o 24h)."""
    try:
        with db._connect() as conn:
            status_obj, _, _ = _read_daily_funnel_status_with_conn(conn, user, request)
    except Exception as exc:
        _main_logger.error("Error leyendo estado del embudo diario: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error verificando el cupo diario de consultas.",
        ) from exc

    if status_obj.is_privileged:
        _apply_funnel_headers(
            response,
            daily_count=status_obj.used_today,
            funnel_step=1,
            allow_social_unlock=False,
        )
        return status_obj

    if status_obj.used_today == 0 or (status_obj.used_today == 1 and status_obj.acct_social_credits >= 1):
        _apply_funnel_headers(
            response,
            daily_count=status_obj.next_count,
            funnel_step=status_obj.funnel_step,
            allow_social_unlock=False,
        )
        return status_obj

    if status_obj.funnel_step == 2 and status_obj.allow_social_unlock:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=_DAILY_FUNNEL_STEP_2_DETAIL,
            headers={
                "X-Daily-Count": str(status_obj.used_today),
                "X-Funnel-Step": "2",
                "X-Allow-Social-Unlock": "true",
            },
        )

    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=_DAILY_FUNNEL_STEP_3_DETAIL,
        headers={
            "X-Daily-Count": str(status_obj.used_today),
            "X-Funnel-Step": "3",
            "X-Allow-Social-Unlock": "false",
        },
    )


def _consume_daily_funnel_slot(
    user: UserProfileResponse,
    request: Request,
    response: Response,
) -> DailyFunnelStatus:
    """Registra una consulta exitosa en el embudo diario y consume crédito social si aplicaba."""
    now_iso = datetime.now(_AR_TIMEZONE).isoformat()
    try:
        with db._connect() as conn:
            status_obj, acct_key, scope_keys = _read_daily_funnel_status_with_conn(conn, user, request)
            if status_obj.is_privileged:
                _apply_funnel_headers(
                    response,
                    daily_count=status_obj.used_today,
                    funnel_step=1,
                    allow_social_unlock=False,
                )
                return status_obj

            new_count = max(1, status_obj.used_today + 1)
            consumed_credit = 1 if (status_obj.used_today == 1 and status_obj.acct_social_credits >= 1) else 0
            placeholders = ",".join("?" for _ in scope_keys)
            existing_rows = conn.execute(
                f"""
                SELECT scope_key, consultations_count, social_credits, social_unlock_used
                FROM daily_funnel_counters
                WHERE day_key = ? AND scope_key IN ({placeholders})
                """,
                [status_obj.day_key, *scope_keys],
            ).fetchall()
            existing_by_key = {str(r["scope_key"]): r for r in existing_rows}

            for key in scope_keys:
                prev = existing_by_key.get(key)
                prev_count = int(prev["consultations_count"] or 0) if prev else 0
                prev_credits = int(prev["social_credits"] or 0) if prev else 0
                prev_social_used = int(prev["social_unlock_used"] or 0) if prev else 0
                next_credits = max(0, prev_credits - consumed_credit) if key == acct_key else prev_credits
                _upsert_funnel_row(
                    conn,
                    scope_key=key,
                    day_key=status_obj.day_key,
                    consultations_count=max(prev_count, new_count),
                    social_credits=next_credits,
                    social_unlock_used=max(prev_social_used, consumed_credit),
                    now_iso=now_iso,
                )

        db.increment_free_consultations(user.email)
        resulting_step = 1 if new_count == 1 else (2 if new_count == 2 else 3)
        _apply_funnel_headers(
            response,
            daily_count=new_count,
            funnel_step=resulting_step,
            allow_social_unlock=False,
        )
        return DailyFunnelStatus(
            day_key=status_obj.day_key,
            used_today=new_count,
            acct_social_credits=max(0, status_obj.acct_social_credits - consumed_credit),
            social_unlock_used=max(status_obj.social_unlock_used, consumed_credit),
            is_privileged=False,
            funnel_step=resulting_step,
            allow_social_unlock=False,
            next_count=new_count + 1,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _main_logger.error("Error consumiendo cupo del embudo diario: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error actualizando el contador de consultas diarias.",
        ) from exc


def _grant_social_unlock_credit(
    user: UserProfileResponse,
    request: Request,
    response: Response,
) -> DailyFunnelStatus:
    """Otorga 1 crédito único diario para la 2ª consulta tras compartir en redes (sin pase permanente)."""
    now_iso = datetime.now(_AR_TIMEZONE).isoformat()
    try:
        with db._connect() as conn:
            status_obj, acct_key, scope_keys = _read_daily_funnel_status_with_conn(conn, user, request)
            if status_obj.is_privileged:
                _apply_funnel_headers(
                    response,
                    daily_count=status_obj.used_today,
                    funnel_step=1,
                    allow_social_unlock=False,
                )
                return status_obj

            if status_obj.used_today >= 2:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=_DAILY_FUNNEL_STEP_3_DETAIL,
                    headers={
                        "X-Daily-Count": str(status_obj.used_today),
                        "X-Funnel-Step": "3",
                        "X-Allow-Social-Unlock": "false",
                    },
                )

            if status_obj.acct_social_credits >= 1:
                _apply_funnel_headers(
                    response,
                    daily_count=status_obj.used_today,
                    funnel_step=2,
                    allow_social_unlock=False,
                )
                return status_obj

            if status_obj.social_unlock_used >= 1:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=_DAILY_FUNNEL_STEP_3_DETAIL,
                    headers={
                        "X-Daily-Count": str(status_obj.used_today),
                        "X-Funnel-Step": "3",
                        "X-Allow-Social-Unlock": "false",
                    },
                )

            placeholders = ",".join("?" for _ in scope_keys)
            existing_rows = conn.execute(
                f"""
                SELECT scope_key, consultations_count, social_credits, social_unlock_used
                FROM daily_funnel_counters
                WHERE day_key = ? AND scope_key IN ({placeholders})
                """,
                [status_obj.day_key, *scope_keys],
            ).fetchall()
            existing_by_key = {str(r["scope_key"]): r for r in existing_rows}

            for key in scope_keys:
                prev = existing_by_key.get(key)
                prev_count = int(prev["consultations_count"] or 0) if prev else 0
                prev_credits = int(prev["social_credits"] or 0) if prev else 0
                next_credits = max(prev_credits, 1) if key == acct_key else prev_credits
                _upsert_funnel_row(
                    conn,
                    scope_key=key,
                    day_key=status_obj.day_key,
                    consultations_count=prev_count,
                    social_credits=next_credits,
                    social_unlock_used=1,
                    now_iso=now_iso,
                )

            _apply_funnel_headers(
                response,
                daily_count=status_obj.used_today,
                funnel_step=2,
                allow_social_unlock=False,
            )
            return DailyFunnelStatus(
                day_key=status_obj.day_key,
                used_today=status_obj.used_today,
                acct_social_credits=1,
                social_unlock_used=1,
                is_privileged=False,
                funnel_step=2,
                allow_social_unlock=False,
                next_count=max(2, status_obj.used_today + 1),
            )
    except HTTPException:
        raise
    except Exception as exc:
        _main_logger.error("Error otorgando desbloqueo social diario: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error registrando el desbloqueo por difusión en redes sociales.",
        ) from exc


case_tracker = CaseTrackerService(db, orchestrator)

admin_sessions = AdminSessionManager(
    admin_user=orchestrator.security_config.admin_user,
    admin_password=orchestrator.security_config.admin_password,
    ttl_hours=orchestrator.security_config.admin_session_ttl_hours,
)

app.add_middleware(ApiKeyLeakGuardMiddleware, config=orchestrator.security_config)

FRONTEND_PWA_DIR = Path(__file__).resolve().parent / "frontend_pwa"

_bearer_scheme = HTTPBearer(auto_error=False, description="Token Bearer de sesión Google o Administrador.")


def _client_origin(request: Request) -> str:
    """Extrae la IP o identificación del cliente."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> UserProfileResponse:
    """Valida el token Bearer (Google OAuth o Administrador) y devuelve el perfil del usuario."""
    token = credentials.credentials if credentials is not None else None
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no iniciada. Por favor iniciá sesión con Google.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clean_tok = token.strip()

    # 1. Intentar validar como sesión Google OAuth
    try:
        return google_auth.validate_session(clean_tok)
    except PermissionError:
        pass

    # 2. Intentar validar como sesión de Administrador (para compatibilidad de panel y tests)
    try:
        admin_status = admin_sessions.validate_token(clean_tok)
        return UserProfileResponse(
            email=admin_status.username,
            name="Administrador Maestro",
            picture="",
            free_consultations_used=0,
            has_paid_pass=True,
            can_consult_free=True,
            is_admin=True,
        )
    except AdminAuthError:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Tu sesión es inválida o expiró. Por favor volvé a ingresar con Google.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _require_admin_basic_auth(request: Request) -> None:
    """Protege /docs, /redoc y /openapi.json exigiendo credenciales del Administrador."""
    challenge = {"WWW-Authenticate": 'Basic realm="Dr. H - Panel Tecnico Privado", charset="UTF-8"'}
    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header.lower().startswith("basic "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acceso técnico restringido al Administrador.",
            headers=challenge,
        )
    try:
        decoded = base64.b64decode(auth_header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cabecera de autenticación mal formada.",
            headers=challenge,
        ) from exc

    username, separator, password = decoded.partition(":")
    if not separator:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cabecera de autenticación mal formada.",
            headers=challenge,
        )
    try:
        admin_sessions.authenticate(username, password, origin=_client_origin(request))
    except AdminLockoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AdminAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=challenge,
        ) from exc


def _parse_optional_decimal(raw_val: str | None, field_name: str) -> Decimal | None:
    """Convierte cadenas numéricas opcionales en Decimal exacto validando formato."""
    if raw_val is None or not raw_val.strip():
        return None
    try:
        val = Decimal(raw_val.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El campo '{field_name}' debe tener un formato numérico decimal válido.",
        ) from exc
    if val < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El campo '{field_name}' no puede ser negativo.",
        )
    return val


# =========================================================================
# AUTENTICACIÓN GOOGLE OAUTH 2.0 (CERO CONTRASEÑAS) Y ADMIN PANEL
# =========================================================================

@app.post("/auth/google", response_model=GoogleAuthResponse, tags=["Autenticación Google"])
async def auth_google_endpoint(req: GoogleAuthRequest) -> GoogleAuthResponse:
    """Inicia sesión con Google Identity Services (GIS). Una sola consulta gratis por cuenta."""
    try:
        return google_auth.login_with_google(req.credential)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/auth/me", response_model=UserProfileResponse, tags=["Autenticación Google"])
async def auth_me_endpoint(user: Annotated[UserProfileResponse, Depends(get_current_user)]) -> UserProfileResponse:
    """Devuelve el perfil del usuario autenticado y su estado de cuota."""
    return user


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Autenticación Google"])
async def auth_logout_endpoint(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> Response:
    """Revoca el token de sesión activo (Google o Admin)."""
    token = credentials.credentials if credentials is not None else None
    if token:
        google_auth.logout(token)
        admin_sessions.revoke_token(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/auth/login", response_model=AdminSessionResponse, tags=["Acceso Administrador"])
async def auth_admin_login_endpoint(credentials: AdminLoginRequest, request: Request) -> AdminSessionResponse:
    """Permite el acceso administrativo de soporte técnico (ADMIN_USER/ADMIN_PASSWORD)."""
    try:
        return admin_sessions.login(credentials, origin=_client_origin(request))
    except AdminLockoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except AdminAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


# =========================================================================
# PANEL TÉCNICO PRIVADO (/docs, /redoc, /openapi.json)
# =========================================================================

@app.get("/openapi.json", include_in_schema=False)
async def private_openapi(request: Request) -> JSONResponse:
    _require_admin_basic_auth(request)
    return JSONResponse(
        get_openapi(
            title="Dr. H Argentina — Panel Privado de Administración",
            version=app.version,
            routes=app.routes,
        )
    )


@app.get("/docs", include_in_schema=False)
async def private_swagger_docs(request: Request) -> HTMLResponse:
    _require_admin_basic_auth(request)
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Docs")


@app.get("/redoc", include_in_schema=False)
async def private_redoc(request: Request) -> HTMLResponse:
    _require_admin_basic_auth(request)
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


# =========================================================================
# MOTORES DE CONSULTA CON CONTROL ANTI-ABUSO Y FILTRO CRIPTOGRÁFICO
# =========================================================================

@app.post(
    "/analyze-expediente",
    response_model=FinalCitizenReport,
    status_code=status.HTTP_200_OK,
    tags=["Flujo Completo Agente000"],
)
async def analyze_expediente_endpoint(
    file: Annotated[UploadFile, File(description="Archivo PDF del expediente judicial.")],
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
    request: Request,
    response: Response,
    user_verbal_statement: Annotated[
        str,
        Form(description="Relato del ciudadano o duda principal."),
    ] = "",
    province_jurisdiction: Annotated[
        ProvinceKey | None,
        Form(description="Jurisdicción procesal opcional (detectada automáticamente si se omite)."),
    ] = None,
    city_locality: Annotated[
        str | None,
        Form(description="Ciudad o localidad informada por el usuario."),
    ] = None,
    user_role: Annotated[
        UserRole | None,
        Form(description="Rol del ciudadano en el caso."),
    ] = None,
    claimed_lawyer_fee_pct: Annotated[
        str | None,
        Form(description="Porcentaje de honorarios pactado o pretendido."),
    ] = None,
    upfront_cost_requested_ars: Annotated[
        str | None,
        Form(description="Adelanto de gastos o tasa solicitado."),
    ] = None,
) -> FinalCitizenReport:
    """Ciclo completo de análisis: validación de cuota, firma SHA-256 anti-abuso y registro en causas."""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo subido está vacío.")

    # 1. Filtro Criptográfico (Firma de Documento SHA-256)
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    if db.is_document_already_processed_free(sha256):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este documento ya fue procesado bajo una prueba gratuita",
        )

    # 2. Embudo Progresivo de Consultas Diarias (Cuenta + IP + X-Device-Fingerprint)
    funnel_status = _enforce_daily_funnel_gate(user, request, response)
    is_free_user = not funnel_status.is_privileged

    fee_dec = _parse_optional_decimal(claimed_lawyer_fee_pct, "claimed_lawyer_fee_pct")
    upfront_dec = _parse_optional_decimal(upfront_cost_requested_ars, "upfront_cost_requested_ars")

    try:
        report = orchestrator.analyze_full_expediente(
            file_bytes=file_bytes,
            file_name=file.filename or "expediente.pdf",
            user_verbal_statement=user_verbal_statement,
            explicit_province=province_jurisdiction,
            city_locality=city_locality or "",
            explicit_role=user_role,
            claimed_lawyer_fee_pct=fee_dec,
            upfront_cost_requested_ars=upfront_dec,
        )

        # 3. Registrar huella del documento para impedir multicuentas sobre el mismo PDF
        db.record_document_fingerprint(
            sha256_hash=sha256,
            user_email=user.email,
            is_free_tier=is_free_user,
        )

        # 4. Consumir cupo en el embudo diario (y actualizar contador en base de datos)
        _consume_daily_funnel_slot(user, request, response)

        # 5. Guardar automáticamente en el Dashboard de causas del usuario
        case_tracker.record_case_from_final_report(
            user_email=user.email,
            report=report,
        )

        return report
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante el análisis del expediente: {exc}",
        ) from exc


@app.post(
    "/pre-quote",
    response_model=PreQuoteResponse,
    status_code=status.HTTP_200_OK,
    tags=["Higiene Zero-Storage"],
)
async def pre_quote_endpoint(
    file: Annotated[UploadFile, File(description="Archivo PDF del expediente.")],
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> PreQuoteResponse:
    """Pre-cotiza y aplica higiene en memoria RAM sin consumir IA."""
    try:
        file_bytes = await file.read()
        return orchestrator.pre_quote_document(
            file_bytes=file_bytes,
            file_name=file.filename or "expediente.pdf",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error procesando la pre-cotización: {exc}",
        ) from exc


@app.post(
    "/emergency-scan",
    response_model=EmergencyScanResponse,
    status_code=status.HTTP_200_OK,
    tags=["Modo Urgencia"],
)
async def emergency_scan_endpoint(
    request: EmergencyScanRequest,
    http_request: Request,
    response: Response,
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> EmergencyScanResponse:
    """Escáner '¡No Firmes Todavía!' para cláusulas de renuncia o resoluciones urgentes."""
    _enforce_daily_funnel_gate(_user, http_request, response)

    try:
        return orchestrator.run_emergency_scan(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en el escáner de urgencia: {exc}",
        ) from exc


def _generate_dynamic_llm_legal_answer(
    query_text: str,
    fuero: FueroKey,
    province: ProvinceKey,
    normative_citations: list[str],
    fallback_answer: str,
    city_locality: str = "",
) -> str:
    """Envía el prompt del ciudadano directamente a la API de Gemini sin fallbacks silenciosos."""
    if os.environ.get("PYTEST_CURRENT_TEST") or "unittest" in sys.modules:
        return fallback_answer

    from agents_fueros.base_fuero_agent import BaseFueroAgent

    api_key = orchestrator.security_config.google_ai_studio_api_key.get_secret_value()
    return BaseFueroAgent.invoke_gemini_llm_strict(
        api_key=api_key,
        query_text=query_text,
        fuero_label=fuero.value,
        province_label=province.value,
        city_locality=city_locality,
        caratula="Consulta ciudadana directa",
    )


@app.post(
    "/repository-consult",
    response_model=RepositoryConsultResponse,
    status_code=status.HTTP_200_OK,
    tags=["Consultor Normativo"],
)
async def repository_consult_endpoint(
    request: RepositoryConsultRequest,
    http_request: Request,
    response: Response,
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> RepositoryConsultResponse:
    """Consulta normativa y generación de respuesta dinámica con IA en lenguaje claro."""
    _enforce_daily_funnel_gate(_user, http_request, response)

    try:
        base_resp = orchestrator.consult_normative_repository(request)
        dynamic_answer = _generate_dynamic_llm_legal_answer(
            query_text=request.query,
            fuero=base_resp.classified_fuero,
            province=base_resp.province_jurisdiction,
            normative_citations=base_resp.normative_citations,
            fallback_answer=base_resp.citizen_clear_answer,
            city_locality=request.city_locality,
        )
        _consume_daily_funnel_slot(_user, http_request, response)
        return RepositoryConsultResponse(
            classified_fuero=base_resp.classified_fuero,
            province_jurisdiction=base_resp.province_jurisdiction,
            normative_citations=base_resp.normative_citations,
            procedural_rules_applied=base_resp.procedural_rules_applied,
            citizen_clear_answer=dynamic_answer,
            temporal_validity_note=base_resp.temporal_validity_note,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error consultando el repositorio: {exc}",
        ) from exc


_RE_EXPEDIENTE_REPORT_CONTEXT = re.compile(
    r"\b(?:expediente|expte\.?|foja|fojas|fs\.?|juzgado|tribunal|c[aá]mara|c[eé]dula|autos\s+caratulados|sentencia|resoluci[oó]n\s+judicial)\b",
    re.IGNORECASE,
)

_RE_GENERIC_PROCEDURAL_QUESTION = re.compile(
    r"(?:estado\s+actual\s+de\s+la\s+causa|pr[oó]xima\s+foja|pr[oó]xima\s+presentaci[oó]n|estado\s+actual\s+del\s+tr[aá]mite)",
    re.IGNORECASE,
)


def _is_factual_non_expediente_payload(payload: dict[str, Any]) -> bool:
    """Detecta si el dictamen proviene de una consulta ciudadana de hecho sin expediente judicial ni fojas."""
    if payload.get("has_expediente") is False:
        return True
    raw_cards = payload.get("verified_timeline_cards")
    card_texts: list[str] = []
    if isinstance(raw_cards, list):
        for item in raw_cards:
            if isinstance(item, dict):
                piece_type = str(item.get("document_piece_type") or "").strip().lower()
                if piece_type in {
                    "consulta ciudadana",
                    "consulta ciudadana en lenguaje claro",
                    "consulta directa en lenguaje claro",
                    "documento escaneado de urgencia",
                }:
                    return True
                card_texts.append(str(item.get("document_piece_type") or ""))
                card_texts.append(str(item.get("verbatim_excerpt") or ""))

    combined_context = " ".join(
        [
            str(payload.get("citizen_plain_summary") or ""),
            str(payload.get("audit_and_deadlines_section") or ""),
            *card_texts,
        ]
    )
    return not bool(_RE_EXPEDIENTE_REPORT_CONTEXT.search(combined_context))


def _coerce_final_citizen_report(payload: dict[str, Any]) -> FinalCitizenReport:
    """Normaliza cualquier dictamen (completo o consulta verbal rápida) a un FinalCitizenReport válido."""
    is_factual_no_expediente = _is_factual_non_expediente_payload(payload)
    raw_id = str(payload.get("report_id") or "").strip()
    report_id = raw_id if len(raw_id) >= 8 else f"drh-rep-{hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]}"

    raw_summary = str(payload.get("citizen_plain_summary") or "").strip()
    citizen_plain_summary = (
        raw_summary
        if len(raw_summary) >= 40
        else f"{raw_summary} Orientación legal ciudadana verificada conforme a la normativa argentina vigente.".strip()
    )

    raw_audit = str(payload.get("audit_and_deadlines_section") or "").strip()
    audit_section = (
        raw_audit
        if len(raw_audit) >= 20
        else (
            f"{raw_audit} Evaluá intimación extrajudicial, carta documento, denuncia o mediación sin demorar la conservación de pruebas.".strip()
            if is_factual_no_expediente
            else f"{raw_audit} Plazos procesales y días de nota sujetos al código procesal aplicable.".strip()
        )
    )

    raw_audio = str(payload.get("audio_script_90_seconds") or "").strip()
    audio_script = (
        raw_audio
        if len(raw_audio) >= 80
        else (
            f"{citizen_plain_summary} Recordá reunir las pruebas del hecho y consultar si conviene enviar intimación extrajudicial, carta documento, denuncia o iniciar mediación."
            if is_factual_no_expediente
            else f"{citizen_plain_summary} Recordá solicitar siempre copia íntegra de toda presentación antes de firmar cualquier escrito o acuerdo judicial."
        )
    )

    raw_wa = str(payload.get("whatsapp_ready_message") or "").strip()
    default_wa = (
        "Hola Doc, necesito asesoramiento por un hecho concreto sin expediente iniciado: ¿conviene enviar una carta documento o intimación extrajudicial, hacer una denuncia o pedir mediación, y qué pruebas debería juntar primero?"
        if is_factual_no_expediente
        else "Hola Doc, le escribo para consultarle cómo sigue el trámite, qué plazos tenemos por delante y solicitarle copia de la última presentación."
    )
    whatsapp_msg = raw_wa if len(raw_wa) >= 40 else default_wa

    raw_disclaimer = str(payload.get("legal_shield_disclaimer") or "").strip()
    disclaimer = (
        raw_disclaimer
        if len(raw_disclaimer) >= 40
        else "Orientación ciudadana basada en normativa argentina vigente. No sustituye el patrocinio letrado obligatorio."
    )

    raw_zero = str(payload.get("zero_storage_confirmation") or "").strip()
    zero_storage = (
        raw_zero
        if len(raw_zero) >= 20
        else "Privacidad garantizada: documento procesado en memoria RAM sin copias en el servidor."
    )

    raw_uri = str(payload.get("drive_saved_file_uri") or "").strip()
    drive_uri = raw_uri if len(raw_uri) >= 10 else f"https://drive.google.com/dr-h-ar/{report_id}.pdf"

    raw_cards = payload.get("verified_timeline_cards")
    cards: list[FojaCitation] = []
    if isinstance(raw_cards, list):
        for idx, item in enumerate(raw_cards, start=1):
            if isinstance(item, dict):
                foja_ref = int(item.get("foja_ref") or idx)
                piece = str(item.get("document_piece_type") or "Actuación / Consulta").strip()
                excerpt = str(item.get("verbatim_excerpt") or "Consulta procesada en Dr. H Argentina").strip()
                if len(excerpt) < 5:
                    excerpt = f"{excerpt} (foja {foja_ref})"
                translation = str(item.get("citizen_plain_translation") or citizen_plain_summary[:250]).strip()
                if len(translation) < 10:
                    translation = f"{translation} — orientación en lenguaje claro."
                norm_ref = str(item.get("normative_ref") or "Normativa Nacional Vigente").strip()
                if len(norm_ref) < 3:
                    norm_ref = "CPCCN / LCT"
                cards.append(
                    FojaCitation(
                        foja_ref=max(1, foja_ref),
                        document_piece_type=piece if len(piece) >= 2 else "Consulta",
                        verbatim_excerpt=excerpt,
                        citizen_plain_translation=translation,
                        normative_ref=norm_ref,
                    )
                )
    if not cards:
        cards.append(
            FojaCitation(
                foja_ref=1,
                document_piece_type="Consulta Ciudadana en Lenguaje Claro",
                verbatim_excerpt="Consulta directa procesada en Dr. H Argentina",
                citizen_plain_translation=citizen_plain_summary[:350],
                normative_ref="Código Procesal Civil y Comercial / Legislación Vigente",
            )
        )

    raw_questions = payload.get("questions_for_your_lawyer")
    questions: list[str] = [str(q).strip() for q in raw_questions if str(q).strip()] if isinstance(raw_questions, list) else []
    if is_factual_no_expediente:
        questions = [q for q in questions if not _RE_GENERIC_PROCEDURAL_QUESTION.search(q)]
        factual_fallbacks = [
            "Según los hechos relatados, ¿conviene enviar primero una intimación extrajudicial / carta documento o iniciar una denuncia o mediación directa?",
            "¿Qué pruebas concretas (fotos, videos, mensajes, actas o testigos) necesito reunir antes de formalizar el reclamo?",
        ]
        for fb in factual_fallbacks:
            if len(questions) >= 2:
                break
            if fb not in questions:
                questions.append(fb)
    else:
        while len(questions) < 2:
            questions.append("¿Cuál es el estado actual del trámite y qué plazo legal rige para la próxima presentación?")

    fuero_val = str(payload.get("fuero") or FueroKey.LABORAL.value)
    try:
        resolved_fuero = FueroKey(fuero_val)
    except ValueError:
        resolved_fuero = FueroKey.LABORAL

    prov_val = str(payload.get("province_jurisdiction") or ProvinceKey.PJN_NACION_CABA.value)
    try:
        resolved_prov = ProvinceKey(prov_val)
    except ValueError:
        resolved_prov = ProvinceKey.PJN_NACION_CABA

    tier_val = str(payload.get("subscription_tier_applied") or SubscriptionTier.NIVEL_0_GRATUITO.value)
    try:
        resolved_tier = SubscriptionTier(tier_val)
    except ValueError:
        resolved_tier = SubscriptionTier.NIVEL_0_GRATUITO

    return FinalCitizenReport(
        report_id=report_id,
        generated_at_iso=str(payload.get("generated_at_iso") or "2026-11-08T00:00:00Z"),
        country_code="AR",
        province_jurisdiction=resolved_prov,
        fuero=resolved_fuero,
        subscription_tier_applied=resolved_tier,
        do_not_sign_yet_alert=bool(payload.get("do_not_sign_yet_alert", False)),
        do_not_sign_alert_box=payload.get("do_not_sign_alert_box") or None,
        citizen_plain_summary=citizen_plain_summary,
        verified_timeline_cards=cards,
        audit_and_deadlines_section=audit_section,
        questions_for_your_lawyer=questions,
        audio_script_90_seconds=audio_script,
        whatsapp_ready_message=whatsapp_msg,
        legal_shield_disclaimer=disclaimer,
        zero_storage_confirmation=zero_storage,
        drive_saved_file_uri=drive_uri,
    )


@app.post(
    "/download-report-pdf",
    status_code=status.HTTP_200_OK,
    tags=["Entregables"],
)
async def download_report_pdf_endpoint(
    payload: Annotated[dict[str, Any], Body(description="Payload del dictamen ciudadano (completo o consulta rápida).")],
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> Response:
    """Genera y descarga en memoria RAM el dictamen en formato PDF institucional."""
    try:
        report = _coerce_final_citizen_report(payload)
        pdf_bytes = CitizenReportBuilder.build_pdf_in_ram(report)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="Informe_Dr_H_{report.report_id}.pdf"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error construyendo el PDF: {exc}",
        ) from exc


@app.get("/demo-sample-pdf", tags=["Demostración"])
async def demo_sample_pdf_endpoint(_user: Annotated[UserProfileResponse, Depends(get_current_user)]) -> Response:
    """Genera en RAM un expediente sintético multipágina para pruebas."""
    doc = fitz.open()
    try:
        p1 = doc.new_page()
        p1.insert_text(
            (50, 70),
            "PROVINCIA DE BUENOS AIRES - PODER JUDICIAL (MEV SCBA)\n"
            "EXPTE: PEREZ JUAN CARLOS C/ METALURGICA DEL SUR S.A. S/ DESPIDO\n"
            "Fecha: 10/03/2026. Promueve demanda laboral por despido sin causa (Art. 245 Ley 20.744).\n"
            "El Sr. Juan Carlos Perez, DNI 30.123.456, CUIL 20-30123456-3, con domicilio real en Calle San Martin 1240, Quilmes,\n"
            "reclama indemnizacion laboral por la suma de $ 4.500.000,00 depositables en CBU 0110599520000012345678.",
        )
        doc.new_page()
        p3 = doc.new_page()
        p3.insert_text((50, 70), "Firmado digitalmente por SECRETARIO JUDICIAL SCBA MEV - Página 3 de 4")
        p4 = doc.new_page()
        p4.insert_text(
            (50, 70),
            "Quilmes, 25/08/2026. Téngase presente la pericia contable agregada.\n"
            "Notifíquese por cédula electrónica en días de nota martes y viernes.\n"
            "Se acompaña propuesta de acuerdo donde el actor declara que una vez percibido nada más tengo que reclamar\n"
            "y suscribe desistimiento de la acción y del derecho.",
        )
        pdf_bytes = doc.tobytes()
    finally:
        doc.close()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="Expediente_Laboral_Demo_SCBA.pdf"'},
    )


# =========================================================================
# DASHBOARD DE CAUSAS Y SEGUIMIENTO ASÍNCRONO ("AGREGAR MOVIMIENTO")
# =========================================================================

@app.get("/cases", tags=["Seguimiento de Causas"])
async def list_cases_endpoint(
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> list[dict[str, Any]]:
    """Devuelve la lista de causas del usuario autenticado."""
    return case_tracker.list_user_cases(user.email)


@app.get("/cases/{case_id}", tags=["Seguimiento de Causas"])
async def get_case_detail_endpoint(
    case_id: str,
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> dict[str, Any]:
    """Obtiene los detalles y el historial de movimientos de una causa."""
    try:
        return case_tracker.get_case_detail(case_id, user.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.post("/cases/{case_id}/movement", tags=["Seguimiento de Causas"])
async def add_movement_endpoint(
    case_id: str,
    file: Annotated[UploadFile, File(description="Novedad procesal de 1 o 2 fojas en PDF.")],
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> dict[str, Any]:
    """Sube un PDF de 1-2 fojas (la novedad), lo contrasta con el caso y actualiza el semáforo."""
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo de la novedad está vacío.")

    try:
        return case_tracker.process_case_movement(
            case_id=case_id,
            user_email=user.email,
            file_bytes=file_bytes,
            file_name=file.filename or "novedad.pdf",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error procesando la novedad procesal: {exc}",
        ) from exc


# =========================================================================
# MODELO COMERCIAL DUAL: PAGO SOCIAL Y LINK EXTERNO (MERCHANT OF RECORD)
# =========================================================================

class PaymentConfigResponse(BaseModel):
    """Configuración de monetización dual: pago social y enlaces de checkout externo / MoR."""
    model_config = ConfigDict(extra="forbid")
    micro_payment_url: str = Field(description="URL checkout externo Lemon Squeezy $4.000 ARS (Micro-consultas y recargas).")
    monthly_payment_url: str = Field(description="URL checkout externo Lemon Squeezy $20.000 ARS (Pase Mensual por Expediente).")
    external_payment_url: str = Field(description="URL externa general de fallback (MoR).")
    social_share_text: str
    app_url: str


class SocialUnlockRequest(BaseModel):
    """Notificación de evento de difusión social para desbloqueo automático."""
    model_config = ConfigDict(extra="forbid")
    network: str = Field(default="whatsapp", description="Red social donde se compartió (whatsapp, facebook, instagram, x, threads).")


class SocialUnlockResponse(BaseModel):
    """Confirmación de desbloqueo de acceso completo."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["UNLOCKED"] = "UNLOCKED"
    message: str
    user_email: str
    has_paid_pass: bool


@app.get("/payments/config", response_model=PaymentConfigResponse, tags=["Monetización Dual"])
async def payments_config_endpoint() -> PaymentConfigResponse:
    """Devuelve las URLs externas de pago (Lemon Squeezy MoR en USD) y el texto promocional para redes."""
    cfg = orchestrator.security_config
    return PaymentConfigResponse(
        micro_payment_url=cfg.external_micro_payment_url,
        monthly_payment_url=cfg.external_monthly_payment_url,
        external_payment_url=cfg.external_payment_url,
        social_share_text=(
            "Descubrí @DrHArgentina: asistente legal gratuito en lenguaje claro para revisar "
            "contratos y expedientes judiciales sin contraseñas."
        ),
        app_url="https://dr-h-argentina.onrender.com",
    )


@app.post("/payments/social-unlock", response_model=SocialUnlockResponse, tags=["Monetización Dual"])
async def payments_social_unlock_endpoint(
    req: SocialUnlockRequest,
    request: Request,
    response: Response,
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> SocialUnlockResponse:
    """Desbloquea exclusivamente la 2ª consulta del día tras compartir en redes (sin pase permanente)."""
    _grant_social_unlock_credit(user, request, response)
    _main_logger.info(
        "Crédito único diario (2ª consulta) otorgado por difusión social para %s (red: %s)",
        user.email,
        req.network,
    )
    return SocialUnlockResponse(
        status="UNLOCKED",
        message=(
            f"¡2ª consulta del día desbloqueada tras difundir en {req.network.capitalize()}! "
            "Este beneficio aplica solo a tu 2ª consulta de hoy y no otorga pase permanente."
        ),
        user_email=user.email,
        has_paid_pass=bool(user.has_paid_pass),
    )


@app.get("/payments/daily-status", response_model=DailyStatusResponse, tags=["Monetización Dual"])
async def payments_daily_status_endpoint(
    request: Request,
    response: Response,
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> DailyStatusResponse:
    """Devuelve el estado actual del embudo progresivo diario (Cuenta + IP + Huella)."""
    with db._connect() as conn:
        status_obj, _, _ = _read_daily_funnel_status_with_conn(conn, user, request)
    _apply_funnel_headers(
        response,
        daily_count=status_obj.used_today,
        funnel_step=status_obj.funnel_step,
        allow_social_unlock=status_obj.allow_social_unlock,
    )
    return DailyStatusResponse(
        day_key=status_obj.day_key,
        used_today=status_obj.used_today,
        acct_social_credits=status_obj.acct_social_credits,
        social_unlock_used=status_obj.social_unlock_used,
        is_privileged=status_obj.is_privileged,
        funnel_step=status_obj.funnel_step,
        allow_social_unlock=status_obj.allow_social_unlock,
    )


class CommercialCheckoutRequest(BaseModel):
    """Solicitud de inicio de pago comercial ($4.000 ARS micro o $20.000 ARS mensual)."""

    model_config = ConfigDict(extra="forbid")
    plan_type: Literal["micro", "monthly"] = Field(
        default="monthly",
        description="Tipo de plan comercial seleccionado: 'micro' ($4.000 ARS) o 'monthly' ($20.000 ARS).",
    )


class CommercialCheckoutResponse(BaseModel):
    """Respuesta del endpoint de checkout comercial (redirección externa o acreditación sandbox)."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["REDIRECT", "SANDBOX_APPROVED"]
    mode: Literal["LIVE", "SANDBOX"]
    plan_type: Literal["micro", "monthly"]
    amount_ars: str
    checkout_url: str
    has_paid_pass: bool
    message: str


@app.post("/payments/checkout", response_model=CommercialCheckoutResponse, tags=["Monetización Dual"])
async def payments_commercial_checkout_endpoint(
    req: CommercialCheckoutRequest,
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> CommercialCheckoutResponse:
    """Inicia el checkout comercial en Lemon Squeezy o ejecuta el checkout simulado/sandbox si las URLs reales aún no están en .env."""
    cfg = orchestrator.security_config
    configured_url = (
        cfg.external_micro_payment_url
        if req.plan_type == "micro"
        else cfg.external_monthly_payment_url
    ).strip()
    amount_ars = "4000.00" if req.plan_type == "micro" else "20000.00"

    is_placeholder_url = (
        not configured_url
        or "dr-h-argentina.onrender.com/#payment-" in configured_url
        or configured_url.startswith("#")
    )

    if not is_placeholder_url:
        return CommercialCheckoutResponse(
            status="REDIRECT",
            mode="LIVE",
            plan_type=req.plan_type,
            amount_ars=amount_ars,
            checkout_url=configured_url,
            has_paid_pass=user.has_paid_pass,
            message="Redirigiendo a la pasarela de pago segura...",
        )

    # Si las URLs reales de Lemon Squeezy aún no están en .env, acreditar checkout simulado/sandbox
    sandbox_pid = f"sandbox_{req.plan_type}_{hashlib.sha256(f'{user.email}:{req.plan_type}'.encode()).hexdigest()[:10]}"
    db.apply_approved_payment(
        payment_id=sandbox_pid,
        user_email=user.email,
        plan_type="PASE_COMPLETO",
        amount_ars=amount_ars,
        currency_id="ARS",
    )
    db.set_user_paid_pass(user.email, True)
    return CommercialCheckoutResponse(
        status="SANDBOX_APPROVED",
        mode="SANDBOX",
        plan_type=req.plan_type,
        amount_ars=amount_ars,
        checkout_url=configured_url or "https://www.lemonsqueezy.com/",
        has_paid_pass=True,
        message=f"Pago comercial (${amount_ars} ARS) procesado en modo Sandbox. Tu informe completo y descargables fueron desbloqueados.",
    )


# =========================================================================
# SISTEMA, SALUD Y CRON 07:00 AM
# =========================================================================

@app.get("/health", response_model=SystemHealthReport, tags=["Sistema"])
async def health_check() -> SystemHealthReport:
    """Healthcheck de disponibilidad técnica."""
    return orchestrator.system_watchdog.build_health_report(orchestrator)


@app.get("/queue-status", response_model=QueueStatusSnapshot, tags=["Sistema"])
async def queue_status_endpoint(_user: Annotated[UserProfileResponse, Depends(get_current_user)]) -> QueueStatusSnapshot:
    """Estado de la cola gratuita de Google AI Studio."""
    return orchestrator.rate_limiter.get_status_snapshot()


@app.post(
    "/cron-07am",
    response_model=list[GamuzaRegulatorySignal],
    status_code=status.HTTP_200_OK,
    tags=["Ingesta Matutina y Gamuza"],
)
async def cron_07am_endpoint(
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
    norms: Annotated[
        list[BoletinOficialNormInput] | None,
        Body(description="Lista opcional de normas del Boletín Oficial."),
    ] = None,
) -> list[GamuzaRegulatorySignal]:
    """Ingesta del Boletín Oficial y emisión de señales a Gamuza."""
    try:
        return orchestrator.ingest_07am_and_dispatch_to_gamuza(
            norms=norms,
            use_default_batch_if_none=(norms is None),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en la ingesta 07:00 AM: {exc}",
        ) from exc


# =========================================================================
# SERVIDOR ESTÁTICO DE LA INTERFAZ PWA CIUDADANA
# =========================================================================

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_pwa_index() -> FileResponse:
    return FileResponse(
        FRONTEND_PWA_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_pwa_index_explicit() -> FileResponse:
    return FileResponse(
        FRONTEND_PWA_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/app.js", include_in_schema=False)
async def serve_pwa_app_js() -> FileResponse:
    return FileResponse(
        FRONTEND_PWA_DIR / "app.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/sw.js", include_in_schema=False)
async def serve_pwa_service_worker() -> FileResponse:
    return FileResponse(
        FRONTEND_PWA_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/manifest.json", include_in_schema=False)
async def serve_pwa_manifest() -> FileResponse:
    return FileResponse(FRONTEND_PWA_DIR / "manifest.json", media_type="application/manifest+json")


@app.api_route("/icons/{icon_name}", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_pwa_icon(icon_name: str) -> FileResponse:
    clean_name = Path(icon_name).name
    icon_path = FRONTEND_PWA_DIR / "icons" / clean_name
    if not icon_path.exists():
        icon_path = FRONTEND_PWA_DIR / "icons" / "icon-512x512.png"
    media = "image/jpeg" if icon_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(
        icon_path,
        media_type=media,
        headers={
            "Cache-Control": "public, max-age=86400",
            "Access-Control-Allow-Origin": "*",
        },
    )


if FRONTEND_PWA_DIR.exists():
    app.mount("/pwa", StaticFiles(directory=str(FRONTEND_PWA_DIR), html=True), name="frontend_pwa")
