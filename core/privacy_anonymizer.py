"""Anonimizador Bidireccional en Memoria RAM para Datos Sensibles (Ley 25.326 Argentina).

Reemplaza en memoria volátil (RAM) identificadores personales y sensibles de expedientes
judiciales argentinos antes de que Agente000 derive el texto a cualquier modelo LLM:
- CUIL / CUIT (formato XX-XXXXXXXX-X o 11 dígitos con validación matemática módulo 11)
- DNI argentino (puntos o números de 7 a 8 dígitos precedidos o acompañados de contexto)
- CBU bancario / CVU (22 dígitos exactos)
- Dominios / Patentes automotor (Mercosur AA123BB y formato anterior ABC123)
- Correos electrónicos y teléfonos móviles/fijos argentinos
- Domicilios reales y constituidos (Calle/Av./Pasaje + numeración/piso/depto)
- Nombres propios de partes y letrados en carátulas ("APELLIDO, Nombre c/ APELLIDO...")

Al finalizar el procesamiento de Agente000, restaura los valores originales únicamente
en el buffer de salida destinado al Google Drive del ciudadano y destruye el diccionario
en RAM sin dejar rastros en disco.
"""
from __future__ import annotations

import gc
import re
from dataclasses import dataclass, field


@dataclass
class SessionAnonymizerMap:
    """Diccionario bidireccional efímero en RAM para una sesión de análisis única."""

    session_id: str
    token_to_real: dict[str, str] = field(default_factory=dict)
    real_to_token: dict[str, str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    is_wiped: bool = False

    def register_entity(self, category: str, real_value: str) -> str:
        """Asigna o recupera un token determinista dentro de la sesión para un valor sensible."""
        if self.is_wiped:
            raise RuntimeError("El mapa de anonimización de esta sesión ya fue destruido de la RAM.")
        normalized = real_value.strip()
        if not normalized:
            return real_value
        if normalized in self.real_to_token:
            return self.real_to_token[normalized]

        next_idx = self.counters.get(category, 0) + 1
        self.counters[category] = next_idx
        token = f"[{category}_{next_idx}]"
        self.real_to_token[normalized] = token
        self.token_to_real[token] = normalized
        return token

    def wipe_memory(self) -> None:
        """Sobrescribe y vacía los diccionarios en RAM e invoca al recolector de basura."""
        for k in list(self.token_to_real.keys()):
            self.token_to_real[k] = "0" * len(self.token_to_real[k])
        for k in list(self.real_to_token.keys()):
            self.real_to_token[k] = "0"
        self.token_to_real.clear()
        self.real_to_token.clear()
        self.counters.clear()
        self.is_wiped = True
        gc.collect()


class PrivacyAnonymizer:
    """Motor de anonimización y des-anonimización reversible en memoria RAM."""

    # Expresiones regulares especializadas para documentación judicial argentina
    _RE_CUIT_CUIL = re.compile(r"\b(?:20|23|24|27|30|33|34)[-\s]?\d{8}[-\s]?\d\b")
    _RE_CBU = re.compile(r"\b\d{22}\b")
    _RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _RE_PATENTE = re.compile(
        r"\b(?:[A-Z]{2}\s?\d{3}\s?[A-Z]{2}|[A-Z]{3}\s?\d{3})\b"
    )
    _RE_TELEFONO = re.compile(
        r"(?:\+?54\s?(?:9\s?)?)?(?:0?(?:11|2\d{2,3}|3\d{2,3})[\s-]?(?:15[\s-]?)?\d{3,4}[\s-]?\d{4})\b"
    )
    _RE_DNI_CONTEXT = re.compile(
        r"(?:D\.?N\.?I\.?|L\.?E\.?|L\.?C\.?|Matr[ií]cula|Doc\.?\s*N[°ºo]?)\s*[:N°ºo.]*\s*(\d{1,2}\.?\d{3}\.?\d{3})\b",
        re.IGNORECASE,
    )
    _RE_DNI_DOTTED = re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}\b")
    _RE_DOMICILIO = re.compile(
        r"\b(?:domicilio\s+(?:real|constituido|procesal|legal|denunciado)\s+en\s+(?:la\s+)?(?:calle|av\.?|avenida|pasaje|pje\.?|bv\.?|boulevard)?|"
        r"(?:calle|av\.|avenida|pasaje|bv\.)\s+)"
        r"([A-ZÁÉÍÓÚÑ][A-Za-záéíóúñ0-9\s\.]{2,35}\s+N?[°ºo]?\s*\d{1,5}(?:\s*,?\s*(?:Piso|P\.|Dto\.?|Depto\.?|Of\.?)\s*[A-Za-z0-9°º\-]{1,6})?)",
        re.IGNORECASE,
    )
    _RE_CARATULA_PARTES = re.compile(
        r"\b([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ]+){1,4})\s+"
        r"(?:C/|c/|contra|CONTRA)\s+"
        r"([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ0-9\.\s]{2,45}?)"
        r"(?=\s+(?:S/|s/|sobre|SOBRE)\b)"
    )
    _RE_PERSONA_ROL = re.compile(
        r"\b(?:Sr\.|Sra\.|Dr\.|Dra\.|actor[a]?|demandad[oa]|imputad[oa]|menor|causante|perito|letrad[oa])\s+"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñA-ZÁÉÍÓÚÑ]+){1,3})\b"
    )

    # Palabras jurídicas o institucionales que no deben ser confundidas con nombres de personas
    _EXCLUDED_LEGAL_TERMS = frozenset({
        "PODER JUDICIAL",
        "CORTE SUPREMA",
        "ESTADO NACIONAL",
        "PROVINCIA DE",
        "MINISTERIO PUBLICO",
        "FISCALIA GENERAL",
        "JUZGADO NACIONAL",
        "CAMARA NACIONAL",
        "BOLETIN OFICIAL",
        "CODIGO CIVIL",
        "CODIGO PENAL",
        "LEY NACIONAL",
        "BANCO CENTRAL",
        "DERECHOS HUMANOS",
    })

    def __init__(self) -> None:
        self._active_sessions: dict[str, SessionAnonymizerMap] = {}

    def create_session(self, session_id: str) -> SessionAnonymizerMap:
        """Crea un contenedor de anonimización en RAM aislado por session_id."""
        if not session_id or not isinstance(session_id, str) or len(session_id.strip()) < 4:
            raise ValueError("El session_id para anonimización debe tener al menos 4 caracteres.")
        clean_id = session_id.strip()
        session_map = SessionAnonymizerMap(session_id=clean_id)
        self._active_sessions[clean_id] = session_map
        return session_map

    def get_session(self, session_id: str) -> SessionAnonymizerMap:
        """Obtiene el mapa activo en RAM o lanza KeyError descriptivo."""
        if session_id not in self._active_sessions:
            raise KeyError(f"No existe una sesión de anonimización activa en RAM para '{session_id}'.")
        return self._active_sessions[session_id]

    @staticmethod
    def is_valid_cuit_cuil(raw_cuit: str) -> bool:
        """Valida matemáticamente el dígito verificador módulo 11 de un CUIT/CUIL argentino."""
        digits = re.sub(r"\D", "", raw_cuit)
        if len(digits) != 11:
            return False
        weights = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
        total = sum(int(d) * w for d, w in zip(digits[:10], weights))
        remainder = total % 11
        expected = 0 if remainder == 0 else (9 if remainder == 1 else 11 - remainder)
        return int(digits[10]) == expected

    def anonymize_text(
        self,
        text: str,
        session_id: str,
        extra_known_names: list[str] | None = None,
    ) -> str:
        """Reemplaza todos los datos personales sensibles por tokens reversibles en RAM."""
        if not isinstance(text, str):
            raise TypeError("El texto a anonimizar debe ser una cadena str.")
        if not text:
            return ""

        session_map = self._active_sessions.get(session_id) or self.create_session(session_id)
        result = text

        # 0. Nombres conocidos suministrados explícitamente
        if extra_known_names:
            for name in extra_known_names:
                clean_name = name.strip()
                if len(clean_name) >= 3:
                    token = session_map.register_entity("PERSONA", clean_name)
                    result = re.sub(re.escape(clean_name), token, result, flags=re.IGNORECASE)

        # 1. Partes en carátula ("APELLIDO NOMBRE c/ DEMANDADO s/ MATERIA")
        def _replace_caratula(match: re.Match[str]) -> str:
            full_str = match.group(0)
            actor = match.group(1).strip()
            demandado = match.group(2).strip()
            t_actor = session_map.register_entity("PERSONA", actor)
            t_demandado = session_map.register_entity("PERSONA", demandado)
            replaced = full_str.replace(actor, t_actor, 1).replace(demandado, t_demandado, 1)
            return replaced

        result = self._RE_CARATULA_PARTES.sub(_replace_caratula, result)

        # 2. CBU de 22 dígitos (antes que teléfonos o DNI para no fragmentar)
        result = self._RE_CBU.sub(
            lambda m: session_map.register_entity("CBU", m.group(0)),
            result,
        )

        # 3. CUIT / CUIL
        result = self._RE_CUIT_CUIL.sub(
            lambda m: session_map.register_entity("CUIT_CUIL", m.group(0)),
            result,
        )

        # 4. DNI con prefijo contextual ("DNI 30.123.456" o "DNI 30123456")
        def _replace_dni_context(match: re.Match[str]) -> str:
            full = match.group(0)
            num = match.group(1)
            tok = session_map.register_entity("DNI", num)
            return full.replace(num, tok)

        result = self._RE_DNI_CONTEXT.sub(_replace_dni_context, result)

        # 5. DNI formateado con puntos ("32.456.789")
        result = self._RE_DNI_DOTTED.sub(
            lambda m: session_map.register_entity("DNI", m.group(0)),
            result,
        )

        # 6. Emails
        result = self._RE_EMAIL.sub(
            lambda m: session_map.register_entity("EMAIL", m.group(0)),
            result,
        )

        # 7. Domicilios reales o constituidos
        def _replace_domicilio(match: re.Match[str]) -> str:
            full = match.group(0)
            addr = match.group(1).strip()
            tok = session_map.register_entity("DOMICILIO", addr)
            return full.replace(addr, tok)

        result = self._RE_DOMICILIO.sub(_replace_domicilio, result)

        # 8. Personas precedidas por rol o tratamiento ("Sr. Juan Pérez", "Dra. María Gómez")
        def _replace_persona_rol(match: re.Match[str]) -> str:
            full = match.group(0)
            person_name = match.group(1).strip()
            upper_check = person_name.upper()
            if any(term in upper_check for term in self._EXCLUDED_LEGAL_TERMS):
                return full
            tok = session_map.register_entity("PERSONA", person_name)
            return full.replace(person_name, tok)

        result = self._RE_PERSONA_ROL.sub(_replace_persona_rol, result)

        # 9. Patentes / Dominios automotor
        result = self._RE_PATENTE.sub(
            lambda m: session_map.register_entity("PATENTE", m.group(0)),
            result,
        )

        # 10. Teléfonos
        result = self._RE_TELEFONO.sub(
            lambda m: session_map.register_entity("TELEFONO", m.group(0)),
            result,
        )

        return result

    def deanonymize_text(self, anonymized_text: str, session_id: str) -> str:
        """Restaura todos los tokens ([PERSONA_1], [DNI_1], etc.) a sus valores reales en RAM."""
        if not isinstance(anonymized_text, str):
            raise TypeError("El texto a des-anonimizar debe ser una cadena str.")
        if not anonymized_text:
            return ""
        session_map = self.get_session(session_id)
        if session_map.is_wiped:
            raise RuntimeError(f"La sesión '{session_id}' ya fue purgada de la memoria RAM.")

        restored = anonymized_text
        # Ordenar tokens por longitud descendente para evitar colisiones parciales ([PERSONA_10] vs [PERSONA_1])
        for token in sorted(session_map.token_to_real.keys(), key=len, reverse=True):
            real_val = session_map.token_to_real[token]
            restored = restored.replace(token, real_val)
        return restored

    def destroy_session(self, session_id: str) -> bool:
        """Destruye definitivamente el mapa de anonimización en RAM para la sesión indicada."""
        session_map = self._active_sessions.pop(session_id, None)
        if session_map is None:
            return False
        session_map.wipe_memory()
        return True
