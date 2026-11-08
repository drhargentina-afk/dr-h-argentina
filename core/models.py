"""Contratos de datos Pydantic v2 para el proyecto Dr. H Argentina (Nodo AR).

Define los esquemas estrictos de entrada, salida y transferencia entre Agente000,
el gestor Zero-Storage en memoria RAM, el anonimizador bidireccional PII,
los 7 especialistas de fuero, el auditor de revisión procesal y la señal
regulatoria hacia Proyecto Gamuza (Geopolítica + Bolsa y Acciones).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SubscriptionTier(str, Enum):
    """Tramos comerciales de Dr. H según el tamaño físico del expediente en páginas/fojas."""

    NIVEL_0_GRATUITO = "NIVEL_0_GRATUITO_HASTA_15_PAGS"
    PLAN_S_ESTANDAR = "PLAN_S_ESTANDAR_HASTA_150_PAGS"
    PLAN_M_COMPLEJO = "PLAN_M_COMPLEJO_HASTA_350_PAGS"
    PLAN_L_VOLUMINOSO = "PLAN_L_VOLUMINOSO_HASTA_600_PAGS"
    PACK_EXTRA_FOJAS = "PACK_EXTRA_MAYOR_600_PAGS"


class UserRole(str, Enum):
    """Rol procesal o situacional del ciudadano frente a la documentación."""

    ACTOR_RECLAMANTE = "ACTOR_RECLAMANTE"
    DEMANDADO_IMPUTADO = "DEMANDADO_IMPUTADO"
    CONSULTA_URGENTE = "CONSULTA_URGENTE"


class FueroKey(str, Enum):
    """Las 7 áreas judiciales especializadas del sistema jurídico argentino en Dr. H."""

    PENAL = "01_penal"
    FAMILIA = "02_familia"
    LABORAL = "03_laboral"
    CIVIL = "04_civil"
    COMERCIAL_CONSUMIDOR = "05_comercial_consumidor"
    SEGURIDAD_SOCIAL_SALUD = "06_seguridad_social_salud"
    CONTENCIOSO_TRIBUTARIO = "07_contencioso_tributario"


class ProvinceKey(str, Enum):
    """Jurisdicciones procesales mapeadas en provinces_config.json."""

    PJN_NACION_CABA = "PJN_NACION_CABA"
    PBA_MEV_SCBA = "PBA_MEV_SCBA"
    CORDOBA = "CORDOBA"
    SANTA_FE = "SANTA_FE"
    MENDOZA = "MENDOZA"
    RESTO_PROVINCIAS = "RESTO_PROVINCIAS"


class FojaPageContent(BaseModel):
    """Representación en memoria RAM de una foja/página individual sin alterar su numeración."""

    model_config = ConfigDict(extra="forbid")

    foja_number: int = Field(..., ge=1, description="Número real de foja/página 1-indexado del PDF original.")
    raw_char_count: int = Field(..., ge=0, description="Cantidad de caracteres nativos extraídos.")
    image_count: int = Field(..., ge=0, description="Cantidad de objetos imagen detectados en la página.")
    requires_ocr: bool = Field(..., description="True si es página escaneada sin texto digital suficiente.")
    is_discarded_hygiene: bool = Field(
        ...,
        description="True si se filtró por ser hoja en blanco, sello aislado o carátula repetida.",
    )
    discard_reason: str | None = Field(
        default=None,
        description="Motivo de descarte en filtro de higiene (conservando número de foja).",
    )
    cleaned_text: str = Field(..., description="Texto útil normalizado de la foja (o marcador de OCR).")


class PreQuoteResponse(BaseModel):
    """Respuesta instantánea (<2s) del pre-cotizador e higiene documental sin consumir IA."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=8, description="Identificador efímero de sesión en RAM.")
    file_name: str = Field(..., min_length=1, description="Nombre del archivo analizado.")
    total_pages: int = Field(..., ge=1, description="Cantidad total de páginas del PDF original.")
    useful_pages_after_hygiene: int = Field(
        ...,
        ge=0,
        description="Cantidad de páginas con contenido procesal útil tras descartar basura/blancos.",
    )
    discarded_junk_pages: int = Field(
        ...,
        ge=0,
        description="Páginas descartadas (en blanco, carátulas duplicadas o firmas de una línea).",
    )
    scanned_pages_requiring_ocr: int = Field(
        ...,
        ge=0,
        description="Páginas escaneadas como imagen que requieren motor OCR selectivo.",
    )
    native_digital_pages: int = Field(
        ...,
        ge=0,
        description="Páginas con texto digital nativo directo.",
    )
    suggested_tier: SubscriptionTier = Field(
        ...,
        description="Nivel de abono sugerido según cantidad de páginas del expediente.",
    )
    qualifies_for_free_tier: bool = Field(
        ...,
        description="True si total_pages <= 15 (Nivel 0 Gratuito).",
    )
    estimated_single_radiography_ars: Decimal = Field(
        ...,
        ge=Decimal("0.00"),
        description="Costo estimado de Pago Único por Radiografía Inicial en ARS (exactitud Decimal).",
    )
    estimated_monthly_tracking_ars: Decimal = Field(
        ...,
        ge=Decimal("0.00"),
        description="Costo estimado de Abono Mensual de Seguimiento en ARS (exactitud Decimal).",
    )
    preserved_foja_index: list[int] = Field(
        default_factory=list,
        description="Lista de números de foja reales que contienen información útil para citar.",
    )
    processing_time_ms: int = Field(..., ge=0, description="Tiempo de ejecución de pre-higiene en milisegundos.")

    @model_validator(mode="after")
    def _validate_page_counts(self) -> PreQuoteResponse:
        if self.useful_pages_after_hygiene + self.discarded_junk_pages != self.total_pages:
            raise ValueError(
                "La suma de páginas útiles y páginas descartadas debe coincidir exactamente con total_pages."
            )
        if self.scanned_pages_requiring_ocr > self.total_pages:
            raise ValueError("Las páginas que requieren OCR no pueden superar el total de páginas.")
        return self


