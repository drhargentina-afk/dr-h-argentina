"""Detector Preventivo de Actos Dispositivos y Verificador de Exenciones Legales Estatutarias (`emergency_scanner.py`).

Implementa con terminología técnica ISO-Compliance:
1. `[ISO-PREV-04] Detector Preventivo de Actos Dispositivos, Renuncia de Derechos y Pactos de Cuota Litis`:
   Analiza en tiempo real cualquier documento exhibido al ciudadano en comisarías, oficinas laborales,
   escribanías, aseguradoras o estudios de cobranza ("¡No Firmes Todavía!"), clasificando el nivel
   de urgencia (`CRITICO_NO_FIRMAR`, `PRECAUCION_CONSULTAR`, `ESTANDAR`) y traduciendo las cláusulas
   al artículo legal exacto que protege al ciudadano.
2. `[ISO-ARAN-03] Verificador de Exenciones Legales Estatutarias y Escalas Arancelarias Oficiales`:
   Audita matemáticamente con `Decimal` cualquier adelanto de gastos o porcentaje de pacto de cuota litis
   frente a la Ley 27.423 (Honorarios Profesionales), Ley 20.744 Art. 20 y Art. 277 (tope 20% laboral y
   gratuidad), Ley 24.240 Art. 53 (gratuidad consumidor), CCyCN Arts. 544/706 (prohibición de cuota litis
   sobre alimentos de menores) y Ley 16.986 (Amparos de Salud).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Literal

from auditor_revision.compliance_shield import ComplianceShield
from core.models import EmergencyScanRequest, EmergencyScanResponse, FueroKey, ProvinceKey


@dataclass(frozen=True)
class DispositiveClauseAlert:
    """Cláusula dispositiva o arancelaria detectada en un documento antes de su firma."""

    clause_category: str
    citizen_plain_warning: str
    protective_norm_article: str
    risk_tier: Literal["CRITICO_NO_FIRMAR", "PRECAUCION_CONSULTAR", "ESTANDAR"]


class EmergencyScanner:
    """Escáner preventivo de actos dispositivos ('¡No Firmes Todavía!') y verificador de exenciones estatutarias."""

    # Catálogo determinista de actos dispositivos y cláusulas de renuncia
    _DISPOSITIVE_RULES: tuple[tuple[re.Pattern[str], str, str, str, Literal["CRITICO_NO_FIRMAR", "PRECAUCION_CONSULTAR"]], ...] = (
        (
            re.compile(r"nada\s+m[aá]s\s+tengo\s+que\s+reclamar|renuncio\s+a\s+todo\s+reclamo|desisto\s+de\s+todo\s+derecho", re.IGNORECASE),
            "RENUNCIA_AMPLIA_DERECHOS",
            "Contiene una cláusula liberatoria total ('nada más tengo que reclamar') que extingue la posibilidad de reclamar diferencias posteriores.",
            "Arts. 12 y 15 Ley 20.744 (Principio de Irrenunciabilidad) y Art. 944 CCyCN (Interpretación restrictiva de la renuncia)",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"desistimiento\s+de\s+la\s+acci[oó]n\s+y\s+del\s+derecho|art\.?\s*305\s+cpcc", re.IGNORECASE),
            "DESISTIMIENTO_ACCION_Y_DERECHO",
            "Implica el desistimiento definitivo tanto del juicio actual como del derecho de fondo, impidiendo iniciar otro proceso en el futuro.",
            "Art. 305 Código Procesal Civil y Comercial de la Nación y Códigos Provinciales",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"renuncia\s+al\s+empleo|renuncio\s+a\s+mi\s+puesto|art\.?\s*240\s+lct", re.IGNORECASE),
            "RENUNCIA_LABORAL_SIN_INDEMNIZACION",
            "Configura una renuncia voluntaria al empleo: al suscribirla se pierde el derecho a la indemnización por antigüedad y al fondo de desempleo.",
            "Arts. 240 y 245 Ley de Contrato de Trabajo N° 20.744",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"recib[ií]\s+conforme\s+el\s+total|carta\s+de\s+pago\s+total|recibo\s+cancelatorio|en\s+efectivo\s+en\s+este\s+acto", re.IGNORECASE),
            "RECIBO_CANCELATORIO_SIN_DEPOSITO_BANCARIO",
            "Declara haber recibido la totalidad del dinero en el acto; jamás debe suscribirse antes de verificar la acreditación efectiva en cuenta bancaria propia (CBU).",
            "Art. 277 Ley 20.744 (Pago en juicio mediante depósito bancario a la orden del trabajador) y Art. 896 CCyCN",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"en\s+blanco|espacio\s+sin\s+completar|firma\s+al\s+pie\s+sin\s+fecha", re.IGNORECASE),
            "INSTRUMENTO_EN_BLANCO",
            "Refiere a la suscripción de formularios o fojas con campos en blanco o sin fecha cierta.",
            "Art. 60 Ley 20.744 (Prohibición de firma en blanco en materia laboral) y Art. 315 CCyCN",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"cesi[oó]n\s+de\s+derechos\s+hereditarios|cesi[oó]n\s+de\s+acciones\s+y\s+derechos\s+litigiosos|poder\s+irrevocable\s+de\s+cobro", re.IGNORECASE),
            "CESION_DERECHOS_O_PODER_DE_COBRO",
            "Transfiere la titularidad de tu herencia, crédito judicial o facultad de percibir dinero a favor de un tercero.",
            "Arts. 1618 y 2302 Código Civil y Comercial de la Nación",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"reconocimiento\s+de\s+deuda|convenio\s+de\s+refinanciaci[oó]n|renuncia\s+a\s+la\s+prescripci[oó]n", re.IGNORECASE),
            "RECONOCIMIENTO_DEUDA_PRESCRIPTA",
            "Suscribir una refinanciación o reconocimiento sobre una deuda antigua interrumpe o hace renunciar a la prescripción liberatoria ya ganada.",
            "Arts. 2535, 2545 y 2560 Código Civil y Comercial de la Nación / Art. 8 bis Ley 24.240",
            "CRITICO_NO_FIRMAR",
        ),
        (
            re.compile(r"acta\s+de\s+declaraci[oó]n\s+en\s+comisar[ií]a|manifestaci[oó]n\s+espont[aá]nea\s+policial", re.IGNORECASE),
            "DECLARACION_EN_SEDE_POLICIAL_SIN_DEFENSOR",
            "Propone tomar declaración en dependencia policial sin presencia previa de abogado defensor o Defensor Oficial.",
            "Art. 18 Constitución Nacional y Art. 294 Código Procesal Penal de la Nación",
            "CRITICO_NO_FIRMAR",
        ),
    )

    _RE_CUOTA_LITIS_PCT = re.compile(
        r"(?:cuota\s+litis|honorarios\s+pactados|participaci[oó]n\s+del\s+letrado|comisi[oó]n)[^%\d]{0,35}(\d{1,2}(?:[.,]\d{1,2})?)\s*%",
        re.IGNORECASE,
    )

    # Topes estatutarios máximos de Pacto de Cuota Litis por Fuero (Ley 27.423 Art. 6 y Ley 20.744 Art. 277)
    _STATUTORY_MAX_CUOTA_LITIS_PCT: dict[FueroKey, Decimal] = {
        FueroKey.PENAL: Decimal("30.00"),
        FueroKey.FAMILIA: Decimal("0.00"),  # Art. 6 inc. c Ley 27.423: prohibido pacto de cuota litis en asuntos alimentarios y de menores
        FueroKey.LABORAL: Decimal("20.00"),  # Art. 277 LCT y Art. 6 inc. c Ley 27.423: tope absoluto del 20%
        FueroKey.CIVIL: Decimal("30.00"),  # Art. 6 inc. b Ley 27.423: hasta 30% sin adelanto de gastos
        FueroKey.COMERCIAL_CONSUMIDOR: Decimal("20.00"),  # Protección al consumidor Ley 24.240
        FueroKey.SEGURIDAD_SOCIAL_SALUD: Decimal("20.00"),  # Art. 6 inc. c Ley 27.423: procesos previsionales
        FueroKey.CONTENCIOSO_TRIBUTARIO: Decimal("30.00"),
    }

    @classmethod
    def verify_statutory_exemptions_and_fees(
        cls,
        fuero: FueroKey,
        claimed_fee_pct: Decimal | None,
        upfront_cost_ars: Decimal | None,
        verbal_or_doc_text: str,
        gratuity_config: dict[str, object],
    ) -> str | None:
        """Ejecuta el [ISO-ARAN-03] Verificador de Exenciones Legales Estatutarias y Escalas Arancelarias Oficiales."""
        observations: list[str] = []
        fuero_grat = gratuity_config.get(fuero.value)
        max_statutory_pct = cls._STATUTORY_MAX_CUOTA_LITIS_PCT.get(fuero, Decimal("30.00"))

        # Extraer porcentaje de cuota litis si aparece escrito en el documento o relato
        effective_pct = claimed_fee_pct
        if effective_pct is None and verbal_or_doc_text:
            match_pct = cls._RE_CUOTA_LITIS_PCT.search(verbal_or_doc_text)
            if match_pct:
                try:
                    effective_pct = Decimal(match_pct.group(1).replace(",", "."))
                except Exception:
                    effective_pct = None

        # 1. Control de tope de Pacto de Cuota Litis (Ley 27.423 Art. 6 y Art. 277 LCT)
        if effective_pct is not None and effective_pct > max_statutory_pct:
            if fuero == FueroKey.FAMILIA:
                observations.append(
                    f"Se registra referencia a pacto de cuota litis del {effective_pct}%, mientras que el Art. 6 inc. c "
                    f"de la Ley 27.423 establece que los asuntos de alimentos y de familia/menores no pueden ser objeto "
                    f"de pactos de cuota litis (tope estatutario: 0%)."
                )
            else:
                observations.append(
                    f"Se registra referencia a pacto de cuota litis del {effective_pct}%, el cual excede la escala "
                    f"máxima de referencia del {max_statutory_pct}% prevista para el área {fuero.value} "
                    f"(Art. 277 Ley 20.744 y Art. 6 Ley 27.423)."
                )

        # 2. Control de Exenciones Legales Estatutarias (Gratuidad)
        if isinstance(fuero_grat, dict) and fuero_grat.get("applies") is True:
            law_ref = str(fuero_grat.get("law_reference", "Normativa de Gratuidad"))
            rule_text = str(fuero_grat.get("citizen_protection_rule", ""))
            low_txt = (verbal_or_doc_text or "").lower()

            if upfront_cost_ars is not None and upfront_cost_ars > Decimal("0.00"):
                observations.append(
                    f"Se informa requerimiento de adelanto por ${upfront_cost_ars} ARS en un trámite alcanzado por "
                    f"exención legal estatutaria ({law_ref}). {rule_text} Se recomienda solicitar discriminación escrita "
                    f"y recibo oficial de cualquier concepto arancelario."
                )
            elif any(w in low_txt for w in ("tasa de justicia", "sellado judicial", "adelanto para el juzgado", "plata para impulsar")):
                observations.append(
                    f"Constancia de Exención Legal Estatutaria ({law_ref}): {rule_text}"
                )

        if not observations:
            return None

        masked = ComplianceShield.apply_legal_mask(observations, iso_category="EXENCIONES_ARANCELES")
        return " | ".join(masked)

    @classmethod
    def scan_for_dispositive_acts(
        cls,
        text_corpus: str,
        fuero: FueroKey,
        claimed_fee_pct: Decimal | None = None,
    ) -> list[DispositiveClauseAlert]:
        """Ejecuta el [ISO-PREV-04] Detector Preventivo de Actos Dispositivos y Pactos de Cuota Litis."""
        alerts: list[DispositiveClauseAlert] = []
        if not text_corpus:
            return alerts

        for pattern, category, warning, norm_art, tier in cls._DISPOSITIVE_RULES:
            if pattern.search(text_corpus):
                clean_warning = ComplianceShield.sanitize_iso_text(warning)
                alerts.append(
                    DispositiveClauseAlert(
                        clause_category=category,
                        citizen_plain_warning=f"{ComplianceShield.ISO_MODULE_HEADERS['ACTOS_DISPOSITIVOS']}: {clean_warning}",
                        protective_norm_article=norm_art,
                        risk_tier=tier,
                    )
                )

        # Verificar también pacto de cuota litis excesivo dentro del texto escaneado
        max_pct = cls._STATUTORY_MAX_CUOTA_LITIS_PCT.get(fuero, Decimal("30.00"))
        detected_pct = claimed_fee_pct
        if detected_pct is None:
            match_pct = cls._RE_CUOTA_LITIS_PCT.search(text_corpus)
            if match_pct:
                try:
                    detected_pct = Decimal(match_pct.group(1).replace(",", "."))
                except Exception:
                    detected_pct = None

        if detected_pct is not None and detected_pct > max_pct:
            alerts.append(
                DispositiveClauseAlert(
                    clause_category="PACTO_CUOTA_LITIS_EXCEDIDO",
                    citizen_plain_warning=(
                        f"{ComplianceShield.ISO_MODULE_HEADERS['ACTOS_DISPOSITIVOS']}: Se observa cláusula de cuota litis "
                        f"del {detected_pct}%, superior al tope legal del {max_pct}% para {fuero.value}."
                    ),
                    protective_norm_article="Art. 6 Ley 27.423 y Art. 277 Ley 20.744",
                    risk_tier="CRITICO_NO_FIRMAR",
                )
            )

        return alerts

    @classmethod
    def evaluate_emergency_document(
        cls,
        request: EmergencyScanRequest,
        detected_fuero: FueroKey,
    ) -> EmergencyScanResponse:
        """Procesa una solicitud del endpoint `/emergency-scan` con validación Anti-Prompt Injection e ISO-Compliance."""
        ComplianceShield.assert_safe_input(request.document_text, "document_text")
        ComplianceShield.assert_safe_input(request.user_context_note, "user_context_note")

        combined = f"{request.document_text}\n{request.user_context_note}"
        alerts = cls.scan_for_dispositive_acts(combined, fuero=detected_fuero)

        if alerts:
            clauses = [f"{a.citizen_plain_warning} (Norma protectoria: {a.protective_norm_article})" for a in alerts]
            explanation = (
                "ALERTA PREVENTIVA — ¡NO FIRMES TODAVÍA! El Detector Preventivo de Actos Dispositivos [ISO-PREV-04] "
                "identificó cláusulas de renuncia de derechos, recibo cancelatorio o disposición patrimonial que "
                "requieren revisión letrada previa antes de prestar conformidad."
            )
            action_guide = (
                "1) Ejercé tu derecho a no suscribir en el acto y solicitá copia íntegra del texto para revisión. "
                "2) Si te encontrás en sede laboral, recordá que rige el principio de irrenunciabilidad (Art. 12 LCT) y gratuidad (Art. 20 LCT). "
                "3) Si es sede policial, recordá que nadie está obligado a declarar en comisaría sin su Defensor Oficial (Art. 18 CN). "
                "4) Consultá de inmediato con tu letrado matriculado o con la Defensoría Pública de tu jurisdicción."
            )
            return EmergencyScanResponse(
                do_not_sign_yet=True,
                urgency_level="PELIGRO_NO_FIRMAR",
                detected_fuero=detected_fuero,
                plain_explanation=explanation,
                detected_risk_clauses=clauses,
                immediate_action_guide=action_guide,
            )

        low_doc = combined.lower()
        if any(w in low_doc for w in ("cédula", "cedula", "traslado", "intimación", "plazo de", "comparezca")):
            return EmergencyScanResponse(
                do_not_sign_yet=False,
                urgency_level="INFORMATIVO_CON_PLAZO",
                detected_fuero=detected_fuero,
                plain_explanation=(
                    "Se detecta una notificación procesal o intimación fehaciente. La recepción de una cédula no implica "
                    "renunciar a derechos, pero activa el cómputo de plazos legales en días hábiles."
                ),
                detected_risk_clauses=[],
                immediate_action_guide=(
                    "1) Registrá la fecha y hora exacta de recepción del documento. "
                    "2) Remití copia urgente a tu abogado matriculado o acudí en las próximas 24 horas hábiles al servicio de patrocinio jurídico gratuito."
                ),
            )

        return EmergencyScanResponse(
            do_not_sign_yet=False,
            urgency_level="PRECAUCION_CONSULTAR_ANTES",
            detected_fuero=detected_fuero,
            plain_explanation=(
                f"Instrumento encuadrado en el área {detected_fuero.value} ({request.province_jurisdiction.value}). "
                f"No se verificaron cláusulas automáticas de desistimiento total, recomendándose revisión profesional estándar."
            ),
            detected_risk_clauses=[],
            immediate_action_guide=(
                "Solicitá ejemplar completo del instrumento para su verificación previa con un profesional matriculado."
            ),
        )
