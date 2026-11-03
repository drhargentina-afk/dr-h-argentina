"""Constructor del Informe Final PDF en Memoria RAM (`io.BytesIO`), Guion de Audio 90s y Plantilla WhatsApp (`report_builder.py`).

Construye los 3 entregables finales del ciudadano bajo estándar ISO-Compliance:
1. PDF Real en Memoria RAM (`build_pdf_in_ram`):
   Utiliza `fitz` (PyMuPDF) sobre un buffer `io.BytesIO` sin tocar el disco local del servidor,
   maquetando el semáforo de estado, la alerta "¡No Firmes Todavía!" (si aplica), la línea de
   tiempo foja por foja, los hallazgos de auditoría ISO-Compliance y el listado de preguntas
   objetivas para el profesional matriculado.
2. Guion de Audio-Resumen de 90 Segundos (`build_audio_script_90s`):
   Texto estructurado en tono claro, empático y en español rioplatense (~210 palabras) listo
   para ser sintetizado en un audio estilo WhatsApp de 90 segundos.
3. Mensaje Firme de WhatsApp para el Abogado (`build_whatsapp_template`):
   Texto respetuoso pero técnicamente preciso, citando número exacto de foja y fecha verificada
   para que el profesional advierta que el ciudadano lleva control documental de su causa.
"""
from __future__ import annotations

import io
import textwrap

import fitz  # PyMuPDF

from auditor_revision.compliance_shield import ComplianceShield
from core.models import AuditReviewResult, FinalCitizenReport, FojaCitation, FueroKey, ProvinceKey