class CaseContext(BaseModel):
    """Contexto clasificado del caso para circular entre Agente000, Fuero y Auditor."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=8, description="ID de sesión efímera en memoria RAM.")
    country_code: Literal["AR"] = Field(default="AR", description="Código ISO de Capa-País (Nodo Argentina).")
    province_jurisdiction: ProvinceKey = Field(
        ...,
        description="Jurisdicción procesal argentina detectada o informada.",
    )
    city_locality: str = Field(
        default="",
        max_length=200,
        description="Ciudad o localidad informada por el usuario o detectada en el caso.",
    )
    user_role: UserRole = Field(..., description="Rol del ciudadano en el expediente o consulta.")
    detected_fuero: FueroKey = Field(..., description="Especialidad de las 7 áreas judiciales asignada.")
    caratula_detected: str = Field(
        ...,
        min_length=3,
        description="Carátula o encabezado del expediente (anonimizado mientras circula hacia el LLM).",
    )
    user_verbal_statement: str = Field(
        default="",
        max_length=5000,
        description="Relato del ciudadano: qué le dijo su abogado, qué le quieren hacer firmar o su duda central.",
    )
    claimed_lawyer_fee_pct: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        le=Decimal("100.00"),
        description="Porcentaje de honorarios o cuota litis que el usuario menciona que le pidieron.",
    )
    upfront_cost_requested_ars: Decimal | None = Field(
        default=None,
        ge=Decimal("0.00"),
        description="Monto en pesos que le pidieron adelantar para supuestas tasas o gastos.",
    )
    fojas_content: list[FojaPageContent] = Field(
        default_factory=list,
        description="Fojas útiles limpias y anonimizadas con su numeración real preservada.",
    )


class FojaCitation(BaseModel):
    """Cita verificable y trazable a una foja específica del expediente y norma legal."""

    model_config = ConfigDict(extra="forbid")

    foja_ref: int = Field(..., ge=1, description="Número exacto de foja/página en el PDF del usuario.")
    document_piece_type: str = Field(
        ...,
        min_length=2,
        description="Tipo de pieza procesal (Demanda, Proveído, Cédula, Pericia, Acta, Sentencia).",
    )
    verbatim_excerpt: str = Field(
        ...,
        min_length=5,
        description="Extracto textual verificable presente en esa foja.",
    )
    citizen_plain_translation: str = Field(
        ...,
        min_length=10,
        description="Traducción a lenguaje claro ciudadano ('En criollo') de esa actuación.",
    )
    normative_ref: str = Field(
        ...,
        min_length=3,
        description="Ley, código o artículo aplicable a esa foja.",
    )


class PreliminaryMoneyCalculation(BaseModel):
    """Cálculo orientativo preliminar con precisión Decimal (indemnización, cuota o intereses)."""

    model_config = ConfigDict(extra="forbid")

    concept_description: str = Field(..., min_length=5, description="Fórmula y rubro calculado (ej. Art. 245 LCT).")
    base_amount_ars: Decimal = Field(..., ge=Decimal("0.00"), description="Monto base en ARS con precisión Decimal.")
    estimated_total_ars: Decimal = Field(..., ge=Decimal("0.00"), description="Total preliminar estimado en ARS.")
    calculation_formula_applied: str = Field(
        ...,
        min_length=5,
        description="Explicación matemática determinista del criterio aplicado.",
    )
    disclaimer_note: str = Field(
        default="Cálculo matemático orientativo sujeto a liquidación judicial y tasa activa del tribunal.",
        description="Advertencia de alcance técnico.",
    )


class FueroAnalysisResult(BaseModel):
    """Resultado devuelto por el Sub-Agente de Fuero hacia Agente000 (Fase 2)."""

    model_config = ConfigDict(extra="forbid")

    fuero_agent_id: FueroKey = Field(..., description="Identificador del especialista de fuero que dictaminó.")
    normative_framework: list[str] = Field(
        ...,
        min_length=1,
        description="Encuadre normativo jerárquico (Constitución, Leyes Nacionales, Código Procesal).",
    )
    applied_current_laws: list[str] = Field(
        ...,
        min_length=1,
        description="Leyes vigentes aplicadas con verificación temporal.",
    )
    foja_citations: list[FojaCitation] = Field(
        ...,
        min_length=1,
        description="Citas exactas a número de foja (foja_ref) con traducción ciudadana.",
    )
    procedural_deadlines_summary: str = Field(
        ...,
        min_length=10,
        description="Reglas de plazos, días de nota (martes/viernes) y sistema electrónico según provincia.",
    )
    last_documented_act_date: date | None = Field(
        default=None,
        description="Fecha de la última actuación impulsoria detectada en las fojas.",
    )
    detected_dates_by_foja: dict[int, str] = Field(
        default_factory=dict,
        description="Mapa de foja -> fecha detectada en texto (YYYY-MM-DD) para auditoría de tiempos muertos.",
    )
    preliminary_calculation: PreliminaryMoneyCalculation | None = Field(
        default=None,
        description="Cálculo preliminar con Decimal exacto si el caso involucra montos documentados.",
    )
    technical_diagnosis: str = Field(
        ...,
        min_length=20,
        description="Síntesis técnica del estado procesal de la causa.",
    )


class ContradictionFinding(BaseModel):
    """Inconsistencia o contradicción detectada entre fojas o frente al relato verbal."""

    model_config = ConfigDict(extra="forbid")

    foja_a: int = Field(..., ge=1, description="Foja donde consta el primer hecho o documento.")
    foja_b: int | None = Field(
        default=None,
        ge=1,
        description="Segunda foja en contradicción (o None si contradice lo que el abogado dijo verbalmente).",
    )
    contradiction_type: Literal[
        "RELATO_ABOGADO_VS_EXPEDIENTE",
        "FECHAS_INCOMPATIBLES",
        "PRUEBA_NO_PRODUCIDA",
        "MONTO_O_ACUERDO_DESFAVORABLE",
    ] = Field(..., description="Categoría de contradicción detectada.")
    objective_description: str = Field(
        ...,
        min_length=15,
        description="Descripción objetiva y verificable de la discrepancia sin calificar intenciones.",
    )


class AuditReviewResult(BaseModel):
    """Resultado de la Segunda Derivación: Auditoría Procesal y Validación Cruzada (Fase 3)."""

    model_config = ConfigDict(extra="forbid")

    days_since_last_impulse: int = Field(
        ...,
        ge=0,
        description="Días corridos transcurridos desde el último acto impulsorio documentado.",
    )
    dead_time_periods_detected: list[str] = Field(
        default_factory=list,
        description="Períodos de inactividad procesal mayores a 30 días detectados en la cronología.",
    )
    caducidad_risk_level: Literal["BAJO", "MODERADO", "ALTO_CRITICO", "NO_APLICA_IMPULSO_OFICIO"] = Field(
        ...,
        description="Nivel de riesgo de caducidad/perención de instancia según provincia y fuero.",
    )
    caducidad_explanation: str = Field(
        ...,
        min_length=15,
        description="Fundamento procesal del riesgo de caducidad según el código provincial aplicable.",
    )
    contradictions_detected: list[ContradictionFinding] = Field(
        default_factory=list,
        description="Contradicciones entre lo que consta en el expediente y la versión verbal o entre actas.",
    )
    gratuity_illegal_cost_alert: str | None = Field(
        default=None,
        description="Alerta si se detecta cobro indebido violando gratuidad laboral/consumidor/familia.",
    )
    do_not_sign_yet_banner: bool = Field(
        ...,
        description="Bandera crítica '¡No Firmes Todavía!' ante desistimientos, renuncias o acuerdos lesivos.",
    )
    do_not_sign_reason: str | None = Field(
        default=None,
        description="Explicación inmediata si se activa la bandera '¡No Firmes Todavía!'.",
    )
    suggested_questions_for_lawyer: list[str] = Field(
        ...,
        min_length=2,
        description="Preguntas objetivas y respaldadas en fojas para hacerle al abogado matriculado.",
    )


class FinalCitizenReport(BaseModel):
    """Informe Final emitido por Agente000 tras Validación Anti-Alucinación y Escudo Legal (Fase 4)."""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(..., min_length=8, description="Identificador único del informe emitido.")
    generated_at_iso: str = Field(..., description="Timestamp ISO-8601 de emisión.")
    country_code: Literal["AR"] = Field(default="AR", description="Nodo País emisor.")
    province_jurisdiction: ProvinceKey = Field(..., description="Jurisdicción procesal aplicada.")
    fuero: FueroKey = Field(..., description="Área judicial intervenida.")
    subscription_tier_applied: SubscriptionTier = Field(..., description="Nivel comercial aplicado al expediente.")
    do_not_sign_yet_alert: bool = Field(..., description="Indicador visual inmediato de alerta preventiva.")
    do_not_sign_alert_box: str | None = Field(
        default=None,
        description="Texto destacado de urgencia si el documento contiene cláusulas de renuncia o riesgo inminente.",
    )
    citizen_plain_summary: str = Field(
        ...,
        min_length=40,
        description="Explicación clara, empática y con Escudo Legal sobre qué ocurrió, qué significa y qué falta.",
    )
    verified_timeline_cards: list[FojaCitation] = Field(
        ...,
        min_length=1,
        description="Tarjetas cronológicas verificadas contra las fojas reales del expediente.",
    )
    audit_and_deadlines_section: str = Field(
        ...,
        min_length=20,
        description="Sección objetiva sobre tiempos del expediente, días de nota y gratuidad aplicable.",
    )
    questions_for_your_lawyer: list[str] = Field(
        ...,
        min_length=2,
        description="Listado numerado de preguntas estratégicas para conversar con su abogado.",
    )
    audio_script_90_seconds: str = Field(
        ...,
        min_length=80,
        description="Guion hablado de ~90 segundos (200-230 palabras) en tono calmado y claro para síntesis de voz.",
    )
    whatsapp_ready_message: str = Field(
        ...,
        min_length=40,
        description="Texto exacto, respetuoso y firme con citas a fojas, listo para copiar y pegar en WhatsApp al abogado.",
    )
    legal_shield_disclaimer: str = Field(
        ...,
        min_length=40,
        description="Cláusula de Escudo Legal que protege a la plataforma y delimita el alcance informativo.",
    )
    zero_storage_confirmation: str = Field(
        ...,
        min_length=20,
        description="Certificación de guardado en el Drive del usuario y purga total de la memoria RAM del servidor.",
    )
    drive_saved_file_uri: str = Field(
        ...,
        min_length=10,
        description="Ruta o URI del informe guardado en el contenedor/Drive del usuario.",
    )


class BoletinOficialNormInput(BaseModel):
    """Entrada normativa matutina (07:00 AM) desde Boletín Oficial, InfoLEG, CSJN, BCRA, CNV, ARCA o INDEC."""

    model_config = ConfigDict(extra="forbid")

    norm_id: str = Field(..., min_length=3, description="Identificador oficial (ej. 'DNU 70/2026', 'Com. A 8120 BCRA').")
    norm_type: Literal[
        "LEY",
        "DECRETO_DNU",
        "RESOLUCION_CNV",
        "COMUNICACION_BCRA",
        "RESOLUCION_ARCA",
        "ACORDADA_CSJN",
        "INDICE_INDEC",
        "RESOLUCION_ENERGIA_MINERIA",
        "RESOLUCION_SMVM",
    ] = Field(
        default="DECRETO_DNU",
        description="Clasificación técnica del instrumento normativo o índice oficial.",
    )
    publication_date: date = Field(..., description="Fecha de publicación en el Boletín Oficial (BORA).")
    source_organism: Literal[
        "BORA",
        "INFOLEG",
        "SAIJ",
        "CSJN",
        "BCRA",
        "CNV",
        "ARCA",
        "ENARGAS_ENRE",
        "INDEC",
        "MINISTERIO_TRABAJO_SMVM",
    ] = Field(
        ...,
        description="Organismo oficial emisor de la norma, acordada o índice.",
    )
    title: str = Field(..., min_length=5, description="Título oficial o sumario de la norma.")
    full_text_excerpt: str = Field(..., min_length=20, description="Texto normativo relevante publicado.")
    effective_from: date = Field(..., description="Fecha de entrada en vigencia temporal.")
    constitutional_hierarchy: str = Field(
        default="Pirámide Constitucional Argentina: Ley Nacional / Reglamentación Federal (Arts. 31, 75 y 99 CN)",
        min_length=5,
        description="Ubicación jerárquica dentro de la pirámide normativa argentina (Art. 31 y Art. 75 inc. 22 CN).",
    )
    modifies_or_repeals: list[str] = Field(
        default_factory=list,
        description="Lista de leyes o decretos anteriores modificados o derogados (historia normativa).",
    )
    economic_parameters_update: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Mapa de parámetros económicos en formato string Decimal para actualización en caliente "
            "(ej. smvm_ars, canasta_6_a_12_bienes_ars, icl_bcra, ipc_mensual_pct)."
        ),
    )


class GamuzaMarketImpact(BaseModel):
    """Sub-esquema de impacto bursátil y sectorial para el módulo de Bolsa y Acciones de Proyecto Gamuza."""

    model_config = ConfigDict(extra="forbid")

    economic_sector: Literal[
        "ENERGIA_OIL_GAS",
        "BANCOS_FINANZAS",
        "AGRO_EXPORTACION",
        "MINERIA_LITIO_MATERIALES",
        "CONSUMO_RETAIL",
        "INDUSTRIA_CONSTRUCCION",
        "TELECOM_TECNOLOGIA",
        "SALUD_FARMACEUTICA",
        "MACRO_IMPUESTOS_GENERAL",
    ] = Field(..., description="Sector económico directamente afectado por la norma.")
    related_byma_wallstreet_tickers: list[str] = Field(
        default_factory=list,
        description="Tickers de acciones/ADRs argentinos vinculados al sector (ej. ['YPF', 'YPFD', 'PAM', 'PAMP', 'GGAL']).",
    )
    regulatory_direction: Literal["DESREGULACION_EXPANSIVA", "RESTRICCION_COSTO_MAYOR", "NEUTRAL_PROCEDIMENTAL"] = (
        Field(..., description="Dirección del impacto regulatorio sobre los costos u operatoria del sector.")
    )
    severity_score: int = Field(..., ge=1, le=10, description="Nivel de impacto regulatorio de 1 a 10.")
    rationale: str = Field(..., min_length=15, description="Justificación jurídica-económica trazable a la norma.")


class GamuzaRegulatorySignal(BaseModel):
    """Payload JSON público de salida hacia Proyecto Gamuza (Geopolítica + Bolsa y Acciones) (Fase 5)."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(..., min_length=8, description="ID único de la señal regulatoria emitida por Dr. H.")
    emitted_at_iso: str = Field(..., description="Timestamp ISO-8601 de emisión (ej. 07:00 AM antes de apertura BYMA).")
    origin_node: Literal["DR_H_AR"] = Field(default="DR_H_AR", description="Nodo Capa-País emisor.")
    country_code: Literal["AR"] = Field(default="AR", description="Código ISO del país.")
    norm_id: str = Field(..., description="Identificador de la ley, decreto, resolución o fallo de la CSJN.")
    norm_type: str = Field(default="DECRETO_DNU", description="Tipo de instrumento normativo procesado.")
    source_organism: str = Field(..., description="Fuente oficial verificada.")
    target_fuero_updated: FueroKey = Field(
        ...,
        description="Sub-Agente de Fuero de Dr. H cuyo repositorio normativo fue actualizado.",
    )
    regulatory_shock_score: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Puntaje consolidado de shock regulatorio/institucional (1 = rutinario, 10 = estructural).",
    )
    geopolitical_categories: list[str] = Field(
        default_factory=list,
        description="Categorías de inteligencia geopolítica e institucional para Proyecto Gamuza.",
    )
    strategic_resources: list[str] = Field(
        default_factory=list,
        description="Recursos estratégicos alcanzados (ej. VACA_MUERTA_HIDROCARBUROS, LITIO_MINERIA, AGRO_EXPORTACION).",
    )
    market_signal: Literal["BULLISH", "BEARISH", "NEUTRAL"] = Field(
        default="NEUTRAL",
        description="Señal direccional para el módulo interno de Bolsa y Acciones de Gamuza.",
    )
    gics_sectors_affected: list[str] = Field(
        default_factory=list,
        description="Sectores GICS estándar afectados (Energy, Financials, Utilities, Materials, Industrials).",
    )
    direct_tickers_byma_nyse: list[str] = Field(
        default_factory=list,
        description="Tickers directos de BYMA y NYSE/Wall Street (YPF, YPFD, PAM, PAMP, TGS, GGAL, BMA, CEPU, TXAR, ALUA).",
    )
    sovereign_bonds_impact: str = Field(
        default="NEUTRAL_CURVA_SOBERANA",
        min_length=5,
        description="Impacto estimado sobre deuda soberana (AL30/GD30/GD35) y prima de riesgo país.",
    )
    geopolitical_institutional_summary: str = Field(
        ...,
        min_length=20,
        description="Resumen de impacto institucional y seguridad jurídica para el motor geopolítico Gamuza.",
    )
    market_and_equities_impacts: list[GamuzaMarketImpact] = Field(
        ...,
        min_length=1,
        description="Señales estructuradas para el módulo interno de Bolsa y Acciones de Gamuza.",
    )
    privacy_isolation_verified: Literal[True] = Field(
        default=True,
        description="Garantía de que este payload proviene 100% de fuentes públicas sin datos de expedientes privados.",
    )


