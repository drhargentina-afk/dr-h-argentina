"""Módulo de Segunda Derivación de Agente000: Auditoría de Calidad Documental y Trazabilidad (`lawyer_watchdog.py`).

Implementa con rigor determinista y enmascaramiento técnico ISO-Compliance:
1. `[ISO-DOC-01] Contraste Objetivo de Trazabilidad entre Manifestación del Usuario y Registro de Fojas`:
   Compara lo que el usuario refiere que se le informó verbalmente ("ya pedí sentencia", "ya apelé",
   "ya libré el oficio", "ya salió el embargo") contra las constancias reales obrantes entre la
   primera y la última foja útil del expediente.
2. `[ISO-PROC-02] Métrica Aritmética de Inactividad Procesal y Alerta Preventiva de Caducidad`:
   Calcula los días corridos y hábiles judiciales estimados desde el último acto impulsorio,
   detecta brechas de silencio >= 45 días entre fojas y evalúa el riesgo frente a los plazos
   de caducidad de cada provincia (`provinces_config.json`).
3. Detector de Contradicciones e Inconsistencias entre Fojas (Actas, Declaraciones y Pericias):
   Cruza horarios, fechas y manifestaciones entre distintas fojas del mismo expediente (ej. acta
   policial en Foja 1 vs declaración testimonial/indagatoria en Foja posterior) para resguardar
   la coherencia probatoria.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Literal

from auditor_revision.compliance_shield import ComplianceShield
from auditor_revision.emergency_scanner import EmergencyScanner
from core.models import (
    AuditReviewResult,
    CaseContext,
    ContradictionFinding,
    FojaPageContent,
    FueroAnalysisResult,
)


class LawyerWatchdogAuditor:
    """Agente de Segunda Derivación para Auditoría de Trazabilidad Documental e Inactividad Procesal."""

    # Mapa de afirmaciones verbales frecuentes -> términos procesales que deben obrar en las fojas reales
    _VERBAL_CLAIM_VERIFICATION_MAP: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
        (
            ("para sentencia", "listo para sentencia", "esperando sentencia", "pasó a sentencia", "pidió sentencia"),
            ("autos para sentencia", "llamamiento de autos", "pase a resolver", "art. 483", "art. 482"),
            "llamado formal de 'Autos para Sentencia' o clausura del período probatorio",
        ),
        (
            ("apeló", "apelo", "presentó apelación", "presento apelacion", "fue a cámara", "fue a camara"),
            ("interpone recurso de apelaci", "apela", "concesión del recurso", "elévense", "elevacion a camara"),
            "interposición o concesión de Recurso de Apelación",
        ),
        (
            ("libró oficio", "libro oficio", "mandó el oficio", "mando el oficio", "oficio al banco", "oficio al empleador"),
            ("líbrese oficio", "librese oficio", "oficio diligenciado", "constancia de recepción de oficio", "deox"),
            "libramiento o diligenciamiento electrónico de Oficio (DEOX / confronte)",
        ),
        (
            ("salió el embargo", "salio el embargo", "trabó embargo", "trabo embargo", "embargamos"),
            ("trábese embargo", "trabese embargo", "decreta embargo", "embargo preventivo", "embargo ejecutivo"),
            "resolución judicial ordenando o trabando medida de Embargo",
        ),
        (
            ("presentó la demanda", "presento la demanda", "ya iniciamos el juicio", "sorteó la demanda"),
            ("promueve demanda", "inicia demanda", "por presentado", "téngase por parte", "radicación"),
            "escrito de inicio de demanda o primer despacho de radicación del juzgado",
        ),
    )

    _RE_TIME_HHMM = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:hs|horas)?\b", re.IGNORECASE)

    def __init__(self, reference_today: date | None = None) -> None:
        self._reference_today = reference_today

    def _contrast_verbal_vs_fojas(
        self,
        verbal_statement: str,
        useful_fojas: list[FojaPageContent],
        last_documented_date: date | None,
    ) -> list[ContradictionFinding]:
        """Ejecuta [ISO-DOC-01] Contraste Objetivo de Trazabilidad entre Manifestación y Fojas."""
        findings: list[ContradictionFinding] = []
        if not verbal_statement or not useful_fojas:
            return findings

        verbal_low = verbal_statement.lower()
        combined_fojas_low = "\n".join(f.cleaned_text.lower() for f in useful_fojas)
        first_foja = useful_fojas[0].foja_number
        last_foja = useful_fojas[-1].foja_number
        last_dt_str = last_documented_date.isoformat() if last_documented_date else "sin fecha datada"

        for trigger_phrases, required_foja_markers, act_label in self._VERBAL_CLAIM_VERIFICATION_MAP:
            if any(phrase in verbal_low for phrase in trigger_phrases):
                has_evidence = any(marker in combined_fojas_low for marker in required_foja_markers)
                if not has_evidence:
                    raw_desc = (
                        f"Entre las Fojas {first_foja} y {last_foja} del documento auditado no obra constancia "
                        f"documental de {act_label} referido en la consulta; la última actuación verificada "
                        f"se registra en la Foja {last_foja} (fecha registrada: {last_dt_str})."
                    )
                    masked_desc = ComplianceShield.apply_legal_mask([raw_desc], iso_category="CONTRASTE_FOJAS")[0]
                    findings.append(
                        ContradictionFinding(
                            foja_a=last_foja,
                            foja_b=None,
                            contradiction_type="RELATO_ABOGADO_VS_EXPEDIENTE",
                            objective_description=masked_desc,
                        )
                    )

        return findings

    def _detect_cross_foja_contradictions(
        self,
        useful_fojas: list[FojaPageContent],
        dates_by_foja: dict[int, str],
    ) -> list[ContradictionFinding]:
        """Detecta inconsistencias objetivas de fechas, horarios o pruebas entre distintas fojas."""
        findings: list[ContradictionFinding] = []
        if len(useful_fojas) < 2:
            return findings

        # 1. Detectar retrocesos cronológicos incompatibles (ej. Foja posterior con fecha de actuación anterior a un proveído previo)
        ordered_fojas = [f.foja_number for f in useful_fojas if f.foja_number in dates_by_foja]
        for idx in range(1, len(ordered_fojas)):
            f_prev = ordered_fojas[idx - 1]
            f_curr = ordered_fojas[idx]
            dt_prev = date.fromisoformat(dates_by_foja[f_prev])
            dt_curr = date.fromisoformat(dates_by_foja[f_curr])
            if dt_curr < dt_prev and (dt_prev - dt_curr).days > 30:
                raw_desc = (
                    f"Secuencia temporal invertida verificada: la Foja {f_prev} registra fecha {dt_prev.isoformat()}, "
                    f"mientras que la Foja posterior {f_curr} consigna una fecha anterior ({dt_curr.isoformat()})."
                )
                masked_desc = ComplianceShield.apply_legal_mask([raw_desc], iso_category="CONTRASTE_FOJAS")[0]
                findings.append(
                    ContradictionFinding(
                        foja_a=f_prev,
                        foja_b=f_curr,
                        contradiction_type="FECHAS_INCOMPATIBLES",
                        objective_description=masked_desc,
                    )
                )

        # 2. Detectar discrepancias horarias en actas de procedimiento vs declaraciones posteriores
        foja_hours: list[tuple[int, str]] = []
        for foja in useful_fojas:
            low = foja.cleaned_text.lower()
            if any(w in low for w in ("acta", "procedimiento", "declaraci", "testigo", "indagatoria")):
                match_h = self._RE_TIME_HHMM.search(foja.cleaned_text)
                if match_h:
                    hhmm = f"{int(match_h.group(1)):02d}:{match_h.group(2)}"
                    foja_hours.append((foja.foja_number, hhmm))

        if len(foja_hours) >= 2 and foja_hours[0][1] != foja_hours[-1][1]:
            f1, h1 = foja_hours[0]
            f2, h2 = foja_hours[-1]
            raw_desc = (
                f"Divergencia horaria en actas/declaraciones: en Foja {f1} se consigna la hora {h1} hs, "
                f"mientras que en Foja {f2} se registra la hora {h2} hs."
            )
            masked_desc = ComplianceShield.apply_legal_mask([raw_desc], iso_category="CONTRASTE_FOJAS")[0]
            findings.append(
                ContradictionFinding(
                    foja_a=f1,
                    foja_b=f2,
                    contradiction_type="FECHAS_INCOMPATIBLES",
                    objective_description=masked_desc,
                )
            )

        return findings

    def audit_case(
        self,
        context: CaseContext,
        fuero_result: FueroAnalysisResult,
        province_rules: dict[str, object],
        gratuity_rules: dict[str, object],
    ) -> AuditReviewResult:
        """Ejecuta la auditoría completa de Segunda Derivación devolviendo AuditReviewResult con ISO-Compliance."""
        # Validación Anti-Prompt Injection sobre el relato del usuario
        ComplianceShield.assert_safe_input(context.user_verbal_statement, "user_verbal_statement")

        today = self._reference_today or datetime.now(timezone.utc).date()
        useful_fojas = [f for f in context.fojas_content if not f.is_discarded_hygiene]
        if not useful_fojas and context.fojas_content:
            useful_fojas = [context.fojas_content[0]]

        # 1. [ISO-PROC-02] Métrica Aritmética de Inactividad Procesal y Caducidad
        if fuero_result.last_documented_act_date is not None:
            days_inactive = max(0, (today - fuero_result.last_documented_act_date).days)
        else:
            days_inactive = 0

        raw_dead_periods: list[str] = []
        sorted_foja_dates = sorted(
            ((f_num, date.fromisoformat(d_str)) for f_num, d_str in fuero_result.detected_dates_by_foja.items()),
            key=lambda x: x[1],
        )
        for idx in range(1, len(sorted_foja_dates)):
            prev_foja, prev_dt = sorted_foja_dates[idx - 1]
            curr_foja, curr_dt = sorted_foja_dates[idx]
            gap_days = (curr_dt - prev_dt).days
            if gap_days >= 45:
                raw_dead_periods.append(
                    f"Intervalo aritmético de {gap_days} días corridos sin actuaciones intermedias registradas "
                    f"entre Foja {prev_foja} ({prev_dt.isoformat()}) y Foja {curr_foja} ({curr_dt.isoformat()})."
                )
        if days_inactive >= 45 and fuero_result.last_documented_act_date is not None:
            last_f = fuero_result.foja_citations[-1].foja_ref
            raw_dead_periods.append(
                f"Alerta temprana de 45+ días: transcurrieron {days_inactive} días corridos desde la última "
                f"actuación fechada en Foja {last_f} ({fuero_result.last_documented_act_date.isoformat()})."
            )

        masked_dead_periods = ComplianceShield.apply_legal_mask(
            raw_dead_periods,
            iso_category="METRICA_INACTIVIDAD",
        )

        caducidad_map = province_rules.get("caducidad_instancia_months", {})
        caducidad_months: int | None = None
        if isinstance(caducidad_map, dict):
            raw_m = caducidad_map.get(context.detected_fuero.value)
            if isinstance(raw_m, int):
                caducidad_months = raw_m

        caducidad_notes = str(province_rules.get("caducidad_notes", ""))
        iso_proc_header = ComplianceShield.ISO_MODULE_HEADERS["METRICA_INACTIVIDAD"]

        if caducidad_months is None:
            risk_level: Literal["BAJO", "MODERADO", "ALTO_CRITICO", "NO_APLICA_IMPULSO_OFICIO"] = (
                "NO_APLICA_IMPULSO_OFICIO"
            )
            cad_explanation = (
                f"{iso_proc_header}: En el área {context.detected_fuero.value} ({context.province_jurisdiction.value}) "
                f"opera el impulso procesal de oficio ({caducidad_notes}). Días corridos desde el último registro "
                f"documental: {days_inactive}."
            )
        else:
            threshold_days = caducidad_months * 30
            if days_inactive >= int(threshold_days * 0.75):
                risk_level = "ALTO_CRITICO"
            elif days_inactive >= 45 or days_inactive >= int(threshold_days * 0.40):
                risk_level = "MODERADO"
            else:
                risk_level = "BAJO"

            cad_explanation = (
                f"{iso_proc_header}: El plazo estatutario de caducidad de instancia en {context.province_jurisdiction.value} "
                f"para {context.detected_fuero.value} es de {caducidad_months} meses (~{threshold_days} días corridos). "
                f"Métrica actual desde la última foja fechada: {days_inactive} días corridos. {caducidad_notes}"
            )

        # 2. [ISO-DOC-01] Contraste Dicho vs. Foja + Contradicciones entre Fojas
        contradictions: list[ContradictionFinding] = []
        contradictions.extend(
            self._contrast_verbal_vs_fojas(
                verbal_statement=context.user_verbal_statement,
                useful_fojas=useful_fojas,
                last_documented_date=fuero_result.last_documented_act_date,
            )
        )
        contradictions.extend(
            self._detect_cross_foja_contradictions(
                useful_fojas=useful_fojas,
                dates_by_foja=fuero_result.detected_dates_by_foja,
            )
        )

        # 3. [ISO-ARAN-03] Verificador de Exenciones Legales Estatutarias y Escalas Arancelarias
        combined_text = "\n".join(f.cleaned_text for f in useful_fojas) + f"\n{context.user_verbal_statement}"
        gratuity_alert = EmergencyScanner.verify_statutory_exemptions_and_fees(
            fuero=context.detected_fuero,
            claimed_fee_pct=context.claimed_lawyer_fee_pct,
            upfront_cost_ars=context.upfront_cost_requested_ars,
            verbal_or_doc_text=combined_text,
            gratuity_config=gratuity_rules,
        )

        # 4. [ISO-PREV-04] Detector Preventivo de Actos Dispositivos ("¡No Firmes Todavía!")
        dispositive_alerts = EmergencyScanner.scan_for_dispositive_acts(
            text_corpus=combined_text,
            fuero=context.detected_fuero,
            claimed_fee_pct=context.claimed_lawyer_fee_pct,
        )
        do_not_sign = len(dispositive_alerts) > 0
        do_not_sign_reason = (
            " | ".join(
                f"{a.citizen_plain_warning} [{a.protective_norm_article}]" for a in dispositive_alerts
            )
            if dispositive_alerts
            else None
        )

        # 5. Preguntas objetivas respaldadas por número exacto de foja y fecha
        last_foja_num = fuero_result.foja_citations[-1].foja_ref
        last_dt_label = (
            fuero_result.last_documented_act_date.isoformat()
            if fuero_result.last_documented_act_date
            else "fecha pendiente de datación"
        )
        questions: list[str] = [
            f"1. Según la copia auditada hasta la Foja {last_foja_num} ({last_dt_label}), ¿cuál es el próximo escrito impulsorio previsto en el sistema {province_rules.get('electronic_system', 'judicial')}?",
            f"2. Considerando el régimen de notificación automática en días de nota ({', '.join(province_rules.get('dias_de_nota', ['MARTES', 'VIERNES'])) if isinstance(province_rules.get('dias_de_nota'), list) else 'martes y viernes'}), ¿existe algún traslado o cédula electrónica reciente pendiente de contestación?",
        ]
        if contradictions:
            questions.append(
                f"3. En relación con el estado conversado, ¿podría facilitarme el cargo electrónico de presentación posterior a la Foja {last_foja_num} para incorporarlo a mi carpeta?"
            )
        if days_inactive >= 45:
            questions.append(
                f"4. Dado que se verifican {days_inactive} días corridos desde la actuación de Foja {last_foja_num}, ¿resulta conveniente presentar un escrito impulsorio preventivo para resguardar el plazo procesal?"
            )
        if gratuity_alert:
            questions.append(
                "5. Conforme al régimen de exenciones legales aplicable a este fuero, ¿podría detallarme por escrito la imputación arancelaria o pericial del concepto mencionado?"
            )
        if do_not_sign:
            questions.append(
                "6. Antes de suscribir el instrumento proyectado, ¿podemos revisar el alcance de las cláusulas dispositivas o de renuncia identificadas?"
            )

        return AuditReviewResult(
            days_since_last_impulse=days_inactive,
            dead_time_periods_detected=masked_dead_periods,
            caducidad_risk_level=risk_level,
            caducidad_explanation=cad_explanation,
            contradictions_detected=contradictions,
            gratuity_illegal_cost_alert=gratuity_alert,
            do_not_sign_yet_banner=do_not_sign,
            do_not_sign_reason=do_not_sign_reason,
            suggested_questions_for_lawyer=questions,
        )
