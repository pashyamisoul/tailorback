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
from flask import Flask, request, jsonify, render_template, send_from_directory, Response, stream_with_context
import json as _json
import threading
import queue as _queue
from flask import Flask, request, jsonify, render_template, send_from_directory
from dotenv import load_dotenv
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth
from flask import session, redirect, url_for
from authlib.integrations.flask_client import OAuth
from flask import session, redirect, url_for

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

# --- Database (SQLite for local dev; swap DATABASE_URL for Postgres on deploy) ---
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///" + os.path.join(BASE, "tailorback.db"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# --- Session secret (required for OAuth login state) ---
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

# --- OAuth (Google) ---
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    provider = db.Column(db.String(32), nullable=False)
    provider_id = db.Column(db.String(255), nullable=False)
    generations_used = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


with app.app_context():
    db.create_all()


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/auth/google")
def auth_google():
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    token = oauth.google.authorize_access_token()
    info = token.get("userinfo") or {}
    email = info.get("email")
    sub = info.get("sub")
    if not email:
        return "Login failed: no email returned.", 400
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, provider="google", provider_id=sub or "")
        db.session.add(user)
        db.session.commit()
    session["user_id"] = user.id
    session["email"] = user.email
    return redirect(url_for("index"))


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("index"))


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

    def event_stream():
        q = _queue.Queue()

        def on_status(stage):
            q.put({"type": "status", "stage": stage})

        holder = {}

        def worker():
            try:
                holder["result"] = llm.generate_all(cv_text, jd_text, on_status=on_status)
            except Exception as e:
                holder["error"] = str(e)
            finally:
                q.put({"type": "_done"})

        t = threading.Thread(target=worker)
        t.start()

        # stream live status events until the worker finishes
        while True:
            msg = q.get()
            if msg.get("type") == "_done":
                break
            yield f"data: {_json.dumps(msg)}\n\n"
        t.join()

        if "error" in holder:
            yield f"data: {_json.dumps({'type': 'error', 'message': 'Generation failed: ' + holder['error']})}\n\n"
            return

        result = holder["result"]
        analysis = result.get("analysis", {})
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
        resume_path = os.path.join(GENERATED, f"{job_id}__{_stem}_resume.docx")
        cover_path = os.path.join(GENERATED, f"{job_id}__{_stem}_coverletter.docx")
        docx_builder.build_resume(resume, resume_path)
        _c = resume.get("contact", {}) or {}
        _contact_line = "   •   ".join(
            x for x in (_c.get("email"), _c.get("phone"), _c.get("location")) if x)
        docx_builder.build_cover_letter(
            result.get("cover_letter", {}), resume.get("name", ""), cover_path,
            contact_line=_contact_line, links=_c.get("links") or [])
        resume_pdf = docx_builder.to_pdf(resume_path)
        cover_pdf = docx_builder.to_pdf(cover_path)

        def _url(path):
            return f"/download/{os.path.basename(path)}" if path else None

        payload = {
            "type": "done",
            "result": {
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
            },
        }
        yield f"data: {_json.dumps(payload)}\n\n"

    return Response(stream_with_context(event_stream()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download/<path:fname>")
def download(fname):
    # disk name is "<job_id>__<clean>.ext"; download as the clean part only
    clean = fname.split("__", 1)[1] if "__" in fname else fname
    return send_from_directory(GENERATED, fname, as_attachment=True,
                               download_name=clean)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
