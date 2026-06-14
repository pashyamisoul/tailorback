"""Designer-grade PDF export via HTML/CSS + WeasyPrint.

The docx pipeline (docx_builder) stays the source for the *editable* .docx
download. This module renders the *PDF* download from real HTML/CSS, which gives
pixel-level control the .docx -> LibreOffice path can't: proper typography,
consistent bullets, true two-column sidebars, dark banners, hairline rules.

One parametric template + a per-style config drives all eight gallery looks, so
accent colour, font, density, header treatment and layout are data, not code.
"""
import os
import datetime
from jinja2 import Environment, select_autoescape

# WeasyPrint pulls in system libs (pango/cairo). Import lazily so the app still
# boots (and the docx path still works) on a box where those libs are missing.
try:
    from weasyprint import HTML
    _WEASY_OK = True
except Exception:  # pragma: no cover - environment dependent
    _WEASY_OK = False


def available():
    return _WEASY_OK


# --- per-template configuration ------------------------------------------------
# layout:  "single" | "sidebar" | "banner"
# header:  "center" | "left"
# family:  "sans" | "serif"
# heading: "rule" (uppercase + bottom rule) | "smallcaps" | "hairline" | "plain"
TEMPLATES = {
    "editorial": {"layout": "single", "header": "center", "family": "sans",  "heading": "rule"},
    "modern":    {"layout": "single", "header": "left",   "family": "sans",  "heading": "accent"},
    "classic":   {"layout": "single", "header": "center", "family": "serif", "heading": "rule"},
    "compact":   {"layout": "single", "header": "left",   "family": "sans",  "heading": "accent"},
    "serif":     {"layout": "single", "header": "center", "family": "serif", "heading": "smallcaps"},
    "bold":      {"layout": "banner", "header": "left",   "family": "sans",  "heading": "accent"},
    "minimal":   {"layout": "single", "header": "left",   "family": "sans",  "heading": "hairline"},
    "sidebar":   {"layout": "sidebar", "header": "left",  "family": "sans",  "heading": "accent"},
}
DEFAULT_TEMPLATE = "editorial"

FONT_STACKS = {
    "sans":  "'Inter', 'Open Sans', 'Helvetica Neue', 'Liberation Sans', Arial, sans-serif",
    "serif": "'Georgia', 'Liberation Serif', 'Times New Roman', serif",
}
# Map the UI font picker onto a concrete stack (falls back to template family).
FONT_OVERRIDES = {
    "Calibri": "sans", "Arial": "sans", "Helvetica": "sans",
    "Georgia": "serif", "Garamond": "serif", "Times New Roman": "serif",
}


def _norm_accent(accent):
    a = (accent or "").lstrip("#")
    if len(a) == 6:
        try:
            int(a, 16)
            return "#" + a
        except ValueError:
            pass
    return "#c8462e"


def _contact_bits(c):
    bits = [c.get("email"), c.get("phone"), c.get("location"), *(c.get("links") or [])]
    return [b for b in bits if b]


_ENV = Environment(autoescape=select_autoescape(["html", "xml"]))

_TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><style>{{ css }}</style></head>
<body class="tmpl-{{ cfg.layout }} head-{{ cfg.header }} fam-{{ cfg.family }} hd-{{ cfg.heading }} dens-{{ density }}">

{% macro section_head(label) -%}
  <h2 class="sec">{{ label }}</h2>
{%- endmacro %}

{% macro entry(title, sub, dates, bullets) -%}
  <div class="entry">
    <div class="entry-top">
      <span class="entry-title">{{ title }}</span>
      {% if dates %}<span class="entry-dates">{{ dates }}</span>{% endif %}
    </div>
    {% if sub %}<div class="entry-sub">{{ sub }}</div>{% endif %}
    {% if bullets %}<ul class="bullets">{% for b in bullets %}<li>{{ b }}</li>{% endfor %}</ul>{% endif %}
  </div>
{%- endmacro %}

{% macro skills_block() -%}
  {% if resume.skills %}<section class="s-skills">{{ section_head(skills_label) }}
    <p class="skills">{{ resume.skills | join('  •  ') }}</p></section>{% endif %}
{%- endmacro %}

