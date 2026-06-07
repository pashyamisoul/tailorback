// ---- popup login (keeps the form filled) ----
window.addEventListener("message", (e) => {
  if (e.origin !== window.location.origin) return;
  if (e.data && e.data.type === "tailorback-login-success") {
    const gate = document.getElementById("signinGate");
    const authActions = document.querySelector(".auth-actions");
    const anchor = document.querySelector(".qm-cards-wrap");
    if (gate) gate.remove();
    if (authActions) authActions.remove();
    closeModal(authModal);
    // Clear the sign-in form + error so nothing stale lingers behind the modal.
    document.getElementById("signinForm")?.reset();
    document.getElementById("signupForm")?.reset();
    hideSigninError();
    if (!document.getElementById("go")) {
      const submit = document.createElement("button");
      submit.type = "submit";
      submit.className = "go";
      submit.id = "go";
      submit.innerHTML = '<span class="run">Run</span><span class="tb-logo big">Tailor<span class="tb-accent">Back</span></span><span class="arrow">→</span>';
      form.insertBefore(submit, anchor);
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
  // Choosing Google clears any "use Google instead" error from the email form.
  hideSigninError();
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

let _modalLastFocus = null;
function _modalFocusable(modal) {
  return Array.from(modal.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )).filter(el => el.offsetParent !== null);
}
function openModal(modal) {
  if (!modal) return;
  _modalLastFocus = document.activeElement;
  modal.classList.remove('hidden');
  const f = _modalFocusable(modal);
  (f[0] || modal).focus?.();
}

function closeModal(modal) {
  if (!modal) return;
  modal.classList.add('hidden');
  if (_modalLastFocus && typeof _modalLastFocus.focus === 'function') {
    _modalLastFocus.focus();
  }
  _modalLastFocus = null;
}

// Keyboard support for any open modal: Esc closes, Tab is trapped inside.
document.addEventListener('keydown', (e) => {
  const open = Array.from(document.querySelectorAll('.modal:not(.hidden)')).pop();
  if (!open) return;
  if (e.key === 'Escape') { closeModal(open); return; }
  if (e.key === 'Tab') {
    const f = _modalFocusable(open);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});

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
// Logged-out CTA: clicking "Run TailorBack" prompts sign-in.
document.getElementById('signinGate')?.addEventListener('click', () => openAuth('signin'));
// Delete the current generation's documents from the results screen.
document.getElementById('deleteGenerated')?.addEventListener('click', async () => {
  if (!currentJobId) return;
  if (!confirm('Delete these documents from your account? This cannot be undone.')) return;
  try {
    const res = await fetch(`/api/generated/${currentJobId}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Could not delete.');
    document.getElementById('results').classList.add('hidden');
    currentJobId = null;
    toast('Documents deleted.');
  } catch (err) {
    toast(err.message || 'Could not delete the documents.', true);
  }
});
// "See a sample result": render a built-in example so visitors feel the value
// before signing up or uploading their own data (client-side, no credit used).
const SAMPLE_RESULT = {
  status: 'ok', sample: true, job_id: null,
  resume_docx_url: null, resume_pdf_url: null, cover_docx_url: null, cover_pdf_url: null,
  style: { template: 'editorial', accent: 'c8462e', font: 'Calibri', density: 'comfortable' },
  job: { company: 'Northwind', role: 'IT Support Engineer' },
  score_before: 54, score_after: 82,
  expires_in_days: 7,
  resume: {
    name: 'Sample Candidate',
    contact: { email: 'sample@email.com', phone: '+1 555 0100', location: 'Remote', links: ['linkedin.com/in/sample'] },
    summary: 'IT Support Engineer with 5+ years resolving hardware, software, and network issues for 500+ users. Strong in macOS/Windows troubleshooting, identity & access (Okta, SSO, MFA), and endpoint management with Intune.',
    skills: ['macOS & Windows support', 'Okta / SSO / MFA', 'Intune / MDM', 'Jira & Confluence', 'Networking (TCP/IP, VPN)', 'Automation (Bash, Python)'],
    experience: [
      { title: 'IT Support Engineer', company: 'Acme GmbH', dates: '2021 – Present', bullets: [
        'Resolved 2nd-level tickets for 500+ employees, cutting average resolution time 35%.',
        'Automated onboarding/offboarding in Okta, saving ~6 hours per week.' ] },
      { title: 'Helpdesk Technician', company: 'Beta Inc', dates: '2019 – 2021', bullets: [
        'Handled 40+ daily tickets across hardware, software, and access requests.' ] }
    ],
    projects: [
      { name: 'Self-service password reset portal', link: 'github.com/sample/reset', dates: '2023', bullets: [
        'Built an internal portal cutting password-reset tickets by 60%.' ] }
    ],
    education: [{ degree: 'BSc Computer Science', institution: 'State University', dates: '2019' }],
    certifications: ['CompTIA A+', 'Okta Certified Professional']
  },
  cover_letter: { greeting: 'Dear Hiring Manager,', body_paragraphs: [
    'I am excited to apply for the IT Support Engineer role at Northwind. My five years supporting macOS and Windows environments map directly to your needs.',
    'In my current role I cut average ticket resolution time by 35% and automated identity workflows in Okta, experience I would bring to your team.' ], closing: 'Sincerely,' },
  gaps: ['PowerShell scripting (job lists it; not evidenced on the resume)'],
  match: { covered: ['macOS/Windows support', 'Okta / SSO / MFA', 'Endpoint management (Intune)'], missing: ['PowerShell scripting'] },
  analysis: {
    overall_score: 82,
    verdict: 'Strong, well-matched resume after tailoring, with a couple of niche keywords still missing.',
    dimensions: [
      { name: 'Job Match', score: 85, note: '11 of 13 job requirements are clearly evidenced.' },
      { name: 'Keyword Coverage', score: 80, note: '8 of 10 job keywords appear in the resume.' },
      { name: 'Structure & Format', score: 90, note: 'All standard sections present with quantified results.' },
      { name: 'Impact & Quantification', score: 70, note: '7 of 10 bullets include a concrete metric.' }
    ],
    strengths: ['Clear quantified achievements', 'Strong identity/access tooling match'],
    improvements: ['Add PowerShell if you have it', 'Quantify the helpdesk role'],
    missing_keywords: ['PowerShell', 'Active Directory']
  }
};
document.querySelectorAll('.try-sample').forEach(btn => btn.addEventListener('click', () => {
  toast('Sample preview. Sign up to tailor your own résumé.');
  renderResults(SAMPLE_RESULT);
}));

// Reopen a past generation from history into the editor + score view.
document.getElementById('historyList')?.addEventListener('click', async (e) => {
  // expand/collapse the notes + downloads row
  const more = e.target.closest('.ar-more');
  if (more) {
    const extra = more.closest('.app-row')?.querySelector('.ar-extra');
    if (extra) { extra.classList.toggle('hidden'); more.classList.toggle('open'); }
    return;
  }
  const btn = e.target.closest('[data-open-job]');
  if (!btn) return;
  const jobId = btn.dataset.openJob;
  if (!jobId) return;
  const original = btn.textContent;
  btn.disabled = true; btn.textContent = 'Opening…';
  try {
    const res = await fetch(`/api/generation/${jobId}`);
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Could not open.');
    closeModal(historyModal);
    renderResults(data.result);
  } catch (err) {
    toast(err.message || 'Could not open that generation.', true);
  } finally {
    btn.disabled = false; btn.textContent = original;
  }
});
// Application tracker: status change + notes save.
document.getElementById('historyList')?.addEventListener('change', async (e) => {
  const sel = e.target.closest('.hi-status');
  if (!sel) return;
  sel.className = 'hi-status status-' + sel.value;
  try {
    const res = await fetch(`/api/generation/${sel.dataset.job}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: sel.value }),
    });
    if (!res.ok) throw new Error();
    toast('Status updated.');
  } catch { toast('Could not update status.', true); }
});
document.getElementById('historyList')?.addEventListener('blur', async (e) => {
  const inp = e.target.closest('.hi-notes');
  if (!inp || inp.dataset.saved === inp.value) return;
  try {
    const res = await fetch(`/api/generation/${inp.dataset.job}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: inp.value }),
    });
    if (!res.ok) throw new Error();
    inp.dataset.saved = inp.value;
  } catch { toast('Could not save notes.', true); }
}, true);
// Account modal tabs (Applications / Settings).
document.getElementById('acctTabs')?.addEventListener('click', (e) => {
  const btn = e.target.closest('.acct-tab');
  if (!btn) return;
  const tab = btn.dataset.tab;
  document.querySelectorAll('.acct-tab').forEach(t => t.classList.toggle('on', t === btn));
  document.querySelectorAll('.acct-panel').forEach(p => p.classList.toggle('hidden', p.dataset.panel !== tab));
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
    openAuth('signin');
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
  const payload = Object.fromEntries(fd);
  payload.newsletter = e.currentTarget.newsletter.checked;
  payload.agree_terms = e.currentTarget.agree_terms.checked;
  try {
    const res = await fetch('/api/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
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
      const name = user.display_name || user.full_name || 'there';
      const initial = (name[0] || user.email?.[0] || 'T').toUpperCase();
      profile.innerHTML = `
        <div class="acct-id">
          <span class="avatar avatar-lg">${initial}</span>
          <div class="acct-id-text">
            <strong>${escapeHtml(name)}</strong>
            <span>${escapeHtml(user.email || '')}</span>
          </div>
        </div>
        <div class="acct-chips">
          <span class="chip">${escapeHtml((user.provider || 'email').toUpperCase())}</span>
          <span class="chip">${user.email_verified ? 'VERIFIED' : 'UNVERIFIED'}</span>
          <span class="chip">${escapeHtml((user.current_pack || 'Free').toUpperCase())}</span>
        </div>`;
    }
    const limit = Math.max(1, Number(user.credits_limit) || 1);
    const pct = Math.max(0, Math.min(100, Math.round((Number(user.credits_remaining) || 0) / limit * 100)));
    summary.innerHTML = `
      <span class="cred-text"><strong>${user.credits_remaining}</strong> of ${user.credits_limit} credits left</span>
      <span class="cred-bar"><i style="width:${pct}%"></i></span>
      <span class="cred-sub">${user.paid_credits} paid · ${user.free_credits_limit} free</span>`;
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
    const STATUS_OPTIONS = [
      ['not_applied', 'Not applied'], ['applied', 'Applied'],
      ['interviewing', 'Interviewing'], ['offer', 'Offer'], ['rejected', 'Rejected'],
    ];
    const generations = runs.generations || [];
    list.innerHTML = generations.length ? generations.map(run => {
      const job = escapeHtml(run.job_id || '');
      const title = run.company
        ? escapeHtml(run.company) + (run.role ? ' · ' + escapeHtml(run.role) : '')
        : escapeHtml(run.resume_name || 'Tailored documents');
      const status = run.status || 'not_applied';
      const meta = new Date(run.created_at).toLocaleDateString()
        + (run.match_score != null ? ` · match ${run.match_score}` : '');
      const dl = (run.downloads.resume_docx_url ? `<a href="${run.downloads.resume_docx_url}">Resume</a>` : '')
        + (run.downloads.cover_docx_url ? `<a href="${run.downloads.cover_docx_url}">Cover letter</a>` : '');
      return `
      <article class="app-row" data-job="${job}">
        <div class="ar-line">
          <div class="ar-main">
            <strong>${title}</strong>
            <span>${meta}</span>
          </div>
          <div class="ar-actions">
            <select class="hi-status status-${status}" data-job="${job}" aria-label="Application status">
              ${STATUS_OPTIONS.map(([v, l]) => `<option value="${v}" ${status === v ? 'selected' : ''}>${l}</option>`).join('')}
            </select>
            <button type="button" class="history-open" data-open-job="${job}">Open</button>
            <button type="button" class="ar-more" aria-label="Show actions" data-job="${job}">⋯</button>
          </div>
        </div>
        <div class="ar-extra hidden" data-job="${job}">
          <input class="hi-notes" data-job="${job}" placeholder="Notes (e.g. recruiter, follow-up date)…" value="${escapeHtml(run.notes || '')}" />
          <div class="ar-links">${dl || '<span class="ar-nodl">No downloads (expired)</span>'}</div>
        </div>
      </article>`;
    }).join('') : '<p class="lib-empty">No applications yet. Tailor a resume to start tracking.</p>';
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
    setLoaderLine(2, 'OpenAI unavailable, switching to Gemini…');
  } else if (stage === 'trying_gemini') {
    setLoaderLine(2, 'Trying Gemini…');
  } else if (stage === 'switching_to_claude') {
    setLoaderLine(2, 'Gemini unavailable, switching to Claude Sonnet…');
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

    // Otherwise it's a Server-Sent Event stream; read it live.
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
// Phase 9: ATS readiness checklist + keyword match report (deterministic,
// computed from the generated resume + analysis — no extra model calls).
function renderAtsReport(data) {
  const panel = document.getElementById('atsReport');
  if (!panel) return;
  const r = data.resume || {};
  if (!r || (!r.summary && !(r.experience || []).length && !(r.skills || []).length)) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = '';

  // --- keyword match rate ---
  const matched = data.match?.covered || [];
  const missingKw = (data.analysis?.missing_keywords && data.analysis.missing_keywords.length)
    ? data.analysis.missing_keywords
    : (data.match?.missing || []);
  const total = matched.length + missingKw.length;
  const rate = total ? Math.round((matched.length / total) * 100) : 0;
  document.getElementById('kwRate').textContent = total ? rate + '%' : '—';
  const kwBar = document.getElementById('kwBar');
  kwBar.style.width = (total ? rate : 0) + '%';
  kwBar.style.background = rate >= 75 ? 'var(--good)' : rate >= 50 ? 'var(--warn)' : 'var(--accent)';
  const paintChips = (id, items, cls, emptyText) => {
    const box = document.getElementById(id);
    box.innerHTML = '';
    if (!items.length) {
      const s = document.createElement('span'); s.className = 'kw muted'; s.textContent = emptyText; box.appendChild(s); return;
    }
    items.forEach(k => { const s = document.createElement('span'); s.className = 'kw ' + cls; s.textContent = k; box.appendChild(s); });
  };
  paintChips('kwMatched', matched, 'on', '—');
  paintChips('kwMissing', missingKw, 'off', 'None left, great coverage');

  // --- ATS readiness checklist (structural, deterministic) ---
  const exp = Array.isArray(r.experience) ? r.experience : [];
  const allBullets = exp.reduce((acc, e) => acc.concat(e.bullets || []), []);
  const quantified = allBullets.filter(b => /\d/.test(b || '')).length;
  const checks = [
    ['Contact email', !!(r.contact && r.contact.email), 'Add an email so recruiters and ATS can reach you.'],
    ['Contact phone', !!(r.contact && r.contact.phone), 'Add a phone number for completeness.'],
    ['Professional summary', !!(r.summary && r.summary.trim()), 'A short summary helps ATS and recruiters place you fast.'],
    ['Skills section (5+)', (r.skills || []).length >= 5, 'List at least 5 relevant hard skills the ATS can match.'],
    ['Work experience present', exp.length >= 1, 'Add at least one role with bullet points.'],
    ['All roles have dates', exp.length > 0 && exp.every(e => e.dates && String(e.dates).trim()), 'Every role should show start and end dates.'],
    ['Quantified achievements', quantified >= 1, 'Add a number or metric to at least one bullet, e.g. "cut tickets 30%".'],
    ['Education or certifications', (r.education || []).length >= 1 || (r.certifications || []).length >= 1, 'Add your education or relevant certifications.'],
    ['Standard single-column layout', true, 'TailorBack exports clean single-column .docx and PDF that ATS parse reliably.'],
  ];
  const list = document.getElementById('atsChecks');
  list.innerHTML = '';
  let passed = 0;
  checks.forEach(([label, ok, hint]) => {
    if (ok) passed++;
    const li = document.createElement('li');
    li.className = 'ats-item ' + (ok ? 'pass' : 'warn');
    li.innerHTML = '<span class="ats-mark" aria-hidden="true">' + (ok ? '✓' : '!') + '</span>' +
      '<span class="ats-label">' + escapeHtml(label) + '</span>' +
      (ok ? '' : '<span class="ats-hint">' + escapeHtml(hint) + '</span>');
    list.appendChild(li);
  });
  const scoreEl = document.getElementById('atsScore');
  scoreEl.textContent = passed + '/' + checks.length;
  scoreEl.className = 'ats-score ' + (passed === checks.length ? 'all' : passed >= checks.length - 2 ? 'most' : 'some');
}

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

  // --- Before → after match-score lift (ring + thermometer) ---
  const lift = document.getElementById('scoreLift');
  if (lift) {
    const before = Number(data.score_before);
    const after = Number(data.score_after);
    if (Number.isFinite(before) && Number.isFinite(after)) {
      const b = Math.max(0, Math.min(100, before));
      const a = Math.max(0, Math.min(100, after));
      document.getElementById('slAfter').textContent = after;        // ring centre
      document.getElementById('slBefore').textContent = before;       // "Uploaded" mark
      document.getElementById('slAfterMark').textContent = after;     // "Tailored" mark
      document.getElementById('slRing').style.setProperty('--pct', a);
      // baseline (uploaded) fill, then the improvement segment in accent on top
      document.getElementById('slTrackBase').style.width = b + '%';
      const gainBar = document.getElementById('slTrackGain');
      gainBar.style.left = b + '%';
      gainBar.style.width = Math.max(0, a - b) + '%';
      const gain = after - before;
      const gainEl = document.getElementById('slGain');
      gainEl.textContent = gain > 0 ? `▲ +${gain}` : gain < 0 ? `▼ ${gain}` : 'no change';
      gainEl.className = 'sl-gain ' + (gain > 0 ? 'up' : gain < 0 ? 'down' : 'flat');
      lift.classList.remove('hidden');
    } else {
      lift.classList.add('hidden');
    }
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

    const DIM_HELP = {
      'Job Match': "How many of the job's must-have responsibilities your resume clearly evidences.",
      'Keyword Coverage': "Share of the job's important keywords and hard skills that appear in your resume.",
      'Structure & Format': 'Whether standard sections, complete contact info, and quantified results are present and ATS-friendly.',
      'Impact & Quantification': 'How many of your experience bullets include a concrete number or measurable result.'
    };
    const dims = document.getElementById('anDimensions');
    dims.innerHTML = '';
    const seenDims = new Set();
    (an.dimensions || []).forEach(d => {
      if (seenDims.has(d.name)) return;   // skip duplicate dimension rows
      seenDims.add(d.name);
      const row = document.createElement('div');
      row.className = 'dim-row';
      // d.name / d.note are model output; escape to avoid HTML injection.
      const score = Math.max(0, Math.min(100, Number(d.score) || 0));
      const help = DIM_HELP[d.name] || d.note || '';
      row.innerHTML =
        '<div class="dim-top"><span class="dim-name">' + escapeHtml(d.name) +
        (help ? ' <span class="dim-info" tabindex="0" title="' + escapeHtml(help) + '" aria-label="' + escapeHtml(help) + '">i</span>' : '') +
        '</span><span class="dim-score">' + score + '</span></div>' +
        '<div class="dim-bar"><div class="dim-fill" style="width:' + score + '%"></div></div>' +
        '<div class="dim-note">' + escapeHtml(d.note || '') + '</div>';
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

  renderAtsReport(data);

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
    go.innerHTML = '<span class="run">Run</span><span class="tb-logo big">Tailor<span class="tb-accent">Back</span></span><span class="arrow">→</span>';
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

// ---- Phase 7: feedback & reviews ----
(function () {
  const modal = document.getElementById('feedbackModal');
  if (!modal) return;
  const isSignedIn = () => !!document.querySelector('.account');
  const errEl = document.getElementById('fbError');
  let chosen = 0; // rating bound to the modal

  function hideErr() { errEl.classList.add('hidden'); errEl.textContent = ''; }
  function showErr(m) { errEl.textContent = m; errEl.classList.remove('hidden'); }

  function paint(group, val) {
    group.querySelectorAll('.fb-star').forEach(s => {
      s.classList.toggle('on', Number(s.dataset.val) <= val);
    });
  }
  // Wire a star group: hover preview, click to pick. onPick(value) fires on click.
  function wireStars(group, onPick) {
    if (!group) return null;
    let current = 0;
    group.querySelectorAll('.fb-star').forEach(s => {
      s.addEventListener('mouseenter', () => paint(group, Number(s.dataset.val)));
      s.addEventListener('click', () => {
        current = Number(s.dataset.val);
        paint(group, current);
        onPick(current);
      });
    });
    group.addEventListener('mouseleave', () => paint(group, current));
    group.setCurrent = (v) => { current = v; paint(group, v); };
    return group;
  }

  const modalStars = wireStars(document.getElementById('fbModalStars'), v => { chosen = v; hideErr(); });

  function openFeedback(initialRating) {
    if (!isSignedIn()) { openAuth('signin'); return; }
    chosen = initialRating || 0;
    modalStars.setCurrent(chosen);
    document.getElementById('fbComment').value = '';
    document.getElementById('fbConsent').checked = false;
    document.getElementById('fbPublishFields').classList.add('hidden');
    document.getElementById('fbName').value = '';
    document.getElementById('fbRole').value = '';
    hideErr();
    openModal(modal);
  }

  wireStars(document.getElementById('fbBandStars'), v => openFeedback(v));
  wireStars(document.getElementById('fbResultStars'), v => openFeedback(v));
  document.getElementById('openFeedback')?.addEventListener('click', () => openFeedback(0));

  document.getElementById('fbConsent')?.addEventListener('change', e => {
    document.getElementById('fbPublishFields').classList.toggle('hidden', !e.target.checked);
  });

  document.getElementById('feedbackForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (chosen < 1) { showErr('Please choose a rating from 1 to 5.'); return; }
    const consent = document.getElementById('fbConsent').checked;
    const payload = {
      rating: chosen,
      comment: document.getElementById('fbComment').value.trim(),
      consent_to_publish: consent,
      display_name: consent ? document.getElementById('fbName').value.trim() : '',
      role: consent ? document.getElementById('fbRole').value.trim() : '',
    };
    const btn = document.getElementById('fbSubmit');
    btn.disabled = true;
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || 'Could not send feedback.');
      closeModal(modal);
      window.tbHasFeedback = true;
      toast(data.published
        ? 'Thanks! With your okay, your review may appear on our homepage.'
        : (data.updated ? 'Thanks, your feedback was updated.' : 'Thanks for your feedback!'));
      const rf = document.getElementById('resultFeedback');
      if (rf) { rf.classList.add('is-done'); rf.textContent = '✓ Thanks for the feedback!'; }
    } catch (err) {
      showErr(err.message || 'Could not send feedback.');
    } finally {
      btn.disabled = false;
    }
  });

  // Expose for the editor's download trigger: prompt once per session, and
  // never if the user already left a review (one review per account).
  let promptedThisSession = false;
  window.openFeedback = openFeedback;
  window.tbMaybePromptFeedback = function () {
    if (promptedThisSession || window.tbHasFeedback || !isSignedIn()) return;
    promptedThisSession = true;
    setTimeout(() => openFeedback(0), 700);
  };

  // Sync the top-right account dropdown with the LIVE session: the
  // server-rendered copy can be stale after switching accounts, which showed
  // a previous user's email. Also read whether this user has already reviewed.
  if (isSignedIn()) {
    fetch('/api/account').then(r => (r.ok ? r.json() : null)).then(d => {
      if (!d || !d.user) return;
      window.tbHasFeedback = !!d.user.has_feedback;
      const email = d.user.email || '';
      const initial = (email[0] || 'A').toUpperCase();
      document.querySelectorAll('.account-email').forEach(el => { el.textContent = email; });
      document.querySelectorAll('.account .avatar').forEach(el => { el.textContent = initial; });
    }).catch(() => {});
  }
})();

// ---- Phase 11: interview prep ----
document.getElementById('openInterview')?.addEventListener('click', async () => {
  if (!currentJobId) { toast('Generate a résumé first to get interview prep.'); return; }
  const modal = document.getElementById('interviewModal');
  const body = document.getElementById('interviewBody');
  body.innerHTML = '<p class="iv-loading">Preparing questions…</p>';
  openModal(modal);
  try {
    const res = await fetch('/api/interview-prep', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: currentJobId }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== 'ok') throw new Error(data.message || 'Could not prepare questions.');
    if (!data.questions.length) { body.innerHTML = '<p class="iv-loading">No questions came back. Try again.</p>'; return; }
    const catLabel = { technical: 'Technical', behavioral: 'Behavioral', 'role-specific': 'Role-specific', gap: 'Gap / risk' };
    body.innerHTML = data.questions.map((q, i) =>
      '<div class="iv-q">' +
        '<div class="iv-q-top"><span class="iv-cat iv-' + q.category + '">' + (catLabel[q.category] || q.category) + '</span><span class="iv-n">Q' + (i + 1) + '</span></div>' +
        '<div class="iv-question">' + escapeHtml(q.question) + '</div>' +
        (q.why ? '<div class="iv-why"><strong>Why they ask:</strong> ' + escapeHtml(q.why) + '</div>' : '') +
        (q.tip ? '<div class="iv-tip"><strong>How to answer:</strong> ' + escapeHtml(q.tip) + '</div>' : '') +
      '</div>').join('');
  } catch (e) {
    body.innerHTML = '<p class="iv-loading err">' + escapeHtml(e.message || 'Could not prepare questions.') + '</p>';
  }
});
