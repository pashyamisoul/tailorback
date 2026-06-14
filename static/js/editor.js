/* ============================================================================
 * TailorBack Studio: live document editor
 *   - Renders the generated resume + cover letter as an editable "paper" preview
 *   - Inline editing (contenteditable) bound back to a JS model
 *   - Template / accent / font / density gallery
 *   - Per-section AI regenerate (summary, skills, bullets, cover letter)
 *   - "Apply & download" re-exports the edited content via /api/export
 * ==========================================================================*/
(function () {
  "use strict";

  const esc = window.escapeHtml || (s => String(s ?? ""));
  const notify = window.toast || ((m) => console.log(m));

  const TEMPLATES = [
    { id: "editorial", name: "Editorial", hint: "Centered · classic" },
    { id: "classic", name: "Classic", hint: "Serif · refined" },
    { id: "serif", name: "Serif executive", hint: "Centered serif · small-caps" },
    { id: "minimal", name: "Minimalist", hint: "Airy · hairline rules" },
    { id: "sidebar", name: "Sidebar", hint: "Two-column · skills left" },
    { id: "skyline", name: "Skyline", hint: "Timeline · teal · monogram" },
    { id: "executive", name: "Executive", hint: "Dark sidebar · gold" },
    { id: "aurora", name: "Aurora", hint: "Teal sidebar · monogram" },
    { id: "spotlight", name: "Spotlight", hint: "Colour header band" },
  ];
  // Rich templates carry a curated colour (used unless the user picks a swatch).
  const TEMPLATE_ACCENT = { skyline: "3d8b7d", executive: "b8893f", aurora: "2f8f7d", spotlight: "5f8d6e" };
  const ACCENTS = ["c8462e", "1f6feb", "0f766e", "7c3aed", "be123c", "b45309", "0369a1", "111827"];
  const FONTS = ["Calibri", "Georgia", "Arial", "Garamond", "Helvetica", "Times New Roman"];
  const FONT_STACK = {
    "Calibri": '"Segoe UI", Calibri, system-ui, sans-serif',
    "Georgia": 'Georgia, "Times New Roman", serif',
    "Arial": "Arial, Helvetica, sans-serif",
    "Garamond": 'Garamond, "Apple Garamond", Georgia, serif',
    "Helvetica": "Helvetica, Arial, sans-serif",
    "Times New Roman": '"Times New Roman", Times, serif',
  };
  const TONES = [
    { id: "", label: "Default" },
    { id: "confident", label: "Confident" },
    { id: "formal", label: "Formal" },
    { id: "concise", label: "Concise" },
    { id: "friendly", label: "Friendly" },
  ];

  const ST = {
    jobId: null,
    resume: {},
    cover: {},
    style: { template: "editorial", accent: "c8462e", font: "Calibri", density: "comfortable" },
    job: {},
    activeDoc: "resume",
    dirty: false,           // edits/style changed since last export
    exported: false,        // a fresh export with current state exists
    urls: {},               // resume_docx_url, resume_pdf_url, cover_docx_url, cover_pdf_url
  };

  // ---- tiny helpers --------------------------------------------------------
  function activeRoot() { return ST.activeDoc === "resume" ? ST.resume : ST.cover; }

  function getByPath(root, path) {
    return path.split(".").reduce((o, k) => (o == null ? o : o[k]), root);
  }
  function setByPath(root, path, val) {
    const ks = path.split(".");
    let o = root;
    for (let i = 0; i < ks.length - 1; i++) {
      if (o[ks[i]] == null) o[ks[i]] = isNaN(ks[i + 1]) ? {} : [];
      o = o[ks[i]];
    }
    o[ks[ks.length - 1]] = val;
  }

  // Swap an array item with its neighbour (dir -1 = up, +1 = down).
  function move(arr, idx, dir) {
    if (!Array.isArray(arr)) return;
    const to = idx + dir;
    if (to < 0 || to >= arr.length) return;
    const tmp = arr[idx]; arr[idx] = arr[to]; arr[to] = tmp;
  }

  function markDirty() {
    ST.dirty = true;
    ST.exported = false;
    setSaveState("Unsaved changes");
    scheduleRescore();
  }

  // ---- Phase 12: live re-scoring ------------------------------------------
  let _rescoreTimer = null;
  function scheduleRescore() {
    if (!ST.jobId) return;            // sample / no stored job: skip live scoring
    clearTimeout(_rescoreTimer);
    _rescoreTimer = setTimeout(runRescore, 1100);
  }
  async function runRescore() {
    if (!ST.jobId) return;
    try {
      const res = await fetch("/api/rescore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: ST.jobId, resume: ST.resume }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "ok") return;
      setScore(data.overall_score);
      if (Array.isArray(data.missing_keywords)) {
        ST.analysis = ST.analysis || {};
        ST.analysis.missing_keywords = data.missing_keywords;
        renderMissingKeywords();
      }
    } catch (e) { /* silent: scoring is best-effort */ }
  }
  function setScore(score) {
    const pill = document.getElementById("scorePill");
    if (!pill || score == null) return;
    if (ST._baseScore == null) ST._baseScore = score;
    const delta = score - ST._baseScore;
    pill.hidden = false;
    pill.innerHTML = 'Match <strong>' + score + '</strong>' +
      (delta ? ' <span class="score-delta ' + (delta > 0 ? 'up' : 'down') + '">' +
        (delta > 0 ? '▲+' + delta : '▼' + delta) + '</span>' : '');
  }
  function setSaveState(text, ok) {
    const el = document.getElementById("saveState");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-ok", !!ok);
  }

  // ---- public entry point --------------------------------------------------
  window.renderStudio = function (data) {
    ST.jobId = data.job_id || null;
    ST.resume = data.resume || {};
    ST.cover = data.cover_letter || {};
    ST.job = data.job || {};
    ST.analysis = data.analysis || {};
    ST.style = Object.assign(
      { template: "editorial", accent: "c8462e", font: "Calibri", density: "comfortable" },
      data.style || {});
    ST.activeDoc = "resume";
    ST.dirty = false;
    ST.exported = true; // generation already produced a matching file set
    ST.urls = {
      resume_docx_url: data.resume_docx_url,
      resume_pdf_url: data.resume_pdf_url,
      cover_docx_url: data.cover_docx_url,
      cover_pdf_url: data.cover_pdf_url,
    };
    ST._baseScore = null;
    buildShell();
    renderStage();
    setSaveState("");
    runRescore();   // initial live match score (no-op for the sample)
  };

  // ---- shell (tabs, toolbar, sidebar) -------------------------------------
  function buildShell() {
    const root = document.getElementById("studio");
    if (!root) return;
    root.innerHTML = `
      <div class="studio-bar">
        <div class="doc-tabs" role="tablist">
          <button class="doc-tab active" data-doc="resume" role="tab">Resume</button>
          <button class="doc-tab" data-doc="cover" role="tab">Cover letter</button>
        </div>
        <div class="studio-bar-right">
          <span class="score-pill" id="scorePill" hidden title="Live match score, updates as you edit"></span>
          <span class="save-state" id="saveState" role="status"></span>
          <div class="sdl-menu" id="sdlMenu">
            <button type="button" class="sdl-btn" id="sdlBtn">Download <span class="sdl-caret">▾</span></button>
            <div class="sdl-options" id="sdlOptions">
              <button type="button" data-fmt="pdf">PDF (.pdf)</button>
              <button type="button" data-fmt="docx">Word (.docx)</button>
              <button type="button" data-fmt="txt">Plain text (.txt)</button>
            </div>
          </div>
        </div>
      </div>
      <div class="studio-body">
        <aside class="studio-side" id="studioSide"></aside>
        <div class="doc-stage"><div class="doc-scroll" id="docScroll"></div></div>
      </div>`;

    root.querySelectorAll(".doc-tab").forEach(tab => {
      tab.addEventListener("click", () => {
        if (tab.dataset.doc === ST.activeDoc) return;
        ST.activeDoc = tab.dataset.doc;
        root.querySelectorAll(".doc-tab").forEach(t =>
          t.classList.toggle("active", t.dataset.doc === ST.activeDoc));
        renderStage();
      });
    });

    buildSidebar();
    bindDownloadMenu();
  }

  function buildSidebar() {
    const side = document.getElementById("studioSide");
    if (!side) return;
    side.innerHTML = `
      <div class="side-group">
        <h4>Template</h4>
        <div class="tpl-grid" id="tplGrid">
          ${TEMPLATES.map(t => `
            <button type="button" class="tpl-chip ${t.id === ST.style.template ? "active" : ""}" data-tpl="${t.id}">
              <span class="tpl-mini tpl-mini-${t.id}"><i></i><i></i><i></i></span>
              <span class="tpl-name">${t.name}</span>
              <span class="tpl-hint">${t.hint}</span>
            </button>`).join("")}
        </div>
      </div>
      <div class="side-group">
        <h4>Accent</h4>
        <div class="swatches" id="swatches">
          ${ACCENTS.map(c => `<button type="button" class="swatch ${c === ST.style.accent ? "active" : ""}" data-accent="${c}" style="background:#${c}" aria-label="#${c}"></button>`).join("")}
          <label class="swatch-custom" title="Custom colour">
            <input type="color" id="accentCustom" value="#${ST.style.accent}" />
          </label>
        </div>
      </div>
      <div class="side-group">
        <h4>Font</h4>
        <select id="fontSelect" class="side-select">
          ${FONTS.map(f => `<option value="${f}" ${f === ST.style.font ? "selected" : ""}>${f}</option>`).join("")}
        </select>
      </div>
      <div class="side-group">
        <h4>Density</h4>
        <div class="density-toggle" id="densityToggle">
          <button type="button" data-density="comfortable" class="${ST.style.density === "comfortable" ? "active" : ""}">Comfortable</button>
          <button type="button" data-density="compact" class="${ST.style.density === "compact" ? "active" : ""}">Compact</button>
        </div>
        <p class="side-hint">Compact fits more on a page. Use it to keep a long resume to one page.</p>
      </div>
      <div class="side-group" id="wqGroup">
        <h4>Writing &amp; keywords</h4>
        <button type="button" class="wq-btn" id="wqCheck">Check writing</button>
        <div class="wq-issues" id="wqIssues"></div>
        <div class="wq-kw-wrap" id="wqKwWrap"></div>
      </div>
      <p class="side-note">Edits and style apply to your download. Click any text in the page to edit it. Use ↑ ↓ to reorder bullets and roles.</p>`;

    side.querySelector("#tplGrid").addEventListener("click", e => {
      const b = e.target.closest("[data-tpl]"); if (!b) return;
      const prev = ST.style.template;
      ST.style.template = b.dataset.tpl;
      side.querySelectorAll(".tpl-chip").forEach(c => c.classList.toggle("active", c === b));
      // The sidebar template has a different DOM structure (two columns), so a
      // full re-render is needed when entering or leaving it; others just restyle.
      if (ST.style.template === "sidebar" || prev === "sidebar") renderStage();
      else applyStageStyle();
      markDirty();
    });
    side.querySelector("#swatches").addEventListener("click", e => {
      const b = e.target.closest("[data-accent]"); if (!b) return;
      setAccent(b.dataset.accent);
    });
    side.querySelector("#accentCustom").addEventListener("input", e => {
      setAccent(e.target.value.replace("#", ""));
    });
    side.querySelector("#fontSelect").addEventListener("change", e => {
      ST.style.font = e.target.value; applyStageStyle(); markDirty();
    });
    side.querySelector("#densityToggle").addEventListener("click", e => {
      const b = e.target.closest("[data-density]"); if (!b) return;
      ST.style.density = b.dataset.density;
      side.querySelectorAll("#densityToggle button").forEach(x => x.classList.toggle("active", x === b));
      applyStageStyle(); markDirty();
    });
    bindWritingTools();
  }

  // ---- Phase 9: writing check + keyword-insert ----------------------------
  function bindWritingTools() {
    const checkBtn = document.getElementById("wqCheck");
    const issuesBox = document.getElementById("wqIssues");
    if (checkBtn && issuesBox) {
      checkBtn.addEventListener("click", async () => {
        checkBtn.disabled = true;
        issuesBox.innerHTML = '<p class="wq-status">Checking…</p>';
        try {
          const res = await fetch("/api/writing-check", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: buildResumeText(ST.resume || {}) }),
          });
          const data = await res.json();
          if (!res.ok || data.status !== "ok") throw new Error(data.message || "Check failed.");
          if (!data.issues.length) {
            issuesBox.innerHTML = '<p class="wq-status ok">No writing issues found.</p>';
            return;
          }
          issuesBox.innerHTML = data.issues.map(it =>
            '<div class="wq-issue sev-' + esc(it.severity) + '">' +
              '<div class="wq-issue-h">' + esc(it.problem) + '</div>' +
              (it.excerpt ? '<div class="wq-excerpt">"' + esc(it.excerpt) + '"</div>' : '') +
              '<div class="wq-fix">' + esc(it.suggestion) + '</div>' +
            '</div>').join("");
        } catch (e) {
          issuesBox.innerHTML = '<p class="wq-status err">' + esc(e.message || "Could not check.") + '</p>';
        } finally {
          checkBtn.disabled = false;
        }
      });
    }

    renderMissingKeywords();
  }

  // Missing keywords -> click to weave one into the summary, truthfully.
  function renderMissingKeywords() {
    const kwWrap = document.getElementById("wqKwWrap");
    if (!kwWrap) return;
    const missing = ((ST.analysis && ST.analysis.missing_keywords) || []).slice(0, 12);
    if (!missing.length) {
      kwWrap.innerHTML = '<h5 class="wq-kw-title">Missing keywords</h5><p class="wq-kw-help">None left, great keyword coverage.</p>';
      return;
    }
    {
      kwWrap.innerHTML = '<h5 class="wq-kw-title">Missing keywords</h5>' +
        '<p class="wq-kw-help">Click one to weave it into your summary, only if your experience supports it.</p>' +
        '<div class="wq-kw">' + missing.map(k =>
          '<button type="button" class="wq-kw-chip" data-kw="' + esc(k) + '">' + esc(k) + '</button>').join("") + '</div>';
      kwWrap.querySelector(".wq-kw").addEventListener("click", async (e) => {
        const b = e.target.closest("[data-kw]"); if (!b) return;
        const kw = b.dataset.kw;
        b.disabled = true; b.classList.add("loading");
        try {
          const res = await fetch("/api/refine", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              kind: "summary",
              content: ST.resume.summary || "",
              instruction: "Naturally incorporate the keyword \"" + kw + "\" ONLY if the candidate's existing experience genuinely supports it. If it is not supported by the current content, return the summary unchanged. Never fabricate.",
              context: { role: ST.job.role || ST.job.title || null, company: ST.job.company || null },
            }),
          });
          const data = await res.json();
          if (!res.ok || data.status !== "ok") throw new Error(data.message || "Could not update.");
          const changed = (data.content || "") !== (ST.resume.summary || "");
          ST.resume.summary = data.content || ST.resume.summary;
          markDirty(); renderStage();
          notify(changed ? '"' + kw + '" woven into your summary.' : '"' + kw + '" was not added (your experience does not evidence it).', !changed);
          if (changed) b.classList.add("done"); else b.classList.add("skipped");
        } catch (err) {
          notify(err.message || "Could not update.", true);
        } finally {
          b.disabled = false; b.classList.remove("loading");
        }
      });
    }
  }

  function setAccent(hex) {
    hex = (hex || "").replace("#", "").toLowerCase();
    if (!/^[0-9a-f]{6}$/.test(hex)) return;
    ST.style.accent = hex;
    document.querySelectorAll("#swatches .swatch[data-accent]").forEach(s =>
      s.classList.toggle("active", s.dataset.accent === hex));
    applyStageStyle(); markDirty();
  }

  // ---- stage (the editable page) ------------------------------------------
  function renderStage() {
    const scroll = document.getElementById("docScroll");
    if (!scroll) return;
    scroll.innerHTML = ST.activeDoc === "resume" ? resumeHtml(ST.resume) : coverHtml(ST.cover);
    applyStageStyle();
    bindStage(scroll);
  }

  function applyStageStyle() {
    const page = document.querySelector("#docScroll .doc-page");
    if (!page) return;
    const coverCls = ST.activeDoc === "cover" ? " cover" : "";
    page.className = `doc-page tmpl-${ST.style.template} density-${ST.style.density}${coverCls}`;
    const acc = (TEMPLATE_ACCENT[ST.style.template] && ST.style.accent === "c8462e")
      ? TEMPLATE_ACCENT[ST.style.template] : ST.style.accent;
    page.style.setProperty("--doc-accent", `#${acc}`);
    page.style.setProperty("--doc-font", FONT_STACK[ST.style.font] || FONT_STACK.Calibri);
  }

  function ed(path, value, cls, tag) {
    tag = tag || "span";
    const ph = value ? "" : ' data-empty="true"';
    return `<${tag} class="ed ${cls || ""}" contenteditable="true" data-bind="${path}"${ph}>${esc(value || "")}</${tag}>`;
  }

  function sectionHead(title, refineBtn) {
    return `<div class="doc-head"><span class="doc-head-t">${esc(title)}</span>${refineBtn || ""}</div>`;
  }
  function refineBtn(label, kind, ref) {
    return `<button type="button" class="refine-btn" data-refine="${kind}" data-ref="${ref || ""}">✦ ${esc(label)}</button>`;
  }

  function skillGroups(skills) {
    // Normalise to [{category, items:[...]}] for grouped editing; accepts the
    // grouped shape, a {category: [items]} dict, or a legacy flat string list.
    if (!skills) return [];
    if (!Array.isArray(skills)) {
      if (typeof skills === "object") {
        return Object.entries(skills).map(([category, items]) =>
          ({ category: category || "", items: Array.isArray(items) ? items : [] }));
      }
      return [];
    }
    if (skills.length && typeof skills[0] === "object" && skills[0] !== null) {
      return skills.map(g => ({ category: (g && g.category) || "",
        items: Array.isArray(g && g.items) ? g.items : [] }));
    }
    return [{ category: "", items: skills.filter(s => typeof s === "string") }];
  }

  function resumeHtml(r) {
    r = r || {};
    const c = r.contact || {};
    const links = (c.links || []);
    const skGroups = (r.skills = skillGroups(r.skills));
    const exp = (r.experience || []);
    const projects = (r.projects || []);
    const edu = (r.education || []);
    const certs = (r.certifications || []);

    const contactParts = [
      ed("contact.email", c.email, "c-item"),
      ed("contact.phone", c.phone, "c-item"),
      ed("contact.location", c.location, "c-item"),
      ...links.map((l, i) => `<span class="c-link">${ed("contact.links." + i, l, "c-item")}<button class="mini-x" data-act="del-link" data-i="${i}" title="Remove">×</button></span>`),
    ].join('<span class="c-dot">•</span>');

    const langs = (r.languages = (Array.isArray(r.languages) ? r.languages : []));

    // Build each section as a fragment so layouts can arrange them differently.
    const nameEl = ed("name", r.name, "doc-name", "h1");
    const headlineEl = `<div class="doc-headline-wrap">${ed("headline", r.headline, "doc-headline", "div")}</div>`;
    const langsSec = `<section class="doc-sec">
        ${sectionHead("Languages")}
        <div class="doc-langs" data-list="languages">
          ${langs.map((l, i) => `<span class="lang-chip">${ed("languages." + i + ".name", (l && l.name) || "", "")}<span class="lang-lvl">${ed("languages." + i + ".level", (l && l.level) || "", "")}</span><button class="mini-x" data-act="del-lang" data-i="${i}" title="Remove">×</button></span>`).join("")}
          <button class="mini-add chip-add" data-act="add-lang" title="Add language">+</button>
        </div>
      </section>`;
    const contactEl = `<div class="doc-contact">${contactParts}
        <button class="mini-add" data-act="add-link" title="Add link">+ link</button>
      </div>`;
    const summarySec = `<section class="doc-sec">
        ${sectionHead("Professional Summary", refineBtn("Rewrite", "summary"))}
        ${ed("summary", r.summary, "doc-summary", "p")}
      </section>`;
    const skillsSec = `<section class="doc-sec">
        ${sectionHead("Skills", refineBtn("Refine", "skills"))}
        <div data-list="skills">
          ${skGroups.map((g, gi) => skillGroupHtml(g, gi)).join("")}
          <button class="mini-add row-add" data-act="add-skill-group">+ Add category</button>
        </div>
      </section>`;
    const expSec = `<section class="doc-sec">
        ${sectionHead("Professional Experience")}
        <div data-list="experience">
          ${exp.map((j, i) => jobHtml(j, i, exp.length)).join("")}
        </div>
        <button class="mini-add row-add" data-act="add-job">+ Add role</button>
      </section>`;
    const projSec = `<section class="doc-sec">
        ${sectionHead("Projects")}
        <div data-list="projects">
          ${projects.map((p, i) => projectHtml(p, i, projects.length)).join("")}
        </div>
        <button class="mini-add row-add" data-act="add-project">+ Add project</button>
      </section>`;
    const eduSec = `<section class="doc-sec">
        ${sectionHead("Education")}
        <div data-list="education">
          ${edu.map((e, i) => eduHtml(e, i)).join("")}
        </div>
        <button class="mini-add row-add" data-act="add-edu">+ Add education</button>
      </section>`;
    const certsSec = certs.length ? `<section class="doc-sec">
        ${sectionHead("Certifications")}
        <ul class="doc-certs" data-list="certifications">
          ${certs.map((ct, i) => `<li>${ed("certifications." + i, ct, "")}<button class="mini-x" data-act="del-cert" data-i="${i}">×</button></li>`).join("")}
        </ul>
      </section>` : "";

    const cls = `doc-page tmpl-${ST.style.template} density-${ST.style.density}`;
    const TWO_COL = ["sidebar", "executive", "aurora"];

    if (TWO_COL.includes(ST.style.template)) {
      // Two columns: contact/skills/education/languages left, the rest right.
      return `
      <div class="${cls}">
        <div class="doc-side">${nameEl}${headlineEl}${contactEl}${skillsSec}${eduSec}${langsSec}${certsSec}</div>
        <div class="doc-main">${summarySec}${expSec}${projSec}</div>
      </div>`;
    }

    // Single column (incl. skyline timeline + spotlight band — themed via CSS).
    return `
    <div class="${cls}">
      <header class="doc-masthead">${nameEl}${headlineEl}${contactEl}</header>
      ${summarySec}
      ${skillsSec}
      ${expSec}
      ${projSec}
      ${eduSec}
      ${certsSec}
      ${langsSec}
    </div>`;
  }

  function skillGroupHtml(g, gi) {
    const items = (g.items || []);
    return `<div class="doc-skill-group" data-skgroup="${gi}">
      <div class="skgroup-head">
        ${ed("skills." + gi + ".category", g.category, "skgroup-cat")}
        <button class="mini-x" data-act="del-skill-group" data-i="${gi}" title="Remove category">×</button>
      </div>
      <div class="doc-skills">
        ${items.map((s, si) => `<span class="skill-chip">${ed("skills." + gi + ".items." + si, s, "")}<button class="mini-x" data-act="del-skill" data-g="${gi}" data-i="${si}" title="Remove">×</button></span>`).join("")}
        <button class="mini-add chip-add" data-act="add-skill" data-g="${gi}" title="Add skill">+</button>
      </div>
    </div>`;
  }

  function jobHtml(j, i, total) {
    j = j || {};
    const bullets = (j.bullets || []);
    return `
    <div class="doc-job" data-job="${i}">
      <div class="job-head">
        <div class="job-title-row">
          ${ed("experience." + i + ".title", j.title, "job-title")}
          <span class="job-co">| ${ed("experience." + i + ".company", j.company, "job-company")}</span>
          <span class="job-loc">${ed("experience." + i + ".location", j.location, "job-location")}</span>
        </div>
        <div class="job-meta">
          ${ed("experience." + i + ".dates", j.dates, "job-dates")}
          ${refineBtn("Punchier", "bullets", String(i))}
          <button class="mini-move" data-act="move-job-up" data-i="${i}" title="Move role up"${i === 0 ? " disabled" : ""}>↑</button>
          <button class="mini-move" data-act="move-job-down" data-i="${i}" title="Move role down"${i === total - 1 ? " disabled" : ""}>↓</button>
          <button class="mini-x job-del" data-act="del-job" data-i="${i}" title="Remove role">×</button>
        </div>
      </div>
      <ul class="job-bullets">
        ${bullets.map((b, bi) => `
          <li>
            ${ed("experience." + i + ".bullets." + bi, b, "")}
            <span class="bullet-tools">
              <button class="mini-move" data-act="move-bullet-up" data-job="${i}" data-i="${bi}" title="Move up"${bi === 0 ? " disabled" : ""}>↑</button>
              <button class="mini-move" data-act="move-bullet-down" data-job="${i}" data-i="${bi}" title="Move down"${bi === bullets.length - 1 ? " disabled" : ""}>↓</button>
              <button class="mini-x" data-act="del-bullet" data-job="${i}" data-i="${bi}" title="Remove">×</button>
            </span>
          </li>`).join("")}
      </ul>
      <button class="mini-add row-add" data-act="add-bullet" data-job="${i}">+ bullet</button>
    </div>`;
  }

  function projectHtml(p, i, total) {
    p = p || {};
    const bullets = (p.bullets || []);
    return `
    <div class="doc-job" data-project="${i}">
      <div class="job-head">
        <div class="job-title-row">
          ${ed("projects." + i + ".name", p.name, "job-title")}
          <span class="job-co">| ${ed("projects." + i + ".link", p.link, "job-company")}</span>
        </div>
        <div class="job-meta">
          ${ed("projects." + i + ".dates", p.dates, "job-dates")}
          ${refineBtn("Punchier", "bullets", "proj:" + i)}
          <button class="mini-move" data-act="move-project-up" data-i="${i}" title="Move project up"${i === 0 ? " disabled" : ""}>↑</button>
          <button class="mini-move" data-act="move-project-down" data-i="${i}" title="Move project down"${i === total - 1 ? " disabled" : ""}>↓</button>
          <button class="mini-x job-del" data-act="del-project" data-i="${i}" title="Remove project">×</button>
        </div>
      </div>
      <ul class="job-bullets">
        ${bullets.map((b, bi) => `
          <li>
            ${ed("projects." + i + ".bullets." + bi, b, "")}
            <span class="bullet-tools">
              <button class="mini-move" data-act="move-pbullet-up" data-project="${i}" data-i="${bi}" title="Move up"${bi === 0 ? " disabled" : ""}>↑</button>
              <button class="mini-move" data-act="move-pbullet-down" data-project="${i}" data-i="${bi}" title="Move down"${bi === bullets.length - 1 ? " disabled" : ""}>↓</button>
              <button class="mini-x" data-act="del-pbullet" data-project="${i}" data-i="${bi}" title="Remove">×</button>
            </span>
          </li>`).join("")}
      </ul>
      <button class="mini-add row-add" data-act="add-pbullet" data-project="${i}">+ bullet</button>
    </div>`;
  }

  function eduHtml(e, i) {
    e = e || {};
    return `
    <div class="doc-edu" data-edu="${i}">
      ${ed("education." + i + ".degree", e.degree, "edu-degree")}
      <span class="edu-inst">, ${ed("education." + i + ".institution", e.institution, "")}</span>
      <span class="edu-dates">${ed("education." + i + ".dates", e.dates, "")}</span>
      <button class="mini-x" data-act="del-edu" data-i="${i}" title="Remove">×</button>
    </div>`;
  }

  function coverHtml(cl) {
    cl = cl || {};
    const paras = (cl.body_paragraphs || []);
    const name = ST.resume.name || "";
    return `
    <div class="doc-page tmpl-${ST.style.template} density-${ST.style.density} cover">
      <div class="cover-top">
        <span class="doc-name">${esc(name)}</span>
        <span class="cover-actions">${refineBtn("Regenerate letter", "cover_letter")}</span>
      </div>
      <p class="cover-date">${new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}</p>
      ${ed("greeting", cl.greeting || "Dear Hiring Manager,", "cover-greeting", "p")}
      <div data-list="body_paragraphs">
        ${paras.map((p, i) => `
          <div class="cover-para-row">
            ${ed("body_paragraphs." + i, p, "cover-para", "p")}
            <button class="mini-x" data-act="del-para" data-i="${i}" title="Remove paragraph">×</button>
          </div>`).join("")}
      </div>
      <button class="mini-add row-add" data-act="add-para">+ paragraph</button>
      ${ed("closing", cl.closing || "Sincerely,", "cover-closing", "p")}
      <p class="cover-sign">${esc(name)}</p>
    </div>`;
  }

  // ---- stage interactions --------------------------------------------------
  function bindStage(scroll) {
    // text edits -> model (no re-render, so the caret stays put)
    scroll.addEventListener("input", e => {
      const t = e.target.closest(".ed[data-bind]");
      if (!t) return;
      setByPath(activeRoot(), t.dataset.bind, t.textContent.replace(/ /g, " ").trim());
      t.removeAttribute("data-empty");
      markDirty();
    });
    // keep Enter from creating <div>/<br> noise in single-line fields
    scroll.addEventListener("keydown", e => {
      const t = e.target.closest(".ed[data-bind]");
      if (!t) return;
      const multiline = t.classList.contains("doc-summary") || t.classList.contains("cover-para");
      if (e.key === "Enter" && !multiline) { e.preventDefault(); t.blur(); }
    });
    // structural buttons + refine triggers
    scroll.addEventListener("click", onStageClick);
  }

  function onStageClick(e) {
    const refine = e.target.closest("[data-refine]");
    if (refine) { openRefine(refine); return; }
    const act = e.target.closest("[data-act]");
    if (!act) return;
    const r = ST.resume, cl = ST.cover;
    const i = act.dataset.i != null ? parseInt(act.dataset.i, 10) : null;
    const jobIdx = act.dataset.job != null ? parseInt(act.dataset.job, 10) : null;
    const projIdx = act.dataset.project != null ? parseInt(act.dataset.project, 10) : null;
    const grpIdx = act.dataset.g != null ? parseInt(act.dataset.g, 10) : null;
    switch (act.dataset.act) {
      case "add-skill-group": (r.skills = r.skills || []).push({ category: "New category", items: ["New skill"] }); break;
      case "del-skill-group": r.skills.splice(i, 1); break;
      case "add-skill": if (grpIdx != null) { (r.skills[grpIdx].items = r.skills[grpIdx].items || []).push("New skill"); } break;
      case "del-skill": if (grpIdx != null) { r.skills[grpIdx].items.splice(i, 1); } break;
      case "add-lang": (r.languages = r.languages || []).push({ name: "Language", level: "" }); break;
      case "del-lang": r.languages.splice(i, 1); break;
      case "add-link": (r.contact = r.contact || {}, r.contact.links = r.contact.links || []).push("link"); break;
      case "del-link": r.contact.links.splice(i, 1); break;
      case "add-job": (r.experience = r.experience || []).push({ title: "Job title", company: "Company", dates: "", bullets: ["Describe an achievement"] }); break;
      case "del-job": r.experience.splice(i, 1); break;
      case "add-bullet": (r.experience[jobIdx].bullets = r.experience[jobIdx].bullets || []).push("New achievement"); break;
      case "del-bullet": r.experience[jobIdx].bullets.splice(i, 1); break;
      case "add-edu": (r.education = r.education || []).push({ degree: "Degree", institution: "Institution", dates: "" }); break;
      case "del-edu": r.education.splice(i, 1); break;
      case "del-cert": r.certifications.splice(i, 1); break;
      case "add-para": (cl.body_paragraphs = cl.body_paragraphs || []).push("New paragraph."); break;
      case "del-para": cl.body_paragraphs.splice(i, 1); break;
      case "move-job-up": move(r.experience, i, -1); break;
      case "move-job-down": move(r.experience, i, 1); break;
      case "move-bullet-up": move(r.experience[jobIdx].bullets, i, -1); break;
      case "move-bullet-down": move(r.experience[jobIdx].bullets, i, 1); break;
      case "add-project": (r.projects = r.projects || []).push({ name: "Project name", link: "", dates: "", bullets: ["What you built and the impact"] }); break;
      case "del-project": r.projects.splice(i, 1); break;
      case "add-pbullet": (r.projects[projIdx].bullets = r.projects[projIdx].bullets || []).push("What you built and the impact"); break;
      case "del-pbullet": r.projects[projIdx].bullets.splice(i, 1); break;
      case "move-project-up": move(r.projects, i, -1); break;
      case "move-project-down": move(r.projects, i, 1); break;
      case "move-pbullet-up": move(r.projects[projIdx].bullets, i, -1); break;
      case "move-pbullet-down": move(r.projects[projIdx].bullets, i, 1); break;
      default: return;
    }
    markDirty();
    renderStage();
  }

  // ---- refine popover ------------------------------------------------------
  let refinePop;
  function closeRefine() { if (refinePop) { refinePop.remove(); refinePop = null; } }
  document.addEventListener("click", e => {
    if (refinePop && !refinePop.contains(e.target) && !e.target.closest("[data-refine]")) closeRefine();
  });

  function openRefine(trigger) {
    closeRefine();
    const kind = trigger.dataset.refine;
    const ref = trigger.dataset.ref;
    const isCover = kind === "cover_letter";
    refinePop = document.createElement("div");
    refinePop.className = "refine-pop";
    refinePop.innerHTML = `
      <label class="rp-label">What should change?</label>
      <input type="text" class="rp-input" id="rpInstr" placeholder="${isCover ? "e.g. open with a stronger hook" : "e.g. make it punchier"}" />
      <div class="rp-row">
        <span>Tone</span>
        <div class="rp-chips" id="rpTone">
          ${TONES.map((t, idx) => `<button type="button" data-tone="${t.id}" class="${idx === 0 ? "active" : ""}">${t.label}</button>`).join("")}
        </div>
      </div>
      ${isCover ? `
      <div class="rp-row">
        <span>Length</span>
        <div class="rp-chips" id="rpLen">
          <button type="button" data-len="" class="active">Keep</button>
          <button type="button" data-len="shorter">Shorter</button>
          <button type="button" data-len="longer">Longer</button>
        </div>
      </div>` : ""}
      <div class="rp-actions">
        <button type="button" class="rp-cancel">Cancel</button>
        <button type="button" class="rp-apply">✦ Regenerate</button>
      </div>
      <div class="rp-busy hidden">Rewriting…</div>`;
    document.body.appendChild(refinePop);
    positionPop(refinePop, trigger);

    let tone = "", length = "";
    refinePop.querySelector("#rpTone").addEventListener("click", ev => {
      const b = ev.target.closest("[data-tone]"); if (!b) return;
      tone = b.dataset.tone;
      refinePop.querySelectorAll("#rpTone button").forEach(x => x.classList.toggle("active", x === b));
    });
    const lenWrap = refinePop.querySelector("#rpLen");
    if (lenWrap) lenWrap.addEventListener("click", ev => {
      const b = ev.target.closest("[data-len]"); if (!b) return;
      length = b.dataset.len;
      lenWrap.querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
    });
    refinePop.querySelector(".rp-cancel").addEventListener("click", closeRefine);
    refinePop.querySelector(".rp-apply").addEventListener("click", async () => {
      const instruction = refinePop.querySelector("#rpInstr").value.trim();
      const busy = refinePop.querySelector(".rp-busy");
      const actions = refinePop.querySelector(".rp-actions");
      busy.classList.remove("hidden"); actions.classList.add("hidden");
      try {
        await runRefine(kind, ref, instruction, tone, length);
        closeRefine();
      } catch (err) {
        busy.textContent = err.message || "Could not refine. Try again.";
      }
    });
    refinePop.querySelector("#rpInstr").focus();
  }

  function positionPop(pop, trigger) {
    const r = trigger.getBoundingClientRect();
    const top = r.bottom + window.scrollY + 6;
    let left = r.left + window.scrollX;
    left = Math.min(left, window.scrollX + document.documentElement.clientWidth - pop.offsetWidth - 12);
    pop.style.top = `${top}px`;
    pop.style.left = `${Math.max(window.scrollX + 12, left)}px`;
  }

  async function runRefine(kind, ref, instruction, tone, length) {
    let content, apply;
    if (kind === "summary") {
      content = ST.resume.summary || "";
      apply = (v) => { ST.resume.summary = typeof v === "string" ? v : (v && v.summary) || ST.resume.summary; };
    } else if (kind === "skills") {
      content = ST.resume.skills || [];
      apply = (v) => { if (Array.isArray(v)) ST.resume.skills = v; };
    } else if (kind === "bullets" && String(ref).startsWith("proj:")) {
      const idx = parseInt(String(ref).slice(5), 10);
      content = (ST.resume.projects[idx] || {}).bullets || [];
      apply = (v) => { if (Array.isArray(v)) ST.resume.projects[idx].bullets = v; };
    } else if (kind === "bullets") {
      const idx = parseInt(ref, 10);
      content = (ST.resume.experience[idx] || {}).bullets || [];
      apply = (v) => { if (Array.isArray(v)) ST.resume.experience[idx].bullets = v; };
    } else if (kind === "cover_letter") {
      content = ST.cover || {};
      apply = (v) => { if (v && typeof v === "object") ST.cover = v; };
    } else return;

    const context = {
      role: ST.job.role || ST.job.title || null,
      company: ST.job.company || null,
    };
    const res = await fetch("/api/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, content, instruction, tone, length, context }),
    });
    const data = await res.json();
    if (!res.ok || data.status !== "ok") throw new Error(data.message || "Refine failed.");
    apply(data.content);
    markDirty();
    renderStage();
    notify("Section regenerated.");
  }

  // ---- download / export ---------------------------------------------------
  function bindDownloadMenu() {
    const menu = document.getElementById("sdlMenu");
    const btn = document.getElementById("sdlBtn");
    const opts = document.getElementById("sdlOptions");
    if (!menu || !btn) return;
    btn.addEventListener("click", e => { e.stopPropagation(); menu.classList.toggle("open"); });
    document.addEventListener("click", () => menu.classList.remove("open"));
    opts.addEventListener("click", e => {
      const b = e.target.closest("[data-fmt]"); if (!b) return;
      menu.classList.remove("open");
      if (b.dataset.fmt === "txt") { downloadText(ST.activeDoc); return; }
      download(ST.activeDoc, b.dataset.fmt);
    });
  }

  // ---- plain-text export (client-side; reflects live edits) ----------------
  function buildResumeText(r) {
    const L = [];
    const push = (s) => L.push(s == null ? "" : String(s));
    if (r.name) push(r.name.toUpperCase());
    const c = r.contact || {};
    const contact = [c.email, c.phone, c.location].filter(Boolean);
    const links = (c.links || []).filter(Boolean);
    if (contact.length) push(contact.join("  |  "));
    if (links.length) push(links.join("  |  "));
    if (r.summary) { push(""); push("SUMMARY"); push(r.summary); }
    if ((r.skills || []).length) { push(""); push("SKILLS"); push(r.skills.join(", ")); }
    if ((r.experience || []).length) {
      push(""); push("EXPERIENCE");
      r.experience.forEach(x => {
        const head = [x.title, x.company].filter(Boolean).join(", ") + (x.dates ? "  (" + x.dates + ")" : "");
        push(""); push(head.trim());
        (x.bullets || []).forEach(b => push("  - " + b));
      });
    }
    if ((r.projects || []).length) {
      push(""); push("PROJECTS");
      r.projects.forEach(p => {
        const head = [p.name, p.dates ? "(" + p.dates + ")" : "", p.link || ""].filter(Boolean).join("  ");
        push(""); push(head.trim());
        (p.bullets || []).forEach(b => push("  - " + b));
      });
    }
    if ((r.education || []).length) {
      push(""); push("EDUCATION");
      r.education.forEach(ed => push([ed.degree, ed.institution].filter(Boolean).join(", ") + (ed.dates ? "  (" + ed.dates + ")" : "")));
    }
    if ((r.certifications || []).length) {
      push(""); push("CERTIFICATIONS");
      r.certifications.forEach(ct => push("  - " + ct));
    }
    return L.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
  }

  function buildCoverText(c, name) {
    const L = [];
    if (c.greeting) { L.push(c.greeting); L.push(""); }
    (c.body_paragraphs || []).forEach(p => { L.push(p); L.push(""); });
    if (c.closing) L.push(c.closing);
    if (name) L.push(name);
    return L.join("\n").replace(/\n{3,}/g, "\n\n").trim() + "\n";
  }

  function downloadText(doc) {
    const isResume = doc === "resume";
    const text = isResume ? buildResumeText(ST.resume || {})
                          : buildCoverText(ST.cover || {}, (ST.resume && ST.resume.name) || "");
    const base = ((ST.resume && ST.resume.name) || "tailorback").trim().replace(/[^\w.-]+/g, "_") || "tailorback";
    const fname = base + (isResume ? "-resume.txt" : "-cover-letter.txt");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify("Plain-text file downloaded.");
    if (window.tbMaybePromptFeedback) window.tbMaybePromptFeedback();
  }

  async function download(doc, fmt) {
    if (ST.dirty || !ST.exported) {
      const ok = await exportNow();
      if (!ok) return;
    }
    const url = ST.urls[`${doc}_${fmt}_url`];
    if (!url) {
      notify(fmt === "pdf"
        ? "PDF export isn't available on this server. Download the Word file instead."
        : "That file isn't available.", true);
      return;
    }
    const a = document.createElement("a");
    a.href = url; a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
    // Prompt for feedback right after a download (once per session, and never
    // if the user has already left a review).
    if (window.tbMaybePromptFeedback) window.tbMaybePromptFeedback();
  }

  async function exportNow() {
    setSaveState("Saving…");
    try {
      const res = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: ST.jobId,
          resume: ST.resume,
          cover_letter: ST.cover,
          style: ST.style,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "ok") throw new Error(data.message || "Export failed.");
      ST.urls = {
        resume_docx_url: data.resume_docx_url,
        resume_pdf_url: data.resume_pdf_url,
        cover_docx_url: data.cover_docx_url,
        cover_pdf_url: data.cover_pdf_url,
      };
      ST.dirty = false; ST.exported = true;
      setSaveState("Saved", true);
      return true;
    } catch (err) {
      setSaveState("");
      notify(err.message || "Could not save changes.", true);
      return false;
    }
  }
})();