class CitizenReportBuilder:
    """Constructor de reportes PDF en RAM, guiones de audio de 90s y plantillas de WhatsApp."""

    @staticmethod
    def build_audio_script_90s(
        caratula: str,
        fuero: FueroKey,
        province: ProvinceKey,
        total_pages: int,
        useful_pages: int,
        citations: list[FojaCitation],
        audit_result: AuditReviewResult,
    ) -> str:
        """Genera un guion de ~90 segundos (190 a 240 palabras) cálido, claro y en criollo argentino."""
        first_foja = citations[0].foja_ref if citations else 1
        last_foja = citations[-1].foja_ref if citations else total_pages
        last_piece = citations[-1].document_piece_type if citations else "actuación procesal"

        urgency_segment = ""
        if audit_result.do_not_sign_yet_banner:
            urgency_segment = (
                "Atención muy importante antes de seguir: nuestro detector preventivo identificó en el texto "
                "cláusulas de renuncia de derechos o recibo cancelatorio total. Por favor, no firmes ningún "
                "papel ni acuerdo sin antes revisarlo punto por punto con un profesional o en la defensoría pública. "
            )

        inactivity_segment = (
            f"En cuanto al movimiento del expediente, la métrica aritmética registra {audit_result.days_since_last_impulse} "
            f"días corridos desde la última fecha datada en la Foja {last_foja}, con un nivel de alerta {audit_result.caducidad_risk_level}. "
        )

        contrast_segment = ""
        if audit_result.contradictions_detected:
            contrast_segment = (
                f"Además, al contrastar lo que te comentaron con las fojas subidas, vimos que hasta la Foja {last_foja} "
                f"todavía no figura cargada esa presentación, por lo que te preparamos la consulta exacta para pedir el comprobante electrónico. "
            )

        gratuity_segment = ""
        if audit_result.gratuity_illegal_cost_alert:
            gratuity_segment = (
                "Recordá también que en este tipo de reclamo te ampara el beneficio legal de gratuidad, por lo que "
                "siempre debés pedir detalle escrito y recibo oficial ante cualquier pedido de adelanto. "
            )

        script = (
            f"Hola, ¿cómo estás? Acabo de terminar la radiografía completa de tu expediente '{caratula}' en Dr. H Argentina. "
            f"Primero que nada, llevate tranquilidad: ya ordenamos las {total_pages} páginas que subiste y filtramos "
            f"las {useful_pages} fojas útiles para explicarte en criollo dónde estás parado. "
            f"Tu causa corresponde al área {fuero.value} y tramita bajo las reglas de {province.value}. "
            f"{urgency_segment}"
            f"El recorrido documentado arranca en la Foja {first_foja} y la última actuación que figura en este archivo "
            f"está en la Foja {last_foja}, que corresponde a un {last_piece}. "
            f"{inactivity_segment}"
            f"{contrast_segment}"
            f"{gratuity_segment}"
            f"Acá abajo en la pantalla ya tenés armadas las preguntas puntuales con número de foja y un mensaje listo "
            f"para copiar y mandarle por WhatsApp a tu abogado. Tu archivo original ya fue borrado de nuestra memoria RAM "
            f"y este informe quedó resguardado en tu propio Google Drive."
        )
        return ComplianceShield.sanitize_iso_text(script)

    @staticmethod
    def build_whatsapp_template(
        caratula: str,
        last_foja_ref: int,
        last_act_date_iso: str | None,
        audit_result: AuditReviewResult,
    ) -> str:
        """Genera el mensaje de WhatsApp respetuoso pero técnicamente preciso citando foja y fecha."""
        date_str = f"de fecha {last_act_date_iso}" if last_act_date_iso else "sin fecha datada en mi copia"
        top_questions = "\n".join(audit_result.suggested_questions_for_lawyer[:3])

        extra_request = ""
        if audit_result.contradictions_detected:
            extra_request = (
                f"\nAsimismo, como en mi copia digitalizada hasta la Foja {last_foja_ref} no visualizo la última "
                f"presentación que conversamos, ¿podría enviarme por acá el PDF del cargo electrónico del portal "
                f"judicial así lo archivo en mi carpeta?"
            )

        msg = (
            f"Estimado/a Dr./Dra., espero que se encuentre muy bien. Estuve organizando mi carpeta digital del "
            f"expediente \"{caratula}\", cuya última actuación en mi copia obra a Foja {last_foja_ref} ({date_str}, "
            f"hace {audit_result.days_since_last_impulse} días corridos).\n\n"
            f"Para quedarme tranquilo/a con los plazos y días de nota del juzgado, quería consultarle puntualmente:\n"
            f"{top_questions}"
            f"{extra_request}\n\n"
            f"Desde ya muchas gracias por su tiempo, quedo atento/a a su respuesta."
        )
        return ComplianceShield.sanitize_iso_text(msg)

    @classmethod
    def build_pdf_in_ram(cls, report: FinalCitizenReport) -> bytes:
        """Construye un documento PDF real en un buffer `io.BytesIO` en memoria RAM usando PyMuPDF (`fitz`)."""
        pdf_doc = fitz.open()
        try:
            page = pdf_doc.new_page(width=595, height=842)  # A4 estándar
            y_cursor = 45.0
            margin_x = 42.0
            max_width_chars = 88

            def _write_wrapped_block(
                text: str,
                fontsize: float = 10.0,
                color: tuple[float, float, float] = (0.1, 0.1, 0.15),
                line_height: float = 14.0,
            ) -> None:
                nonlocal page, y_cursor
                for paragraph in text.splitlines():
                    wrapped_lines = textwrap.wrap(paragraph, width=max_width_chars) or [""]
                    for wline in wrapped_lines:
                        if y_cursor > 790.0:
                            page = pdf_doc.new_page(width=595, height=842)
                            y_cursor = 45.0
                        page.insert_text(
                            (margin_x, y_cursor),
                            wline,
                            fontsize=fontsize,
                            color=color,
                        )
                        y_cursor += line_height

            # 1. Encabezado Institucional ISO-Compliance
            _write_wrapped_block(
                f"DR. H ARGENTINA (NODO AR) — INFORME DE TRAZABILIDAD DOCUMENTAL ({report.report_id})",
                fontsize=12.0,
                color=(0.05, 0.20, 0.45),
                line_height=18.0,
            )
            _write_wrapped_block(
                f"Fecha de Emisión: {report.generated_at_iso} | Área: {report.fuero.value} | "
                f"Jurisdicción: {report.province_jurisdiction.value} | Nivel: {report.subscription_tier_applied.value}",
                fontsize=8.5,
                color=(0.35, 0.35, 0.35),
                line_height=14.0,
            )
            y_cursor += 6.0

            # 2. Alerta Preventiva "¡No Firmes Todavía!" si está activa
            if report.do_not_sign_yet_alert and report.do_not_sign_alert_box:
                _write_wrapped_block(
                    "=== ALERTA PREVENTIVA DE ACTO DISPOSITIVO [ISO-PREV-04] ===",
                    fontsize=10.5,
                    color=(0.75, 0.05, 0.05),
                    line_height=15.0,
                )
                _write_wrapped_block(
                    report.do_not_sign_alert_box,
                    fontsize=9.5,
                    color=(0.65, 0.05, 0.05),
                    line_height=13.5,
                )
                y_cursor += 6.0

            # 3. Resumen Ciudadano en Lenguaje Claro
            _write_wrapped_block(
                "1. RADIOGRAFÍA DOCUMENTAL EN LENGUAJE CLARO",
                fontsize=11.0,
                color=(0.05, 0.25, 0.40),
                line_height=16.0,
            )
            _write_wrapped_block(report.citizen_plain_summary, fontsize=9.5, line_height=13.5)
            y_cursor += 6.0

            # 4. Línea de Tiempo Verificada Foja por Foja
            _write_wrapped_block(
                "2. LÍNEA DE TIEMPO VERIFICADA POR NÚMERO DE FOJA",
                fontsize=11.0,
                color=(0.05, 0.25, 0.40),
                line_height=16.0,
            )
            for card in report.verified_timeline_cards:
                _write_wrapped_block(
                    f"• [Foja {card.foja_ref}] {card.document_piece_type} ({card.normative_ref})",
                    fontsize=9.5,
                    color=(0.1, 0.15, 0.35),
                    line_height=13.5,
                )
                _write_wrapped_block(
                    f"  En criollo: {card.citizen_plain_translation}",
                    fontsize=9.0,
                    line_height=13.0,
                )
                _write_wrapped_block(
                    f"  Extracto: \"{card.verbatim_excerpt[:140]}\"",
                    fontsize=8.5,
                    color=(0.3, 0.3, 0.3),
                    line_height=13.0,
                )
            y_cursor += 6.0

            # 5. Auditoría de Tiempos, Exenciones y Trazabilidad ISO-Compliance
            _write_wrapped_block(
                "3. AUDITORÍA DE CALIDAD DOCUMENTAL Y MÉTRICA DE TIEMPOS (ISO-COMPLIANCE)",
                fontsize=11.0,
                color=(0.05, 0.25, 0.40),
                line_height=16.0,
            )
            _write_wrapped_block(report.audit_and_deadlines_section, fontsize=9.5, line_height=13.5)
            y_cursor += 6.0

            # 6. Preguntas Sugeridas y Plantilla WhatsApp
            _write_wrapped_block(
                "4. PREGUNTAS SUGERIDAS PARA SU LETRADO MATRICULADO",
                fontsize=11.0,
                color=(0.05, 0.25, 0.40),
                line_height=16.0,
            )
            for q in report.questions_for_your_lawyer:
                _write_wrapped_block(f"  {q}", fontsize=9.5, line_height=13.5)
            y_cursor += 6.0

            _write_wrapped_block(
                "5. MENSAJE LISTO PARA COPIAR Y ENVIAR POR WHATSAPP",
                fontsize=11.0,
                color=(0.05, 0.25, 0.40),
                line_height=16.0,
            )
            _write_wrapped_block(report.whatsapp_ready_message, fontsize=9.0, line_height=13.0)
            y_cursor += 8.0

            # 7. Escudo Legal y Certificación Zero-Storage
            _write_wrapped_block(
                report.legal_shield_disclaimer,
                fontsize=8.0,
                color=(0.35, 0.35, 0.35),
                line_height=11.5,
            )
            _write_wrapped_block(
                report.zero_storage_confirmation,
                fontsize=8.0,
                color=(0.15, 0.40, 0.20),
                line_height=12.0,
            )

            ram_out = io.BytesIO()
            pdf_doc.save(ram_out)
            return ram_out.getvalue()
        finally:
            pdf_doc.close()
