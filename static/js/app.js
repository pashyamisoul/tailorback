// ---- popup login (keeps the form filled) ----
window.addEventListener("message", (e) => {
  if (e.origin !== window.location.origin) return;
  if (e.data && e.data.type === "tailorback-login-success") {
    const gate = document.getElementById("authGate");
    const authActions = document.querySelector(".auth-actions");
    const quietMeta = document.querySelector(".quiet-meta");
    if (gate) gate.remove();
    if (authActions) authActions.remove();
    closeModal(authModal);
    if (!document.getElementById("go")) {
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.className = "go";
      submit.id = "go";
      submit.innerHTML = '<span>Generate tailored documents</span><span class="arrow">→</span>';
      form.insertBefore(submit, quietMeta);
      go = submit;
    }
    document.querySelectorAll(".sign-in-pack[data-pack-id]").forEach(link => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "buy-pack";
      btn.dataset.packId = link.dataset.packId;
      btn.textContent = "Buy";
      link.replaceWith(btn);
    });
    const nav = document.querySelector("header.masthead nav");
    if (nav && !nav.querySelector(".account")) {
      const email = e.data.email || "";
      const initial = (email[0] || "A").toUpperCase();
      const remaining = Number.isFinite(e.data.creditsRemaining) ? e.data.creditsRemaining : 0;
      const limit = Number.isFinite(e.data.creditsLimit) ? e.data.creditsLimit : 5;
      const pct = limit ? Math.max(0, Math.min(100, Math.round((remaining / limit) * 100))) : 0;
      const credits = document.createElement("div");
      credits.className = "nav-credits";
      credits.setAttribute("aria-label", "Credits remaining");
      credits.innerHTML = `<strong>${remaining}</strong><span>credits</span>`;
      const account = document.createElement("div");
      account.className = "account";
      account.id = "account";
      account.innerHTML = `
        <button type="button" class="account-btn" id="accountBtn" aria-label="Account menu">
          <span class="avatar">${initial}</span>
          <span class="account-caret">▾</span>
        </button>
        <div class="account-menu" id="accountMenu" hidden>
          <div class="account-head">
            <span class="avatar avatar-lg">${initial}</span>
            <span class="account-email"></span>
          </div>
          <div class="account-credits">
              <div class="credits-row">
              <span>Generations</span>
              <span class="credits-count">${remaining} of ${limit} left</span>
            </div>
            <div class="credits-bar"><div class="credits-fill" style="width: ${pct}%"></div></div>
          </div>
          <button type="button" class="account-action" id="openHistory">Account &amp; settings</button>
          <a class="account-signout" href="/auth/logout">Sign out</a>
        </div>`;
      account.querySelector(".account-email").textContent = email;
      nav.appendChild(credits);
      nav.appendChild(account);
      bindAccountDropdown();
      bindHistoryButton();
    }
  }
});

function updateAccountCredits(remaining, limit) {
  if (!Number.isFinite(remaining) || !Number.isFinite(limit) || limit <= 0) return;
  const count = document.querySelector(".credits-count");
  const fill = document.querySelector(".credits-fill");
  const navCredits = document.querySelector(".nav-credits strong");
  if (count) count.textContent = `${remaining} of ${limit} left`;
  if (navCredits) navCredits.textContent = remaining;
  if (fill) {
    const pct = Math.max(0, Math.min(100, Math.round((remaining / limit) * 100)));
    fill.style.width = `${pct}%`;
  }
}

function startPopupLogin() {
  const w = 480, h = 640;
  const left = window.screenX + (window.outerWidth - w) / 2;
  const top = window.screenY + (window.outerHeight - h) / 2;
  window.open("/auth/google?popup=1", "tailorback_login",
    `width=${w},height=${h},left=${left},top=${top}`);
}

// ---- auth + account modals ----
const authModal = document.getElementById('authModal');
const historyModal = document.getElementById('historyModal');
const signinView = document.getElementById('signinView');
const signupView = document.getElementById('signupView');
const signupForm = document.getElementById('signupForm');
const signupCheck = document.getElementById('signupCheck');
const devActivationLink = document.getElementById('devActivationLink');
const signinError = document.getElementById('signinError');

