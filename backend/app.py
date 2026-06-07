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
import json as _json
import threading
import queue as _queue
import hashlib
import hmac
import time
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    send_from_directory,
    session,
    stream_with_context,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from authlib.integrations.flask_client import OAuth
from werkzeug.security import check_password_hash, generate_password_hash

from services import cv_parser, jd_source, llm, docx_builder, scoring

load_dotenv()

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS = os.path.join(BASE, "uploads")
GENERATED = os.path.join(BASE, "generated")
os.makedirs(UPLOADS, exist_ok=True)
os.makedirs(GENERATED, exist_ok=True)

ALLOWED = {".pdf", ".docx"}
MAX_BYTES = 8 * 1024 * 1024
FREE_LIMIT = int(os.environ.get("FREE_GENERATION_LIMIT", "2"))
GENERATED_RETENTION_DAYS = int(os.environ.get("GENERATED_RETENTION_DAYS", "7"))

# --- Document style gallery (drives docx_builder + the in-app editor) ---
ALLOWED_TEMPLATES = {"editorial", "modern", "classic", "compact"}
ALLOWED_FONTS = {"Calibri", "Georgia", "Arial", "Garamond", "Helvetica", "Times New Roman"}
ALLOWED_DENSITY = {"comfortable", "compact"}
_DEFAULT_DOC_STYLE = {
    "template": "editorial",
    "accent": "c8462e",
    "font": "Calibri",
    "density": "comfortable",
}
STRIPE_API = "https://api.stripe.com/v1"
STRIPE_PACKS = [
    {
        "id": "starter",
        "name": "Starter",
        "credits": 10,
        "price": "€7",
        "cadence": "10 generations",
        "price_env": "STRIPE_PRICE_STARTER",
    },
    {
        "id": "hunt",
        "name": "Job Hunt",
        "credits": 40,
        "price": "€19",
        "cadence": "40 generations",
        "price_env": "STRIPE_PRICE_HUNT",
        "featured": True,
    },
    {
        "id": "sprint",
        "name": "Career Sprint",
        "credits": 150,
        "price": "€49",
        "cadence": "150 generations",
        "price_env": "STRIPE_PRICE_SPRINT",
    },
]

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
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("FLASK_SECRET_KEY must be set in production.")
    app.secret_key = "dev-only-change-me"

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
    full_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    provider = db.Column(db.String(32), nullable=False)
    provider_id = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verification_token = db.Column(db.String(64), unique=True, nullable=True)
    country = db.Column(db.String(120), nullable=True)
    zip_code = db.Column(db.String(32), nullable=True)
    current_pack = db.Column(db.String(64), nullable=True)
    newsletter_opt_in = db.Column(db.Boolean, default=False, nullable=False)
    terms_accepted_at = db.Column(db.DateTime, nullable=True)
    generations_used = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class GeneratedDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(32), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    filename = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, index=True, nullable=False)


class CreditGrant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    credits = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(64), nullable=False)
    pack_id = db.Column(db.String(64), nullable=True)
    note = db.Column(db.Text, nullable=True)
    stripe_session_id = db.Column(db.String(255), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class GenerationRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(32), unique=True, index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    jd_source_type = db.Column(db.String(16), nullable=False)
    jd_url = db.Column(db.Text, nullable=True)
    jd_text = db.Column(db.Text, nullable=False)
    cv_source_type = db.Column(db.String(16), nullable=False)
    cv_text = db.Column(db.Text, nullable=False)
    resume_json = db.Column(db.Text, nullable=True)
    cover_letter_json = db.Column(db.Text, nullable=True)
    analysis_json = db.Column(db.Text, nullable=True)
    model_status = db.Column(db.String(64), nullable=True)
    model_provider = db.Column(db.String(32), nullable=True)
    model_name = db.Column(db.String(128), nullable=True)
    generation_seconds = db.Column(db.Float, nullable=True)
    style_json = db.Column(db.Text, nullable=True)
    # Application tracker fields
    company = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(32), nullable=True)   # not_applied|applied|interviewing|offer|rejected
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True, nullable=False)
    rating = db.Column(db.Integer, nullable=False)            # 1-5
    comment = db.Column(db.Text, nullable=True)
    consent_to_publish = db.Column(db.Boolean, default=False, nullable=False)
    display_name = db.Column(db.String(120), nullable=True)
    role = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


ALLOWED_APP_STATUSES = ("not_applied", "applied", "interviewing", "offer", "rejected")


def _ensure_schema():
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
        return
    columns = {
        "user": {
            "full_name": "VARCHAR(255)",
            "password_hash": "VARCHAR(255)",
            "email_verified": "BOOLEAN DEFAULT 0",
            "email_verification_token": "VARCHAR(64)",
            "country": "VARCHAR(120)",
            "zip_code": "VARCHAR(32)",
            "current_pack": "VARCHAR(64)",
            "newsletter_opt_in": "BOOLEAN DEFAULT 0",
            "terms_accepted_at": "DATETIME",
        },
        "credit_grant": {
            "pack_id": "VARCHAR(64)",
            "note": "TEXT",
        },
        "generation_run": {
            "model_provider": "VARCHAR(32)",
            "model_name": "VARCHAR(128)",
            "generation_seconds": "FLOAT",
            "style_json": "TEXT",
            "company": "VARCHAR(255)",
            "role": "VARCHAR(255)",
            "status": "VARCHAR(32)",
            "notes": "TEXT",
        },
    }
    with db.engine.connect() as conn:
        for table, wanted in columns.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            for name, ddl in wanted.items():
                if name not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        conn.exec_driver_sql("UPDATE user SET email_verified = 1 WHERE provider IN ('google', 'both')")
        conn.commit()


with app.app_context():
    db.create_all()
    _ensure_schema()


def _paid_credits(user):
    if not user:
        return 0
    total = db.session.query(db.func.coalesce(db.func.sum(CreditGrant.credits), 0)).filter_by(
        user_id=user.id).scalar()
    return int(total or 0)


