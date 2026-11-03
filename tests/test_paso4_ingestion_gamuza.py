"""Suite de Pruebas Unitarias e Integración del Paso 4 (`ingestion_07am/` + Puente a Proyecto Gamuza + Health Watchdog).

Verifica:
1. `OfficialGazetteCollector`: recolección del lote realista de las 07:00 AM (BORA, BCRA, INDEC, SMVM, ARCA, CSJN),
   parseo de feeds XML/RSS oficiales, detección de derogaciones expresas y jerarquía constitucional.
2. `NormativeRepositoryUpdater`: enrutamiento selectivo al Sub-Agente del fuero afectado y actualización
   en caliente con precisión `Decimal` de las calculadoras oficiales:
   - Canasta de Crianza INDEC en `02_familia` (`RepoFamilia.calculate_canasta_crianza_indec`).
   - Salario Mínimo Vital y Móvil (SMVM) y tope de embargo Decreto 484/87 en `05_comercial_consumidor` y `03_laboral`.
   - Índices ICL (BCRA) e IPC (INDEC) en `04_civil`.
3. `GamuzaKnowledgeBridge`: generación de `GamuzaRegulatorySignal` con `market_signal` (`BULLISH` / `BEARISH` / `NEUTRAL`),
   recursos estratégicos (Vaca Muerta, Litio, Agro), sectores GICS, tickers directos de BYMA y NYSE/Wall Street
   (`YPF`, `PAM`, `TGS`, `GGAL`, `BMA`, `CEPU`, `TXAR`, `ALUA`) y bloqueo estricto ante cualquier rastro de expediente privado.
4. `SystemHealthWatchdog` y endpoints `/cron-07am` y `/health` de `main.py`: verificación de memoria RAM limpia,
   timestamp de sincronización 07:00 AM (`is_normative_db_up_to_date`), versión PWA y emisión de alertas administrativas.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import importlib
import unittest

from fastapi.testclient import TestClient

from core.models import BoletinOficialNormInput, FueroKey
from ingestion_07am import (
    GamuzaKnowledgeBridge,
    GamuzaPrivacyViolationError,
    NormativeRepositoryUpdater,
    OfficialGazetteCollector,
)
from main import app, orchestrator

_mod_fam = importlib.import_module("agents_fueros.02_familia.repo_familia")
_mod_lab = importlib.import_module("agents_fueros.03_laboral.repo_laboral")
_mod_civ = importlib.import_module("agents_fueros.04_civil.repo_civil")
_mod_com = importlib.import_module("agents_fueros.05_comercial_consumidor.repo_comercial")

RepoFamilia = _mod_fam.RepoFamilia
RepoLaboral = _mod_lab.RepoLaboral
RepoCivil = _mod_civ.RepoCivil
RepoComercial = _mod_com.RepoComercial


class TestPaso4IngestionAndGamuzaBridge(unittest.TestCase):
    """Pruebas deterministas del módulo ingestion_07am, actualización en caliente y puente hacia Gamuza."""

    def setUp(self) -> None:
        # Guardar instantánea de las constantes económicas iniciales para restaurar tras cada prueba
        self._orig_canasta = dict(RepoFamilia._CANASTA_CRIANZA_INDEC_ARS)
        self._orig_smvm_com = RepoComercial._SMVM_CURRENT_ARS
        self._orig_smvm_lab = RepoLaboral._SMVM_CURRENT_ARS
        self._orig_ripte_lab = RepoLaboral._RIPTE_COEFFICIENT
        self._orig_icl_civ = RepoCivil._LATEST_ICL_BCRA_INDEX
        self._orig_ipc_civ = RepoCivil._LATEST_IPC_INDEC_MONTHLY_PCT

    def tearDown(self) -> None:
        RepoFamilia._CANASTA_CRIANZA_INDEC_ARS = dict(self._orig_canasta)
        RepoComercial._SMVM_CURRENT_ARS = self._orig_smvm_com
        RepoLaboral._SMVM_CURRENT_ARS = self._orig_smvm_lab
        RepoLaboral._RIPTE_COEFFICIENT = self._orig_ripte_lab
        RepoCivil._LATEST_ICL_BCRA_INDEX = self._orig_icl_civ
        RepoCivil._LATEST_IPC_INDEC_MONTHLY_PCT = self._orig_ipc_civ
        orchestrator.system_watchdog.clear_alerts()

    def test_official_gazette_collector_batch_and_rss_xml_parser(self) -> None:
        """Verifica la recolección del lote oficial 07:00 AM y el parser XML/RSS con extracción de derogaciones."""
        batch = OfficialGazetteCollector.get_default_07am_batch(reference_date=date(2026, 11, 3))
        self.assertEqual(len(batch), 6)

        norm_types = {n.norm_type for n in batch}
        self.assertIn("DECRETO_DNU", norm_types)
        self.assertIn("COMUNICACION_BCRA", norm_types)
        self.assertIn("INDICE_INDEC", norm_types)
        self.assertIn("RESOLUCION_SMVM", norm_types)
        self.assertIn("RESOLUCION_ARCA", norm_types)
        self.assertIn("ACORDADA_CSJN", norm_types)

        # Verificar parseo de un feed XML/RSS con derogación expresa en el texto
        sample_rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Boletín Oficial de la República Argentina - Primera Sección</title>
            <item>
              <guid>Ley 27.890</guid>
              <title>Ley Nacional de Promoción de Industrialización del Litio y Electromovilidad</title>
              <description>Establécese el régimen federal de valor agregado para el litio en salares andinos. Derógase la Resolución 412/2019.</description>
            </item>
          </channel>
        </rss>"""
        parsed_rss = OfficialGazetteCollector.parse_rss_xml_feed(
            sample_rss,
            source_organism="BORA",
            reference_date=date(2026, 11, 3),
        )
        self.assertEqual(len(parsed_rss), 1)
        self.assertEqual(parsed_rss[0].norm_id, "Ley 27.890")
        self.assertEqual(parsed_rss[0].norm_type, "LEY")
        self.assertIn("Nivel 2 Pirámide Constitucional", parsed_rss[0].constitutional_hierarchy)
        self.assertTrue(any("412/2019" in r for r in parsed_rss[0].modifies_or_repeals))

    def test_hot_update_of_fuero_calculators_indec_smvm_and_icl(self) -> None:
        """Comprueba que la ingesta 07:00 AM actualice en caliente con Decimal las calculadoras de los fueros."""
        # Antes de la ingesta: hijo de 8 años (tramo 6 a 12 años) -> $302.400 + $263.200 = $565.600
        before_fam = RepoFamilia.calculate_canasta_crianza_indec([8], non_conviviente_share_pct=Decimal("50.00"))
        self.assertEqual(before_fam["total_canasta_crianza_indec_ars"], Decimal("565600.00"))
        self.assertEqual(before_fam["suggested_monthly_quota_ars"], Decimal("282800.00"))

        # Antes de la ingesta: sueldo de $340.000 con SMVM de $310.000 tiene $3.000 de embargo (10% s/ $30.000)
        before_emb = RepoComercial.calculate_embargo_sueldo_limit(Decimal("340000.00"))
        self.assertEqual(before_emb["max_legal_monthly_embargo_ars"], Decimal("3000.00"))

        # Ejecutar lote matutino 07:00 AM (actualiza Canasta INDEC, SMVM a $350.000 e ICL a 21.8500)
        batch = OfficialGazetteCollector.get_default_07am_batch()
        for norm in batch:
            NormativeRepositoryUpdater.update_repositories_for_norm(
                norm=norm,
                fueros_router=orchestrator.fueros_router,
                daily_norm_store=orchestrator._daily_norm_updates,
            )

        # Después de la ingesta: hijo de 8 años tiene nueva Canasta INDEC ($328.500 + $286.500 = $615.000)
        after_fam = RepoFamilia.calculate_canasta_crianza_indec([8], non_conviviente_share_pct=Decimal("50.00"))
        self.assertEqual(after_fam["total_canasta_crianza_indec_ars"], Decimal("615000.00"))
        self.assertEqual(after_fam["suggested_monthly_quota_ars"], Decimal("307500.00"))

        # Después de la ingesta: con el nuevo SMVM en $350.000, el sueldo de $340.000 pasa a ser 100% INEMBARGABLE ($0.00)
        after_emb = RepoComercial.calculate_embargo_sueldo_limit(Decimal("340000.00"))
        self.assertEqual(after_emb["smvm_reference_ars"], Decimal("350000.00"))
        self.assertEqual(after_emb["max_legal_monthly_embargo_ars"], Decimal("0.00"))
        self.assertEqual(RepoLaboral._SMVM_CURRENT_ARS, Decimal("350000.00"))
        self.assertEqual(RepoLaboral._RIPTE_COEFFICIENT, Decimal("1.1250"))
        self.assertEqual(RepoCivil._LATEST_ICL_BCRA_INDEX, Decimal("21.8500"))
        self.assertEqual(RepoCivil._LATEST_IPC_INDEC_MONTHLY_PCT, Decimal("2.70"))

    def test_gamuza_bridge_signals_tickers_and_strict_privacy_isolation(self) -> None:
        """Verifica señales BULLISH/BEARISH, tickers BYMA/NYSE y bloqueo de datos de expedientes privados."""
        batch = OfficialGazetteCollector.get_default_07am_batch()

        # 1. Norma energética de Vaca Muerta (DNU 215/2026) -> BULLISH con YPF, PAM, TGS, CEPU
        energy_norm = batch[0]
        sig_energy = GamuzaKnowledgeBridge.build_regulatory_signal(
            norm=energy_norm,
            target_fuero=FueroKey.CONTENCIOSO_TRIBUTARIO,
        )
        self.assertEqual(sig_energy.country_code, "AR")
        self.assertEqual(sig_energy.market_signal, "BULLISH")
        self.assertGreaterEqual(sig_energy.regulatory_shock_score, 8)
        self.assertIn("VACA_MUERTA_HIDROCARBUROS", sig_energy.strategic_resources)
        for ticker in ("YPF", "YPFD", "PAM", "PAMP", "TGS", "CEPU"):
            self.assertIn(ticker, sig_energy.direct_tickers_byma_nyse)
        self.assertIn("AL30", sig_energy.sovereign_bonds_impact)

        # 2. Norma BCRA (Com. A 8195) -> BULLISH con GGAL, BMA
        bcra_norm = batch[1]
        sig_bcra = GamuzaKnowledgeBridge.build_regulatory_signal(
            norm=bcra_norm,
            target_fuero=FueroKey.COMERCIAL_CONSUMIDOR,
        )
        self.assertEqual(sig_bcra.market_signal, "BULLISH")
        for ticker in ("GGAL", "BMA"):
            self.assertIn(ticker, sig_bcra.direct_tickers_byma_nyse)

        # 3. Norma ARCA de recargo tributario siderúrgico (RG 5640/2026) -> BEARISH con TXAR y ALUA
        arca_norm = batch[4]
        sig_arca = GamuzaKnowledgeBridge.build_regulatory_signal(
            norm=arca_norm,
            target_fuero=FueroKey.CONTENCIOSO_TRIBUTARIO,
        )
        self.assertEqual(sig_arca.market_signal, "BEARISH")
        for ticker in ("TXAR", "ALUA"):
            self.assertIn(ticker, sig_arca.direct_tickers_byma_nyse)

        # 4. Bloqueo estricto si alguien intenta enviar datos de un expediente privado o token PII a Gamuza
        contaminated_norm = BoletinOficialNormInput(
            norm_id="Nota Interna",
            publication_date=date(2026, 11, 3),
            effective_from=date(2026, 11, 3),
            source_organism="BORA",
            title="Intento de envío de expediente privado",
            full_text_excerpt="Constancia privada del ciudadano [DNI_1] en gdrive://user/Informes_Dr_H/sess-123456.",
        )
        with self.assertRaises(GamuzaPrivacyViolationError):
            GamuzaKnowledgeBridge.build_regulatory_signal(
                norm=contaminated_norm,
                target_fuero=FueroKey.CIVIL,
            )

    def test_cron_07am_and_health_watchdog_endpoints_and_admin_alerts(self) -> None:
        """Ejecuta /cron-07am con lote por defecto y verifica /health y alertas administrativas."""
        client = TestClient(app)

        # 1. Ejecutar /cron-07am sin payload explícito (toma el lote oficial argentino de las 07:00 AM)
        resp_cron = client.post("/cron-07am")
        self.assertEqual(resp_cron.status_code, 200)
        signals = resp_cron.json()
        self.assertEqual(len(signals), 6)
        self.assertTrue(all(s["privacy_isolation_verified"] is True for s in signals))

        # 2. Verificar estado en /health tras la sincronización 07:00 AM
        resp_health = client.get("/health")
        self.assertEqual(resp_health.status_code, 200)
        health = resp_health.json()
        self.assertEqual(health["status"], "OK")
        self.assertTrue(health["agente000_ready"])
        self.assertTrue(health["auditor_revision_ready"])
        self.assertTrue(health["ram_zero_storage_clean"])
        self.assertEqual(health["active_volatile_ram_sessions"], 0)
        self.assertTrue(health["is_normative_db_up_to_date"])
        self.assertIsNotNone(health["last_07am_sync_iso"])
        self.assertEqual(health["platform_version"], "1.4.0-AR-PWA")
        self.assertEqual(health["current_economic_parameters"]["smvm_decreto_484_87_ars"], "350000.00")

        # 3. Simular caída de un conector oficial (BCRA) y verificar que el Watchdog emita alerta administrativa
        statuses_with_failure = OfficialGazetteCollector.probe_official_sources_connectivity(
            simulated_failing_sources={"BCRA"}
        )
        alerts = orchestrator.system_watchdog.record_07am_sync(source_statuses=statuses_with_failure)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].failing_organism, "BCRA")
        self.assertEqual(alerts[0].severity, "WARNING_FALLBACK_APPLIED")


if __name__ == "__main__":
    unittest.main()
