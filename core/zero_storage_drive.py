"""Gestor Zero-Storage en Memoria RAM desde Google Drive del Usuario + Higiene de Fojas PyMuPDF.

Arquitectura de Privacidad y Eficiencia de Costos:
1. Recibe el stream binario del expediente directamente en un buffer `io.BytesIO` en RAM
   (nunca persiste el expediente del ciudadano en bases de datos propias del servidor).
2. Ejecuta el Pre-Cotizador y Filtro de Higiene de Fojas con `fitz` (PyMuPDF) en <2 segundos
   sin invocar modelos de lenguaje (cero gasto de tokens en cotización):
   - Cuenta las páginas totales y determina el tramo comercial con `Decimal` exacto.
   - Detecta cuáles páginas poseen texto digital nativo y cuáles son imágenes escaneadas
     que requieren OCR selectivo.
   - Descarta páginas basura (hojas en blanco, sellos/firmas digitales de 1 línea o carátulas
     repetidas), conservando intacto el número real de foja (`foja_number`) de cada página útil
     para jamás desfasar las citas jurídicas.
3. Escribe el informe final des-anonimizado en el directorio/contenedor de Google Drive del
   usuario y ejecuta una purga activa de buffers en RAM seguida de `gc.collect()`.
"""
from __future__ import annotations

import gc
import hashlib
import io
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import fitz  # PyMuPDF

from core.models import FojaPageContent, PreQuoteResponse, SubscriptionTier


@dataclass
class VolatileExpedienteSession:
    """Contenedor estrictamente en memoria RAM para un expediente en tránsito."""

    session_id: str
    file_name: str
    pdf_ram_buffer: io.BytesIO
    pre_quote: PreQuoteResponse
    fojas: list[FojaPageContent] = field(default_factory=list)
    is_purged: bool = False

    def purge_ram(self) -> None:
        """Sobrescribe el buffer en memoria RAM, cierra el stream y libera las estructuras."""
        if not self.is_purged:
            try:
                self.pdf_ram_buffer.seek(0)
                size = self.pdf_ram_buffer.getbuffer().nbytes
                if size > 0:
                    self.pdf_ram_buffer.write(b"\x00" * min(size, 65536))
                self.pdf_ram_buffer.truncate(0)
                self.pdf_ram_buffer.close()
            except Exception as exc:
                # Si el buffer ya estaba cerrado, continuar con limpieza de listas
                _ = str(exc)
            for foja in self.fojas:
                foja.cleaned_text = ""
            self.fojas.clear()
            self.is_purged = True
            gc.collect()


