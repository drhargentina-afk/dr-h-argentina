"""Entrypoint FastAPI de Dr. H Argentina (Nodo Capa-País 'AR') — MODO PRIVADO EXCLUSIVO ADMINISTRADOR.

Control de acceso:
- `/docs`, `/redoc` y `/openapi.json` deshabilitados para el público; solo responden con credenciales
  de Administrador (HTTP Basic con `ADMIN_USER` / `ADMIN_PASSWORD`).
- `POST /auth/login`  -> valida `ADMIN_USER` / `ADMIN_PASSWORD` y emite un token Bearer de Administrador.
- `GET  /auth/me`     -> verifica el token vigente.
- `POST /auth/logout` -> revoca el token.
- Todos los motores exigen `Authorization: Bearer <token de Administrador>`:
  /pre-quote, /analyze-expediente, /emergency-scan, /repository-consult, /download-report-pdf,
  /demo-sample-pdf, /cron-07am y /queue-status.
- `/health` permanece abierto (sin datos de usuarios) para los chequeos de disponibilidad del hosting.

Motores conectados con `Agente000Orchestrator` (sin cambios funcionales):
1. POST /pre-quote          -> Cotizador e higiene instantánea PyMuPDF en RAM.
2. POST /analyze-expediente -> Ciclo completo Ida y Vuelta de Agente000.
3. POST /emergency-scan     -> Escáner "¡No Firmes Todavía!".
4. POST /repository-consult -> Consultor del repositorio normativo argentino.
5. POST /cron-07am          -> Ingesta matutina del Boletín Oficial y señales a Proyecto Gamuza.
"""
from __future__ import annotations

import base64
import binascii
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

import fitz  # PyMuPDF
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

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
from core.config import ApiKeyLeakGuardMiddleware
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