function openModal(modal) {
  modal?.classList.remove('hidden');
}

function closeModal(modal) {
  modal?.classList.add('hidden');
}

function switchAuthTab(tab) {
  hideSigninError();
  document.querySelectorAll('.auth-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.authTab === tab);
  });
  signinView?.classList.toggle('hidden', tab !== 'signin');
  signupView?.classList.toggle('hidden', tab !== 'signup');
}

function openAuth(tab = 'signin') {
  switchAuthTab(tab);
  openModal(authModal);
}

function hideSigninError() {
  if (!signinError) return;
  signinError.classList.add('hidden');
  signinError.textContent = '';
}

function showSigninError(message, includeSignupLink = false) {
  if (!signinError) return;
  signinError.classList.remove('hidden');
  signinError.innerHTML = '';
  const text = document.createElement('span');
  text.textContent = message;
  signinError.appendChild(text);
  if (includeSignupLink) {
    signinError.appendChild(document.createTextNode(' '));
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Sign up';
    btn.addEventListener('click', () => switchAuthTab('signup'));
    signinError.appendChild(btn);
  }
}

function showSignedInUi(user) {
  updateAccountCredits(user.credits_remaining, user.credits_limit);
  window.location.reload();
}

function showActivationNotice(data) {
  switchAuthTab('signup');
  signupForm?.classList.add('hidden');
  signupCheck?.classList.remove('hidden');
  if (data?.activation_url && devActivationLink) {
    devActivationLink.href = data.activation_url;
    devActivationLink.classList.remove('hidden');
  }
}

document.getElementById('openSignin')?.addEventListener('click', () => openAuth('signin'));
document.getElementById('openSignup')?.addEventListener('click', () => openAuth('signup'));
document.getElementById('navHistory')?.addEventListener('click', () => openHistory());
document.getElementById('navPricing')?.addEventListener('click', () => {
  const proPopover = document.getElementById('proPopover');
  if (proPopover) proPopover.hidden = false;
});
document.querySelectorAll('[data-close-modal]').forEach(btn => {
  btn.addEventListener('click', () => closeModal(btn.closest('.modal')));
});
document.querySelectorAll('.modal').forEach(modal => {
  modal.addEventListener('click', e => {
    if (e.target === modal) closeModal(modal);
  });
});
document.querySelectorAll('[data-auth-tab]').forEach(btn => {
  btn.addEventListener('click', () => switchAuthTab(btn.dataset.authTab));
});
document.querySelectorAll('.sign-in-pack[data-pack-id]').forEach(btn => {
  btn.addEventListener('click', () => {
    const popover = document.getElementById('proPopover');
    if (popover) popover.hidden = true;
    openAuth('signup');
  });
});
document.getElementById('signinForm')?.addEventListener('submit', async e => {
  e.preventDefault();
  hideSigninError();
  const fd = new FormData(e.currentTarget);
  try {
    const res = await fetch('/api/auth/signin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(fd)),
    });
    const data = await res.json();
    if (!res.ok) {
      if (data.status === 'verification_required') showActivationNotice(data);
      if (data.status === 'account_not_found') {
        showSigninError('No user account found for this email. Please', true);
        return;
      }
      showSigninError(data.message || 'Sign in failed.');
      throw new Error(data.message || 'Sign in failed.');
    }
    toast('Signed in.');
    showSignedInUi(data.user);
  } catch (err) {
    if (!signinError || signinError.classList.contains('hidden')) {
      showSigninError(err.message || 'Sign in failed.');
    }
  }
});
signupForm?.addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.currentTarget);
  try {
    const res = await fetch('/api/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(fd)),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Sign up failed.');
    showActivationNotice(data);
    toast(data.message || 'Check your email to activate your account.');
  } catch (err) {
    toast(err.message || 'Sign up failed.', true);
  }
});

