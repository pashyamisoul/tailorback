import os, datetime
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BLACK = RGBColor(0, 0, 0)
DARK = RGBColor(0x1a, 0x1a, 0x1a)
GREY = RGBColor(0x5a, 0x5a, 0x5a)
ACCENT = RGBColor(0x2b, 0x2b, 0x2b)
WHITE = RGBColor(0xff, 0xff, 0xff)
BANNER_BG = "1a1a1a"          # dark banner fill for the "bold" template
BANNER_CONTACT = RGBColor(0xcf, 0xca, 0xbf)

# ---------------------------------------------------------------------------
# Style system
# ---------------------------------------------------------------------------
# A "style" dict drives the look of the exported documents so the UI can offer
# a template gallery + accent / font / density controls. Everything is optional;
# an empty/None style reproduces the original editorial look exactly.
#
#   style = {
#     "template": "editorial" | "modern" | "classic" | "compact",
#     "accent":   "c8462e"  (hex, with or without leading '#'),
#     "font":     "Calibri" | "Georgia" | "Arial" | ...,
#     "density":  "comfortable" | "compact",
#   }

TEMPLATES = {
    "editorial": {
        "font": "Calibri",
        "name_align": WD_ALIGN_PARAGRAPH.CENTER,
        "name_size": 23,
        "name_color": BLACK,
        "heading_uses_accent": False,   # headings stay dark grey
        "heading_border": "999999",
        "heading_border_accent": False,
        "contact_align": WD_ALIGN_PARAGRAPH.CENTER,
    },
    "modern": {
        "font": "Calibri",
        "name_align": WD_ALIGN_PARAGRAPH.LEFT,
        "name_size": 21,
        "name_color": None,             # filled with accent at resolve time
        "name_uses_accent": True,
        "heading_uses_accent": True,
        "heading_border": None,         # filled with accent
        "heading_border_accent": True,
        "contact_align": WD_ALIGN_PARAGRAPH.LEFT,
    },
    "classic": {
        "font": "Georgia",
        "name_align": WD_ALIGN_PARAGRAPH.CENTER,
        "name_size": 22,
        "name_color": BLACK,
        "heading_uses_accent": False,
        "heading_color": BLACK,
        "heading_border": "000000",
        "heading_border_accent": False,
        "contact_align": WD_ALIGN_PARAGRAPH.CENTER,
    },
    "compact": {
        "font": "Calibri",
        "name_align": WD_ALIGN_PARAGRAPH.LEFT,
        "name_size": 18,
        "name_color": None,
        "name_uses_accent": True,
        "heading_uses_accent": True,
        "heading_border": None,
        "heading_border_accent": True,
        "contact_align": WD_ALIGN_PARAGRAPH.LEFT,
    },
    "serif": {
        "font": "Georgia",
        "name_align": WD_ALIGN_PARAGRAPH.CENTER,
        "name_size": 22,
        "name_color": BLACK,
        "heading_uses_accent": False,
        "heading_color": BLACK,
        "heading_border": "000000",
        "heading_border_accent": False,
        "contact_align": WD_ALIGN_PARAGRAPH.CENTER,
        "heading_align": WD_ALIGN_PARAGRAPH.CENTER,
        "heading_small_caps": True,
    },
    "bold": {
        "font": "Calibri",
        "name_align": WD_ALIGN_PARAGRAPH.LEFT,
        "name_size": 22,
        "name_color": WHITE,            # white on the dark banner
        "heading_uses_accent": True,
        "heading_border": None,
        "heading_border_accent": True,
        "contact_align": WD_ALIGN_PARAGRAPH.LEFT,
        "banner": True,                 # render name + contact in a dark banner
    },
    "minimal": {
        "font": "Calibri",
        "name_align": WD_ALIGN_PARAGRAPH.LEFT,
        "name_size": 19,
        "name_color": BLACK,
        "heading_uses_accent": False,
        "heading_color": RGBColor(0x22, 0x22, 0x22),
        "heading_border": "dddddd",     # hairline rule
        "heading_border_accent": False,
        "contact_align": WD_ALIGN_PARAGRAPH.LEFT,
    },
    "sidebar": {
        "font": "Calibri",
        "name_align": WD_ALIGN_PARAGRAPH.LEFT,
        "name_size": 18,
        "name_color": BLACK,
        "heading_uses_accent": True,
        "heading_border": None,
        "heading_border_accent": True,
        "contact_align": WD_ALIGN_PARAGRAPH.LEFT,
        "sidebar": True,                # two-column table layout
    },
}

