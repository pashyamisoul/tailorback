// ---- popup login (keeps the form filled) ----
window.addEventListener("message", (e) => {
  if (e.data && e.data.type === "tailorback-login-success") {
    const note = document.querySelector(".signin-note");
    if (note) note.remove();
    const old = document.getElementById("go");
    if (old) {
      old.outerHTML =
        '<button type="submit" class="go" id="go"><span>Generate tailored documents</span><span class="arrow">→</span></button>';
    }
    const nav = document.querySelector("header.masthead nav");
    if (nav && !nav.querySelector(".authlink")) {
      const email = document.createElement("span");
      email.className = "kicker";
      email.textContent = e.data.email || "";
      const out = document.createElement("a");
      out.className = "authlink";
      out.href = "/auth/logout";
      out.textContent = "Sign out";
      nav.appendChild(email);
      nav.appendChild(out);
    }
  }
});

function startPopupLogin() {
  const w = 480, h = 640;
  const left = window.screenX + (window.outerWidth - w) / 2;
  const top = window.screenY + (window.outerHeight - h) / 2;
  window.open("/auth/google?popup=1", "tailorback_login",
    `width=${w},height=${h},left=${left},top=${top}`);
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

['dragover', 'dragenter'].forEach(ev =>
  dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(ev =>
  dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(); }
});
fileInput.addEventListener('change', showFile);
function showFile() {
  dzFile.textContent = fileInput.files.length ? `▣ ${fileInput.files[0].name}` : '';
}

// ---- toast ----
let toastEl;
function toast(msg, warn = false) {
  if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'toast'; document.body.appendChild(toastEl); }
  toastEl.textContent = msg;
  toastEl.classList.toggle('warn', warn);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(() => toastEl.classList.remove('show'), 4200);
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
  if (stage === 'trying_gemini') {
    setLoaderLine(2, 'Trying Gemini…');
  } else if (stage === 'switching_to_claude') {
    setLoaderLine(2, 'Gemini unavailable — switching to Claude Opus…');
  } else if (stage === 'generating_claude') {
    setLoaderLine(2, 'Generating with Claude Opus…');
  }
}
function stopLoader() { overlay.classList.add('hidden'); clearTimeout(stepTimer); }

// ---- submit ----
const form = document.getElementById('builder');
const go = document.getElementById('go');

form.addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(form);
  const hasJD = (fd.get('jd_text') || '').trim() || (fd.get('jd_url') || '').trim();
  const hasCV = (fd.get('cv_text') || '').trim() || (fileInput.files.length > 0);
  if (!hasJD) return toast('Add the job description or a job link first.', true);
  if (!hasCV) return toast('Upload or paste your CV first.', true);
  go.disabled = true; startLoader();
  try {
    const res = await fetch('/api/generate', { method: 'POST', body: fd });
    const ctype = res.headers.get('content-type') || '';

    // Validation errors come back as normal JSON (not a stream).
    if (ctype.includes('application/json')) {
      const data = await res.json();
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
    console.error('Stack:', err && err.stack);
    alert('REAL ERROR: ' + (err && err.message ? err.message : err) + '\n\n' + (err && err.stack ? err.stack : ''));
  } finally {
    go.disabled = false; stopLoader();
  }
});

// ---- results ----
function renderResults(data) {
// Wire up DOCX (always present) and PDF (may be null if LibreOffice missing).
  const setDl = (id, url) => {
    const el = document.getElementById(id);
    if (url) { el.href = url; el.style.display = ''; }
    else { el.style.display = 'none'; }
  };
  setDl('dlResumeDocx', data.resume_docx_url);
  setDl('dlResumePdf', data.resume_pdf_url);
  setDl('dlCoverDocx', data.cover_docx_url);
  setDl('dlCoverPdf', data.cover_pdf_url);

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

  const pv = data.preview || {};
  document.getElementById('pvName').textContent = pv.name || '';
  document.getElementById('pvSummary').textContent = pv.summary || '';
  const skills = document.getElementById('pvSkills');
  skills.innerHTML = '';
  (pv.skills || []).forEach(s => { const sp = document.createElement('span'); sp.textContent = s; skills.appendChild(sp); });

  const results = document.getElementById('results');
  results.classList.remove('hidden');
  results.scrollIntoView({ behavior: 'smooth' });
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
    root.setAttribute('data-theme', next);
    localStorage.setItem('tb-theme', next);
    sync();
    if (next === 'terminal') {
      root.classList.remove('crt-boot');
      void root.offsetWidth;            // restart the animation
      root.classList.add('crt-boot');
      setTimeout(() => root.classList.remove('crt-boot'), 900);
    }
  });
})();

// ---- account dropdown ----
(function () {
  const btn = document.getElementById("accountBtn");
  const menu = document.getElementById("accountMenu");
  if (btn && menu) {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", (e) => {
      if (!menu.hidden && !menu.contains(e.target) && e.target !== btn) {
        menu.hidden = true;
      }
    });
  }
})();