const authState = new URLSearchParams(window.location.search).get('auth');
if (authState === 'activated') {
  toast('Account activated. You are signed in.');
  window.history.replaceState({}, document.title, window.location.pathname);
} else if (authState === 'invalid_activation') {
  toast('That activation link is invalid or has already been used.', true);
  window.history.replaceState({}, document.title, window.location.pathname);
}

async function openHistory() {
  openModal(historyModal);
  const profile = document.getElementById('accountProfile');
  const summary = document.getElementById('historySummary');
  const settings = document.getElementById('accountSettings');
  const list = document.getElementById('historyList');
  if (profile) profile.innerHTML = '';
  summary.innerHTML = '<span>Loading account...</span>';
  if (settings) settings.innerHTML = '';
  list.innerHTML = '';
  try {
    const [accountRes, runsRes] = await Promise.all([
      fetch('/api/account'),
      fetch('/api/generations'),
    ]);
    const account = await accountRes.json();
    const runs = await runsRes.json();
    if (!accountRes.ok) throw new Error(account.message || 'Could not load account.');
    const user = account.user;
    if (profile) {
      const initial = (user.email?.[0] || 'T').toUpperCase();
      profile.innerHTML = `
        <div class="account-profile-main">
          <span class="avatar avatar-lg">${initial}</span>
          <div>
            <strong>${escapeHtml(user.full_name || 'TailorBack user')}</strong>
            <span>${escapeHtml(user.email || '')}</span>
          </div>
        </div>
        <div class="account-profile-meta">
          <span>${escapeHtml(user.provider || 'email')}</span>
          <span>${user.email_verified ? 'Verified' : 'Unverified'}</span>
          <span>${escapeHtml(user.current_pack || 'Free')}</span>
        </div>`;
    }
    summary.innerHTML = `
      <div><strong>${user.credits_remaining}</strong><span>Credits left</span></div>
      <div><strong>${user.credits_used}</strong><span>Used</span></div>
      <div><strong>${user.credits_limit}</strong><span>Total limit</span></div>
      <div><strong>${user.paid_credits}</strong><span>Paid credits</span></div>`;
    if (settings) {
      settings.innerHTML = user.has_password ? `
        <details class="settings-card">
          <summary>
            <strong>Change password</strong>
            <span>Update the password for this email account.</span>
          </summary>
          <form class="password-form" id="passwordForm">
            <label>
              <span>Current password</span>
              <input type="password" name="current_password" autocomplete="current-password" required />
            </label>
            <label>
              <span>New password</span>
              <input type="password" name="new_password" autocomplete="new-password" minlength="8" required />
            </label>
            <label>
              <span>Repeat new password</span>
              <input type="password" name="repeat_password" autocomplete="new-password" minlength="8" required />
            </label>
            <button type="submit">Update password</button>
            <p class="settings-message" id="passwordMessage"></p>
          </form>
        </details>` : `
        <div class="settings-card google-managed">
          <strong>Password managed by Google</strong>
          <span>This account signs in with Google, so password changes happen in your Google account.</span>
        </div>`;
      bindPasswordForm();
    }
    const generations = runs.generations || [];
    list.innerHTML = generations.length ? generations.map(run => `
      <article class="history-item">
        <div>
          <strong>${escapeHtml(run.resume_name || 'Tailored documents')}</strong>
          <span>${new Date(run.created_at).toLocaleString()}</span>
        </div>
        <span>${escapeHtml(run.model_provider || 'unknown')} ${run.generation_seconds ? `· ${run.generation_seconds}s` : ''}</span>
        <div class="history-links">
          ${run.downloads.resume_docx_url ? `<a href="${run.downloads.resume_docx_url}">Resume</a>` : ''}
          ${run.downloads.cover_docx_url ? `<a href="${run.downloads.cover_docx_url}">Cover letter</a>` : ''}
        </div>
      </article>`).join('') : '<p>No generations yet.</p>';
  } catch (err) {
    summary.innerHTML = `<p>${err.message || 'Could not load account history.'}</p>`;
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function bindPasswordForm() {
  const form = document.getElementById('passwordForm');
  if (!form || form.dataset.bound === 'true') return;
  form.dataset.bound = 'true';
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const msg = document.getElementById('passwordMessage');
    if (msg) {
      msg.textContent = '';
      msg.className = 'settings-message';
    }
    const fd = new FormData(form);
    try {
      const res = await fetch('/api/account/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.fromEntries(fd)),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'Could not update password.');
      form.reset();
      if (msg) {
        msg.textContent = data.message || 'Password updated.';
        msg.classList.add('ok');
      }
    } catch (err) {
      if (msg) {
        msg.textContent = err.message || 'Could not update password.';
        msg.classList.add('warn');
      }
    }
  });
}
// ---- mode toggles ----
document.querySelectorAll('.toggle').forEach(toggle => {
  const group = toggle.dataset.group;            // "jd" | "cv"
  toggle.querySelectorAll('.seg').forEach(seg => {
    seg.addEventListener('click', () => {
      toggle.querySelectorAll('.seg').forEach(s => s.classList.remove('active'));
      seg.classList.add('active');
      const mode = seg.dataset.mode;
      document.querySelectorAll(`.mode.${group}-paste, .mode.${group}-link, .mode.${group}-upload`)
        .forEach(m => m.classList.add('hidden'));
      document.querySelector(`.mode.${group}-${mode}`).classList.remove('hidden');
      updateReadiness();
    });
  });
});

function setMode(group, mode) {
  const toggle = document.querySelector(`.toggle[data-group="${group}"]`);
  toggle.querySelector(`.seg[data-mode="${mode}"]`)?.click();
}

// ---- dropzone ----
const dropzone = document.getElementById('dropzone');
const fileInput = dropzone.querySelector('input[type=file]');
const dzFile = document.getElementById('dzFile');
const fileState = document.getElementById('fileState');
const clearFile = document.getElementById('clearFile');

['dragover', 'dragenter'].forEach(ev =>
  dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev =>
  dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(); }
});
fileInput.addEventListener('change', showFile);
clearFile?.addEventListener('click', () => {
  fileInput.value = '';
  showFile();
});
function showFile() {
  if (!fileInput.files.length) {
    dzFile.textContent = '';
    fileState?.classList.add('hidden');
    updateReadiness();
    return;
  }
  const f = fileInput.files[0];
  const size = f.size >= 1024 * 1024
    ? `${(f.size / 1024 / 1024).toFixed(1)} MB`
    : `${Math.max(1, Math.round(f.size / 1024))} KB`;
  dzFile.textContent = `${f.name} · ${size}`;
  fileState?.classList.remove('hidden');
  updateReadiness();
}

