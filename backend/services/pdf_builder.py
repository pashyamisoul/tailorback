"""Designer-grade PDF export via HTML/CSS + WeasyPrint.

The docx pipeline (docx_builder) stays the source for the *editable* .docx
download; this renders the *PDF* from real HTML/CSS for full control.

Template gallery (9):
  single-column / ATS-plainest : editorial, classic, serif, minimal
  divider two-column           : sidebar
  rich designs                 : skyline (timeline), executive (dark sidebar),
                                 aurora (tinted sidebar), spotlight (header band)

The rich designs carry a curated default colour; the accent picker overrides it
only when the user deliberately changes the swatch away from the app default.
"""
import os
import html as _html

from services.link_utils import external_link_target

try:
    from weasyprint import HTML
    _WEASY_OK = True
except Exception:  # pragma: no cover
    _WEASY_OK = False


def available():
    return _WEASY_OK


_APP_DEFAULT_ACCENT = "c8462e"   # the editor's default swatch

# layout: single | sidebar | timeline | sidebar_solid | sidebar_tint | banner
TEMPLATES = {
    "editorial": {"layout": "single", "header": "center", "family": "serif", "rule": "mono"},
    "classic":   {"layout": "single", "header": "center", "family": "serif", "rule": "mono"},
    "serif":     {"layout": "single", "header": "center", "family": "serif", "rule": "mono", "smallcaps": True},
    "minimal":   {"layout": "single", "header": "left",   "family": "sans",  "rule": "mono"},
    "sidebar":   {"layout": "sidebar", "header": "left",  "family": "sans",  "rule": "accent"},
    "skyline":   {"layout": "timeline", "family": "sans", "default": "#3d8b7d"},
    "executive": {"layout": "sidebar_solid", "side_bg": "#23344d", "family": "sans", "default": "#b8893f"},
    "aurora":    {"layout": "sidebar_tint", "family": "sans", "default": "#2f8f7d"},
    "spotlight": {"layout": "banner", "family": "sans", "default": "#5f8d6e"},
}
DEFAULT_TEMPLATE = "editorial"

FONT_STACKS = {
    "sans":  "'Open Sans', 'Helvetica Neue', 'Liberation Sans', Arial, sans-serif",
    "serif": "'Georgia', 'Liberation Serif', 'Times New Roman', serif",
}
FONT_OVERRIDES = {"Calibri": "sans", "Arial": "sans", "Helvetica": "sans",
                  "Georgia": "serif", "Garamond": "serif", "Times New Roman": "serif"}


def _norm_accent(accent, fallback):
    a = (accent or "").lstrip("#")
    if len(a) == 6:
        try:
            int(a, 16); return "#" + a
        except ValueError:
            pass
    return fallback


def e(s):
    return _html.escape(str(s)) if s is not None else ""


def _a(text):
    target = external_link_target(text)
    if not target:
        return e(text)
    return f"<a href='{e(target)}'>{e(text)}</a>"


def _contact_bits(c):
    return [b for b in [c.get("location"), c.get("phone"), c.get("email"), *(c.get("links") or [])] if b]


def _contact_join(bits, sep="  •  "):
    return e(sep).join(_a(b) for b in bits if b)


def _skill_groups(skills):
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


def _languages(resume):
    out = []
    for l in (resume.get("languages") or []):
        if isinstance(l, dict) and l.get("name"):
            out.append((l["name"], l.get("level") or ""))
        elif isinstance(l, str) and l.strip():
            out.append((l.strip(), ""))
    return out


def _monogram(name):
    parts = [p for p in (name or "").split() if p[:1].isalpha()]
    if not parts:
        return "•"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _headline(resume):
    h = (resume.get("headline") or "").strip()
    if h:
        return h
    exp = resume.get("experience") or []
    return (exp[0].get("title") if exp and isinstance(exp[0], dict) else "") or ""


# ---- shared HTML fragments -------------------------------------------------
def _ul(bullets, accent):
    if not bullets:
        return ""
    lis = "".join(f"<li>{e(b)}</li>" for b in bullets)
    return f"<ul class='bul'>{lis}</ul>"


