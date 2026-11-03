"""Escudo Legal ISO-Compliance, Sanitizador Objetivo y Defensa Anti-Prompt Injection (`compliance_shield.py`).

Garantiza tres barreras de seguridad y cumplimiento normativo para Dr. H Argentina:
1. Defensa Anti-Prompt Injection / Anti-Jailbreak (`detect_prompt_injection`):
   Bloquea intentos de usuarios, terceros o documentos manipulados de extraer instrucciones
   internas del sistema, alterar roles de los agentes o desactivar las validaciones de fojas.
2. Enmascaramiento Técnico ISO-Compliance (`apply_legal_mask` y `sanitize_iso_text`):
   Asegura que ningún texto, log o reporte contenga calificativos subjetivos o imputaciones
   contra profesionales o magistrados, reemplazándolos por terminología neutra de:
   - "Contraste Objetivo de Trazabilidad entre Manifestación del Usuario y Registro de Fojas"
   - "Métrica Aritmética de Inactividad Procesal y Alerta Preventiva de Caducidad"
   - "Verificador de Exenciones Legales Estatutarias y Escalas Arancelarias Oficiales"
   - "Detector Preventivo de Actos Dispositivos, Renuncia de Derechos y Pactos de Cuota Litis"
3. Blindaje Estatutario de Alcance Informativo (`get_statutory_disclaimer`):
   Delimita jurídicamente el servicio conforme a las Leyes 23.187 (CPACF), 5.177 (PBA),
   5.805 (Córdoba) y normas concordantes sobre patrocinio letrado obligatorio.
"""
from __future__ import annotations

import re

from core.models import FueroKey, ProvinceKey


class PromptInjectionBlockedError(ValueError):
    """Excepción lanzada cuando el Escudo ISO-Compliance bloquea un intento de Prompt Injection."""


