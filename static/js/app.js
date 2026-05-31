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
  // render all 3 lines: first active (spinner), rest waiting
  loaderText.innerHTML = steps.map((s, idx) =>
    `<div class="log-line ${idx === 0 ? 'log-active' : 'log-wait'}"><span class="mk"></span>${s}</div>`
  ).join('');
  let i = 0;
  stepTimer = setInterval(() => {
    const els = loaderText.querySelectorAll('.log-line');
    if (i < els.length) {
      els[i].classList.remove('log-active');
      els[i].classList.add('log-done');
    }
    i++;
    if (i < els.length) {
      els[i].classList.remove('log-wait');
      els[i].classList.add('log-active');
    } else {
      clearInterval(stepTimer);
      // hold the last line spinning until the result lands
      if (els.length) {
        els[els.length - 1].classList.remove('log-done');
        els[els.length - 1].classList.add('log-active');
      }
    }
  }, 2000);
}
function stopLoader() { overlay.classList.add('hidden'); clearInterval(stepTimer); }

// ---- submit ----
const form = document.getElementById('builder');
const go = document.getElementById('go');

form.addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(form);

  // Light client-side guard so we don't post an empty job/CV.
  const hasJD = (fd.get('jd_text') || '').trim() || (fd.get('jd_url') || '').trim();
  const hasCV = (fd.get('cv_text') || '').trim() || (fileInput.files.length > 0);
  if (!hasJD) return toast('Add the job description or a job link first.', true);
  if (!hasCV) return toast('Upload or paste your CV first.', true);

  go.disabled = true; startLoader();
  try {
    const res = await fetch('/api/generate', { method: 'POST', body: fd });
    const data = await res.json();

    if (data.status === 'needs_paste') {
      // Seamless fallback: flip to the paste box, focus it, explain.
      if (data.field === 'jd') { setMode('jd', 'paste'); document.querySelector('[name=jd_text]').focus(); }
      if (data.field === 'cv') { setMode('cv', 'paste'); document.querySelector('[name=cv_text]').focus(); }
      toast(data.message, true);
      return;
    }
    if (data.status !== 'ok') { toast(data.message || 'Something went wrong.', true); return; }

    renderResults(data);
  } catch (err) {
    toast('Network error — is the server running?', true);
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
    (an.dimensions || []).forEach(d => {
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