def _credits_payload(user):
    used = user.generations_used if user else 0
    paid = _paid_credits(user)
    limit = FREE_LIMIT + paid
    return {
        "credits_used": used,
        "credits_remaining": max(0, limit - used),
        "credits_limit": limit,
        "free_credits_limit": FREE_LIMIT,
        "paid_credits": paid,
    }


def _current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user = db.session.get(User, uid)
    if not user:
        session.clear()
    return user


def _register_generated_file(user, job_id, path):
    if not path:
        return
    doc = GeneratedDocument(
        job_id=job_id,
        user_id=user.id,
        filename=os.path.basename(path),
        expires_at=datetime.utcnow() + timedelta(days=GENERATED_RETENTION_DAYS),
    )
    db.session.add(doc)


def _download_urls_for_job(user_id, job_id):
    docs = GeneratedDocument.query.filter_by(user_id=user_id, job_id=job_id).all()
    out = {}
    for doc in docs:
        key = "cover" if "cover" in doc.filename.lower() else "resume"
        fmt = "pdf" if doc.filename.lower().endswith(".pdf") else "docx"
        out[f"{key}_{fmt}_url"] = f"/download/{doc.filename}"
    return out


def _generation_payload(run, include_inputs=False):
    payload = {
        "job_id": run.job_id,
        "created_at": run.created_at.isoformat() + "Z",
        "jd_source_type": run.jd_source_type,
        "jd_url": run.jd_url,
        "cv_source_type": run.cv_source_type,
        "downloads": _download_urls_for_job(run.user_id, run.job_id),
        "model_provider": run.model_provider or "unknown",
        "model_name": run.model_name or run.model_status or "Unknown",
        "generation_seconds": run.generation_seconds,
        "company": run.company,
        "role": run.role,
        "status": run.status or "not_applied",
        "notes": run.notes or "",
    }
    try:
        resume = _json.loads(run.resume_json or "{}")
    except ValueError:
        resume = {}
    try:
        cover = _json.loads(run.cover_letter_json or "{}")
    except ValueError:
        cover = {}
    payload["resume_name"] = resume.get("name", "")
    payload["resume_summary"] = resume.get("summary", "")
    payload["cover_subject"] = cover.get("subject", "")
    try:
        payload["match_score"] = _json.loads(run.analysis_json or "{}").get("overall_score")
    except ValueError:
        payload["match_score"] = None
    if include_inputs:
        letter_parts = [
            cover.get("salutation"),
            *(cover.get("paragraphs") or []),
            cover.get("closing"),
        ]
        payload.update({
            "jd_text": run.jd_text,
            "cv_text": run.cv_text,
            "resume": resume,
            "cover_letter": cover,
            "cover_letter_text": "\n\n".join(p for p in letter_parts if p),
            "analysis": _json.loads(run.analysis_json or "{}"),
        })
    return payload


def _cleanup_generated():
    now = datetime.utcnow()
    expired = GeneratedDocument.query.filter(GeneratedDocument.expires_at <= now).all()
    for doc in expired:
        try:
            os.remove(os.path.join(GENERATED, doc.filename))
        except FileNotFoundError:
            pass
        db.session.delete(doc)
    db.session.commit()


import re as _re


def _slugify(*parts):
    out = []
    for p in parts:
        if not p:
            continue
        s = _re.sub(r"[^A-Za-z0-9]+", "-", str(p)).strip("-")
        if s:
            out.append(s)
    return "-".join(out)


def _sanitize_style(style):
    """Clamp an incoming style dict to known-safe values."""
    style = style if isinstance(style, dict) else {}
    accent = str(style.get("accent") or "").lstrip("#")
    if not _re.fullmatch(r"[0-9a-fA-F]{6}", accent or ""):
        accent = _DEFAULT_DOC_STYLE["accent"]
    template = style.get("template")
    font = style.get("font")
    density = style.get("density")
    return {
        "template": template if template in ALLOWED_TEMPLATES else _DEFAULT_DOC_STYLE["template"],
        "accent": accent.lower(),
        "font": font if font in ALLOWED_FONTS else _DEFAULT_DOC_STYLE["font"],
        "density": density if density in ALLOWED_DENSITY else _DEFAULT_DOC_STYLE["density"],
    }


def _clean_str(v, limit=4000):
    return str(v).strip()[:limit] if v is not None else ""


def _clean_str_list(v, limit=60, item_limit=600):
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:limit]:
        s = _clean_str(item, item_limit)
        if s:
            out.append(s)
    return out


def _sanitize_resume(resume):
    """Structurally validate/coerce an edited resume payload before building."""
    resume = resume if isinstance(resume, dict) else {}
    contact = resume.get("contact") if isinstance(resume.get("contact"), dict) else {}
    experience = []
    for job in (resume.get("experience") or [])[:25]:
        if not isinstance(job, dict):
            continue
        experience.append({
            "title": _clean_str(job.get("title"), 200),
            "company": _clean_str(job.get("company"), 200),
            "dates": _clean_str(job.get("dates"), 120),
            "bullets": _clean_str_list(job.get("bullets"), limit=20, item_limit=1000),
        })
    projects = []
    for proj in (resume.get("projects") or [])[:25]:
        if not isinstance(proj, dict):
            continue
        projects.append({
            "name": _clean_str(proj.get("name"), 200),
            "link": _clean_str(proj.get("link"), 300),
            "dates": _clean_str(proj.get("dates"), 120),
            "bullets": _clean_str_list(proj.get("bullets"), limit=20, item_limit=1000),
        })
    education = []
    for ed in (resume.get("education") or [])[:15]:
        if not isinstance(ed, dict):
            continue
        education.append({
            "degree": _clean_str(ed.get("degree"), 200),
            "institution": _clean_str(ed.get("institution"), 200),
            "dates": _clean_str(ed.get("dates"), 120),
        })
    return {
        "name": _clean_str(resume.get("name"), 200),
        "contact": {
            "email": _clean_str(contact.get("email"), 200),
            "phone": _clean_str(contact.get("phone"), 80),
            "location": _clean_str(contact.get("location"), 200),
            "links": _clean_str_list(contact.get("links"), limit=10, item_limit=300),
        },
        "summary": _clean_str(resume.get("summary"), 3000),
        "skills": _clean_str_list(resume.get("skills"), limit=60, item_limit=120),
        "experience": experience,
        "projects": projects,
        "education": education,
        "certifications": _clean_str_list(resume.get("certifications"), limit=30, item_limit=300),
    }


