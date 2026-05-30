from __future__ import annotations
"""
Turn a public job-posting URL into clean job-description text.

Strategy:
  1. Simple HTTP fetch (fast, cheap).
  2. If the page is a JS shell or blocked, retry with a headless browser.
  3. Platform-aware extraction for friendly ATS portals (Greenhouse, Lever, Ashby).
  4. Generic main-content extraction (trafilatura) for everything else.
  5. Validate the result actually looks like a JD; otherwise signal failure so
     the caller falls back to "paste the job description".

LinkedIn / Indeed aggressively block bots and prohibit scraping in their ToS.
We attempt a plain fetch only and expect to fall back. We do NOT try to defeat
their bot protection.
"""
from urllib.parse import urlparse
import httpx
import trafilatura

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

MIN_JD_CHARS = 200
# Cheap signals that we hit a wall / login page rather than a real posting.
BLOCK_MARKERS = ("sign in to continue", "please enable javascript", "are you a human",
                  "verify you are human", "access denied", "captcha")
# Words a real posting almost always contains — used as a positive check.
JD_SIGNALS = ("responsibilit", "requirement", "qualif", "experience",
              "skills", "you will", "we are looking", "about the role")


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _simple_fetch(url: str) -> str | None:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=15)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None


def _browser_fetch(url: str) -> str | None:
    """Render JS-heavy pages. Optional — only used if Playwright is installed."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="networkidle", timeout=25000)
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None


def _greenhouse(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("#content") or soup.select_one(".job__description")
    return node.get_text("\n", strip=True) if node else ""


def _lever(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(".posting-page") or soup.select_one(".section-wrapper")
    return node.get_text("\n", strip=True) if node else ""


def _ashby(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("[class*='descriptionText']") or soup.find("main")
    return node.get_text("\n", strip=True) if node else ""


PLATFORM_EXTRACTORS = {
    "greenhouse.io": _greenhouse,
    "boards.greenhouse.io": _greenhouse,
    "lever.co": _lever,
    "jobs.lever.co": _lever,
    "ashbyhq.com": _ashby,
}


def _extract(html: str, domain: str) -> str:
    for key, fn in PLATFORM_EXTRACTORS.items():
        if key in domain:
            text = fn(html)
            if len(text) >= MIN_JD_CHARS:
                return text
    # Generic fallback for unknown sites.
    text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    return text.strip()


def _validate(text: str) -> bool:
    if not text or len(text) < MIN_JD_CHARS:
        return False
    low = text.lower()
    if any(m in low for m in BLOCK_MARKERS):
        return False
    return any(s in low for s in JD_SIGNALS)


def fetch_jd(url: str) -> tuple[str, bool]:
    """
    Return (jd_text, ok). ok=False means the caller should ask the user to
    paste the description instead.
    """
    domain = _domain(url)

    # LinkedIn / Indeed: attempt simple fetch only; expect to fall back.
    html = _simple_fetch(url)
    if html:
        text = _extract(html, domain)
        if _validate(text):
            return text, True

    # Escalate to a headless browser for JS-rendered portals.
    html = _browser_fetch(url)
    if html:
        text = _extract(html, domain)
        if _validate(text):
            return text, True

    return "", False
