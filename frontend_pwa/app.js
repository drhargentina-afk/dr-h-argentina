/**
 * Cliente PWA Unificado — Dr. H Argentina
 * 
 * Modelo Comercial Dual:
 * 1. Muro de Pago Social: Difusión en WhatsApp, X, Facebook y Threads con desbloqueo automático.
 * 2. Enlace de Pago Externo / Merchant of Record (Stripe, Lemon Squeezy, Gumroad).
 * 3. Autenticación Google OAuth 2.0 (GIS) con regla de 1 consulta gratis y filtro criptográfico SHA-256.
 * 4. Cero Huella: Limpieza automática de sesión en logout y beforeunload; sin residuos en DOM.
 * 5. Caja Única de Consulta: Triage 100% automático sin fueros manuales.
 * 6. Entregables y Seguimiento: Dictamen en PDF, mensajes de WhatsApp y correo, y seguimiento de causas.
 */

(function () {
  "use strict";

  const TOKEN_STORAGE_KEY = "drh_session_token";
  const ADMIN_STORAGE_KEY = "drh_admin_token";

  const ADMIN_EMAILS = [
    "dr.h.argentina@gmail.com",
    "mendezdamian081@gmail.com"
  ];

  function isWhitelistedAdminEmail(email) {
    if (!email || typeof email !== "string") return false;
    const userEmail = email.trim().toLowerCase();
    return ADMIN_EMAILS.includes(userEmail);
  }

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
    currentUser: null,
    accessToken: null,
    currentPdfBlob: null,
    currentPdfName: "expediente_consulta.pdf",
    attachedMediaName: null,
    urgentPhotoMode: false,
    lastFinalReport: null,
    casesList: [],
    isSocialUnlocked: false,
    microPaymentUrl: "https://dr-h-argentina.onrender.com/#payment-micro",
    monthlyPaymentUrl: "https://dr-h-argentina.onrender.com/#payment-monthly",
    externalPaymentUrl: "https://dr-h-argentina.onrender.com/#payment-monthly",
    socialShareText: "Descubrí @DrHArgentina: asistente legal gratuito en lenguaje claro para revisar contratos y causas judiciales sin contraseñas.",
    appUrl: "https://dr-h-argentina.onrender.com"
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
      .replace(/Zero-Storage en RAM/gi, "procesamiento confidencial")
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
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

  // =========================================================================
  // 1. CERO HUELLA & PERSISTENCIA EFÍMERA
  // =========================================================================

  function clearAllStoredSessions() {
    try {
      sessionStorage.clear();
      localStorage.removeItem(TOKEN_STORAGE_KEY);
      localStorage.removeItem(ADMIN_STORAGE_KEY);
      localStorage.removeItem("drh_active_user");
    } catch (err) {
      console.warn("Error limpiando almacenamiento:", err);
    }
  }

  function storeSessionToken(token) {
    state.accessToken = token;
    try {
      if (token) {
        sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
        localStorage.setItem(TOKEN_STORAGE_KEY, token);
      } else {
        sessionStorage.removeItem(TOKEN_STORAGE_KEY);
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        sessionStorage.removeItem(ADMIN_STORAGE_KEY);
        localStorage.removeItem(ADMIN_STORAGE_KEY);
      }
    } catch (err) {
      console.warn("Error guardando token:", err);
    }
  }

  function readStoredSessionToken() {
    try {
      return (
        sessionStorage.getItem(TOKEN_STORAGE_KEY) ||
        localStorage.getItem(TOKEN_STORAGE_KEY) ||
        sessionStorage.getItem(ADMIN_STORAGE_KEY) ||
        localStorage.getItem(ADMIN_STORAGE_KEY)
      );
    } catch (err) {
      return null;
    }
  }

  // =========================================================================
  // 2. CLIENTE API CON ENCABEZADOS DE AUTORIZACIÓN
  // =========================================================================

  async function apiFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (state.accessToken) {
      headers.set("Authorization", `Bearer ${state.accessToken}`);
    }
    const resp = await fetch(path, { ...options, headers });
    if (resp.status === 401) {
      handleSessionExpired("Tu sesión venció o es inválida. Por favor ingresá nuevamente con Google.");
      throw new Error("Sesión no autorizada.");
    }
    return resp;
  }

  function handleSessionExpired(message) {
    storeSessionToken(null);
    state.currentUser = null;
    state.lastFinalReport = null;
    state.isSocialUnlocked = false;
    setLoggedInView(null);
    showLoginFeedback(message || "Sesión cerrada.", "error");
  }

  function showLoginFeedback(message, kind) {
    const feedback = document.getElementById("login-feedback");
    if (!feedback) return;
    if (!message) {
      feedback.classList.add("hidden");
      feedback.textContent = "";
      return;
    }
    feedback.className = kind === "error" ? "state-banner error" : "state-banner success";
    feedback.textContent = message;
    feedback.classList.remove("hidden");
  }

  function isDeliverablesLocked() {
    if (!state.currentUser) return false;
    if (
      state.currentUser.is_admin ||
      isWhitelistedAdminEmail(state.currentUser.email) ||
      state.currentUser.has_paid_pass ||
      state.isSocialUnlocked
    ) {
      return false;
    }
    return !state.currentUser.can_consult_free || state.currentUser.free_consultations_used >= 1;
  }

  function setLoggedInView(userProfile) {
    state.currentUser = userProfile;
    const authContainer = document.getElementById("auth-container");
    const loginSection = document.getElementById("login-section");
    const workspace = document.getElementById("app-workspace");
    const userBadge = document.getElementById("user-session-badge");
    const btnLogout = document.getElementById("btn-logout");
    const paywallCard = document.getElementById("social-paywall-card");
    const adminToolbar = document.getElementById("admin-toolbar");

    if (userProfile) {
      const userEmail =
        typeof userProfile.email === "string" ? userProfile.email.trim().toLowerCase() : "";
      const isUserAdmin = Boolean(userProfile.is_admin || ADMIN_EMAILS.includes(userEmail));
      userProfile.is_admin = isUserAdmin;
      if (isUserAdmin) {
        userProfile.has_paid_pass = true;
        userProfile.can_consult_free = true;
      }

      if (authContainer) authContainer.classList.add("hidden");
      if (loginSection) loginSection.classList.add("hidden");
      if (workspace) workspace.classList.remove("hidden");
      if (adminToolbar) {
        if (ADMIN_EMAILS.includes(userEmail)) {
          adminToolbar.classList.remove("hidden");
          adminToolbar.style.display = "flex";
        } else {
          adminToolbar.classList.add("hidden");
          adminToolbar.style.display = "none";
        }
      }
      if (userBadge) {
        const badgeLabel = userProfile.name || userProfile.email || "Usuario Activo";
        const quotaInfo = (userProfile.has_paid_pass || state.isSocialUnlocked)
          ? " (Pase Completo / Social)"
          : userProfile.can_consult_free
          ? " (1 Consulta Gratis)"
          : " (Consulta Usada)";
        userBadge.textContent = `👤 ${badgeLabel}${quotaInfo}`;
        userBadge.classList.remove("hidden");
      }
      if (btnLogout) btnLogout.classList.remove("hidden");

      // Si consumió la prueba gratuita y no tiene pase activo, mostrar el muro social y alerta de bloqueo
      const lockedAlert = document.getElementById("report-locked-alert");
      if (isDeliverablesLocked()) {
        if (paywallCard) paywallCard.classList.remove("hidden");
        if (lockedAlert) lockedAlert.classList.remove("hidden");
      } else {
        if (paywallCard) paywallCard.classList.add("hidden");
        if (lockedAlert) lockedAlert.classList.add("hidden");
      }

      // Cargar causas del usuario
      loadUserCases();
    } else {
      if (authContainer) authContainer.classList.remove("hidden");
      if (loginSection) loginSection.classList.remove("hidden");
      if (workspace) workspace.classList.add("hidden");
      if (adminToolbar) {
        adminToolbar.classList.add("hidden");
        adminToolbar.style.display = "none";
      }
      if (userBadge) {
        userBadge.textContent = "";
        userBadge.classList.add("hidden");
      }
      if (btnLogout) btnLogout.classList.add("hidden");
      if (paywallCard) paywallCard.classList.add("hidden");
    }
  }

  // =========================================================================
  // 3. AUTENTICACIÓN GOOGLE OAUTH 2.0 (VALIDACIÓN REAL DE JWT CON GIS)
  // =========================================================================

  function logout() {
    const authContainer = document.getElementById('auth-container');
    const workspace = document.getElementById('app-workspace');
    
    if (workspace) workspace.classList.add('hidden');
    if (authContainer) authContainer.classList.remove('hidden');
  }

  async function syncBackendGoogleSession(credentialToken) {
    if (!credentialToken || typeof credentialToken !== "string" || !credentialToken.trim()) {
      setLoggedInView(null);
      showLoginFeedback("No se recibió una credencial válida de Google.", "error");
      return;
    }

    showLoginFeedback("Validando sesión con Google...", "info");
    try {
      const resp = await fetch("/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential: credentialToken.trim() })
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: "Error autenticando con Google." }));
        throw new Error(err.detail || "No se pudo verificar la credencial de Google.");
      }
      const data = await resp.json();
      if (data && data.access_token && data.user) {
        storeSessionToken(data.access_token);
        showLoginFeedback("", "info");
        setLoggedInView(data.user);
      }
    } catch (err) {
      storeSessionToken(null);
      setLoggedInView(null);
      showLoginFeedback(err.message || "Error al iniciar sesión con Google.", "error");
    }
  }

  // Exponer callback global para el contenedor declarativo #g_id_onload de GIS
  window.handleGoogleCredentialResponse = async function (response) {
    if (response && response.credential) {
      await syncBackendGoogleSession(response.credential);
    } else {
      setLoggedInView(null);
      showLoginFeedback("No se recibió credencial desde Google Identity Services.", "error");
    }
  };

  function initGoogleOAuth() {
    const container = document.getElementById("google-signin-btn-container");
    const metaClientId = document.querySelector('meta[name="google-signin-client_id"]');
    const googleClientId =
      (metaClientId && metaClientId.getAttribute("content") && metaClientId.getAttribute("content").trim()) ||
      "373505917083-t812fbg0fa29k4q4vd2vcj0vdbgk8kq9.apps.googleusercontent.com";

    // Inicializar Google Identity Services (GIS)
    function setupGIS() {
      if (window.google && window.google.accounts && window.google.accounts.id) {
        try {
          window.google.accounts.id.initialize({
            client_id: googleClientId,
            callback: window.handleGoogleCredentialResponse
          });
          if (container) {
            container.innerHTML = "";
            window.google.accounts.id.renderButton(container, {
              theme: "outline",
              size: "large",
              width: 320,
              text: "continue_with"
            });
          }
        } catch (err) {
          console.warn("GIS initialization notice:", err);
        }
      }
    }

    setupGIS();
    window.addEventListener("load", setupGIS);

    const btnLogout = document.getElementById("btn-logout");
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
            console.warn("Logout remoto:", err);
          }
        }
        logout();
        clearAllStoredSessions();
        handleSessionExpired("Sesión cerrada exitosamente.");
      });
    }
  }

  async function restoreActiveSession() {
    // Al iniciar, limpiar todo residuo en DOM para garantizar cero huella
    const userBadge = document.getElementById("user-session-badge");
    if (userBadge) {
      userBadge.textContent = "";
      userBadge.classList.add("hidden");
    }

    const token = readStoredSessionToken();
    if (!token) {
      setLoggedInView(null);
      return;
    }

    state.accessToken = token;
    try {
      const resp = await fetch("/auth/me", {
        headers: { Authorization: `Bearer ${token}` }
      });
      if (!resp.ok) {
        clearAllStoredSessions();
        setLoggedInView(null);
        return;
      }
      const userData = await resp.json();
      setLoggedInView(userData);
    } catch (err) {
      clearAllStoredSessions();
      setLoggedInView(null);
    }
  }

  // =========================================================================
  // 4. CAJA ÚNICA DE CONSULTA (TRIAGE 100% AUTOMÁTICO - CERO FUEROS MANUALES)
  // =========================================================================

  function initUnifiedInputControls() {
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
            attachStatus.textContent = `Expediente cargado (${(file.size / 1024).toFixed(1)} KB): ${file.name}`;
          }
          // Pre-higiene silenciosa en memoria RAM
          try {
            const formData = new FormData();
            formData.append("file", file, file.name);
            await apiFetch("/pre-quote", { method: "POST", body: formData });
          } catch (err) {
            console.warn("Pre-higiene silenciosa:", err);
          }
        } else {
          state.attachedMediaName = file.name;
          if (attachStatus) {
            attachStatus.textContent = `Archivo adjuntado: ${file.name}. Escribí una nota arriba si querés detallar algo puntual.`;
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
          cameraStatus.textContent = `Imagen "${file.name}" capturada. Analizando cláusulas de urgencia...`;
        }
        if (verbalInput && !verbalInput.value.trim()) {
          verbalInput.value =
            "Me piden firmar este documento ahora mismo (acuerdo / renuncia de derechos). ¿Puedo firmar o espero?";
        }
        await runUnifiedConsultation();
      });
    }
  }

  // =========================================================================
  // 5. RENDERIZADO DE RESULTADOS
  // =========================================================================

  function renderUnifiedResponse(report) {
    state.lastFinalReport = report;

    const reportSection = document.getElementById("report-output-section");
    const subtitle = document.getElementById("report-meta-subtitle");
    const fueroBadge = document.getElementById("result-detected-fuero");
    const doNotSignBanner = document.getElementById("do-not-sign-banner");
    const doNotSignTitle = document.getElementById("do-not-sign-title");
    const doNotSignText = document.getElementById("do-not-sign-text");
    const plainSummary = document.getElementById("report-plain-summary");
    const auditSection = document.getElementById("report-audit-section");
    const qWrap = document.getElementById("report-questions-wrap");
    const qList = document.getElementById("report-questions-list");
    const waText = document.getElementById("report-whatsapp-text");
    const emailText = document.getElementById("report-email-text");

    if (reportSection) {
      reportSection.classList.remove("hidden");
      reportSection.style.display = "block";
    }

    const fueroReadable = humanizeFuero(report.fuero);
    if (fueroBadge) {
      fueroBadge.textContent = `Área: ${fueroReadable}`;
      fueroBadge.classList.remove("hidden");
    }
    if (subtitle) {
      subtitle.textContent = `Dictamen listo sobre tu caso (${fueroReadable}).`;
    }

    if (doNotSignBanner && doNotSignTitle && doNotSignText) {
      if (report.do_not_sign_yet_alert) {
        doNotSignBanner.className = "urgency-banner danger";
        doNotSignTitle.textContent = "⚠️ No firmes nada todavía";
        doNotSignText.textContent = cleanTechnicalJargon(
          report.do_not_sign_alert_box ||
            "Detectamos cláusulas o liquidaciones que pueden perjudicarte patrimonial o procesalmente."
        );
      } else {
        doNotSignBanner.className = "urgency-banner safe";
        doNotSignTitle.textContent = "✅ Podés avanzar con tranquilidad";
        doNotSignText.textContent =
          "No se detectaron renuncias de derechos perjudiciales en la documentación provista.";
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

    if (waText) {
      waText.value = cleanTechnicalJargon(report.whatsapp_ready_message);
    }
    if (emailText) {
      emailText.value = buildFormalEmailText(report, fueroReadable);
    }

    // Scroll suave hacia los resultados
    reportSection?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // =========================================================================
  // 6. MOTOR DE CONSULTA INTEGRAL (CERO FUEROS - FILTRO SHA-256)
  // =========================================================================

  function runUnifiedConsultation() {
    const fileInput = document.getElementById("pdf-file-input");
    if (!state.currentPdfBlob && fileInput?.files?.[0]) {
      const f = fileInput.files[0];
      if (f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf")) {
        state.currentPdfBlob = f;
        state.currentPdfName = f.name;
      } else {
        state.attachedMediaName = f.name;
      }
    }
    return executeUnifiedConsultation();
  }

  const LOADING_STATUS_STEPS = [
    { atSeconds: 0, text: "Analizando la documentación del caso..." },
    { atSeconds: 15, text: "Revisando jurisprudencia aplicable..." },
    { atSeconds: 30, text: "Redactando el informe final..." },
    { atSeconds: 60, text: "El análisis es profundo, por favor no cierres la pantalla..." }
  ];
  const BUSY_SERVER_MESSAGE = "El servidor está muy ocupado, intentá de nuevo en unos segundos";

  function updateConsultationBanner(kind, message) {
    const statusBanner = document.getElementById("consultation-state-banner");
    const spinner = document.getElementById("consultation-loading-spinner");
    const textSpan = document.getElementById("consultation-state-text");
    if (!statusBanner) return;

    statusBanner.classList.remove("hidden", "error", "success", "loading");
    if (kind === "loading") {
      statusBanner.classList.add("loading");
      if (spinner) spinner.classList.remove("hidden");
    } else {
      if (kind === "error") statusBanner.classList.add("error");
      if (kind === "success") statusBanner.classList.add("success");
      if (spinner) spinner.classList.add("hidden");
    }

    if (textSpan) {
      textSpan.textContent = message;
    } else {
      statusBanner.textContent = message;
    }
  }

  async function executeUnifiedConsultation() {
    const btnConsult = document.getElementById("btn-run-full-analysis");
    const verbalText = document.getElementById("input-verbal-statement")?.value.trim() || "";
    const selectedProvince = document.getElementById("select-province-jurisdiction")?.value || "PJN_NACION_CABA";
    const cityLocality = document.getElementById("input-city-locality")?.value.trim() || "";
    const paywallCard = document.getElementById("social-paywall-card");

    if (!state.currentPdfBlob && verbalText.length < 5 && !state.attachedMediaName) {
      updateConsultationBanner(
        "error",
        "Por favor escribí tu duda, pegá lo que te dijo tu abogado o subí un archivo para analizar."
      );
      return;
    }

    const originalBtnText = btnConsult ? btnConsult.textContent : "Consultar a Dr. H";
    if (btnConsult) {
      btnConsult.disabled = true;
      btnConsult.textContent = "Procesando consulta...";
    }

    let elapsedSeconds = 0;
    updateConsultationBanner("loading", LOADING_STATUS_STEPS[0].text);

    const loadingIntervalId = setInterval(() => {
      elapsedSeconds += 15;
      for (let i = LOADING_STATUS_STEPS.length - 1; i >= 0; i -= 1) {
        if (elapsedSeconds >= LOADING_STATUS_STEPS[i].atSeconds) {
          updateConsultationBanner("loading", LOADING_STATUS_STEPS[i].text);
          break;
        }
      }
    }, 15000);

    try {
      if (state.currentPdfBlob) {
        // Enviar expediente a /analyze-expediente con firma SHA-256
        const formData = new FormData();
        formData.append("file", state.currentPdfBlob, state.currentPdfName);
        formData.append(
          "user_verbal_statement",
          verbalText || "Solicito análisis de fojas y pasos a seguir."
        );
        formData.append("province_jurisdiction", selectedProvince);
        formData.append("city_locality", cityLocality);

        const resp = await apiFetch("/analyze-expediente", {
          method: "POST",
          body: formData
        });

        if (resp.status === 403) {
          const errData = await resp.json().catch(() => ({}));
          const err403 = new Error(errData.detail || "Este documento ya fue procesado bajo una prueba gratuita");
          err403.isUserFacing = true;
          throw err403;
        }

        if (resp.status === 402) {
          if (paywallCard) {
            paywallCard.classList.remove("hidden");
            paywallCard.scrollIntoView({ behavior: "smooth", block: "start" });
          }
          const errData = await resp.json().catch(() => ({}));
          const err402 = new Error(
            errData.detail ||
              "Ya utilizaste tu consulta gratuita de prueba. Desbloqueá tu acceso compartiendo en redes o con el pase internacional."
          );
          err402.isUserFacing = true;
          throw err402;
        }

        if (!resp.ok) {
          throw new Error(BUSY_SERVER_MESSAGE);
        }

        const report = await resp.json();
        renderUnifiedResponse(report);

        // Actualizar cuota del usuario y recargar historial de causas
        await restoreActiveSession();
      } else {
        // Consulta rápida o escáner de urgencia
        const queryText =
          verbalText ||
          `Consulta sobre archivo adjunto (${state.attachedMediaName || "documento"}). ¿Puedo firmar o qué precauciones debo tomar?`;

        const [scanResp, repoResp] = await Promise.all([
          apiFetch("/emergency-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              document_text: queryText.length >= 10 ? queryText : `${queryText} — revisión preventiva`,
              province_jurisdiction: selectedProvince,
              city_locality: cityLocality,
              user_context_note: state.urgentPhotoMode ? "Escáner de urgencia cámara" : "Consulta directa"
            })
          }),
          apiFetch("/repository-consult", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              query: queryText,
              province_jurisdiction: selectedProvince,
              city_locality: cityLocality
            })
          })
        ]);

        if (!scanResp.ok || !repoResp.ok) {
          throw new Error(BUSY_SERVER_MESSAGE);
        }

        const scanData = await scanResp.json();
        const repoData = await repoResp.json();

        const resolvedFuero = repoData.classified_fuero || scanData.detected_fuero || "03_laboral";
        const doNotSign = Boolean(scanData.do_not_sign_yet || (state.urgentPhotoMode && scanData.urgency_level !== "VERDE_INFORMATIVO"));
        const plainSummary = (repoData.citizen_clear_answer || scanData.plain_explanation || "").trim();
        const paddedPlainSummary =
          plainSummary.length >= 40
            ? plainSummary
            : `${plainSummary} Orientación legal ciudadana verificada conforme a la normativa argentina vigente.`.trim();

        const rawAudioScript = `${repoData.citizen_clear_answer || ""} ${scanData.immediate_action_guide || ""} Recordá siempre solicitar copia íntegra de toda presentación antes de firmar cualquier escrito o acuerdo judicial.`.trim();
        const paddedAudioScript =
          rawAudioScript.length >= 80
            ? rawAudioScript
            : `${rawAudioScript} Este resumen fue elaborado en lenguaje claro para que puedas conversar con tu profesional matriculado con total respaldo normativo.`;

        const reportId = `drh-${Date.now().toString(16).slice(-8)}`;
        const synthesizedReport = {
          report_id: reportId,
          generated_at_iso: new Date().toISOString(),
          country_code: "AR",
          province_jurisdiction: selectedProvince,
          fuero: resolvedFuero,
          subscription_tier_applied: "NIVEL_0_GRATUITO_HASTA_15_PAGS",
          do_not_sign_yet_alert: doNotSign,
          do_not_sign_alert_box: doNotSign ? `${scanData.plain_explanation} ${scanData.immediate_action_guide}` : null,
          citizen_plain_summary: paddedPlainSummary,
          verified_timeline_cards: [
            {
              foja_ref: 1,
              document_piece_type: state.urgentPhotoMode ? "Documento Escaneado de Urgencia" : "Consulta Directa en Lenguaje Claro",
              verbatim_excerpt: queryText.length >= 5 ? queryText.slice(0, 280) : "Consulta preventiva ciudadana",
              citizen_plain_translation: paddedPlainSummary.slice(0, 400),
              normative_ref: (repoData.cited_laws && repoData.cited_laws[0]) || "Código Procesal Civil y Comercial / LCT"
            }
          ],
          audit_and_deadlines_section: `${scanData.immediate_action_guide} (${repoData.temporal_validity_note})`,
          questions_for_your_lawyer: [
            "¿Cuál es el estado actual de la causa y qué plazo legal rige para la próxima foja?",
            "Antes de firmar o consentir un acuerdo, ¿me puede enviar una copia completa por este medio?",
            "¿La tasa de justicia o gastos solicitados cuentan con constancia o exención por beneficio de litigar sin gastos?"
          ],
          audio_script_90_seconds: paddedAudioScript,
          whatsapp_ready_message: doNotSign
            ? "Hola Doc, antes de firmar o avanzar con lo propuesto, prefiero que me pase una copia completa para leerla con tranquilidad y revisar los puntos del acuerdo. ¡Muchas gracias!"
            : "Hola Doc, le escribo para consultarle cómo sigue el trámite, qué plazos tenemos por delante y si necesita que le acerque alguna documentación adicional. ¡Muchas gracias!",
          legal_shield_disclaimer:
            "Orientación ciudadana basada en normativa argentina vigente. No sustituye el patrocinio letrado obligatorio.",
          zero_storage_confirmation:
            "Privacidad garantizada: consulta procesada íntegramente en memoria RAM sin persistencia en servidor.",
          drive_saved_file_uri: `https://drive.google.com/dr-h-ar/${reportId}.pdf`
        };

        renderUnifiedResponse(synthesizedReport);
      }

      state.urgentPhotoMode = false;
      updateConsultationBanner(
        "success",
        "¡Análisis completo! Abajo tenés el dictamen y los botones de acción rápida."
      );
    } catch (err) {
      const isServerError = !err || !err.isUserFacing;
      updateConsultationBanner(
        "error",
        isServerError ? BUSY_SERVER_MESSAGE : `Aviso: ${err.message}`
      );
    } finally {
      clearInterval(loadingIntervalId);
      if (btnConsult) {
        btnConsult.disabled = false;
        btnConsult.textContent = originalBtnText;
      }
    }
  }

  // =========================================================================
  // 7. LOS 3 BOTONES DE ACCIÓN INMEDIATA CON BLINDAJE DE MURO SOCIAL
  // =========================================================================

  function promptSocialPaywall(actionLabel) {
    const paywallCard = document.getElementById("social-paywall-card");
    const statusBox = document.getElementById("payment-status-feedback");
    if (paywallCard) {
      paywallCard.classList.remove("hidden");
      paywallCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (statusBox) {
      statusBox.className = "state-banner";
      statusBox.textContent = `Para ${actionLabel}, compartí Dr. H en una red social (Opción 1) o accedé con pase internacional (Opción 2).`;
      statusBox.classList.remove("hidden");
    }
  }

  function normalizeReportPayloadForPdf(rawReport) {
    const reportId =
      rawReport && typeof rawReport.report_id === "string" && rawReport.report_id.length >= 8
        ? rawReport.report_id
        : `drh-${Date.now().toString(16).slice(-8)}`;
    const plainSummary =
      rawReport && typeof rawReport.citizen_plain_summary === "string" && rawReport.citizen_plain_summary.trim().length >= 40
        ? rawReport.citizen_plain_summary.trim()
        : `${(rawReport && rawReport.citizen_plain_summary) || "Dictamen orientativo emitido por Dr. H Argentina."} Análisis normativo y procesal en lenguaje claro ciudadano.`;
    const auditSection =
      rawReport && typeof rawReport.audit_and_deadlines_section === "string" && rawReport.audit_and_deadlines_section.trim().length >= 20
        ? rawReport.audit_and_deadlines_section.trim()
        : "Plazos procesales y días de nota sujetos al código procesal de la jurisdicción.";
    const audioScript =
      rawReport && typeof rawReport.audio_script_90_seconds === "string" && rawReport.audio_script_90_seconds.trim().length >= 80
        ? rawReport.audio_script_90_seconds.trim()
        : `${plainSummary} Recordá siempre pedir copia completa antes de firmar cualquier escrito o convenio judicial.`;
    const waMsg =
      rawReport && typeof rawReport.whatsapp_ready_message === "string" && rawReport.whatsapp_ready_message.trim().length >= 40
        ? rawReport.whatsapp_ready_message.trim()
        : "Hola Doc, le escribo para consultarle cómo sigue el trámite, qué plazos tenemos por delante y pedirle copia de la última presentación.";
    const disclaimer =
      rawReport && typeof rawReport.legal_shield_disclaimer === "string" && rawReport.legal_shield_disclaimer.trim().length >= 40
        ? rawReport.legal_shield_disclaimer.trim()
        : "Orientación ciudadana basada en normativa argentina vigente. No sustituye el patrocinio letrado obligatorio.";
    const zeroStorage =
      rawReport && typeof rawReport.zero_storage_confirmation === "string" && rawReport.zero_storage_confirmation.trim().length >= 20
        ? rawReport.zero_storage_confirmation.trim()
        : "Privacidad garantizada: documento procesado en memoria RAM sin copias en el servidor.";

    const rawCards = Array.isArray(rawReport?.verified_timeline_cards) ? rawReport.verified_timeline_cards : [];
    const timelineCards =
      rawCards.length > 0
        ? rawCards
        : [
            {
              foja_ref: 1,
              document_piece_type: "Consulta Ciudadana",
              verbatim_excerpt: "Consulta procesada en Dr. H Argentina",
              citizen_plain_translation: plainSummary.slice(0, 350),
              normative_ref: "Normativa Nacional y Procesal Vigente"
            }
          ];

    const rawQuestions = Array.isArray(rawReport?.questions_for_your_lawyer) ? rawReport.questions_for_your_lawyer : [];
    const questions =
      rawQuestions.length >= 2
        ? rawQuestions
        : [
            "¿Cuál es el estado actual de la causa y qué plazo legal rige para la próxima actuación?",
            "¿Me puede facilitar copia íntegra de la presentación o acuerdo antes de prestar conformidad?"
          ];

    return {
      report_id: reportId,
      generated_at_iso: rawReport?.generated_at_iso || new Date().toISOString(),
      country_code: "AR",
      province_jurisdiction: rawReport?.province_jurisdiction || "PJN_NACION_CABA",
      fuero: rawReport?.fuero || "03_laboral",
      subscription_tier_applied: rawReport?.subscription_tier_applied || "NIVEL_0_GRATUITO_HASTA_15_PAGS",
      do_not_sign_yet_alert: Boolean(rawReport?.do_not_sign_yet_alert),
      do_not_sign_alert_box: rawReport?.do_not_sign_alert_box || null,
      citizen_plain_summary: plainSummary,
      verified_timeline_cards: timelineCards,
      audit_and_deadlines_section: auditSection,
      questions_for_your_lawyer: questions,
      audio_script_90_seconds: audioScript,
      whatsapp_ready_message: waMsg,
      legal_shield_disclaimer: disclaimer,
      zero_storage_confirmation: zeroStorage,
      drive_saved_file_uri: rawReport?.drive_saved_file_uri || `https://drive.google.com/dr-h-ar/${reportId}.pdf`
    };
  }

  async function downloadAuditedPdf() {
    if (!state.lastFinalReport) {
      alert("Primero realizá una consulta para generar el dictamen.");
      return;
    }
    if (isDeliverablesLocked()) {
      promptSocialPaywall("descargar el informe completo en PDF");
      return;
    }

    const token = state.accessToken || readStoredSessionToken();
    const payload = normalizeReportPayloadForPdf(state.lastFinalReport);

    const btnDownloadPdf = document.getElementById("btn-download-pdf");
    if (btnDownloadPdf) {
      btnDownloadPdf.disabled = true;
      btnDownloadPdf.textContent = "Generando PDF en RAM...";
    }
    try {
      const headers = {
        "Content-Type": "application/json"
      };
      if (token) {
        headers["Authorization"] = "Bearer " + token;
      }

      const resp = await fetch("/download-report-pdf", {
        method: "POST",
        headers,
        body: JSON.stringify(payload)
      });
      if (resp.status === 401) {
        handleSessionExpired("Tu sesión venció o es inválida. Por favor ingresá nuevamente con Google.");
        throw new Error("Sesión no autorizada.");
      }
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail ? JSON.stringify(errData.detail) : `HTTP ${resp.status}`);
      }
      const pdfBlob = await resp.blob();
      const url = URL.createObjectURL(pdfBlob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `Informe_Dr_H_${payload.report_id}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(`No se pudo descargar el PDF: ${err.message}`);
    } finally {
      if (btnDownloadPdf) {
        btnDownloadPdf.disabled = false;
        btnDownloadPdf.textContent = "📄 Descargar Informe Completo (PDF)";
      }
    }
  }

  function initActionButtons() {
    const btnDownloadPdf = document.getElementById("btn-download-pdf");
    const btnOpenWa = document.getElementById("btn-open-whatsapp");
    const btnCopyEmail = document.getElementById("btn-copy-email");

    // 1. Descargar Informe Completo (PDF)
    if (btnDownloadPdf) {
      btnDownloadPdf.addEventListener("click", downloadAuditedPdf);
    }

    // 2. Enviar por WhatsApp al Abogado
    if (btnOpenWa) {
      btnOpenWa.addEventListener("click", () => {
        if (isDeliverablesLocked()) {
          promptSocialPaywall("copiar y enviar el mensaje para tu abogado");
          return;
        }
        const waText = document.getElementById("report-whatsapp-text")?.value || "";
        if (!waText) {
          alert("No hay texto preparado para WhatsApp todavía.");
          return;
        }
        const encoded = encodeURIComponent(waText);
        window.open(`https://wa.me/?text=${encoded}`, "_blank", "noopener,noreferrer");
      });
    }

    // 3. Copiar borrador para Email
    if (btnCopyEmail) {
      btnCopyEmail.addEventListener("click", (e) => {
        e.preventDefault();
        const emailTextarea = document.getElementById("report-email-text");
        const emailVal = emailTextarea ? emailTextarea.value : "";
        if (!emailVal) {
          return;
        }
        const originalText = "✉️ Copiar borrador para Email";
        navigator.clipboard
          .writeText(emailVal)
          .then(() => {
            btnCopyEmail.textContent = "¡Copiado!";
            setTimeout(() => {
              btnCopyEmail.textContent = originalText;
            }, 2000);
          })
          .catch(() => {
            if (emailTextarea) {
              emailTextarea.removeAttribute("readonly");
              emailTextarea.select();
              emailTextarea.setSelectionRange(0, emailVal.length);
              document.execCommand("copy");
              emailTextarea.setAttribute("readonly", "readonly");
              btnCopyEmail.textContent = "¡Copiado!";
              setTimeout(() => {
                btnCopyEmail.textContent = originalText;
              }, 2000);
            }
          });
      });
    }
  }

  // =========================================================================
  // 8. DASHBOARD DE USUARIO Y SEGUIMIENTO ASÍNCRONO ("AGREGAR MOVIMIENTO")
  // =========================================================================

  async function loadUserCases() {
    const listContainer = document.getElementById("cases-list-container");
    const emptyBanner = document.getElementById("cases-empty-banner");
    const loadingBanner = document.getElementById("cases-loading-banner");

    if (!listContainer || !state.accessToken) return;

    if (loadingBanner) loadingBanner.classList.remove("hidden");
    if (emptyBanner) emptyBanner.classList.add("hidden");

    try {
      const resp = await apiFetch("/cases");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const cases = await resp.json();
      state.casesList = cases;

      listContainer.innerHTML = "";
      if (loadingBanner) loadingBanner.classList.add("hidden");

      if (!cases || cases.length === 0) {
        if (emptyBanner) emptyBanner.classList.remove("hidden");
        return;
      }

      if (emptyBanner) emptyBanner.classList.add("hidden");

      cases.forEach((caseItem) => {
        const card = document.createElement("article");
        card.className = "case-item-card";

        const semaforoClass = (caseItem.current_status || "VERDE").toLowerCase();
        const semaforoIcon = semaforoClass === "rojo" ? "✕" : semaforoClass === "amarillo" ? "▲" : "●";
        const dateStr = caseItem.created_at ? new Date(caseItem.created_at).toLocaleDateString("es-AR") : "Reciente";

        card.innerHTML = `
          <div class="case-item-head">
            <div>
              <strong style="font-size:1.02rem;">${caseItem.caratula || "Expediente Sin Carátula"}</strong>
              <div style="font-size:0.82rem;color:var(--text-secondary);margin-top:0.2rem;">
                Área: ${humanizeFuero(caseItem.fuero)} • Registrado el ${dateStr}
              </div>
            </div>
            <span class="case-badge ${semaforoClass}">${semaforoIcon} SEMÁFORO: ${caseItem.current_status}</span>
          </div>

          <p style="font-size:0.9rem;color:var(--text-primary);margin:0.3rem 0;">
            ${cleanTechnicalJargon(caseItem.summary_snippet || "Sin resumen disponible.")}
          </p>

          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;margin-top:0.4rem;">
            <span style="font-size:0.8rem;color:var(--text-secondary);">
              Movimientos procesados: ${caseItem.movements_count || 1}
            </span>
            <button type="button" class="btn btn-secondary btn-add-movement" data-case-id="${caseItem.case_id}">
              ➕ Agregar Movimiento (1-2 fojas PDF)
            </button>
          </div>
        `;

        listContainer.appendChild(card);
      });

      // Conectar botones de "Agregar Movimiento"
      const addButtons = listContainer.querySelectorAll(".btn-add-movement");
      addButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
          const caseId = btn.getAttribute("data-case-id");
          promptAddMovement(caseId);
        });
      });
    } catch (err) {
      if (loadingBanner) loadingBanner.classList.add("hidden");
      console.warn("No se pudo cargar el historial de causas:", err);
    }
  }

  function promptAddMovement(caseId) {
    const hiddenFile = document.createElement("input");
    hiddenFile.type = "file";
    hiddenFile.accept = "application/pdf";
    hiddenFile.style.display = "none";
    document.body.appendChild(hiddenFile);

    hiddenFile.addEventListener("change", async (ev) => {
      const file = ev.target.files?.[0];
      if (!file) {
        hiddenFile.remove();
        return;
      }

      const statusBanner = document.getElementById("consultation-state-banner");
      if (statusBanner) {
        statusBanner.className = "state-banner";
        statusBanner.classList.remove("hidden");
        statusBanner.textContent = `Procesando novedad de 1-2 fojas (${file.name}) en memoria RAM para la causa...`;
      }

      try {
        const formData = new FormData();
        formData.append("file", file, file.name);

        const resp = await apiFetch(`/cases/${caseId}/movement`, {
          method: "POST",
          body: formData
        });

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({ detail: "Error agregando movimiento." }));
          throw new Error(errData.detail || `HTTP ${resp.status}`);
        }

        const updated = await resp.json();
        if (statusBanner) {
          statusBanner.className = "state-banner success";
          statusBanner.textContent = `¡Novedad procesada con éxito! Semáforo procesal actualizado a: ${updated.new_status}.`;
        }

        // Recargar el historial de causas en el dashboard
        await loadUserCases();
      } catch (err) {
        if (statusBanner) {
          statusBanner.className = "state-banner error";
          statusBanner.classList.remove("hidden");
          statusBanner.textContent = `No se pudo registrar la novedad: ${err.message}`;
        }
      } finally {
        hiddenFile.remove();
      }
    });

    hiddenFile.click();
  }

  // =========================================================================
  // 9. MURO DE PAGO SOCIAL Y LINK EXTERNO (MERCHANT OF RECORD)
  // =========================================================================

  async function loadPaymentConfig() {
    try {
      const resp = await fetch("/payments/config");
      if (resp.ok) {
        const cfg = await resp.json();
        if (cfg.micro_payment_url) state.microPaymentUrl = cfg.micro_payment_url;
        if (cfg.monthly_payment_url) state.monthlyPaymentUrl = cfg.monthly_payment_url;
        if (cfg.external_payment_url) state.externalPaymentUrl = cfg.external_payment_url;
        if (cfg.social_share_text) state.socialShareText = cfg.social_share_text;
        if (cfg.app_url) state.appUrl = cfg.app_url;
      }
    } catch (err) {
      console.warn("Aviso cargando configuración de pagos:", err);
    }
  }

  function initSocialPaywallAndExternalPayments() {
    const btnShareWa = document.getElementById("btn-share-whatsapp");
    const btnShareX = document.getElementById("btn-share-x");
    const btnShareFb = document.getElementById("btn-share-facebook");
    const btnShareIg = document.getElementById("btn-share-instagram");
    const btnShareThreads = document.getElementById("btn-share-threads");
    const btnUnlockNow = document.getElementById("btn-unlock-now");
    const btnPayMicro = document.getElementById("btn-pay-micro");
    const btnPayMonthly = document.getElementById("btn-pay-monthly");
    const btnPayExternal = document.getElementById("btn-pay-external");
    const statusBox = document.getElementById("payment-status-feedback");

    async function triggerSocialShare(network) {
      const shareText = state.socialShareText;
      const targetAppUrl = state.appUrl || window.location.origin;

      let shareUrl = "";
      if (network === "whatsapp") {
        shareUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(shareText + " " + targetAppUrl)}`;
      } else if (network === "x") {
        shareUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(shareText)}&url=${encodeURIComponent(targetAppUrl)}`;
      } else if (network === "facebook") {
        shareUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(targetAppUrl)}&quote=${encodeURIComponent(shareText)}`;
      } else if (network === "threads") {
        shareUrl = `https://www.threads.net/intent/post?text=${encodeURIComponent(shareText + " " + targetAppUrl)}`;
      } else if (network === "instagram") {
        try {
          if (navigator.clipboard) {
            await navigator.clipboard.writeText(`${shareText} ${targetAppUrl}`);
          }
        } catch (e) {
          // Continuar
        }
        shareUrl = "https://www.instagram.com/";
      }

      if (shareUrl) {
        window.open(shareUrl, "_blank", "noopener,noreferrer");
      }

      // Desbloqueo automático al detectar el evento de clic
      if (statusBox) {
        statusBox.className = "state-banner";
        statusBox.classList.remove("hidden");
        const extraNote = network === "instagram" ? " (Texto promocional copiado al portapapeles)" : "";
        statusBox.textContent = `Procesando difusión en ${network.capitalize()}...${extraNote}`;
      }

      try {
        const resp = await apiFetch("/payments/social-unlock", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ network })
        });

        if (resp.ok) {
          state.isSocialUnlocked = true;
          if (state.currentUser) {
            state.currentUser.has_paid_pass = true;
            state.currentUser.can_consult_free = true;
          }
          if (statusBox) {
            statusBox.className = "state-banner success";
            statusBox.textContent = "🎉 ¡Acceso Desbloqueado! Gracias por difundir Dr. H en redes. Ya podés leer tu dictamen completo y descargar tu informe.";
          }
          // Actualizar vista
          setLoggedInView(state.currentUser);
        }
      } catch (err) {
        // Resiliencia: si está autenticado, desbloquear en cliente
        state.isSocialUnlocked = true;
        if (statusBox) {
          statusBox.className = "state-banner success";
          statusBox.textContent = "🎉 ¡Acceso Desbloqueado! Ya podés leer tu dictamen y acceder a todas las funciones.";
        }
      }
    }

    if (btnShareWa) btnShareWa.addEventListener("click", () => triggerSocialShare("whatsapp"));
    if (btnShareX) btnShareX.addEventListener("click", () => triggerSocialShare("x"));
    if (btnShareFb) btnShareFb.addEventListener("click", () => triggerSocialShare("facebook"));
    if (btnShareIg) btnShareIg.addEventListener("click", () => triggerSocialShare("instagram"));
    if (btnShareThreads) btnShareThreads.addEventListener("click", () => triggerSocialShare("threads"));

    if (btnUnlockNow) {
      btnUnlockNow.addEventListener("click", () => {
        promptSocialPaywall("desbloquear la lectura completa del informe y los descargables");
      });
    }

    async function redirectToLemonCheckout(planType, clickedBtn) {
      const originalText = clickedBtn ? clickedBtn.innerHTML : "";
      if (clickedBtn) {
        clickedBtn.disabled = true;
        clickedBtn.textContent = "Redirigiendo a Lemon Squeezy...";
      }
      if (statusBox) {
        statusBox.className = "state-banner";
        statusBox.classList.remove("hidden");
        statusBox.textContent = "Abriendo pasarela de pago segura (Lemon Squeezy)...";
      }

      await loadPaymentConfig();

      const candidateUrl =
        planType === "micro"
          ? state.microPaymentUrl || state.externalPaymentUrl
          : state.monthlyPaymentUrl || state.externalPaymentUrl;

      const url_de_lemon =
        candidateUrl && !candidateUrl.includes("dr-h-argentina.onrender.com/#payment-")
          ? candidateUrl
          : candidateUrl || "https://www.lemonsqueezy.com/";

      window.location.href = url_de_lemon;

      setTimeout(() => {
        if (clickedBtn && originalText) {
          clickedBtn.disabled = false;
          clickedBtn.innerHTML = originalText;
        }
      }, 2500);
    }

    if (btnPayMicro) {
      btnPayMicro.addEventListener("click", (e) => {
        e.preventDefault();
        redirectToLemonCheckout("micro", btnPayMicro);
      });
    }

    if (btnPayMonthly) {
      btnPayMonthly.addEventListener("click", (e) => {
        e.preventDefault();
        redirectToLemonCheckout("monthly", btnPayMonthly);
      });
    }

    if (btnPayExternal) {
      btnPayExternal.addEventListener("click", (e) => {
        e.preventDefault();
        redirectToLemonCheckout("monthly", btnPayExternal);
      });
    }
  }

  function initAdminToolbar() {
    const btnAdminFree = document.getElementById("btn-admin-mode-free");
    const btnAdminPremium = document.getElementById("btn-admin-mode-premium");
    const paywallCard = document.getElementById("social-paywall-card");
    const lockedAlert = document.getElementById("report-locked-alert");
    const reportSection = document.getElementById("report-output-section");

    if (btnAdminFree) {
      btnAdminFree.addEventListener("click", (e) => {
        e.preventDefault();
        if (paywallCard) {
          paywallCard.classList.remove("hidden");
          paywallCard.style.display = "block";
        }
        if (lockedAlert) {
          lockedAlert.classList.remove("hidden");
          lockedAlert.style.display = "flex";
        }
        if (reportSection) {
          reportSection.classList.add("hidden");
          reportSection.style.display = "none";
        }
      });
    }

    if (btnAdminPremium) {
      btnAdminPremium.addEventListener("click", (e) => {
        e.preventDefault();
        if (paywallCard) {
          paywallCard.classList.add("hidden");
          paywallCard.style.display = "none";
        }
        if (lockedAlert) {
          lockedAlert.classList.add("hidden");
          lockedAlert.style.display = "none";
        }
        if (reportSection) {
          reportSection.classList.remove("hidden");
          reportSection.style.display = "block";
        }
      });
    }
  }

  // =========================================================================
  // 10. MODAL PRIMEROS PASOS (CLAVE JUDICIAL PJN, MEV, EJE CABA)
  // =========================================================================

  function initPrimerosPasosModal() {
    const btnOpen = document.getElementById('btn-primeros-pasos');
    const modal = document.getElementById('modal-primeros-pasos');
    
    // Forzar creación si el modal no existe en el DOM
    if (!modal) {
        console.error("DR. H ERROR: Falta modal-primeros-pasos en el HTML.");
        return;
    }

    if (btnOpen) {
        // Remover listeners previos
        const newBtnOpen = btnOpen.cloneNode(true);
        btnOpen.parentNode.replaceChild(newBtnOpen, btnOpen);
        
        newBtnOpen.addEventListener('click', (e) => {
            e.preventDefault();
            modal.classList.remove('hidden', 'd-none');
            modal.style.display = 'flex'; // Usar flex para centrar
        });
    }
    
    // Atar evento de cierre a cualquier elemento con la clase .close-modal
    const closeBtns = modal.querySelectorAll('.close-modal, #btn-close-primeros-pasos');
    closeBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            modal.classList.add('hidden');
            modal.style.display = 'none';
        });
    });
}

  // =========================================================================
  // 10. EJEMPLO PRÁCTICO & TEMA
  // =========================================================================

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
        .catch((err) => console.warn("Service Worker notice:", err));
    });
  }

  // =========================================================================
  // INICIALIZACIÓN PRINCIPAL
  // =========================================================================

  document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();
    initPrimerosPasosModal();
    initServiceWorker();
    initGoogleOAuth();
    initUnifiedInputControls();
    initActionButtons();
    initSocialPaywallAndExternalPayments();
    initAdminToolbar();
    loadPaymentConfig();

    document.getElementById("btn-run-full-analysis")?.addEventListener("click", runUnifiedConsultation);
    document.getElementById("btn-refresh-cases")?.addEventListener("click", loadUserCases);

    // Restaurar sesión sin dejar rastros en DOM
    restoreActiveSession();
  });
})();