def _canon_entries(items, accent):
    """Canonical entries: primary(bold) + topright / secondary(italic) + botright."""
    out = ""
    for primary, topright, secondary, botright, bullets in items:
        sub = ""
        if secondary or botright:
            sub = (f"<div class='er'><span class='esec'>{_a(secondary)}</span>"
                   f"<span class='er2'>{e(botright)}</span></div>")
        out += (f"<div class='entry'><div class='er'><span class='ep'>{e(primary)}</span>"
                f"<span class='ert'>{e(topright)}</span></div>{sub}{_ul(bullets, accent)}</div>")
    return out


def _exp_canon(resume):
    return [(j.get("company") or j.get("title"), j.get("location"),
             j.get("title") if j.get("company") else None, j.get("dates"), j.get("bullets") or [])
            for j in (resume.get("experience") or [])]


def _proj_canon(resume):
    return [(p.get("name"), p.get("dates"), p.get("link"), None, p.get("bullets") or [])
            for p in (resume.get("projects") or [])]


def _edu_canon(resume):
    return [(ed.get("institution") or ed.get("degree"), ed.get("dates"),
             ed.get("degree") if ed.get("institution") else None, None, [])
            for ed in (resume.get("education") or [])]


def _skills_lines(groups):
    return "".join(
        f"<p>{('<b>'+e(g['category'])+':</b> ') if g['category'] else ''}{e(', '.join(g['items']))}</p>"
        for g in groups)


def _skills_side(groups):
    return "".join(
        f"<div class='skg'>{('<div class=skc>'+e(g['category'])+'</div>') if g['category'] else ''}"
        f"<div class='ski'>{e(', '.join(g['items']))}</div></div>" for g in groups)


# ---- per-layout renderers --------------------------------------------------
def _doc(css, body):
    return f"<!doctype html><html><head><meta charset=utf-8><style>{css}</style></head><body>{body}</body></html>"