class EmergencyScanRequest(BaseModel):
    """Solicitud para el modo de urgencia '¡No Firmes Todavía!' (/emergency-scan)."""

    model_config = ConfigDict(extra="forbid")

    document_text: str = Field(
        ...,
        min_length=10,
        max_length=50000,
        description="Texto extraído por cámara/OCR del documento que le quieren hacer firmar o cédula urgente.",
    )
    province_jurisdiction: ProvinceKey = Field(
        default=ProvinceKey.PJN_NACION_CABA,
        description="Provincia donde ocurre la situación.",
    )
    city_locality: str = Field(
        default="",
        max_length=200,
        description="Ciudad o localidad donde ocurre la situación.",
    )
    user_context_note: str = Field(
        default="",
        max_length=2000,
        description="Contexto breve del usuario (ej. 'Estoy en la escribanía/oficina y me piden firmar esto').",
    )


class EmergencyScanResponse(BaseModel):
    """Respuesta inmediata del escáner de urgencia '¡No Firmes Todavía!'."""

    model_config = ConfigDict(extra="forbid")

    do_not_sign_yet: bool = Field(..., description="True si contiene renuncia de derechos, recibo saldo total o trampa.")
    urgency_level: Literal["PELIGRO_NO_FIRMAR", "PRECAUCION_CONSULTAR_ANTES", "INFORMATIVO_CON_PLAZO"] = Field(
        ...,
        description="Semáforo de urgencia para el ciudadano.",
    )
    detected_fuero: FueroKey = Field(..., description="Fuero jurídico involucrado.")
    plain_explanation: str = Field(..., min_length=20, description="Explicación inmediata en 3 puntos simples.")
    detected_risk_clauses: list[str] = Field(
        default_factory=list,
        description="Frases o cláusulas peligrosas detectadas en el papel.",
    )
    immediate_action_guide: str = Field(
        ...,
        min_length=15,
        description="Qué hacer en los próximos 5 minutos y a qué organismo/defensoría acudir.",
    )