# docs_url / redoc_url / openapi_url en None: FastAPI no publica la documentación automáticamente.
app = FastAPI(
    title="Dr. H Argentina — Panel Privado de Administración",
    description="Plataforma de análisis jurídico argentino en modo privado exclusivo para el Administrador.",
    version="1.4.0-AR-PWA",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

orchestrator = Agente000Orchestrator()
admin_sessions = AdminSessionManager(
    admin_user=orchestrator.security_config.admin_user,
    admin_password=orchestrator.security_config.admin_password,
    ttl_hours=orchestrator.security_config.admin_session_ttl_hours,
)
app.add_middleware(ApiKeyLeakGuardMiddleware, config=orchestrator.security_config)

FRONTEND_PWA_DIR = Path(__file__).resolve().parent / "frontend_pwa"

_bearer_scheme = HTTPBearer(auto_error=False, description="Token de sesión del Administrador (POST /auth/login).")


def _client_origin(request: Request) -> str:
    """Origen del cliente para el control de fuerza bruta.

    Se usa la IP de la conexión (no `X-Forwarded-For`, que el cliente puede falsificar). Detrás de un
    proxy, ejecutar uvicorn con `--proxy-headers --forwarded-allow-ips=<IP del proxy>`.
    """
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def require_admin_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AdminSessionStatus:
    """Dependencia de candado: solo deja pasar peticiones con token Bearer válido del Administrador."""
    token = credentials.credentials if credentials is not None else None
    try:
        return admin_sessions.validate_token(token)
    except AdminAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


AdminGuard = Annotated[AdminSessionStatus, Depends(require_admin_token)]


def _require_admin_basic_auth(request: Request) -> None:
    """Exige credenciales HTTP Basic del Administrador para el panel técnico (/docs, /redoc, /openapi.json)."""
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
# PANEL TÉCNICO PRIVADO (/docs, /redoc, /openapi.json) — SOLO ADMINISTRADOR
# =========================================================================

@app.get("/openapi.json", include_in_schema=False)
async def private_openapi(request: Request) -> JSONResponse:
    """Esquema OpenAPI disponible únicamente con credenciales del Administrador."""
    _require_admin_basic_auth(request)
    return JSONResponse(get_openapi(title=app.title, version=app.version, routes=app.routes))


@app.get("/docs", include_in_schema=False)
async def private_swagger_docs(request: Request) -> HTMLResponse:
    """Swagger UI disponible únicamente con credenciales del Administrador."""
    _require_admin_basic_auth(request)
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Docs")


@app.get("/redoc", include_in_schema=False)
async def private_redoc(request: Request) -> HTMLResponse:
    """ReDoc disponible únicamente con credenciales del Administrador."""
    _require_admin_basic_auth(request)
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")


# =========================================================================
# AUTENTICACIÓN DEL ADMINISTRADOR
# =========================================================================

@app.post("/auth/login", response_model=AdminSessionResponse, tags=["Acceso Administrador"])
async def auth_login_endpoint(credentials: AdminLoginRequest, request: Request) -> AdminSessionResponse:
    """Valida ADMIN_USER / ADMIN_PASSWORD y emite un token Bearer de Administrador."""
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


@app.get("/auth/me", response_model=AdminSessionStatus, tags=["Acceso Administrador"])
async def auth_me_endpoint(session: AdminGuard) -> AdminSessionStatus:
    """Confirma que el token Bearer del Administrador sigue vigente."""
    return session


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Acceso Administrador"])
async def auth_logout_endpoint(
    _session: AdminGuard,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> Response:
    """Revoca el token Bearer del Administrador."""
    admin_sessions.revoke_token(credentials.credentials if credentials is not None else None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =========================================================================
# SISTEMA
# =========================================================================

@app.get("/health", response_model=SystemHealthReport, tags=["Sistema"])
async def health_check() -> SystemHealthReport:
    """Healthcheck de disponibilidad (abierto para el hosting; no expone datos de consultas)."""
    return orchestrator.system_watchdog.build_health_report(orchestrator)


@app.get("/queue-status", response_model=QueueStatusSnapshot, tags=["Sistema"])
async def queue_status_endpoint(_session: AdminGuard) -> QueueStatusSnapshot:
    """Estado de la cola de Google AI Studio y contador de enfriamientos HTTP 429 (solo Administrador)."""
    return orchestrator.rate_limiter.get_status_snapshot()


# =========================================================================
# MOTORES DE CONSULTA (CANDADO: TOKEN DE ADMINISTRADOR OBLIGATORIO)
# =========================================================================

@app.post(
    "/download-report-pdf",
    status_code=status.HTTP_200_OK,
    tags=["2. Flujo Completo Agente000 (Ida y Vuelta)"],
)
async def download_report_pdf_endpoint(report: FinalCitizenReport, _session: AdminGuard) -> Response:
    """Genera y descarga en memoria RAM el PDF institucional auditado sin escribir en el disco del servidor."""
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
            detail=f"Error construyendo el PDF en memoria RAM: {exc}",
        ) from exc


@app.get("/demo-sample-pdf", tags=["1. Pre-Cotizador e Higiene Zero-Storage"])
async def demo_sample_pdf_endpoint(_session: AdminGuard) -> Response:
    """Genera en RAM un expediente sintético multipágina (SCBA Laboral) para pruebas del Administrador."""
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
        doc.new_page()  # Foja 2 en blanco (descartada por higiene)
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


@app.post(
    "/pre-quote",
    response_model=PreQuoteResponse,
    status_code=status.HTTP_200_OK,
    tags=["1. Pre-Cotizador e Higiene Zero-Storage"],
)
async def pre_quote_endpoint(
    file: Annotated[UploadFile, File(description="Archivo PDF del expediente.")],
    _session: AdminGuard,
) -> PreQuoteResponse:
    """Cuenta páginas, filtra hojas basura/repetidas con PyMuPDF en RAM y cotiza el abono sin gastar tokens de IA."""
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
            detail=f"Error procesando la pre-cotización en memoria RAM: {exc}",
        ) from exc


@app.post(
    "/analyze-expediente",
    response_model=FinalCitizenReport,
    status_code=status.HTTP_200_OK,
    tags=["2. Flujo Completo Agente000 (Ida y Vuelta)"],
)
async def analyze_expediente_endpoint(
    file: Annotated[UploadFile, File(description="Archivo PDF del expediente judicial.")],
    _session: AdminGuard,
    user_verbal_statement: Annotated[
        str,
        Form(description="Relato del ciudadano: '¿Qué te dijo tu abogado o cuál es tu duda principal?'"),
    ] = "",
    province_jurisdiction: Annotated[
        ProvinceKey | None,
        Form(description="Jurisdicción procesal opcional (si se omite, Agente000 la detecta del expediente)."),
    ] = None,
    user_role: Annotated[
        UserRole | None,
        Form(description="Rol del ciudadano en el caso."),
    ] = None,
    claimed_lawyer_fee_pct: Annotated[
        str | None,
        Form(description="Porcentaje de honorarios o cuota litis informado por el ciudadano (ej. '25.00')."),
    ] = None,
    upfront_cost_requested_ars: Annotated[
        str | None,
        Form(description="Adelanto de dinero en ARS solicitado al ciudadano para tasas/gastos."),
    ] = None,
) -> FinalCitizenReport:
    """Ejecuta las Fases 1 a 4 de Agente000, guarda el informe en el Drive del usuario y borra la RAM."""
    fee_dec = _parse_optional_decimal(claimed_lawyer_fee_pct, "claimed_lawyer_fee_pct")
    upfront_dec = _parse_optional_decimal(upfront_cost_requested_ars, "upfront_cost_requested_ars")

    try:
        file_bytes = await file.read()
        return orchestrator.analyze_full_expediente(
            file_bytes=file_bytes,
            file_name=file.filename or "expediente.pdf",
            user_verbal_statement=user_verbal_statement,
            explicit_province=province_jurisdiction,
            explicit_role=user_role,
            claimed_lawyer_fee_pct=fee_dec,
            upfront_cost_requested_ars=upfront_dec,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error durante el ciclo de análisis de Agente000: {exc}",
        ) from exc


@app.post(
    "/emergency-scan",
    response_model=EmergencyScanResponse,
    status_code=status.HTTP_200_OK,
    tags=["3. Modo Urgencia '¡No Firmes Todavía!'"],
)
async def emergency_scan_endpoint(request: EmergencyScanRequest, _session: AdminGuard) -> EmergencyScanResponse:
    """Detecta al instante cláusulas abusivas, desistimientos, renuncias o plazos urgentes en un papel o cédula."""
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
    tags=["4. Consultor de Repositorios Normativos"],
)
async def repository_consult_endpoint(
    request: RepositoryConsultRequest,
    _session: AdminGuard,
) -> RepositoryConsultResponse:
    """Consulta las leyes argentinas vigentes y reglas procesales provinciales con control de vigencia temporal."""
    try:
        return orchestrator.consult_normative_repository(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error consultando el repositorio normativo: {exc}",
        ) from exc


