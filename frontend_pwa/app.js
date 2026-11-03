/**
 * Cliente Asincrónico Oficial — Dr. H Argentina (Nodo Capa-País AR)
 * Consume los endpoints de FastAPI y Agente000Orchestrator:
 * - POST /pre-quote
 * - POST /analyze-expediente
 * - POST /download-report-pdf
 * - POST /emergency-scan
 * - POST /repository-consult
 * - POST /cron-07am
 * - GET  /health
 * - GET  /queue-status
 */

(function () {
  "use strict";

  const state = {
    selectedFuero: "AUTO",
    currentPdfBlob: null,
    currentPdfName: "expediente_judicial.pdf",
    lastFinalReport: null,
    platformVersion: null
  };

  // --- 1. GESTIÓN DE TEMA CLARO / OSCURO (modern-web-guidance) ---
  function initThemeToggle() {
    const btnToggle = document.getElementById("btn-theme-toggle");
    const metaScheme = document.querySelector('meta[name="color-scheme"]');
    if (!btnToggle || !metaScheme) return;

    btnToggle.addEventListener("click", () => {
      const sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      const sysScheme = sysDark ? "dark" : "light";
      const stored = localStorage.getItem("color-scheme");
      const currentRendered = stored === "dark" || stored === "light" ? stored : sysScheme;
      const targetScheme = currentRendered === "dark" ? "light" : "dark";

      try {
        if (targetScheme === sysScheme) {
          localStorage.removeItem("color-scheme");
          metaScheme.content = "light dark";
        } else {
          localStorage.setItem("color-scheme", targetScheme);
          metaScheme.content = targetScheme;
        }
      } catch (err) {
        metaScheme.content = targetScheme;
      }
    });
  }

  // --- 2. REGISTRO DE SERVICE WORKER Y AUTO-ACTUALIZACIÓN PWA ---
  function initServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    window.addEventListener("load", () => {
      navigator.serviceWorker
        .register("/sw.js")
        .then((reg) => {
          reg.addEventListener("updatefound", () => {
            const newWorker = reg.installing;
            if (!newWorker) return;
            newWorker.addEventListener("statechange", () => {
              if (newWorker.state === "installed" && navigator.serviceWorker.controller) {
                newWorker.postMessage({ type: "SKIP_WAITING" });
              }
            });
          });
        })
        .catch((err) => console.warn("Service Worker en modo local:", err));
    });
  }

  // --- 3. MONITOR DE SALUD (/health) Y COLA GRATUITA (/queue-status) ---
  async function refreshSystemHealth() {
    const badgeRam = document.getElementById("badge-zero-storage");
    const badge07am = document.getElementById("badge-07am");
    const badgeQueue = document.getElementById("badge-queue");

    try {
      const [healthResp, queueResp] = await Promise.all([
        fetch("/health"),
        fetch("/queue-status")
      ]);

      if (healthResp.ok) {
        const health = await healthResp.json();
        if (badgeRam) {
          badgeRam.textContent = health.ram_zero_storage_clean
            ? `RAM Zero-Storage: Limpia (${health.active_volatile_ram_sessions} sesiones)`
            : `RAM Activa: ${health.active_volatile_ram_sessions} sesión en curso`;
        }
        if (badge07am) {
          const smvm = health.current_economic_parameters?.smvm_decreto_484_87_ars || "310000.00";
          badge07am.textContent = health.is_normative_db_up_to_date
            ? `07:00 AM Al Día (${health.total_norms_ingested_today} normas | SMVM $${smvm})`
            : `07:00 AM Pendiente Sync (SMVM base $${smvm})`;
          badge07am.className = health.is_normative_db_up_to_date ? "badge-pill ok" : "badge-pill";
        }
        if (state.platformVersion && state.platformVersion !== health.platform_version) {
          if ("serviceWorker" in navigator && navigator.serviceWorker.controller) {
            navigator.serviceWorker.controller.postMessage({ type: "SKIP_WAITING" });
          }
        }
        state.platformVersion = health.platform_version;
      }

      if (queueResp.ok && badgeQueue) {
        const q = await queueResp.json();
        badgeQueue.textContent = `Cola IA Gratis: ${q.requests_in_last_60s}/${q.max_requests_per_minute} RPM (429 recuperados: ${q.total_429_intercepted})`;
      }
    } catch (err) {
      if (badge07am) {
        badge07am.textContent = "Estado Servidor: Sin conexión temporal";
      }
    }
  }

  // --- 4. PRE-COTIZADOR E HIGIENE EN RAM (/pre-quote) ---
  function setProgressBar(percent) {
    const bar = document.getElementById("upload-progress-bar");
    const fill = document.getElementById("upload-progress-fill");
    if (bar) bar.setAttribute("aria-valuenow", String(percent));
    if (fill) fill.style.width = `${percent}%`;
  }

  async function runPreQuote(pdfBlob, fileName) {
    const banner = document.getElementById("prequote-state-banner");
    const elTotal = document.getElementById("metric-total-pages");
    const elUseful = document.getElementById("metric-useful-pages");
    const elJunk = document.getElementById("metric-junk-pages");
    const elTier = document.getElementById("metric-tier");
    const elPrice = document.getElementById("metric-price-ars");
    const elPreserved = document.getElementById("preserved-fojas-list");

    state.currentPdfBlob = pdfBlob;
    state.currentPdfName = fileName;

    if (banner) {
      banner.className = "state-banner";
      banner.textContent = `Analizando higiene documental de "${fileName}" en memoria RAM...`;
    }
    setProgressBar(35);

    try {
      const formData = new FormData();
      formData.append("file", pdfBlob, fileName);

      const resp = await fetch("/pre-quote", {
        method: "POST",
        body: formData
      });
      setProgressBar(100);

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({ detail: "Error en pre-cotización" }));
        throw new Error(errData.detail || `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      if (elTotal) elTotal.textContent = String(data.total_pages);
      if (elUseful) elUseful.textContent = String(data.useful_pages_after_hygiene);
      if (elJunk) elJunk.textContent = String(data.discarded_junk_pages);
      if (elTier) {
        elTier.textContent = data.qualifies_for_free_tier
          ? "Nivel 0 Gratuito"
          : data.suggested_tier.replace(/_/g, " ");
      }
      if (elPrice) {
        elPrice.textContent = `$${data.estimated_single_radiography_ars} ARS (Único) | $${data.estimated_monthly_tracking_ars}/mes`;
      }
      if (elPreserved) {
        elPreserved.textContent = `Índice de Fojas Reales Preservadas: [${data.preserved_foja_index.join(", ")}] (${data.processing_time_ms} ms en RAM)`;
      }
      if (banner) {
        banner.className = "state-banner success";
        banner.textContent = `Pre-cotización completada en ${data.processing_time_ms} ms: ${data.useful_pages_after_hygiene} fojas útiles y ${data.discarded_junk_pages} páginas basura descartadas.`;
      }
      await refreshSystemHealth();
    } catch (err) {
      setProgressBar(0);
      if (banner) {
        banner.className = "state-banner error";
        banner.textContent = `Error en Pre-Cotizador: ${err.message}`;
      }
    }
  }

  // --- 5. CICLO COMPLETO AGENTE000 (/analyze-expediente) ---
  function renderFinalCitizenReport(report) {
    state.lastFinalReport = report;

    const emptyState = document.getElementById("report-empty-state");
    const contentGrid = document.getElementById("report-content-grid");
    const timelineWrap = document.getElementById("report-timeline-wrap");
    const doNotSignBanner = document.getElementById("do-not-sign-banner");
    const doNotSignText = document.getElementById("do-not-sign-text");
    const btnDownloadPdf = document.getElementById("btn-download-pdf");

    if (emptyState) emptyState.classList.add("hidden");
    if (contentGrid) contentGrid.classList.remove("hidden");
    if (timelineWrap) timelineWrap.classList.remove("hidden");
    if (btnDownloadPdf) btnDownloadPdf.disabled = false;

    const subtitle = document.getElementById("report-meta-subtitle");
    if (subtitle) {
      subtitle.textContent = `Informe ID: ${report.report_id} | Fuero: ${report.fuero} | Jurisdicción: ${report.province_jurisdiction} | Emitido: ${report.generated_at_iso}`;
    }

    if (report.do_not_sign_yet_alert && doNotSignBanner && doNotSignText) {
      doNotSignBanner.classList.remove("hidden");
      doNotSignText.textContent = report.do_not_sign_alert_box || "Se detectaron cláusulas dispositivas riesgosas.";
    } else if (doNotSignBanner) {
      doNotSignBanner.classList.add("hidden");
    }

    const plainSummary = document.getElementById("report-plain-summary");
    if (plainSummary) plainSummary.textContent = report.citizen_plain_summary;

    const auditSection = document.getElementById("report-audit-section");
    if (auditSection) auditSection.textContent = report.audit_and_deadlines_section;

    const qList = document.getElementById("report-questions-list");
    if (qList) {
      qList.innerHTML = "";
      (report.questions_for_your_lawyer || []).forEach((q) => {
        const li = document.createElement("li");
        li.textContent = q;
        qList.appendChild(li);
      });
    }

    const audioScript = document.getElementById("report-audio-script");
    if (audioScript) audioScript.textContent = report.audio_script_90_seconds;

    const waText = document.getElementById("report-whatsapp-text");
    if (waText) waText.value = report.whatsapp_ready_message;

    const timelineCards = document.getElementById("report-timeline-cards");
    if (timelineCards) {
      timelineCards.innerHTML = "";
      (report.verified_timeline_cards || []).forEach((card) => {
        const div = document.createElement("div");
        div.className = "foja-card";
        div.innerHTML = `
          <div class="foja-card-head">
            <span>Foja N° ${card.foja_ref} — ${card.document_piece_type}</span>
            <span style="color:var(--accent-brand);font-size:0.8rem;">${card.normative_ref}</span>
          </div>
          <p style="font-size:0.88rem;"><strong>Traducción Ciudadana:</strong> ${card.citizen_plain_translation}</p>
          <p style="font-size:0.8rem;color:var(--text-secondary);"><em>Extracto verificable:</em> "${card.verbatim_excerpt}"</p>
        `;
        timelineCards.appendChild(div);
      });
    }

    const shieldEl = document.getElementById("report-shield-disclaimer");
    if (shieldEl) shieldEl.textContent = report.legal_shield_disclaimer;

    const certEl = document.getElementById("report-zero-storage-cert");
    if (certEl) certEl.textContent = report.zero_storage_confirmation;
  }

  async function runFullExpedienteAnalysis() {
    const emptyState = document.getElementById("report-empty-state");
    if (!state.currentPdfBlob) {
      if (emptyState) {
        emptyState.className = "state-banner error";
        emptyState.classList.remove("hidden");
        emptyState.textContent = "Primero seleccioná un archivo PDF o hacé clic en 'Cargar Expediente de Prueba' en el Paso 1.";
      }
      return;
    }

    if (emptyState) {
      emptyState.className = "state-banner";
      emptyState.classList.remove("hidden");
      emptyState.textContent = "Ejecutando Ciclo Ida y Vuelta de Agente000 (Anonimización PII -> Fuero -> Auditor ISO-Compliance -> Wipe RAM)...";
    }

    try {
      const formData = new FormData();
      formData.append("file", state.currentPdfBlob, state.currentPdfName);

      const verbal = document.getElementById("input-verbal-statement")?.value || "";
      const prov = document.getElementById("select-province")?.value || "";
      const role = document.getElementById("select-role")?.value || "";
      const feePct = document.getElementById("input-fee-pct")?.value || "";
      const upfrontArs = document.getElementById("input-upfront-ars")?.value || "";

      formData.append("user_verbal_statement", verbal);
      if (prov) formData.append("province_jurisdiction", prov);
      if (role) formData.append("user_role", role);
      if (feePct) formData.append("claimed_lawyer_fee_pct", feePct);
      if (upfrontArs) formData.append("upfront_cost_requested_ars", upfrontArs);

      const resp = await fetch("/analyze-expediente", {
        method: "POST",
        body: formData
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({ detail: "Error en análisis de expediente" }));
        throw new Error(errData.detail || `HTTP ${resp.status}`);
      }

      const report = await resp.json();
      renderFinalCitizenReport(report);
      await refreshSystemHealth();
    } catch (err) {
      if (emptyState) {
        emptyState.className = "state-banner error";
        emptyState.classList.remove("hidden");
        emptyState.textContent = `Error en Agente000: ${err.message}`;
      }
    }
  }

  // --- 6. DESCARGA DE PDF AUDITADO GENERADO EN RAM (/download-report-pdf) ---
  async function downloadAuditedPdf() {
    if (!state.lastFinalReport) return;
    try {
      const resp = await fetch("/download-report-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(state.lastFinalReport)
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const pdfBlob = await resp.blob();
      const url = URL.createObjectURL(pdfBlob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `Informe_Dr_H_${state.lastFinalReport.report_id}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(`No se pudo descargar el PDF auditado: ${err.message}`);
    }
  }

  // --- 7. REPRODUCTOR DE AUDIO DE 90 SEGUNDOS Y PLANTILLA DE WHATSAPP ---
  function initAudioAndWhatsAppControls() {
    const btnPlay = document.getElementById("btn-play-audio");
    const btnStop = document.getElementById("btn-stop-audio");
    const statusLbl = document.getElementById("audio-status-label");
    const btnCopyWa = document.getElementById("btn-copy-whatsapp");
    const btnOpenWa = document.getElementById("btn-open-whatsapp");

    if (btnPlay) {
      btnPlay.addEventListener("click", () => {
        const scriptText = document.getElementById("report-audio-script")?.textContent || "";
        if (!scriptText) return;
        if (!("speechSynthesis" in window)) {
          if (statusLbl) statusLbl.textContent = "Síntesis de voz no disponible en este navegador.";
          return;
        }
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(scriptText);
        utterance.lang = "es-AR";
        utterance.rate = 0.98;
        utterance.onstart = () => {
          if (statusLbl) statusLbl.textContent = "Reproduciendo guion de 90 segundos...";
        };
        utterance.onend = () => {
          if (statusLbl) statusLbl.textContent = "Reproducción finalizada.";
        };
        window.speechSynthesis.speak(utterance);
      });
    }

    if (btnStop) {
      btnStop.addEventListener("click", () => {
        if ("speechSynthesis" in window) {
          window.speechSynthesis.cancel();
          if (statusLbl) statusLbl.textContent = "Audio detenido.";
        }
      });
    }

    if (btnCopyWa) {
      btnCopyWa.addEventListener("click", async () => {
        const waText = document.getElementById("report-whatsapp-text")?.value || "";
        if (!waText) return;
        try {
          await navigator.clipboard.writeText(waText);
          btnCopyWa.textContent = "¡Copiado al Portapapeles!";
          setTimeout(() => {
            btnCopyWa.textContent = "Copiar Texto para WhatsApp";
          }, 2000);
        } catch (err) {
          console.warn("Error copiando al portapapeles:", err);
        }
      });
    }

    if (btnOpenWa) {
      btnOpenWa.addEventListener("click", () => {
        const waText = document.getElementById("report-whatsapp-text")?.value || "";
        if (!waText) return;
        const encoded = encodeURIComponent(waText);
        window.open(`https://wa.me/?text=${encoded}`, "_blank", "noopener,noreferrer");
      });
    }
  }

  // --- 8. ESCÁNER DE URGENCIA "¡NO FIRMES TODAVÍA!" (/emergency-scan) ---
  async function runEmergencyScan() {
    const docText = document.getElementById("emergency-doc-text")?.value.trim() || "";
    const outBox = document.getElementById("emergency-scan-output");
    const prov = document.getElementById("select-province")?.value || "PJN_NACION_CABA";

    if (!docText || docText.length < 10) {
      if (outBox) {
        outBox.className = "state-banner error";
        outBox.textContent = "Ingresá al menos 10 caracteres del documento o acta para escanear.";
      }
      return;
    }

    if (outBox) {
      outBox.className = "state-banner";
      outBox.textContent = "Ejecutando detector preventivo de actos dispositivos [ISO-PREV-04]...";
    }

    try {
      const resp = await fetch("/emergency-scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document_text: docText,
          province_jurisdiction: prov,
          user_context_note: "Escaneo desde panel ciudadano PWA"
        })
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Error en escaneo de urgencia" }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (outBox) {
        outBox.className = data.do_not_sign_yet ? "state-banner error" : "state-banner success";
        const clausesHtml = (data.detected_risk_clauses || []).join(" | ");
        outBox.innerHTML = `
          <strong>Semáforo: ${data.urgency_level} (Fuero: ${data.detected_fuero})</strong><br>
          <span>${data.plain_explanation}</span><br>
          ${clausesHtml ? `<strong>Hallazgos:</strong> ${clausesHtml}<br>` : ""}
          <strong>Guía de Acción Inmediata:</strong> ${data.immediate_action_guide}
        `;
      }
    } catch (err) {
      if (outBox) {
        outBox.className = "state-banner error";
        outBox.textContent = `Error en Escáner: ${err.message}`;
      }
    }
  }

  // --- 9. CONSULTOR DE REPOSITORIOS (/repository-consult) Y SINCRONIZACIÓN 07:00 AM (/cron-07am) ---
  async function runRepositoryConsult() {
    const query = document.getElementById("repo-query-input")?.value.trim() || "";
    const outBox = document.getElementById("repo-consult-output");
    const prov = document.getElementById("select-province")?.value || "PJN_NACION_CABA";

    if (!query || query.length < 5) {
      if (outBox) {
        outBox.className = "state-banner error";
        outBox.textContent = "Ingresá una consulta de al menos 5 caracteres.";
      }
      return;
    }

    try {
      const payload = {
        query: query,
        province_jurisdiction: prov
      };
      if (state.selectedFuero && state.selectedFuero !== "AUTO") {
        payload.fuero = state.selectedFuero;
      }

      const resp = await fetch("/repository-consult", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Error en consulta normativa" }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (outBox) {
        outBox.className = "state-banner success";
        outBox.innerHTML = `
          <strong>Fuero Clasificado: ${data.classified_fuero} (${data.province_jurisdiction})</strong><br>
          <span>${data.citizen_clear_answer}</span><br>
          <em>${data.temporal_validity_note}</em>
        `;
      }
    } catch (err) {
      if (outBox) {
        outBox.className = "state-banner error";
        outBox.textContent = `Error en Consultor: ${err.message}`;
      }
    }
  }

  async function triggerMorning07amSync() {
    const outBox = document.getElementById("repo-consult-output");
    if (outBox) {
      outBox.className = "state-banner";
      outBox.textContent = "Ejecutando ingesta matutina 07:00 AM (BORA, BCRA, INDEC, SMVM, ARCA, CSJN) y despachando señales a Proyecto Gamuza...";
    }
    try {
      const resp = await fetch("/cron-07am", { method: "POST" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const signals = await resp.json();
      const tickers = Array.from(new Set(signals.flatMap((s) => s.direct_tickers_byma_nyse || []))).join(", ");
      if (outBox) {
        outBox.className = "state-banner success";
        outBox.innerHTML = `
          <strong>Sincronización 07:00 AM Completada (${signals.length} Normas Oficiales Procesadas):</strong><br>
          <span>Calculadoras INDEC, SMVM Decreto 484/87 e ICL actualizadas en caliente.</span><br>
          <span>Señales enviadas a Proyecto Gamuza (Tickers BYMA/NYSE alcanzados: ${tickers}).</span>
        `;
      }
      await refreshSystemHealth();
    } catch (err) {
      if (outBox) {
        outBox.className = "state-banner error";
        outBox.textContent = `Error sincronizando 07:00 AM: ${err.message}`;
      }
    }
  }

  // --- 10. EXPEDIENTE DE DEMOSTRACIÓN DESDE SERVIDOR (/demo-sample-pdf) ---
  async function loadDemoExpediente() {
    try {
      const resp = await fetch("/demo-sample-pdf");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const pdfBlob = await resp.blob();
      const verbalInput = document.getElementById("input-verbal-statement");
      const feeInput = document.getElementById("input-fee-pct");
      const upfrontInput = document.getElementById("input-upfront-ars");
      if (verbalInput && !verbalInput.value) {
        verbalInput.value = "Me informaron que ya pidió sentencia y que abone $45000 para tasa de justicia.";
      }
      if (feeInput && !feeInput.value) feeInput.value = "30";
      if (upfrontInput && !upfrontInput.value) upfrontInput.value = "45000";
      await runPreQuote(pdfBlob, "Expediente_Laboral_Demo_SCBA.pdf");
    } catch (err) {
      console.error("Error cargando PDF de prueba:", err);
    }
  }

  // --- INICIALIZACIÓN DE EVENTOS DOM ---
  document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();
    initServiceWorker();
    initAudioAndWhatsAppControls();
    refreshSystemHealth();

    const fileInput = document.getElementById("pdf-file-input");
    if (fileInput) {
      fileInput.addEventListener("change", (ev) => {
        const file = ev.target.files?.[0];
        if (file) {
          runPreQuote(file, file.name);
        }
      });
    }

    document.getElementById("btn-demo-pdf")?.addEventListener("click", loadDemoExpediente);
    document.getElementById("btn-run-full-analysis")?.addEventListener("click", runFullExpedienteAnalysis);
    document.getElementById("btn-download-pdf")?.addEventListener("click", downloadAuditedPdf);
    document.getElementById("btn-run-emergency-scan")?.addEventListener("click", runEmergencyScan);
    document.getElementById("btn-demo-emergency")?.addEventListener("click", () => {
      const el = document.getElementById("emergency-doc-text");
      if (el) {
        el.value =
          "Recibí conforme el total y declaro que nada más tengo que reclamar por despido, suscribiendo desistimiento de la acción y del derecho y pacto de cuota litis del 35%.";
        runEmergencyScan();
      }
    });
    document.getElementById("btn-run-repo-consult")?.addEventListener("click", runRepositoryConsult);
    document.getElementById("btn-trigger-07am")?.addEventListener("click", triggerMorning07amSync);

    const pillsContainer = document.getElementById("fuero-selector-pills");
    if (pillsContainer) {
      pillsContainer.querySelectorAll(".fuero-chip").forEach((btn) => {
        btn.addEventListener("click", () => {
          pillsContainer.querySelectorAll(".fuero-chip").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          state.selectedFuero = btn.getAttribute("data-fuero") || "AUTO";
        });
      });
    }
  });
})();