// ---- readiness ----
const jobReady = document.getElementById('jobReady');
const cvReady = document.getElementById('cvReady');
const jdText = document.querySelector('[name=jd_text]');
const jdUrl = document.querySelector('[name=jd_url]');
const cvText = document.querySelector('[name=cv_text]');

function setReady(el, ready, label) {
  if (!el) return;
  el.classList.toggle('is-ready', ready);
  const text = el.querySelector('span:last-child');
  if (text) text.textContent = label;
}

function updateReadiness() {
  const hasJD = (jdText?.value || '').trim() || (jdUrl?.value || '').trim();
  const hasCV = (cvText?.value || '').trim() || (fileInput?.files?.length > 0);
  setReady(jobReady, Boolean(hasJD), hasJD ? 'Job ready' : 'Job needed');
  setReady(cvReady, Boolean(hasCV), hasCV ? 'CV ready' : 'CV needed');
}

[jdText, jdUrl, cvText].forEach(el => el?.addEventListener('input', updateReadiness));
updateReadiness();

// ---- toast ----
let toastEl;
function toast(msg, warn = false) {
  if (!toastEl) {
    toastEl = document.createElement('div');
    toastEl.className = 'toast';
    toastEl.setAttribute('role', 'status');
    toastEl.innerHTML = '<span class="toast-msg"></span>';
    document.body.appendChild(toastEl);
  }
  toastEl.querySelector('.toast-msg').textContent = msg;
  toastEl.classList.toggle('warn', warn);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(() => toastEl.classList.remove('show'), 3000);
}

