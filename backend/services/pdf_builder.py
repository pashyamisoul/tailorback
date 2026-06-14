"""Designer-grade, ATS-standard PDF export via HTML/CSS + WeasyPrint.

The docx pipeline (docx_builder) stays the source for the *editable* .docx
download. This module renders the *PDF* from real HTML/CSS for pixel-level
control over the recruiter/ATS-standard layout:

  * single column (parses cleanly top-to-bottom)
  * canonical entries: Company (bold) + Location (right) / Title (italic) +
    Dates (right), bullets led by action verbs
  * skills grouped by labelled category
  * monochrome by default; an accent is an optional, restrained tint

One parametric template + a per-style config drives every gallery look
(serif/sans, centered/left header, monochrome/accent/banner).
"""
import os
from jinja2 import Environment, select_autoescape

try:
    from weasyprint import HTML
    _WEASY_OK = True
except Exception:  # pragma: no cover - environment dependent
    _WEASY_OK = False


def available():
    return _WEASY_OK


# accent: "none" | "rule" (tint section rules) | "banner" (dark header band)
TEMPLATES = {
    "editorial": {"header": "center", "family": "serif", "accent": "none",   "smallcaps": False, "two_col": False},
    "modern":    {"header": "left",   "family": "sans",  "accent": "rule",   "smallcaps": False, "two_col": False},
    "classic":   {"header": "center", "family": "serif", "accent": "none",   "smallcaps": False, "two_col": False},
    "compact":   {"header": "left",   "family": "sans",  "accent": "none",   "smallcaps": False, "two_col": False},
    "serif":     {"header": "center", "family": "serif", "accent": "none",   "smallcaps": True,  "two_col": False},
    "bold":      {"header": "left",   "family": "sans",  "accent": "banner", "smallcaps": False, "two_col": False},
    "minimal":   {"header": "left",   "family": "sans",  "accent": "none",   "smallcaps": False, "two_col": False},
    "sidebar":   {"header": "left",   "family": "sans",  "accent": "rule",   "smallcaps": False, "two_col": True},
}
DEFAULT_TEMPLATE = "editorial"

FONT_STACKS = {
    "sans":  "'Open Sans', 'Helvetica Neue', 'Liberation Sans', Arial, sans-serif",
    "serif": "'Georgia', 'Liberation Serif', 'Times New Roman', serif",
}
FONT_OVERRIDES = {
    "Calibri": "sans", "Arial": "sans", "Helvetica": "sans",
    "Georgia": "serif", "Garamond": "serif", "Times New Roman": "serif",
}


def _norm_accent(accent):
    a = (accent or "").lstrip("#")
    if len(a) == 6:
        try:
            int(a, 16); return "#" + a
        except ValueError:
            pass
    return "#c8462e"


def _contact_bits(c):
    bits = [c.get("location"), c.get("phone"), c.get("email"), *(c.get("links") or [])]
    return [b for b in bits if b]


def _skill_groups(skills):
    """Normalise skills into [{'category': str|None, 'items': [str]}], accepting
    the new grouped shape, a dict, or a legacy flat list of strings."""
    if not skills:
        return []
    if isinstance(skills, dict):
        return [{"category": k, "items": v} for k, v in skills.items() if v]
    if isinstance(skills, list) and skills and isinstance(skills[0], dict):
        out = []
        for g in skills:
            items = g.get("items") or []
            if items:
                out.append({"category": (g.get("category") or "").strip() or None, "items": items})
        return out
    return [{"category": None, "items": [s for s in skills if s]}]


_ENV = Environment(autoescape=select_autoescape(["html", "xml"]))

_TEMPLATE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><style>{{ css }}</style></head>
<body class="head-{{ cfg.header }} fam-{{ cfg.family }} acc-{{ cfg.accent }}{% if cfg.smallcaps %} smallcaps{% endif %}{% if cfg.two_col %} two-col{% endif %}">

{% macro section(label) -%}<h2 class="sec">{{ label }}</h2>{%- endmacro %}

{% macro entry(primary, topright, secondary, botright, bullets) -%}
  <div class="entry">
    <div class="erow"><span class="eprimary">{{ primary }}</span>{% if topright %}<span class="eright">{{ topright }}</span>{% endif %}</div>
    {% if secondary or botright %}<div class="erow esub"><span class="esec">{{ secondary }}</span>{% if botright %}<span class="eright2">{{ botright }}</span>{% endif %}</div>{% endif %}
    {% if bullets %}<ul class="bullets">{% for b in bullets %}<li>{{ b }}</li>{% endfor %}</ul>{% endif %}
  </div>
{%- endmacro %}

{% macro summary_block() -%}{% if resume.summary %}<section>{{ section('Summary') }}<p class="summary">{{ resume.summary }}</p></section>{% endif %}{%- endmacro %}

{% macro skills_block() -%}
  {% if groups %}<section>{{ section('Skills') }}<div class="skills">
    {% for g in groups %}<p>{% if g['category'] %}<span class="skcat">{{ g['category'] }}:</span> {% endif %}{{ g['items'] | join(', ') }}</p>{% endfor %}
  </div></section>{% endif %}
{%- endmacro %}

