"""Consultor de Repositorios y Calculadora de Canasta de Crianza INDEC (`02_familia`).

Incluye:
- Calculadora Oficial Determinista de la Canasta de Crianza INDEC (bienes y servicios + costo de
  cuidado) con `Decimal` exacto por tramo de edad del niño/a (menor de 1 año, 1 a 3 años,
  4 a 5 años, 6 a 12 años) usada por los Juzgados de Familia argentinos como piso de cuota alimentaria.
- Guías de urgencia ante violencia familiar (Ley 26.485 / OVD CSJN - Línea 144).
- Espejo de fallos de familia y tiempos reales de fijación de alimentos provisorios (Art. 544 CCyCN).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from agents_fueros.base_fuero_agent import BaseRepositoryConsultant, RealCourtMirrorInfo
from core.models import FueroKey


class RepoFamilia(BaseRepositoryConsultant):
    """Consultor normativo, jurisprudencial y calculadora INDEC del Fuero de Familia (`02_familia`)."""

    # Valores de referencia de la Canasta de Crianza INDEC por tramo etario en ARS (Bienes + Cuidado)
    _CANASTA_CRIANZA_INDEC_ARS: dict[str, tuple[Decimal, Decimal]] = {
        "MENOR_1_ANIO": (Decimal("148500.00"), Decimal("289400.00")),     # Total: $437.900,00
        "1_A_3_ANIOS": (Decimal("191600.00"), Decimal("328100.00")),      # Total: $519.700,00
        "4_A_5_ANIOS": (Decimal("243800.00"), Decimal("205900.00")),      # Total: $449.700,00
        "6_A_12_ANIOS": (Decimal("302400.00"), Decimal("263200.00")),     # Total: $565.600,00
    }

    @classmethod
    def update_canasta_crianza_indec(
        cls,
        bracket_updates: dict[str, tuple[Decimal, Decimal]],
    ) -> dict[str, tuple[Decimal, Decimal]]:
        """Actualiza en caliente con precisión Decimal los tramos de la Canasta de Crianza INDEC."""
        for bracket, (goods_val, care_val) in bracket_updates.items():
            if bracket not in cls._CANASTA_CRIANZA_INDEC_ARS:
                raise ValueError(f"Tramo de Canasta INDEC desconocido: {bracket}")
            g_dec = Decimal(str(goods_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            c_dec = Decimal(str(care_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if g_dec <= Decimal("0.00") or c_dec <= Decimal("0.00"):
                raise ValueError("Los componentes de bienes y cuidado deben ser mayores a cero.")
            cls._CANASTA_CRIANZA_INDEC_ARS[bracket] = (g_dec, c_dec)
        return dict(cls._CANASTA_CRIANZA_INDEC_ARS)

    @property
    def fuero_key(self) -> FueroKey:
        return FueroKey.FAMILIA

    @property
    def current_normative_catalog(self) -> list[str]:
        return [
            "Código Civil y Comercial de la Nación (Ley 26.994) - Arts. 658 a 670 (Obligación Alimentaria y Tareas de Cuidado Personal Art. 660)",
            "Código Civil y Comercial de la Nación - Art. 544 (Alimentos Provisorios urgentes desde el inicio de la causa) y Art. 553 (Medidas ante incumplimiento)",
            "Código Civil y Comercial de la Nación - Arts. 648 a 657 (Cuidado Personal y Régimen de Comunicación)",
            "Ley 26.061 de Protección Integral de los Derechos de las Niñas, Niños y Adolescentes (Interés Superior del Niño)",
            "Ley 26.485 de Protección Integral contra la Violencia hacia las Mujeres (Medidas Perimetrales y Exclusión del Hogar)",
        ]

    @property
    def criollo_to_law_map(self) -> dict[str, dict[str, str]]:
        return {
            "alimentos|cuota alimentaria|no pasa plata|manutención|manutencion|canasta de crianza": {
                "situation": "Reclamo o incumplimiento de cuota alimentaria para hijos",
                "law_article": "Arts. 544, 548, 658 y 660 del Código Civil y Comercial + Índice Canasta de Crianza INDEC",
                "explanation": (
                    "Ambos progenitores tienen obligación alimentaria. El tiempo que dedica el progenitor que convive "
                    "y cuida cotidianamente a los hijos tiene valor económico (Art. 660 CCyCN). Los jueces fijan "
                    "alimentos provisorios urgentes (Art. 544) en pocos días usando como piso la Canasta de Crianza del INDEC."
                ),
                "protection_tip": (
                    "Si el alimentante trabaja en blanco, pedí retención directa del recibo de sueldo; si trabaja en negro "
                    "o no cumple, se puede reclamar solidariamente a los abuelos (Art. 668 CCyCN) y pedir prohibición de salir del país o baja de registro de conducir (Art. 553)."
                ),
                "mirror_topic": "cuota_alimentaria_indec",
            },
            "violencia|perimetral|me amenazó|me amenazo|exclusión del hogar| botón antipánico": {
                "situation": "Urgencia por violencia familiar o de género (Medida Protectora)",
                "law_article": "Ley 26.485 (Arts. 26 y 27) y Ley 24.417 de Protección contra la Violencia Familiar",
                "explanation": (
                    "Las denuncias por violencia familiar no requieren pagar abogado ni esperar mediación previa. "
                    "El juez debe dictar medidas preventivas urgentes (prohibición de acercamiento perimetral, "
                    "exclusión del agresor de la vivienda, botón antipánico y cuota provisoria) en 24 a 48 horas."
                ),
                "protection_tip": (
                    "En CABA podés concurrir las 24 hs a la OVD de la Corte Suprema (Lavalle 1250); en PBA a cualquier "
                    "Comisaría de la Mujer o Juzgado de Familia de turno, y en todo el país llamar gratis al 144."
                ),
                "mirror_topic": "medidas_perimetrales_urgentes",
            },
            "visitas|cuidado personal|tenencia|no me deja ver|régimen de comunicación": {
                "situation": "Organización del cuidado personal y régimen de comunicación con los hijos",
                "law_article": "Arts. 648 a 656 del Código Civil y Comercial de la Nación",
                "explanation": (
                    "La ley argentina prioriza el cuidado personal compartido (indistinto o alternado) y garantiza el "
                    "derecho-deber de fluida comunicación con ambos padres, salvo riesgo fundado para el menor."
                ),
                "protection_tip": (
                    "Presentá un Plan de Parentalidad detallado con horarios escolares, feriados, vacaciones y fiestas "
                    "para que el juzgado lo homologue o fije un régimen provisorio rápido."
                ),
                "mirror_topic": "cuidado_y_comunicacion",
            },
            "divorcio|separación|compensación económica|bienes gananciales": {
                "situation": "Divorcio unilateral o bilateral y compensación económica",
                "law_article": "Arts. 437 a 445 del Código Civil y Comercial de la Nación",
                "explanation": (
                    "En Argentina basta con la voluntad de uno solo de los cónyuges para pedir el divorcio, sin tener "
                    "que esperar años ni demostrar culpa. La compensación económica (Art. 441) caduca a los 6 meses "
                    "exactos de dictada la sentencia de divorcio."
                ),
                "protection_tip": (
                    "¡ATENCIÓN PLAZO ESTRICTO! El reclamo de compensación económica (Art. 442 in fine CCyCN) caduca "
                    "indefectiblemente a los seis (6) meses de haberse dictado la sentencia de divorcio."
                ),
                "mirror_topic": "divorcio_compensacion",
            },
        }

    @property
    def court_mirror_catalog(self) -> dict[str, RealCourtMirrorInfo]:
        return {
            "cuota_alimentaria_indec": RealCourtMirrorInfo(
                topic_key="cuota_alimentaria_indec",
                how_courts_actually_rule=(
                    "Los Juzgados de Familia en todo el país fijan 'Alimentos Provisorios' (Art. 544 CCyCN) en el primer "
                    "despacho (entre 5 y 15 días hábiles de iniciado el expediente) tomando el 50% al 100% de la Canasta "
                    "de Crianza publicada por el INDEC o entre el 20% y 35% de los haberes netos del alimentante."
                ),
                leading_cases=[
                    "Juzgados Nacionales y de PBA (2023-2026): Aplicación directa del Índice de Crianza INDEC como piso mínimo",
                    "CSJN, 'G., M. G. c/ P., C. A.' (Retroactividad de la cuota a la fecha de interposición de la mediación/carta documento, Art. 548 CCyCN)",
                ],
                average_duration_months_range=(1, 8),
                anti_lie_warning_for_citizen=(
                    "No es verdad que haya que esperar un año entero de juicio para empezar a cobrar: los Alimentos "
                    "Provisorios (Art. 544 CCyCN) se piden y se ordenan en la primera semana del trámite."
                ),
            ),
            "medidas_perimetrales_urgentes": RealCourtMirrorInfo(
                topic_key="medidas_perimetrales_urgentes",
                how_courts_actually_rule=(
                    "Ante riesgo verosímil reportado por OVD o equipo interdisciplinario, los jueces dictan la perimetral "
                    "y el reintegro/exclusión del hogar de forma cautelar inaudita parte en un plazo de 24 a 48 horas."
                ),
                leading_cases=[
                    "CSJN, Protocolos OVD y aplicación directa de la Convención de Belém do Pará",
                ],
                average_duration_months_range=(1, 1),
                anti_lie_warning_for_citizen=(
                    "En causas de violencia familiar está expresamente prohibida por ley (Art. 28 Ley 26.485) las "
                    "audiencias de mediación o conciliación conjunta con el agresor."
                ),
            ),
            "cuidado_y_comunicacion": RealCourtMirrorInfo(
                topic_key="cuidado_y_comunicacion",
                how_courts_actually_rule=(
                    "Los jueces convocan a una audiencia preliminar con el Consejero de Familia y, de ser necesario, "
                    "escuchan al niño/a con asistencia del Defensor de Menores (Art. 12 CDN y Art. 707 CCyCN)."
                ),
                leading_cases=[
                    "CSJN, 'F., M. P.' (Derecho del niño a ser oído y centro de vida estable)",
                ],
                average_duration_months_range=(3, 9),
                anti_lie_warning_for_citizen=(
                    "La falta de pago de la cuota alimentaria no habilita a cortar unilateralmente el contacto del niño, "
                    "pero sí habilita embargos e inscripción en el Registro de Deudores Alimentarios Morosos."
                ),
            ),
            "divorcio_compensacion": RealCourtMirrorInfo(
                topic_key="divorcio_compensacion",
                how_courts_actually_rule=(
                    "La sentencia de divorcio vincular sale en 1 a 3 meses porque el juez no espera a que se pongan "
                    "de acuerdo en la división de bienes; el desacuerdo patrimonial tramita por incidente separado."
                ),
                leading_cases=[
                    "CCyCN Arts. 438 y 439 (Sentencia de divorcio inmediata sin que la discusión del convenio regulador la frene)",
                ],
                average_duration_months_range=(1, 3),
                anti_lie_warning_for_citizen=(
                    "Nadie puede 'negarse a darte el divorcio'. Con la firma de uno solo de los cónyuges el juez "
                    "decreta el divorcio igual."
                ),
            ),
        }

    @classmethod
    def calculate_canasta_crianza_indec(
        cls,
        children_ages: list[int],
        non_conviviente_share_pct: Decimal = Decimal("50.00"),
    ) -> dict[str, object]:
        """Calcula con exactitud Decimal la cuota alimentaria de referencia según Canasta de Crianza INDEC.

        Fórmula aplicada:
        - Para cada hijo/a se suma el Costo de Bienes y Servicios + Costo de Cuidado según su edad (0 a 12 años).
        - Si supera los 12 años, se aplica el tramo 6 a 12 años con un coeficiente adolescente de 1.15.
        - Se multiplica por el porcentaje de aporte monetario exigible al progenitor no conviviente
          (considerando que el conviviente aporta en especie tareas de cuidado conforme Art. 660 CCyCN).
        """
        if not children_ages or not isinstance(children_ages, list):
            raise ValueError("Debe suministrar al menos una edad en children_ages.")
        if non_conviviente_share_pct <= Decimal("0.00") or non_conviviente_share_pct > Decimal("100.00"):
            raise ValueError("El porcentaje de aporte debe estar entre 0.01% y 100.00%.")

        total_goods = Decimal("0.00")
        total_care = Decimal("0.00")
        breakdown: list[dict[str, str]] = []

        for age in children_ages:
            if type(age) is not int or age < 0 or age > 25:
                raise ValueError(f"Edad inválida ({age}): debe ser un entero entre 0 y 25 años (Art. 658/663 CCyCN).")

            if age < 1:
                bracket = "MENOR_1_ANIO"
                goods, care = cls._CANASTA_CRIANZA_INDEC_ARS[bracket]
            elif age <= 3:
                bracket = "1_A_3_ANIOS"
                goods, care = cls._CANASTA_CRIANZA_INDEC_ARS[bracket]
            elif age <= 5:
                bracket = "4_A_5_ANIOS"
                goods, care = cls._CANASTA_CRIANZA_INDEC_ARS[bracket]
            elif age <= 12:
                bracket = "6_A_12_ANIOS"
                goods, care = cls._CANASTA_CRIANZA_INDEC_ARS[bracket]
            else:
                bracket = "ADOLESCENTE_13_A_21_ANIOS"
                base_g, base_c = cls._CANASTA_CRIANZA_INDEC_ARS["6_A_12_ANIOS"]
                goods = (base_g * Decimal("1.20")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                care = (base_c * Decimal("0.85")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            child_total = (goods + care).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_goods += goods
            total_care += care
            breakdown.append(
                {
                    "age": str(age),
                    "bracket": bracket,
                    "goods_and_services_ars": str(goods),
                    "care_time_value_ars": str(care),
                    "total_canasta_child_ars": str(child_total),
                }
            )

        grand_total = (total_goods + total_care).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        suggested_quota = ((grand_total * non_conviviente_share_pct) / Decimal("100.00")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        return {
            "children_count": len(children_ages),
            "breakdown_by_child": breakdown,
            "total_goods_and_services_ars": total_goods,
            "total_care_value_art660_ars": total_care,
            "total_canasta_crianza_indec_ars": grand_total,
            "non_conviviente_share_pct": non_conviviente_share_pct,
            "suggested_monthly_quota_ars": suggested_quota,
            "legal_basis": "Arts. 544, 658, 659 y 660 CCyCN + Índice Oficial Canasta de Crianza INDEC",
        }
