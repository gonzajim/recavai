document.addEventListener('DOMContentLoaded', function () {
  // ===================== 1) FIREBASE =====================
  const firebaseConfig = {
    apiKey: "AIzaSyAAlSxno1oBOtyhh7ntS2mv8rkAnWeAzmM",
    authDomain: "recava-auditor-dev.firebaseapp.com",
    projectId: "recava-auditor-dev",
    // FIX: bucket estándar de Firebase Storage
    storageBucket: "recava-auditor-dev.appspot.com",
    messagingSenderId: "370417116045",
    appId: "1:370417116045:web:41c77969d5d880382d93c4",
    measurementId: "G-2J8TTR4SD2"
  };
  let auth;
  try {
    firebase.initializeApp(firebaseConfig);
    auth = firebase.auth();

    if (location.hostname === 'localhost') {
      firebase.auth().useEmulator('http://localhost:9099/');
    }
  } catch (initErr) {
    // Si Firebase no carga (bloqueador de anuncios/privacidad, red corporativa,
    // extensión del navegador, etc.) los botones de login/registro quedaban
    // "muertos" sin ningún aviso. Mostramos un mensaje visible en vez de fallar en silencio.
    console.error('No se pudo inicializar Firebase Auth:', initErr);
    const loginBox = document.querySelector('#login-view .login-box, #login-container .login-box');
    if (loginBox) {
      loginBox.innerHTML =
        '<p class="error-message" style="display:block;">' +
        'No se pudo cargar el servicio de acceso. Puede deberse a un bloqueador de anuncios/privacidad ' +
        'o a un problema de red — desactívalo para este sitio y recarga la página. ' +
        'Si el problema persiste, contacta con el administrador.' +
        '</p>';
    }
    return; // sin `auth` nada del resto del script puede funcionar
  }

  // Endpoints por entorno
  const endpoints = {
    prod: {
      auditor: 'https://orchestrator-520199812528.europe-west1.run.app/chat_auditor',
      advisor: 'https://orchestrator-520199812528.europe-west1.run.app/chat_assistant'
    },
    dev: {
      auditor: 'https://orchestrator-dev-370417116045.europe-west1.run.app/chat_auditor',
      advisor: 'https://orchestrator-dev-370417116045.europe-west1.run.app/chat_assistant'
    },
    local: {
      auditor: 'http://localhost:8080/chat_auditor',
      advisor: 'http://localhost:8080/chat_assistant'
    }
  };
  let currentEndpoints = endpoints.prod;

  async function configureEnvironment() {
    if (location.hostname === 'localhost') {
      currentEndpoints = endpoints.local; return;
    }
    try {
      const initResp = await fetch('/__/firebase/init.json');
      if (!initResp.ok) throw new Error(`init.json ${initResp.status}`);
      const cfg = await initResp.json().catch(() => null);
      currentEndpoints = (cfg?.projectId === 'recava-auditor') ? endpoints.prod : endpoints.dev;
    } catch (_e) {
      currentEndpoints = endpoints.dev;
    }
  }
  const environmentReadyPromise = configureEnvironment();

  function getOrchestratorBaseUrl() {
    const ref = currentEndpoints?.auditor || currentEndpoints?.advisor;
    if (!ref) return "";
    if (ref.includes("/chat_auditor")) return ref.split("/chat_auditor")[0];
    if (ref.includes("/chat_assistant")) return ref.split("/chat_assistant")[0];
    return ref.replace(/\/$/, "");
  }

  // ===================== 1B) UTILS RED/RESPUESTA =====================
  async function parseApiResponse(resp) {
    const payload = await resp.json().catch(() => ({}));
    if (payload && typeof payload === 'object') {
      if (payload.ok === true && payload.data !== undefined) return payload.data;
      if (payload.ok === false) {
        const msg = payload?.error?.message || payload?.error || `HTTP ${resp.status}`;
        throw new Error(msg);
      }
    }
    return payload; // retrocompatibilidad con APIs que devuelven objeto directo
  }
  function withTimeout(ms = 90000) {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), ms);
    return { signal: controller.signal, cancel: () => clearTimeout(t) };
  }
  function idempotencyKey() {
    return (crypto?.randomUUID && crypto.randomUUID()) || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  // ===================== 2) SELECTORES =====================
  const loginViewEl = document.getElementById('login-view') || document.getElementById('login-container');
  const chatWrapperEl = document.querySelector('.chat-wrapper');
  const emailInputEl = document.getElementById('email-input');
  const passwordInputEl = document.getElementById('password-input');
  const loginButtonEl = document.getElementById('login-button');
  const registerButtonEl = document.getElementById('register-button');
  const loginErrorEl = document.getElementById('login-error');

  // Inject "Olvidé mi contraseña" link below the buttons in the login box
  (() => {
    const loginBox = document.querySelector('#login-view .login-box, #login-container .login-box');
    const errEl = loginBox?.querySelector('.error-message, #login-error');
    if (loginBox && errEl) {
      const link = document.createElement('button');
      link.className = 'forgot-password-link';
      link.type = 'button';
      link.textContent = '¿Olvidaste tu contraseña?';
      link.addEventListener('click', async () => {
        const email = emailInputEl?.value?.trim();
        if (!email) {
          loginErrorEl.textContent = 'Introduce tu email arriba para recibir el enlace.';
          loginErrorEl.classList.remove('is-success');
          loginErrorEl.style.display = 'block';
          return;
        }
        try {
          await auth.sendPasswordResetEmail(email);
          loginErrorEl.textContent = 'Te hemos enviado un enlace para restablecer tu contraseña.';
          loginErrorEl.classList.add('is-success');
          loginErrorEl.style.display = 'block';
        } catch (err) {
          loginErrorEl.textContent = _fbMsg(err);
          loginErrorEl.classList.remove('is-success');
          loginErrorEl.style.display = 'block';
        }
      });
      loginBox.insertBefore(link, errEl);
    }
  })();

  const chatBubbleEl = document.getElementById('chat-bubble');
  const chatWidgetContainerEl = document.getElementById('chat-widget-container');
  const chatCloseButtonEl = document.getElementById('chat-close-button');
  const chatHomeButtonEl = document.getElementById('chat-home-button');

  const chatMessagesEl = document.getElementById('chat-messages');
  const inputAreaWrapperEl = document.getElementById('input-area-wrapper');
  const userInputEl = document.getElementById('user-input');
  const sendButtonEl = document.getElementById('send-button');
  const attachFileButtonEl = document.getElementById('attach-file-button');

  // Accesibilidad log
  if (chatMessagesEl) { chatMessagesEl.setAttribute('aria-live','polite'); chatMessagesEl.setAttribute('role','log'); }

  // Scroll-to-bottom button
  let scrollToBottomBtn = null;
  if (chatWrapperEl) {
    scrollToBottomBtn = document.createElement('button');
    scrollToBottomBtn.id = 'scroll-to-bottom-btn';
    scrollToBottomBtn.title = 'Ir al final';
    scrollToBottomBtn.innerHTML = '&#8595;';
    scrollToBottomBtn.setAttribute('aria-label', 'Ir al final del chat');
    chatWrapperEl.appendChild(scrollToBottomBtn);
    scrollToBottomBtn.addEventListener('click', () => scrollChatToBottom({ behavior: 'smooth' }));
  }
  function _updateScrollBtn() {
    if (!chatMessagesEl || !scrollToBottomBtn) return;
    const distFromBottom = chatMessagesEl.scrollHeight - chatMessagesEl.scrollTop - chatMessagesEl.clientHeight;
    scrollToBottomBtn.classList.toggle('visible', distFromBottom > 120);
  }
  chatMessagesEl?.addEventListener('scroll', debounce(_updateScrollBtn, 80));

  const AUDIT_BLOCKS_DEFINITION = [
    { id: 'block_1', label: '1. Contexto y Alcance' },
    { id: 'block_2', label: '2. Información Corporativa' },
    { id: 'block_3', label: '3. Cadena de Valor' },
    { id: 'block_4', label: '4. Gobernanza y Compliance' },
    { id: 'block_5', label: '5. Impacto Ambiental' },
    { id: 'block_6', label: '6. Personas y Derechos Humanos' },
    { id: 'block_7', label: '7. Riesgos y Controles' },
    { id: 'block_8', label: '8. Conclusiones y Roadmap' },
  ];

  let currentUser = null;
  let currentChatMode = null;
  let currentChatThreadId = null;
  let currentConversationMessages = [];
  let recentConversationsCache = [];
  const conversationThreadCache = new Map();
  let historySectionState = null;
  let isRestoringHistoryPlayback = false;
  let auditorProgressPanelEl = null;
  let auditorProgressTitleEl = null;
  let auditorProgressPercentEl = null;
  let auditorProgressBarFillEl = null;
  let auditorProgressSummaryEl = null;
  let auditorProgressSummaryTitleEl = null;
  let auditorProgressSummaryTextEl = null;
  let auditorProgressListEl = null;
  let auditorProgressEmptyEl = null;
  let auditProgressState = null;
  let isFetchingAuditProgress = false;
  let auditRightPanelEl = null;
  let userFiles = [];          // server-sourced; shared across advisor + auditor
  let _auditDomReady = false;

  if (chatWrapperEl && chatMessagesEl) {
    auditorProgressPanelEl = document.createElement('section');
    auditorProgressPanelEl.className = 'auditor-progress-panel hidden';
    auditorProgressPanelEl.innerHTML = `
      <header class="auditor-progress__header">
        <h3 class="auditor-progress__title">Progreso de auditoria</h3>
        <span class="auditor-progress__percent">0%</span>
      </header>
      <div class="auditor-progress__bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
        <div class="auditor-progress__bar-fill" style="width:0%;"></div>
      </div>
      <div class="auditor-progress__summary hidden">
        <h4 class="auditor-progress__summary-title"></h4>
        <p class="auditor-progress__summary-text"></p>
      </div>
      <ul class="auditor-progress__list"></ul>
      <p class="auditor-progress__empty">Selecciona el modo auditor para comenzar el flujo guiado.</p>
    `;
    chatWrapperEl.insertBefore(auditorProgressPanelEl, chatMessagesEl);
    auditorProgressTitleEl = auditorProgressPanelEl.querySelector('.auditor-progress__title');
    auditorProgressPercentEl = auditorProgressPanelEl.querySelector('.auditor-progress__percent');
    auditorProgressBarFillEl = auditorProgressPanelEl.querySelector('.auditor-progress__bar-fill');
    auditorProgressSummaryEl = auditorProgressPanelEl.querySelector('.auditor-progress__summary');
    auditorProgressSummaryTitleEl = auditorProgressPanelEl.querySelector('.auditor-progress__summary-title');
    auditorProgressSummaryTextEl = auditorProgressPanelEl.querySelector('.auditor-progress__summary-text');
    auditorProgressListEl = auditorProgressPanelEl.querySelector('.auditor-progress__list');
    auditorProgressEmptyEl = auditorProgressPanelEl.querySelector('.auditor-progress__empty');
    auditorProgressPanelEl.addEventListener('click', handleAuditProgressPanelClick);
    setAuditProgressState(buildDefaultAuditProgressState());
  }

  // ===================== 3) HELPERS VERIFICACIÓN =====================
  // Firebase Auth error code → Spanish message
  const _fbErrors = {
    'auth/email-already-in-use':   'Ese correo ya tiene una cuenta. Inicia sesión o recupera tu contraseña.',
    'auth/invalid-email':          'El formato del correo no es válido.',
    'auth/weak-password':          'La contraseña debe tener al menos 6 caracteres.',
    'auth/user-not-found':         'No existe ninguna cuenta con ese correo.',
    'auth/wrong-password':         'Contraseña incorrecta.',
    'auth/invalid-credential':     'Correo o contraseña incorrectos.',
    'auth/user-disabled':          'Esta cuenta ha sido deshabilitada. Contacta con el administrador.',
    'auth/too-many-requests':      'Demasiados intentos fallidos. Espera unos minutos e inténtalo de nuevo.',
    'auth/network-request-failed': 'Error de red. Comprueba tu conexión e inténtalo de nuevo.',
    'auth/operation-not-allowed':  'El registro no está habilitado. Contacta con el administrador.',
    'auth/requires-recent-login':  'Por seguridad, cierra sesión, vuelve a entrar y repite la operación.',
  };
  function _fbMsg(err) {
    return _fbErrors[err?.code] || err?.message || 'Error inesperado. Inténtalo de nuevo.';
  }

  async function sendVerificationIfNeeded(user) {
    try { if (user && !user.emailVerified) await user.sendEmailVerification(); }
    catch(e){ console.error('No se pudo enviar verificación:', e); }
  }
  async function reloadAndCheckVerification() {
    if (!auth.currentUser) return false;
    await auth.currentUser.reload(); return !!auth.currentUser.emailVerified;
  }
  async function getVerifiedIdTokenOrThrow() {
    const u = auth.currentUser;
    if (!u) throw new Error('No autenticado');
    if (!u.emailVerified) throw new Error('Email no verificado');
    return await u.getIdToken(true);
  }

  // ===================== 4) BANNER VERIFICACIÓN =====================
  const verifyBanner = document.createElement('div');
  verifyBanner.classList.add('verify-banner');
  verifyBanner.style.display = 'none';
  verifyBanner.innerHTML = `
    <strong class="verify-banner__title">Revisa tu correo</strong>
    <span class="verify-banner__subtitle">Te hemos enviado un email para verificar tu cuenta.</span>
    <div class="verify-banner__actions">
      <button id="btn-verify-retry" class="verify-banner__button primary">Ya lo verifiqué</button>
      <button id="btn-verify-resend" class="verify-banner__button secondary">Reenviar verificación</button>
      <button id="btn-verify-logout" class="verify-banner__button secondary">Cerrar sesión</button>
      <button id="btn-reset-password" class="verify-banner__button ghost">Olvidé mi contraseña</button>
    </div>`;
  if (loginViewEl) loginViewEl.appendChild(verifyBanner);

  verifyBanner.addEventListener('click', async (e) => {
    const id = e.target?.id;
    try {
      if (id === 'btn-verify-retry') {
        const ok = await reloadAndCheckVerification();
        if (ok) {
          loginErrorEl.style.display = 'none';
          verifyBanner.style.display = 'none';
          loginViewEl.style.display = 'none';
          document.querySelector('.chat-wrapper').style.display = 'flex';
          chatMessagesEl.style.display = 'none';
          inputAreaWrapperEl.style.display = 'block';
          await initializeSelectionLayout();
        } else {
          loginErrorEl.textContent = "Tu email sigue sin estar verificado. Revisa el buzón o reenvía el correo.";
          loginErrorEl.style.display = 'block';
        }
      } else if (id === 'btn-verify-resend') {
        await sendVerificationIfNeeded(auth.currentUser);
        loginErrorEl.textContent = "Hemos reenviado el email de verificación.";
        loginErrorEl.style.display = 'block';
      } else if (id === 'btn-verify-logout') {
        await auth.signOut();
      } else if (id === 'btn-reset-password') {
        const email = emailInputEl?.value || auth.currentUser?.email;
        if (!email) throw new Error("Introduce tu email en el formulario");
        await auth.sendPasswordResetEmail(email);
        loginErrorEl.textContent = "Te hemos enviado un enlace para restablecer tu contraseña.";
        loginErrorEl.style.display = 'block';
      }
    } catch (err) {
      loginErrorEl.textContent = _fbMsg(err);
      loginErrorEl.style.display = 'block';
    }
  });

  // ===================== 5) AUTH STATE =====================
  auth.onAuthStateChanged(async (user) => {
    if (user) {
      currentUser = user;
      currentConversationMessages = [];
      recentConversationsCache = [];
      conversationThreadCache.clear();
      historySectionState = null;
      isRestoringHistoryPlayback = false;
      if (!user.emailVerified) {
        hideAuditorProgressPanel();
        loginViewEl.style.display = 'block';
        verifyBanner.style.display = 'block';
        document.querySelector('.chat-wrapper').style.display = 'none';
        chatMessagesEl.style.display = 'none';
        inputAreaWrapperEl.style.display = 'none';
        return;
      }
      verifyBanner.style.display = 'none';
      loginErrorEl.style.display = 'none';
      loginViewEl.style.display = 'none';
      document.querySelector('.chat-wrapper').style.display = 'flex';
      chatMessagesEl.style.display = 'none';
      inputAreaWrapperEl.style.display = 'block';
      loadUserFiles();
      await initializeSelectionLayout();
    } else {
      currentUser = null;
      currentConversationMessages = [];
      recentConversationsCache = [];
      conversationThreadCache.clear();
      historySectionState = null;
      isRestoringHistoryPlayback = false;
      hideAuditorProgressPanel();
      verifyBanner.style.display = 'none';
      loginViewEl.style.display = 'block';
      document.querySelector('.chat-wrapper').style.display = 'none';
      chatMessagesEl.style.display = 'none';
      inputAreaWrapperEl.style.display = 'none';
    }
  });

  // ===================== 6) LOGIN / REGISTRO =====================
  loginButtonEl?.addEventListener('click', async () => {
    const email = emailInputEl.value.trim(), password = passwordInputEl.value;
    if (!email || !password) {
      loginErrorEl.textContent = "Por favor, introduce email y contraseña.";
      loginErrorEl.style.display = 'block'; return;
    }
    loginButtonEl.disabled = true;
    loginButtonEl.textContent = 'Iniciando sesión...';
    try {
      const cred = await auth.signInWithEmailAndPassword(email, password);
      loginErrorEl.classList.remove('is-success');
      if (!cred.user.emailVerified) {
        verifyBanner.style.display = 'block';
        loginErrorEl.textContent = "Debes verificar tu correo antes de usar el chat.";
        loginErrorEl.style.display = 'block';
      }
    } catch (err) {
      loginErrorEl.textContent = _fbMsg(err);
      loginErrorEl.classList.remove('is-success');
      loginErrorEl.style.display = 'block';
    } finally {
      loginButtonEl.disabled = false;
      loginButtonEl.textContent = 'Entrar';
    }
  });

  registerButtonEl?.addEventListener('click', async () => {
    const email = emailInputEl.value.trim(), password = passwordInputEl.value;
    if (!email || !password) {
      loginErrorEl.textContent = "Por favor, introduce email y contraseña.";
      loginErrorEl.style.display = 'block'; return;
    }
    registerButtonEl.disabled = true;
    registerButtonEl.textContent = 'Creando cuenta...';
    try {
      const cred = await auth.createUserWithEmailAndPassword(email, password);
      let msg = "¡Cuenta creada!";
      try {
        await cred.user.sendEmailVerification();
        msg += " Te hemos enviado un email de verificación.";
      } catch (_) {
        msg += " No pudimos enviar el email de verificación — usa el botón 'Reenviar verificación'.";
      }
      loginErrorEl.textContent = msg;
      loginErrorEl.classList.add('is-success');
      loginErrorEl.style.display = 'block';
      verifyBanner.style.display = 'block';
    } catch (err) {
      loginErrorEl.textContent = _fbMsg(err);
      loginErrorEl.classList.remove('is-success');
      loginErrorEl.style.display = 'block';
    } finally {
      registerButtonEl.disabled = false;
      registerButtonEl.textContent = 'Registrarse';
    }
  });

  // ===================== 7) WIDGET OPEN/CLOSE =====================
  chatBubbleEl?.addEventListener('click', () => chatWidgetContainerEl.classList.toggle('is-open'));
  chatCloseButtonEl?.addEventListener('click', () => chatWidgetContainerEl.classList.remove('is-open'));

  // ===================== 8) SELECCIÓN (3 FILAS) =====================
  async function initializeSelectionLayout() {
    await environmentReadyPromise;

    hideAuditorProgressPanel();

    // Fila 3 deshabilitada hasta elegir modo
    sendButtonEl.disabled = true;
    attachFileButtonEl.disabled = true;
    userInputEl.placeholder = "Selecciona un modo para comenzar...";

    // Ocultamos timeline en la selección
    chatMessagesEl.style.display = 'none';

    // evita duplicado
    document.querySelector('.selection-container')?.remove();

    const selectionContainer = document.createElement('section');
    selectionContainer.className = 'selection-container';

    // Fila 1: hero — un momento de llegada con identidad propia, antes de pedir
    // ninguna decisión. El saludo con nombre pasa a ser la línea secundaria.
    const hero = document.createElement('div');
    hero.className = 'hero-band';
    const displayName = (currentUser.displayName || currentUser.email || '').split('@')[0] || 'usuario';
    hero.innerHTML = `
      <p class="hero-eyebrow">Observatorio RECAVA</p>
      <h2 class="hero-h">Sostenibilidad y diligencia debida, sin ambigüedad</h2>
      <p class="hero-dek">Dos modos sobre el mismo corpus normativo — CSDDD, CSRD, GRI, EUDR —: asesoría inmediata o una auditoría estructurada con hallazgos clasificados.</p>
      <p class="hero-greet">Hola, <b>${escapeHtml(displayName)}</b> — esto es lo que puedes hacer ahora:</p>`;
    selectionContainer.appendChild(hero);

    renderConversationHistorySection(selectionContainer);

    // Fila 2: tarjetas
    const grid = document.createElement('div');
    grid.className = 'mode-grid';
    // Icono de "check" para los bullets de "Qué sí hace" y chevron del <details> "Ver más".
    const _bulletCheck = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>';
    const _moreChev = '<svg class="mode-more__chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';

    grid.innerHTML = `
      <article class="mode-card mode-card--advisor">
        <div class="mode-card__head">
          <span class="mode-card__icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M15.5 8.5l-2.2 5.8-5.8 2.2 2.2-5.8z"/></svg>
          </span>
          <div class="mode-card__titles">
            <span class="mode-card__kicker">Cumplimiento en sostenibilidad</span>
            <p class="mode-card__title">Modo Asesor</p>
          </div>
        </div>

        <p class="mode-card__summary">Te ayuda a implantar políticas, priorizar riesgos y traducir la CSDDD en procedimientos y KPIs operativos.</p>

        <ul class="mode-card__bullets">
          <li>${_bulletCheck}Traduce requisitos legales en procedimientos, cláusulas, checklists y KPIs operativos.</li>
          <li>${_bulletCheck}Orienta la implementación: gobernanza, matriz de riesgos, canales, auditorías internas, evidencias.</li>
          <li>${_bulletCheck}Señala qué datos/evidencias generan insumos útiles para CSRD/GRI sin elaborar la memoria.</li>
        </ul>

        <div class="mode-card__footer">
          <button class="mode-button-chat" data-mode="advisor" title="Seleccionar modo asesor" role="button">
            Seleccionar Modo Asesor
          </button>
        </div>

        <details class="mode-more">
          <summary>${_moreChev}Ver descripción completa y qué no hace</summary>
          <div class="mode-more__body">
            <p>En Modo Asesor, el asistente actúa como experto en diligencia debida y cumplimiento normativo en sostenibilidad,
              alineado con la CSDDD y normativa conexa (p. ej., EUDR, canales de alerta, PRL, etc.).
              Su función es ayudar a implantar políticas y códigos, analizar y priorizar riesgos, diseñar controles y trazabilidad,
              definir medidas correctoras y remediación, y operativizar los requisitos con estándares (OCDE, OIT, ISO).</p>
            <p><strong>Qué no hace:</strong> No redacta ni cierra el informe de sostenibilidad bajo CSRD ni sustituye verificaciones externas.</p>
            <p>Las respuestas se basan en fuentes normativas verificadas (UE/BOE/autoridades), estándares reconocidos (ISO/OCDE/OIT/GRI)
              y el corpus metodológico RECAVA, por lo que resultan idóneas para consultas técnicas y operativas sin intervención humana directa.</p>
          </div>
        </details>
      </article>

      <article class="mode-card mode-card--auditor">
        <div class="mode-card__head">
          <span class="mode-card__icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><path d="M9 12.5l2 2 4-4.5"/></svg>
          </span>
          <div class="mode-card__titles">
            <span class="mode-card__kicker">Cumplimiento en sostenibilidad</span>
            <p class="mode-card__title">Modo Auditor</p>
          </div>
        </div>

        <p class="mode-card__summary">Revisa tu sistema frente a la CSDDD en bloques estructurados y devuelve hallazgos clasificados con plan de acción.</p>

        <ul class="mode-card__bullets">
          <li>${_bulletCheck}Evalúa conformidad de tu sistema (políticas/códigos, análisis de riesgos, trazabilidad, remediación) frente a requisitos legales y estándares operativos (ISO/OCDE/OIT/GRI).</li>
          <li>${_bulletCheck}Pide y analiza muestras documentales (p. ej., matrices de riesgo, cláusulas a proveedores, registros de formación/SST, geolocalización EUDR).</li>
          <li>${_bulletCheck}Emite hallazgos clasificados (Crítico/Alto/Medio), con medidas, evidencias y prioridad.</li>
        </ul>

        <div class="mode-card__footer">
          <button class="mode-button-chat" data-mode="auditor" title="Seleccionar modo auditor" role="button">
            Seleccionar Modo Auditor
          </button>
        </div>

        <details class="mode-more">
          <summary>${_moreChev}Ver descripción completa, qué no hace y módulos del proceso</summary>
          <div class="mode-more__body">
            <p>En Modo Auditor, el asistente actúa como auditor digital de cumplimiento: revisa políticas, procedimientos y evidencias,
              detecta brechas frente a la CSDDD y normas relacionadas (p. ej., EUDR, canales de alerta, PRL), solicita información adicional cuando falta
              y devuelve un plan de acciones correctivas con responsables, plazos y evidencias mínimas.</p>
            <p><strong>Qué no hace:</strong> No sustituye auditorías de tercera parte ni inspecciones oficiales, ni emite certificaciones.
              No redacta ni valida el informe CSRD; solo indica qué datos/evidencias del cumplimiento alimentan ese reporte.</p>
            <p>Las respuestas se basan en fuentes normativas verificadas (UE/BOE/autoridades), estándares reconocidos (ISO/OCDE/OIT/GRI)
              y el corpus metodológico RECAVA, por lo que resultan idóneas para conocer tu grado de cumplimiento o el de tus proveedores.</p>
            <div class="mode-card__modules">
              <div class="mode-card__modules-title">Módulos del proceso</div>
              <div class="mode-chips">
                <span>Análisis de Riesgos</span><span>Políticas y Códigos en DDHH</span><span>Sistema de Gestión de Riesgos</span>
                <span>Transparencia y Publicidad</span><span>Informe de Sostenibilidad</span><span>Reparación de Daños</span>
                <span>Condiciones de Trabajo Dignas</span><span>Seguridad y Salud Laboral</span><span>Trabajo Forzado</span>
                <span>Trabajo Infantil</span><span>Medioambiente y Cambio Climático</span>
              </div>
            </div>
          </div>
        </details>
      </article>`;
    selectionContainer.appendChild(grid);

    // Insertar Fila 1+2 justo antes del input (Fila 3)
    if (chatWrapperEl && inputAreaWrapperEl) {
      chatWrapperEl.insertBefore(selectionContainer, inputAreaWrapperEl);
    } else {
      chatWidgetContainerEl.appendChild(selectionContainer);
    }

    // listeners
    selectionContainer.querySelectorAll('[data-mode]').forEach(btn => {
      btn.addEventListener('click', handleModeSelectionClick);
    });
  }

  // ===================== 8B) HISTORIAL DE CONVERSACIONES =====================
  function renderConversationHistorySection(parentEl) {
    if (!parentEl) return;
    historySectionState = null;

    // Franja compacta, no una tarjeta del tamaño de la decisión principal: si no hay
    // nada que continuar, se oculta entera en vez de decir "no tienes conversaciones".
    const section = document.createElement('div');
    section.className = 'history-strip';

    const label = document.createElement('span');
    label.className = 'history-strip__label';
    label.textContent = 'Continuar';
    section.appendChild(label);

    const listEl = document.createElement('ul');
    listEl.className = 'history-strip__list';
    section.appendChild(listEl);

    const statusEl = document.createElement('span');
    statusEl.className = 'history-strip__status';
    statusEl.textContent = 'Cargando conversaciones…';
    section.appendChild(statusEl);

    parentEl.appendChild(section);

    historySectionState = { section, listEl, statusEl };

    fetchRecentConversations(5)
      .then((conversations) => {
        recentConversationsCache = conversations;
        updateHistoryList(conversations);
      })
      .catch((error) => {
        console.error('No se pudo cargar el historial:', error);
        section.hidden = false;
        listEl.innerHTML = '';
        setHistoryStatusMessage('No se pudo cargar el historial. Inténtalo más tarde.', true);
      });
  }

  function setHistoryStatusMessage(message, isError = false) {
    if (!historySectionState?.statusEl) return;
    const { statusEl } = historySectionState;
    if (!message) {
      statusEl.textContent = '';
      statusEl.style.display = 'none';
      statusEl.classList.remove('is-error');
      return;
    }
    statusEl.textContent = message;
    statusEl.style.display = 'inline';
    statusEl.classList.toggle('is-error', !!isError);
  }

  function updateHistoryList(conversations) {
    if (!historySectionState?.listEl) return;
    const { listEl, section } = historySectionState;
    listEl.innerHTML = '';

    if (!conversations || !conversations.length) {
      // Nada que continuar: la franja no aporta nada, así que no ocupa espacio.
      section.hidden = true;
      return;
    }

    section.hidden = false;
    setHistoryStatusMessage('', false);

    conversations.forEach((conversation) => {
      const item = document.createElement('li');
      item.className = 'history-chip';

      const link = document.createElement('a');
      link.href = '#';
      link.className = 'history-chip__link';
      link.dataset.threadId = conversation.thread_id;
      if (conversation.endpoint_source) {
        link.dataset.endpointSource = conversation.endpoint_source;
      }

      const modeLabel = (conversation.endpoint_source || '').includes('auditor') ? 'Auditor' : 'Asesor';
      const summaryText = conversation.summary || 'Conversación previa';
      const metaText = formatHistoryTimestamp(conversation.last_timestamp);
      link.innerHTML =
        `<b>${escapeHtml(modeLabel)}</b><span class="history-chip__summary">${escapeHtml(summaryText)}</span>`
        + (metaText ? `<time>${escapeHtml(metaText)}</time>` : '');
      link.addEventListener('click', handleHistoryItemClick);

      item.appendChild(link);
      listEl.appendChild(item);

      const existing = conversationThreadCache.get(conversation.thread_id) || {};
      const merged = { ...existing, ...conversation };
      if (existing.messages && !conversation.messages) {
        merged.messages = existing.messages;
      }
      conversationThreadCache.set(conversation.thread_id, merged);
    });
  }

  function formatHistoryTimestamp(isoString) {
    if (!isoString) return '';
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return '';
    try {
      return date.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' });
    } catch (_e) {
      return date.toISOString().replace('T', ' ').split('.')[0];
    }
  }

  async function fetchRecentConversations(limit = 5) {
    await environmentReadyPromise;
    const baseUrl = getOrchestratorBaseUrl();
    if (!baseUrl) throw new Error('No se pudo determinar la URL del orquestador.');
    const token = await getVerifiedIdTokenOrThrow();
    const url = `${baseUrl}/chat_history/recents?limit=${encodeURIComponent(limit)}`;

    const { signal, cancel } = withTimeout(90000);
    const resp = await fetch(url, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Authorization': `Bearer ${token}`,
        'Idempotency-Key': idempotencyKey()
      },
      signal
    }).finally(cancel);

    if (!resp.ok) {
      let message = `Error ${resp.status}`;
      try {
        const payload = await resp.json();
        if (payload?.error) message = payload.error;
      } catch (_err) { /* noop */ }
      throw new Error(message);
    }

    const data = await parseApiResponse(resp);
    return Array.isArray(data?.conversations) ? data.conversations : [];
  }

  async function fetchConversationThread(threadId) {
    if (!threadId) throw new Error('threadId requerido');

    const cached = conversationThreadCache.get(threadId);
    if (cached?.messages && cached.messages.length) {
      return cached;
    }

    await environmentReadyPromise;
    const baseUrl = getOrchestratorBaseUrl();
    if (!baseUrl) throw new Error('No se pudo determinar la URL del orquestador.');
    const token = await getVerifiedIdTokenOrThrow();
    const url = `${baseUrl}/chat_history/thread/${encodeURIComponent(threadId)}`;

    const { signal, cancel } = withTimeout(90000);
    const resp = await fetch(url, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Authorization': `Bearer ${token}`,
        'Idempotency-Key': idempotencyKey()
      },
      signal
    }).finally(cancel);

    if (!resp.ok) {
      let message = `Error ${resp.status}`;
      try {
        const payload = await resp.json();
        if (payload?.error) message = payload.error;
      } catch (_err) { /* noop */ }
      throw new Error(message);
    }

    const data = await parseApiResponse(resp);
    if (!data || typeof data !== 'object') {
      throw new Error('Respuesta invalida al recuperar la conversacion.');
    }
    if (!Array.isArray(data.messages)) {
      data.messages = [];
    }

    const existing = conversationThreadCache.get(threadId) || {};
    conversationThreadCache.set(threadId, { ...existing, ...data });

    return data;
  }

  async function fetchAuditProgressForThread(threadId) {
    await environmentReadyPromise;
    const baseUrl = getOrchestratorBaseUrl();
    if (!baseUrl) throw new Error('No se pudo determinar la URL del orquestador.');
    const token = await getVerifiedIdTokenOrThrow();

    const { signal, cancel } = withTimeout(90000);
    const resp = await fetch(`${baseUrl}/audit_progress/${encodeURIComponent(threadId)}`, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
        'Authorization': `Bearer ${token}`,
        'Idempotency-Key': idempotencyKey()
      },
      signal
    }).finally(cancel);

    if (!resp.ok) {
      let message = `Error ${resp.status}`;
      try {
        const payload = await resp.json();
        if (payload?.error) message = payload.error;
      } catch (_err) { /* noop */ }
      throw new Error(message);
    }

    return await parseApiResponse(resp);
  }

  async function updateAuditProgressBlockStatus(threadId, blockId, status, summary) {
    await environmentReadyPromise;
    const baseUrl = getOrchestratorBaseUrl();
    if (!baseUrl) throw new Error('No se pudo determinar la URL del orquestador.');
    const token = await getVerifiedIdTokenOrThrow();

    const { signal, cancel } = withTimeout(90000);
    const resp = await fetch(`${baseUrl}/audit_progress/${encodeURIComponent(threadId)}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': `Bearer ${token}`,
        'Idempotency-Key': idempotencyKey()
      },
      signal,
      body: JSON.stringify({
        block_id: blockId,
        status,
        summary,
      }),
    }).finally(cancel);

    if (!resp.ok) {
      let message = `Error ${resp.status}`;
      try {
        const payload = await resp.json();
        if (payload?.error) message = payload.error;
      } catch (_err) { /* noop */ }
      throw new Error(message);
    }

    return await parseApiResponse(resp);
  }

  function determineModeFromEndpoint(endpointSource) {
    if (!endpointSource) return currentChatMode || 'advisor';
    if (endpointSource.includes('auditor')) return 'auditor';
    if (endpointSource.includes('assistant')) return 'advisor';
    return currentChatMode || 'advisor';
  }

  function showAuditorProgressPanel() {
    if (!auditorProgressPanelEl) return;
    auditorProgressPanelEl.classList.remove('hidden');
    _setupAuditLayout();
    renderAuditProgressPanel();
    if (currentChatThreadId) {
      refreshAuditProgress(currentChatThreadId);
    }
  }

  function hideAuditorProgressPanel() {
    if (!auditorProgressPanelEl) return;
    auditorProgressPanelEl.classList.add('hidden');
    _teardownAuditLayout();
  }

  function _setupAuditLayout() {
    if (!chatWrapperEl) return;
    chatWrapperEl.classList.add('audit-mode');

    if (_auditDomReady) return;
    _auditDomReady = true;

    // Restructure left panel: prepend new header + mini progress bar, wrap rest in body
    const hdr = document.createElement('div');
    hdr.className = 'audit-panel-hdr';
    hdr.innerHTML = `<span class="audit-panel-hdr__title">Proceso de auditoría</span><button class="audit-panel-hdr__toggle" title="Colapsar panel">‹</button>`;
    auditorProgressPanelEl.prepend(hdr);
    hdr.querySelector('.audit-panel-hdr__toggle').addEventListener('click', () => {
      chatWrapperEl.classList.toggle('audit-left-collapsed');
    });

    const miniBar = document.createElement('div');
    miniBar.className = 'audit-progress-mini-bar';
    miniBar.innerHTML = `
      <div class="audit-progress-mini-meta">
        <span>Completado</span>
        <span class="audit-progress-mini-pct">0%</span>
      </div>
      <div class="auditor-progress__bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
        <div class="auditor-progress__bar-fill" style="width:0%"></div>
      </div>`;
    hdr.insertAdjacentElement('afterend', miniBar);

    // Redirect refs to new elements
    auditorProgressTitleEl = hdr.querySelector('.audit-panel-hdr__title');
    auditorProgressPercentEl = miniBar.querySelector('.audit-progress-mini-pct');
    auditorProgressBarFillEl = miniBar.querySelector('.auditor-progress__bar-fill');

    // Wrap remaining children in scrollable body; hide old header + bar (replaced by mini-bar)
    const body = document.createElement('div');
    body.className = 'audit-panel-body';
    const toMove = [...auditorProgressPanelEl.children].slice(2);
    toMove.forEach(child => body.appendChild(child));
    auditorProgressPanelEl.appendChild(body);
    const oldHdr = body.querySelector('.auditor-progress__header');
    const oldBar = body.querySelector('.auditor-progress__bar');
    if (oldHdr) oldHdr.style.display = 'none';
    if (oldBar) oldBar.style.display = 'none';

    // Create right panel
    auditRightPanelEl = document.createElement('aside');
    auditRightPanelEl.className = 'audit-panel--right';
    auditRightPanelEl.innerHTML = `
      <div class="audit-panel-hdr">
        <button class="audit-panel-hdr__toggle" title="Colapsar panel">›</button>
        <span class="audit-panel-hdr__title">Informe y archivos</span>
      </div>
      <div class="audit-right-tabs">
        <button class="audit-right-tab is-active" data-tab="report">Informe</button>
        <button class="audit-right-tab" data-tab="files">Archivos</button>
      </div>
      <div class="audit-tab-pane is-active" data-pane="report">
        <p class="audit-panel-empty">Los hallazgos aparecerán aquí conforme se completen los bloques.</p>
      </div>
      <div class="audit-tab-pane" data-pane="files">
        <p class="audit-panel-empty">Los archivos adjuntos aparecerán aquí.</p>
      </div>`;
    chatWrapperEl.appendChild(auditRightPanelEl);

    auditRightPanelEl.querySelector('.audit-panel-hdr__toggle').addEventListener('click', () => {
      chatWrapperEl.classList.toggle('audit-right-collapsed');
    });
    auditRightPanelEl.querySelectorAll('.audit-right-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        auditRightPanelEl.querySelectorAll('.audit-right-tab').forEach(t => t.classList.remove('is-active'));
        auditRightPanelEl.querySelectorAll('.audit-tab-pane').forEach(p => p.classList.remove('is-active'));
        tab.classList.add('is-active');
        auditRightPanelEl.querySelector(`[data-pane="${tab.dataset.tab}"]`).classList.add('is-active');
      });
    });
  }

  function _teardownAuditLayout() {
    if (!chatWrapperEl) return;
    chatWrapperEl.classList.remove('audit-mode', 'audit-left-collapsed', 'audit-right-collapsed');
  }

  function buildDefaultAuditProgressState() {
    const blocks = AUDIT_BLOCKS_DEFINITION.map((block) => ({
      id: block.id,
      label: block.label,
      status: 'pending',
      summary: null,
      answered_count: 0,
      mandatory_count: 0,
      pending_questions: [],
      answered_questions: [],
      findings: [],
      deferred_reason: null,
      completed_at: null,
      updated_at: null,
    }));
    return {
      thread_id: currentChatThreadId || null,
      active_block_id: blocks[0]?.id || null,
      completed_count: 0,
      total_blocks: blocks.length,
      percent: 0,
      blocks,
    };
  }

  function normalizeAuditProgressState(state) {
    const defaultState = buildDefaultAuditProgressState();
    if (!state || !Array.isArray(state.blocks)) {
      return defaultState;
    }

    const incomingBlocks = new Map(state.blocks.map(block => [block.id, block]));
    const normalizedBlocks = AUDIT_BLOCKS_DEFINITION.map((definition) => {
      const info = incomingBlocks.get(definition.id) || {};
      const KNOWN = ['completed', 'in_progress', 'deferred'];
      const st = KNOWN.includes(info.status) ? info.status : 'pending';
      return {
        id: definition.id,
        label: info.label || definition.label,
        status: st,
        summary: info.summary || null,
        answered_count: Number.isFinite(info.answered_count) ? info.answered_count : 0,
        mandatory_count: Number.isFinite(info.mandatory_count) ? info.mandatory_count : 0,
        pending_questions: Array.isArray(info.pending_questions) ? info.pending_questions : [],
        answered_questions: Array.isArray(info.answered_questions) ? info.answered_questions : [],
        findings: Array.isArray(info.findings) ? info.findings : [],
        deferred_reason: info.deferred_reason || null,
        completed_at: info.completed_at || null,
        updated_at: info.updated_at || null,
      };
    });

    const completed = normalizedBlocks.filter(block => block.status === 'completed').length;
    const total = normalizedBlocks.length;

    let activeBlockId = state.active_block_id
      || (normalizedBlocks.find(b => b.status === 'in_progress')?.id)
      || (normalizedBlocks.find(b => b.status !== 'completed')?.id)
      || (normalizedBlocks[normalizedBlocks.length - 1]?.id);

    const percent = typeof state.percent === 'number'
      ? Math.max(0, Math.min(100, Math.round(state.percent)))
      : (total ? Math.round((completed / total) * 100) : 0);

    return {
      thread_id: state.thread_id || currentChatThreadId || null,
      active_block_id: activeBlockId,
      completed_count: typeof state.completed_count === 'number' ? state.completed_count : completed,
      total_blocks: typeof state.total_blocks === 'number' ? state.total_blocks : total,
      percent,
      blocks: normalizedBlocks,
    };
  }

  function setAuditProgressState(state) {
    auditProgressState = normalizeAuditProgressState(state);
    renderAuditProgressPanel();
  }

  function renderAuditProgressPanel() {
    if (!auditorProgressPanelEl) return;
    const state = auditProgressState || buildDefaultAuditProgressState();
    const totalBlocks = state.total_blocks || state.blocks.length || AUDIT_BLOCKS_DEFINITION.length;
    const completedBlocks = typeof state.completed_count === 'number'
      ? state.completed_count
      : state.blocks.filter(block => block.status === 'completed').length;
    const percent = typeof state.percent === 'number'
      ? state.percent
      : (totalBlocks ? Math.round((completedBlocks / totalBlocks) * 100) : 0);

    if (auditorProgressTitleEl) {
      auditorProgressTitleEl.textContent = `Progreso de auditoria (${completedBlocks}/${totalBlocks})`;
    }
    if (auditorProgressPercentEl) {
      auditorProgressPercentEl.textContent = `${percent}%`;
    }
    if (auditorProgressBarFillEl) {
      auditorProgressBarFillEl.style.width = `${percent}%`;
      auditorProgressBarFillEl.setAttribute('aria-valuenow', String(percent));
      const bar = auditorProgressBarFillEl.parentElement;
      if (bar) bar.setAttribute('aria-valuenow', String(percent));
    }

    renderAuditProgressList(state);
    renderAuditProgressSummary(state);
    _renderAuditRightPanel();
  }

  async function loadUserFiles() {
    if (!currentUser) return;
    try {
      const token = await currentUser.getIdToken();
      const baseUrl = getOrchestratorBaseUrl();
      const resp = await fetch(`${baseUrl}/user_files`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!resp.ok) return;
      const data = await resp.json();
      userFiles = data.files || [];
      renderFileList();
    } catch (_) { /* silent */ }
  }

  async function deleteUserFile(docId) {
    if (!currentUser) return;
    try {
      const token = await currentUser.getIdToken();
      const baseUrl = getOrchestratorBaseUrl();
      const resp = await fetch(`${baseUrl}/user_files/${encodeURIComponent(docId)}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!resp.ok) { addSystemMessageToChat('No se pudo eliminar el documento.'); return; }
      const data = await resp.json();
      userFiles = data.files || [];
      renderFileList();
    } catch (_) { addSystemMessageToChat('Error al eliminar el documento.'); }
  }

  function _fileItemHtml(f) {
    const date = f.uploaded_at ? new Date(f.uploaded_at).toLocaleDateString('es-ES', { day: '2-digit', month: 'short', year: '2-digit' }) : '';
    return `<div class="audit-file-item" data-doc-id="${escapeHtml(f.doc_id)}">
      <span class="audit-file-item__icon">📄</span>
      <div class="audit-file-item__info">
        <div class="audit-file-item__name" title="${escapeHtml(f.filename)}">${escapeHtml(f.filename)}</div>
        <div class="audit-file-item__block">${f.chunk_count} fragmentos · ${date}</div>
      </div>
      <button class="audit-file-item__del" title="Eliminar" data-doc-id="${escapeHtml(f.doc_id)}">✕</button>
    </div>`;
  }

  function renderFileList() {
    // Auditor right panel
    _renderAuditRightPanel();
    // Advisor file list
    const advisorList = document.getElementById('advisor-file-list');
    if (advisorList) {
      advisorList.innerHTML = userFiles.length
        ? userFiles.map(_fileItemHtml).join('')
        : '<p class="advisor-file-list__empty">Sin documentos subidos.</p>';
      advisorList.querySelectorAll('.audit-file-item__del').forEach(btn => {
        btn.addEventListener('click', () => deleteUserFile(btn.dataset.docId));
      });
    }
  }

  function _renderAuditRightPanel() {
    if (!auditRightPanelEl) return;
    const state = auditProgressState || buildDefaultAuditProgressState();

    // Informe tab — se lee como un documento que crece: un bloque plegable por cada
    // bloque cerrado, más el activo "en curso" con lo registrado hasta ahora. Antes solo
    // mostraba el último bloque cerrado y dejaba caer los hallazgos (findings) del activo.
    const reportPane = auditRightPanelEl.querySelector('[data-pane="report"]');
    if (reportPane) {
      const blocks = state.blocks || [];
      const closed = blocks.filter(b => b.status === 'completed' && b.summary && b.summary.trim());
      const activeBlock = blocks.find(b => b.id === state.active_block_id && b.status !== 'completed');

      const entries = [];
      closed.forEach(b => entries.push(
        `<details class="audit-report-blk" open>
          <summary class="audit-report-blk__hdr">
            <span class="audit-report-blk__chev">▶</span>
            <span class="audit-report-blk__title">${escapeHtml(b.label || b.id)}</span>
            <span class="audit-report-blk__status audit-report-blk__status--closed">Cerrado</span>
          </summary>
          <p class="audit-report-blk__text">${escapeHtml(b.summary.trim())}</p>
        </details>`
      ));
      if (activeBlock) {
        const total = activeBlock.mandatory_count || 0;
        const done = activeBlock.answered_count || 0;
        const pct = total ? Math.round((done / total) * 100) : 0;
        entries.push(
          `<details class="audit-report-blk" open>
            <summary class="audit-report-blk__hdr">
              <span class="audit-report-blk__chev">▶</span>
              <span class="audit-report-blk__title">${escapeHtml(activeBlock.label || activeBlock.id)}</span>
              <span class="audit-report-blk__status audit-report-blk__status--progress">En curso</span>
            </summary>
            <p class="audit-report-blk__text audit-report-blk__text--muted">${done} de ${total} preguntas obligatorias registradas.</p>
            <div class="audit-report-blk__bar"><i style="width:${pct}%"></i></div>
            ${_renderFindingsBadges(activeBlock.findings)}
          </details>`
        );
      }

      reportPane.innerHTML = entries.length
        ? entries.join('') + `<p class="audit-report-foot">Los bloques siguientes se irán añadiendo aquí a medida que se cierren.</p>`
        : '<p class="audit-panel-empty">Los hallazgos aparecerán aquí conforme se completen los bloques.</p>';
    }

    // Archivos tab — sourced from userFiles (server)
    const filesPane = auditRightPanelEl.querySelector('[data-pane="files"]');
    if (filesPane) {
      if (!userFiles.length) {
        filesPane.innerHTML = '<p class="audit-panel-empty">Los archivos subidos aparecerán aquí.</p>';
      } else {
        filesPane.innerHTML = userFiles.map(_fileItemHtml).join('');
        filesPane.querySelectorAll('.audit-file-item__del').forEach(btn => {
          btn.addEventListener('click', () => deleteUserFile(btn.dataset.docId));
        });
      }
    }
  }

  // Iconos del checklist en vivo del bloque activo (respondida / siguiente / en cola).
  const _checkIconDone = '<svg class="audit-check__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>';
  const _checkIconCurrent = '<svg class="audit-check__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="4.5"/></svg>';
  const _checkIconPending = '<svg class="audit-check__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/></svg>';

  // Mismo vocabulario de veredicto que usa el servidor (ver _VERDICT_RE en gemini_service.py).
  const VERDICT_META = {
    'cumple':              { label: 'Cumple',             cls: 'ok' },
    'cumple parcialmente': { label: 'Cumple parcialmente', cls: 'warn' },
    'no cumple':           { label: 'No cumple',           cls: 'bad' },
    'no evaluable':        { label: 'No evaluable',        cls: 'neutral' },
  };

  // `assessment` es el texto crudo "VEREDICTO: …\nBRECHA: …\nRECOMENDACIÓN: …\nBASE: …"
  // que guarda el servidor. Aquí solo se extrae la brecha para el resumen visual.
  function _parseFindingGap(text) {
    const m = (text || '').match(/BRECHA:\s*([\s\S]*?)(?:\n[A-ZÁÉÍÓÚ]+:|$)/i);
    return m ? m[1].trim() : '';
  }

  function _renderFindingsBadges(findings) {
    const list = (Array.isArray(findings) ? findings : []).filter(f => f && f.verdict);
    if (!list.length) return '';
    return '<div class="audit-blk__findings">' + list.slice(-4).map(f => {
      const meta = VERDICT_META[String(f.verdict).toLowerCase()] || VERDICT_META['no evaluable'];
      const gap = _parseFindingGap(f.assessment);
      const gapHtml = (meta.cls !== 'ok' && gap && gap.toLowerCase() !== 'ninguna')
        ? `<p class="audit-finding__gap">${escapeHtml(gap)}</p>` : '';
      return `<div class="audit-finding audit-finding--${meta.cls}">
        <span class="audit-finding__badge">${meta.label}</span>${gapHtml}
      </div>`;
    }).join('') + '</div>';
  }

  function renderAuditProgressList(state) {
    if (!auditorProgressListEl || !auditorProgressEmptyEl) return;
    auditorProgressListEl.innerHTML = '';

    const blocks = state.blocks || [];
    if (!blocks.length) {
      auditorProgressEmptyEl.classList.remove('hidden');
      return;
    }

    auditorProgressEmptyEl.classList.add('hidden');
    const activeBlockId = state.active_block_id;

    blocks.forEach((block, idx) => {
      const isActive = block.id === activeBlockId;
      const isDone = block.status === 'completed';
      const isDeferred = block.status === 'deferred';
      const total = block.mandatory_count || 0;
      const done = block.answered_count || 0;

      const li = document.createElement('li');
      const modClass = isDone ? 'audit-blk--done'
        : (isDeferred ? 'audit-blk--deferred' : (isActive ? 'audit-blk--active' : ''));
      li.className = `audit-blk${modClass ? ' ' + modClass : ''}`;
      li.dataset.blockId = block.id;

      const numContent = isDone ? '✓' : (isDeferred ? '⏸' : String(idx + 1));
      const canComplete = canCompleteAuditBlock(block, state);
      const completeBtnHtml = canComplete
        ? `<button class="audit-blk__complete-btn" type="button" data-action="complete-block" data-block-id="${block.id}">Completar</button>`
        : '';
      // Contador de preguntas obligatorias: es lo que deja ver de un vistazo si el
      // bloque está realmente cubierto o solo parcialmente contestado.
      const countHtml = (!isDone && total)
        ? `<span class="audit-blk__count" title="Preguntas obligatorias respondidas">${done}/${total}</span>`
        : '';

      let bodyContent;
      if (isDone && block.summary && block.summary.trim()) {
        bodyContent = escapeHtml(block.summary.trim());
      } else if (isDeferred) {
        const reason = block.deferred_reason ? ` — ${escapeHtml(block.deferred_reason)}` : '';
        bodyContent = `<span class="audit-blk__body-empty">Aplazado${reason}</span>`;
      } else if (isActive || done > 0) {
        const answeredQ = Array.isArray(block.answered_questions) ? block.answered_questions : [];
        const pend = Array.isArray(block.pending_questions) ? block.pending_questions : [];
        // Checklist en vivo: lo ya registrado (✓), la siguiente pregunta del guion
        // (● — es la única que el auditor formulará ahora, una por turno) y el resto
        // en cola (○). Sustituye a la lista plana de "pendientes" de antes.
        const checkItems = [
          ...answeredQ.map(q => `<li class="audit-check audit-check--done">${_checkIconDone}${escapeHtml(q.text || q.id || '')}</li>`),
          ...pend.slice(0, 1).map(q => `<li class="audit-check audit-check--current">${_checkIconCurrent}${escapeHtml(q.text || q.id || '')}</li>`),
          ...pend.slice(1, 8).map(q => `<li class="audit-check audit-check--pending">${_checkIconPending}${escapeHtml(q.text || q.id || '')}</li>`),
        ];
        const moreCount = pend.length > 8 ? pend.length - 8 : 0;
        bodyContent = checkItems.length
          ? `<ul class="audit-blk__checklist">${checkItems.join('')}${moreCount ? `<li class="audit-check audit-check--pending">…y ${moreCount} más</li>` : ''}</ul>`
          : '<span class="audit-blk__body-empty">Todas las obligatorias respondidas: listo para cerrar.</span>';
        bodyContent += _renderFindingsBadges(block.findings);
      } else {
        bodyContent = '<span class="audit-blk__body-empty">Pendiente</span>';
      }

      li.innerHTML = `
        <div class="audit-blk__hdr">
          <span class="audit-blk__num">${numContent}</span>
          <span class="audit-blk__label">${escapeHtml(block.label || block.id)}</span>
          ${countHtml}
          ${completeBtnHtml}
          <span class="audit-blk__chevron">▶</span>
        </div>
        <div class="audit-blk__body">${bodyContent}</div>`;

      if (isDone || isActive) li.classList.add('is-open');

      li.querySelector('.audit-blk__hdr').addEventListener('click', (e) => {
        if (e.target.closest('[data-action]')) return;
        li.classList.toggle('is-open');
      });

      auditorProgressListEl.appendChild(li);
    });
  }

  function renderAuditProgressSummary(state) {
    if (!auditorProgressSummaryEl) return;

    const blocks = state.blocks || [];
    const activeBlock = blocks.find(block => block.id === state.active_block_id) || blocks[0];

    if (!activeBlock) {
      auditorProgressSummaryEl.classList.add('hidden');
      return;
    }

    auditorProgressSummaryEl.classList.remove('hidden');
    if (auditorProgressSummaryTitleEl) {
      auditorProgressSummaryTitleEl.textContent = `Bloque activo: ${activeBlock.label || activeBlock.id}`;
    }
    if (auditorProgressSummaryTextEl) {
      const summary = activeBlock.summary;
      auditorProgressSummaryTextEl.textContent = summary && summary.trim().length
        ? summary.trim()
        : 'Aun no hay un informe disponible para este bloque.';
    }
  }

  function formatAuditBlockStatus(status, isActive) {
    if (status === 'completed') return 'Completado';
    if (status === 'deferred') return 'Aplazado';
    if (status === 'in_progress' || isActive) return 'En progreso';
    return 'Pendiente';
  }

  function canViewAuditReport(block, state) {
    if (!block) return false;
    return !!(block.summary && block.summary.trim().length);
  }

  function canCompleteAuditBlock(block, state) {
    if (!block) return false;
    if (!currentUser || currentChatMode !== 'auditor') return false;
    const isActive = block.id === state.active_block_id;
    return isActive && block.status !== 'completed';
  }

  function handleAuditProgressPanelClick(event) {
    const action = event.target?.dataset?.action;
    if (!action) return;
    const blockId = event.target.dataset.blockId;
    if (!blockId) return;

    if (action === 'view-report') {
      handleViewAuditReport(blockId);
    } else if (action === 'complete-block') {
      handleCompleteAuditBlock(blockId, event.target);
    }
  }

  function handleViewAuditReport(blockId) {
    const state = auditProgressState || buildDefaultAuditProgressState();
    const block = state.blocks.find(b => b.id === blockId);
    if (!block) return;

    const baseLabel = block.label || block.id;
    const summary = block.summary && block.summary.trim().length
      ? block.summary.trim()
      : 'Todavia no hay un informe disponible para este bloque.';

    addSystemMessageToChat(`Informe de auditoria para "${baseLabel}": ${summary}`);
  }

  async function handleCompleteAuditBlock(blockId, buttonEl) {
    if (!currentChatThreadId) {
      addSystemMessageToChat('Necesitas iniciar una conversacion antes de marcar bloques.');
      return;
    }

    const _blockForConfirm = (auditProgressState?.blocks || []).find(b => b.id === blockId);
    const _blockLabel = _blockForConfirm?.label || blockId;
    if (!confirm(`¿Confirmas que el bloque "${_blockLabel}" está completado?\nEsta acción no se puede deshacer.`)) {
      return;
    }

    if (buttonEl) {
      buttonEl.disabled = true;
      buttonEl.dataset.loading = '1';
      buttonEl.textContent = 'Actualizando...';
    }

    try {
      const updated = await updateAuditProgressBlockStatus(currentChatThreadId, blockId, 'completed');
      setAuditProgressState(updated);
      addSystemMessageToChat(`El bloque ha sido marcado como completado.`);
    } catch (error) {
      console.error('No se pudo actualizar el bloque de auditoria:', error);
      addSystemMessageToChat('No se pudo actualizar el progreso de auditoria. Intentalo mas tarde.');
    } finally {
      if (buttonEl) {
        if (buttonEl.isConnected) {
          buttonEl.removeAttribute('data-loading');
          buttonEl.textContent = 'Marcar completado';
          buttonEl.disabled = !canCompleteAuditBlock(
            (auditProgressState?.blocks || []).find(block => block.id === blockId),
            auditProgressState || buildDefaultAuditProgressState()
          );
        }
      }
    }
  }

  async function refreshAuditProgress(threadId) {
    if (!threadId || isFetchingAuditProgress) return;
    isFetchingAuditProgress = true;
    try {
      const progress = await fetchAuditProgressForThread(threadId);
      if (progress) setAuditProgressState(progress);
    } catch (error) {
      console.error('No se pudo obtener el progreso de auditoria:', error);
    } finally {
      isFetchingAuditProgress = false;
    }
  }

  function resumeConversationFromHistory(conversationData) {
    if (!conversationData) return;

    currentChatThreadId = conversationData.thread_id || null;
    currentChatMode = determineModeFromEndpoint(conversationData.endpoint_source);
    currentConversationMessages = Array.isArray(conversationData.messages)
      ? conversationData.messages.slice()
      : [];

    if (currentChatMode === 'auditor') {
      setAuditProgressState(buildDefaultAuditProgressState());
      showAuditorProgressPanel();
      if (currentChatThreadId) refreshAuditProgress(currentChatThreadId);
    } else {
      hideAuditorProgressPanel();
    }

    document.querySelector('.selection-container')?.remove();

    chatMessagesEl.style.display = 'flex';
    sendButtonEl.disabled = false;
    attachFileButtonEl.disabled = false;
    userInputEl.value = '';
    adjustUserInputHeight();
    inputAreaWrapperEl.style.display = 'block';

    const messagesToShow = currentConversationMessages.slice(-15);
    chatMessagesEl.innerHTML = '';

    if (!messagesToShow.length) {
      addSystemMessageToChat('No encontramos mensajes previos en esta conversacion.');
    } else {
      isRestoringHistoryPlayback = true;
      try {
        messagesToShow.forEach((msg) => {
          if (msg.role === 'assistant') {
            addAssistantMessageWithCitations(msg.text, []);
          } else if (msg.role === 'user') {
            addUserMessageToChat(msg.text);
          } else if (msg.role === 'system') {
            addSystemMessageToChat(msg.text);
          }
        });
      } finally {
        isRestoringHistoryPlayback = false;
      }
    }

    scrollChatToBottom({ behavior: 'auto' });

    if (currentChatMode === 'auditor') {
      userInputEl.placeholder = 'Continua con la conversacion de auditor...';
    } else {
      userInputEl.placeholder = 'Escribe tu mensaje para continuar...';
    }

    userInputEl.focus();
    syncConversationCache(currentChatThreadId, conversationData.endpoint_source);
    setHistoryStatusMessage('', false);
  }

  function syncConversationCache(threadId, endpointSource) {
    if (!threadId) return;
    const existing = conversationThreadCache.get(threadId) || {};
    const payload = {
      ...existing,
      thread_id: threadId,
      endpoint_source: endpointSource || existing.endpoint_source || getEndpointSourceForMode(currentChatMode),
      messages: currentConversationMessages.slice(),
      last_timestamp: new Date().toISOString(),
    };
    conversationThreadCache.set(threadId, payload);
  }

  function getEndpointSourceForMode(mode) {
    if (mode === 'auditor') return '/chat_auditor';
    if (mode === 'advisor') return '/chat_assistant';
    return undefined;
  }

  async function handleHistoryItemClick(event) {
    event.preventDefault();
    const link = event.currentTarget;
    if (!link || link.dataset.loading === '1') return;

    const threadId = link.dataset.threadId;
    if (!threadId) return;

    link.dataset.loading = '1';
    link.classList.add('is-loading');
    setHistoryStatusMessage('Cargando conversacion...', false);

    try {
      const conversation = await fetchConversationThread(threadId);
      resumeConversationFromHistory(conversation);
    } catch (error) {
      console.error('No se pudo abrir la conversacion seleccionada:', error);
      setHistoryStatusMessage('No se pudo abrir la conversacion seleccionada.', true);
    } finally {
      link.dataset.loading = '';
      link.classList.remove('is-loading');
    }
  }

  function handleModeSelectionClick(e) {
    currentChatMode = e.target.dataset.mode;
    document.querySelector('.selection-container')?.remove();

    // Mostrar timeline y habilitar input (Fila 3)
    chatMessagesEl.style.display = 'flex';
    chatMessagesEl.innerHTML = '';
    sendButtonEl.disabled = false;
    attachFileButtonEl.disabled = false;
    userInputEl.value = ''; userInputEl.focus(); adjustUserInputHeight();
    currentChatThreadId = null;
    currentConversationMessages = [];
    inputAreaWrapperEl.style.display = 'block';
    if (currentChatMode === 'auditor') {
      setAuditProgressState(buildDefaultAuditProgressState());
      showAuditorProgressPanel();
      advisorFilePanelEl.classList.add('hidden');
    } else {
      hideAuditorProgressPanel();
      if (userFiles.length) advisorFilePanelEl.classList.remove('hidden');
    }
    setHistoryStatusMessage('', false);

    if (currentChatMode === 'auditor') {
      addAssistantMessageInternal("Has seleccionado el modo <strong>AUDITOR</strong>.<br>Comienza por contarme: nombre de la empresa, sector, tamaño y sedes.");
      userInputEl.placeholder = "Nombre, sector, tamaño, sedes...";
    } else {
      addAssistantMessageInternal("Has seleccionado el modo <strong>ASESOR</strong>.<br>¿En qué puedo ayudarte hoy?");
      userInputEl.placeholder = "Escribe tu consulta de asesoría...";
    }
  }

  // ===================== 9) ENVÍO MENSAJES =====================
  async function handleSendMessageToServer() {
    const messageText = userInputEl.value.trim();
    if (!messageText) return;

    if (!currentChatMode) {
      addSystemMessageToChat("Elige primero <strong>Modo Asesor</strong> o <strong>Modo Auditor</strong>.");
      return;
    }
    if (!currentUser) {
      addSystemMessageToChat("Error de autenticación. Por favor, recarga la página.");
      return;
    }

    let token;
    try { token = await getVerifiedIdTokenOrThrow(); }
    catch(e){
      removeTypingIndicatorFromChat();
      addSystemMessageToChat(e.message || "Necesitas verificar tu email para continuar.");
      verifyBanner.style.display = 'block';
      return;
    }

    currentConversationMessages.push({
      role: 'user',
      text: messageText,
      timestamp: new Date().toISOString(),
    });
    addUserMessageToChat(messageText);
    userInputEl.value = ''; adjustUserInputHeight();
    showTypingIndicatorToChat();

    const endpointUrl = currentChatMode === 'auditor' ? currentEndpoints.auditor : currentEndpoints.advisor;

    try {
      const { signal, cancel } = withTimeout(90000);
      const resp = await fetch(endpointUrl, {
        method:'POST',
        headers:{
          'Content-Type':'application/json',
          'Authorization':`Bearer ${token}`,
          'Idempotency-Key': idempotencyKey()
        },
        signal,
        body: JSON.stringify({ message: messageText, thread_id: currentChatThreadId })
      });
      cancel();
      removeTypingIndicatorFromChat();
      if (!resp.ok) {
        const err = await resp.json().catch(()=>({error:"Error de red", details:`Status ${resp.status}`}));
        currentConversationMessages.pop();
        userInputEl.value = messageText;
        adjustUserInputHeight();
        addSystemErrorWithRetry(
          `Error del servidor: ${err.error || resp.statusText}.`,
          () => handleSendMessageToServer()
        );
        userInputEl.focus();
        return;
      }
      const data = await parseApiResponse(resp);
      if (data.thread_id) currentChatThreadId = data.thread_id;
      if (data.response) {
        const cleaned = data.response.replace(/【.*?†source】/g,'').trim();
        addAssistantMessageWithCitations(cleaned, data.sources || []);
        currentConversationMessages.push({
          role: 'assistant',
          text: cleaned,
          timestamp: new Date().toISOString(),
        });
      } else if (data.error) {
        addSystemMessageToChat(`Error del asistente: ${data.error}`);
      }
      syncConversationCache(currentChatThreadId, getEndpointSourceForMode(currentChatMode));
      if (currentChatMode === 'auditor' && currentChatThreadId) {
        await refreshAuditProgress(currentChatThreadId);
      }
    } catch (e) {
      removeTypingIndicatorFromChat();
      currentConversationMessages.pop();
      userInputEl.value = messageText;
      adjustUserInputHeight();
      addSystemErrorWithRetry(
        "No se pudo conectar con el servidor.",
        () => handleSendMessageToServer()
      );
      console.error("fetch error:", e);
      userInputEl.focus();
    }
  }

  // ===================== 10) AUXILIARES UI =====================
  function addAssistantMessageWithCitations(responseText, sourcesList){
    const wrap = document.createElement('div'); wrap.classList.add('message','assistant-message');
    const main = document.createElement('div'); main.classList.add('main-assistant-text');

    // Render markdown then replace [N] with clickable+hoverable superscript badges
    const fallback = "El asistente no proporcionó una respuesta textual.";
    const _rawHtml = window.marked ? marked.parse(responseText || fallback) : (responseText || fallback);
    let html = window.DOMPurify ? DOMPurify.sanitize(_rawHtml) : _rawHtml;
    html = html.replace(/\[(\d+)\]/g, (_, n) => {
      const src = (sourcesList || []).find(s => String(s.index) === n);
      const ttTitle = src
        ? escapeHtml(`${src.title}${src.page != null ? ` · p.${src.page}${src.total_pages ? '/' + src.total_pages : ''}` : ''}${src.category ? ' · ' + src.category : ''}`)
        : '';
      const ttExcerpt = src ? escapeHtml(src.excerpt || '') : '';
      return `<sup class="citation-inline" data-ref="${n}" data-tt-title="${ttTitle}" data-tt-excerpt="${ttExcerpt}">[${n}]</sup>`;
    });
    main.innerHTML = html;
    const _timeEl = document.createElement('time');
    _timeEl.className = 'message-time';
    _timeEl.textContent = _formatMsgTime(new Date());
    main.appendChild(_timeEl);
    wrap.appendChild(main);

    if (sourcesList && sourcesList.length) {
      const cont = document.createElement('div');
      cont.classList.add('citations-container', 'collapsed');

      const toggle = document.createElement('div');
      toggle.classList.add('citations-toggle');
      toggle.innerHTML =
        `<span class="citations-label">Fuentes documentales (${sourcesList.length})</span>` +
        `<span class="citations-toggle__arrow">▼</span>`;
      toggle.addEventListener('click', () => cont.classList.toggle('collapsed'));
      cont.appendChild(toggle);

      const list = document.createElement('div');
      list.classList.add('citations-list');
      sourcesList.forEach(s => {
        const item = document.createElement('div');
        item.classList.add('citation-item');
        item.dataset.idx = String(s.index);

        const scorePct = Math.round((s.score || 0) * 100);
        const pagePart = s.page != null
          ? ` · p.${s.page}${s.total_pages ? '/' + s.total_pages : ''}`
          : '';
        const catPart  = s.category ? ` · ${escapeHtml(s.category)}` : '';

        item.innerHTML =
          `<span class="citation-marker">[${s.index}]</span>` +
          `<span class="citation-title">${escapeHtml(s.title || 'Documento')}</span>` +
          `<span class="citation-meta">${catPart}${pagePart} · relevancia ${scorePct}%</span>` +
          `<span class="citation-quote">${escapeHtml(s.excerpt || '')}</span>`;

        list.appendChild(item);
      });
      cont.appendChild(list);
      wrap.appendChild(cont);
    }

    chatMessagesEl.appendChild(wrap);

    // Wire up inline badges → scroll to citation card
    wrap.querySelectorAll('.citation-inline').forEach(badge => {
      badge.tabIndex = 0;
      badge.setAttribute('role', 'button');
      const _activateCitation = () => {
        const target = wrap.querySelector(`.citation-item[data-idx="${badge.dataset.ref}"]`);
        if (target) { target.scrollIntoView({behavior:'smooth',block:'nearest'}); target.classList.add('citation-highlight'); setTimeout(()=>target.classList.remove('citation-highlight'), 1200); }
      };
      badge.addEventListener('click', _activateCitation);
      badge.addEventListener('keydown', (e) => { if (e.key==='Enter'||e.key===' ') { e.preventDefault(); _activateCitation(); } });
    });

    scrollChatToBottom();
  }
  function _formatMsgTime(date) {
    try { return date.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' }); }
    catch (_) { return ''; }
  }
  function addMessageToChatDOM(html, cls){
    const el = document.createElement('div'); el.classList.add('message', cls); el.innerHTML = html;
    chatMessagesEl.appendChild(el); scrollChatToBottom();
  }
  function addUserMessageToChat(t){
    const s=t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    const el=document.createElement('div'); el.classList.add('message','user-message');
    el.innerHTML=`${s}<time class="message-time">${_formatMsgTime(new Date())}</time>`;
    chatMessagesEl.appendChild(el); scrollChatToBottom();
  }
  function addSystemMessageToChat(t){ const s=t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); addMessageToChatDOM(s,'system-message'); }
  // Variante para errores de envío: el texto del usuario ya se restaura en el input
  // (ver handleSendMessageToServer), así que "reintentar" es simplemente reenviarlo.
  function addSystemErrorWithRetry(t, onRetry){
    const s = t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    const el = document.createElement('div');
    el.classList.add('message', 'system-message', 'system-message--error');
    el.innerHTML = `<span class="system-message__text">${s}</span>` +
      `<button type="button" class="system-message__retry">↻ Reintentar</button>`;
    el.querySelector('.system-message__retry').addEventListener('click', () => { el.remove(); onRetry(); });
    chatMessagesEl.appendChild(el);
    scrollChatToBottom();
  }
  function addAssistantMessageInternal(html){ const el=document.createElement('div'); el.classList.add('message','assistant-message'); el.innerHTML=`<div class="main-assistant-text">${html}</div>`; chatMessagesEl.appendChild(el); scrollChatToBottom(); }
  function escapeHtml(u){ if(!u) return ''; return u.toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;"); }

  // ---- RAG chunk tooltip (hover over inline citation badge) ----
  const _ragTip = document.createElement('div');
  _ragTip.id = 'rag-tooltip';
  _ragTip.style.display = 'none';
  document.body.appendChild(_ragTip);

  function _positionRagTip(badge) {
    _ragTip.style.visibility = 'hidden';
    _ragTip.style.display = 'block';
    const r = badge.getBoundingClientRect();
    const h = _ragTip.offsetHeight, w = _ragTip.offsetWidth;
    let top = r.top - h - 10;
    let left = r.left;
    if (top < 8) top = r.bottom + 10;
    if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
    if (left < 8) left = 8;
    _ragTip.style.top = top + 'px';
    _ragTip.style.left = left + 'px';
    _ragTip.style.visibility = 'visible';
  }

  if (chatMessagesEl) {
    chatMessagesEl.addEventListener('mouseover', e => {
      const b = e.target.closest('.citation-inline');
      if (!b) return;
      const title = b.dataset.ttTitle || '';
      const excerpt = b.dataset.ttExcerpt || '';
      if (!title && !excerpt) return;
      _ragTip.innerHTML =
        (title   ? `<div class="tt-title">${title}</div>` : '') +
        (excerpt ? `<div class="tt-excerpt">${excerpt}</div>` : '');
      _positionRagTip(b);
    });
    chatMessagesEl.addEventListener('mouseout', e => {
      if (e.target.closest('.citation-inline')) _ragTip.style.display = 'none';
    });
  }
  // ---- end RAG tooltip ----

  function adjustUserInputHeight(){
    userInputEl.style.height='auto';
    const max=120, sh=userInputEl.scrollHeight;
    userInputEl.style.height=(sh>max?max:sh)+'px';
    userInputEl.style.overflowY=(sh>max?'auto':'hidden');
  }
  function debounce(fn, ms=100){ let h; return (...a)=>{ clearTimeout(h); h=setTimeout(()=>fn(...a),ms); }; }
  userInputEl?.addEventListener('input', debounce(adjustUserInputHeight, 60)); adjustUserInputHeight();

  let typingIndicatorDiv=null;
  let _typingProgressTimer=null;
  function showTypingIndicatorToChat(){
    if(typingIndicatorDiv) return;
    typingIndicatorDiv=document.createElement('div');
    typingIndicatorDiv.classList.add('message','assistant-message','typing-indicator');
    typingIndicatorDiv.innerHTML='<div class="typing-dots"><span></span><span></span><span></span></div><p class="typing-progress-msg" style="display:none;margin:.35rem 0 0;font-size:.78rem;color:var(--texto-gris-sutil);"></p>';
    chatMessagesEl.appendChild(typingIndicatorDiv);
    scrollChatToBottom({ behavior: 'smooth' });
    const msgEl = typingIndicatorDiv.querySelector('.typing-progress-msg');
    _typingProgressTimer = setTimeout(() => {
      if (msgEl) { msgEl.textContent = 'Generando respuesta...'; msgEl.style.display = ''; }
      _typingProgressTimer = setTimeout(() => {
        if (msgEl) msgEl.textContent = 'Esto puede tardar un momento más...';
      }, 25000);
    }, 12000);
  }
  function removeTypingIndicatorFromChat(){
    if(_typingProgressTimer){ clearTimeout(_typingProgressTimer); _typingProgressTimer=null; }
    if(typingIndicatorDiv){ typingIndicatorDiv.remove(); typingIndicatorDiv=null; }
  }

  function scrollChatToBottom(options){
    if (!chatMessagesEl) return;
    const requestedBehavior = (options && options.behavior) ? options.behavior : 'smooth';
    const effectiveBehavior = isRestoringHistoryPlayback ? 'auto' : requestedBehavior;

    const performScroll = () => {
      if (typeof chatMessagesEl.scrollTo === 'function') {
        try {
          chatMessagesEl.scrollTo({ top: chatMessagesEl.scrollHeight, behavior: effectiveBehavior });
          return;
        } catch (_e) {
          // fallback below
        }
      }
      chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
    };

    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(performScroll);
    } else {
      performScroll();
    }
  }

  // ===================== FILE UPLOAD =====================
  let pendingFile = null;

  // Hidden file input — injected once
  const fileInputEl = document.createElement('input');
  fileInputEl.type = 'file';
  fileInputEl.accept = 'application/pdf';
  fileInputEl.style.display = 'none';
  document.body.appendChild(fileInputEl);

  // Advisor file list — persistent docs panel above input (advisor mode only)
  const advisorFilePanelEl = document.createElement('div');
  advisorFilePanelEl.id = 'advisor-file-panel';
  advisorFilePanelEl.className = 'advisor-file-panel hidden';
  advisorFilePanelEl.innerHTML =
    `<div class="advisor-file-panel__hdr">
       <span>Mis documentos</span>
       <button class="advisor-file-panel__toggle" title="Cerrar">✕</button>
     </div>
     <div id="advisor-file-list" class="advisor-file-list"></div>`;
  inputAreaWrapperEl?.insertAdjacentElement('beforebegin', advisorFilePanelEl);
  advisorFilePanelEl.querySelector('.advisor-file-panel__toggle')
    ?.addEventListener('click', () => advisorFilePanelEl.classList.add('hidden'));

  // Active-doc badge — shows which PDF is loaded in this session
  const activeDocBadgeContainer = document.createElement('div');
  activeDocBadgeContainer.id = 'active-doc-badge';
  activeDocBadgeContainer.className = 'active-doc-badge hidden';
  inputAreaWrapperEl?.insertAdjacentElement('beforebegin', activeDocBadgeContainer);

  function showActiveDocBadge(fileName) {
    activeDocBadgeContainer.innerHTML =
      `<span class="active-doc-badge__icon">📄</span>` +
      `<span class="active-doc-badge__name">${escapeHtml(fileName)}</span>` +
      `<span class="active-doc-badge__hint">activo en esta sesión</span>`;
    activeDocBadgeContainer.classList.remove('hidden');
  }
  function clearActiveDocBadge() {
    activeDocBadgeContainer.classList.add('hidden');
    activeDocBadgeContainer.innerHTML = '';
  }

  // File preview row (already in HTML as #file-preview-area)
  const filePreviewEl = document.getElementById('file-preview-area');
  let filePreviewNameEl = null;
  let uploadBtnEl = null;
  if (filePreviewEl) {
    filePreviewNameEl = filePreviewEl.querySelector('span') || (() => {
      const s = document.createElement('span'); filePreviewEl.prepend(s); return s;
    })();
    uploadBtnEl = document.createElement('button');
    uploadBtnEl.className = 'upload-pdf-btn';
    uploadBtnEl.textContent = 'Subir PDF';
    uploadBtnEl.type = 'button';
    const removeBtn = filePreviewEl.querySelector('.remove-file-button');
    filePreviewEl.insertBefore(uploadBtnEl, removeBtn || null);
    uploadBtnEl.addEventListener('click', () => handleFileUpload());
  }

  function showFilePreview(file) {
    pendingFile = file;
    if (filePreviewNameEl) filePreviewNameEl.textContent = `📄 ${file.name}`;
    filePreviewEl?.classList.add('has-file');
  }
  function clearFilePreview() {
    pendingFile = null;
    fileInputEl.value = '';
    if (filePreviewNameEl) filePreviewNameEl.textContent = '';
    filePreviewEl?.classList.remove('has-file');
  }

  // "Remove file" button
  filePreviewEl?.querySelector('.remove-file-button')?.addEventListener('click', clearFilePreview);

  fileInputEl.addEventListener('change', (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      addSystemMessageToChat('Solo se admiten archivos PDF.');
      fileInputEl.value = '';
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      addSystemMessageToChat('El archivo supera el límite de 20 MB.');
      fileInputEl.value = '';
      return;
    }
    showFilePreview(file);
  });

  async function handleFileUpload() {
    if (!pendingFile) return;
    if (!currentUser) { addSystemMessageToChat('Debes iniciar sesión para subir documentos.'); return; }
    if (!currentChatMode) currentChatMode = 'advisor';

    let token;
    try { token = await getVerifiedIdTokenOrThrow(); }
    catch(e) { addSystemMessageToChat(e.message || 'Necesitas verificar tu email.'); return; }

    if (uploadBtnEl) { uploadBtnEl.disabled = true; uploadBtnEl.textContent = 'Subiendo…'; }
    const fileName = pendingFile.name;

    const formData = new FormData();
    formData.append('file', pendingFile);
    if (currentChatThreadId) formData.append('thread_id', currentChatThreadId);

    const baseUrl = getOrchestratorBaseUrl();
    try {
      const { signal, cancel } = withTimeout(120000);
      const resp = await fetch(`${baseUrl}/upload_document`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` },
        body: formData,
        signal,
      }).finally(cancel);

      const data = await parseApiResponse(resp);
      if (data.thread_id && !currentChatThreadId) currentChatThreadId = data.thread_id;

      addSystemMessageToChat(
        `✓ ${data.chunks_indexed} fragmentos de "<strong>${escapeHtml(fileName)}</strong>" indexados. Ahora puedes hacer preguntas sobre el documento.`
          .replace(/&lt;strong&gt;/g, '<strong>').replace(/&lt;\/strong&gt;/g, '</strong>')
      );
      // Show upload in messages as a system line with real HTML
      const sysDiv = chatMessagesEl.lastElementChild;
      if (sysDiv?.classList.contains('system-message')) {
        sysDiv.innerHTML =
          `✓ ${data.chunks_indexed} fragmentos de "<strong>${escapeHtml(fileName)}</strong>" indexados. ` +
          `Ahora puedes hacer preguntas sobre el documento.`;
      }

      showActiveDocBadge(fileName);

      // Refresh server-sourced file list (response includes updated files array)
      if (data.files) {
        userFiles = data.files;
        renderFileList();
      }

      clearFilePreview();

      // Enable chat if first interaction
      if (!chatMessagesEl.style.display || chatMessagesEl.style.display === 'none') {
        chatMessagesEl.style.display = 'flex';
        sendButtonEl.disabled = false;
        userInputEl.placeholder = 'Pregunta sobre el documento o sobre normativa...';
      }
    } catch (err) {
      addSystemMessageToChat(`Error al subir el documento: ${err.message}`);
    } finally {
      if (uploadBtnEl) { uploadBtnEl.disabled = false; uploadBtnEl.textContent = 'Subir PDF'; }
    }
  }

  attachFileButtonEl?.addEventListener('click', () => {
    if (!currentUser) { addSystemMessageToChat('Debes iniciar sesión para subir documentos.'); return; }
    fileInputEl.click();
  });
  sendButtonEl?.addEventListener('click', handleSendMessageToServer);
  userInputEl?.addEventListener('keypress', (e)=> {
    if (e.key === 'Enter' && !e.shiftKey && !sendButtonEl.disabled) { e.preventDefault(); handleSendMessageToServer(); }
  });
  chatHomeButtonEl?.addEventListener('click', handleNavigateHomeClick);

  function handleNavigateHomeClick() {
    if (!currentUser) return;
    removeTypingIndicatorFromChat();
    currentChatMode = null;
    currentChatThreadId = null;
    currentConversationMessages = [];
    hideAuditorProgressPanel();
    clearActiveDocBadge();
    advisorFilePanelEl.classList.add('hidden');
    chatMessagesEl.innerHTML = '';
    chatMessagesEl.style.display = 'none';
    inputAreaWrapperEl.style.display = 'none';
    sendButtonEl.disabled = true;
    attachFileButtonEl.disabled = true;
    userInputEl.value = '';
    userInputEl.placeholder = 'Selecciona un modo para comenzar...';
    setHistoryStatusMessage('', false);
    initializeSelectionLayout();
  }
});