{% macro education_block() -%}
  {% if resume.education %}<section>{{ section_head('Education') }}
    {% for e in resume.education %}{{ entry(e.degree, e.institution, e.dates, none) }}{% endfor %}</section>{% endif %}
{%- endmacro %}

{% macro certs_block() -%}
  {% if resume.certifications %}<section>{{ section_head('Certifications') }}
    <ul class="bullets certs">{% for c in resume.certifications %}<li>{{ c }}</li>{% endfor %}</ul></section>{% endif %}
{%- endmacro %}

{% macro summary_block() -%}
  {% if resume.summary %}<section>{{ section_head('Professional Summary') }}
    <p class="summary">{{ resume.summary }}</p></section>{% endif %}
{%- endmacro %}

{% macro experience_block() -%}
  {% if resume.experience %}<section>{{ section_head('Professional Experience') }}
    {% for j in resume.experience %}{{ entry(j.title, j.company, j.dates, j.bullets) }}{% endfor %}</section>{% endif %}
{%- endmacro %}

{% macro projects_block() -%}
  {% if resume.projects %}<section>{{ section_head('Projects') }}
    {% for p in resume.projects %}{{ entry(p.name, p.link, p.dates, p.bullets) }}{% endfor %}</section>{% endif %}
{%- endmacro %}

{% if cfg.layout == 'banner' %}
  <header class="banner">
    <h1 class="name">{{ resume.name }}</h1>
    {% if contact %}<div class="contact">{{ contact | join('   •   ') }}</div>{% endif %}
  </header>
  <main>
    {{ summary_block() }}{{ skills_block() }}{{ experience_block() }}
    {{ projects_block() }}{{ education_block() }}{{ certs_block() }}
  </main>

{% elif cfg.layout == 'sidebar' %}
  <div class="sheet">
    <aside class="side">
      <h1 class="name">{{ resume.name }}</h1>
      {% if contact %}<div class="contact">{% for b in contact %}<div>{{ b }}</div>{% endfor %}</div>{% endif %}
      {{ skills_block() }}{{ education_block() }}{{ certs_block() }}
    </aside>
    <main class="body">
      {{ summary_block() }}{{ experience_block() }}{{ projects_block() }}
    </main>
  </div>

{% else %}
  <header class="masthead">
    <h1 class="name">{{ resume.name }}</h1>
    {% if contact %}<div class="contact">{{ contact | join('   •   ') }}</div>{% endif %}
  </header>
  <main>
    {{ summary_block() }}{{ skills_block() }}{{ experience_block() }}
    {{ projects_block() }}{{ education_block() }}{{ certs_block() }}
  </main>
{% endif %}

</body></html>"""


def _css(cfg, accent, font_family):
    base = 10.5
    return f"""
@page {{ size: Letter; margin: 1.3cm 1.45cm; }}
* {{ box-sizing: border-box; }}
body {{
  font-family: {font_family};
  font-size: {base}pt; line-height: 1.34; color: #1f2125; margin: 0;
  --accent: {accent};
  hyphens: manual;            /* never hyphenate brand names like "ServiceNow" */
  overflow-wrap: break-word;  /* only break a token (e.g. long URL) if it can't fit */
}}
body.dens-compact {{ font-size: 9.5pt; line-height: 1.26; }}