class ComplianceShield:
    """Motor de blindaje legal ISO-Compliance y filtro de seguridad Anti-Prompt Injection."""

    # Patrones deterministas de ataques de Prompt Injection / Jailbreak (español e inglés)
    _INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(
            r"ignor[aáe]\s+(?:todas\s+)?(?:tus|las)\s+(?:instrucciones|reglas|directrices|restricciones)",
            re.IGNORECASE,
        ),
        re.compile(
            r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+(?:instructions|prompts|rules)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:mostr[aá]me|revel[aá]|imprim[ií]|decime|print|show|reveal)\s+(?:tu|el|las|the)\s*(?:system\s*prompt|prompt\s*interno|instrucciones\s*ocultas|reglas\s*internas(?:\s+ocultas)?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:actu[aá]\s+como\s+DAN|modo\s+desarrollador\s+sin\s+filtros|jailbreak|developer\s+mode\s+enabled)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:olvid[aá]\s+tu\s+rol|forget\s+your\s+role|override\s+compliance\s+shield|desactiv[aá]\s+el\s+escudo)",
            re.IGNORECASE,
        ),
        re.compile(
            r"<\s*(?:system|admin_override|prompt_leak)\s*>",
            re.IGNORECASE,
        ),
    )

    # Mapa de reemplazo obligatorio para garantizar vocabulario técnico ISO-Compliance 100% neutro
    _SUBJECTIVE_TO_ISO_TERMS: tuple[tuple[re.Pattern[str], str], ...] = (
        (
            re.compile(r"\bmentira(?:s)?\s+del\s+abogado\b", re.IGNORECASE),
            "divergencia objetiva entre la manifestación informada y las constancias de fojas",
        ),
        (
            re.compile(r"\babogado\s+mentiroso\b|\bte\s+minti[oó]\b|\best[aá]\s+mintiendo\b", re.IGNORECASE),
            "asimetría informativa respecto del registro documental verificado",
        ),
        (
            re.compile(r"\bmala\s+praxis\b|\bnegligencia\s+del\s+abogado\b|\babogado\s+incompetente\b", re.IGNORECASE),
            "desvío en la métrica de impulso procesal documentado",
        ),
        (
            re.compile(r"\babandono\s+de\s+causa\b|\babandon[oó]\s+el\s+juicio\b", re.IGNORECASE),
            "período de inactividad procesal computable (Art. 310 CPCCN)",
        ),
        (
            re.compile(r"\bestafa\s+del\s+abogado\b|\brobo\s+de\s+honorarios\b|\bcobro\s+ilegal\b|\bgastos\s+falsos\b", re.IGNORECASE),
            "observación de cumplimiento sobre exenciones legales estatutarias y escalas arancelarias (Ley 27.423)",
        ),
        (
            re.compile(r"\bcorrupci[oó]n\b|\bjuez\s+corrupto\b|\bcoima\b|\barreglo\s+espurio\b", re.IGNORECASE),
            "observación de trazabilidad formal y control de legalidad procesal",
        ),
        (
            re.compile(r"\bestudio\s+fantasma\b|\bcobrador\s+extorsivo\b", re.IGNORECASE),
            "gestión de cobro extrajudicial sujeta a verificación de exigibilidad temporal (Art. 2560 CCyCN)",
        ),
    )

    # Encabezados ISO-Compliance estandarizados para los 4 módulos de auditoría
    ISO_MODULE_HEADERS: dict[str, str] = {
        "CONTRASTE_FOJAS": (
            "[ISO-DOC-01] Contraste Objetivo de Trazabilidad entre Manifestación del Usuario y Registro de Fojas"
        ),
        "METRICA_INACTIVIDAD": (
            "[ISO-PROC-02] Métrica Aritmética de Inactividad Procesal y Alerta Preventiva de Caducidad (Art. 310 CPCCN / Códigos Provinciales)"
        ),
        "EXENCIONES_ARANCELES": (
            "[ISO-ARAN-03] Verificador de Exenciones Legales Estatutarias y Escalas Arancelarias Oficiales (Ley 27.423 / Ley 20.744 Art. 20 / Ley 24.240 Art. 53)"
        ),
        "ACTOS_DISPOSITIVOS": (
            "[ISO-PREV-04] Detector Preventivo de Actos Dispositivos, Renuncia de Derechos y Pactos de Cuota Litis"
        ),
    }

    @classmethod
    def detect_prompt_injection(cls, user_input: str) -> bool:
        """Devuelve True si el texto de entrada contiene patrones de Prompt Injection o Jailbreak."""
        if not isinstance(user_input, str):
            raise TypeError("user_input debe ser una cadena de texto str.")
        if not user_input.strip():
            return False

        for pattern in cls._INJECTION_PATTERNS:
            if pattern.search(user_input):
                return True
        return False

    @classmethod
    def assert_safe_input(cls, user_input: str, field_label: str = "entrada del usuario") -> str:
        """Valida que el texto no contenga Prompt Injection; si lo detecta, lanza PromptInjectionBlockedError."""
        if cls.detect_prompt_injection(user_input):
            raise PromptInjectionBlockedError(
                f"Bloqueo de Seguridad ISO-Compliance: Se detectó un patrón de instrucción no autorizada "
                f"(Prompt Injection / Jailbreak) en '{field_label}'. Las directrices internas de trazabilidad "
                f"de Dr. H Argentina son inmutables y confidenciales."
            )
        return user_input

    @classmethod
    def sanitize_iso_text(cls, raw_text: str) -> str:
        """Transforma cualquier expresión subjetiva en terminología técnica-objetiva de trazabilidad."""
        if not isinstance(raw_text, str):
            raise TypeError("raw_text debe ser una cadena de texto str.")
        sanitized = raw_text.strip()
        if not sanitized:
            return ""

        for pattern, replacement in cls._SUBJECTIVE_TO_ISO_TERMS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    @classmethod
    def apply_legal_mask(
        cls,
        raw_findings: list[str],
        iso_category: str = "CONTRASTE_FOJAS",
    ) -> list[str]:
        """Enmascara una lista de hallazgos bajo el estándar técnico ISO-Compliance correspondiente."""
        if not isinstance(raw_findings, list):
            raise TypeError("raw_findings debe ser una lista de cadenas de texto.")

        header = cls.ISO_MODULE_HEADERS.get(
            iso_category.upper(),
            cls.ISO_MODULE_HEADERS["CONTRASTE_FOJAS"],
        )
        masked_list: list[str] = []

        for item in raw_findings:
            if not isinstance(item, str) or not item.strip():
                continue
            clean_item = cls.sanitize_iso_text(item)
            if not clean_item.startswith("[ISO-"):
                clean_item = f"{header}: {clean_item}"
            masked_list.append(clean_item)

        return masked_list

    @classmethod
    def get_statutory_disclaimer(
        cls,
        fuero: FueroKey | str,
        province: ProvinceKey | str,
    ) -> str:
        """Genera el descargo legal estatutario adaptado a la jurisdicción y materia del caso."""
        fuero_str = fuero.value if isinstance(fuero, FueroKey) else str(fuero)
        prov_str = province.value if isinstance(province, ProvinceKey) else str(province)

        bar_laws_by_province: dict[str, str] = {
            ProvinceKey.PJN_NACION_CABA.value: "Ley Nacional N° 23.187 (Colegio Público de la Abogacía de la Capital Federal)",
            ProvinceKey.PBA_MEV_SCBA.value: "Ley Provincial N° 5.177 (Ejercicio y Reglamentación de la Profesión de Abogado en PBA)",
            ProvinceKey.CORDOBA.value: "Ley Provincial N° 5.805 (Colegio de Abogados de la Provincia de Córdoba)",
            ProvinceKey.SANTA_FE.value: "Ley Orgánica del Poder Judicial de Santa Fe N° 10.160 y Estatutos de Colegios de Abogados",
            ProvinceKey.MENDOZA.value: "Ley Provincial N° 4.976 (Ejercicio de la Abogacía y Procuración en Mendoza)",
            ProvinceKey.RESTO_PROVINCIAS.value: "Leyes Provinciales de Colegiación Legal y Patrocinio Letrado Obligatorio",
        }
        applicable_bar_law = bar_laws_by_province.get(
            prov_str,
            bar_laws_by_province[ProvinceKey.PJN_NACION_CABA.value],
        )

        return (
            f"DECLARACIÓN DE CUMPLIMIENTO NORMATIVO Y ALCANCE TÉCNICO (ISO-COMPLIANCE — NODO AR): "
            f"El presente reporte de auditoría documental y métrica aritmética de tiempos procesales "
            f"(Área: {fuero_str} | Jurisdicción: {prov_str}) ha sido elaborado de manera automatizada "
            f"mediante procesamiento de lectura óptica y contraste objetivo sobre las fojas aportadas por "
            f"el titular de los datos (Ley 25.326). Su contenido reviste carácter estrictamente informativo, "
            f"educativo y de organización documental ciudadana. No constituye asesoramiento jurídico vinculante, "
            f"dictamen pericial ni reemplaza bajo ninguna circunstancia el patrocinio letrado obligatorio de un "
            f"abogado matriculado conforme a la {applicable_bar_law}. Las preguntas sugeridas tienen como único "
            f"propósito facilitar el diálogo informado y colaborativo entre el ciudadano y su profesional de confianza."
        )
