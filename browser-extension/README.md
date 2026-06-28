# TailorBack Job Grabber

A Manifest V3 Chrome/Edge extension that captures a job posting from the page
you're viewing and sends it to TailorBack to tailor your résumé.

> ⚠️ Not yet published to the Chrome Web Store. It runs fully via **Load
> unpacked** for testing. It has not been auto-tested from the build
> environment — load it in Chrome to verify on real postings.

## What it does
1. When you open the popup on a job page, it **detects the posting** —
   role, company, location, and the full description — using per-site
   extractors for **LinkedIn, Indeed, Greenhouse, and Lever**, with a generic
   fallback for any other site.
2. Shows you what it found (role / company / source / a preview).
3. **Tailor this job** opens TailorBack with the Job Description box already
   filled in, and also copies the description to your clipboard as a fallback.

Only **on-screen** text is read (via `activeTab` + `scripting`, on click). The
extension declares no broad host permissions and stores nothing about you;
the only saved setting is your TailorBack URL.

## Files
- `manifest.json` — MV3 config (activeTab, scripting, storage, clipboardWrite).
- `popup.html` / `popup.css` / `popup.js` — the popup UI and its states
  (detected → sent, plus an empty/fallback state).
- `extractors.js` — `tbPageExtractor()`, injected into the active tab to read
  the posting. Self-contained; per-site selectors are heuristic.
- `options.html` / `options.js` — set the TailorBack URL.
- `icons/` — 16/48/128px action icons.

## Load it for testing (developer mode)
1. Chrome/Edge → `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `browser-extension/` folder.
3. (Optional) open the extension's **Settings** (the ⚙ in the popup, or the
   extensions page → Details → Extension options) and set your TailorBack URL.
   Defaults to the local dev server `http://127.0.0.1:5000/`.
4. Open a job on LinkedIn/Indeed/Greenhouse/Lever → click the TailorBack icon
   → **Tailor this job**.

## Roadmap / not done yet
- **Signed-in status / credits** in the popup (needs the app endpoint above).
- Per-site selectors will drift as these sites change their markup; revisit
  periodically. Everything still falls back to the generic extractor.
- **Chrome Web Store submission** (one-time developer fee + review) before
  public release.

## Not included (deliberately)
- LinkedIn **profile import** is handled inside the app the ToS-safe way:
  export your profile via LinkedIn → *More → Save to PDF* and upload it as your
  CV (the existing PDF parser reads it). Automated profile scraping violates
  LinkedIn's terms and is intentionally not done here.
