/*
 * extractors.js — runs in the popup, but tbPageExtractor() is injected into the
 * active tab via chrome.scripting.executeScript({func: tbPageExtractor}).
 *
 * IMPORTANT: tbPageExtractor must be fully self-contained (no references to
 * anything outside its own body), because Chrome serializes the function source
 * and runs it in the page's context. Per-site selectors are heuristic and may
 * need updating as job sites change their markup; every site falls back to a
 * generic extractor.
 */
function tbPageExtractor() {
  function text(el) {
    return el ? (el.innerText || el.textContent || "").trim() : "";
  }
  function firstText(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      const t = text(el);
      if (t) return t;
    }
    return "";
  }
  function clean(s) {
    return (s || "").replace(/ /g, " ").replace(/[ \t]+\n/g, "\n")
      .replace(/\n{3,}/g, "\n\n").trim();
  }

  const host = location.hostname;
  let site = "Generic", role = "", company = "", loc = "", desc = "";

  if (host.includes("linkedin.")) {
    site = "LinkedIn";
    role = firstText([
      ".job-details-jobs-unified-top-card__job-title",
      ".jobs-unified-top-card__job-title",
      ".top-card-layout__title",
      "h1",
    ]);
    company = firstText([
      ".job-details-jobs-unified-top-card__company-name",
      ".jobs-unified-top-card__company-name",
      ".topcard__org-name-link",
      "a[data-tracking-control-name*='company']",
    ]);
    loc = firstText([
      ".job-details-jobs-unified-top-card__bullet",
      ".jobs-unified-top-card__bullet",
      ".topcard__flavor--bullet",
    ]);
    desc = firstText([
      ".jobs-description__content",
      "#job-details",
      ".show-more-less-html__markup",
      ".description__text",
    ]);
  } else if (host.includes("indeed.")) {
    site = "Indeed";
    role = firstText([
      "h1.jobsearch-JobInfoHeader-title",
      ".jobsearch-JobInfoHeader-title",
      "h1",
    ]);
    company = firstText([
      "[data-company-name='true']",
      ".jobsearch-CompanyInfoContainer a",
      ".jobsearch-InlineCompanyRating div",
    ]);
    loc = firstText([
      "[data-testid='inlineHeader-companyLocation']",
      ".jobsearch-JobInfoHeader-subtitle div:last-child",
    ]);
    desc = firstText(["#jobDescriptionText", ".jobsearch-jobDescriptionText"]);
  } else if (host.includes("greenhouse.io") || host.includes("boards.greenhouse")) {
    site = "Greenhouse";
    role = firstText([".app-title", "h1.section-header", "h1"]);
    company = firstText([".company-name", "span.company-name", ".level-0"]);
    loc = firstText([".location", ".app-location"]);
    desc = firstText(["#content", "#main", "div.content"]);
  } else if (host.includes("lever.co") || host.includes("jobs.lever")) {
    site = "Lever";
    role = firstText([".posting-headline h2", "h2"]);
    company = firstText([".main-header-logo img", ".posting-categories .company"]);
    loc = firstText([".posting-categories .location", ".sort-by-time posting-category"]);
    desc = firstText([".posting-page .section-wrapper", ".content", "[data-qa='job-description']"]);
  }

  // Generic fallback: largest meaningful block on the page.
  if (!desc) {
    site = site === "Generic" ? "this page" : site;
    const candidates = Array.from(document.querySelectorAll(
      "article, main, [class*='description'], [class*='job'], [id*='description'], section"
    ));
    let best = "", bestLen = 0;
    for (const el of candidates) {
      const t = text(el);
      if (t.length > bestLen && t.length < 20000) { best = t; bestLen = t.length; }
    }
    desc = best || text(document.body).slice(0, 8000);
  }
  if (!role) role = firstText(["h1", "h2"]) || (document.title || "").split(/[|\-–]/)[0].trim();

  desc = clean(desc);
  return {
    site,
    role: clean(role),
    company: clean(company),
    location: clean(loc),
    text: desc,
    words: desc ? desc.split(/\s+/).filter(Boolean).length : 0,
    url: location.href,
  };
}