class ZeroStorageDriveManager:
    """Administrador de lectura en RAM, pre-cotización PyMuPDF y guardado en Drive del usuario."""

    # Patrones de líneas que son únicamente sellos automáticos o firmas digitales aisladas
    _RE_SINGLE_LINE_STAMP = re.compile(
        r"^(?:firmado\s+digitalmente\s+por|fecha\s+y\s+hora\s+de\s+firma|lex\s*100|scba\s+mev|"
        r"c[oó]digo\s+de\s+verificaci[oó]n|p[aá]gina\s+\d+\s+de\s+\d+|es\s+copia\s+fiel)[\s\S]{0,120}$",
        re.IGNORECASE,
    )

    # Tarifas de referencia en ARS (con precisión Decimal exacta) para el cotizador instantáneo
    _PRICING_TABLE_ARS: dict[SubscriptionTier, tuple[Decimal, Decimal]] = {
        SubscriptionTier.NIVEL_0_GRATUITO: (Decimal("0.00"), Decimal("0.00")),
        SubscriptionTier.PLAN_S_ESTANDAR: (Decimal("14500.00"), Decimal("6900.00")),
        SubscriptionTier.PLAN_M_COMPLEJO: (Decimal("28900.00"), Decimal("11500.00")),
        SubscriptionTier.PLAN_L_VOLUMINOSO: (Decimal("46500.00"), Decimal("16900.00")),
        SubscriptionTier.PACK_EXTRA_FOJAS: (Decimal("68000.00"), Decimal("24500.00")),
    }

    def __init__(self, user_drive_mount_dir: str | Path | None = None) -> None:
        if user_drive_mount_dir is None:
            default_dir = Path(__file__).resolve().parent.parent / "user_google_drive_outbox"
            self._drive_dir = default_dir
        else:
            self._drive_dir = Path(user_drive_mount_dir)
        self._drive_dir.mkdir(parents=True, exist_ok=True)
        self._volatile_sessions: dict[str, VolatileExpedienteSession] = {}

    @staticmethod
    def calculate_subscription_tier(total_pages: int) -> tuple[SubscriptionTier, Decimal, Decimal]:
        """Determina el tramo de abono según la cantidad de fojas/páginas del expediente.

        Fórmula comercial determinista:
        - <= 15 páginas: Nivel 0 Gratuito ($0.00)
        - 16 a 150 páginas: Plan S Estándar
        - 151 a 350 páginas: Plan M Complejo
        - 351 a 600 páginas: Plan L Voluminoso
        - > 600 páginas: Pack Extra Fojas + adicional proporcional por cada bloque de 100 fojas extra.
        """
        if type(total_pages) is not int or total_pages <= 0:
            raise ValueError("total_pages debe ser un entero positivo estricto (>= 1).")

        if total_pages <= 15:
            tier = SubscriptionTier.NIVEL_0_GRATUITO
        elif total_pages <= 150:
            tier = SubscriptionTier.PLAN_S_ESTANDAR
        elif total_pages <= 350:
            tier = SubscriptionTier.PLAN_M_COMPLEJO
        elif total_pages <= 600:
            tier = SubscriptionTier.PLAN_L_VOLUMINOSO
        else:
            tier = SubscriptionTier.PACK_EXTRA_FOJAS

        single_price, monthly_price = ZeroStorageDriveManager._PRICING_TABLE_ARS[tier]
        if total_pages > 600:
            extra_blocks = Decimal((total_pages - 600 + 99) // 100)
            single_price = (single_price + extra_blocks * Decimal("8500.00")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            monthly_price = (monthly_price + extra_blocks * Decimal("2500.00")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        return tier, single_price, monthly_price

    @classmethod
    def _normalize_whitespace(cls, raw_text: str) -> str:
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw_text.splitlines()]
        non_empty = [ln for ln in lines if ln]
        return "\n".join(non_empty)

    def load_and_prequote_in_ram(
        self,
        file_bytes: bytes,
        file_name: str,
        session_id: str | None = None,
    ) -> VolatileExpedienteSession:
        """Carga el PDF en un buffer io.BytesIO en RAM y ejecuta pre-cotización + higiene PyMuPDF."""
        start_ns = time.perf_counter_ns()
        if not isinstance(file_bytes, (bytes, bytearray)) or len(file_bytes) == 0:
            raise ValueError("El archivo recibido desde Google Drive está vacío o es inválido.")
        clean_name = (file_name or "expediente.pdf").strip()
        sid = (session_id or f"drh-{uuid.uuid4().hex[:12]}").strip()

        ram_buffer = io.BytesIO(file_bytes)
        try:
            pdf_doc = fitz.open(stream=ram_buffer.getvalue(), filetype="pdf")
        except Exception as exc:
            ram_buffer.close()
            raise ValueError(f"No se pudo abrir el stream como un documento PDF válido: {exc}") from exc

        try:
            total_pages = pdf_doc.page_count
            if total_pages <= 0:
                raise ValueError("El documento PDF no contiene páginas legibles.")

            fojas_list: list[FojaPageContent] = []
            seen_hashes: set[str] = set()
            useful_count = 0
            discarded_count = 0
            scanned_ocr_count = 0
            native_digital_count = 0
            preserved_foja_numbers: list[int] = []

            for page_idx in range(total_pages):
                foja_num = page_idx + 1  # Numeración real de foja 1-indexada
                page = pdf_doc.load_page(page_idx)
                raw_text = page.get_text("text") or ""
                cleaned = self._normalize_whitespace(raw_text)
                char_len = len(cleaned)
                images = page.get_images(full=False)
                img_count = len(images)

                # Determinación de página escaneada vs digital nativa
                requires_ocr = False
                is_discarded = False
                discard_reason: str | None = None

                if char_len < 25 and img_count > 0:
                    # Página escaneada sin capa de texto (típica cédula o acta fotografiada)
                    requires_ocr = True
                    useful_count += 1
                    scanned_ocr_count += 1
                    preserved_foja_numbers.append(foja_num)
                    cleaned = (
                        f"[FOJA_ESCANEADA_{foja_num}: Detectada imagen judicial ({img_count} objeto/s) "
                        f"marcada para extracción OCR selectiva]"
                    )
                elif char_len == 0 and img_count == 0:
                    # Hoja completamente en blanco
                    is_discarded = True
                    discard_reason = "HOJA_EN_BLANCO"
                    discarded_count += 1
                elif char_len < 130 and self._RE_SINGLE_LINE_STAMP.match(cleaned):
                    # Sello o pie de firma digital aislado de una sola línea
                    is_discarded = True
                    discard_reason = "SELLO_O_FIRMA_AISLADA"
                    discarded_count += 1
                else:
                    # Verificar si es una carátula o página exactamente duplicada dentro del PDF
                    text_fingerprint = hashlib.sha256(cleaned.lower().encode("utf-8")).hexdigest()
                    if char_len > 40 and text_fingerprint in seen_hashes:
                        is_discarded = True
                        discard_reason = "CARATULA_O_FOJA_REPETIDA"
                        discarded_count += 1
                    else:
                        seen_hashes.add(text_fingerprint)
                        useful_count += 1
                        native_digital_count += 1
                        preserved_foja_numbers.append(foja_num)

                fojas_list.append(
                    FojaPageContent(
                        foja_number=foja_num,
                        raw_char_count=char_len,
                        image_count=img_count,
                        requires_ocr=requires_ocr,
                        is_discarded_hygiene=is_discarded,
                        discard_reason=discard_reason,
                        cleaned_text=cleaned,
                    )
                )
        finally:
            pdf_doc.close()

        tier, single_ars, monthly_ars = self.calculate_subscription_tier(total_pages)
        elapsed_ms = max(1, int((time.perf_counter_ns() - start_ns) / 1_000_000))

        pre_quote = PreQuoteResponse(
            session_id=sid,
            file_name=clean_name,
            total_pages=total_pages,
            useful_pages_after_hygiene=useful_count,
            discarded_junk_pages=discarded_count,
            scanned_pages_requiring_ocr=scanned_ocr_count,
            native_digital_pages=native_digital_count,
            suggested_tier=tier,
            qualifies_for_free_tier=(total_pages <= 15),
            estimated_single_radiography_ars=single_ars,
            estimated_monthly_tracking_ars=monthly_ars,
            preserved_foja_index=preserved_foja_numbers,
            processing_time_ms=elapsed_ms,
        )

        volatile_session = VolatileExpedienteSession(
            session_id=sid,
            file_name=clean_name,
            pdf_ram_buffer=ram_buffer,
            pre_quote=pre_quote,
            fojas=fojas_list,
        )
        self._volatile_sessions[sid] = volatile_session
        return volatile_session

    def get_active_session(self, session_id: str) -> VolatileExpedienteSession:
        """Recupera una sesión activa en RAM."""
        if session_id not in self._volatile_sessions:
            raise KeyError(f"La sesión volátil '{session_id}' no existe o ya fue eliminada de la RAM.")
        session = self._volatile_sessions[session_id]
        if session.is_purged:
            raise RuntimeError(f"La sesión '{session_id}' ya fue purgada de la memoria RAM.")
        return session

    def save_report_and_wipe_ram(
        self,
        session_id: str,
        report_payload: dict[str, object],
        pdf_report_bytes: bytes | None = None,
        user_drive_subfolder: str = "Informes_Dr_H",
    ) -> str:
        """Guarda el informe final (JSON y PDF generado en RAM) en el Google Drive del usuario y destruye la RAM."""
        session = self._volatile_sessions.pop(session_id, None)
        try:
            target_folder = self._drive_dir / user_drive_subfolder
            target_folder.mkdir(parents=True, exist_ok=True)
            report_file_name = f"Informe_Dr_H_{session_id}.json"
            report_path = target_folder / report_file_name
            serialized = json.dumps(report_payload, ensure_ascii=False, indent=2, default=str)
            report_path.write_text(serialized, encoding="utf-8")

            if pdf_report_bytes is not None and len(pdf_report_bytes) > 0:
                pdf_file_name = f"Informe_Dr_H_{session_id}.pdf"
                pdf_path = target_folder / pdf_file_name
                pdf_path.write_bytes(pdf_report_bytes)

            return f"gdrive://user/{user_drive_subfolder}/{report_file_name}"
        finally:
            if session is not None:
                session.purge_ram()
            gc.collect()