def _resume_to_text(resume):
    """Flatten a structured resume into plain text for the deterministic scorer
    (so the tailored output can be scored the same way the uploaded CV is)."""
    resume = resume if isinstance(resume, dict) else {}
    parts = []
    if resume.get("name"):
        parts.append(resume["name"])
    c = resume.get("contact") or {}
    contact_bits = [c.get("email"), c.get("phone"), c.get("location"), *(c.get("links") or [])]
    contact = " | ".join(x for x in contact_bits if x)
    if contact:
        parts.append(contact)
    if resume.get("summary"):
        parts.append("Summary\n" + resume["summary"])
    if resume.get("skills"):
        parts.append("Skills\n" + ", ".join(resume["skills"]))
    if resume.get("experience"):
        parts.append("Experience")
        for j in resume["experience"]:
            parts.append(f"{j.get('title','')} - {j.get('company','')} ({j.get('dates','')})")
            parts.extend("• " + b for b in (j.get("bullets") or []))
    if resume.get("projects"):
        parts.append("Projects")
        for p in resume["projects"]:
            parts.append(f"{p.get('name','')} ({p.get('dates','')})")
            parts.extend("• " + b for b in (p.get("bullets") or []))
    if resume.get("education"):
        parts.append("Education")
        for e in resume["education"]:
            parts.append(f"{e.get('degree','')} - {e.get('institution','')} ({e.get('dates','')})")
    if resume.get("certifications"):
        parts.append("Certifications\n" + "; ".join(resume["certifications"]))
    return "\n".join(parts)


def _sanitize_cover(cover):
    cover = cover if isinstance(cover, dict) else {}
    return {
        "greeting": _clean_str(cover.get("greeting"), 300),
        "body_paragraphs": _clean_str_list(cover.get("body_paragraphs"), limit=12, item_limit=3000),
        "closing": _clean_str(cover.get("closing"), 300),
    }


def _build_documents(user, job_id, resume, cover, style, job=None):
    """Build resume + cover-letter docx/pdf for a job, register them, and return
    download URLs. Replaces any previously generated files for this job_id."""
    # Drop the previous file set for this job (edits supersede older exports).
    for old in GeneratedDocument.query.filter_by(user_id=user.id, job_id=job_id).all():
        try:
            os.remove(os.path.join(GENERATED, old.filename))
        except FileNotFoundError:
            pass
        db.session.delete(old)

    job = job or {}
    full = (resume.get("name", "") or "").strip()
    last = full.split()[-1] if full else ""
    stem = _slugify(last, job.get("company")) or "tailorback"
    rev = uuid.uuid4().hex[:4]
    resume_path = os.path.join(GENERATED, f"{job_id}__{stem}_{rev}_resume.docx")
    cover_path = os.path.join(GENERATED, f"{job_id}__{stem}_{rev}_coverletter.docx")

    docx_builder.build_resume(resume, resume_path, style=style)
    c = resume.get("contact", {}) or {}
    contact_line = "   •   ".join(
        x for x in (c.get("email"), c.get("phone"), c.get("location")) if x)
    docx_builder.build_cover_letter(
        cover, resume.get("name", ""), cover_path,
        contact_line=contact_line, links=c.get("links") or [], style=style)
    pdfs = docx_builder.to_pdfs([resume_path, cover_path])
    for path in (resume_path, cover_path, pdfs.get(resume_path), pdfs.get(cover_path)):
        _register_generated_file(user, job_id, path)
    return _download_urls_for_job(user.id, job_id)


def _public_credit_packs():
    return [
        {
            "id": pack["id"],
            "name": pack["name"],
            "credits": pack["credits"],
            "price": pack["price"],
            "cadence": pack["cadence"],
            "featured": bool(pack.get("featured")),
            "configured": bool(os.environ.get(pack["price_env"])),
        }
        for pack in STRIPE_PACKS
    ]


def _credit_pack(pack_id):
    return next((pack for pack in STRIPE_PACKS if pack["id"] == pack_id), None)


def _login_user(user):
    session["user_id"] = user.id
    session["email"] = user.email


def _display_name(user):
    """Best display name: stored full name, else derived from the email local part."""
    if user.full_name and user.full_name.strip():
        return user.full_name.strip()
    local = (user.email or "").split("@")[0]
    parts = [p for p in _re.split(r"[._\-+]+", local) if p]
    name = " ".join(p.capitalize() for p in parts)
    return name or "there"


def _safe_user_payload(user):
    credits = _credits_payload(user)
    pack = _credit_pack(user.current_pack) if user and user.current_pack else None
    return {
        "full_name": user.full_name,
        "display_name": _display_name(user),
        "email": user.email,
        "email_verified": bool(user.email_verified),
        "provider": user.provider,
        "has_password": bool(user.password_hash),
        "country": user.country,
        "zip_code": user.zip_code,
        "current_pack": pack["name"] if pack else "Free",
        **credits,
    }


def _make_email_verification_token():
    return uuid.uuid4().hex + uuid.uuid4().hex


def _send_activation_email(user):
    activation_url = url_for("activate_email", token=user.email_verification_token, _external=True)
    if os.environ.get("FLASK_ENV") == "production":
        app.logger.warning("Email provider is not configured; activation email was not sent for %s", user.email)
    else:
        app.logger.warning("TailorBack activation link for %s: %s", user.email, activation_url)
    return activation_url


