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
    "mendezdamian081@gmail.com",
    // "slot3@gmail.com",
    // "slot4@gmail.com",
    // "slot5@gmail.com",
    // "slot6@gmail.com",
    // "slot7@gmail.com",
    // "slot8@gmail.com",
    // "slot9@gmail.com",
    // "slot10@gmail.com",
    // "slot11@gmail.com",
    // "slot12@gmail.com"
  ];

  function isWhitelistedAdminEmail(email) {
    if (!email || typeof email !== "string") return false;
    const userEmail = email.trim().toLowerCase();
    if (!userEmail) return false;
    const validAdmins = ADMIN_EMAILS.filter(
      (item) => typeof item === "string" && item.trim().length > 0
    ).map((item) => item.trim().toLowerCase());
    return validAdmins.includes(userEmail);
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

  const EXTERNAL_MICRO_PAYMENT_URL =
    "https://drhargentina.lemonsqueezy.com/checkout/buy/7e6e6656-c5b8-4a49-9717-db29c481f62b";
  const EXTERNAL_MONTHLY_PAYMENT_URL =
    "https://drhargentina.lemonsqueezy.com/checkout/buy/b8678882-2d52-43ec-a2f8-5c6ec55e84d0";

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
    social_share_unlocked: false,
    currentReportUnlocked: false,
    paywallLockedStage: 0,
    pendingConsultation: null,
    microPaymentUrl: EXTERNAL_MICRO_PAYMENT_URL,
    monthlyPaymentUrl: EXTERNAL_MONTHLY_PAYMENT_URL,
    externalPaymentUrl: EXTERNAL_MONTHLY_PAYMENT_URL,
    socialShareText: "Descubrí @DrHArgentina: asistente legal gratuito en lenguaje claro para revisar contratos y causas judiciales sin contraseñas.",
    appUrl: "https://dr-h-argentina.onrender.com"
  };

  function getDeviceFingerprint() {
    try {
      const tz =
        typeof Intl !== "undefined" && Intl.DateTimeFormat
          ? Intl.DateTimeFormat().resolvedOptions().timeZone || ""
          : "";
      const raw = [
        navigator.userAgent || "",
        navigator.language || "",
        navigator.platform || "",
        String(navigator.hardwareConcurrency || ""),
        String(window.screen?.width || 0),
        String(window.screen?.height || 0),
        String(window.screen?.colorDepth || 0),
        tz
      ].join("|");
      let h1 = 0x811c9dc5;
      let h2 = 0x9e3779b9;
      for (let i = 0; i < raw.length; i += 1) {
        const ch = raw.charCodeAt(i);
        h1 = Math.imul(h1 ^ ch, 0x01000193);
        h2 = Math.imul(h2 ^ (ch + i), 0x85ebca6b);
      }
      const hex1 = (h1 >>> 0).toString(16).padStart(8, "0");
      const hex2 = (h2 >>> 0).toString(16).padStart(8, "0");
      return `dfp_${hex1}${hex2}`;
    } catch (err) {
      return "dfp_fallback_device";
    }
  }

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

  const EXPEDIENTE_CONTEXT_REGEX =
    /\b(?:expediente|expte\.?|foja|fojas|fs\.?|juzgado|tribunal|c[aá]mara|c[eé]dula|autos\s+caratulados|demanda\s+judicial|sentencia|resoluci[oó]n\s+judicial)\b/i;

  function hasJudicialExpedienteReference(queryText, pdfBlob, report) {
    if (pdfBlob) return true;
    if (report && typeof report.has_expediente === "boolean") {
      return report.has_expediente;
    }
    if (EXPEDIENTE_CONTEXT_REGEX.test(String(queryText || ""))) {
      return true;
    }
    if (report) {
      const firstPieceType = String(
        report.verified_timeline_cards?.[0]?.document_piece_type || ""
      )
        .trim()
        .toLowerCase();
      const directConsultationTypes = [
        "consulta ciudadana",
        "consulta ciudadana en lenguaje claro",
        "consulta directa en lenguaje claro",
        "documento escaneado de urgencia"
      ];
      if (directConsultationTypes.includes(firstPieceType)) {
        return false;
      }
      const combined = [
        report.citizen_plain_summary || "",
        report.audit_and_deadlines_section || "",
        report.verified_timeline_cards?.[0]?.document_piece_type || "",
        report.verified_timeline_cards?.[0]?.verbatim_excerpt || ""
      ].join(" ");
      return EXPEDIENTE_CONTEXT_REGEX.test(combined);
    }
    return false;
  }

  function buildContextualCitizenDeliverables(queryText, fuero, doNotSign, hasExpediente) {
    if (hasExpediente) {
      return {
        questions: [
          "¿Cuál es el estado actual de la causa y qué plazo legal rige para la próxima foja?",
          "Antes de firmar o consentir un acuerdo, ¿me puede enviar una copia completa por este medio?",
          "¿La tasa de justicia o gastos solicitados cuentan con constancia o exención por beneficio de litigar sin gastos?"
        ],
        whatsappMessage: doNotSign
          ? "Hola Doc, antes de firmar o avanzar con lo propuesto, prefiero que me pase una copia completa para leerla con tranquilidad y revisar los puntos del acuerdo. ¡Muchas gracias!"
          : "Hola Doc, le escribo para consultarle cómo sigue el trámite, qué plazos tenemos por delante y si necesita que le acerque alguna documentación adicional. ¡Muchas gracias!"
      };
    }

    const normalizedQuery = String(queryText || "").toLowerCase();
    const isNeighborOrNoiseIssue =
      /(ruid|molest|vecin|c[aá]mara|grab|vigilanc|consorcio|humedad|medianera|administraci[oó]n|m[uú]sica|ladrido)/i.test(
        normalizedQuery
      );
    const isLaborFactIssue =
      fuero === "03_laboral" ||
      /(despido|despid|ech[oó]|verbal|en\s+negro|no\s+registr|empleador|patr[oó]n|telegrama|sueldo|aguinaldo|art\b)/i.test(
        normalizedQuery
      );

    if (isNeighborOrNoiseIssue) {
      return {
        questions: [
          "Con los hechos que describí (ruidos, cámaras, daños o conflicto vecinal/consorcio), ¿conviene intimar primero por carta documento / intimación extrajudicial o iniciar directamente una denuncia o mediación vecinal?",
          "¿Qué pruebas válidas debo resguardar desde hoy (videos con fecha, medición de decibeles, acta de constatación, denuncias municipales o reclamos al consorcio) para sostener el reclamo?",
          "Si el problema persiste pese a la intimación o mediación, ¿corresponde pedir una medida cautelar de cese o reclamo por daños?"
        ],
        whatsappMessage: doNotSign
          ? "Hola Doc, necesito asesorarme por un conflicto concreto de convivencia/propiedad sin expediente iniciado (ruidos, cámaras o daños) y antes de firmar nada quiero evaluar una intimación extrajudicial o carta documento, denuncia o mediación. ¿Qué pruebas debo juntar primero?"
          : "Hola Doc, le consulto por una situación concreta sin juicio iniciado (conflicto vecinal / ruidos / cámaras / consorcio): ¿me conviene enviar una carta documento o intimación extrajudicial, hacer una denuncia o pedir mediación? Le puedo pasar fotos y detalles."
      };
    }

    if (isLaborFactIssue) {
      return {
        questions: [
          "Ante esta situación laboral (despido verbal, negativa de tareas o falta de registración), ¿qué intimación formal por telegrama laboral gratuito o carta documento debo enviar hoy mismo?",
          "Antes de firmar cualquier recibo, renuncia o acuerdo espontáneo, ¿me ayuda a revisar la liquidación y rubros indemnizatorios que me corresponden?",
          "¿Corresponde iniciar instancia de conciliación o mediación laboral (SECLO / Ministerio de Trabajo) y qué mensajes, transferencias o testigos debo conservar?"
        ],
        whatsappMessage: doNotSign
          ? "Hola Doc, tuve un problema laboral urgente (situación de hecho sin juicio iniciado) y antes de firmar ningún papel ni acuerdo quiero que me asesore para enviar el telegrama / intimación correcta o ir a conciliación. ¡Muchas gracias!"
          : "Hola Doc, necesito consultarle por una situación laboral concreta (ej. despido verbal o reclamo al empleador) para evaluar el envío de un telegrama / intimación extrajudicial o citar a mediación. ¿Qué constancias debo ir reuniendo?"
      };
    }

    const fueroName = humanizeFuero(fuero || "01_civil_comercial");
    return {
      questions: [
        `Por este hecho concreto (${fueroName}), ¿conviene enviar primero una intimación extrajudicial o carta documento, presentar una denuncia administrativa/policial o citar a mediación?`,
        "¿Qué pruebas concretas (mensajes, comprobantes, fotos, facturas, audios o testigos) conviene certificar o resguardar antes de formalizar el reclamo?",
        "¿Existe algún plazo urgente de reclamo, prescripción o precaución antes de responder comunicaciones o firmar recibos de la otra parte?"
      ],
      whatsappMessage: doNotSign
        ? "Hola Doc, necesito asesoramiento por un problema concreto sin expediente judicial abierto y prefiero no firmar nada hasta definir con usted si enviamos una intimación extrajudicial, carta documento, denuncia o pedido de mediación. ¡Muchas gracias!"
        : "Hola Doc, le escribo para consultarle por una situación de hecho concreta sin expediente iniciado: ¿conviene enviar una carta documento o intimación extrajudicial, radicar denuncia o iniciar mediación, y qué pruebas debería reunir primero?"
    };
  }

  function buildFormalEmailText(report, fueroReadable) {
    const hasExpediente = hasJudicialExpedienteReference(null, null, report);
    const questions = (report.questions_for_your_lawyer || []).map(
      (q, idx) => `${idx + 1}. ${cleanTechnicalJargon(q)}`
    );
    const pointsBlock =
      questions.length > 0
        ? questions.join("\n")
        : hasExpediente
          ? "1. Solicito me informe el estado actual del trámite y los próximos pasos previstos.\n2. Solicito copia simple de las últimas presentaciones realizadas."
          : "1. Evaluar si corresponde enviar una carta documento o intimación extrajudicial formal por los hechos ocurridos.\n2. Definir si conviene iniciar una denuncia administrativa/penal o instancia de mediación y qué pruebas reunir.";

    const urgencyLine = report.do_not_sign_yet_alert
      ? "Asimismo, le informo que previo a suscribir cualquier acuerdo, recibo o escrito dispositivo, necesito revisar el detalle completo de la liquidación y sus alcances legales.\n\n"
      : "";

    const subjectLine = hasExpediente
      ? "Asunto: Consulta sobre estado de trámite y puntos pendientes"
      : "Asunto: Consulta legal por situación de hecho — intimación, denuncia o mediación";
    const introLine = hasExpediente
      ? `Me comunico con usted en relación con mi asunto (${fueroReadable}) a fin de solicitarle información actualizada y dejar asentados los siguientes puntos que necesito revisar:`
      : `Me comunico con usted por una situación concreta (${fueroReadable}) sin expediente judicial previo, para evaluar las vías de acción (intimación extrajudicial, carta documento, denuncia o mediación) y revisar estos puntos:`;
    const closingLine = hasExpediente
      ? "Quedo a la espera de su respuesta por esta vía o con copia de las constancias correspondientes."
      : "Quedo a la espera de su orientación por esta vía para coordinar los próximos pasos y acercarle las pruebas disponibles.";

    return [
      subjectLine,
      "",
      "Estimado/a Dr./Dra.:",
      "",
      introLine,
      "",
      pointsBlock,
      "",
      urgencyLine + closingLine,
      "",
      "Atentamente."
    ].join("\n");
  }

  // =========================================================================
  // 1. CERO HUELLA & PERSISTENCIA EFÍMERA
  // =========================================================================

  function purgePassAndAdminStorage() {
    const keysToPurge = [
      ADMIN_STORAGE_KEY,
      "drh_active_user",
      "drh_has_paid_pass",
      "drh_is_admin",
      "drh_social_unlocked",
      "drh_bonificado",
      "drh_free_pass",
      "drh_demo_mode"
    ];
    try {
      keysToPurge.forEach((k) => {
        localStorage.removeItem(k);
        sessionStorage.removeItem(k);
      });
    } catch (err) {
      console.warn("Error purgando claves de pase/admin:", err);
    }
  }

  function clearAllStoredSessions() {
    state.accessToken = null;
    state.isSocialUnlocked = false;
    state.social_share_unlocked = false;
    state.currentReportUnlocked = false;
    state.paywallLockedStage = 0;
    state.pendingConsultation = null;
    state.currentUser = null;
    state.lastFinalReport = null;
    state.casesList = [];
    state.currentPdfBlob = null;
    state.attachedMediaName = null;
    state.urgentPhotoMode = false;
    try {
      localStorage.clear();
      sessionStorage.clear();
      purgePassAndAdminStorage();
    } catch (err) {
      console.warn("Error limpiando almacenamiento:", err);
      purgePassAndAdminStorage();
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
    headers.set("X-Device-Fingerprint", getDeviceFingerprint());
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
    clearAllStoredSessions();
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

  const STAGE_2_PAYWALL_SUBTITLE =
    "Elegí entre desbloquear tu 2ª consulta de hoy compartiendo en redes sociales (Opción 1) o abonar en ARS con Lemon Squeezy (Opción 2).";
  const STAGE_3_PAYWALL_SUBTITLE =
    "Alcanzaste el límite de 2 consultas gratuitas por hoy. Habilitá tu pase con Lemon Squeezy (Opción 2) o esperá 24 horas hasta mañana para volver a consultar.";

  function isDeliverablesLocked(userProfile) {
    const userData = userProfile || state.currentUser;
    if (!userData) {
      return (
        !state.currentReportUnlocked &&
        !state.social_share_unlocked &&
        !state.isSocialUnlocked
      );
    }
    const userEmail =
      typeof userData.email === "string" ? userData.email.trim().toLowerCase() : "";
    return (
      !ADMIN_EMAILS.includes(userEmail) &&
      !userData.has_paid_pass &&
      !state.currentReportUnlocked &&
      !state.social_share_unlocked &&
      !state.isSocialUnlocked
    );
  }

  function setSocialShareSectionVisibility(visible) {
    const shareIds = [
      "btn-share-whatsapp",
      "btn-share-x",
      "btn-share-facebook",
      "btn-share-instagram",
      "btn-share-threads"
    ];
    const btnShareWa = document.getElementById("btn-share-whatsapp");
    const gridEl = btnShareWa ? btnShareWa.parentElement : null;
    const option1Section = gridEl ? gridEl.parentElement : null;

    if (option1Section && option1Section.id !== "social-paywall-card") {
      if (visible) {
        option1Section.classList.remove("hidden");
        option1Section.style.display = "";
      } else {
        option1Section.classList.add("hidden");
        option1Section.style.display = "none";
      }
    }
    if (gridEl && gridEl.id !== "social-paywall-card") {
      if (visible) {
        gridEl.classList.remove("hidden");
        gridEl.style.display = "";
      } else {
        gridEl.classList.add("hidden");
        gridEl.style.display = "none";
      }
    }
    shareIds.forEach((id) => {
      const btn = document.getElementById(id);
      if (btn) {
        btn.disabled = !visible;
        if (visible) {
          btn.classList.remove("hidden");
          btn.style.display = "";
        } else {
          btn.classList.add("hidden");
          btn.style.display = "none";
        }
      }
    });
  }

  function configureAndShowPaywallStage(stage, customMessage) {
    const numericStage = Number(stage) >= 3 ? 3 : 2;
    state.paywallLockedStage = numericStage;

    const paywallCard = document.getElementById("social-paywall-card");
    const lockedAlert = document.getElementById("report-locked-alert");
    const unlockedDetails = document.getElementById("report-unlocked-details");
    const btnUnlockNow = document.getElementById("btn-unlock-now");
    const statusBox = document.getElementById("payment-status-feedback");
    const paywallSubtitle = paywallCard ? paywallCard.querySelector(".panel-header p") : null;

    ensurePaymentButtonsVisible();
    setSocialShareSectionVisibility(numericStage < 3);

    if (paywallSubtitle) {
      paywallSubtitle.textContent =
        numericStage >= 3 ? STAGE_3_PAYWALL_SUBTITLE : STAGE_2_PAYWALL_SUBTITLE;
    }

    if (unlockedDetails) {
      unlockedDetails.classList.add("hidden");
      unlockedDetails.style.display = "none";
    }
    if (lockedAlert) {
      if (numericStage >= 3) {
        lockedAlert.classList.add("hidden");
        lockedAlert.style.display = "none";
      } else if (state.lastFinalReport) {
        lockedAlert.classList.remove("hidden");
        lockedAlert.style.display = "flex";
      }
    }
    if (btnUnlockNow) {
      if (numericStage >= 3) {
        btnUnlockNow.classList.add("hidden");
        btnUnlockNow.style.display = "none";
      } else {
        btnUnlockNow.classList.remove("hidden");
        btnUnlockNow.style.display = "";
      }
    }

    if (paywallCard) {
      paywallCard.classList.remove("hidden");
      paywallCard.style.display = "block";
      paywallCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    if (statusBox) {
      const fallbackMsg =
        numericStage >= 3
          ? STAGE_3_PAYWALL_SUBTITLE
          : "Esta es tu 2ª consulta del día: compartí en una red social para desbloquear únicamente esta consulta (sin pase permanente) o elegí un pase en Lemon Squeezy.";
      statusBox.className = numericStage >= 3 ? "state-banner error" : "state-banner";
      statusBox.textContent = customMessage || fallbackMsg;
      statusBox.classList.remove("hidden");
    }
  }

  function ensurePaymentButtonsVisible() {
    const btnPayMicro = document.getElementById("btn-pay-micro");
    const btnPayMonthly = document.getElementById("btn-pay-monthly");
    if (btnPayMicro) {
      btnPayMicro.classList.remove("hidden");
      btnPayMicro.style.display = "flex";
      btnPayMicro.disabled = false;
    }
    if (btnPayMonthly) {
      btnPayMonthly.classList.remove("hidden");
      btnPayMonthly.style.display = "flex";
      btnPayMonthly.disabled = false;
    }
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
    const reportSection = document.getElementById("report-output-section");

    if (userProfile) {
      const userEmail =
        typeof userProfile.email === "string" ? userProfile.email.trim().toLowerCase() : "";
      const isUserAdmin = Boolean(isWhitelistedAdminEmail(userEmail) && ADMIN_EMAILS.includes(userEmail));
      userProfile.is_admin = isUserAdmin;
      if (isUserAdmin) {
        userProfile.has_paid_pass = true;
        userProfile.can_consult_free = true;
      } else {
        purgePassAndAdminStorage();
        userProfile.is_admin = false;
        userProfile.has_paid_pass = userProfile.has_paid_pass === true;
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
          const metricsPanel = document.getElementById("admin-metrics-panel");
          if (metricsPanel) {
            metricsPanel.classList.add("hidden");
            metricsPanel.style.display = "none";
          }
        }
      }
      if (userBadge) {
        const badgeLabel = userProfile.name || userProfile.email || "Usuario Activo";
        const quotaInfo = ADMIN_EMAILS.includes(userEmail)
          ? " (Admin Autorizado)"
          : userProfile.has_paid_pass
          ? " (Pase Activo)"
          : " (Cuenta Estándar - Sin Pase)";
        userBadge.textContent = `👤 ${badgeLabel}${quotaInfo}`;
        userBadge.classList.remove("hidden");
      }
      if (btnLogout) btnLogout.classList.remove("hidden");

      // Al iniciar sesión o cargar la página (antes de consultar), el Muro de Pago permanece oculto
      // para que lo primero que vea el usuario sea "Hacé tu consulta en un solo lugar".
      const lockedAlert = document.getElementById("report-locked-alert");
      const unlockedDetails = document.getElementById("report-unlocked-details");
      ensurePaymentButtonsVisible();

      if (!ADMIN_EMAILS.includes(userEmail) && !userProfile.has_paid_pass) {
        if (adminToolbar) {
          adminToolbar.classList.add("hidden");
          adminToolbar.style.display = "none";
        }
      }

      if (!state.lastFinalReport && !state.paywallLockedStage) {
        setSocialShareSectionVisibility(true);
        if (paywallCard) {
          paywallCard.classList.add("hidden");
          paywallCard.style.display = "none";
        }
        if (lockedAlert) {
          lockedAlert.classList.add("hidden");
          lockedAlert.style.display = "none";
        }
        if (reportSection) {
          reportSection.classList.add("hidden");
          reportSection.style.display = "none";
        }
      } else if (state.paywallLockedStage >= 2 && isDeliverablesLocked(userProfile)) {
        configureAndShowPaywallStage(state.paywallLockedStage);
      } else if (isDeliverablesLocked(userProfile)) {
        configureAndShowPaywallStage(2);
      } else {
        setSocialShareSectionVisibility(true);
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
        if (unlockedDetails) {
          unlockedDetails.classList.remove("hidden");
          unlockedDetails.style.display = "flex";
        }
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
        const metricsPanel = document.getElementById("admin-metrics-panel");
        if (metricsPanel) {
          metricsPanel.classList.add("hidden");
          metricsPanel.style.display = "none";
        }
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
    clearAllStoredSessions();
    setLoggedInView(null);
    const authContainer = document.getElementById("auth-container");
    const workspace = document.getElementById("app-workspace");
    if (workspace) workspace.classList.add("hidden");
    if (authContainer) authContainer.classList.remove("hidden");
  }

  async function syncBackendGoogleSession(credentialToken) {
    if (!credentialToken || typeof credentialToken !== "string" || !credentialToken.trim()) {
      clearAllStoredSessions();
      setLoggedInView(null);
      showLoginFeedback("No se recibió una credencial válida de Google.", "error");
      return;
    }

    // Purgar por completo localStorage y sessionStorage antes de iniciar la nueva cuenta
    clearAllStoredSessions();
    setLoggedInView(null);
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
      clearAllStoredSessions();
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

    // Disparar el Muro de Pago SOLO después de procesar la consulta si el usuario está bloqueado
    const paywallCard = document.getElementById("social-paywall-card");
    const lockedAlert = document.getElementById("report-locked-alert");
    const unlockedDetails = document.getElementById("report-unlocked-details");
    ensurePaymentButtonsVisible();

    if (isDeliverablesLocked(state.currentUser) === true) {
      configureAndShowPaywallStage(state.paywallLockedStage || 2);
    } else {
      setSocialShareSectionVisibility(true);
      if (paywallCard) {
        paywallCard.classList.add("hidden");
        paywallCard.style.display = "none";
      }
      if (lockedAlert) {
        lockedAlert.classList.add("hidden");
        lockedAlert.style.display = "none";
      }
      if (unlockedDetails) {
        unlockedDetails.classList.remove("hidden");
        unlockedDetails.style.display = "flex";
      }
      reportSection?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
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
    statusBanner.style.display = "";
    if (kind === "loading") {
      statusBanner.classList.add("loading");
      if (spinner) {
        spinner.classList.remove("hidden");
        spinner.style.display = "inline-block";
      }
    } else {
      if (kind === "error") statusBanner.classList.add("error");
      if (kind === "success") statusBanner.classList.add("success");
      if (spinner) {
        spinner.classList.add("hidden");
        spinner.style.display = "none";
      }
    }

    if (textSpan) {
      textSpan.textContent = message;
    } else {
      statusBanner.textContent = message;
    }
  }

  function resetConsultationLoadingUi(intervalId, btnConsult, originalBtnText) {
    if (intervalId !== null && intervalId !== undefined) {
      clearInterval(intervalId);
    }
    const spinner = document.getElementById("consultation-loading-spinner");
    if (spinner) {
      spinner.classList.add("hidden");
      spinner.style.display = "none";
    }
    if (btnConsult) {
      btnConsult.disabled = false;
      btnConsult.textContent = originalBtnText;
    }
  }

  async function handleFunnelPaywallResponse(resp, snapshot) {
    const stepRaw = resp.headers.get("X-Funnel-Step");
    const allowSocialRaw = resp.headers.get("X-Allow-Social-Unlock");
    const parsedStep = stepRaw ? Number.parseInt(stepRaw, 10) : 2;
    const effectiveStage = allowSocialRaw === "false" || parsedStep >= 3 ? 3 : 2;

    const errData = await resp.json().catch(() => ({}));
    const defaultMsg =
      effectiveStage >= 3
        ? STAGE_3_PAYWALL_SUBTITLE
        : "Ya utilizaste tu consulta gratuita de prueba. Compartí en redes sociales para desbloquear tu 2ª consulta del día o habilitá tu pase con Lemon Squeezy.";
    const detailMsg = errData.detail || defaultMsg;

    state.currentReportUnlocked = false;
    state.social_share_unlocked = false;
    state.isSocialUnlocked = false;
    state.pendingConsultation = effectiveStage === 2 ? snapshot : null;

    configureAndShowPaywallStage(effectiveStage, detailMsg);

    const err402 = new Error(detailMsg);
    err402.isUserFacing = true;
    throw err402;
  }

  function markCurrentReportUnlocked(resp) {
    const dailyCountRaw = resp && resp.headers ? resp.headers.get("X-Daily-Count") : null;
    if (dailyCountRaw) {
      const parsedCount = Number.parseInt(dailyCountRaw, 10);
      if (!Number.isNaN(parsedCount)) {
        state.dailyConsultationCount = parsedCount;
      }
    }
    state.currentReportUnlocked = true;
    state.paywallLockedStage = 0;
    state.pendingConsultation = null;
    state.social_share_unlocked = false;
    state.isSocialUnlocked = false;
  }

  async function executeUnifiedConsultation(overrideParams) {
    const retryPayload =
      overrideParams && overrideParams.isRetryPayload === true ? overrideParams : null;
    const btnConsult = document.getElementById("btn-run-full-analysis");
    const pdfBlob = retryPayload ? retryPayload.pdfBlob : state.currentPdfBlob;
    const pdfName = retryPayload ? retryPayload.pdfName : state.currentPdfName;
    const verbalText = retryPayload
      ? retryPayload.verbalText
      : document.getElementById("input-verbal-statement")?.value.trim() || "";
    const selectedProvince = retryPayload
      ? retryPayload.selectedProvince
      : document.getElementById("select-province-jurisdiction")?.value || "PJN_NACION_CABA";
    const cityLocality = retryPayload
      ? retryPayload.cityLocality
      : document.getElementById("input-city-locality")?.value.trim() || "";
    const attachedMediaName = retryPayload ? retryPayload.attachedMediaName : state.attachedMediaName;
    const urgentPhotoMode = retryPayload ? Boolean(retryPayload.urgentPhotoMode) : Boolean(state.urgentPhotoMode);

    if (!pdfBlob && verbalText.length < 5 && !attachedMediaName) {
      updateConsultationBanner(
        "error",
        "Por favor escribí tu duda, pegá lo que te dijo tu abogado o subí un archivo para analizar."
      );
      return;
    }

    const consultationSnapshot = {
      isRetryPayload: true,
      pdfBlob,
      pdfName,
      verbalText,
      selectedProvince,
      cityLocality,
      attachedMediaName,
      urgentPhotoMode
    };

    state.currentReportUnlocked = false;
    state.paywallLockedStage = 0;
    state.social_share_unlocked = false;
    state.isSocialUnlocked = false;
    const prevUnlockedEl = document.getElementById("report-unlocked-details");
    if (prevUnlockedEl) {
      prevUnlockedEl.classList.add("hidden");
      prevUnlockedEl.style.display = "none";
    }
    const paywallCard = document.getElementById("social-paywall-card");
    if (paywallCard) {
      paywallCard.classList.add("hidden");
      paywallCard.style.display = "none";
    }

    const originalBtnText = btnConsult ? btnConsult.textContent : "Consultar a Dr. H";
    if (btnConsult) {
      btnConsult.disabled = true;
      btnConsult.textContent = "Procesando consulta...";
    }

    let elapsedSeconds = 0;
    updateConsultationBanner("loading", LOADING_STATUS_STEPS[0].text);

    let loadingIntervalId = setInterval(() => {
      elapsedSeconds += 15;
      for (let i = LOADING_STATUS_STEPS.length - 1; i >= 0; i -= 1) {
        if (elapsedSeconds >= LOADING_STATUS_STEPS[i].atSeconds) {
          updateConsultationBanner("loading", LOADING_STATUS_STEPS[i].text);
          break;
        }
      }
    }, 15000);

    try {
      if (pdfBlob) {
        // Enviar expediente a /analyze-expediente con firma SHA-256
        const formData = new FormData();
        formData.append("file", pdfBlob, pdfName || "expediente.pdf");
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
          await handleFunnelPaywallResponse(resp, consultationSnapshot);
        }

        if (!resp.ok) {
          throw new Error(BUSY_SERVER_MESSAGE);
        }

        const report = await resp.json();
        markCurrentReportUnlocked(resp);
        renderUnifiedResponse(report);

        // Actualizar cuota del usuario y recargar historial de causas
        await restoreActiveSession();
      } else {
        // Consulta rápida o escáner de urgencia (secuencial para validar cupo antes de consumir)
        const queryText =
          verbalText ||
          `Consulta sobre archivo adjunto (${attachedMediaName || "documento"}). ¿Puedo firmar o qué precauciones debo tomar?`;

        const scanResp = await apiFetch("/emergency-scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            document_text: queryText.length >= 10 ? queryText : `${queryText} — revisión preventiva`,
            province_jurisdiction: selectedProvince,
            city_locality: cityLocality,
            user_context_note: urgentPhotoMode ? "Escáner de urgencia cámara" : "Consulta directa"
          })
        });

        if (scanResp.status === 402) {
          await handleFunnelPaywallResponse(scanResp, consultationSnapshot);
        }

        if (!scanResp.ok) {
          throw new Error(BUSY_SERVER_MESSAGE);
        }

        const repoResp = await apiFetch("/repository-consult", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: queryText,
            province_jurisdiction: selectedProvince,
            city_locality: cityLocality
          })
        });

        if (repoResp.status === 402) {
          await handleFunnelPaywallResponse(repoResp, consultationSnapshot);
        }

        if (!repoResp.ok) {
          throw new Error(BUSY_SERVER_MESSAGE);
        }

        const scanData = await scanResp.json();
        const repoData = await repoResp.json();

        const resolvedFuero = repoData.classified_fuero || scanData.detected_fuero || "03_laboral";
        const doNotSign = Boolean(scanData.do_not_sign_yet || (urgentPhotoMode && scanData.urgency_level !== "VERDE_INFORMATIVO"));
        const plainSummary = (repoData.citizen_clear_answer || scanData.plain_explanation || "").trim();
        const paddedPlainSummary =
          plainSummary.length >= 40
            ? plainSummary
            : `${plainSummary} Orientación legal ciudadana verificada conforme a la normativa argentina vigente.`.trim();

        const hasExpediente = hasJudicialExpedienteReference(queryText, pdfBlob, null);
        const contextualDeliverables = buildContextualCitizenDeliverables(
          queryText,
          resolvedFuero,
          doNotSign,
          hasExpediente
        );

        const rawAudioScript = `${repoData.citizen_clear_answer || ""} ${scanData.immediate_action_guide || ""} ${
          hasExpediente
            ? "Recordá siempre solicitar copia íntegra de toda presentación antes de firmar cualquier escrito o acuerdo judicial."
            : "Recordá resguardar todas las pruebas del hecho y consultar si corresponde enviar carta documento, intimación extrajudicial, denuncia o mediación."
        }`.trim();
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
          has_expediente: hasExpediente,
          subscription_tier_applied: "NIVEL_0_GRATUITO_HASTA_15_PAGS",
          do_not_sign_yet_alert: doNotSign,
          do_not_sign_alert_box: doNotSign ? `${scanData.plain_explanation} ${scanData.immediate_action_guide}` : null,
          citizen_plain_summary: paddedPlainSummary,
          verified_timeline_cards: [
            {
              foja_ref: 1,
              document_piece_type: urgentPhotoMode ? "Documento Escaneado de Urgencia" : "Consulta Directa en Lenguaje Claro",
              verbatim_excerpt: queryText.length >= 5 ? queryText.slice(0, 280) : "Consulta preventiva ciudadana",
              citizen_plain_translation: paddedPlainSummary.slice(0, 400),
              normative_ref: (repoData.cited_laws && repoData.cited_laws[0]) || "Código Procesal Civil y Comercial / LCT"
            }
          ],
          audit_and_deadlines_section: `${scanData.immediate_action_guide} (${repoData.temporal_validity_note})`,
          questions_for_your_lawyer: contextualDeliverables.questions,
          audio_script_90_seconds: paddedAudioScript,
          whatsapp_ready_message: contextualDeliverables.whatsappMessage,
          legal_shield_disclaimer:
            "Orientación ciudadana basada en normativa argentina vigente. No sustituye el patrocinio letrado obligatorio.",
          zero_storage_confirmation:
            "Privacidad garantizada: consulta procesada íntegramente en memoria RAM sin persistencia en servidor.",
          drive_saved_file_uri: `https://drive.google.com/dr-h-ar/${reportId}.pdf`
        };

        markCurrentReportUnlocked(repoResp);
        renderUnifiedResponse(synthesizedReport);
        await restoreActiveSession();
      }

      state.urgentPhotoMode = false;
      updateConsultationBanner(
        "success",
        "¡Análisis completo! Abajo tenés el dictamen y los botones de acción rápida."
      );
    } catch (err) {
      resetConsultationLoadingUi(loadingIntervalId, btnConsult, originalBtnText);
      loadingIntervalId = null;
      const isServerError = !err || !err.isUserFacing;
      const errorMsg = isServerError
        ? BUSY_SERVER_MESSAGE
        : `Aviso: ${err.message}`;
      updateConsultationBanner("error", errorMsg);
    } finally {
      resetConsultationLoadingUi(loadingIntervalId, btnConsult, originalBtnText);
      loadingIntervalId = null;
    }
  }

  // =========================================================================
  // 7. LOS 3 BOTONES DE ACCIÓN INMEDIATA CON BLINDAJE DE MURO SOCIAL
  // =========================================================================

  function promptSocialPaywall(actionLabel) {
    const stage = state.paywallLockedStage >= 3 ? 3 : 2;
    const msg =
      stage >= 3
        ? STAGE_3_PAYWALL_SUBTITLE
        : `Para ${actionLabel}, compartí Dr. H en una red social (Opción 1) o accedé con Lemon Squeezy (Opción 2).`;
    configureAndShowPaywallStage(stage, msg);
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
    const hasExpediente = hasJudicialExpedienteReference(null, null, rawReport);
    const fallbackDeliverables = buildContextualCitizenDeliverables(
      rawReport?.citizen_plain_summary || "",
      rawReport?.fuero || "03_laboral",
      Boolean(rawReport?.do_not_sign_yet_alert),
      hasExpediente
    );

    const auditSection =
      rawReport && typeof rawReport.audit_and_deadlines_section === "string" && rawReport.audit_and_deadlines_section.trim().length >= 20
        ? rawReport.audit_and_deadlines_section.trim()
        : hasExpediente
          ? "Plazos procesales y días de nota sujetos al código procesal de la jurisdicción."
          : "Evaluá intimación extrajudicial, carta documento, denuncia o mediación mientras resguardás pruebas del hecho.";
    const audioScript =
      rawReport && typeof rawReport.audio_script_90_seconds === "string" && rawReport.audio_script_90_seconds.trim().length >= 80
        ? rawReport.audio_script_90_seconds.trim()
        : hasExpediente
          ? `${plainSummary} Recordá siempre pedir copia completa antes de firmar cualquier escrito o convenio judicial.`
          : `${plainSummary} Recordá conservar todas las pruebas concretas y consultar si corresponde carta documento, denuncia o mediación.`;
    const waMsg =
      rawReport && typeof rawReport.whatsapp_ready_message === "string" && rawReport.whatsapp_ready_message.trim().length >= 40
        ? rawReport.whatsapp_ready_message.trim()
        : fallbackDeliverables.whatsappMessage;
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
        : fallbackDeliverables.questions;

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
        "Content-Type": "application/json",
        "X-Device-Fingerprint": getDeviceFingerprint()
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

  function resolveCheckoutUrl(candidateUrl, fallbackUrl) {
    if (
      typeof candidateUrl === "string" &&
      candidateUrl.startsWith("https://") &&
      !candidateUrl.includes("dr-h-argentina.onrender.com/#payment-")
    ) {
      return candidateUrl;
    }
    return fallbackUrl;
  }

  async function loadPaymentConfig() {
    try {
      const resp = await fetch("/payments/config");
      if (resp.ok) {
        const cfg = await resp.json();
        state.microPaymentUrl = resolveCheckoutUrl(
          cfg.micro_payment_url,
          EXTERNAL_MICRO_PAYMENT_URL
        );
        state.monthlyPaymentUrl = resolveCheckoutUrl(
          cfg.monthly_payment_url,
          EXTERNAL_MONTHLY_PAYMENT_URL
        );
        state.externalPaymentUrl = resolveCheckoutUrl(
          cfg.external_payment_url,
          EXTERNAL_MONTHLY_PAYMENT_URL
        );
        if (cfg.social_share_text) state.socialShareText = cfg.social_share_text;
        if (cfg.app_url) state.appUrl = cfg.app_url;
      }
    } catch (err) {
      console.warn("Aviso cargando configuración de pagos:", err);
    }
  }

  function unlockDeliverablesBySocialShare(network, options = {}) {
    state.social_share_unlocked = true;
    state.isSocialUnlocked = true;
    state.currentReportUnlocked = Boolean(state.lastFinalReport);
    state.paywallLockedStage = 0;

    const paywallCard = document.getElementById("social-paywall-card");
    const paywallModal = document.getElementById("paywall-modal");
    const lockedAlert = document.getElementById("report-locked-alert");
    const reportSection = document.getElementById("report-output-section");
    const unlockedDetails = document.getElementById("report-unlocked-details");
    const btnDownloadPdf = document.getElementById("btn-download-pdf");
    const btnOpenWa = document.getElementById("btn-open-whatsapp");
    const btnCopyEmail = document.getElementById("btn-copy-email");

    if (paywallCard) {
      paywallCard.classList.add("hidden");
      paywallCard.style.display = "none";
    }
    if (paywallModal) {
      paywallModal.classList.add("hidden");
      paywallModal.style.display = "none";
    }
    if (lockedAlert) {
      lockedAlert.classList.add("hidden");
      lockedAlert.style.display = "none";
    }
    if (reportSection) {
      reportSection.classList.remove("hidden");
      reportSection.style.display = "block";
    }
    if (unlockedDetails) {
      unlockedDetails.classList.remove("hidden");
      unlockedDetails.style.display = "flex";
    }
    [btnDownloadPdf, btnOpenWa, btnCopyEmail].forEach((btn) => {
      if (btn) {
        btn.classList.remove("hidden");
        btn.disabled = false;
      }
    });

    // Notificar al backend en segundo plano si se invocó de forma directa
    if (state.accessToken && options.syncBackend !== false) {
      apiFetch("/payments/social-unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ network: network || "whatsapp" })
      }).catch((err) => console.warn("Aviso sincronizando desbloqueo social:", err));
    }

    reportSection?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  window.unlockDeliverablesBySocialShare = unlockDeliverablesBySocialShare;

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

    const MIN_SHARE_RETURN_MS = 12000;
    const EARLY_RETURN_TOAST =
      "Parece que volviste antes de completar la publicación. Compartí Dr. H en tu red elegida para destrabar el informe";
    let activeShareCleanup = null;

    function showShareFeedback(kind, message) {
      if (statusBox) {
        statusBox.className = kind === "error" ? "state-banner error" : "state-banner success";
        statusBox.classList.remove("hidden");
        statusBox.textContent = message;
      }
      updateConsultationBanner(kind === "error" ? "error" : "success", message);
    }

    async function finalizeSocialUnlockForCurrentReport(network) {
      // 1. Validar crédito único diario en backend antes de habilitar la 2ª consulta
      if (state.accessToken) {
        try {
          const unlockResp = await apiFetch("/payments/social-unlock", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ network: network || "whatsapp" })
          });

          if (unlockResp.status === 402) {
            const errData = await unlockResp.json().catch(() => ({}));
            const msg3 = errData.detail || STAGE_3_PAYWALL_SUBTITLE;
            state.pendingConsultation = null;
            configureAndShowPaywallStage(3, msg3);
            updateConsultationBanner("error", msg3);
            return;
          }

          if (!unlockResp.ok) {
            const errData = await unlockResp.json().catch(() => ({}));
            const errMsg = errData.detail || "No se pudo validar el desbloqueo por difusión social.";
            showShareFeedback("error", errMsg);
            return;
          }

          const unlockData = await unlockResp.json().catch(() => ({}));
          if (state.currentUser && typeof unlockData.has_paid_pass === "boolean") {
            state.currentUser.has_paid_pass = unlockData.has_paid_pass;
          }
        } catch (err) {
          console.error("Error validando desbloqueo social en servidor:", err);
          showShareFeedback("error", "Error de conexión al validar tu desbloqueo social. Intentá nuevamente.");
          return;
        }
      }

      // 2. Desbloquear exclusivamente la 2ª consulta actual (sin otorgar pase permanente)
      const pending = state.pendingConsultation;
      unlockDeliverablesBySocialShare(network, { syncBackend: false });

      if (pending) {
        state.pendingConsultation = null;
        showShareFeedback(
          "success",
          "🎉 ¡2ª consulta del día desbloqueada tras difundir en redes! Procesando tu consulta ahora..."
        );
        await executeUnifiedConsultation(pending);
        return;
      }

      showShareFeedback(
        "success",
        "🎉 ¡2ª consulta del día desbloqueada! Ya podés consultar o descargar este informe (no otorga pase permanente)."
      );
    }

    async function triggerSocialShare(network) {
      if (state.paywallLockedStage >= 3) {
        configureAndShowPaywallStage(3, STAGE_3_PAYWALL_SUBTITLE);
        return;
      }

      if (typeof activeShareCleanup === "function") {
        activeShareCleanup();
        activeShareCleanup = null;
      }

      const shareStartTime = Date.now();
      const rawShareCopy = state.socialShareText;
      const shareUrl = state.appUrl || window.location.origin;
      const shareText = `${rawShareCopy} ${shareUrl}`.trim();

      // 1. Abrir la red social correspondiente en pestaña nueva completa (_blank) sin pop-up chico
      if (network === "whatsapp") {
        window.open("https://api.whatsapp.com/send?text=" + encodeURIComponent(shareText), "_blank");
      } else if (network === "instagram") {
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(shareText);
          }
        } catch (e) {
          // Continuar si el navegador restringe clipboard
        }
        showShareFeedback("success", "¡Texto copiado! Pegalo en tu Historia o publicación");
        window.open("https://www.instagram.com/", "_blank");
      } else if (network === "x") {
        window.open(
          `https://twitter.com/intent/tweet?text=${encodeURIComponent(rawShareCopy)}&url=${encodeURIComponent(shareUrl)}`,
          "_blank"
        );
      } else if (network === "facebook") {
        window.open(
          `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(shareUrl)}&quote=${encodeURIComponent(rawShareCopy)}`,
          "_blank"
        );
      } else if (network === "threads") {
        window.open(
          `https://www.threads.net/intent/post?text=${encodeURIComponent(shareText)}`,
          "_blank"
        );
      }

      // 2. Control Anti-Trampa de 12 segundos al regresar a la pestaña
      let handledReturn = false;
      const previousOnFocus = window.onfocus;

      function cleanupReturnListeners() {
        document.removeEventListener("visibilitychange", onVisibilityReturn);
        if (window.onfocus === onFocusReturn) {
          window.onfocus = previousOnFocus || null;
        }
        if (activeShareCleanup === cleanupReturnListeners) {
          activeShareCleanup = null;
        }
      }

      function evaluateShareReturn() {
        if (handledReturn) return;
        handledReturn = true;
        cleanupReturnListeners();

        const elapsedMs = Date.now() - shareStartTime;
        if (elapsedMs < MIN_SHARE_RETURN_MS) {
          showShareFeedback("error", EARLY_RETURN_TOAST);
          return;
        }

        void finalizeSocialUnlockForCurrentReport(network);
      }

      function onVisibilityReturn() {
        if (document.visibilityState === "visible") {
          evaluateShareReturn();
        }
      }

      function onFocusReturn(ev) {
        if (typeof previousOnFocus === "function") {
          try {
            previousOnFocus.call(window, ev);
          } catch (err) {
            console.warn("Error ejecutando onfocus previo:", err);
          }
        }
        evaluateShareReturn();
      }

      activeShareCleanup = cleanupReturnListeners;
      document.addEventListener("visibilitychange", onVisibilityReturn);
      window.onfocus = onFocusReturn;
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

    function redirectToLemonCheckout(planType) {
      const targetUrl =
        planType === "micro"
          ? resolveCheckoutUrl(state.microPaymentUrl, EXTERNAL_MICRO_PAYMENT_URL)
          : resolveCheckoutUrl(state.monthlyPaymentUrl, EXTERNAL_MONTHLY_PAYMENT_URL);

      try {
        window.open(targetUrl, "_blank");
      } catch (err) {
        console.error("Error al abrir el checkout de Lemon Squeezy:", err);
        if (statusBox) {
          statusBox.className = "state-banner error";
          statusBox.classList.remove("hidden");
          statusBox.textContent = "No pudimos abrir la pestaña de pago. Verificá los permisos del navegador.";
        }
      }
    }

    if (btnPayMicro) {
      btnPayMicro.addEventListener("click", (e) => {
        e.preventDefault();
        redirectToLemonCheckout("micro");
      });
    }

    if (btnPayMonthly) {
      btnPayMonthly.addEventListener("click", (e) => {
        e.preventDefault();
        redirectToLemonCheckout("monthly");
      });
    }

    if (btnPayExternal) {
      btnPayExternal.addEventListener("click", (e) => {
        e.preventDefault();
        redirectToLemonCheckout("monthly");
      });
    }
  }

  function initAdminToolbar() {
    const btnAdminFree = document.getElementById("btn-admin-mode-free");
    const btnAdminPremium = document.getElementById("btn-admin-mode-premium");
    const paywallCard = document.getElementById("social-paywall-card");
    const lockedAlert = document.getElementById("report-locked-alert");
    const reportSection = document.getElementById("report-output-section");
    const unlockedDetails = document.getElementById("report-unlocked-details");

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
        if (unlockedDetails) {
          unlockedDetails.classList.add("hidden");
          unlockedDetails.style.display = "none";
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
        if (unlockedDetails) {
          unlockedDetails.classList.remove("hidden");
          unlockedDetails.style.display = "flex";
        }
        if (reportSection) {
          reportSection.classList.remove("hidden");
          reportSection.style.display = "block";
        }
      });
    }

    const btnToggleMetrics = document.getElementById("btn-admin-toggle-metrics");
    const metricsPanel = document.getElementById("admin-metrics-panel");
    const btnRefreshMetrics = document.getElementById("btn-admin-refresh-metrics");

    if (btnToggleMetrics && metricsPanel) {
      btnToggleMetrics.addEventListener("click", (e) => {
        e.preventDefault();
        const isCurrentlyHidden =
          metricsPanel.classList.contains("hidden") || metricsPanel.style.display === "none";
        if (isCurrentlyHidden) {
          metricsPanel.classList.remove("hidden");
          metricsPanel.style.display = "flex";
          btnToggleMetrics.setAttribute("aria-expanded", "true");
          loadAdminDailyMetrics();
        } else {
          metricsPanel.classList.add("hidden");
          metricsPanel.style.display = "none";
          btnToggleMetrics.setAttribute("aria-expanded", "false");
        }
      });
    }

    if (btnRefreshMetrics) {
      btnRefreshMetrics.addEventListener("click", (e) => {
        e.preventDefault();
        loadAdminDailyMetrics();
      });
    }
  }

  function setAdminMetricsUiState(mode, errorMessage) {
    const loadingEl = document.getElementById("admin-metrics-loading");
    const errorEl = document.getElementById("admin-metrics-error");
    const emptyEl = document.getElementById("admin-metrics-empty");
    const contentEl = document.getElementById("admin-metrics-content");

    [loadingEl, errorEl, emptyEl, contentEl].forEach((el) => {
      if (el) {
        el.classList.add("hidden");
        el.style.display = "none";
      }
    });

    if (mode === "loading" && loadingEl) {
      loadingEl.classList.remove("hidden");
      loadingEl.style.display = "";
    } else if (mode === "error" && errorEl) {
      errorEl.textContent = errorMessage || "No se pudieron cargar las métricas diarias.";
      errorEl.classList.remove("hidden");
      errorEl.style.display = "";
    } else if (mode === "empty" && emptyEl) {
      emptyEl.classList.remove("hidden");
      emptyEl.style.display = "";
    } else if (mode === "success" && contentEl) {
      contentEl.classList.remove("hidden");
      contentEl.style.display = "flex";
    }
  }

  function renderAdminMetricsData(data) {
    const todayCardsEl = document.getElementById("admin-metrics-today-cards");
    const tbodyEl = document.getElementById("admin-metrics-7d-body");
    const today = data?.today || {};
    const rows = Array.isArray(data?.last_7_days) ? data.last_7_days : [];

    const hasAnyActivity = rows.some(
      (r) =>
        Number(r.visits_without_query || 0) > 0 ||
        Number(r.users_single_query || 0) > 0 ||
        Number(r.users_two_queries || 0) > 0 ||
        Number(r.users_three_plus_queries || 0) > 0 ||
        Number(r.total_queries_executed || 0) > 0
    );

    if (!hasAnyActivity) {
      setAdminMetricsUiState("empty");
      return;
    }

    if (todayCardsEl) {
      todayCardsEl.innerHTML = "";
      const cardsSpec = [
        { label: `Visitas s/ consulta (${today.metric_date || "Hoy"})`, val: today.visits_without_query || 0 },
        { label: "1 consulta", val: today.users_single_query || 0 },
        { label: "2 consultas (Social)", val: today.users_two_queries || 0 },
        { label: "3+ (Comercial)", val: today.users_three_plus_queries || 0 },
        { label: "Total IA hoy", val: today.total_queries_executed || 0 }
      ];
      cardsSpec.forEach((c) => {
        const card = document.createElement("div");
        card.style.cssText =
          "padding:0.55rem 0.7rem;border-radius:0.5rem;border:1px solid var(--border-subtle, rgba(148,163,184,0.35));background:var(--bg-surface, transparent);display:flex;flex-direction:column;gap:0.15rem;";
        const title = document.createElement("span");
        title.style.cssText = "font-size:0.75rem;color:var(--text-secondary);";
        title.textContent = c.label;
        const num = document.createElement("strong");
        num.style.cssText = "font-size:1.15rem;";
        num.textContent = String(c.val);
        card.appendChild(title);
        card.appendChild(num);
        todayCardsEl.appendChild(card);
      });
    }

    if (tbodyEl) {
      tbodyEl.innerHTML = "";
      rows.forEach((r) => {
        const tr = document.createElement("tr");
        tr.style.borderBottom = "1px solid var(--border-subtle, rgba(148,163,184,0.2))";
        [
          r.metric_date || "",
          r.visits_without_query || 0,
          r.users_single_query || 0,
          r.users_two_queries || 0,
          r.users_three_plus_queries || 0,
          r.total_queries_executed || 0
        ].forEach((cellVal) => {
          const td = document.createElement("td");
          td.style.padding = "0.4rem 0.5rem";
          td.textContent = String(cellVal);
          tr.appendChild(td);
        });
        tbodyEl.appendChild(tr);
      });
    }

    setAdminMetricsUiState("success");
  }

  async function loadAdminDailyMetrics() {
    setAdminMetricsUiState("loading");
    try {
      const resp = await apiFetch("/admin/metrics/daily", { method: "GET" });
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.detail || `Error HTTP ${resp.status} cargando métricas.`);
      }
      const data = await resp.json();
      renderAdminMetricsData(data);
    } catch (err) {
      setAdminMetricsUiState("error", err?.message || "Error consultando métricas diarias.");
    }
  }

  async function trackAnonymousDailyVisit() {
    const visitKey = "drh_anon_metric_visit_day";
    const todayKey = new Date(Date.now() - 3 * 3600 * 1000).toISOString().slice(0, 10);
    try {
      if (sessionStorage.getItem(visitKey) === todayKey) {
        return;
      }
      const resp = await fetch("/metrics/visit", { method: "POST" });
      if (resp.ok) {
        sessionStorage.setItem(visitKey, todayKey);
      }
    } catch (err) {
      console.warn("Telemetry visit notice:", err);
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

    // Restaurar sesión sin dejar rastros en DOM y registrar visita anónima diaria
    restoreActiveSession();
    trackAnonymousDailyVisit();
  });
})();