SIDEBAR_BG = "f3efe6"                    # tinted left column

DEFAULT_TEMPLATE = "editorial"


def _rgb(hexstr, fallback=ACCENT):
    hexstr = (hexstr or "").lstrip("#")
    if len(hexstr) != 6:
        return fallback
    try:
        return RGBColor(int(hexstr[0:2], 16), int(hexstr[2:4], 16), int(hexstr[4:6], 16))
    except ValueError:
        return fallback


def _resolve_style(style):
    style = style or {}
    name = (style.get("template") or DEFAULT_TEMPLATE).lower()
    tmpl = TEMPLATES.get(name, TEMPLATES[DEFAULT_TEMPLATE])
    accent = _rgb(style.get("accent"), fallback=RGBColor(0xc8, 0x46, 0x2e))
    font = style.get("font") or tmpl["font"]
    density = (style.get("density") or "comfortable").lower()
    scale = 0.78 if density == "compact" else 1.0
    base_size = 9.5 if density == "compact" else 10.5

    name_color = tmpl.get("name_color")
    if tmpl.get("name_uses_accent"):
        name_color = accent
    if name_color is None:
        name_color = BLACK

    heading_color = accent if tmpl.get("heading_uses_accent") else tmpl.get("heading_color", ACCENT)
    heading_border = tmpl.get("heading_border")
    if tmpl.get("heading_border_accent"):
        heading_border = "%02x%02x%02x" % (accent[0], accent[1], accent[2])
    heading_border = heading_border or "999999"

    return {
        "template": name,
        "font": font,
        "accent": accent,
        "scale": scale,
        "base_size": base_size,
        "name_align": tmpl["name_align"],
        "name_size": tmpl["name_size"] * (0.85 if density == "compact" else 1.0),
        "name_color": name_color,
        "heading_color": heading_color,
        "heading_border": heading_border,
        "contact_align": tmpl["contact_align"],
        "heading_align": tmpl.get("heading_align", WD_ALIGN_PARAGRAPH.LEFT),
        "heading_small_caps": tmpl.get("heading_small_caps", False),
        "banner": tmpl.get("banner", False),
        "sidebar": tmpl.get("sidebar", False),
    }


def _sp(p, before=0, after=0, line=None):
    pf = p.paragraph_format
    pf.space_before = Pt(before); pf.space_after = Pt(after)
    if line is not None: pf.line_spacing = line

def _border(p, size=6, color="000000", val="single"):
    el = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr"); b = OxmlElement("w:bottom")
    b.set(qn("w:val"), val); b.set(qn("w:sz"), str(size))
    b.set(qn("w:space"), "3"); b.set(qn("w:color"), color)
    pBdr.append(b); el.append(pBdr)

def _track(run, val):
    rPr = run._element.get_or_add_rPr(); s = OxmlElement("w:spacing")
    s.set(qn("w:val"), str(val)); rPr.append(s)

