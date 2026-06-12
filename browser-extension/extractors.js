/*
 * extractors.js — runs in the popup, but tbPageExtractor() is injected into the
 * active tab via chrome.scripting.executeScript({func: tbPageExtractor}).
 *
 * IMPORTANT: tbPageExtractor must be fully self-contained (no references to
 * anything outside its own body), because Chrome serializes the function source
 * and runs it in the page's context.
 *
 * It only reports `confident: true` when the page is actually a job posting —
 * detected via schema.org JobPosting JSON-LD (most reliable) or per-site
 * selectors for LinkedIn/Indeed/Greenhouse/Lever. On any other page it returns
 * confident:false so the popup says "no job found" (with a manual fallback).
 */
function tbPageExtractor() {
  function text(el) { return el ? (el.innerText || el.textContent || "").trim() : ""; }
  function firstText(selectors) {
    for (const sel of selectors) {
      const t = text(document.querySelector(sel));
      if (t) return t;
    }
    return "";
  }
  function stripHtml(html) {
    if (!html) return "";
    const d = document.createElement("div");
    d.innerHTML = html;
    return d.textContent || d.innerText || "";
  }
  function clean(s) {
    return (s || "").replace(/ /g, " ").replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n").trim();
  }
  function words(s) { return s ? s.split(/\s+/).filter(Boolean).length : 0; }

  // --- 1. schema.org JobPosting JSON-LD (works on most career/ATS sites) ---
  function fromJsonLd() {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const s of scripts) {
      let data;
      try { data = JSON.parse(s.textContent); } catch (e) { continue; }
      const nodes = Array.isArray(data) ? data : (data["@graph"] || [data]);
      for (const n of nodes) {
        const t = n && n["@type"];
        const isJob = t === "JobPosting" || (Array.isArray(t) && t.indexOf("JobPosting") !== -1);
        if (isJob) return n;
      }
    }
    return null;
  }

  const host = location.hostname;
  let site = "", role = "", company = "", loc = "", desc = "", confident = false;

  const ld = fromJsonLd();
  if (ld) {
    confident = true;
    site = "Job posting";
    role = ld.title || "";
    const org = ld.hiringOrganization;
    company = (org && (org.name || org)) || "";
    const addr = ld.jobLocation && (Array.isArray(ld.jobLocation) ? ld.jobLocation[0] : ld.jobLocation);
    loc = (addr && addr.address && (addr.address.addressLocality || addr.address.addressRegion)) || "";
    desc = stripHtml(ld.description);
  }

  // --- 2. per-site selectors ---
  if (!desc && host.includes("linkedin.")) {
    site = "LinkedIn";
    role = firstText([".job-details-jobs-unified-top-card__job-title", ".jobs-unified-top-card__job-title", ".top-card-layout__title"]);
    company = firstText([".job-details-jobs-unified-top-card__company-name", ".jobs-unified-top-card__company-name", ".topcard__org-name-link"]);
    loc = firstText([".job-details-jobs-unified-top-card__bullet", ".jobs-unified-top-card__bullet", ".topcard__flavor--bullet"]);
    desc = firstText([".jobs-description__content", "#job-details", ".show-more-less-html__markup", ".description__text"]);
    if (desc) confident = true;
  } else if (!desc && host.includes("indeed.")) {
    site = "Indeed";
    role = firstText(["h1.jobsearch-JobInfoHeader-title", ".jobsearch-JobInfoHeader-title"]);
    company = firstText(["[data-company-name='true']", ".jobsearch-CompanyInfoContainer a"]);
    loc = firstText(["[data-testid='inlineHeader-companyLocation']"]);
    desc = firstText(["#jobDescriptionText", ".jobsearch-jobDescriptionText"]);
    if (desc) confident = true;
  } else if (!desc && (host.includes("greenhouse.io") || host.includes("boards.greenhouse"))) {
    site = "Greenhouse";
    role = firstText([".app-title", "h1.section-header"]);
    company = firstText([".company-name", "span.company-name"]);
    loc = firstText([".location", ".app-location"]);
    desc = firstText(["#content", "#main", "div.content"]);
    if (desc) confident = true;
  } else if (!desc && (host.includes("lever.co") || host.includes("jobs.lever"))) {
    site = "Lever";
    role = firstText([".posting-headline h2", "h2"]);
    loc = firstText([".posting-categories .location"]);
    desc = firstText([".posting-page .section-wrapper", ".content", "[data-qa='job-description']"]);
    if (desc) confident = true;
  }

  // --- 3. generic fallback: NOT confident (used only for "grab anyway") ---
  if (!desc) {
    const candidates = Array.from(document.querySelectorAll(
      "article, main, [class*='description'], [id*='description']"
    ));
    let best = "", bestLen = 0;
    for (const el of candidates) {
      const t = text(el);
      if (t.length > bestLen && t.length < 20000) { best = t; bestLen = t.length; }
    }
    desc = best || text(document.body).slice(0, 8000);
    site = "this page";
    confident = false;
  }

  if (!role) role = firstText(["h1", "h2"]) || (document.title || "").split(/[|\-–]/)[0].trim();
  desc = clean(desc);
  return {
    site, confident,
    role: clean(role), company: clean(company), location: clean(loc),
    text: desc, words: words(desc), url: location.href,
  };
}
