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
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from pathlib import Path
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
from core.config import ApiKeyLeakGuardMiddleware
from core.database import DrHDatabase
from core.models import (
    BoletinOficialNormInput,
    EmergencyScanRequest,
    EmergencyScanResponse,
    FinalCitizenReport,
    GamuzaRegulatorySignal,
    PreQuoteResponse,
    ProvinceKey,
    RepositoryConsultRequest,
    RepositoryConsultResponse,
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
)

orchestrator = Agente000Orchestrator()
db = DrHDatabase(orchestrator.security_config.database_url or None)
google_auth = GoogleOAuthManager(orchestrator.security_config, db)
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
    user_verbal_statement: Annotated[
        str,
        Form(description="Relato del ciudadano o duda principal."),
    ] = "",
    province_jurisdiction: Annotated[
        ProvinceKey | None,
        Form(description="Jurisdicción procesal opcional (detectada automáticamente si se omite)."),
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

    # 2. Regla de Gratuidad: 1 sola consulta gratis por cuenta de Google
    is_free_user = not user.is_admin and not user.has_paid_pass
    if is_free_user and user.free_consultations_used >= 1:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Ya utilizaste tu consulta gratuita de prueba. Habilitá tu pase para continuar.",
        )

    fee_dec = _parse_optional_decimal(claimed_lawyer_fee_pct, "claimed_lawyer_fee_pct")
    upfront_dec = _parse_optional_decimal(upfront_cost_requested_ars, "upfront_cost_requested_ars")

    try:
        report = orchestrator.analyze_full_expediente(
            file_bytes=file_bytes,
            file_name=file.filename or "expediente.pdf",
            user_verbal_statement=user_verbal_statement,
            explicit_province=province_jurisdiction,
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

        # 4. Consumir consulta gratuita si correspondía
        if is_free_user:
            db.increment_free_consultations(user.email)

        # 5. Guardar automáticamente en el Dashboard de causas del usuario
        case_tracker.record_case_from_final_report(
            user_email=user.email,
            report=report,
        )

        return report
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
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> EmergencyScanResponse:
    """Escáner '¡No Firmes Todavía!' para cláusulas de renuncia o resoluciones urgentes."""
    try:
        return orchestrator.run_emergency_scan(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en el escáner de urgencia: {exc}",
        ) from exc


@app.post(
    "/repository-consult",
    response_model=RepositoryConsultResponse,
    status_code=status.HTTP_200_OK,
    tags=["Consultor Normativo"],
)
async def repository_consult_endpoint(
    request: RepositoryConsultRequest,
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> RepositoryConsultResponse:
    """Consulta normativa y contraste de jurisprudencia."""
    try:
        return orchestrator.consult_normative_repository(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error consultando el repositorio: {exc}",
        ) from exc


@app.post(
    "/download-report-pdf",
    status_code=status.HTTP_200_OK,
    tags=["Entregables"],
)
async def download_report_pdf_endpoint(
    report: FinalCitizenReport,
    _user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> Response:
    """Genera y descarga en memoria RAM el dictamen en formato PDF institucional."""
    try:
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
    """Configuración de monetización y enlace de pago externo / MoR."""
    model_config = ConfigDict(extra="forbid")
    external_payment_url: str
    social_share_text: str
    app_url: str


class SocialUnlockRequest(BaseModel):
    """Notificación de evento de difusión social para desbloqueo automático."""
    model_config = ConfigDict(extra="forbid")
    network: str = Field(default="whatsapp", description="Red social donde se compartió (whatsapp, x, facebook, threads).")


class SocialUnlockResponse(BaseModel):
    """Confirmación de desbloqueo de acceso completo."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["UNLOCKED"] = "UNLOCKED"
    message: str
    user_email: str
    has_paid_pass: bool


@app.get("/payments/config", response_model=PaymentConfigResponse, tags=["Monetización Dual"])
async def payments_config_endpoint() -> PaymentConfigResponse:
    """Devuelve la URL externa de pago (Gumroad, Lemon Squeezy, Stripe) y el texto promocional para redes."""
    ext_url = orchestrator.security_config.external_payment_url
    return PaymentConfigResponse(
        external_payment_url=ext_url,
        social_share_text=(
            "Descubrí @DrHArgentina: asistente legal gratuito en lenguaje claro para revisar "
            "contratos y expedientes judiciales sin contraseñas."
        ),
        app_url="https://dr-h-argentina.onrender.com",
    )


@app.post("/payments/social-unlock", response_model=SocialUnlockResponse, tags=["Monetización Dual"])
async def payments_social_unlock_endpoint(
    req: SocialUnlockRequest,
    user: Annotated[UserProfileResponse, Depends(get_current_user)],
) -> SocialUnlockResponse:
    """Desbloquea el acceso completo (descarga de dictamen, entregables y causas) tras compartir en redes."""
    db.set_user_paid_pass(user.email, True)
    _main_logger.info("Pase desbloqueado por pago social para %s (red: %s)", user.email, req.network)
    return SocialUnlockResponse(
        status="UNLOCKED",
        message=f"¡Pase desbloqueado con éxito tras difundir en {req.network.capitalize()}! Ya podés descargar tu informe.",
        user_email=user.email,
        has_paid_pass=True,
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

@app.get("/", include_in_schema=False)
async def serve_pwa_index() -> FileResponse:
    return FileResponse(FRONTEND_PWA_DIR / "index.html", media_type="text/html")


@app.get("/index.html", include_in_schema=False)
async def serve_pwa_index_explicit() -> FileResponse:
    return FileResponse(FRONTEND_PWA_DIR / "index.html", media_type="text/html")


@app.get("/app.js", include_in_schema=False)
async def serve_pwa_app_js() -> FileResponse:
    return FileResponse(FRONTEND_PWA_DIR / "app.js", media_type="application/javascript")


@app.get("/sw.js", include_in_schema=False)
async def serve_pwa_service_worker() -> FileResponse:
    return FileResponse(
        FRONTEND_PWA_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json", include_in_schema=False)
async def serve_pwa_manifest() -> FileResponse:
    return FileResponse(FRONTEND_PWA_DIR / "manifest.json", media_type="application/manifest+json")


if FRONTEND_PWA_DIR.exists():
    app.mount("/pwa", StaticFiles(directory=str(FRONTEND_PWA_DIR), html=True), name="frontend_pwa")