def _shade(cell, fill):
    """Fill a table cell with a solid background colour."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)

def _no_table_borders(table):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + edge); e.set(qn("w:val"), "none"); e.set(qn("w:sz"), "0")
        borders.append(e)
    tblPr.append(borders)

def _banner(doc, name, contact, S):
    """Dark full-width banner holding the name + contact (the 'bold' template)."""
    table = doc.add_table(rows=1, cols=1)
    _no_table_borders(table)
    cell = table.cell(0, 0)
    _shade(cell, BANNER_BG)
    # tighten cell margins a touch via the single paragraph spacing
    np = cell.paragraphs[0]; _sp(np, before=2, after=2)
    nr = np.add_run((name or "").upper())
    nr.bold = True; nr.font.size = Pt(S["name_size"]); nr.font.color.rgb = WHITE; _track(nr, 60)
    if contact:
        cp = cell.add_paragraph(); _sp(cp, before=2, after=2)
        cr = cp.add_run(contact); cr.font.size = Pt(9); cr.font.color.rgb = BANNER_CONTACT
    doc.add_paragraph()  # spacer below the banner

def _set_cell_width(cell, width):
    cell.width = width
    tcPr = cell._tc.get_or_add_tcPr()
    for existing in tcPr.findall(qn("w:tcW")):
        tcPr.remove(existing)
    tcW = OxmlElement("w:tcW")
    tcW.set(qn("w:w"), str(int(width.twips))); tcW.set(qn("w:type"), "dxa")
    tcPr.append(tcW)

def _cell_margins(cell, top=80, bottom=80, left=140, right=140):
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for edge, val in (("top", top), ("bottom", bottom), ("start", left), ("end", right),
                      ("left", left), ("right", right)):
        m = OxmlElement("w:" + edge); m.set(qn("w:w"), str(val)); m.set(qn("w:type"), "dxa")
        mar.append(m)
    tcPr.append(mar)

def _cell_heading(cell, text, S, first=False):
    p = cell.add_paragraph(); _sp(p, before=0 if first else 10, after=3)
    r = p.add_run(text.upper()); r.bold = True; r.font.size = Pt(S["base_size"])
    r.font.color.rgb = S["heading_color"]; _track(r, 50)
    return p

def _job_into(container, job, S, name_key="title", link_key="company"):
    head = container.add_paragraph(); _sp(head, before=6, after=1)
    r = head.add_run(job.get(name_key, "")); r.bold = True; r.font.size = Pt(S["base_size"])
    sub = job.get(link_key)
    if sub:
        sr = head.add_run(f"  ·  {sub}"); sr.font.size = Pt(S["base_size"]); sr.font.color.rgb = S["heading_color"]
    if job.get("dates"):
        d = head.add_run(f"   {job['dates']}"); d.italic = True; d.font.size = Pt(8.5); d.font.color.rgb = GREY
    for b in (job.get("bullets") or []):
        bp = container.add_paragraph(b, style="List Bullet"); _sp(bp, after=2, line=1.08)

def _build_resume_sidebar(resume, doc, S):
    """Two-column layout: tinted left column (contact, skills, education),
    main right column (summary, experience, projects). Rendered as a borderless
    table so it survives the .docx/PDF pipeline."""
    table = doc.add_table(rows=1, cols=2)
    table.allow_autofit = False
    _no_table_borders(table)
    left, right = table.cell(0, 0), table.cell(0, 1)
    _set_cell_width(left, Inches(2.35)); _set_cell_width(right, Inches(4.45))
    _shade(left, SIDEBAR_BG)
    _cell_margins(left); _cell_margins(right, left=160, right=80)

    c = resume.get("contact", {}) or {}

    # ---- left column ----
    np = left.paragraphs[0]; _sp(np, after=4)
    nr = np.add_run((resume.get("name", "") or "").upper())
    nr.bold = True; nr.font.size = Pt(S["name_size"]); nr.font.color.rgb = BLACK; _track(nr, 30)
    for v in [c.get("email"), c.get("phone"), c.get("location"), *(c.get("links") or [])]:
        if v:
            cp = left.add_paragraph(); _sp(cp, after=1)
            cr = cp.add_run(v); cr.font.size = Pt(8.5); cr.font.color.rgb = GREY

    if resume.get("skills"):
        _cell_heading(left, "Skills", S)
        for s in resume["skills"]:
            sp = left.add_paragraph(); _sp(sp, after=1)
            sr = sp.add_run(s); sr.font.size = Pt(S["base_size"])

    if resume.get("education"):
        _cell_heading(left, "Education", S)
        for e in resume["education"]:
            ep = left.add_paragraph(); _sp(ep, after=3)
            ep.add_run(e.get("degree", "")).bold = True
            if e.get("institution"):
                ir = ep.add_run(f"\n{e['institution']}"); ir.font.size = Pt(9); ir.font.color.rgb = GREY
            if e.get("dates"):
                dr = ep.add_run(f"\n{e['dates']}"); dr.italic = True; dr.font.size = Pt(8.5); dr.font.color.rgb = GREY

    if resume.get("certifications"):
        _cell_heading(left, "Certifications", S)
        for cert in resume["certifications"]:
            cp = left.add_paragraph(); _sp(cp, after=2)
            cp.add_run(cert).font.size = Pt(9)

    # ---- right column ----
    first = True
    if resume.get("summary"):
        _cell_heading(right, "Professional Summary", S, first=True); first = False
        p = right.add_paragraph(resume["summary"]); _sp(p, after=2, line=1.12)
    if resume.get("experience"):
        _cell_heading(right, "Professional Experience", S, first=first); first = False
        for job in resume["experience"]:
            _job_into(right, job, S, "title", "company")
    if resume.get("projects"):
        _cell_heading(right, "Projects", S, first=first)
        for proj in resume["projects"]:
            _job_into(right, proj, S, "name", "link")

def _base(S):
    doc = Document(); s = doc.sections[0]
    s.page_width = Inches(8.5); s.page_height = Inches(11)
    s.top_margin = Inches(0.6); s.bottom_margin = Inches(0.6)
    s.left_margin = Inches(0.85); s.right_margin = Inches(0.85)
    n = doc.styles["Normal"]; n.font.name = S["font"]
    n.font.size = Pt(S["base_size"]); n.font.color.rgb = DARK
    n.paragraph_format.space_after = Pt(0); n.paragraph_format.line_spacing = 1.1
    return doc

def _heading(doc, text, S):
    p = doc.add_paragraph(); _sp(p, before=12 * S["scale"], after=5 * S["scale"])
    p.alignment = S.get("heading_align", WD_ALIGN_PARAGRAPH.LEFT)
    small_caps = S.get("heading_small_caps", False)
    # Small caps needs the original case; otherwise we upper-case for the rule look.
    r = p.add_run(text if small_caps else text.upper())
    r.bold = True; r.font.size = Pt(S["base_size"]); r.font.color.rgb = S["heading_color"]
    if small_caps:
        r.font.small_caps = True
    _track(r, 80 if small_caps else 60)
    _border(p, size=4, color=S["heading_border"])
    return p

def build_resume(resume, out_path, style=None):
    S = _resolve_style(style)
    doc = _base(S)

    if S.get("sidebar"):
        _build_resume_sidebar(resume, doc, S)
        doc.save(out_path); return out_path

    c = resume.get("contact", {}) or {}
    bits = [c.get("email"), c.get("phone"), c.get("location"), *(c.get("links") or [])]
    contact = "    •    ".join(b for b in bits if b)

    if S.get("banner"):
        # Dark full-width banner with name + contact (the "bold" template).
        _banner(doc, resume.get("name", ""), contact, S)
    else:
        name_p = doc.add_paragraph(); name_p.alignment = S["name_align"]
        _sp(name_p, after=3)
        nr = name_p.add_run((resume.get("name","") or "").upper())
        nr.bold = True; nr.font.size = Pt(S["name_size"]); nr.font.color.rgb = S["name_color"]; _track(nr, 80)
        if contact:
            cp = doc.add_paragraph(); cp.alignment = S["contact_align"]
            _sp(cp, after=6); cr = cp.add_run(contact)
            cr.font.size = Pt(9); cr.font.color.rgb = GREY
            _border(cp, size=8, color="000000")

    if resume.get("summary"):
        _heading(doc, "Professional Summary", S)
        p = doc.add_paragraph(resume["summary"]); _sp(p, after=2, line=1.12)

    if resume.get("skills"):
        _heading(doc, "Core Competencies", S)
        p = doc.add_paragraph(); _sp(p, after=2, line=1.2)
        p.add_run(" • ".join(resume["skills"]))

    if resume.get("experience"):
        _heading(doc, "Professional Experience", S)
        for job in resume["experience"]:
            head = doc.add_paragraph(); _sp(head, before=7 * S["scale"], after=1)
            head.paragraph_format.tab_stops.add_tab_stop(Inches(6.8), WD_TAB_ALIGNMENT.RIGHT)
            r = head.add_run(job.get("title","")); r.bold = True; r.font.size = Pt(S["base_size"])
            if job.get("company"):
                cr = head.add_run(f"   |   {job['company']}"); cr.bold = True
                cr.font.size = Pt(S["base_size"]); cr.font.color.rgb = S["heading_color"]
            if job.get("dates"):
                d = head.add_run(f"\t{job['dates']}"); d.italic = True
                d.font.size = Pt(9); d.font.color.rgb = GREY
            for b in job.get("bullets", []):
                bp = doc.add_paragraph(b, style="List Bullet"); _sp(bp, after=2, line=1.08)

    if resume.get("projects"):
        _heading(doc, "Projects", S)
        for proj in resume["projects"]:
            head = doc.add_paragraph(); _sp(head, before=7 * S["scale"], after=1)
            head.paragraph_format.tab_stops.add_tab_stop(Inches(6.8), WD_TAB_ALIGNMENT.RIGHT)
            r = head.add_run(proj.get("name", "")); r.bold = True; r.font.size = Pt(S["base_size"])
            if proj.get("link"):
                lr = head.add_run(f"   |   {proj['link']}")
                lr.font.size = Pt(9); lr.font.color.rgb = S["heading_color"]
            if proj.get("dates"):
                d = head.add_run(f"\t{proj['dates']}"); d.italic = True
                d.font.size = Pt(9); d.font.color.rgb = GREY
            for b in proj.get("bullets", []):
                bp = doc.add_paragraph(b, style="List Bullet"); _sp(bp, after=2, line=1.08)

    if resume.get("education"):
        _heading(doc, "Education", S)
        for ed in resume["education"]:
            p = doc.add_paragraph(); _sp(p, before=2, after=1)
            p.paragraph_format.tab_stops.add_tab_stop(Inches(6.8), WD_TAB_ALIGNMENT.RIGHT)
            p.add_run(ed.get("degree","")).bold = True
            if ed.get("institution"):
                ir = p.add_run(f", {ed['institution']}"); ir.font.color.rgb = GREY
            if ed.get("dates"):
                dr = p.add_run(f"\t{ed['dates']}"); dr.italic = True
                dr.font.size = Pt(9); dr.font.color.rgb = GREY

    if resume.get("certifications"):
        _heading(doc, "Certifications", S)
        for cert in resume["certifications"]:
            cp = doc.add_paragraph(cert, style="List Bullet"); _sp(cp, after=2, line=1.08)
    doc.save(out_path); return out_path

def build_cover_letter(letter, applicant_name, out_path, contact_line="", links=None, style=None):
    S = _resolve_style(style)
    doc = _base(S)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.2
    if applicant_name:
        n = doc.add_paragraph(); n.alignment = S["name_align"]; _sp(n, after=2)
        nr = n.add_run(applicant_name.upper()); nr.bold = True
        nr.font.size = Pt(max(16, S["name_size"] - 4)); nr.font.color.rgb = S["name_color"]; _track(nr, 60)
    line_bits = []
    if contact_line: line_bits.append(contact_line)
    if links: line_bits.extend(links)
    if line_bits:
        cp = doc.add_paragraph(); cp.alignment = S["contact_align"]; _sp(cp, after=6)
        cr = cp.add_run("   •   ".join(line_bits)); cr.font.size = Pt(9)
        cr.font.color.rgb = GREY; _border(cp, size=8, color="000000")
    dp = doc.add_paragraph(); _sp(dp, before=2, after=10)
    dp.add_run(datetime.date.today().strftime("%B %d, %Y")).font.color.rgb = GREY

    g = doc.add_paragraph(letter.get("greeting","Dear Hiring Manager,")); _sp(g, after=9)
    for para in letter.get("body_paragraphs", []):
        bp = doc.add_paragraph(para); _sp(bp, after=9, line=1.2)
    cl = doc.add_paragraph(letter.get("closing","Sincerely,")); _sp(cl, before=4, after=1)
    if applicant_name: doc.add_paragraph(applicant_name)
    doc.save(out_path); return out_path
import shutil, subprocess

def to_pdf(docx_path):
    """Convert a .docx to .pdf via LibreOffice. Returns the pdf path, or None
    if LibreOffice isn't available / conversion fails (caller falls back to docx)."""
    return to_pdfs([docx_path]).get(docx_path)


def to_pdfs(docx_paths):
    """Convert multiple .docx files in one LibreOffice run.

    Starting LibreOffice is the slow part, so batching resume + cover letter
    avoids paying that startup cost twice.
    """
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        soffice = mac if os.path.exists(mac) else None
    if not soffice:
        return {p: None for p in docx_paths}
    if not docx_paths:
        return {}
    outdir = os.path.dirname(docx_paths[0])
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", outdir, *docx_paths],
            check=True, capture_output=True, timeout=60)
    except Exception:
        return {p: None for p in docx_paths}
    out = {}
    for docx_path in docx_paths:
        pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
        out[docx_path] = pdf_path if os.path.exists(pdf_path) else None
    return out