/* ---- header ---- */
.name {{ font-size: 25pt; font-weight: 700; letter-spacing: .4px; margin: 0 0 4px;
        color: #15171a; line-height: 1.02; }}
.contact {{ font-size: 8.7pt; color: #5c5f66; letter-spacing: .2px; }}
.contact a {{ color: inherit; text-decoration: none; }}
.masthead {{ text-align: left; padding-bottom: 8px; border-bottom: 2px solid #1f2125; margin-bottom: 4px; }}
.head-center .masthead {{ text-align: center; }}
.head-center .name {{ letter-spacing: 1.2px; }}

/* dark banner header (bold template) */
.banner {{ background: #16181c; color: #fff; padding: 16px 18px; margin: -2px -4px 12px; border-radius: 2px; }}
.banner .name {{ color: #fff; }}
.banner .contact {{ color: #c9cdd4; margin-top: 4px; }}
.banner .name::after {{ content: ""; display: block; width: 46px; height: 3px;
  background: var(--accent); margin-top: 8px; }}

/* ---- section headings ---- */
h2.sec {{ font-size: 10.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: 1.4px;
  color: #2a2d31; margin: 15px 0 7px; padding-bottom: 3px; border-bottom: 1px solid #c9ccd1;
  break-after: avoid; }}
.hd-accent h2.sec {{ color: var(--accent); border-bottom: 2px solid var(--accent); }}
.hd-hairline h2.sec {{ font-weight: 600; letter-spacing: 2px; border-bottom: 1px solid #e2e4e7; color: #33363a; }}
.hd-smallcaps h2.sec {{ text-transform: none; font-variant: small-caps; letter-spacing: 1.6px;
  border-bottom: 1px solid #b9744f; }}
section {{ margin-bottom: 2px; }}

/* ---- entries ---- */
.entry {{ margin: 0 0 9px; break-inside: avoid; }}
.entry-top {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.entry-title {{ font-weight: 700; font-size: {base + 0.5}pt; color: #16181c; }}
.entry-dates {{ font-style: italic; font-size: 8.7pt; color: #6b6e74; white-space: nowrap; flex: none; }}
.entry-sub {{ color: var(--accent); font-size: {base}pt; margin-top: 1px; }}

/* ---- bullets ----
   Native list markers (in document flow) so the PDF text layer stays in reading
   order for ATS parsers; ::marker only recolours the dot. */
ul.bullets {{ margin: 4px 0 0; padding-left: 15px; list-style: disc; }}
ul.bullets li {{ margin-bottom: 3px; padding-left: 2px; }}
ul.bullets li::marker {{ color: var(--accent); }}
.certs li {{ margin-bottom: 2px; }}

.summary {{ margin: 0; }}
.skills {{ margin: 0; color: #2c2f33; }}

/* ---- sidebar layout ---- */
.sheet {{ display: flex; gap: 18px; }}
.side {{ width: 33%; flex: none; padding-right: 16px; border-right: 1px solid #cfd2d7; }}
.body {{ flex: 1; min-width: 0; }}
.side .name {{ font-size: 19pt; }}
.side .contact {{ margin-top: 6px; line-height: 1.5; }}
.side .skills {{ font-size: {base - 0.5}pt; line-height: 1.5; }}
.side h2.sec {{ margin-top: 13px; }}
.side .entry-dates {{ display: block; white-space: normal; }}
"""


def _render_html(resume, cfg, accent, font_family, density):
    tmpl = _ENV.from_string(_TEMPLATE_HTML)
    return tmpl.render(
        resume=resume,
        cfg=cfg,
        css=_css(cfg, accent, font_family),
        contact=_contact_bits(resume.get("contact", {}) or {}),
        density=density,
        skills_label="Core Competencies" if cfg["layout"] != "sidebar" else "Skills",
    )


def render_resume_pdf(resume, out_path, style=None):
    """Render a résumé dict to a styled PDF. Returns out_path, or None if
    WeasyPrint is unavailable (caller falls back to the LibreOffice/docx PDF)."""
    if not _WEASY_OK:
        return None
    style = style or {}
    name = (style.get("template") or DEFAULT_TEMPLATE).lower()
    cfg = TEMPLATES.get(name, TEMPLATES[DEFAULT_TEMPLATE])
    accent = _norm_accent(style.get("accent"))
    fam = FONT_OVERRIDES.get(style.get("font") or "", cfg["family"])
    font_family = FONT_STACKS[fam]
    density = "compact" if (style.get("density") == "compact") else "comfortable"
    html = _render_html(resume, cfg, accent, font_family, density)
    try:
        HTML(string=html).write_pdf(out_path)
    except Exception:
        return None
    return out_path if os.path.exists(out_path) else None
