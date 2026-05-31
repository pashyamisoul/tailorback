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

def _base():
    doc = Document(); s = doc.sections[0]
    s.page_width = Inches(8.5); s.page_height = Inches(11)
    s.top_margin = Inches(0.6); s.bottom_margin = Inches(0.6)
    s.left_margin = Inches(0.85); s.right_margin = Inches(0.85)
    n = doc.styles["Normal"]; n.font.name = "Calibri"
    n.font.size = Pt(10.5); n.font.color.rgb = DARK
    n.paragraph_format.space_after = Pt(0); n.paragraph_format.line_spacing = 1.1
    return doc

def _heading(doc, text):
    p = doc.add_paragraph(); _sp(p, before=12, after=5)
    r = p.add_run(text.upper()); r.bold = True; r.font.size = Pt(10.5)
    r.font.color.rgb = ACCENT; _track(r, 60)
    _border(p, size=4, color="999999")
    return p

def build_resume(resume, out_path):
    doc = _base()
    name_p = doc.add_paragraph(); name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _sp(name_p, after=3)
    nr = name_p.add_run((resume.get("name","") or "").upper())
    nr.bold = True; nr.font.size = Pt(23); nr.font.color.rgb = BLACK; _track(nr, 80)

    c = resume.get("contact", {}) or {}
    bits = [c.get("email"), c.get("phone"), c.get("location"), *(c.get("links") or [])]
    contact = "    \u2022    ".join(b for b in bits if b)
    if contact:
        cp = doc.add_paragraph(); cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _sp(cp, after=6); cr = cp.add_run(contact)
        cr.font.size = Pt(9); cr.font.color.rgb = GREY
        _border(cp, size=8, color="000000")

    if resume.get("summary"):
        _heading(doc, "Professional Summary")
        p = doc.add_paragraph(resume["summary"]); _sp(p, after=2, line=1.12)

    if resume.get("skills"):
        _heading(doc, "Core Competencies")
        p = doc.add_paragraph(); _sp(p, after=2, line=1.2)
        p.add_run(" \u2022 ".join(resume["skills"]))

    if resume.get("experience"):
        _heading(doc, "Professional Experience")
        for job in resume["experience"]:
            head = doc.add_paragraph(); _sp(head, before=7, after=1)
            head.paragraph_format.tab_stops.add_tab_stop(Inches(6.8), WD_TAB_ALIGNMENT.RIGHT)
            r = head.add_run(job.get("title","")); r.bold = True; r.font.size = Pt(10.5)
            if job.get("company"):
                cr = head.add_run(f"   |   {job['company']}"); cr.bold = True
                cr.font.size = Pt(10.5); cr.font.color.rgb = ACCENT
            if job.get("dates"):
                d = head.add_run(f"\t{job['dates']}"); d.italic = True
                d.font.size = Pt(9); d.font.color.rgb = GREY
            for b in job.get("bullets", []):
                bp = doc.add_paragraph(b, style="List Bullet"); _sp(bp, after=2, line=1.08)

    if resume.get("education"):
        _heading(doc, "Education")
        for ed in resume["education"]:
            p = doc.add_paragraph(); _sp(p, before=2, after=1)
            p.paragraph_format.tab_stops.add_tab_stop(Inches(6.8), WD_TAB_ALIGNMENT.RIGHT)
            p.add_run(ed.get("degree","")).bold = True
            if ed.get("institution"):
                ir = p.add_run(f" \u2014 {ed['institution']}"); ir.font.color.rgb = GREY
            if ed.get("dates"):
                dr = p.add_run(f"\t{ed['dates']}"); dr.italic = True
                dr.font.size = Pt(9); dr.font.color.rgb = GREY

    if resume.get("certifications"):
        _heading(doc, "Certifications")
        for cert in resume["certifications"]:
            cp = doc.add_paragraph(cert, style="List Bullet"); _sp(cp, after=2, line=1.08)
    doc.save(out_path); return out_path

def build_cover_letter(letter, applicant_name, out_path, contact_line="", links=None):
    doc = _base()
    doc.styles["Normal"].paragraph_format.line_spacing = 1.2
    if applicant_name:
        n = doc.add_paragraph(); _sp(n, after=2)
        nr = n.add_run(applicant_name.upper()); nr.bold = True
        nr.font.size = Pt(19); _track(nr, 60)
    line_bits = []
    if contact_line: line_bits.append(contact_line)
    if links: line_bits.extend(links)
    if line_bits:
        cp = doc.add_paragraph(); _sp(cp, after=6)
        cr = cp.add_run("   \u2022   ".join(line_bits)); cr.font.size = Pt(9)
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