def _model_from_stages(stages):
    if "generating_claude" in stages or "switching_to_claude" in stages:
        return "anthropic", os.environ.get("ANTHROPIC_MODEL", llm.CLAUDE_MODEL)
    if "trying_gemini" in stages or "switching_to_gemini" in stages:
        return "gemini", os.environ.get("GEMINI_MODEL", llm.GEMINI_MODEL)
    if "generating_openai" in stages:
        return "openai", os.environ.get("OPENAI_MODEL", llm.OPENAI_MODEL)
    return "unknown", "Unknown"


def _admin_adjust_user_credits(user, action, amount, note):
    credits = _credits_payload(user)
    if action == "add":
        delta = amount
        source = "admin:add"
    elif action == "deduct":
        delta = -amount
        source = "admin:deduct"
    elif action == "set_total":
        delta = amount - credits["credits_limit"]
        source = "admin:set-total"
    elif action == "set_remaining":
        target_limit = user.generations_used + amount
        delta = target_limit - credits["credits_limit"]
        source = "admin:set-remaining"
    else:
        raise ValueError("Unknown adjustment action.")
    if delta == 0:
        return credits
    db.session.add(CreditGrant(
        user_id=user.id,
        credits=delta,
        source=source,
        pack_id="admin",
        note=note,
    ))
    db.session.commit()
    return _credits_payload(user)


def _stripe_signature_ok(payload, signature_header):
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not signature_header:
        return False
    values = {}
    for part in signature_header.split(","):
        key, _, value = part.partition("=")
        if key and value:
            values.setdefault(key, []).append(value)
    try:
        timestamp = int(values.get("t", ["0"])[0])
    except ValueError:
        return False
    if abs(int(time.time()) - timestamp) > 300:
        return False
    signed = str(timestamp).encode("utf-8") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in values.get("v1", []))


@app.context_processor
def _inject_globals():
    return {"current_year": datetime.utcnow().year}


def _published_testimonials(limit=3):
    """Auto-published reviews for the landing testimonials: consented + rating >= 4.

    Returns the most recent ones with a non-empty comment. Falls back to an
    empty list (the template then shows its placeholder quotes)."""
    rows = (Feedback.query
            .filter(Feedback.consent_to_publish.is_(True),
                    Feedback.rating >= 4,
                    Feedback.comment.isnot(None))
            .order_by(Feedback.created_at.desc())
            .limit(limit).all())
    out = []
    for r in rows:
        text = (r.comment or "").strip()
        if not text:
            continue
        out.append({
            "text": text,
            "name": (r.display_name or "").strip() or "Verified user",
            "role": (r.role or "").strip(),
            "rating": int(r.rating or 0),
        })
    return out


@app.route("/")
def index():
    _cleanup_generated()
    u = _current_user()
    credits = _credits_payload(u)
    return render_template("index.html",
                           user_email=u.email if u else None,
                           credits_used=credits["credits_used"],
                           credits_remaining=credits["credits_remaining"],
                           credits_limit=credits["credits_limit"],
                           free_credits_limit=FREE_LIMIT,
                           credit_packs=_public_credit_packs(),
                           testimonials=_published_testimonials(),
                           billing_enabled=bool(os.environ.get("STRIPE_SECRET_KEY")))

@app.route("/auth/google")
def auth_google():
    if request.args.get("popup"):
        session["login_popup"] = True
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(
        redirect_uri,
        prompt="select_account",
    )


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
        user = User(
            full_name=info.get("name") or "",
            email=email,
            provider="google",
            provider_id=sub or "",
            email_verified=True,
            terms_accepted_at=datetime.utcnow(),
        )
        db.session.add(user)
        db.session.commit()
    elif user.provider != "google":
        user.provider = "both"
        user.provider_id = sub or user.provider_id
        user.email_verified = True
        if info.get("name") and not user.full_name:
            user.full_name = info.get("name")
        db.session.commit()
    elif not user.email_verified:
        user.email_verified = True
        db.session.commit()
    # Backfill the name for any existing account that's missing it.
    if info.get("name") and not (user.full_name or "").strip():
        user.full_name = info.get("name")
        db.session.commit()
    _login_user(user)
    if session.pop("login_popup", False):
        credits = _credits_payload(user)
        origin = request.host_url.rstrip("/")
        return """<!doctype html><meta charset="utf-8"><title>Signed in</title>
<script>
  if (window.opener) {{ window.opener.postMessage({{
    type:"tailorback-login-success",
    email:{},
    creditsRemaining:{},
    creditsLimit:{}
  }}, {}); }}
  window.close();
</script>
<p>Signed in. You can close this window.</p>""".format(
            _json.dumps(email or ""),
            credits["credits_remaining"],
            credits["credits_limit"],
            _json.dumps(origin))
    return redirect(url_for("index"))


