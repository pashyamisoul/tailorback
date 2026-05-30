"""
ATS Resume & Cover Letter Builder — Flask backend.

Inputs (multipart form):
  Job description (one of):
    jd_text   pasted text
    jd_url    public job-posting link
  CV (one of):
    cv_file   uploaded .pdf / .docx
    cv_text   pasted CV text

Flow: resolve JD -> resolve CV -> analyse JD -> structure CV -> tailor ->
build resume.docx + cover_letter.docx. Any unreadable source returns a
needs_paste signal so the frontend can prompt for paste.
"""
import os
import uuid
from flask import Flask, request, jsonify, render_template, send_from_directory
from dotenv import load_dotenv

from services import cv_parser, jd_source, llm, docx_builder

load_dotenv()

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS = os.path.join(BASE, "uploads")
GENERATED = os.path.join(BASE, "generated")
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(GENERATED, exist_ok=True)

ALLOWED = {".pdf", ".docx", ".doc"}
MAX_BYTES = 8 * 1024 * 1024

app = Flask(__name__,
            template_folder=os.path.join(BASE, "templates"),
            static_folder=os.path.join(BASE, "static"))
app.config["MAX_CONTENT_LENGTH"] = MAX_BYTES


@app.route("/")
def index():
    return render_template("index.html")


def _resolve_jd(form):
    """Return (jd_text, error_field|None)."""
    jd_text = (form.get("jd_text") or "").strip()
    jd_url = (form.get("jd_url") or "").strip()
    if jd_text:
        return jd_text, None
    if jd_url:
        text, ok = jd_source.fetch_jd(jd_url)
        if ok:
            return text, None
        return "", "jd"   # signal: ask user to paste the JD
    return "", "jd_missing"


def _resolve_cv(form, files):
    """Return (cv_text, error_field|None)."""
    cv_text = (form.get("cv_text") or "").strip()
    if cv_text:
        if cv_parser.looks_like_cv(cv_text):
            return cv_text, None
        return "", "cv"
    f = files.get("cv_file")
    if f and f.filename:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED:
            return "", "cv_type"
        path = os.path.join(UPLOADS, f"{uuid.uuid4().hex}{ext}")
        f.save(path)
        try:
            text, ok = cv_parser.parse_cv(path)
        finally:
            os.remove(path)  # don't retain the user's file longer than needed
        if ok:
            return text, None
        return "", "cv"   # signal: ask user to paste the CV
    return "", "cv_missing"


@app.route("/api/generate", methods=["POST"])
def generate():
    jd_text, jd_err = _resolve_jd(request.form)
    cv_text, cv_err = _resolve_cv(request.form, request.files)

    if jd_err == "jd":
        return jsonify({"status": "needs_paste", "field": "jd",
                        "message": "We couldn't read that job link. Please paste the job description."}), 200
    if jd_err == "jd_missing":
        return jsonify({"status": "error", "message": "Provide a job description or link."}), 400
    if cv_err == "cv":
        return jsonify({"status": "needs_paste", "field": "cv",
                        "message": "We couldn't read that file. Please paste your CV text."}), 200
    if cv_err == "cv_type":
        return jsonify({"status": "error", "message": "Upload a .pdf or .docx file."}), 400
    if cv_err == "cv_missing":
        return jsonify({"status": "error", "message": "Upload or paste your CV."}), 400

    if not os.environ.get("GEMINI_API_KEY"):
        return jsonify({"status": "error",
                        "message": "Server missing GEMINI_API_KEY. Get a free key at "
                                   "https://aistudio.google.com/apikey"}), 500

    try:
        result = llm.generate_all(cv_text, jd_text)
        analysis = result.get("analysis", {})
    except Exception as e:
        return jsonify({"status": "error",
                        "message": f"Generation failed: {e}"}), 500

    job_id = uuid.uuid4().hex[:10]
    resume = result.get("resume", {})

    def _slug(*parts):
        import re
        out = []
        for p in parts:
            if not p:
                continue
            s = re.sub(r"[^A-Za-z0-9]+", "-", str(p)).strip("-")
            if s:
                out.append(s)
        return "-".join(out)

    _job = result.get("job", {}) or {}
    _full = (resume.get("name", "") or "").strip()
    _last = _full.split()[-1] if _full else ""
    _stem = _slug(_last, _job.get("company")) or "tailorback"
    # disk name = <id>__<clean-stem>-<type>.docx ; route serves the part after __
    resume_path = os.path.join(GENERATED, f"{job_id}__{_stem}_resume.docx")
    cover_path = os.path.join(GENERATED, f"{job_id}__{_stem}_coverletter.docx")
    docx_builder.build_resume(resume, resume_path)
    _c = resume.get("contact", {}) or {}
    _contact_line = "   •   ".join(
        x for x in (_c.get("email"), _c.get("phone"), _c.get("location")) if x)
    docx_builder.build_cover_letter(
        result.get("cover_letter", {}), resume.get("name", ""), cover_path,
        contact_line=_contact_line, links=_c.get("links") or [])

    # Also produce PDFs (falls back to None if LibreOffice isn't installed).
    resume_pdf = docx_builder.to_pdf(resume_path)
    cover_pdf = docx_builder.to_pdf(cover_path)

    def _url(path):
        return f"/download/{os.path.basename(path)}" if path else None

    return jsonify({
        "status": "ok",
        "resume_docx_url": _url(resume_path),
        "cover_docx_url": _url(cover_path),
        "resume_pdf_url": _url(resume_pdf),
        "cover_pdf_url": _url(cover_pdf),
        "gaps": result.get("gaps", []),
        "match": result.get("match_summary", {}),
        "analysis": analysis,
        "preview": {
            "name": resume.get("name", ""),
            "summary": resume.get("summary", ""),
            "skills": resume.get("skills", []),
        },
    })


@app.route("/download/<path:fname>")
def download(fname):
    # disk name is "<job_id>__<clean>.ext"; download as the clean part only
    clean = fname.split("__", 1)[1] if "__" in fname else fname
    return send_from_directory(GENERATED, fname, as_attachment=True,
                               download_name=clean)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
