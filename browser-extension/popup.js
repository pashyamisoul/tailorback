"use strict";

const DEFAULT_APP_URL = "http://127.0.0.1:5000/";
const body = document.getElementById("body");

document.getElementById("optionsLink").addEventListener("click", (e) => {
  e.preventDefault();
  if (chrome.runtime.openOptionsPage) chrome.runtime.openOptionsPage();
});

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function getAppUrl() {
  return new Promise((resolve) => {
    try {
      chrome.storage.sync.get({ appUrl: DEFAULT_APP_URL }, (v) =>
        resolve((v && v.appUrl) || DEFAULT_APP_URL));
    } catch (_) {
      resolve(DEFAULT_APP_URL);
    }
  });
}

async function detect() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || /^(chrome|edge|about|chrome-extension):/.test(tab.url || "")) {
    return renderEmpty();
  }
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: tbPageExtractor, // from extractors.js
    });
    const job = results && results[0] && results[0].result;
    if (job && job.text && job.words >= 40) renderDetected(job);
    else renderEmpty(job);
  } catch (err) {
    renderEmpty(null, err && err.message);
  }
}

function renderDetected(job) {
  const co = [job.company, job.location].filter(Boolean).join(" · ");
  body.innerHTML = `
    <div class="detect">Detected job</div>
    <div class="role">${esc(job.role) || "Job posting"}</div>
    <div class="co">${esc(co)}</div>
    <span class="src">${esc(job.site)}${job.words ? " · " + job.words + " words" : ""}</span>
    <div class="jd">${esc(job.text.slice(0, 600))}</div>
    <button class="btn btn-primary" id="tailor">Tailor this job →</button>
    <p class="hint">Copies the job description and opens TailorBack to paste it in.</p>
    <p class="msg" id="msg"></p>`;
  document.getElementById("tailor").addEventListener("click", () => tailor(job));
}

function renderSent(job) {
  body.innerHTML = `
    <div class="detect">Captured</div>
    <div class="role">${esc(job.role) || "Job posting"}</div>
    ${job.company ? `<div class="co">${esc(job.company)}</div>` : ""}
    <div class="step done"><span class="n">✓</span><span><b>Job description copied</b> (${job.words} words)</span></div>
    <div class="step done"><span class="n">✓</span><span>Opened TailorBack in a new tab</span></div>
    <div class="step"><span class="n">3</span><span>Paste into the Job Description box (Ctrl/Cmd+V), pick your CV, hit <b>Run</b></span></div>
    <button class="btn btn-primary" id="reopen">Open TailorBack ↗</button>`;
  document.getElementById("reopen").addEventListener("click", openApp);
}

function renderEmpty(job, errMsg) {
  body.innerHTML = `
    <div class="empty">
      <div class="big">🔎</div>
      <p><b>No job posting detected here.</b></p>
      <p>Open a job on LinkedIn, Indeed, Greenhouse or Lever, then click the TailorBack icon.</p>
    </div>
    ${errMsg ? `<p class="msg err">${esc(errMsg)}</p>` : ""}
    <button class="btn btn-ghost" id="grabAnyway">Grab visible text anyway</button>
    <button class="btn btn-primary" id="openApp">Open TailorBack</button>`;
  document.getElementById("openApp").addEventListener("click", openApp);
  document.getElementById("grabAnyway").addEventListener("click", grabAnyway);
}

async function tailor(job) {
  const msg = document.getElementById("msg");
  try {
    await navigator.clipboard.writeText(job.text);
    await openApp();
    renderSent(job);
  } catch (err) {
    if (msg) { msg.textContent = "Could not copy: " + (err.message || err); msg.className = "msg err"; }
  }
}

async function grabAnyway() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => (document.body.innerText || "").trim().slice(0, 12000),
    });
    const txt = results && results[0] && results[0].result;
    if (txt) {
      await navigator.clipboard.writeText(txt);
      await openApp();
      renderSent({ role: "Visible page text", company: "", words: txt.split(/\s+/).length });
    }
  } catch (_) { /* restricted page */ }
}

async function openApp() {
  const url = await getAppUrl();
  chrome.tabs.create({ url });
}

detect();
