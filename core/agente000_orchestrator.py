"""Cerebro Central de Ida y Vuelta de Dr. H Argentina: Agente000Orchestrator.

Gobierna las 5 fases secuenciales del sistema:
- Fase 1: Recepción desde Zero-Storage RAM, Higiene PyMuPDF, Anonimización PII en RAM
          y Triage Exacto hacia una de las 7 áreas judiciales argentinas (`01_penal` a `07_contencioso_tributario`),
          detectando provincia y rol procesal del ciudadano.
- Fase 2: Primera Derivación (Ida y Vuelta) hacia el Sub-Agente del Fuero correspondiente,
          recibiendo el encuadre normativo, citas verificables a fojas (`foja_ref`), plazos provinciales
          y cálculo preliminar con `Decimal` exacto.
- Fase 3: Segunda Derivación (Ida y Vuelta) hacia el Agente de Revisión y Auditoría (`auditor_revision`),
          cruzando el dictamen del Fuero con el relato verbal del ciudadano ("qué me dijo mi abogado")
          para auditar tiempos muertos, riesgo de caducidad de instancia, contradicciones documentales,
          violaciones al beneficio de gratuidad y banderas críticas "¡No Firmes Todavía!".
- Fase 4: Validación Determinista Anti-Alucinación (verifica que cada `foja_ref` y ley citada existan),
          formateo con Escudo Legal, guion de audio de 90 segundos, mensaje listo para WhatsApp,
          des-anonimización en RAM, guardado en el Google Drive del usuario y purga total de memoria.
- Fase 5: Enrutamiento Matutino 07:00 AM del Boletín Oficial (BORA / InfoLEG / CSJN / BCRA / CNV)
          actualizando únicamente el repositorio del Sub-Agente de Fuero afectado y despachando el
          payload `GamuzaRegulatorySignal` hacia Proyecto Gamuza (Geopolítica + Bolsa y Acciones).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Protocol

from core.models import (
    AuditReviewResult,
    BoletinOficialNormInput,
    CaseContext,
    ContradictionFinding,
    EmergencyScanRequest,
    EmergencyScanResponse,
    FinalCitizenReport,
    FojaCitation,
    FojaPageContent,
    FueroAnalysisResult,
    FueroKey,
    GamuzaMarketImpact,
    GamuzaRegulatorySignal,
    PreliminaryMoneyCalculation,
    PreQuoteResponse,
    ProvinceKey,
    RepositoryConsultRequest,
    RepositoryConsultResponse,
    SubscriptionTier,
    UserRole,
)
from core.privacy_anonymizer import PrivacyAnonymizer
from core.zero_storage_drive import ZeroStorageDriveManager


class FueroAgentHandler(Protocol):
    """Contrato para cualquier Sub-Agente de Fuero inyectable (Paso 2)."""

    def analyze_case(self, context: CaseContext, province_rules: dict[str, object]) -> FueroAnalysisResult:
        ...


class AuditorRevisionHandler(Protocol):
    """Contrato para el Agente de Revisión y Auditoría inyectable (Paso 3)."""

    def audit_case(
        self,
        context: CaseContext,
        fuero_result: FueroAnalysisResult,
        province_rules: dict[str, object],
        gratuity_rules: dict[str, object],
    ) -> AuditReviewResult:
        ...


class Agente000Orchestrator:
    """Orquestador central supervisado de Dr. H Argentina (Nodo AR)."""

    # Palabras clave deterministas para el Triage Exacto de las 7 Áreas Judiciales
    _FUERO_TRIAGE_KEYWORDS: dict[FueroKey, tuple[str, ...]] = {
        FueroKey.PENAL: (
            "imputado",
            "fiscalia",
            "fiscalía",
            "querella",
            "sobreseimiento",
            "prision preventiva",
            "prisión preventiva",
            "excarcelacion",
            "excarcelación",
            "codigo penal",
            "código penal",
            "delito",
            "instruccion penal",
            "instrucción penal",
            "ipp",
            "denuncia penal",
            "defensor oficial penal",
            "allanamiento",
            "indagatoria",
        ),
        FueroKey.FAMILIA: (
            "alimentos",
            "cuota alimentaria",
            "cuidado personal",
            "regimen de comunicacion",
            "régimen de comunicación",
            "divorcio",
            "violencia familiar",
            "filiacion",
            "filiación",
            "sucesion",
            "sucesión",
            "declaratoria de herederos",
            "acervo hereditario",
            "compensacion economica",
            "compensación económica",
        ),
        FueroKey.LABORAL: (
            "despido",
            "ley 20.744",
            "ley 20744",
            "lct",
            "indemnizacion art. 245",
            "indemnización art. 245",
            "telegrama colacionado",
            "tcl",
            "seclo",
            "trabajo no registrado",
            "art 245",
            "accidente de trabajo",
            "ley 24.557",
            "comision medica",
            "comisión médica",
            "remuneracion",
            "remuneración",
            "empleador",
        ),
        FueroKey.CIVIL: (
            "daños y perjuicios",
            "danos y perjuicios",
            "accidente de transito",
            "accidente de tránsito",
            "desalojo",
            "locacion",
            "locación",
            "escrituracion",
            "escrituración",
            "usucapion",
            "usucapión",
            "incumplimiento contractual",
            "mala praxis",
            "consorcio",
            "propiedad horizontal",
            "hipoteca",
        ),
        FueroKey.COMERCIAL_CONSUMIDOR: (
            "defensa del consumidor",
            "ley 24.240",
            "ley 24240",
            "coprec",
            "concurso preventivo",
            "quiebra",
            "pagare",
            "pagaré",
            "cheque",
            "ejecucion prendaria",
            "ejecución prendaria",
            "tarjeta de credito",
            "tarjeta de crédito",
            "plan de ahorro",
            "estafa bancaria",
            "phishing",
            "sociedad anonima",
            "s.r.l.",
        ),
        FueroKey.SEGURIDAD_SOCIAL_SALUD: (
            "anses",
            "jubilacion",
            "jubilación",
            "reajuste de haberes",
            "pension",
            "pensión",
            "amparo de salud",
            "obra social",
            "prepaga",
            "pami",
            "ioma",
            "certificado unico de discapacidad",
            "cud",
            "cobertura medica",
            "cobertura médica",
            "medicacion oncologica",
        ),
        FueroKey.CONTENCIOSO_TRIBUTARIO: (
            "arca",
            "afip",
            "arba",
            "agip",
            "ejecucion fiscal",
            "ejecución fiscal",
            "boleta de deuda",
            "acto administrativo",
            "empleo publico",
            "empleo público",
            "licitacion",
            "licitación",
            "multa municipal",
            "tribunal fiscal",
            "aduana",
            "impuesto a las ganancias",
            "ingresos brutos",
        ),
    }

    # Base normativa inicial por Fuero para validación y consultas al repositorio (Etapas 2, 3, 4 y 7)
    _BASE_NORMATIVE_REPOSITORIES: dict[FueroKey, list[str]] = {
        FueroKey.PENAL: [
            "Constitución Nacional Art. 18 (Garantías del Debido Proceso, Defensa en Juicio y Principio de Inocencia)",
            "Código Penal de la Nación Argentina (Ley 11.179 t.o.)",
            "Código Procesal Penal Federal (Ley 27.063 t.o. 2019) / CPPN (Ley 23.984)",
        ],
        FueroKey.FAMILIA: [
            "Código Civil y Comercial de la Nación (Ley 26.994) - Libro Segundo (Relaciones de Familia, Arts. 401 a 723)",
            "Código Civil y Comercial de la Nación - Libro Quinto (Transmisión de Derechos por Causa de Muerte / Sucesiones, Arts. 2277 a 2531)",
            "Ley 26.061 de Protección Integral de los Derechos de las Niñas, Niños y Adolescentes",
            "Ley 26.485 de Protección Integral contra la Violencia hacia las Mujeres",
        ],
        FueroKey.LABORAL: [
            "Constitución Nacional Art. 14 bis (Protección del Trabajo)",
            "Ley de Contrato de Trabajo N° 20.744 (Art. 20 Beneficio de Gratuidad, Art. 245 Indemnización por Antigüedad)",
            "Ley de Riesgos del Trabajo N° 24.557 y Ley 27.348 (Comisiones Médicas / ART)",
            "Ley 18.345 de Organización y Procedimiento de la Justicia Nacional del Trabajo (Impulso de Oficio Art. 46)",
        ],
        FueroKey.CIVIL: [
            "Código Civil y Comercial de la Nación (Ley 26.994) - Responsabilidad Civil (Arts. 1708 a 1780) y Contratos/Locaciones (Arts. 1187 a 1226)",
            "Código Procesal Civil y Comercial de la Nación (Ley 17.454 t.o.) y Códigos Procesales Provinciales",
            "Ley Nacional de Tránsito N° 24.449",
        ],
        FueroKey.COMERCIAL_CONSUMIDOR: [
            "Constitución Nacional Art. 42 (Derechos de Consumidores y Usuarios)",
            "Ley de Defensa del Consumidor N° 24.240 (Art. 53 Beneficio de Justicia Gratuita, Art. 52 bis Daño Punitivo)",
            "Ley General de Sociedades N° 19.550 y Ley de Concursos y Quiebras N° 24.522",
            "Decreto-Ley 5965/63 (Letra de Cambio y Pagaré) y Ley de Cheques N° 24.452",
        ],
        FueroKey.SEGURIDAD_SOCIAL_SALUD: [
            "Ley 24.241 (Sistema Integrado de Jubilaciones y Pensiones) y Ley 24.463 (Solidaridad Previsional)",
            "Ley 16.986 de Acción de Amparo y Art. 43 de la Constitución Nacional (Amparo de Salud)",
            "Ley 23.660 (Obras Sociales), Ley 26.682 (Medicina Prepaga) y Ley 24.901 (Sistema de Prestaciones Básicas en Discapacidad)",
        ],
        FueroKey.CONTENCIOSO_TRIBUTARIO: [
            "Ley Nacional de Procedimientos Administrativos N° 19.549 y Decreto Reglamentario 1759/72",
            "Ley de Procedimiento Tributario N° 11.683 (t.o.) - Régimen ARCA (ex AFIP)",
            "Código Aduanero (Ley 22.415) y Códigos Fiscales Provinciales (ARBA / AGIP / Rentas)",
        ],
    }

    # Patrones críticos que disparan la bandera "¡No Firmes Todavía!"
    _DO_NOT_SIGN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(r"nada\s+m[aá]s\s+tengo\s+que\s+reclamar|renuncio\s+a\s+todo\s+reclamo", re.IGNORECASE),
            "Cláusula de renuncia amplia ('nada más que reclamar') que intenta bloquear futuros reclamos judiciales.",
        ),
        (
            re.compile(r"desistimiento\s+de\s+la\s+acci[oó]n\s+y\s+del\s+derecho", re.IGNORECASE),
            "Desistimiento de la acción y del derecho: extingue definitivamente tu reclamo y no podrás volver a iniciar el juicio.",
        ),
        (
            re.compile(r"renuncia\s+al\s+empleo|art\.?\s*240\s+lct", re.IGNORECASE),
            "Se menciona renuncia laboral (Art. 240 LCT), la cual hace perder la indemnización por antigüedad del Art. 245 LCT.",
        ),
        (
            re.compile(r"recib[ií]\s+conforme\s+el\s+total|otorgo\s+carta\s+de\s+pago\s+total", re.IGNORECASE),
            "Recibo de pago cancelatorio total: nunca debe firmarse antes de que el dinero esté efectivamente acreditado en tu cuenta bancaria (CBU).",
        ),
        (
            re.compile(r"pacto\s+de\s+cuota\s+litis.*?(?:3[0-9]|4[0-9]|5[0-9])\s*%", re.IGNORECASE),
            "Pacto de honorarios/cuota litis superior al tope legal del 20% previsto para causas laborales o de consumo.",
        ),
        (
            re.compile(r"cesi[oó]n\s+de\s+derechos\s+hereditarios|cesi[oó]n\s+de\s+acciones\s+y\s+derechos\s+litigiosos", re.IGNORECASE),
            "Cesión de derechos hereditarios o litigiosos: transfiere la titularidad de tu parte del juicio o herencia a un tercero.",
        ),
    )

    _RE_DATE_EXTRACTOR = re.compile(
        r"\b(?:(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})|(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2}))\b"
    )
    _RE_AMOUNT_ARS = re.compile(r"\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{4,12}(?:\.\d{1,2})?)")

    def __init__(
        self,
        drive_manager: ZeroStorageDriveManager | None = None,
        anonymizer: PrivacyAnonymizer | None = None,
        provinces_config_path: str | Path | None = None,
    ) -> None:
        self.drive_manager = drive_manager or ZeroStorageDriveManager()
        self.anonymizer = anonymizer or PrivacyAnonymizer()
        config_path = (
            Path(provinces_config_path)
            if provinces_config_path
            else Path(__file__).resolve().parent / "provinces_config.json"
        )
        if not config_path.exists():
            raise FileNotFoundError(f"No se encontró el archivo de configuración procesal: {config_path}")
        raw_cfg = json.loads(config_path.read_text(encoding="utf-8"))
        self.provinces_config: dict[str, object] = raw_cfg

        # Registro de handlers modulares (conecta automáticamente los 7 Sub-Agentes de Fuero del Paso 2)
        self._fuero_handlers: dict[FueroKey, FueroAgentHandler] = {}
        self._auditor_handler: AuditorRevisionHandler | None = None
        # Historial en memoria de actualizaciones matutinas 07:00 AM por fuero
        self._daily_norm_updates: dict[FueroKey, list[BoletinOficialNormInput]] = {k: [] for k in FueroKey}
        self._emitted_gamuza_signals: list[GamuzaRegulatorySignal] = []

        from agents_fueros.router_fueros import FuerosRouter
        from auditor_revision.lawyer_watchdog import LawyerWatchdogAuditor
        from core.config import ServerSecurityConfig
        from core.rate_limiter_queue import RateLimitQueueHandler
        from ingestion_07am.system_watchdog import SystemHealthWatchdog

        self.security_config = ServerSecurityConfig.load()
        self.rate_limiter = RateLimitQueueHandler(
            max_rpm=self.security_config.rate_limit_free_rpm,
            max_retries=self.security_config.rate_limit_max_retries,
            base_cooling_seconds=self.security_config.rate_limit_base_cooling_seconds,
        )

        self.fueros_router = FuerosRouter()
        for fuero_key, fuero_agent in self.fueros_router.all_agents().items():
            self.register_fuero_agent(fuero_key, fuero_agent)

        self.watchdog_auditor = LawyerWatchdogAuditor()
        self.register_auditor_agent(self.watchdog_auditor)
        self.system_watchdog = SystemHealthWatchdog()

    def register_fuero_agent(self, fuero: FueroKey, handler: FueroAgentHandler) -> None:
        """Permite registrar un Sub-Agente especializado de Fuero (Paso 2)."""
        self._fuero_handlers[fuero] = handler

    def register_auditor_agent(self, handler: AuditorRevisionHandler) -> None:
        """Permite registrar el Agente de Revisión y Auditoría Procesal (Paso 3)."""
        self._auditor_handler = handler

    # =========================================================================
    # FASE 1: RECEPCIÓN ZERO-STORAGE, HIGIENE Y TRIAGE EXACTO
    # =========================================================================

    def pre_quote_document(self, file_bytes: bytes, file_name: str) -> PreQuoteResponse:
        """Ejecuta el Pre-Cotizador instantáneo en RAM y libera el buffer inmediatamente."""
        volatile_session = self.drive_manager.load_and_prequote_in_ram(
            file_bytes=file_bytes,
            file_name=file_name,
        )
        try:
            return volatile_session.pre_quote
        finally:
            self.drive_manager._volatile_sessions.pop(volatile_session.session_id, None)
            volatile_session.purge_ram()

    def classify_fuero_and_jurisdiction(
        self,
        text_sample: str,
        explicit_province: ProvinceKey | None = None,
        explicit_role: UserRole | None = None,
    ) -> tuple[FueroKey, ProvinceKey, UserRole]:
        """Determina con exactitud a cuál de las 7 áreas judiciales pertenece el texto, su provincia y rol."""
        lower_text = text_sample.lower()

        # 1. Clasificación de Fuero por puntaje ponderado
        scores: dict[FueroKey, int] = {fuero: 0 for fuero in FueroKey}
        for fuero, keywords in self._FUERO_TRIAGE_KEYWORDS.items():
            for kw in keywords:
                occurrences = lower_text.count(kw)
                if occurrences > 0:
                    scores[fuero] += occurrences * (3 if len(kw) > 8 else 2)

        best_fuero = max(scores, key=lambda k: scores[k])
        if scores[best_fuero] == 0:
            best_fuero = FueroKey.CIVIL

        # 2. Detección de Jurisdicción Provincial
        if explicit_province is not None:
            detected_prov = explicit_province
        elif any(x in lower_text for x in ("scba", "mev", "provincia de buenos aires", "la plata", "lomas de zamora", "san isidro", "morón", "moron", "quilmes")):
            detected_prov = ProvinceKey.PBA_MEV_SCBA
        elif any(x in lower_text for x in ("córdoba", "cordoba", "sac multifuero", "tsj córdoba")):
            detected_prov = ProvinceKey.CORDOBA
        elif any(x in lower_text for x in ("santa fe", "rosario", "sisfe")):
            detected_prov = ProvinceKey.SANTA_FE
        elif any(x in lower_text for x in ("mendoza", "iurix", "ley 9001", "ley 9.001")):
            detected_prov = ProvinceKey.MENDOZA
        elif any(x in lower_text for x in ("pjn", "lex100", "lex 100", "justicia nacional", "capital federal", "caba")):
            detected_prov = ProvinceKey.PJN_NACION_CABA
        else:
            detected_prov = ProvinceKey.PJN_NACION_CABA

        # 3. Detección de Rol del Usuario
        if explicit_role is not None:
            detected_role = explicit_role
        elif any(x in lower_text for x in ("me notificaron", "me llegó una cédula", "me intimaron", "imputado", "demandado")):
            detected_role = UserRole.DEMANDADO_IMPUTADO
        elif any(x in lower_text for x in ("firmar ahora", "urgente", "escribanía", "escribania")):
            detected_role = UserRole.CONSULTA_URGENTE
        else:
            detected_role = UserRole.ACTOR_RECLAMANTE

        return best_fuero, detected_prov, detected_role

    def _extract_caratula_from_fojas(self, fojas: list[FojaPageContent]) -> str:
        """Extrae la carátula de las primeras fojas útiles o construye un encabezado descriptivo."""
        caratula_pattern = re.compile(
            r"([A-ZÁÉÍÓÚÑ0-9\[\]_,\.\s]{3,60}\s+(?:C/|c/|contra|CONTRA)\s+[A-ZÁÉÍÓÚÑ0-9\[\]_,\.\s]{3,60}\s+(?:S/|s/|sobre|SOBRE)\s+[^\n]{3,60})"
        )
        for foja in fojas[:5]:
            if foja.is_discarded_hygiene:
                continue
            match = caratula_pattern.search(foja.cleaned_text)
            if match:
                return match.group(1).strip()
        for foja in fojas:
            if not foja.is_discarded_hygiene and foja.cleaned_text:
                first_line = foja.cleaned_text.splitlines()[0][:120]
                return first_line
        return "EXPEDIENTE JUDICIAL EN ANÁLISIS DOCUMENTAL"

    # =========================================================================
    # FASE 2: PRIMERA DERIVACIÓN -> SUB-AGENTE DE FUERO (IDA Y VUELTA)
    # =========================================================================

    def _parse_dates_in_text(self, text: str) -> list[date]:
        """Extrae fechas reales válidas contenidas en el texto de una foja."""
        found: list[date] = []
        for match in self._RE_DATE_EXTRACTOR.finditer(text):
            try:
                if match.group(1):
                    d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
                else:
                    y, m, d = int(match.group(4)), int(match.group(5)), int(match.group(6))
                parsed = date(y, m, d)
                if 1990 <= parsed.year <= 2030:
                    found.append(parsed)
            except ValueError:
                continue
        return found

    def _extract_max_amount_ars(self, text: str) -> Decimal | None:
        """Extrae el mayor monto monetario documentado en el texto con precisión Decimal."""
        amounts: list[Decimal] = []
        for match in self._RE_AMOUNT_ARS.finditer(text):
            raw = match.group(1).strip()
            if "," in raw and "." in raw:
                normalized = raw.replace(".", "").replace(",", ".")
            elif "." in raw and len(raw.split(".")[-1]) == 3:
                normalized = raw.replace(".", "")
            else:
                normalized = raw.replace(",", ".")
            try:
                val = Decimal(normalized).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if val > Decimal("0.00"):
                    amounts.append(val)
            except Exception:
                continue
        return max(amounts) if amounts else None

    @staticmethod
    def _classify_piece_type(text: str) -> tuple[str, str]:
        """Clasifica el tipo de pieza procesal y genera su traducción ciudadana ('En criollo')."""
        low = text.lower()
        if any(w in low for w in ("sentencia definitiva", "fallo:", "resuelvo:")):
            return (
                "Sentencia / Resolución Judicial",
                "El juez dictó una decisión formal resolviendo un pedido o el fondo del reclamo.",
            )
        if any(w in low for w in ("cédula", "cedula", "notifico a ud", "queda ud. debidamente notificado")):
            return (
                "Cédula de Notificación",
                "Aviso oficial del juzgado comunicando una decisión a las partes; a partir de su recepción corren plazos en días hábiles.",
            )
        if any(w in low for w in ("pericia", "experto", "dictamen pericial", "incapacidad")):
            return (
                "Informe Pericial",
                "Dictamen técnico de un perito oficial (médico, contador, ingeniero o calígrafo) evaluando las pruebas del caso.",
            )
        if any(w in low for w in ("contesta demanda", "opone excepcion", "niega cada uno")):
            return (
                "Contestación de Demanda",
                "Presentación donde la parte demandada responde al reclamo, da su versión y ofrece sus pruebas.",
            )
        if any(w in low for w in ("promueve demanda", "inicia demanda", "objeto:", "hechos:")):
            return (
                "Escrito de Demanda / Inicio",
                "Presentación inicial donde se explica el reclamo al juez, los hechos ocurridos y lo que se solicita cobrar o resolver.",
            )
        if any(w in low for w in ("téngase presente", "tengase presente", "agréguense", "autos para", "traslado")):
            return (
                "Proveído de Trámite del Juzgado",
                "Orden interna del juzgado que hace avanzar el expediente (acepta un escrito o pide notificar a la otra parte).",
            )
        return (
            "Actuación / Escrito Judicial",
            "Constancia documental incorporada al expediente que registra una presentación o trámite de la causa.",
        )

    def _run_fuero_derivation(
        self,
        context: CaseContext,
        province_rules: dict[str, object],
    ) -> FueroAnalysisResult:
        """Ejecuta la Primera Derivación hacia el Sub-Agente del Fuero y devuelve FueroAnalysisResult."""
        if context.detected_fuero in self._fuero_handlers:
            res = self._fuero_handlers[context.detected_fuero].analyze_case(context, province_rules)
            for norm_update in self._daily_norm_updates.get(context.detected_fuero, []):
                res.applied_current_laws.append(
                    f"{norm_update.norm_id} ({norm_update.source_organism}, vigente desde {norm_update.effective_from.isoformat()}): {norm_update.title}"
                )
            return res

        # Motor determinista integrado de Fuero (100% funcional desde el Paso 1)
        laws = list(self._BASE_NORMATIVE_REPOSITORIES.get(context.detected_fuero, []))
        for norm_update in self._daily_norm_updates.get(context.detected_fuero, []):
            laws.append(
                f"{norm_update.norm_id} ({norm_update.source_organism}, vigente desde {norm_update.effective_from.isoformat()}): {norm_update.title}"
            )

        useful_fojas = [f for f in context.fojas_content if not f.is_discarded_hygiene]
        if not useful_fojas and context.fojas_content:
            useful_fojas = [context.fojas_content[0]]

        citations: list[FojaCitation] = []
        detected_dates_map: dict[int, str] = {}
        all_dates: list[date] = []
        max_documented_amount: Decimal | None = None

        for foja in useful_fojas:
            piece_type, plain_translation = self._classify_piece_type(foja.cleaned_text)
            excerpt = foja.cleaned_text[:240].strip()
            if len( excerpt ) < 5:
                excerpt = f"Constancia documental registrada en foja {foja.foja_number}."

            foja_dates = self._parse_dates_in_text(foja.cleaned_text)
            if foja_dates:
                latest_in_foja = max(foja_dates)
                detected_dates_map[foja.foja_number] = latest_in_foja.isoformat()
                all_dates.extend(foja_dates)

            amount_in_foja = self._extract_max_amount_ars(foja.cleaned_text)
            if amount_in_foja is not None:
                if max_documented_amount is None or amount_in_foja > max_documented_amount:
                    max_documented_amount = amount_in_foja

            primary_law = laws[0] if laws else "Código Procesal aplicable"
            citations.append(
                FojaCitation(
                    foja_ref=foja.foja_number,
                    document_piece_type=piece_type,
                    verbatim_excerpt=excerpt,
                    citizen_plain_translation=plain_translation,
                    normative_ref=primary_law,
                )
            )

        last_act_date = max(all_dates) if all_dates else None
        prelim_calc: PreliminaryMoneyCalculation | None = None
        if max_documented_amount is not None:
            # Cálculo determinista con Decimal: reserva para intereses moratorios/actualización orientativa (15%)
            estimated_with_acc = (max_documented_amount * Decimal("1.15")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            prelim_calc = PreliminaryMoneyCalculation(
                concept_description=f"Capital base documentado en expediente ({context.detected_fuero.value})",
                base_amount_ars=max_documented_amount,
                estimated_total_ars=estimated_with_acc,
                calculation_formula_applied=(
                    "Capital Nominal Documentado * 1.15 (proyección orientativa base antes de tasa activa judicial)"
                ),
            )

        prov_name = str(province_rules.get("name", context.province_jurisdiction.value))
        elec_sys = str(province_rules.get("electronic_system", "Sistema de Gestión Judicial"))
        nota_rule = str(province_rules.get("nota_rule", "Notificación automática martes y viernes."))
        grace_rule = str(province_rules.get("grace_period_hours", "Dos primeras horas hábiles del día siguiente."))

        deadlines_summary = (
            f"Jurisdicción: {prov_name} ({elec_sys}). {nota_rule} Plazo de gracia: {grace_rule}"
        )

        return FueroAnalysisResult(
            fuero_agent_id=context.detected_fuero,
            normative_framework=[
                "Constitución de la Nación Argentina y Tratados con Jerarquía Constitucional (Art. 75 inc. 22)",
                str(province_rules.get("procedural_code", "Código Procesal aplicable")),
            ],
            applied_current_laws=laws,
            foja_citations=citations,
            procedural_deadlines_summary=deadlines_summary,
            last_documented_act_date=last_act_date,
            detected_dates_by_foja=detected_dates_map,
            preliminary_calculation=prelim_calc,
            technical_diagnosis=(
                f"Expediente clasificado en {context.detected_fuero.value} bajo jurisdicción {prov_name}. "
                f"Se verificaron {len(citations)} fojas útiles con trazabilidad directa."
            ),
        )

    # =========================================================================
    # FASE 3: SEGUNDA DERIVACIÓN -> AUDITORÍA Y REVISIÓN PROCESAL (IDA Y VUELTA)
    # =========================================================================

    def _run_auditor_derivation(
        self,
        context: CaseContext,
        fuero_result: FueroAnalysisResult,
        province_rules: dict[str, object],
        gratuity_rules: dict[str, object],
        reference_today: date | None = None,
    ) -> AuditReviewResult:
        """Ejecuta la Segunda Derivación hacia el Agente de Revisión para auditar tiempos, costos y riesgos."""
        if self._auditor_handler is not None:
            return self._auditor_handler.audit_case(
                context=context,
                fuero_result=fuero_result,
                province_rules=province_rules,
                gratuity_rules=gratuity_rules,
            )

        today = reference_today or datetime.now(timezone.utc).date()

        # 1. Cálculo de días desde el último acto impulsorio y tiempos muertos entre fojas
        if fuero_result.last_documented_act_date is not None:
            days_inactive = max(0, (today - fuero_result.last_documented_act_date).days)
        else:
            days_inactive = 0

        dead_periods: list[str] = []
        sorted_foja_dates = sorted(
            ((f_num, date.fromisoformat(d_str)) for f_num, d_str in fuero_result.detected_dates_by_foja.items()),
            key=lambda x: x[1],
        )
        for idx in range(1, len(sorted_foja_dates)):
            prev_foja, prev_dt = sorted_foja_dates[idx - 1]
            curr_foja, curr_dt = sorted_foja_dates[idx]
            gap_days = (curr_dt - prev_dt).days
            if gap_days > 45:
                dead_periods.append(
                    f"Inactividad documentada de {gap_days} días corridos entre Foja {prev_foja} ({prev_dt.isoformat()}) "
                    f"y Foja {curr_foja} ({curr_dt.isoformat()})."
                )

        # 2. Evaluación de riesgo de Caducidad de Instancia según Provincia y Fuero
        caducidad_map = province_rules.get("caducidad_instancia_months", {})
        caducidad_months: int | None = None
        if isinstance(caducidad_map, dict):
            raw_m = caducidad_map.get(context.detected_fuero.value)
            if isinstance(raw_m, int):
                caducidad_months = raw_m

        caducidad_notes = str(province_rules.get("caducidad_notes", ""))
        if caducidad_months is None:
            risk_level: str = "NO_APLICA_IMPULSO_OFICIO"
            cad_explanation = (
                f"En {context.detected_fuero.value} bajo {context.province_jurisdiction.value} rige impulso de oficio "
                f"o no opera caducidad civil ordinaria ({caducidad_notes}). Días desde la última fecha leída: {days_inactive}."
            )
        else:
            threshold_days = caducidad_months * 30
            if days_inactive >= int(threshold_days * 0.75):
                risk_level = "ALTO_CRITICO"
            elif days_inactive >= int(threshold_days * 0.40):
                risk_level = "MODERADO"
            else:
                risk_level = "BAJO"
            cad_explanation = (
                f"El plazo legal de caducidad de instancia en {context.province_jurisdiction.value} para este fuero es de "
                f"{caducidad_months} meses (~{threshold_days} días). Transcurrieron {days_inactive} días corridos desde "
                f"la última fecha documentada en las fojas acompañadas. {caducidad_notes}"
            )

        # 3. Cruce de contradicciones entre el relato verbal ("qué me dijo mi abogado") y las fojas reales
        contradictions: list[ContradictionFinding] = []
        combined_fojas_text = "\n".join(f.cleaned_text for f in context.fojas_content if not f.is_discarded_hygiene)
        verbal_low = context.user_verbal_statement.lower()

        if verbal_low:
            if (
                any(phrase in verbal_low for phrase in ("para sentencia", "listo para sentencia", "esperando sentencia"))
                and "autos para sentencia" not in combined_fojas_text.lower()
            ):
                first_ref = fuero_result.foja_citations[-1].foja_ref
                contradictions.append(
                    ContradictionFinding(
                        foja_a=first_ref,
                        foja_b=None,
                        contradiction_type="RELATO_ABOGADO_VS_EXPEDIENTE",
                        objective_description=(
                            f"Mencionaste que te informaron que la causa está 'para sentencia', pero hasta la Foja {first_ref} "
                            f"del archivo subido no consta el llamado oficial de 'Autos para Sentencia'."
                        ),
                    )
                )
            if (
                any(phrase in verbal_low for phrase in ("ya presentamos", "presentó apelación", "apelo", "apeló"))
                and "apela" not in combined_fojas_text.lower()
                and "recurso de apelaci" not in combined_fojas_text.lower()
            ):
                first_ref = fuero_result.foja_citations[-1].foja_ref
                contradictions.append(
                    ContradictionFinding(
                        foja_a=first_ref,
                        foja_b=None,
                        contradiction_type="RELATO_ABOGADO_VS_EXPEDIENTE",
                        objective_description=(
                            f"Indicaste que se habría presentado una apelación, pero en las fojas aportadas (hasta Foja {first_ref}) "
                            f"no figura glosado el escrito de apelación ni su concesión."
                        ),
                    )
                )

        # 4. Auditoría de Beneficio de Gratuidad y Costos Ilegales
        gratuity_alert: str | None = None
        fuero_gratuity = gratuity_rules.get(context.detected_fuero.value)
        if isinstance(fuero_gratuity, dict) and fuero_gratuity.get("applies") is True:
            law_ref = str(fuero_gratuity.get("law_reference", ""))
            rule_desc = str(fuero_gratuity.get("citizen_protection_rule", ""))
            max_pct = Decimal(str(fuero_gratuity.get("max_quota_litis_pct", "20.00")))

            if context.upfront_cost_requested_ars is not None and context.upfront_cost_requested_ars > Decimal("0.00"):
                gratuity_alert = (
                    f"ALERTA DE GRATUIDAD ({law_ref}): Informaste que te solicitaron un adelanto de "
                    f"${context.upfront_cost_requested_ars} ARS. {rule_desc} Pedí siempre recibo oficial detallado."
                )
            elif context.claimed_lawyer_fee_pct is not None and context.claimed_lawyer_fee_pct > max_pct:
                gratuity_alert = (
                    f"ALERTA DE TOPE DE HONORARIOS ({law_ref}): El porcentaje mencionado ({context.claimed_lawyer_fee_pct}%) "
                    f"supera el tope de referencia del {max_pct}% para pactos de cuota litis en esta materia."
                )
            elif any(w in verbal_low for w in ("me pide plata para tasa", "pagar tasa de justicia", "adelanto de gastos")):
                gratuity_alert = f"RECORDATORIO DE GRATUIDAD LEGAL ({law_ref}): {rule_desc}"

        # 5. Escáner de Bandera Crítica "¡No Firmes Todavía!"
        do_not_sign = False
        do_not_sign_reasons: list[str] = []
        full_audit_corpus = f"{combined_fojas_text}\n{context.user_verbal_statement}"
        for pattern, warning_msg in self._DO_NOT_SIGN_PATTERNS:
            if pattern.search(full_audit_corpus):
                do_not_sign = True
                do_not_sign_reasons.append(warning_msg)

        # 6. Construcción de preguntas objetivas para el abogado con referencias a foja
        last_foja_num = fuero_result.foja_citations[-1].foja_ref
        questions: list[str] = [
            f"1. Según las constancias que tengo hasta la Foja {last_foja_num}, ¿cuál es el próximo paso procesal concreto para impulsar la causa?",
            f"2. Teniendo en cuenta el sistema {province_rules.get('electronic_system', 'judicial')} y los días de nota (martes y viernes), ¿quedó alguna cédula o traslado pendiente de contestar?",
        ]
        if days_inactive > 30:
            questions.append(
                f"3. Observo que la última actuación fechada en mi copia data de hace {days_inactive} días corridos: ¿hubo presentaciones posteriores en el portal electrónico que aún no estén en este PDF?"
            )
        if gratuity_alert:
            questions.append(
                "4. Dado que en esta materia rige el beneficio legal de gratuidad, ¿a qué concepto arancelario o pericial corresponde exactamente el gasto mencionado?"
            )
        if do_not_sign:
            questions.append(
                "5. Antes de suscribir el documento propuesto, ¿podemos revisar juntos el alcance de las cláusulas de renuncia o desistimiento señaladas?"
            )

        return AuditReviewResult(
            days_since_last_impulse=days_inactive,
            dead_time_periods_detected=dead_periods,
            caducidad_risk_level=risk_level,  # type: ignore[arg-type]
            caducidad_explanation=cad_explanation,
            contradictions_detected=contradictions,
            gratuity_illegal_cost_alert=gratuity_alert,
            do_not_sign_yet_banner=do_not_sign,
            do_not_sign_reason=" | ".join(do_not_sign_reasons) if do_not_sign_reasons else None,
            suggested_questions_for_lawyer=questions,
        )

    # =========================================================================
    # FASE 4: VALIDACIÓN ANTI-ALUCINACIÓN, ESCUDO LEGAL Y GUARDADO EN DRIVE
    # =========================================================================

    def _validate_anti_hallucination(
        self,
        fuero_result: FueroAnalysisResult,
        valid_foja_numbers: set[int],
    ) -> list[FojaCitation]:
        """Verifica determinísticamente que cada foja citada exista en el documento real."""
        if not valid_foja_numbers:
            raise ValueError("No existen fojas válidas en la sesión para verificar trazabilidad.")

        verified: list[FojaCitation] = []
        for citation in fuero_result.foja_citations:
            if citation.foja_ref not in valid_foja_numbers:
                raise ValueError(
                    f"Bloqueo Anti-Alucinación de Agente000: El dictamen intentó citar la Foja {citation.foja_ref}, "
                    f"la cual no existe entre las fojas útiles del expediente ({sorted(valid_foja_numbers)})."
                )
            verified.append(citation)
        return verified

    def analyze_full_expediente(
        self,
        file_bytes: bytes,
        file_name: str,
        user_verbal_statement: str = "",
        explicit_province: ProvinceKey | None = None,
        explicit_role: UserRole | None = None,
        claimed_lawyer_fee_pct: Decimal | None = None,
        upfront_cost_requested_ars: Decimal | None = None,
        extra_known_names: list[str] | None = None,
    ) -> FinalCitizenReport:
        """Ejecuta el ciclo completo de Ida y Vuelta de Agente000 (Fases 1 a 4) y purga la RAM."""
        from auditor_revision.compliance_shield import ComplianceShield
        from auditor_revision.report_builder import CitizenReportBuilder

        ComplianceShield.assert_safe_input(user_verbal_statement or "", "user_verbal_statement")

        volatile_session = self.drive_manager.load_and_prequote_in_ram(
            file_bytes=file_bytes,
            file_name=file_name,
        )
        session_id = volatile_session.session_id
        self.anonymizer.create_session(session_id)

        try:
            # --- FASE 1: Anonimización en RAM y Triage Exacto ---
            for foja_check in volatile_session.fojas:
                if not foja_check.is_discarded_hygiene:
                    ComplianceShield.assert_safe_input(
                        foja_check.cleaned_text,
                        f"Foja {foja_check.foja_number}",
                    )
            anonymized_fojas: list[FojaPageContent] = []
            for foja in volatile_session.fojas:
                anon_text = self.anonymizer.anonymize_text(
                    foja.cleaned_text,
                    session_id=session_id,
                    extra_known_names=extra_known_names,
                )
                anonymized_fojas.append(
                    FojaPageContent(
                        foja_number=foja.foja_number,
                        raw_char_count=foja.raw_char_count,
                        image_count=foja.image_count,
                        requires_ocr=foja.requires_ocr,
                        is_discarded_hygiene=foja.is_discarded_hygiene,
                        discard_reason=foja.discard_reason,
                        cleaned_text=anon_text,
                    )
                )

            anon_verbal = self.anonymizer.anonymize_text(
                user_verbal_statement or "",
                session_id=session_id,
                extra_known_names=extra_known_names,
            )

            sample_corpus = "\n".join(
                f.cleaned_text for f in anonymized_fojas[:5] if not f.is_discarded_hygiene
            ) + f"\n{anon_verbal}"

            detected_fuero, detected_province, detected_role = self.classify_fuero_and_jurisdiction(
                text_sample=sample_corpus,
                explicit_province=explicit_province,
                explicit_role=explicit_role,
            )
            caratula_anon = self._extract_caratula_from_fojas(anonymized_fojas)

            case_context = CaseContext(
                session_id=session_id,
                country_code="AR",
                province_jurisdiction=detected_province,
                user_role=detected_role,
                detected_fuero=detected_fuero,
                caratula_detected=caratula_anon,
                user_verbal_statement=anon_verbal,
                claimed_lawyer_fee_pct=claimed_lawyer_fee_pct,
                upfront_cost_requested_ars=upfront_cost_requested_ars,
                fojas_content=anonymized_fojas,
            )

            jurisdictions_dict = self.provinces_config.get("jurisdictions", {})
            province_rules = (
                jurisdictions_dict.get(detected_province.value, {})
                if isinstance(jurisdictions_dict, dict)
                else {}
            )
            gratuity_rules = (
                self.provinces_config.get("gratuity_principles", {})
                if isinstance(self.provinces_config.get("gratuity_principles"), dict)
                else {}
            )

            # --- FASE 2: Primera Derivación -> Sub-Agente de Fuero (con Cola Anti-429) ---
            fuero_result = self.rate_limiter.execute_with_cooling(
                self._run_fuero_derivation,
                case_context,
                province_rules,
            )

            # --- FASE 3: Segunda Derivación -> Agente de Revisión y Auditoría ---
            audit_result = self._run_auditor_derivation(
                context=case_context,
                fuero_result=fuero_result,
                province_rules=province_rules,
                gratuity_rules=gratuity_rules,
            )

            # --- FASE 4: Validación Anti-Alucinación, Escudo Legal y Des-anonimización ---
            valid_foja_set = {f.foja_number for f in anonymized_fojas if not f.is_discarded_hygiene}
            if not valid_foja_set:
                valid_foja_set = {f.foja_number for f in anonymized_fojas}
            verified_citations = self._validate_anti_hallucination(fuero_result, valid_foja_set)

            # Des-anonimizar las citas verificadas para que el informe del usuario tenga sus datos reales
            restored_citations: list[FojaCitation] = []
            for cit in verified_citations:
                restored_citations.append(
                    FojaCitation(
                        foja_ref=cit.foja_ref,
                        document_piece_type=cit.document_piece_type,
                        verbatim_excerpt=self.anonymizer.deanonymize_text(cit.verbatim_excerpt, session_id),
                        citizen_plain_translation=self.anonymizer.deanonymize_text(
                            cit.citizen_plain_translation, session_id
                        ),
                        normative_ref=cit.normative_ref,
                    )
                )

            restored_caratula = self.anonymizer.deanonymize_text(caratula_anon, session_id)
            calc_note = ""
            if fuero_result.preliminary_calculation is not None:
                c = fuero_result.preliminary_calculation
                calc_note = (
                    f" Se identificó un monto base documentado de ${c.base_amount_ars} ARS "
                    f"(proyección preliminar orientativa: ${c.estimated_total_ars} ARS según {c.calculation_formula_applied})."
                )

            citizen_summary = (
                f"Radiografía Documental de tu Causa ({restored_caratula}): "
                f"Tu expediente tramita en el área {detected_fuero.value} bajo las reglas de {detected_province.value}. "
                f"De un total de {volatile_session.pre_quote.total_pages} páginas recibidas, se analizaron "
                f"{volatile_session.pre_quote.useful_pages_after_hygiene} fojas útiles verificadas "
                f"(descartando {volatile_session.pre_quote.discarded_junk_pages} páginas sin contenido procesal). "
                f"{fuero_result.technical_diagnosis}{calc_note}"
            )

            contradictions_text = ""
            if audit_result.contradictions_detected:
                items = [
                    self.anonymizer.deanonymize_text(c.objective_description, session_id)
                    for c in audit_result.contradictions_detected
                ]
                contradictions_text = " Observaciones documentales cruzadas: " + " | ".join(items)

            gratuity_text = f" {audit_result.gratuity_illegal_cost_alert}" if audit_result.gratuity_illegal_cost_alert else ""
            audit_section = (
                f"{fuero_result.procedural_deadlines_summary} "
                f"Estado de actividad: {audit_result.caducidad_explanation}"
                f"{contradictions_text}{gratuity_text}"
            )

            alert_box: str | None = None
            if audit_result.do_not_sign_yet_banner:
                alert_box = (
                    f"¡NO FIRMES TODAVÍA! [ISO-PREV-04] Se detectaron cláusulas dispositivas en la documentación: "
                    f"{audit_result.do_not_sign_reason}. Consultá con un profesional matriculado o defensoría "
                    f"pública antes de suscribir cualquier conformidad."
                )

            last_foja_ref = restored_citations[-1].foja_ref
            last_dt_iso = (
                fuero_result.last_documented_act_date.isoformat()
                if fuero_result.last_documented_act_date
                else None
            )

            audio_script = CitizenReportBuilder.build_audio_script_90s(
                caratula=restored_caratula,
                fuero=detected_fuero,
                province=detected_province,
                total_pages=volatile_session.pre_quote.total_pages,
                useful_pages=volatile_session.pre_quote.useful_pages_after_hygiene,
                citations=restored_citations,
                audit_result=audit_result,
            )

            whatsapp_msg = CitizenReportBuilder.build_whatsapp_template(
                caratula=restored_caratula,
                last_foja_ref=last_foja_ref,
                last_act_date_iso=last_dt_iso,
                audit_result=audit_result,
            )

            legal_shield = ComplianceShield.get_statutory_disclaimer(
                fuero=detected_fuero,
                province=detected_province,
            )

            report_id = f"rep-{uuid.uuid4().hex[:10]}"
            generated_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

            final_report = FinalCitizenReport(
                report_id=report_id,
                generated_at_iso=generated_iso,
                country_code="AR",
                province_jurisdiction=detected_province,
                fuero=detected_fuero,
                subscription_tier_applied=volatile_session.pre_quote.suggested_tier,
                do_not_sign_yet_alert=audit_result.do_not_sign_yet_banner,
                do_not_sign_alert_box=alert_box,
                citizen_plain_summary=ComplianceShield.sanitize_iso_text(citizen_summary),
                verified_timeline_cards=restored_citations,
                audit_and_deadlines_section=ComplianceShield.sanitize_iso_text(audit_section),
                questions_for_your_lawyer=audit_result.suggested_questions_for_lawyer,
                audio_script_90_seconds=audio_script,
                whatsapp_ready_message=whatsapp_msg,
                legal_shield_disclaimer=legal_shield,
                zero_storage_confirmation=(
                    f"Certificación Zero-Storage Ley 25.326: Expediente procesado en RAM volátil, informe JSON + PDF "
                    f"guardados en el Drive del titular y memoria de sesión '{session_id}' destruida al 100%."
                ),
                drive_saved_file_uri=f"gdrive://user/Informes_Dr_H/Informe_Dr_H_{session_id}.json",
            )

            pdf_bytes_in_ram = CitizenReportBuilder.build_pdf_in_ram(final_report)

            temp_payload = {
                "report_id": report_id,
                "generated_at_iso": generated_iso,
                "caratula": restored_caratula,
                "fuero": detected_fuero.value,
                "province": detected_province.value,
                "citizen_plain_summary": final_report.citizen_plain_summary,
                "audit_and_deadlines_section": final_report.audit_and_deadlines_section,
                "questions_for_your_lawyer": final_report.questions_for_your_lawyer,
                "whatsapp_ready_message": final_report.whatsapp_ready_message,
                "legal_shield_disclaimer": final_report.legal_shield_disclaimer,
            }

            drive_uri = self.drive_manager.save_report_and_wipe_ram(
                session_id=session_id,
                report_payload=temp_payload,
                pdf_report_bytes=pdf_bytes_in_ram,
            )
            final_report.drive_saved_file_uri = drive_uri
            return final_report
        finally:
            self.anonymizer.destroy_session(session_id)

    # =========================================================================
    # ESCÁNER DE URGENCIA ("¡NO FIRMES TODAVÍA!") Y CONSULTOR DE REPOSITORIO
    # =========================================================================

    def run_emergency_scan(self, request: EmergencyScanRequest) -> EmergencyScanResponse:
        """Analiza en tiempo real un documento fotografiado o cédula urgente (/emergency-scan)."""
        from auditor_revision.compliance_shield import ComplianceShield
        from auditor_revision.emergency_scanner import EmergencyScanner

        ComplianceShield.assert_safe_input(request.document_text, "document_text")
        ComplianceShield.assert_safe_input(request.user_context_note, "user_context_note")

        fuero, _, _ = self.classify_fuero_and_jurisdiction(
            text_sample=f"{request.document_text}\n{request.user_context_note}",
            explicit_province=request.province_jurisdiction,
            explicit_role=UserRole.CONSULTA_URGENTE,
        )
        return EmergencyScanner.evaluate_emergency_document(
            request=request,
            detected_fuero=fuero,
        )

    def consult_normative_repository(self, request: RepositoryConsultRequest) -> RepositoryConsultResponse:
        """Responde consultas sobre leyes vigentes e historia temporal del repositorio por área (/repository-consult)."""
        from auditor_revision.compliance_shield import ComplianceShield

        ComplianceShield.assert_safe_input(request.query, "query")
        fuero = request.fuero
        if fuero is None:
            fuero, _, _ = self.classify_fuero_and_jurisdiction(
                text_sample=request.query,
                explicit_province=request.province_jurisdiction,
            )

        criollo_translation, citation_audit, repo_laws = self.fueros_router.consult_fuero_repository(
            fuero=fuero,
            query=request.query,
            reference_date=request.reference_date,
        )

        laws = list(repo_laws)
        for update in self._daily_norm_updates.get(fuero, []):
            if request.reference_date is None or update.effective_from <= request.reference_date:
                laws.append(
                    f"{update.norm_id} ({update.source_organism} - Vigente desde {update.effective_from.isoformat()}): {update.title}"
                )
        if citation_audit.repealed_or_modified_warnings:
            laws.extend(citation_audit.repealed_or_modified_warnings)

        jurisdictions_dict = self.provinces_config.get("jurisdictions", {})
        prov_rules = (
            jurisdictions_dict.get(request.province_jurisdiction.value, {})
            if isinstance(jurisdictions_dict, dict)
            else {}
        )
        gratuity_dict = self.provinces_config.get("gratuity_principles", {})
        grat_rule = (
            gratuity_dict.get(fuero.value, {"applies": False})
            if isinstance(gratuity_dict, dict)
            else {"applies": False}
        )

        ref_date_str = (
            request.reference_date.isoformat()
            if request.reference_date is not None
            else datetime.now(timezone.utc).date().isoformat()
        )

        answer = (
            f"[{criollo_translation.get('criollo_situation_detected', fuero.value)}] "
            f"{criollo_translation.get('plain_explanation', '')} "
            f"Norma aplicable: {criollo_translation.get('applicable_law_and_article', '')}. "
            f"Protección inmediata: {criollo_translation.get('immediate_citizen_protection', '')} "
            f"Jurisdicción {request.province_jurisdiction.value}: opera mediante "
            f"{prov_rules.get('electronic_system', 'sistema judicial local')} ({prov_rules.get('nota_rule', '')})."
        )

        validity_status = (
            "Todas las normas citadas se encuentran vigentes."
            if citation_audit.is_fully_current
            else f"ALERTA DE VIGENCIA: {' | '.join(citation_audit.repealed_or_modified_warnings + citation_audit.hallucinated_or_unknown_warnings)}"
        )

        return RepositoryConsultResponse(
            classified_fuero=fuero,
            province_jurisdiction=request.province_jurisdiction,
            normative_citations=laws,
            procedural_rules_applied={
                "dias_de_nota": prov_rules.get("dias_de_nota", ["MARTES", "VIERNES"]),
                "grace_period_hours": prov_rules.get("grace_period_hours", ""),
                "gratuity": grat_rule,
                "criollo_to_law": criollo_translation,
                "citation_validation": {
                    "is_fully_current": citation_audit.is_fully_current,
                    "repealed_warnings": citation_audit.repealed_or_modified_warnings,
                    "hallucinated_warnings": citation_audit.hallucinated_or_unknown_warnings,
                },
            },
            citizen_clear_answer=answer,
            temporal_validity_note=(
                f"Control de Vigencia Temporal (Etapa 4.4 - Fecha corte {ref_date_str}): {validity_status}"
            ),
        )

    # =========================================================================
    # FASE 5: ENRUTAMIENTO 07:00 AM BOLETÍN OFICIAL -> FUERO + PROYECTO GAMUZA (BOLSA)
    # =========================================================================

    def ingest_07am_and_dispatch_to_gamuza(
        self,
        norms: list[BoletinOficialNormInput] | None = None,
        use_default_batch_if_none: bool = False,
    ) -> list[GamuzaRegulatorySignal]:
        """Procesa las normas del Boletín Oficial (07:00 AM), actualiza el Fuero respectivo y emite señal a Gamuza."""
        from ingestion_07am.gamuza_bridge import GamuzaKnowledgeBridge
        from ingestion_07am.repository_updater import NormativeRepositoryUpdater
        from ingestion_07am.scraper_oficial import OfficialGazetteCollector

        if norms is None and use_default_batch_if_none:
            norms_to_process = OfficialGazetteCollector.get_default_07am_batch()
        elif norms is None or len(norms) == 0:
            raise ValueError("La lista de normas matutinas para ingesta 07:00 AM no puede estar vacía.")
        else:
            norms_to_process = norms

        emitted: list[GamuzaRegulatorySignal] = []

        for norm in norms_to_process:
            # 1. Validar aislamiento estricto (Cero datos de expedientes privados hacia Gamuza)
            GamuzaKnowledgeBridge.assert_zero_private_data_leakage(norm)

            # 2. Actualizar exclusivamente el/los Sub-Agentes de Fuero afectados y las calculadoras en caliente
            update_summary = NormativeRepositoryUpdater.update_repositories_for_norm(
                norm=norm,
                fueros_router=self.fueros_router,
                daily_norm_store=self._daily_norm_updates,
            )

            # 3. Construir y emitir la señal regulatoria nacional hacia Proyecto Gamuza (Geopolítica + Bolsa)
            signal = GamuzaKnowledgeBridge.build_regulatory_signal(
                norm=norm,
                target_fuero=update_summary.primary_fuero,
            )
            self._emitted_gamuza_signals.append(signal)
            emitted.append(signal)

        source_statuses = OfficialGazetteCollector.probe_official_sources_connectivity()
        self.system_watchdog.record_07am_sync(source_statuses=source_statuses)
        return emitted