// ---- loader ----
const overlay = document.getElementById('overlay');
const loaderText = document.getElementById('loaderText');
const steps = [
  'Parsing the job description',
  'Comparing your CV against the role and calculating the score',
  'Writing your resume & cover letter',
];
let stepTimer;
function startLoader() {
  overlay.classList.remove('hidden');
  // line 0 = parsing, line 1 = comparing/model work, line 2 = writing
  loaderText.innerHTML = steps.map((s, idx) =>
    `<div class="log-line ${idx === 0 ? 'log-active' : 'log-wait'}"><span class="mk"></span>${s}</div>`
  ).join('');
  // Stage 0 holds briefly, then the real stream events drive the rest.
  // A gentle timer only advances line 0 -> 1 so "parsing" doesn't feel stuck
  // before the first backend event arrives.
  let advanced = false;
  stepTimer = setTimeout(() => { if (!advanced) setLoaderLine(1); }, 1500);
}
function setLoaderLine(activeIdx, text) {
  const els = loaderText.querySelectorAll('.log-line');
  els.forEach((el, idx) => {
    el.classList.remove('log-active', 'log-wait', 'log-done');
    if (idx < activeIdx) el.classList.add('log-done');
    else if (idx === activeIdx) el.classList.add('log-active');
    else el.classList.add('log-wait');
  });
  if (text != null && els[activeIdx]) {
    // replace the active line's text, keep its spinner marker
    els[activeIdx].innerHTML = `<span class="mk"></span>${text}`;
  }
}
function updateLoaderStage(stage) {
  clearTimeout(stepTimer);
  if (stage === 'generating_openai') {
    setLoaderLine(2, 'Generating with OpenAI…');
  } else if (stage === 'switching_to_gemini') {
    setLoaderLine(2, 'OpenAI unavailable — switching to Gemini…');
  } else if (stage === 'trying_gemini') {
    setLoaderLine(2, 'Trying Gemini…');
  } else if (stage === 'switching_to_claude') {
    setLoaderLine(2, 'Gemini unavailable — switching to Claude Sonnet…');
  } else if (stage === 'generating_claude') {
    setLoaderLine(2, 'Generating with Claude Sonnet…');
  } else if (stage === 'building_documents') {
    setLoaderLine(2, 'Building your documents…');
  } else if (stage === 'converting_documents') {
    setLoaderLine(2, 'Preparing download formats…');
  }
}
function stopLoader() { overlay.classList.add('hidden'); clearTimeout(stepTimer); }

// ---- submit ----
const form = document.getElementById('builder');
let go = document.getElementById('go');
let currentJobId = null;

const checkoutState = new URLSearchParams(window.location.search).get('checkout');
if (checkoutState === 'success') {
  toast('Payment received. Your credits will appear once Stripe confirms the checkout.');
  window.history.replaceState({}, document.title, window.location.pathname);
} else if (checkoutState === 'cancelled') {
  toast('Checkout cancelled. No credits were purchased.', true);
  window.history.replaceState({}, document.title, window.location.pathname);
}

