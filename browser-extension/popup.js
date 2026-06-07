// TailorBack Job Grabber (PROTOTYPE).
// Pulls the main visible text from the active tab, copies it to the clipboard,
// and opens TailorBack so the user can paste it into the Job Description box.
// No scraping of gated/private data; it only reads what the user is viewing.

const statusEl = document.getElementById("status");

function setStatus(msg, cls) {
  statusEl.textContent = msg;
  statusEl.className = "status" + (cls ? " " + cls : "");
}

// Runs in the page context: grab a reasonable "main content" text block.
function extractJobText() {
  const pick = (sel) => {
    const el = document.querySelector(sel);
    return el ? el.innerText.trim() : "";
  };
  // Try common job-posting containers first, then fall back to <main>/body.
  const candidates = [
    '[class*="job-description"]', '[class*="jobDescription"]',
    '[data-testid*="jobDescription"]', 'article', 'main',
  ];
  for (const sel of candidates) {
    const t = pick(sel);
    if (t && t.length > 200) return t;
  }
  return (document.body ? document.body.innerText : "").trim();
}

document.getElementById("grab").addEventListener("click", async () => {
  setStatus("Reading page…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractJobText,
    });
    const text = (result || "").slice(0, 20000);
    if (!text || text.length < 100) {
      setStatus("Could not find enough text on this page.", "err");
      return;
    }
    await navigator.clipboard.writeText(text);
    const url = (document.getElementById("appUrl").value || "").trim() || "http://127.0.0.1:5000/";
    await chrome.tabs.create({ url });
    setStatus("Copied. Paste it into the Job Description box.", "ok");
  } catch (e) {
    setStatus("Error: " + (e && e.message ? e.message : e), "err");
  }
});
