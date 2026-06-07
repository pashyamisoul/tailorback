# TailorBack Job Grabber — PROTOTYPE

⚠️ **This is an early scaffold, not a finished/published extension.** It has
not been tested in a browser from this environment and is not wired into the
app's build. Treat it as a starting point for the Phase 13 browser extension.

## What it does
A Manifest V3 Chrome/Edge extension that:
1. Reads the **visible** job-description text from the page you're viewing
   (it does not scrape gated or private data — only what's on screen).
2. Copies that text to your clipboard.
3. Opens TailorBack so you can paste it into the Job Description box.

This clipboard approach needs **no changes to the app** and avoids putting job
text in URLs.

## Load it for testing (developer mode)
1. Chrome → `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this `browser-extension/` folder.
3. Open a job posting, click the TailorBack icon → **Grab job description**.
4. Set the "TailorBack URL" field to your deployed app URL (defaults to the
   local dev server).

## Known limitations / TODO before this is production-ready
- No icons yet (add 16/48/128px PNGs and reference them in `manifest.json`).
- Text extraction is heuristic; per-site selectors (LinkedIn, Indeed,
  Greenhouse, Lever) would improve accuracy.
- A deeper integration could POST the JD to a TailorBack endpoint and deep-link
  into a pre-filled builder, instead of clipboard + paste. That needs an app
  endpoint + auth/CORS handling.
- Needs real testing across browsers and a store-submission review before
  release.

## Not included (deliberately)
- LinkedIn **profile import** is handled inside the app the ToS-safe way:
  export your profile via LinkedIn → *More → Save to PDF* and upload it as your
  CV (the existing PDF parser reads it). Automated profile scraping is against
  LinkedIn's terms and is intentionally not done here.