form.addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(form);
  const hasJD = (fd.get('jd_text') || '').trim() || (fd.get('jd_url') || '').trim();
  const hasCV = (fd.get('cv_text') || '').trim() || (fileInput.files.length > 0);
  if (!hasJD) return toast('Add the job description or a job link first.', true);
  if (!hasCV) return toast('Upload or paste your CV first.', true);
  if (!go) return toast('Sign in before generation.', true);
  go.disabled = true; startLoader();
  try {
    const res = await fetch('/api/generate', { method: 'POST', body: fd });
    const ctype = res.headers.get('content-type') || '';

    // Validation errors come back as normal JSON (not a stream).
    if (ctype.includes('application/json')) {
      const data = await res.json();
      updateAccountCredits(data.credits_remaining, data.credits_limit);
      if (res.status === 401) {
        toast(data.message || 'Your session expired. Please sign in again.', true);
        setTimeout(() => window.location.reload(), 1200);
        return;
      }
      if (data.status === 'needs_paste') {
        if (data.field === 'jd') { setMode('jd', 'paste'); document.querySelector('[name=jd_text]').focus(); }
        if (data.field === 'cv') { setMode('cv', 'paste'); document.querySelector('[name=cv_text]').focus(); }
        toast(data.message, true);
        return;
      }
      toast(data.message || 'Something went wrong.', true);
      return;
    }

    // Otherwise it's a Server-Sent Event stream — read it live.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = false;
    while (!done) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();  // keep the last incomplete chunk
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        let msg;
        try { msg = JSON.parse(line.slice(5).trim()); } catch { continue; }
        if (msg.type === 'status') {
          updateLoaderStage(msg.stage);
        } else if (msg.type === 'error') {
          toast(msg.message || 'Generation failed.', true);
          done = true;
        } else if (msg.type === 'done') {
          renderResults(msg.result);
          done = true;
        }
      }
    }
} catch (err) {
    console.error('Generate error:', err);
    toast('The request failed before generation completed. Please try again.', true);
  } finally {
    go.disabled = false; stopLoader();
  }
});

// ---- results ----
function renderResults(data) {
  updateAccountCredits(data.credits_remaining, data.credits_limit);
  currentJobId = data.job_id || null;
  // Hand the editable document + downloads off to the studio (editor.js).
  if (window.renderStudio) window.renderStudio(data);
  const retention = document.getElementById('retentionNote');
  if (retention) {
    const days = Number.isFinite(data.expires_in_days) ? data.expires_in_days : 7;
    retention.textContent = `Downloads are private to your signed-in account and expire in ${days} days.`;
  }

  // --- Analysis panel ---
  const an = data.analysis || {};
  const panel = document.getElementById('analysisPanel');
  if (an && an.overall_score != null) {
    panel.style.display = '';
    document.getElementById('anScore').textContent = an.overall_score;
    document.getElementById('anVerdict').textContent = an.verdict || '';
    const fill = document.getElementById('anScoreFill');
    fill.style.width = an.overall_score + '%';
    fill.style.background = an.overall_score >= 75 ? 'var(--good)'
                          : an.overall_score >= 50 ? 'var(--warn)' : 'var(--accent)';

    const dims = document.getElementById('anDimensions');
    dims.innerHTML = '';
    const seenDims = new Set();
    (an.dimensions || []).forEach(d => {
      if (seenDims.has(d.name)) return;   // skip duplicate dimension rows
      seenDims.add(d.name);
      const row = document.createElement('div');
      row.className = 'dim-row';
      row.innerHTML =
        '<div class="dim-top"><span class="dim-name">' + d.name +
        '</span><span class="dim-score">' + d.score + '</span></div>' +
        '<div class="dim-bar"><div class="dim-fill" style="width:' + d.score + '%"></div></div>' +
        '<div class="dim-note">' + (d.note || '') + '</div>';
      dims.appendChild(row);
    });

    const fillList = (id, items) => {
      const ul = document.getElementById(id);
      ul.innerHTML = '';
      (items || []).forEach(t => {
        const li = document.createElement('li');
        li.textContent = t;
        ul.appendChild(li);
      });
    };
    fillList('anStrengths', an.strengths);
    fillList('anImprovements', an.improvements);

    const miss = document.getElementById('anMissing');
    miss.innerHTML = '';
    (an.missing_keywords || []).forEach(k => {
      const chip = document.createElement('span');
      chip.textContent = k;
      miss.appendChild(chip);
    });
  } else {
    panel.style.display = 'none';
  }

  const covered = document.getElementById('covered');
  const gaps = document.getElementById('gaps');
  covered.innerHTML = ''; gaps.innerHTML = '';
  (data.match?.covered || []).forEach(t => { const li = document.createElement('li'); li.textContent = t; covered.appendChild(li); });
  const missing = data.match?.missing?.length ? data.match.missing : data.gaps;
  (missing || []).forEach(t => { const li = document.createElement('li'); li.textContent = t; gaps.appendChild(li); });
  if (!gaps.children.length) { const li = document.createElement('li'); li.textContent = 'No major gaps detected.'; gaps.appendChild(li); }

  const results = document.getElementById('results');
  results.classList.remove('hidden');
  resetBuilderInputs();
  results.scrollIntoView({ behavior: 'smooth' });
}