_BASE = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:%(font)s;font-size:9.6pt;line-height:1.32;color:#2b2e33}
a{color:inherit;text-decoration:none}
.entry{margin-bottom:6px}
.er{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.ep{font-weight:700;color:#16181c}.ert{font-weight:700;white-space:nowrap;flex:none}
.esec{font-style:italic}.er2{font-style:italic;font-size:8.8pt;color:#666;white-space:nowrap;flex:none}
ul.bul{list-style:none;margin:2px 0 0}ul.bul li{position:relative;padding-left:12px;margin-bottom:2px}
ul.bul li::before{content:'';position:absolute;left:0;top:6px;width:3.5px;height:3.5px;border-radius:50%%;background:%(ac)s}
"""


def _render_single(r, cfg, ac, font):
    base = _BASE % {"font": font, "ac": ac}
    rule = ac if cfg.get("rule") == "accent" else "#000"
    sc = ".smc" if cfg.get("smallcaps") else ""
    head_align = "center" if cfg.get("header") == "center" else "left"
    css = base + f"""
@page{{size:Letter;margin:1.05cm 1.35cm}}
.name{{font-size:21pt;font-weight:700;text-transform:uppercase;letter-spacing:.6px;text-align:{head_align}}}
.hl{{text-align:{head_align};color:{ac};font-weight:600;font-size:10pt;margin-top:1px}}
.ct{{text-align:{head_align};font-size:9pt;color:#222;margin-top:3px}}
.mast{{border-bottom:2px solid #111;padding-bottom:6px;margin-bottom:3px}}
h2{{font-size:11pt;font-weight:700;text-transform:uppercase;letter-spacing:.9px;margin:11px 0 4px;
  padding-bottom:1.5px;border-bottom:1px solid {rule}}}
{sc} h2{{text-transform:none;font-variant:small-caps;letter-spacing:1px}}
.skills p{{margin:1.5px 0}} .lang{{display:inline-block;margin-right:22px}} .lang b{{color:{ac}}}
"""
    groups = _skill_groups(r.get("skills"))
    hl = _headline(r)
    contact = _contact_bits(r.get("contact", {}) or {})
    parts = [f"<div class='mast'><div class='name'>{e(r.get('name'))}</div>"
             f"{('<div class=hl>'+e(hl)+'</div>') if hl else ''}"
             f"{('<div class=ct>'+_contact_join(contact)+'</div>') if contact else ''}</div>"]
    if r.get("summary"):
        parts.append(f"<h2>Summary</h2><p>{e(r['summary'])}</p>")
    if groups:
        parts.append(f"<h2>Skills</h2><div class='skills'>{_skills_lines(groups)}</div>")
    if r.get("experience"):
        parts.append(f"<h2>Experience</h2>{_canon_entries(_exp_canon(r), ac)}")
    if r.get("projects"):
        parts.append(f"<h2>Projects</h2>{_canon_entries(_proj_canon(r), ac)}")
    if r.get("education"):
        parts.append(f"<h2>Education</h2>{_canon_entries(_edu_canon(r), ac)}")
    if r.get("certifications"):
        parts.append(f"<h2>Certifications</h2>{_ul(r['certifications'], ac)}")
    langs = _languages(r)
    if langs:
        parts.append("<h2>Languages</h2><div>" + "".join(
            f"<span class='lang'>{e(n)} <b>{e(lv)}</b></span>" for n, lv in langs) + "</div>")
    return _doc(css, "".join(parts))


def _render_sidebar(r, cfg, ac, font):
    """Existing divider two-column (skills/edu left, body right)."""
    base = _BASE % {"font": font, "ac": ac}
    css = base + f"""
@page{{size:Letter;margin:1.1cm 1.3cm}}
.mast .name{{font-size:20pt;font-weight:700;text-transform:uppercase;letter-spacing:.5px}}
.mast .hl{{color:{ac};font-weight:600;font-size:9.5pt}}
.sheet{{display:table;width:100%;table-layout:fixed;margin-top:8px}}
.side{{display:table-cell;width:32%;vertical-align:top;padding-right:15px;border-right:1px solid #cfd2d7}}
.body{{display:table-cell;vertical-align:top;padding-left:16px}}
h2{{font-size:10.5pt;font-weight:700;text-transform:uppercase;letter-spacing:.9px;color:{ac};
  margin:11px 0 4px;padding-bottom:1.5px;border-bottom:2px solid {ac}}}
.side h2:first-child{{margin-top:0}}
.ci{{font-size:8.6pt;margin-bottom:3px;word-break:break-word;color:#444}}
.skg{{margin-bottom:4px}}.skc{{font-weight:700;font-size:9pt;color:#222}}.ski{{font-size:8.6pt;color:#555}}
.lang{{font-size:8.7pt;margin-bottom:2px}} .lang b{{color:{ac}}}
"""
    groups = _skill_groups(r.get("skills"))
    hl = _headline(r)
    contact = _contact_bits(r.get("contact", {}) or {})
    langs = _languages(r)
    side = [f"<h2>Contact</h2>" + "".join(f"<div class=ci>{_a(c)}</div>" for c in contact)]
    if groups:
        side.append("<h2>Skills</h2>" + _skills_side(groups))
    if r.get("education"):
        side.append("<h2>Education</h2>" + "".join(
            f"<div class=skg><div class=skc>{e(ed.get('institution') or ed.get('degree'))}</div>"
            f"<div class=ski>{e(ed.get('degree') if ed.get('institution') else '')}{(' · '+e(ed.get('dates'))) if ed.get('dates') else ''}</div></div>"
            for ed in r["education"]))
    if langs:
        side.append("<h2>Languages</h2>" + "".join(f"<div class=lang>{e(n)} <b>{e(lv)}</b></div>" for n, lv in langs))
    if r.get("certifications"):
        side.append("<h2>Certifications</h2>" + "".join(f"<div class=ci>• {e(c)}</div>" for c in r["certifications"]))
    body = []
    if r.get("summary"):
        body.append(f"<h2>Summary</h2><p>{e(r['summary'])}</p>")
    if r.get("experience"):
        body.append(f"<h2>Experience</h2>{_canon_entries(_exp_canon(r), ac)}")
    if r.get("projects"):
        body.append(f"<h2>Projects</h2>{_canon_entries(_proj_canon(r), ac)}")
    mast = (f"<div class='mast'><div class='name'>{e(r.get('name'))}</div>"
            f"{('<div class=hl>'+e(hl)+'</div>') if hl else ''}</div>")
    html = f"{mast}<div class='sheet'><aside class='side'>{''.join(side)}</aside><main class='body'>{''.join(body)}</main></div>"
    return _doc(css, html)


def _render_timeline(r, cfg, ac, font):
    """Skyline: date column + vertical line + dots, teal accents, monogram, cloud bg."""
    base = _BASE % {"font": font, "ac": ac}
    css = base + f"""
@page{{size:Letter;margin:0}}
body{{background:linear-gradient(170deg,#eef5f6 0%,#fbfdfd 16%,#fff 60%,#fdf3ec 100%)}}
.pad{{padding:0.5in 0.6in}}
.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}}
.name{{font-size:24pt;font-weight:800;color:#222b33;letter-spacing:.4px}}
.hl{{color:{ac};font-weight:700;font-size:9.8pt;margin-top:3px}}
.ct{{margin-top:8px;display:flex;flex-wrap:wrap;gap:3px 16px;font-size:8.6pt;color:#444}}
.ct span{{white-space:nowrap}}
.mono{{width:70px;height:70px;border-radius:50%;background:{ac};color:#fff;font-size:21pt;font-weight:800;
  display:flex;align-items:center;justify-content:center;flex:none}}
h2{{font-size:11.5pt;font-weight:800;text-transform:uppercase;letter-spacing:.6px;color:#222b33;
  border-bottom:1.5px solid #d6e3e0;padding-bottom:3px;margin:13px 0 7px}}
.row{{display:flex;align-items:stretch}}
.when{{width:1.4in;flex:none;padding-right:12px;padding-top:1px}}
.dt{{font-weight:700;font-size:8.6pt;color:#3a3f45}}.lo{{font-size:8.1pt;color:#9098a0}}
.bd{{border-left:2px solid #cfe2dd;padding-left:18px;position:relative;padding-bottom:9px;flex:1;min-width:0}}
.bd::before{{content:'';position:absolute;left:-6px;top:3px;width:9px;height:9px;border-radius:50%;background:{ac};border:2px solid #fff}}
.ti{{font-weight:600;font-size:10.2pt;color:#262b30}}.og{{font-weight:700;font-size:9.2pt;color:{ac};margin-bottom:2px}}
.sk-top{{color:{ac};font-size:9.1pt;margin-bottom:5px}}.sk p{{margin:1.5px 0}}.sk b{{color:#262b30}}
.langs span{{display:inline-block;margin-right:28px}}.langs b{{color:{ac}}}
.certs span{{display:inline-block;margin-right:36px;font-weight:600;color:#2c3035}}
"""

    def tl(items):
        out = "<div class='tl'>"
        for when, loc, title, org, bullets in items:
            out += (f"<div class='row'><div class='when'><div class='dt'>{e(when)}</div>"
                    f"<div class='lo'>{e(loc)}</div></div><div class='bd'><div class='ti'>{e(title)}</div>"
                    f"<div class='og'>{e(org)}</div>{_ul(bullets, ac)}</div></div>")
        return out + "</div>"
    exp = [(j.get("dates"), j.get("location"), j.get("title"), j.get("company"), j.get("bullets") or [])
           for j in (r.get("experience") or [])]
    edu = [(ed.get("dates"), "", ed.get("degree"), ed.get("institution"), []) for ed in (r.get("education") or [])]
    groups = _skill_groups(r.get("skills"))
    hl = _headline(r)
    contact = _contact_bits(r.get("contact", {}) or {})
    langs = _languages(r)
    parts = [f"<div class='top'><div><div class='name'>{e(r.get('name'))}</div>"
             f"{('<div class=hl>'+e(hl)+'</div>') if hl else ''}"
             f"<div class='ct'>{''.join('<span>'+_a(c)+'</span>' for c in contact)}</div></div>"
             f"<div class='mono'>{e(_monogram(r.get('name')))}</div></div>"]
    if r.get("summary"):
        parts.append(f"<h2>Summary</h2><p>{e(r['summary'])}</p>")
    if exp:
        parts.append(f"<h2>Experience</h2>{tl(exp)}")
    if edu:
        parts.append(f"<h2>Education</h2>{tl(edu)}")
    if groups:
        top = " | ".join(groups[0]["items"][:8]) if groups else ""
        parts.append(f"<h2>Skills</h2><div class='sk'><div class='sk-top'>{e(top)}</div>{_skills_lines(groups)}</div>")
    if r.get("certifications"):
        parts.append("<h2>Certifications</h2><div class='certs'>" + "".join(f"<span>{e(c)}</span>" for c in r["certifications"]) + "</div>")
    if langs:
        parts.append("<h2>Languages</h2><div class='langs'>" + "".join(f"<span>{e(n)} <b>{e(lv)}</b></span>" for n, lv in langs) + "</div>")
    return _doc(css, f"<div class='pad'>{''.join(parts)}</div>")


def _render_solid_sidebar(r, cfg, ac, font, side_bg):
    """Executive: dark right sidebar (contact/skills/langs/certs) + main left."""
    base = _BASE % {"font": font, "ac": ac}
    css = base + f"""
@page{{size:Letter;margin:0}}
/* Fixed full-height band repeats on every page, so overflow pages show a
   clean colour band (never a half-empty box). Content flows over it. */
.sidebar-bg{{position:fixed;top:0;bottom:0;right:0;width:2.55in;background:{side_bg}}}
.wrap{{display:table;width:100%;table-layout:fixed}}
.main{{display:table-cell;vertical-align:top;padding:0.55in 0.4in 0.55in 0.55in}}
.aside{{display:table-cell;width:2.55in;vertical-align:top;color:#dfe6f0;padding:0.55in 0.34in}}
.name{{font-size:23pt;font-weight:800;color:{side_bg};letter-spacing:1px;text-transform:uppercase}}
.hl{{color:{ac};font-weight:700;font-size:10pt;margin:2px 0 8px}}
.main h2{{font-size:11pt;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:{side_bg};
  border-bottom:2px solid {ac};padding-bottom:2px;margin:14px 0 6px}}
.aside h2{{font-size:10pt;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;color:#fff;
  border-bottom:1px solid rgba(255,255,255,.25);padding-bottom:3px;margin:15px 0 6px}}
.aside h2:first-child{{margin-top:0}}
.ci{{font-size:8.6pt;margin-bottom:3px;word-break:break-word}}
.skg{{margin-bottom:5px}}.skc{{font-weight:700;color:#fff;font-size:8.9pt}}.ski{{font-size:8.5pt;color:#c4cee0}}
.ep,.ert{{color:#1c2330}}.esec{{color:#333}}.og{{color:{ac};font-weight:600;font-size:9pt;margin-bottom:2px}}
"""
    groups = _skill_groups(r.get("skills"))
    hl = _headline(r); contact = _contact_bits(r.get("contact", {}) or {}); langs = _languages(r)
    main = [f"<div class='name'>{e(r.get('name'))}</div>{('<div class=hl>'+e(hl)+'</div>') if hl else ''}"]
    if r.get("summary"):
        main.append(f"<h2>Summary</h2><p>{e(r['summary'])}</p>")
    if r.get("experience"):
        main.append(f"<h2>Experience</h2>{_canon_entries(_exp_canon(r), ac)}")
    if r.get("projects"):
        main.append(f"<h2>Projects</h2>{_canon_entries(_proj_canon(r), ac)}")
    if r.get("education"):
        main.append(f"<h2>Education</h2>{_canon_entries(_edu_canon(r), ac)}")
    aside = ["<h2>Contact</h2>" + "".join(f"<div class=ci>{_a(c)}</div>" for c in contact)]
    if groups:
        aside.append("<h2>Skills</h2>" + _skills_side(groups))
    if r.get("certifications"):
        aside.append("<h2>Certifications</h2>" + "".join(f"<div class=ci>• {e(c)}</div>" for c in r["certifications"]))
    if langs:
        aside.append("<h2>Languages</h2>" + "".join(f"<div class=ci>{e(n)} — {e(lv)}</div>" for n, lv in langs))
    return _doc(css, f"<div class='sidebar-bg'></div><div class='wrap'><main class='main'>{''.join(main)}</main><aside class='aside'>{''.join(aside)}</aside></div>")


def _render_tint_sidebar(r, cfg, ac, font):
    """Aurora: tinted left sidebar with monogram + contact/skills/langs/certs."""
    base = _BASE % {"font": font, "ac": ac}
    css = base + f"""
@page{{size:Letter;margin:0}}
/* Fixed full-height tint band repeats on every page (clean overflow pages). */
.sidebar-bg{{position:fixed;top:0;bottom:0;left:0;width:2.5in;background:{ac}14;border-right:3px solid {ac}}}
.wrap{{display:table;width:100%;table-layout:fixed}}
.aside{{display:table-cell;width:2.5in;vertical-align:top;padding:0.5in 0.32in}}
.main{{display:table-cell;vertical-align:top;padding:0.5in 0.45in}}
.mono{{width:66px;height:66px;border-radius:50%;background:{ac};color:#fff;font-size:20pt;font-weight:800;
  display:flex;align-items:center;justify-content:center;margin-bottom:10px}}
.aside h2{{font-size:9.5pt;font-weight:800;text-transform:uppercase;letter-spacing:1.2px;color:{ac};
  border-bottom:1px solid {ac}55;padding-bottom:3px;margin:14px 0 5px}}
.ci{{font-size:8.6pt;margin-bottom:3px;word-break:break-word;color:#3a4540}}
.skg{{margin-bottom:5px}}.skc{{font-weight:700;color:#283330;font-size:8.9pt}}.ski{{font-size:8.5pt;color:#4a5752}}
.name{{font-size:23pt;font-weight:800;color:#1f2a28;letter-spacing:.4px}}
.hl{{color:{ac};font-weight:700;font-size:10pt;margin:2px 0 4px}}
.main h2{{font-size:11pt;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:#283330;
  border-bottom:2px solid {ac};padding-bottom:2px;margin:13px 0 6px}}
.og{{color:{ac};font-weight:600;font-size:9pt;margin-bottom:2px}}
"""
    groups = _skill_groups(r.get("skills"))
    hl = _headline(r); contact = _contact_bits(r.get("contact", {}) or {}); langs = _languages(r)
    aside = [f"<div class='mono'>{e(_monogram(r.get('name')))}</div>",
             "<h2>Contact</h2>" + "".join(f"<div class=ci>{_a(c)}</div>" for c in contact)]
    if groups:
        aside.append("<h2>Skills</h2>" + _skills_side(groups))
    if langs:
        aside.append("<h2>Languages</h2>" + "".join(f"<div class=ci>{e(n)} — {e(lv)}</div>" for n, lv in langs))
    if r.get("certifications"):
        aside.append("<h2>Certifications</h2>" + "".join(f"<div class=ci>• {e(c)}</div>" for c in r["certifications"]))
    main = [f"<div class='name'>{e(r.get('name'))}</div>{('<div class=hl>'+e(hl)+'</div>') if hl else ''}"]
    if r.get("summary"):
        main.append(f"<h2>Summary</h2><p>{e(r['summary'])}</p>")
    if r.get("experience"):
        main.append(f"<h2>Experience</h2>{_canon_entries(_exp_canon(r), ac)}")
    if r.get("projects"):
        main.append(f"<h2>Projects</h2>{_canon_entries(_proj_canon(r), ac)}")
    if r.get("education"):
        main.append(f"<h2>Education</h2>{_canon_entries(_edu_canon(r), ac)}")
    return _doc(css, f"<div class='sidebar-bg'></div><div class='wrap'><aside class='aside'>{''.join(aside)}</aside><main class='main'>{''.join(main)}</main></div>")


def _render_banner(r, cfg, ac, font):
    """Spotlight: coloured header band + monogram, single-column body."""
    base = _BASE % {"font": font, "ac": ac}
    css = base + f"""
@page{{size:Letter;margin:0}}
.band{{background:{ac};color:#fff;padding:0.42in 0.55in;display:flex;justify-content:space-between;align-items:center;gap:16px}}
.band .name{{font-size:24pt;font-weight:800;letter-spacing:.5px}}
.band .hl{{color:#ffffffd0;font-weight:600;font-size:10pt;margin-top:2px}}
.band .ct{{font-size:8.7pt;color:#ffffffcc;margin-top:7px}}
.mono{{width:74px;height:74px;border-radius:50%;background:#fff;color:{ac};font-size:23pt;font-weight:800;
  display:flex;align-items:center;justify-content:center;flex:none}}
.body{{padding:0.34in 0.55in}}
h2{{font-size:11pt;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:{ac};
  border-bottom:2px solid {ac}40;padding-bottom:2px;margin:13px 0 6px}}
h2:first-child{{margin-top:0}}
.og{{color:{ac};font-weight:600;font-size:9pt;margin-bottom:2px}}
.skills p{{margin:1.5px 0}} .langs span{{display:inline-block;margin-right:28px}} .langs b{{color:{ac}}}
"""
    groups = _skill_groups(r.get("skills"))
    hl = _headline(r); contact = _contact_bits(r.get("contact", {}) or {}); langs = _languages(r)
    band = (f"<div class='band'><div><div class='name'>{e(r.get('name'))}</div>"
            f"{('<div class=hl>'+e(hl)+'</div>') if hl else ''}"
            f"{('<div class=ct>'+_contact_join(contact)+'</div>') if contact else ''}</div>"
            f"<div class='mono'>{e(_monogram(r.get('name')))}</div></div>")
    body = []
    if r.get("summary"):
        body.append(f"<h2>Summary</h2><p>{e(r['summary'])}</p>")
    if r.get("experience"):
        body.append(f"<h2>Experience</h2>{_canon_entries(_exp_canon(r), ac)}")
    if groups:
        body.append(f"<h2>Skills</h2><div class='skills'>{_skills_lines(groups)}</div>")
    if r.get("projects"):
        body.append(f"<h2>Projects</h2>{_canon_entries(_proj_canon(r), ac)}")
    if r.get("education"):
        body.append(f"<h2>Education</h2>{_canon_entries(_edu_canon(r), ac)}")
    if r.get("certifications"):
        body.append(f"<h2>Certifications</h2>{_ul(r['certifications'], ac)}")
    if langs:
        body.append("<h2>Languages</h2><div class='langs'>" + "".join(f"<span>{e(n)} <b>{e(lv)}</b></span>" for n, lv in langs) + "</div>")
    return _doc(css, band + f"<div class='body'>{''.join(body)}</div>")


_LAYOUTS = {
    "single": _render_single, "sidebar": _render_sidebar, "timeline": _render_timeline,
    "sidebar_solid": lambda r, c, ac, f: _render_solid_sidebar(r, c, ac, f, c.get("side_bg", "#23344d")),
    "sidebar_tint": _render_tint_sidebar, "banner": _render_banner,
}


def render_resume_pdf(resume, out_path, style=None):
    if not _WEASY_OK:
        return None
    style = style or {}
    name = (style.get("template") or DEFAULT_TEMPLATE).lower()
    cfg = TEMPLATES.get(name, TEMPLATES[DEFAULT_TEMPLATE])
    # Rich templates use their curated colour unless the user changed the swatch.
    picked = (style.get("accent") or "").lstrip("#")
    if cfg.get("default") and (not picked or picked == _APP_DEFAULT_ACCENT):
        ac = cfg["default"]
    else:
        ac = _norm_accent(style.get("accent"), cfg.get("default", "#c8462e"))
    fam = FONT_OVERRIDES.get(style.get("font") or "", cfg.get("family", "sans"))
    font = FONT_STACKS[fam]
    render = _LAYOUTS.get(cfg["layout"], _render_single)
    try:
        HTML(string=render(resume, cfg, ac, font)).write_pdf(out_path)
    except Exception:
        return None
    return out_path if os.path.exists(out_path) else None