@app.post(
    "/cron-07am",
    response_model=list[GamuzaRegulatorySignal],
    status_code=status.HTTP_200_OK,
    tags=["5. Ingesta 07:00 AM y Puente a Proyecto Gamuza (Bolsa y Acciones)"],
)
async def cron_07am_endpoint(
    _session: AdminGuard,
    norms: Annotated[
        list[BoletinOficialNormInput] | None,
        Body(description="Lista opcional de normas (si se omite, colecta el lote oficial argentino de las 07:00 AM)."),
    ] = None,
) -> list[GamuzaRegulatorySignal]:
    """Ingesta matutina del Boletín Oficial, actualización selectiva por Fuero y despacho a Proyecto Gamuza."""
    try:
        return orchestrator.ingest_07am_and_dispatch_to_gamuza(
            norms=norms,
            use_default_batch_if_none=(norms is None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en el enrutamiento matutino 07:00 AM hacia Gamuza: {exc}",
        ) from exc


# =========================================================================
# SERVIDOR ESTÁTICO DE LA INTERFAZ PWA (frontend_pwa/)
# La pantalla es pública, pero sin token de Administrador solo muestra el login
# y ningún motor responde.
# =========================================================================

@app.get("/", include_in_schema=False)
async def serve_pwa_index() -> FileResponse:
    """Sirve la pantalla principal de la PWA (login de Administrador)."""
    return FileResponse(FRONTEND_PWA_DIR / "index.html", media_type="text/html")


@app.get("/index.html", include_in_schema=False)
async def serve_pwa_index_explicit() -> FileResponse:
    """Alias explícito para el App Shell del Service Worker."""
    return FileResponse(FRONTEND_PWA_DIR / "index.html", media_type="text/html")


@app.get("/app.js", include_in_schema=False)
async def serve_pwa_app_js() -> FileResponse:
    """Sirve el cliente JavaScript asincrónico de la PWA."""
    return FileResponse(FRONTEND_PWA_DIR / "app.js", media_type="application/javascript")


@app.get("/sw.js", include_in_schema=False)
async def serve_pwa_service_worker() -> FileResponse:
    """Sirve el Service Worker en la raíz del dominio con alcance global."""
    return FileResponse(
        FRONTEND_PWA_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json", include_in_schema=False)
async def serve_pwa_manifest() -> FileResponse:
    """Sirve el Web App Manifest institucional de Dr. H Argentina."""
    return FileResponse(FRONTEND_PWA_DIR / "manifest.json", media_type="application/manifest+json")


if FRONTEND_PWA_DIR.exists():
    app.mount("/pwa", StaticFiles(directory=str(FRONTEND_PWA_DIR), html=True), name="frontend_pwa")
