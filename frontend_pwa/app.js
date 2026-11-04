/**
 * Cliente Unificado de Interfaz — Dr. H Argentina (MODO PRIVADO EXCLUSIVO ADMINISTRADOR)
 * - POST /auth/login, GET /auth/me, POST /auth/logout (sesión del Administrador)
 * Todas las llamadas a los motores envían `Authorization: Bearer <token>` mediante `apiFetch`:
 * - POST /pre-quote
 * - POST /analyze-expediente
 * - POST /download-report-pdf
 * - POST /emergency-scan
 * - POST /repository-consult
 * - GET  /demo-sample-pdf
 */

(function () {
  "use strict";

  const TOKEN_STORAGE_KEY = "drh_admin_token";
  const LEGACY_STORAGE_KEYS = ["drh_active_user"];

  const FUERO_LABELS = {
    AUTO: "Detección automática",
    "01_penal": "Penal y Garantías Constitucionales",
    "02_familia": "Familia y Cuota Alimentaria",
    "03_laboral": "Laboral y Accidentes de Trabajo (ART)",
    "04_civil": "Civil, Contratos y Alquileres",
    "05_comercial_consumidor": "Deudas, Bancos y Defensa del Consumidor",
    "06_seguridad_social_salud": "Jubilaciones (ANSES) y Salud",
    "07_contencioso_tributario": "Multas, Impuestos y Reclamos al Estado"
  };

  const state = {
    loggedInUser: null,
    accessToken: null,
    selectedFuero: "AUTO",
    currentPdfBlob: null,
    currentPdfName: "expediente_consulta.pdf",
    attachedMediaName: null,
    urgentPhotoMode: false,
    lastFinalReport: null
  };

  function humanizeFuero(code) {
    if (!code) return "General";
    return FUERO_LABELS[code] || String(code).replace(/_/g, " ");
  }

  function cleanTechnicalJargon(text) {
    if (!text) return "";
    return String(text)
      .replace(/\[ISO-[A-Z]+-\d+\]:?\s*/g, "")
      .replace(/Agente000/gi, "Dr. H")
      .replace(/Zero-Storage en RAM/gi, "procesamiento privado")
      .replace(/\s+/g, " ")
      .trim();
  }

  function buildFormalEmailText(report, fueroReadable) {
    const questions = (report.questions_for_your_lawyer || []).map(
      (q, idx) => `${idx + 1}. ${cleanTechnicalJargon(q)}`
    );
    const pointsBlock =
      questions.length > 0
        ? questions.join("\n")
        : "1. Solicito me informe el estado actual del trámite y los próximos pasos previstos.\n2. Solicito copia simple de las últimas presentaciones realizadas.";

    const urgencyLine = report.do_not_sign_yet_alert
      ? "Asimismo, le informo que previo a suscribir cualquier acuerdo, recibo o escrito dispositivo, necesito revisar el detalle completo de la liquidación y sus alcances legales.\n\n"
      : "";

    return [
      "Asunto: Consulta sobre estado de trámite y puntos pendientes",
      "",
      "Estimado/a Dr./Dra.:",
      "",
      `Me comunico con usted en relación con mi asunto (${fueroReadable}) a fin de solicitarle información actualizada y dejar asentados los siguientes puntos que necesito revisar:`,
      "",
      pointsBlock,
      "",
      urgencyLine +
        "Quedo a la espera de su respuesta por esta vía o con copia de las constancias correspondientes.",
      "",
      "Atentamente."
    ].join("\n");
  }

  // --- 1. TEMA CLARO / OSCURO ---
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

  // --- 2. REGISTRO DE SERVICE WORKER ---
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
        .catch((err) => console.warn("Service Worker local:", err));
    });
  }

  // --- 3. ACCESO PRIVADO DEL ADMINISTRADOR (LOGIN / LOGOUT REAL CONTRA EL BACKEND) ---
  function readStoredToken() {
    try {
      LEGACY_STORAGE_KEYS.forEach((key) => sessionStorage.removeItem(key));
      return sessionStorage.getItem(TOKEN_STORAGE_KEY);
    } catch (err) {
      console.warn("No se pudo leer la sesión guardada:", err);
      return null;
    }
  }

  function storeToken(token) {
    state.accessToken = token;
    try {
      if (token) {
        sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
      } else {
        sessionStorage.removeItem(TOKEN_STORAGE_KEY);
      }
    } catch (err) {
      console.warn("No se pudo actualizar la sesión guardada:", err);
    }
  }

  function showLoginFeedback(message, kind) {
    const feedback = document.getElementById("login-feedback");
    if (!feedback) return;
    if (!message) {
      feedback.classList.add("hidden");
      feedback.textContent = "";
      return;
    }
    feedback.className = kind === "error" ? "state-banner error" : "state-banner";
    feedback.textContent = message;
  }

  function setLoggedInView(username) {
    state.loggedInUser = username;
    const loginSection = document.getElementById("login-section");
    const workspace = document.getElementById("app-workspace");
    const userBadge = document.getElementById("user-session-badge");
    const btnLogout = document.getElementById("btn-logout");

    if (username) {
      if (loginSection) loginSection.classList.add("hidden");
      if (workspace) workspace.classList.remove("hidden");
      if (userBadge) {
        userBadge.textContent = `Administrador: ${username}`;
        userBadge.classList.remove("hidden");
      }
      if (btnLogout) btnLogout.classList.remove("hidden");
    } else {
      if (loginSection) loginSection.classList.remove("hidden");
      if (workspace) workspace.classList.add("hidden");
      if (userBadge) userBadge.classList.add("hidden");
      if (btnLogout) btnLogout.classList.add("hidden");
    }
  }

  function handleSessionExpired(message) {
    storeToken(null);
    state.lastFinalReport = null;
    setLoggedInView(null);
    showLoginFeedback(message || "Tu sesión de administrador venció. Volvé a ingresar.", "error");
  }

  /**
   * fetch autenticado: agrega el token Bearer del Administrador y cierra la sesión ante un 401.
   */
  async function apiFetch(path, options = {}) {
    if (!state.accessToken) {
      handleSessionExpired("Acceso privado: ingresá como administrador.");
      throw new Error("Sesión de administrador no iniciada.");
    }
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${state.accessToken}`);
    const resp = await fetch(path, { ...options, headers });
    if (resp.status === 401) {
      handleSessionExpired("Tu sesión de administrador venció. Volvé a ingresar.");
      throw new Error("La sesión de administrador venció.");
    }
    return resp;
  }

  async function restoreSession() {
    const token = readStoredToken();
    if (!token) {
      setLoggedInView(null);
      return;
    }
    state.accessToken = token;
    try {
      const resp = await fetch("/auth/me", { headers: { Authorization: `Bearer ${token}` } });
      if (!resp.ok) {
        storeToken(null);
        setLoggedInView(null);
        return;
      }
      const data = await resp.json();
      setLoggedInView(data.username);
    } catch (err) {
      console.warn("No se pudo verificar la sesión guardada:", err);
      storeToken(null);
      setLoggedInView(null);
    }
  }

  function initUserAccess() {
    const loginForm = document.getElementById("login-form");
    const userInput = document.getElementById("login-username");
    const passInput = document.getElementById("login-password");
    const btnLogin = document.getElementById("btn-login");
    const btnLogout = document.getElementById("btn-logout");

    restoreSession();

    if (loginForm) {
      loginForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const username = userInput?.value.trim() || "";
        const password = passInput?.value || "";

        if (!username || !password) {
          showLoginFeedback("Ingresá el usuario y la contraseña de administrador.", "error");
          return;
        }

        showLoginFeedback("Verificando credenciales...", "info");
        if (btnLogin) btnLogin.disabled = true;

        try {
          const resp = await fetch("/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password })
          });

          if (resp.status === 429) {
            const data = await resp.json().catch(() => ({}));
            throw new Error(data.detail || "Demasiados intentos fallidos. Esperá unos minutos.");
          }
          if (!resp.ok) {
            throw new Error("Usuario o contraseña de administrador incorrectos.");
          }

          const data = await resp.json();
          storeToken(data.access_token);
          if (passInput) passInput.value = "";
          showLoginFeedback("", "info");
          setLoggedInView(data.username);
        } catch (err) {
          showLoginFeedback(err.message || "No se pudo iniciar sesión.", "error");
        } finally {
          if (btnLogin) btnLogin.disabled = false;
        }
      });
    }

    if (btnLogout) {
      btnLogout.addEventListener("click", async () => {
        const token = state.accessToken;
        if (token) {
          try {
            await fetch("/auth/logout", {
              method: "POST",
              headers: { Authorization: `Bearer ${token}` }
            });
          } catch (err) {
            console.warn("No se pudo revocar la sesión en el servidor:", err);
          }
        }
        storeToken(null);
        state.lastFinalReport = null;
        if (passInput) passInput.value = "";
        setLoggedInView(null);
      });
    }
  }

  // --- 4. PREGUNTA OPCIONAL DE FUERO Y ARCHIVOS ADJUNTOS ---
  function initUnifiedInputControls() {
    const radioAuto = document.getElementById("radio-fuero-auto");
    const radioManual = document.getElementById("radio-fuero-manual");
    const manualWrap = document.getElementById("manual-fuero-wrap");
    const selectFuero = document.getElementById("select-fuero-manual");

    const updateFueroMode = () => {
      if (radioManual && radioManual.checked) {
        manualWrap?.classList.remove("hidden");
        state.selectedFuero = selectFuero?.value || "AUTO";
      } else {
        manualWrap?.classList.add("hidden");
        state.selectedFuero = "AUTO";
      }
    };

    radioAuto?.addEventListener("change", updateFueroMode);
    radioManual?.addEventListener("change", updateFueroMode);
    selectFuero?.addEventListener("change", () => {
      state.selectedFuero = selectFuero.value || "AUTO";
    });

    const fileInput = document.getElementById("pdf-file-input");
    const attachStatus = document.getElementById("attachment-status-text");
    if (fileInput) {
      fileInput.addEventListener("change", async (ev) => {
        const file = ev.target.files?.[0];
        if (!file) return;

        if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
          state.currentPdfBlob = file;
          state.currentPdfName = file.name;
          state.attachedMediaName = null;
          if (attachStatus) {
            attachStatus.textContent = `Archivo cargado listo para analizar: ${file.name}`;
          }
          // Pre-procesamiento silencioso en segundo plano
          try {
            const formData = new FormData();
            formData.append("file", file, file.name);
            await apiFetch("/pre-quote", { method: "POST", body: formData });
          } catch (err) {
            console.warn("Pre-lectura silenciosa:", err);
          }
        } else {
          state.attachedMediaName = file.name;
          if (attachStatus) {
            attachStatus.textContent = `Audio adjuntado: ${file.name}. Sumá una breve nota arriba si querés detallar algo puntual.`;
          }
        }
      });
    }

    const btnCamera = document.getElementById("btn-camera-urgent");
    const cameraInput = document.getElementById("camera-file-input");
    const cameraStatus = document.getElementById("camera-status-text");
    const verbalInput = document.getElementById("input-verbal-statement");

    if (btnCamera && cameraInput) {
      btnCamera.addEventListener("click", () => {
        state.urgentPhotoMode = true;
        cameraInput.click();
      });

      cameraInput.addEventListener("change", async (ev) => {
        const file = ev.target.files?.[0];
        if (!file) return;

        state.urgentPhotoMode = true;
        if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
          state.currentPdfBlob = file;
          state.currentPdfName = file.name;
        } else {
          state.attachedMediaName = file.name;
        }

        if (cameraStatus) {
          cameraStatus.textContent = `Imagen/documento "${file.name}" capturado. Analizando si es seguro firmar...`;
        }
        if (verbalInput && !verbalInput.value.trim()) {
          verbalInput.value =
            "Me piden firmar este documento ahora mismo (recibo / acuerdo con renuncia de reclamos). ¿Lo puedo firmar o espero?";
        }
        await runUnifiedConsultation();
      });
    }
  }

  // --- 5. RENDERIZADO DE LA RESPUESTA PRÁCTICA ---
  function renderUnifiedResponse(report) {
    state.lastFinalReport = report;

    const emptyState = document.getElementById("report-empty-state");
    const contentWrap = document.getElementById("report-content-wrap");
    const subtitle = document.getElementById("report-meta-subtitle");
    const fueroBadge = document.getElementById("result-detected-fuero");
    const doNotSignBanner = document.getElementById("do-not-sign-banner");
    const doNotSignTitle = document.getElementById("do-not-sign-title");
    const doNotSignText = document.getElementById("do-not-sign-text");
    const plainSummary = document.getElementById("report-plain-summary");
    const auditSection = document.getElementById("report-audit-section");
    const qWrap = document.getElementById("report-questions-wrap");
    const qList = document.getElementById("report-questions-list");
    const audioScript = document.getElementById("report-audio-script");
    const waText = document.getElementById("report-whatsapp-text");
    const emailText = document.getElementById("report-email-text");
    const btnDownloadPdf = document.getElementById("btn-download-pdf");

    if (emptyState) emptyState.classList.add("hidden");
    if (contentWrap) contentWrap.classList.remove("hidden");
    if (btnDownloadPdf) btnDownloadPdf.disabled = false;

    const fueroReadable = humanizeFuero(report.fuero);
    if (fueroBadge) {
      fueroBadge.textContent = `Fuero detectado: ${fueroReadable}`;
      fueroBadge.classList.remove("hidden");
    }
    if (subtitle) {
      subtitle.textContent = `Dictamen listo sobre tu consulta de ${fueroReadable}.`;
    }

    if (doNotSignBanner && doNotSignTitle && doNotSignText) {
      if (report.do_not_sign_yet_alert) {
        doNotSignBanner.className = "urgency-banner danger";
        doNotSignTitle.textContent = "⚠️ No firmes nada todavía";
        doNotSignText.textContent = cleanTechnicalJargon(
          report.do_not_sign_alert_box ||
            "Detectamos puntos delicados que pueden hacerte perder derechos o plata si firmás sin revisarlo antes."
        );
      } else {
        doNotSignBanner.className = "urgency-banner safe";
        doNotSignTitle.textContent = "✅ Podés avanzar con tranquilidad";
        doNotSignText.textContent =
          "No se detectaron cláusulas trampa de renuncia de derechos en los datos ingresados. Seguí las recomendaciones de abajo.";
      }
    }

    if (plainSummary) {
      plainSummary.textContent = cleanTechnicalJargon(report.citizen_plain_summary);
    }
    if (auditSection) {
      auditSection.textContent = cleanTechnicalJargon(report.audit_and_deadlines_section);
    }

    if (qList && qWrap) {
      qList.innerHTML = "";
      const questions = report.questions_for_your_lawyer || [];
      if (questions.length > 0) {
        qWrap.classList.remove("hidden");
        questions.forEach((q) => {
          const li = document.createElement("li");
          li.textContent = cleanTechnicalJargon(q);
          qList.appendChild(li);
        });
      } else {
        qWrap.classList.add("hidden");
      }
    }

    if (audioScript) {
      audioScript.textContent = cleanTechnicalJargon(
        report.audio_script_90_seconds || report.citizen_plain_summary
      );
    }

    if (waText) {
      waText.value = cleanTechnicalJargon(report.whatsapp_ready_message);
    }

    if (emailText) {
      emailText.value = buildFormalEmailText(report, fueroReadable);
    }
  }

  // --- 6. FLUJO UNIFICADO DE CONSULTA (UN SOLO BOTÓN PARA TODO) ---
  async function runUnifiedConsultation() {
    const statusBanner = document.getElementById("consultation-state-banner");
    const verbalText = document.getElementById("input-verbal-statement")?.value.trim() || "";

    if (!state.currentPdfBlob && verbalText.length < 5 && !state.attachedMediaName) {
      if (statusBanner) {
        statusBanner.className = "state-banner error";
        statusBanner.classList.remove("hidden");
        statusBanner.textContent =
          "Por favor escribí tu consulta, pegá lo que te dijo tu abogado o adjuntá un archivo para que Dr. H pueda ayudarte.";
      }
      return;
    }

    if (statusBanner) {
      statusBanner.className = "state-banner";
      statusBanner.classList.remove("hidden");
      statusBanner.textContent = "Analizando tu consulta y preparando las respuestas listas para usar...";
    }

    try {
      // Caso A: El usuario subió un PDF (o cargó el ejemplo práctico)
      if (state.currentPdfBlob) {
        const formData = new FormData();
        formData.append("file", state.currentPdfBlob, state.currentPdfName);
        formData.append(
          "user_verbal_statement",
          verbalText || "Solicito revisión integral del expediente y pasos a seguir."
        );

        const resp = await apiFetch("/analyze-expediente", {
          method: "POST",
          body: formData
        });

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({ detail: "No se pudo procesar el archivo PDF." }));
          throw new Error(errData.detail || `HTTP ${resp.status}`);
        }

        const report = await resp.json();
        if (state.selectedFuero && state.selectedFuero !== "AUTO") {
          report.fuero = state.selectedFuero;
        }
        renderUnifiedResponse(report);
      } else {
        // Caso B: El usuario escribió su consulta en criollo, pegó lo que le piden firmar o usó la cámara/audio
        const queryText =
          verbalText ||
          `Consulta sobre archivo adjunto (${state.attachedMediaName || "documento"}). ¿Qué debo tener en cuenta antes de firmar o avanzar?`;

        const consultPayload = {
          query: queryText,
          province_jurisdiction: "PJN_NACION_CABA"
        };
        if (state.selectedFuero && state.selectedFuero !== "AUTO") {
          consultPayload.fuero = state.selectedFuero;
        }

        const [scanResp, repoResp] = await Promise.all([
          apiFetch("/emergency-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              document_text: queryText.length >= 10 ? queryText : `${queryText} — revisión preventiva`,
              province_jurisdiction: "PJN_NACION_CABA",
              user_context_note: state.urgentPhotoMode ? "Escáner de urgencia cámara" : "Consulta unificada"
            })
          }),
          apiFetch("/repository-consult", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(consultPayload)
          })
        ]);

        if (!scanResp.ok || !repoResp.ok) {
          throw new Error("No se pudo completar la consulta en este momento.");
        }

        const scanData = await scanResp.json();
        const repoData = await repoResp.json();

        const resolvedFuero =
          state.selectedFuero !== "AUTO"
            ? state.selectedFuero
            : repoData.classified_fuero || scanData.detected_fuero || "03_laboral";

        const doNotSign = Boolean(scanData.do_not_sign_yet || state.urgentPhotoMode && scanData.urgency_level !== "VERDE_INFORMATIVO");
        const questions = [
          "¿Me puede enviar por escrito el detalle de los pasos que siguen y los plazos estimados?",
          "Antes de firmar cualquier escrito o acuerdo, ¿me puede adelantar una copia para leerla tranquilo/a en casa?",
          "¿Existe algún gasto o beneficio de gratuidad que me corresponda según la ley vigente?"
        ];

        const whatsappMsg = doNotSign
          ? "Hola Doc, antes de firmar o avanzar con lo que conversamos, prefiero que me pase una copia completa por acá para leerla tranquilo/a y revisar bien la liquidación y los puntos del acuerdo. ¡Gracias!"
          : "Hola Doc, le escribo para pedirle si me puede comentar brevemente cómo sigue mi trámite, qué plazos tenemos por delante y si hace falta que le acerque alguna documentación adicional. ¡Muchas gracias!";

        const synthesizedReport = {
          report_id: `drh-${Date.now().toString(16).slice(-10)}`,
          session_id: `ses-${Date.now()}`,
          generated_at_iso: new Date().toISOString(),
          fuero: resolvedFuero,
          province_jurisdiction: "PJN_NACION_CABA",
          citizen_plain_summary: `${repoData.citizen_clear_answer} ${scanData.plain_explanation}`,
          verified_timeline_cards: [
            {
              foja_ref: 1,
              document_piece_type: "Consulta Ciudadana",
              verbatim_excerpt: queryText.slice(0, 140),
              citizen_plain_translation: repoData.citizen_clear_answer,
              normative_ref: (repoData.matched_articles && repoData.matched_articles[0]?.law_or_code) || "Legislación Nacional Vigente"
            }
          ],
          audit_and_deadlines_section: `${scanData.immediate_action_guide} (${repoData.temporal_validity_note})`,
          do_not_sign_yet_alert: doNotSign,
          do_not_sign_alert_box: doNotSign
            ? `${scanData.plain_explanation} ${scanData.immediate_action_guide}`
            : null,
          questions_for_your_lawyer: questions,
          whatsapp_ready_message: whatsappMsg,
          audio_script_90_seconds: `${repoData.citizen_clear_answer}. ${scanData.immediate_action_guide}`,
          zero_storage_confirmation: "Privacidad protegida: tu consulta fue procesada de forma confidencial.",
          legal_shield_disclaimer: "Orientación informativa basada en la legislación argentina vigente."
        };

        renderUnifiedResponse(synthesizedReport);
      }

      state.urgentPhotoMode = false;
      if (statusBanner) {
        statusBanner.className = "state-banner success";
        statusBanner.textContent = "¡Listo! Abajo tenés la respuesta clara y los textos preparados para copiar.";
      }
    } catch (err) {
      if (statusBanner) {
        statusBanner.className = "state-banner error";
        statusBanner.classList.remove("hidden");
        statusBanner.textContent = `No pudimos procesar la consulta: ${err.message}`;
      }
    }
  }

  // --- 7. DESCARGA DE INFORME EN PDF (/download-report-pdf) ---
  async function downloadAuditedPdf() {
    if (!state.lastFinalReport) return;
    try {
      const resp = await apiFetch("/download-report-pdf", {
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
      alert(`No se pudo descargar el PDF: ${err.message}`);
    }
  }

  // --- 8. CONTROLES DE AUDIO, WHATSAPP Y EMAIL ---
  function initResponseActionButtons() {
    const btnPlay = document.getElementById("btn-play-audio");
    const btnStop = document.getElementById("btn-stop-audio");
    const statusLbl = document.getElementById("audio-status-label");
    const btnCopyWa = document.getElementById("btn-copy-whatsapp");
    const btnOpenWa = document.getElementById("btn-open-whatsapp");
    const btnCopyEmail = document.getElementById("btn-copy-email");
    const btnOpenEmail = document.getElementById("btn-open-email");

    if (btnPlay) {
      btnPlay.addEventListener("click", () => {
        const scriptText = document.getElementById("report-audio-script")?.textContent || "";
        if (!scriptText) return;
        if (!("speechSynthesis" in window)) {
          if (statusLbl) statusLbl.textContent = "Tu navegador no admite lectura por voz.";
          return;
        }
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(scriptText);
        utterance.lang = "es-AR";
        utterance.rate = 0.98;
        utterance.onstart = () => {
          if (statusLbl) statusLbl.textContent = "Escuchando explicación...";
        };
        utterance.onend = () => {
          if (statusLbl) statusLbl.textContent = "Lectura finalizada.";
        };
        window.speechSynthesis.speak(utterance);
      });
    }

    if (btnStop) {
      btnStop.addEventListener("click", () => {
        if ("speechSynthesis" in window) {
          window.speechSynthesis.cancel();
          if (statusLbl) statusLbl.textContent = "Voz detenida.";
        }
      });
    }

    if (btnCopyWa) {
      btnCopyWa.addEventListener("click", async () => {
        const waText = document.getElementById("report-whatsapp-text")?.value || "";
        if (!waText) return;
        try {
          await navigator.clipboard.writeText(waText);
          btnCopyWa.textContent = "¡Texto copiado!";
          setTimeout(() => {
            btnCopyWa.textContent = "Copiar texto";
          }, 2000);
        } catch (err) {
          console.warn("No se pudo copiar al portapapeles:", err);
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

    if (btnCopyEmail) {
      btnCopyEmail.addEventListener("click", async () => {
        const emailVal = document.getElementById("report-email-text")?.value || "";
        if (!emailVal) return;
        try {
          await navigator.clipboard.writeText(emailVal);
          btnCopyEmail.textContent = "¡Texto copiado!";
          setTimeout(() => {
            btnCopyEmail.textContent = "Copiar texto";
          }, 2000);
        } catch (err) {
          console.warn("No se pudo copiar el email:", err);
        }
      });
    }

    if (btnOpenEmail) {
      btnOpenEmail.addEventListener("click", () => {
        const emailVal = document.getElementById("report-email-text")?.value || "";
        if (!emailVal) return;
        const subject = encodeURIComponent("Consulta sobre estado de mi trámite");
        const body = encodeURIComponent(emailVal);
        window.location.href = `mailto:?subject=${subject}&body=${body}`;
      });
    }
  }

  // --- 9. CARGA DE EJEMPLO PRÁCTICO (/demo-sample-pdf) ---
  async function loadDemoExample() {
    const attachStatus = document.getElementById("attachment-status-text");
    const verbalInput = document.getElementById("input-verbal-statement");
    try {
      const resp = await apiFetch("/demo-sample-pdf");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const pdfBlob = await resp.blob();
      state.currentPdfBlob = pdfBlob;
      state.currentPdfName = "Ejemplo_Caso_Laboral.pdf";
      if (verbalInput && !verbalInput.value.trim()) {
        verbalInput.value =
          "Mi abogado me dijo que ya pidió sentencia la semana pasada y me pide transferirle $45.000 para gastos y firmar un convenio de honorarios del 30%.";
      }
      if (attachStatus) {
        attachStatus.textContent = "Archivo de ejemplo cargado: Ejemplo_Caso_Laboral.pdf";
      }
      await runUnifiedConsultation();
    } catch (err) {
      console.error("Error cargando ejemplo:", err);
    }
  }

  // --- INICIALIZACIÓN DOM ---
  document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();
    initServiceWorker();
    initUserAccess();
    initUnifiedInputControls();
    initResponseActionButtons();

    document.getElementById("btn-demo-pdf")?.addEventListener("click", loadDemoExample);
    document.getElementById("btn-run-full-analysis")?.addEventListener("click", runUnifiedConsultation);
    document.getElementById("btn-download-pdf")?.addEventListener("click", downloadAuditedPdf);
  });
})();