function resetBuilderInputs() {
  form.reset();
  fileInput.value = '';
  showFile();
  setMode('jd', 'paste');
  setMode('cv', 'upload');
  if (go) {
    go.disabled = false;
    go.innerHTML = '<span>Generate tailored documents</span><span class="arrow">→</span>';
  }
  updateReadiness();
}

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.buy-pack[data-pack-id]:not(.sign-in-pack)');
  if (!btn) return;
  const packId = btn.dataset.packId;
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Opening...';
  try {
    const res = await fetch('/api/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pack_id: packId }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok' || !data.checkout_url) {
      throw new Error(data.message || 'Checkout failed');
    }
    window.location.href = data.checkout_url;
  } catch (err) {
    console.error('Checkout failed:', err);
    btn.disabled = false;
    btn.textContent = original;
    toast(err.message || 'Checkout is not available yet.', true);
  }
});

// TailorBack Pro dropdown.
const proTrigger = document.getElementById('proTrigger');
const proPopover = document.getElementById('proPopover');
if (proTrigger && proPopover) {
  proTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    proPopover.hidden = !proPopover.hidden;
  });
  proPopover.addEventListener('click', (e) => e.stopPropagation());
  document.addEventListener('click', () => {
    proPopover.hidden = true;
  });
}
// Download dropdown: open on click, close when clicking elsewhere.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.dl-btn');
  const openMenus = document.querySelectorAll('.dl-menu.open');
  if (btn) {
    const menu = btn.closest('.dl-menu');
    const wasOpen = menu.classList.contains('open');
    openMenus.forEach(m => m.classList.remove('open'));
    if (!wasOpen) menu.classList.add('open');
    e.stopPropagation();
    return;
  }
  // Clicked a download link or anywhere else: close all.
  openMenus.forEach(m => m.classList.remove('open'));
});

// Theme toggle (editorial ↔ terminal), remembered across reloads.
(function () {
  const root = document.documentElement;
  const btn = document.getElementById('themeToggle');
  const saved = localStorage.getItem('tb-theme');
  if (saved) root.setAttribute('data-theme', saved);
  const sync = () => {
    const isTerminal = root.getAttribute('data-theme') === 'terminal';
    btn.textContent = isTerminal ? '◑ editorial mode' : '◐ terminal mode';
  };
  sync();
 btn.addEventListener('click', () => {
    const next = root.getAttribute('data-theme') === 'terminal' ? 'editorial' : 'terminal';
    root.classList.remove('crt-boot', 'paper-boot');
    void root.offsetWidth;            // restart the transition animation
    root.setAttribute('data-theme', next);
    localStorage.setItem('tb-theme', next);
    sync();
    if (next === 'terminal') {
      root.classList.add('crt-boot');
      setTimeout(() => root.classList.remove('crt-boot'), 900);
    } else {
      root.classList.add('paper-boot');
      setTimeout(() => root.classList.remove('paper-boot'), 900);
    }
  });
})();

// ---- account dropdown ----
function bindAccountDropdown() {
  const btn = document.getElementById("accountBtn");
  const menu = document.getElementById("accountMenu");
  if (!btn || !menu || btn.dataset.bound === "true") return;
  btn.dataset.bound = "true";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
  });
}

bindAccountDropdown();
function bindHistoryButton() {
  const btn = document.getElementById("openHistory");
  if (!btn || btn.dataset.bound === "true") return;
  btn.dataset.bound = "true";
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = document.getElementById("accountMenu");
    if (menu) menu.hidden = true;
    openHistory();
  });
}
bindHistoryButton();
document.addEventListener("click", (e) => {
  const menu = document.getElementById("accountMenu");
  const btn = document.getElementById("accountBtn");
  if (menu && btn && !menu.hidden && !menu.contains(e.target) && e.target !== btn) {
    menu.hidden = true;
  }
});