class RepositoryConsultRequest(BaseModel):
    """Consulta ciudadana o profesional al repositorio normativo por área (/repository-consult)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=5, max_length=2000, description="Consulta sobre leyes vigentes o procedimiento.")
    fuero: FueroKey | None = Field(default=None, description="Área judicial opcional (si se omite, Agente000 clasifica).")
    province_jurisdiction: ProvinceKey = Field(
        default=ProvinceKey.PJN_NACION_CABA,
        description="Jurisdicción aplicable.",
    )
    city_locality: str = Field(
        default="",
        max_length=200,
        description="Ciudad o localidad informada por el usuario.",
    )
    reference_date: date | None = Field(
        default=None,
        description="Fecha histórica para consulta de vigencia temporal (Etapa 4.4).",
    )

    @field_validator("query")
    @classmethod
    def _clean_query(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("La consulta debe contener al menos 5 caracteres útiles.")
        return cleaned


class RepositoryConsultResponse(BaseModel):
    """Respuesta trazable del repositorio normativo de Dr. H."""

    model_config = ConfigDict(extra="forbid")

    classified_fuero: FueroKey = Field(..., description="Especialista de fuero que respondió.")
    province_jurisdiction: ProvinceKey = Field(..., description="Jurisdicción procesal considerada.")
    normative_citations: list[str] = Field(..., min_length=1, description="Leyes y artículos vigentes aplicados.")
    procedural_rules_applied: dict[str, object] = Field(
        ...,
        description="Días de nota, plazos de caducidad y beneficio de gratuidad de la jurisdicción.",
    )
    citizen_clear_answer: str = Field(..., min_length=20, description="Respuesta clara con trazabilidad normativa.")
    temporal_validity_note: str = Field(..., description="Confirmación de vigencia temporal de la norma citada.")
