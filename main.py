"""Entrypoint FastAPI de Dr. H Argentina (Nodo Capa-País 'AR').

Expone los 5 endpoints principales conectados con Agente000Orchestrator:
1. POST /pre-quote          -> Cotizador e higiene instantánea PyMuPDF en RAM (<2s, sin gastar IA).
2. POST /analyze-expediente -> Ciclo completo Ida y Vuelta de Agente000 (Triage -> Fuero -> Auditoría -> Escudo Legal -> Drive + Wipe RAM).
3. POST /emergency-scan     -> Escáner de primera urgencia "¡No Firmes Todavía!" para cédulas, actas y acuerdos.
4. POST /repository-consult -> Consultor del repositorio normativo argentino con control de vigencia temporal.
5. POST /cron-07am          -> Ingesta matutina del Boletín Oficial (07:00 AM) y emisión de señales a Proyecto Gamuza (Geopolítica + Bolsa).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

import fitz  # PyMuPDF
from fastapi import Body, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from auditor_revision.report_builder import CitizenReportBuilder
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

app = FastAPI(
    title="Dr. H Argentina — Sistema Multi-Agente de Inteligencia y Auditoría Jurídica (Nodo AR)",
    description=(
        "Plataforma de lectura, higiene y auditoría trazable de expedientes judiciales argentinos "
        "con arquitectura Zero-Storage en RAM, Escudo Legal ciudadano y puente regulatorio hacia Proyecto Gamuza."
    ),
    version="1.4.0-AR-PWA",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = Agente000Orchestrator()
app.add_middleware(ApiKeyLeakGuardMiddleware, config=orchestrator.security_config)

FRONTEND_PWA_DIR = Path(__file__).resolve().parent / "frontend_pwa"


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


@app.get("/health", response_model=SystemHealthReport, tags=["Sistema"])
async def health_check() -> SystemHealthReport:
    """Ejecuta el Healthcheck profundo de Agente000, los 7 fueros, Auditor, RAM Zero-Storage y estado 07:00 AM."""
    return orchestrator.system_watchdog.build_health_report(orchestrator)


@app.get("/queue-status", response_model=QueueStatusSnapshot, tags=["Sistema"])
async def queue_status_endpoint() -> QueueStatusSnapshot:
    """Devuelve el estado de la cola gratuita de Google AI Studio y el contador de enfriamientos HTTP 429."""
    return orchestrator.rate_limiter.get_status_snapshot()


@app.post(
    "/download-report-pdf",
    status_code=status.HTTP_200_OK,
    tags=["2. Flujo Completo Agente000 (Ida y Vuelta)"],
)
async def download_report_pdf_endpoint(report: FinalCitizenReport) -> Response:
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
async def demo_sample_pdf_endpoint() -> Response:
    """Genera en RAM un expediente sintético multipágina (SCBA Laboral) para pruebas instantáneas en la PWA."""
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
    file: Annotated[UploadFile, File(description="Archivo PDF del expediente desde el Google Drive del usuario.")],
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
async def emergency_scan_endpoint(request: EmergencyScanRequest) -> EmergencyScanResponse:
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
async def repository_consult_endpoint(request: RepositoryConsultRequest) -> RepositoryConsultResponse:
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
# SERVIDOR ESTÁTICO DE LA INTERFAZ PWA CIUDADANA (frontend_pwa/)
# =========================================================================

@app.get("/", include_in_schema=False)
async def serve_pwa_index() -> FileResponse:
    """Sirve el panel visual principal de la PWA de Dr. H Argentina."""
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