@app.route("/api/auth/signup", methods=["POST"])
def auth_signup_email():
    data = request.get_json(silent=True) or {}
    full_name = (data.get("full_name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    repeat = data.get("repeat_password") or ""
    if not full_name:
        return jsonify({"status": "error", "message": "Enter your full name."}), 400
    if not email or "@" not in email:
        return jsonify({"status": "error", "message": "Enter a valid email address."}), 400
    if len(password) < 8:
        return jsonify({"status": "error", "message": "Password must be at least 8 characters."}), 400
    if password != repeat:
        return jsonify({"status": "error", "message": "Passwords do not match."}), 400
    if not data.get("agree_terms"):
        return jsonify({"status": "error",
                        "message": "Please agree to the Terms of Use and Privacy Policy to continue."}), 400
    existing = User.query.filter_by(email=email).first()
    if existing:
        return jsonify({"status": "error", "message": "This email already has an account. Sign in instead."}), 409
    token = _make_email_verification_token()
    user = User(
        full_name=full_name,
        email=email,
        provider="email",
        provider_id=email,
        password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        email_verified=False,
        email_verification_token=token,
        newsletter_opt_in=bool(data.get("newsletter")),
        terms_accepted_at=datetime.utcnow(),
    )
    db.session.add(user)
    db.session.commit()
    activation_url = _send_activation_email(user)
    payload = {
        "status": "verification_required",
        "message": "Account created. Check your email to activate your account.",
    }
    if os.environ.get("FLASK_ENV") != "production":
        payload["activation_url"] = activation_url
    return jsonify(payload)


@app.route("/auth/activate/<token>")
def activate_email(token):
    user = User.query.filter_by(email_verification_token=token).first()
    if not user:
        return redirect(url_for("index", auth="invalid_activation"))
    user.email_verified = True
    user.email_verification_token = None
    db.session.commit()
    _login_user(user)
    return redirect(url_for("index", auth="activated"))


@app.route("/api/auth/signin", methods=["POST"])
def auth_signin_email():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({
            "status": "account_not_found",
            "message": "No TailorBack account exists for this email. Create an account first.",
        }), 404
    if not user.password_hash:
        return jsonify({
            "status": "use_google",
            "message": "This email uses Google sign-in. Continue with Google instead.",
        }), 401
    if not check_password_hash(user.password_hash, password):
        return jsonify({"status": "error", "message": "Incorrect password."}), 401
    if not user.email_verified:
        activation_url = _send_activation_email(user)
        payload = {
            "status": "verification_required",
            "message": "Please activate your account from the email we sent before signing in.",
        }
        if os.environ.get("FLASK_ENV") != "production":
            payload["activation_url"] = activation_url
        return jsonify(payload), 403
    _login_user(user)
    return jsonify({"status": "ok", "user": _safe_user_payload(user)})


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
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error",
                        "message": "Please sign in to generate."}), 401
    if not current_user.email_verified:
        return jsonify({"status": "error",
                        "message": "Please activate your account before generating."}), 403
    _cleanup_generated()
    credits_before = _credits_payload(current_user)
    if credits_before["credits_remaining"] <= 0:
        return jsonify({"status": "error",
                        "message": "You're out of generations. Buy a credit pack to keep tailoring.",
                        **credits_before}), 402
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
    if not any(os.environ.get(k) for k in (
            "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")):
        return jsonify({"status": "error",
                        "message": "Server missing an LLM API key. Add OPENAI_API_KEY, "
                                   "GEMINI_API_KEY, or ANTHROPIC_API_KEY."}), 500

    def event_stream():
        q = _queue.Queue()
        stages = []
        generation_started_at = time.perf_counter()

        def on_status(stage):
            stages.append(stage)
            q.put({"type": "status", "stage": stage})

        holder = {}

        def worker():
            try:
                holder["result"] = llm.generate_all(cv_text, jd_text, on_status=on_status)
            except Exception as e:
                app.logger.exception("Generation failed")
                holder["error"] = "Generation failed. Please try again shortly."
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
            yield f"data: {_json.dumps({'type': 'error', 'message': holder['error']})}\n\n"
            return

        result = holder["result"]
        provider, model_name = _model_from_stages(stages)
        generation_seconds = round(time.perf_counter() - generation_started_at, 2)
        # Count this successful generation against the user's free quota.
        try:
            current_user.generations_used += 1
            db.session.commit()
        except Exception:
            db.session.rollback()
        credits = _credits_payload(current_user)
        analysis = scoring.score_resume(cv_text, jd_text, result.get("analysis", {}))
        job_id = uuid.uuid4().hex[:10]
        resume = result.get("resume", {})
        # Re-score the tailored resume so the UI can show the before→after lift.
        tailored_analysis = scoring.score_resume(_resume_to_text(resume), jd_text)
        score_before = analysis.get("overall_score")
        score_after = tailored_analysis.get("overall_score")

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
        on_status("building_documents")
        docx_builder.build_resume(resume, resume_path)
        _c = resume.get("contact", {}) or {}
        _contact_line = "   •   ".join(
            x for x in (_c.get("email"), _c.get("phone"), _c.get("location")) if x)
        docx_builder.build_cover_letter(
            result.get("cover_letter", {}), resume.get("name", ""), cover_path,
            contact_line=_contact_line, links=_c.get("links") or [])
        on_status("converting_documents")
        pdfs = docx_builder.to_pdfs([resume_path, cover_path])
        resume_pdf = pdfs.get(resume_path)
        cover_pdf = pdfs.get(cover_path)
        for path in (resume_path, cover_path, resume_pdf, cover_pdf):
            _register_generated_file(current_user, job_id, path)
        run = GenerationRun(
            job_id=job_id,
            user_id=current_user.id,
            jd_source_type="link" if (request.form.get("jd_url") or "").strip() else "paste",
            jd_url=(request.form.get("jd_url") or "").strip() or None,
            jd_text=jd_text,
            cv_source_type="upload" if request.files.get("cv_file") else "paste",
            cv_text=cv_text,
            resume_json=_json.dumps(result.get("resume", {})),
            cover_letter_json=_json.dumps(result.get("cover_letter", {})),
            analysis_json=_json.dumps(analysis),
            model_status=",".join(stages[-8:]),
            model_provider=provider,
            model_name=model_name,
            generation_seconds=generation_seconds,
            company=(_job.get("company") or None),
            role=(_job.get("role") or None),
            status="not_applied",
        )
        db.session.add(run)
        db.session.commit()

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
                "job_id": job_id,
                "expires_in_days": GENERATED_RETENTION_DAYS,
                "gaps": result.get("gaps", []),
                "match": result.get("match_summary", {}),
                "analysis": analysis,
                "score_before": score_before,
                "score_after": score_after,
                **credits,
                "resume": resume,
                "cover_letter": result.get("cover_letter", {}),
                "job": _job,
                "style": _DEFAULT_DOC_STYLE,
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


@app.route("/api/credit-packs")
def credit_packs():
    current_user = _current_user()
    payload = _credits_payload(current_user) if current_user else None
    return jsonify({
        "status": "ok",
        "billing_enabled": bool(os.environ.get("STRIPE_SECRET_KEY")),
        "packs": _public_credit_packs(),
        "credits": payload,
    })