{% macro experience_block() -%}
  {% if resume.experience %}<section>{{ section('Experience') }}
    {% for j in resume.experience %}{{ entry(j.company or j.title, j.location, j.title if j.company else None, j.dates, j.bullets) }}{% endfor %}
  </section>{% endif %}
{%- endmacro %}

{% macro projects_block() -%}
  {% if resume.projects %}<section>{{ section('Projects') }}
    {% for p in resume.projects %}{{ entry(p.name, p.dates, p.link, none, p.bullets) }}{% endfor %}
  </section>{% endif %}
{%- endmacro %}

{% macro education_block() -%}
  {% if resume.education %}<section>{{ section('Education') }}
    {% for e in resume.education %}{{ entry(e.institution or e.degree, e.dates, e.degree if e.institution else None, none, none) }}{% endfor %}
  </section>{% endif %}
{%- endmacro %}

{% macro certs_block() -%}
  {% if resume.certifications %}<section>{{ section('Certifications') }}<ul class="bullets">{% for c in resume.certifications %}<li>{{ c }}</li>{% endfor %}</ul></section>{% endif %}
{%- endmacro %}

{% if cfg.accent == 'banner' %}
  <header class="banner"><h1 class="name">{{ resume.name }}</h1>{% if contact %}<div class="contact">{{ contact | join('  •  ') }}</div>{% endif %}</header>
{% else %}
  <header class="masthead"><h1 class="name">{{ resume.name }}</h1>{% if contact %}<div class="contact">{{ contact | join('  •  ') }}</div>{% endif %}</header>
{% endif %}

{% if cfg.two_col %}
  <div class="sheet">
    <aside class="side">{{ skills_block() }}{{ education_block() }}{{ certs_block() }}</aside>
    <main class="body">{{ summary_block() }}{{ experience_block() }}{{ projects_block() }}</main>
  </div>
{% else %}
  <main>{{ summary_block() }}{{ skills_block() }}{{ experience_block() }}{{ projects_block() }}{{ education_block() }}{{ certs_block() }}</main>
{% endif %}

</body></html>"""


def _css(cfg, accent, font_family):
    base = 10.5
    # In monochrome templates the rule + skill labels are black; accent only
    # tints when the template opts in.
    rule = accent if cfg["accent"] in ("rule", "banner") else "#000"
    return f"""
@page {{ size: Letter; margin: 1.15cm 1.4cm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: {font_family}; font-size: {base}pt; line-height: 1.3; color: #000; margin: 0;
  hyphens: manual; overflow-wrap: break-word; }}

.name {{ font-size: 21pt; font-weight: 700; letter-spacing: .6px; margin: 0 0 3px; text-transform: uppercase; }}
.contact {{ font-size: 9.2pt; color: #222; }}
.contact a {{ color: inherit; text-decoration: none; }}
.masthead {{ margin-bottom: 2px; }}
.head-center .masthead {{ text-align: center; }}
.head-center .name {{ letter-spacing: 1.5px; }}

.banner {{ background: #16181c; color: #fff; padding: 14px 16px; margin: -2px -4px 8px; }}
.banner .name {{ color: #fff; }}
.banner .contact {{ color: #cfd3da; margin-top: 3px; }}
.banner .name::after {{ content: ""; display: block; width: 44px; height: 3px; background: {accent}; margin-top: 7px; }}

h2.sec {{ font-size: 11pt; font-weight: 700; text-transform: uppercase; letter-spacing: .8px;
  margin: 11px 0 3px; padding-bottom: 1.5px; border-bottom: 1px solid {rule}; break-after: avoid; }}
.smallcaps h2.sec {{ text-transform: none; font-variant: small-caps; letter-spacing: 1px; }}
section {{ margin-bottom: 1px; }}

.summary {{ margin: 2px 0 0; }}
.skills p {{ margin: 1.5px 0; }}
.skills .skcat {{ font-weight: 700; }}

.entry {{ margin: 0 0 6px; break-inside: avoid; }}
.erow {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }}
.eprimary {{ font-weight: 700; }}
.eright {{ font-weight: 700; white-space: nowrap; flex: none; }}
.esub {{ margin-top: 0; }}
.esec {{ font-style: italic; }}
.eright2 {{ font-style: italic; white-space: nowrap; flex: none; font-size: 9.6pt; color: #222; }}

ul.bullets {{ margin: 2px 0 0; padding-left: 15px; list-style: disc; }}
ul.bullets li {{ margin-bottom: 1.5px; padding-left: 2px; }}
ul.bullets li::marker {{ color: {rule}; }}

/* two-column (sidebar) */
.sheet {{ display: flex; gap: 16px; }}
.side {{ width: 31%; flex: none; padding-right: 14px; border-right: 1px solid #cfd2d7; }}
.body {{ flex: 1; min-width: 0; }}
.side h2.sec {{ margin-top: 10px; }}
.side .eright {{ font-weight: 400; font-size: 9pt; }}
"""


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
    html = _ENV.from_string(_TEMPLATE_HTML).render(
        resume=resume, cfg=cfg, css=_css(cfg, accent, font_family),
        contact=_contact_bits(resume.get("contact", {}) or {}),
        groups=_skill_groups(resume.get("skills")),
    )
    try:
        HTML(string=html).write_pdf(out_path)
    except Exception:
        return None
    return out_path if os.path.exists(out_path) else None