@app.route("/api/checkout", methods=["POST"])
def create_checkout():
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error",
                        "message": "Please sign in before buying credits."}), 401
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        return jsonify({"status": "error",
                        "message": "Stripe checkout is not configured yet."}), 503
    data = request.get_json(silent=True) or {}
    pack = _credit_pack(data.get("pack_id"))
    if not pack:
        return jsonify({"status": "error", "message": "Unknown credit pack."}), 400
    price_id = os.environ.get(pack["price_env"])
    if not price_id:
        return jsonify({"status": "error",
                        "message": "This credit pack is not configured yet."}), 503
    origin = request.host_url.rstrip("/")
    success_url = os.environ.get(
        "STRIPE_SUCCESS_URL", f"{origin}/?checkout=success")
    cancel_url = os.environ.get(
        "STRIPE_CANCEL_URL", f"{origin}/?checkout=cancelled")
    form = [
        ("mode", "payment"),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("customer_email", current_user.email),
        ("client_reference_id", str(current_user.id)),
        ("line_items[0][price]", price_id),
        ("line_items[0][quantity]", "1"),
        ("metadata[user_id]", str(current_user.id)),
        ("metadata[pack_id]", pack["id"]),
        ("metadata[credits]", str(pack["credits"])),
    ]
    try:
        resp = httpx.post(
            f"{STRIPE_API}/checkout/sessions",
            data=form,
            auth=(stripe_key, ""),
            timeout=20,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        app.logger.exception("Stripe checkout failed: %s", exc.response.text[:500])
        return jsonify({"status": "error",
                        "message": "Stripe checkout failed. Check the configured price IDs."}), 502
    except httpx.RequestError:
        app.logger.exception("Stripe checkout request failed")
        return jsonify({"status": "error",
                        "message": "Could not reach Stripe. Try again shortly."}), 502
    checkout = resp.json()
    return jsonify({"status": "ok", "checkout_url": checkout.get("url")})


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    if not _stripe_signature_ok(payload, request.headers.get("Stripe-Signature")):
        return jsonify({"status": "error", "message": "Invalid signature."}), 400
    try:
        event = _json.loads(payload.decode("utf-8"))
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid payload."}), 400
    if event.get("type") != "checkout.session.completed":
        return jsonify({"status": "ignored"})
    session_obj = event.get("data", {}).get("object", {}) or {}
    if session_obj.get("payment_status") not in ("paid", "no_payment_required"):
        return jsonify({"status": "ignored"})
    session_id = session_obj.get("id")
    if not session_id:
        return jsonify({"status": "error", "message": "Missing session id."}), 400
    if CreditGrant.query.filter_by(stripe_session_id=session_id).first():
        return jsonify({"status": "ok", "already_processed": True})
    metadata = session_obj.get("metadata", {}) or {}
    try:
        user_id = int(metadata.get("user_id"))
        credits = int(metadata.get("credits"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Missing credit metadata."}), 400
    user = db.session.get(User, user_id)
    if not user or credits <= 0:
        return jsonify({"status": "error", "message": "Invalid credit grant."}), 400
    db.session.add(CreditGrant(
        user_id=user.id,
        credits=credits,
        source=f"stripe:{metadata.get('pack_id', 'unknown')}",
        stripe_session_id=session_id,
    ))
    db.session.commit()
    return jsonify({"status": "ok", "credits_added": credits})


@app.route("/download/<path:fname>")
def download(fname):
    current_user = _current_user()
    if not current_user:
        abort(401)
    doc = GeneratedDocument.query.filter_by(
        filename=fname, user_id=current_user.id).first()
    if not doc:
        abort(404)
    if doc.expires_at <= datetime.utcnow():
        _cleanup_generated()
        abort(404)
    # disk name is "<job_id>__<clean>.ext"; download as the clean part only
    clean = fname.split("__", 1)[1] if "__" in fname else fname
    return send_from_directory(GENERATED, fname, as_attachment=True,
                               download_name=clean)


@app.route("/api/generated/<job_id>", methods=["DELETE"])
def delete_generated(job_id):
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    docs = GeneratedDocument.query.filter_by(
        job_id=job_id, user_id=current_user.id).all()
    if not docs:
        return jsonify({"status": "ok", "deleted": 0})
    deleted = 0
    for doc in docs:
        try:
            os.remove(os.path.join(GENERATED, doc.filename))
            deleted += 1
        except FileNotFoundError:
            pass
        db.session.delete(doc)
    db.session.commit()
    return jsonify({"status": "ok", "deleted": deleted})


@app.route("/api/refine", methods=["POST"])
def refine_section():
    """Regenerate a single resume/cover-letter section. Does not consume credits —
    iterating on a generation you already paid for stays free."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    if not any(os.environ.get(k) for k in (
            "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")):
        return jsonify({"status": "error", "message": "Server missing an LLM API key."}), 500
    data = request.get_json(silent=True) or {}
    kind = data.get("kind")
    if kind not in ("summary", "skills", "bullets", "cover_letter"):
        return jsonify({"status": "error", "message": "Unknown section."}), 400
    content = data.get("content")
    if content is None:
        return jsonify({"status": "error", "message": "Nothing to refine."}), 400
    instruction = _clean_str(data.get("instruction"), 400)
    tone = data.get("tone") if data.get("tone") in (
        "formal", "confident", "concise", "friendly") else ""
    length = data.get("length") if data.get("length") in ("shorter", "longer") else ""
    context = data.get("context") if isinstance(data.get("context"), dict) else None
    try:
        refined = llm.refine_section(
            kind, content, instruction=instruction, tone=tone, length=length, context=context)
    except Exception:
        app.logger.exception("Refine failed")
        return jsonify({"status": "error", "message": "Could not refine that section. Try again."}), 502
    # Coerce to the exact shape the editor expects.
    if kind == "summary":
        out = _clean_str(refined.get("summary"), 3000)
    elif kind == "skills":
        out = _clean_str_list(refined.get("skills"), limit=60, item_limit=120)
    elif kind == "bullets":
        out = _clean_str_list(refined.get("bullets"), limit=20, item_limit=1000)
    else:
        out = _sanitize_cover(refined)
    return jsonify({"status": "ok", "kind": kind, "content": out})


@app.route("/api/export", methods=["POST"])
def export_documents():
    """Rebuild resume + cover-letter docx/pdf from edited content and a chosen
    style. Owner-scoped to the job_id; does not consume credits."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    data = request.get_json(silent=True) or {}
    job_id = (data.get("job_id") or "").strip()
    run = GenerationRun.query.filter_by(job_id=job_id, user_id=current_user.id).first()
    if not run:
        return jsonify({"status": "error", "message": "Unknown document."}), 404
    resume = _sanitize_resume(data.get("resume"))
    cover = _sanitize_cover(data.get("cover_letter"))
    style = _sanitize_style(data.get("style"))
    if not resume["name"] and not resume["experience"]:
        return jsonify({"status": "error", "message": "The resume looks empty."}), 400
    try:
        downloads = _build_documents(current_user, job_id, resume, cover, style, job={})
        run.resume_json = _json.dumps(resume)
        run.cover_letter_json = _json.dumps(cover)
        run.style_json = _json.dumps(style)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Export failed")
        return jsonify({"status": "error", "message": "Could not rebuild the documents."}), 500
    return jsonify({
        "status": "ok",
        "job_id": job_id,
        "style": style,
        "downloads": downloads,
        "resume_docx_url": downloads.get("resume_docx_url"),
        "resume_pdf_url": downloads.get("resume_pdf_url"),
        "cover_docx_url": downloads.get("cover_docx_url"),
        "cover_pdf_url": downloads.get("cover_pdf_url"),
        "expires_in_days": GENERATED_RETENTION_DAYS,
    })


@app.route("/api/account")
def account_summary():
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    return jsonify({"status": "ok", "user": _safe_user_payload(current_user)})


@app.route("/api/account/password", methods=["POST"])
def change_account_password():
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    if not current_user.password_hash:
        return jsonify({
            "status": "error",
            "message": "This account uses Google sign-in. Manage the password with Google.",
        }), 400
    data = request.get_json(silent=True) or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    repeat_password = data.get("repeat_password") or ""
    if not check_password_hash(current_user.password_hash, current_password):
        return jsonify({"status": "error", "message": "Current password is incorrect."}), 401
    if len(new_password) < 8:
        return jsonify({"status": "error", "message": "New password must be at least 8 characters."}), 400
    if new_password != repeat_password:
        return jsonify({"status": "error", "message": "New passwords do not match."}), 400
    if check_password_hash(current_user.password_hash, new_password):
        return jsonify({"status": "error", "message": "Choose a new password that is different."}), 400
    current_user.password_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
    db.session.commit()
    return jsonify({"status": "ok", "message": "Password updated."})


@app.route("/api/generations")
def account_generations():
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    runs = GenerationRun.query.filter_by(user_id=current_user.id).order_by(
        GenerationRun.created_at.desc()).all()
    return jsonify({
        "status": "ok",
        "generations": [_generation_payload(run) for run in runs],
    })


@app.route("/api/generation/<job_id>")
def get_generation(job_id):
    """Return a past generation in the same shape as the live 'done' result, so
    the frontend can reopen it in the editor + score view."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    run = GenerationRun.query.filter_by(job_id=job_id, user_id=current_user.id).first()
    if not run:
        return jsonify({"status": "error", "message": "Not found."}), 404
    try:
        resume = _json.loads(run.resume_json or "{}")
    except ValueError:
        resume = {}
    try:
        cover = _json.loads(run.cover_letter_json or "{}")
    except ValueError:
        cover = {}
    try:
        analysis = _json.loads(run.analysis_json or "{}")
    except ValueError:
        analysis = {}
    try:
        style = _sanitize_style(_json.loads(run.style_json or "{}")) if run.style_json else _DEFAULT_DOC_STYLE
    except ValueError:
        style = _DEFAULT_DOC_STYLE
    # Recompute the before→after lift (score_after isn't persisted).
    score_before = analysis.get("overall_score")
    try:
        score_after = scoring.score_resume(_resume_to_text(resume), run.jd_text).get("overall_score")
    except Exception:
        score_after = None
    downloads = _download_urls_for_job(current_user.id, job_id)
    return jsonify({
        "status": "ok",
        "result": {
            "status": "ok",
            "job_id": job_id,
            "resume": resume,
            "cover_letter": cover,
            "analysis": analysis,
            "match": {"covered": [], "missing": []},
            "gaps": resume.get("gaps", []) if isinstance(resume, dict) else [],
            "score_before": score_before,
            "score_after": score_after,
            "style": style,
            "expires_in_days": GENERATED_RETENTION_DAYS,
            "resume_docx_url": downloads.get("resume_docx_url"),
            "resume_pdf_url": downloads.get("resume_pdf_url"),
            "cover_docx_url": downloads.get("cover_docx_url"),
            "cover_pdf_url": downloads.get("cover_pdf_url"),
            **_credits_payload(current_user),
        },
    })




@app.route("/api/generation/<job_id>/status", methods=["POST"])
def update_generation_status(job_id):
    """Application tracker: update the status / notes of a generation."""
    user = _current_user()
    if not user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    run = GenerationRun.query.filter_by(job_id=job_id, user_id=user.id).first()
    if not run:
        return jsonify({"status": "error", "message": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    if "status" in data:
        if data.get("status") not in ALLOWED_APP_STATUSES:
            return jsonify({"status": "error", "message": "Unknown status."}), 400
        run.status = data["status"]
    if "notes" in data:
        run.notes = _clean_str(data.get("notes"), 2000)
    db.session.commit()
    return jsonify({"status": "ok", "job_status": run.status, "notes": run.notes or ""})


@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    """Store a signed-in user's rating + optional review. Consented reviews
    rated >= 4 are auto-published as landing testimonials."""
    user = _current_user()
    if not user:
        return jsonify({"status": "error", "message": "Please sign in to leave feedback."}), 401
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    if rating < 1 or rating > 5:
        return jsonify({"status": "error", "message": "Please choose a rating from 1 to 5."}), 400
    consent = bool(data.get("consent_to_publish"))
    comment = _clean_str(data.get("comment"), 2000)
    display_name = (_clean_str(data.get("display_name"), 120) or (user.full_name or "")) if consent else ""
    role = _clean_str(data.get("role"), 120) if consent else ""
    fb = Feedback(
        user_id=user.id,
        rating=rating,
        comment=comment or None,
        consent_to_publish=consent,
        display_name=display_name or None,
        role=role or None,
    )
    db.session.add(fb)
    db.session.commit()
    published = bool(consent and rating >= 4 and comment)
    return jsonify({"status": "ok", "published": published})


def _admin_ok():
    return bool(session.get("admin_ok"))


@app.route("/admin", methods=["GET", "POST"])
def admin_portal():
    admin_user = os.environ.get("TAILORBACK_ADMIN_USER", "admin")
    admin_password = os.environ.get("TAILORBACK_ADMIN_PASSWORD", "tailorback-admin")
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if hmac.compare_digest(username, admin_user) and hmac.compare_digest(password, admin_password):
            session["admin_ok"] = True
            return redirect(url_for("admin_portal"))
        error = "Invalid admin credentials."
    if not _admin_ok():
        return render_template("admin_login.html", error=error)
    users = User.query.order_by(User.created_at.desc()).all()
    runs = GenerationRun.query.order_by(GenerationRun.created_at.desc()).limit(100).all()
    grants = CreditGrant.query.order_by(CreditGrant.created_at.desc()).limit(60).all()
    user_rows = []
    for user in users:
        credits = _credits_payload(user)
        user_rows.append({
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "provider": user.provider,
            "email_verified": bool(user.email_verified),
            "country": user.country,
            "zip_code": user.zip_code,
            "current_pack": user.current_pack or "free",
            "created_at": user.created_at,
            **credits,
        })
    run_rows = []
    for run in runs:
        user = db.session.get(User, run.user_id)
        row = _generation_payload(run, include_inputs=True)
        row["user_email"] = user.email if user else "deleted-user"
        run_rows.append(row)
    grant_rows = []
    for grant in grants:
        user = db.session.get(User, grant.user_id)
        grant_rows.append({
            "id": grant.id,
            "user_email": user.email if user else "deleted-user",
            "credits": grant.credits,
            "source": grant.source,
            "pack_id": grant.pack_id or "-",
            "note": grant.note or "",
            "stripe_session_id": grant.stripe_session_id,
            "created_at": grant.created_at,
        })
    feedback_rows = []
    for fb in Feedback.query.order_by(Feedback.created_at.desc()).limit(80).all():
        fu = db.session.get(User, fb.user_id)
        feedback_rows.append({
            "id": fb.id,
            "user_email": fu.email if fu else "deleted-user",
            "rating": fb.rating,
            "comment": fb.comment or "",
            "consent": bool(fb.consent_to_publish),
            "display_name": fb.display_name or "",
            "role": fb.role or "",
            "published": bool(fb.consent_to_publish and fb.rating >= 4 and (fb.comment or "").strip()),
            "created_at": fb.created_at,
        })
    total_credits_remaining = sum(row["credits_remaining"] for row in user_rows)
    verified_users = sum(1 for row in user_rows if row["email_verified"])
    paid_credit_grants = sum(max(0, row["credits"]) for row in grant_rows)
    avg_seconds_values = [row["generation_seconds"] for row in run_rows if row.get("generation_seconds") is not None]
    stats = {
        "total_users": len(user_rows),
        "verified_users": verified_users,
        "total_generations": GenerationRun.query.count(),
        "visible_generations": len(run_rows),
        "credits_remaining": total_credits_remaining,
        "credits_granted": paid_credit_grants,
        "avg_generation_seconds": round(sum(avg_seconds_values) / len(avg_seconds_values), 1) if avg_seconds_values else None,
        "feedback_count": len(feedback_rows),
        "avg_rating": round(sum(r["rating"] for r in feedback_rows) / len(feedback_rows), 1) if feedback_rows else None,
    }
    return render_template(
        "admin.html",
        users=user_rows,
        runs=run_rows,
        grants=grant_rows,
        feedback=feedback_rows,
        stats=stats,
    )


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_ok", None)
    return redirect(url_for("admin_portal"))


@app.route("/admin/users/<int:user_id>/credits", methods=["POST"])
def admin_adjust_credits(user_id):
    if not _admin_ok():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    action = (request.form.get("action") or "").strip()
    note = (request.form.get("note") or "").strip()
    try:
        amount = int(request.form.get("amount") or "0")
    except ValueError:
        amount = -1
    if amount < 0:
        return redirect(url_for("admin_portal", error="invalid_amount"))
    try:
        _admin_adjust_user_credits(user, action, amount, note)
    except ValueError:
        return redirect(url_for("admin_portal", error="invalid_action"))
    return redirect(url_for("admin_portal", adjusted=user.id))


LEGAL_UPDATED = "June 3, 2026"


@app.route("/terms")
def terms_page():
    return render_template(
        "terms.html",
        updated=LEGAL_UPDATED,
        free_limit=FREE_LIMIT,
        retention_days=GENERATED_RETENTION_DAYS,
        packs=_public_credit_packs(),
    )


@app.route("/privacy")
def privacy_page():
    return render_template(
        "privacy.html",
        updated=LEGAL_UPDATED,
        retention_days=GENERATED_RETENTION_DAYS,
    )


@app.route("/favicon.ico")
def favicon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="10" fill="#1a1714"/>
<text x="14" y="43" font-family="Georgia,serif" font-size="36" font-weight="700" fill="#f4efe6">T</text>
<circle cx="48" cy="43" r="4" fill="#c8462e"/>
</svg>"""
    return Response(svg, mimetype="image/svg+xml")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
