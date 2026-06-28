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
import functools
import threading
import queue as _queue
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    jsonify,
    make_response,
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

from services import cv_parser, jd_source, llm, docx_builder, scoring, provider_billing, pdf_builder

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
# After this many days, personal free-text in generation records is purged (GDPR).
GENERATION_RETENTION_DAYS = int(os.environ.get("GENERATION_RETENTION_DAYS", "90"))

# --- Document style gallery (drives docx_builder + the in-app editor) ---
ALLOWED_TEMPLATES = {"editorial", "classic", "serif", "minimal", "sidebar",
                     "skyline", "executive", "aurora", "spotlight"}
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

# --- Database (SQLite for local dev; DATABASE_URL for Postgres on deploy) ---
_db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(BASE, "tailorback.db"))
# Managed Postgres (Render/Heroku/Supabase) hand out postgres://; SQLAlchemy needs postgresql://.
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}  # survive idle DB drops
db = SQLAlchemy(app)

# --- Session secret (required for OAuth login state) ---
app.secret_key = os.environ.get("FLASK_SECRET_KEY")
if not app.secret_key:
    if os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("FLASK_SECRET_KEY must be set in production.")
    app.secret_key = "dev-only-change-me"

# Sessions expire after 14 days of issuance (logins are marked permanent).
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)

# --- Production hardening: trust the platform's TLS proxy + secure cookies ---
if os.environ.get("FLASK_ENV") == "production":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PREFERRED_URL_SCHEME="https",
    )

# --- OAuth (Google) ---
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _google_auth_enabled():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    provider = db.Column(db.String(32), nullable=False)
    provider_id = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verification_token = db.Column(db.String(64), unique=True, nullable=True)
    password_reset_token = db.Column(db.String(128), unique=True, nullable=True)
    password_reset_expires = db.Column(db.DateTime, nullable=True)
    # Bumped on password change/reset to invalidate all existing sessions.
    session_version = db.Column(db.Integer, default=0, nullable=False)
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
    model_status = db.Column(db.Text, nullable=True)  # comma-joined status trail; can exceed 64 chars
    model_provider = db.Column(db.String(32), nullable=True)
    model_name = db.Column(db.String(128), nullable=True)
    generation_seconds = db.Column(db.Float, nullable=True)
    prompt_tokens = db.Column(db.Integer, nullable=True)
    completion_tokens = db.Column(db.Integer, nullable=True)
    total_tokens = db.Column(db.Integer, nullable=True)
    est_cost_usd = db.Column(db.Float, nullable=True)
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


class ProviderBudget(db.Model):
    """Admin-set starting balance + refill threshold per LLM provider, so the
    dashboard can count spend down and flag when to refill."""
    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), unique=True, nullable=False)  # openai|gemini|claude
    starting_balance = db.Column(db.Float, nullable=True)   # USD you topped up to
    refill_threshold = db.Column(db.Float, nullable=True)   # alert when balance dips below this
    balance_since = db.Column(db.DateTime, nullable=True)   # spend counted from this reset point
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ContactMessage(db.Model):
    """A message submitted through the public Contact form."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    mobile = db.Column(db.String(40), nullable=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


ALLOWED_APP_STATUSES = ("not_applied", "applied", "interviewing", "offer", "rejected")
LLM_PROVIDERS = ("openai", "gemini", "claude")


def _ensure_schema():
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("postgresql"):
        _ensure_schema_postgres()
        return
    if not uri.startswith("sqlite:///"):
        return
    columns = {
        "user": {
            "full_name": "VARCHAR(255)",
            "password_hash": "VARCHAR(255)",
            "email_verified": "BOOLEAN DEFAULT 0",
            "email_verification_token": "VARCHAR(64)",
            "password_reset_token": "VARCHAR(128)",
            "password_reset_expires": "DATETIME",
            "session_version": "INTEGER DEFAULT 0",
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
            "prompt_tokens": "INTEGER",
            "completion_tokens": "INTEGER",
            "total_tokens": "INTEGER",
            "est_cost_usd": "FLOAT",
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


def _ensure_schema_postgres():
    """db.create_all() builds fresh tables, but it never *alters* existing ones.
    Postgres (unlike SQLite) enforces VARCHAR lengths, so columns that hold long
    or growing values must be widened to TEXT to avoid StringDataRightTruncation
    on insert. Idempotent: only alters a column that isn't already TEXT."""
    widen = {
        "generation_run": ["model_status"],
    }
    # New columns added after the table already existed in production.
    add_columns = {
        "user": [("session_version", "INTEGER NOT NULL DEFAULT 0")],
    }
    with db.engine.connect() as conn:
        for table, cols in widen.items():
            for col in cols:
                row = conn.exec_driver_sql(
                    "SELECT data_type FROM information_schema.columns "
                    f"WHERE table_name = '{table}' AND column_name = '{col}'"
                ).fetchone()
                if row and row[0] != "text":
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ALTER COLUMN {col} TYPE TEXT")
        for table, cols in add_columns.items():
            for col, ddl in cols:
                exists = conn.exec_driver_sql(
                    "SELECT 1 FROM information_schema.columns "
                    f"WHERE table_name = '{table}' AND column_name = '{col}'"
                ).fetchone()
                if not exists:
                    conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN {col} {ddl}')
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


def _reserve_generation(user):
    """Atomically claim one generation credit. Concurrency-safe: a conditional
    UPDATE prevents two simultaneous requests both spending the last credit.
    Returns True if a credit was reserved, False if out of credits."""
    limit = FREE_LIMIT + _paid_credits(user)
    updated = (db.session.query(User)
               .filter(User.id == user.id, User.generations_used < limit)
               .update({User.generations_used: User.generations_used + 1},
                       synchronize_session=False))
    db.session.commit()
    if updated:
        db.session.refresh(user)
        return True
    return False


def _refund_generation(user):
    """Give back a reserved credit when a generation fails."""
    (db.session.query(User)
     .filter(User.id == user.id, User.generations_used > 0)
     .update({User.generations_used: User.generations_used - 1}, synchronize_session=False))
    db.session.commit()
    db.session.refresh(user)


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
        return None
    # Session invalidation: a password change/reset bumps session_version, so
    # cookies issued before it (including a stolen one) stop authenticating.
    if session.get("sv") != (user.session_version or 0):
        session.clear()
        return None
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
        "total_tokens": run.total_tokens,
        "est_cost_usd": run.est_cost_usd,
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
            cover.get("greeting"),
            cover.get("salutation"),
            *(cover.get("body_paragraphs") or []),
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
    _purge_old_generation_data()


def _purge_old_generation_data():
    """GDPR retention: after GENERATION_RETENTION_DAYS, strip the personal free
    text (CV, job description, tailored documents) from old generation records,
    keeping only the lightweight application-tracker fields (company/role/status).
    Idempotent — only touches rows not already purged."""
    cutoff = datetime.utcnow() - timedelta(days=GENERATION_RETENTION_DAYS)
    try:
        (db.session.query(GenerationRun)
         .filter(GenerationRun.created_at < cutoff, GenerationRun.cv_text != "")
         .update({GenerationRun.cv_text: "", GenerationRun.jd_text: "",
                  GenerationRun.resume_json: None, GenerationRun.cover_letter_json: None,
                  GenerationRun.analysis_json: None}, synchronize_session=False))
        db.session.commit()
    except Exception:
        db.session.rollback()


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


def _skill_groups(skills):
    """Normalise skills into [{'category': str|None, 'items': [str]}], accepting
    the grouped shape, a dict, or a legacy flat list of strings."""
    if not skills:
        return []
    if isinstance(skills, dict):
        return [{"category": k, "items": v} for k, v in skills.items() if v]
    if isinstance(skills, list) and skills and isinstance(skills[0], dict):
        out = []
        for g in skills:
            if not isinstance(g, dict):
                continue
            items = g.get("items") or []
            if items:
                out.append({"category": (g.get("category") or "").strip() or None, "items": items})
        return out
    return [{"category": None, "items": [s for s in skills if s]}]


def _flatten_skills(skills):
    flat = []
    for g in _skill_groups(skills):
        flat.extend(g["items"])
    return flat


def _clean_skills(skills):
    """Sanitize skills, preserving grouped structure when present."""
    groups = _skill_groups(skills)
    if not groups:
        return []
    if len(groups) == 1 and groups[0]["category"] is None:
        return _clean_str_list(groups[0]["items"], limit=60, item_limit=120)
    out = []
    for g in groups[:8]:
        items = _clean_str_list(g["items"], limit=40, item_limit=120)
        if items:
            out.append({"category": _clean_str(g["category"], 60), "items": items})
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
            "location": _clean_str(job.get("location"), 200),
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
    languages = []
    for lng in (resume.get("languages") or [])[:15]:
        if isinstance(lng, dict) and _clean_str(lng.get("name"), 60):
            languages.append({"name": _clean_str(lng.get("name"), 60),
                              "level": _clean_str(lng.get("level"), 40)})
        elif isinstance(lng, str) and lng.strip():
            languages.append({"name": _clean_str(lng, 60), "level": ""})
    return {
        "name": _clean_str(resume.get("name"), 200),
        "headline": _clean_str(resume.get("headline"), 200),
        "languages": languages,
        "contact": {
            "email": _clean_str(contact.get("email"), 200),
            "phone": _clean_str(contact.get("phone"), 80),
            "location": _clean_str(contact.get("location"), 200),
            "links": _clean_str_list(contact.get("links"), limit=10, item_limit=300),
        },
        "summary": _clean_str(resume.get("summary"), 3000),
        "skills": _clean_skills(resume.get("skills")),
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
        parts.append("Skills\n" + ", ".join(_flatten_skills(resume["skills"])))
    if resume.get("experience"):
        parts.append("Experience")
        for j in resume["experience"]:
            loc = f", {j.get('location')}" if j.get("location") else ""
            parts.append(f"{j.get('title','')} - {j.get('company','')}{loc} ({j.get('dates','')})")
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


def _render_pdfs(resume, resume_path, cover_path, style=None):
    """Résumé PDF via the HTML/CSS engine (designer-grade, ATS-clean); cover
    letter via the docx -> LibreOffice path. Falls back to LibreOffice for the
    résumé too if WeasyPrint isn't available on the host."""
    resume_pdf = os.path.splitext(resume_path)[0] + ".pdf"
    if pdf_builder.render_resume_pdf(resume, resume_pdf, style=style):
        cover_pdf = docx_builder.to_pdfs([cover_path]).get(cover_path)
        return resume_pdf, cover_pdf
    pdfs = docx_builder.to_pdfs([resume_path, cover_path])
    return pdfs.get(resume_path), pdfs.get(cover_path)


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
    resume_pdf, cover_pdf = _render_pdfs(resume, resume_path, cover_path, style)
    for path in (resume_path, cover_path, resume_pdf, cover_pdf):
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
    session["sv"] = user.session_version or 0
    session.permanent = True


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
        "has_feedback": Feedback.query.filter_by(user_id=user.id).first() is not None,
        **credits,
    }


def _make_email_verification_token():
    return secrets.token_urlsafe(32)


# ---- lightweight in-memory rate limiting (single-instance friendly) ----
_rl_lock = threading.Lock()
_rl_hits = {}


def _rl_identity():
    uid = session.get("user_id")
    if uid:
        return f"u{uid}"
    fwd = request.headers.get("X-Forwarded-For", "")
    ip = (fwd.split(",")[0].strip() if fwd else None) or request.remote_addr or "anon"
    return ip


def rate_limit(max_calls, per_seconds, methods=None):
    """Throttle a route to max_calls per per_seconds, keyed by user or client IP."""
    method_set = {m.upper() for m in methods} if methods else None

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if method_set and request.method.upper() not in method_set:
                return fn(*args, **kwargs)
            now = time.time()
            key = f"{fn.__name__}:{_rl_identity()}"
            with _rl_lock:
                hits = [t for t in _rl_hits.get(key, ()) if now - t < per_seconds]
                if len(hits) >= max_calls:
                    retry = max(1, int(per_seconds - (now - hits[0])) + 1)
                    resp = jsonify({"status": "rate_limited",
                                    "message": "Too many requests. Please wait a moment and try again."})
                    resp.status_code = 429
                    resp.headers["Retry-After"] = str(retry)
                    return resp
                hits.append(now)
                _rl_hits[key] = hits
                if len(_rl_hits) > 4000:  # opportunistic cleanup
                    for k in [k for k, v in _rl_hits.items() if not v or now - v[-1] > per_seconds]:
                        _rl_hits.pop(k, None)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# --- CSRF: same-origin enforcement on state-changing requests ----------------
# The frontend is same-origin fetch/forms, so a cross-site attacker page cannot
# set a matching Origin/Referer. Server-to-server POSTs (Stripe webhook) are
# exempt — they carry their own signature verification instead.
_CSRF_EXEMPT_PATHS = {"/stripe/webhook"}


@app.before_request
def _csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.path in _CSRF_EXEMPT_PATHS:
        return
    from urllib.parse import urlparse
    host = request.host
    origin = request.headers.get("Origin")
    if origin:
        if urlparse(origin).netloc != host:
            abort(403)
        return
    referer = request.headers.get("Referer")
    if referer and urlparse(referer).netloc == host:
        return
    # No trustworthy same-origin signal on a mutating request — reject.
    abort(403)


def _mail_configured():
    """True when an email transport is available (Resend API key or SMTP host)."""
    sp = os.environ.get("SMTP_PASSWORD") or ""
    return bool(os.environ.get("RESEND_API_KEY") or sp.startswith("re_")
                or os.environ.get("SMTP_HOST"))


def _smtp_send(to_email, subject, body):
    """Send a plain-text email. Prefers the Resend HTTPS API because PaaS hosts
    (Render, Heroku, ...) block outbound SMTP ports (25/465/587). Falls back to
    SMTP only when no Resend key is present (local dev). Raises on failure."""
    sender = os.environ.get("SMTP_FROM") or "noreply@tailorback.com"
    from_name = os.environ.get("SMTP_FROM_NAME", "TailorBack")
    from_header = f"{from_name} <{sender}>" if from_name else sender
    # Resend API key may be set explicitly, or reused from SMTP_PASSWORD (re_...).
    sp = os.environ.get("SMTP_PASSWORD") or ""
    resend_key = os.environ.get("RESEND_API_KEY") or (sp if sp.startswith("re_") else None)
    if resend_key:
        import requests
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={"from": from_header, "to": [to_email], "subject": subject,
                  "text": body, "reply_to": OPERATOR_EMAIL},
            timeout=20)
        if resp.status_code >= 300:
            raise RuntimeError(f"Resend API {resp.status_code}: {resp.text[:300]}")
        return

    # --- SMTP fallback (local dev / self-hosted relay) ---
    import smtplib
    from email.message import EmailMessage
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{sender}>" if from_name else sender
    msg["Reply-To"] = OPERATOR_EMAIL
    msg["To"] = to_email
    msg.set_content(body)
    if os.environ.get("SMTP_USE_SSL") == "1":
        with smtplib.SMTP_SSL(host, port, timeout=20) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)


def _send_activation_email(user):
    activation_url = url_for("activate_email", token=user.email_verification_token, _external=True)
    body = (
        "Welcome to TailorBack!\n\n"
        "Activate your account by opening this link:\n"
        f"{activation_url}\n\n"
        "If you did not sign up, you can ignore this email.\n\n"
        f"Need help? Email us at {OPERATOR_EMAIL}."
    )
    if _mail_configured():
        try:
            _smtp_send(user.email, "Activate your TailorBack account", body)
            app.logger.info("Activation email sent to %s", user.email)
        except Exception:
            app.logger.exception("Failed to send activation email to %s", user.email)
    else:
        # No mail provider configured: log the link so dev/self-host can still activate.
        if os.environ.get("FLASK_ENV") != "production":
            app.logger.warning("SMTP not configured; activation link for %s: %s", user.email, activation_url)
    return activation_url


def _send_password_reset_email(user):
    reset_url = url_for("reset_password_page", token=user.password_reset_token, _external=True)
    body = (
        "We received a request to reset your TailorBack password.\n\n"
        "Choose a new password by opening this link (valid for 1 hour):\n"
        f"{reset_url}\n\n"
        "If you did not request this, ignore this email, your password will not change.\n\n"
        f"Need help? Email us at {OPERATOR_EMAIL}."
    )
    if _mail_configured():
        try:
            _smtp_send(user.email, "Reset your TailorBack password", body)
            app.logger.info("Password reset email sent to %s", user.email)
        except Exception:
            app.logger.exception("Failed to send password reset email to %s", user.email)
    else:
        if os.environ.get("FLASK_ENV") != "production":
            app.logger.warning("SMTP not configured; reset link for %s: %s", user.email, reset_url)
    return reset_url


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


# --- Operator / legal identity -------------------------------------------------
# TailorBack is run by an individual (not a company), established in Germany.
# These feed the Impressum, Privacy Policy, Terms, and ROPA. Fill the bracketed
# items (postal address, support email) before public launch.
OPERATOR_NAME = "Amith AshokReddy Rajolkar"
OPERATOR_JURISDICTION = "Germany"
OPERATOR_CITY = "Berlin, Germany"
OPERATOR_ADDRESS = "[street and number], [postal code] Berlin, Germany"
OPERATOR_EMAIL = "contact@tailorback.com"     # general support / data requests
FINANCE_EMAIL = "finance@tailorback.com"      # billing / refunds
SUPERVISORY_AUTHORITY = (
    "Berliner Beauftragte für Datenschutz und Informationsfreiheit (BlnBDI)"
)


@app.context_processor
def _inject_globals():
    return {
        "current_year": datetime.utcnow().year,
        "operator_name": OPERATOR_NAME,
        "operator_jurisdiction": OPERATOR_JURISDICTION,
        "operator_city": OPERATOR_CITY,
        "operator_address": OPERATOR_ADDRESS,
        "operator_email": OPERATOR_EMAIL,
        "finance_email": FINANCE_EMAIL,
        "supervisory_authority": SUPERVISORY_AUTHORITY,
        "google_auth_enabled": _google_auth_enabled(),
    }


def _published_testimonials(limit=3):
    """Auto-published reviews for the landing testimonials: consented + rating >= 4.

    Returns the most recent ones with a non-empty comment. When empty, the
    landing page hides the whole 'What job-seekers say' section."""
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
    resp = make_response(render_template("index.html",
                           user_email=u.email if u else None,
                           credits_used=credits["credits_used"],
                           credits_remaining=credits["credits_remaining"],
                           credits_limit=credits["credits_limit"],
                           free_credits_limit=FREE_LIMIT,
                           credit_packs=_public_credit_packs(),
                           testimonials=_published_testimonials(),
                           billing_enabled=bool(os.environ.get("STRIPE_SECRET_KEY"))))
    # Never cache the authenticated landing: a stale copy could show a
    # previous user's account details after switching accounts.
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

@app.route("/auth/google")
def auth_google():
    if not _google_auth_enabled():
        abort(503)
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
    # Only trust a Google-verified email — otherwise it could be used to link to
    # or hijack an existing email/password account with the same address.
    if info.get("email_verified") is False:
        return "Login failed: your Google email is not verified.", 400
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
@rate_limit(8, 600)
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
    if not (8 <= len(password) <= 128):
        return jsonify({"status": "error", "message": "Password must be 8 to 128 characters."}), 400
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
    # Remember which email this browser is awaiting activation for, so the
    # "check your email" screen can poll and auto-advance once it's activated
    # (even when the link is opened on another device).
    session["pending_activation_email"] = user.email
    payload = {
        "status": "verification_required",
        "message": "Account created. Check your email to activate your account.",
    }
    if os.environ.get("FLASK_ENV") != "production":
        payload["activation_url"] = activation_url
    return jsonify(payload)


@app.route("/api/auth/activation-status")
def activation_status():
    """Poll target for the 'check your email' screen: has the account this
    browser just signed up with been activated yet (possibly on another device)?
    Reads the email from the session, so it can't be used to probe other emails."""
    email = session.get("pending_activation_email")
    if not email:
        return jsonify({"activated": False})
    user = User.query.filter_by(email=email).first()
    if user and user.email_verified:
        session.pop("pending_activation_email", None)
        return jsonify({"activated": True, "email": email})
    return jsonify({"activated": False})


@app.route("/auth/activate/<token>")
def activate_email(token):
    user = User.query.filter_by(email_verification_token=token).first()
    if not user:
        return redirect(url_for("index", auth="invalid_activation"))
    user.email_verified = True
    user.email_verification_token = None
    db.session.commit()
    # Do NOT auto-login: send the user to the sign-in screen with a confirmation.
    return redirect(url_for("index", auth="activated"))


@app.route("/api/auth/signin", methods=["POST"])
@rate_limit(12, 300)
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


@app.route("/api/auth/forgot", methods=["POST"])
@rate_limit(5, 900)
def auth_forgot_password():
    """Start a password reset. Always returns a generic message so it never
    reveals whether an email is registered."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    generic = {"status": "ok",
               "message": "If an account exists for that email, a reset link is on its way."}
    if not email:
        return jsonify(generic)
    user = User.query.filter_by(email=email).first()
    # Only password (email/password) accounts can reset; Google-only accounts can't.
    if user and user.password_hash:
        user.password_reset_token = _make_email_verification_token()
        user.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()
        reset_url = _send_password_reset_email(user)
        # Dev convenience: when no mailer is configured, surface the link.
        if not _mail_configured() and os.environ.get("FLASK_ENV") != "production":
            return jsonify({**generic, "reset_url": reset_url})
    return jsonify(generic)


@app.route("/auth/reset/<token>")
def reset_password_page(token):
    user = User.query.filter_by(password_reset_token=token).first()
    valid = bool(user and user.password_reset_expires
                 and user.password_reset_expires > datetime.utcnow())
    return render_template("reset_password.html", token=token, valid=valid)


@app.route("/api/auth/reset", methods=["POST"])
@rate_limit(10, 600)
def auth_reset_password():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    password = data.get("password") or ""
    if not (8 <= len(password) <= 128):
        return jsonify({"status": "error",
                        "message": "Password must be 8 to 128 characters."}), 400
    user = User.query.filter_by(password_reset_token=token).first() if token else None
    if not user or not user.password_reset_expires or user.password_reset_expires < datetime.utcnow():
        return jsonify({"status": "error",
                        "message": "This reset link is invalid or has expired. Request a new one."}), 400
    user.password_hash = generate_password_hash(password, method="pbkdf2:sha256")
    user.password_reset_token = None
    user.password_reset_expires = None
    user.email_verified = True  # completing reset proves they control the inbox
    user.session_version = (user.session_version or 0) + 1  # invalidate any existing sessions
    db.session.commit()
    app.logger.info("Password reset completed for %s", user.email)
    _send_password_changed_email(user)   # security notice (same as the in-app change)
    return jsonify({"status": "ok",
                    "message": "Your password has been reset. You can now sign in."})


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/account/delete", methods=["POST"])
def account_delete():
    """Self-service erasure: a signed-in user permanently deletes their own
    account and all associated data. Requires an explicit confirmation flag."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Not signed in."}), 401
    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"error": "Deletion not confirmed."}), 400
    email = user.email
    _erase_user_completely(user)
    session.clear()
    app.logger.warning("User self-deleted account %s", email)
    return jsonify({"status": "ok"})


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
@rate_limit(20, 300)
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

    # Reserve one credit atomically now (inputs are valid). This is the real
    # concurrency-safe gate — the check above is just a fast pre-filter. The
    # credit is refunded inside the stream if generation fails.
    if not _reserve_generation(current_user):
        return jsonify({"status": "error",
                        "message": "You're out of generations. Buy a credit pack to keep tailoring.",
                        **_credits_payload(current_user)}), 402

    def event_stream():
        q = _queue.Queue()
        stages = []
        generation_started_at = time.perf_counter()

        def on_status(stage):
            stages.append(stage)
            q.put({"type": "status", "stage": stage})

        holder = {}
        usage = {}

        def worker():
            try:
                holder["result"] = llm.generate_all(cv_text, jd_text, on_status=on_status, usage_sink=usage)
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
            _refund_generation(current_user)   # generation failed — give the credit back
            yield f"data: {_json.dumps({'type': 'error', 'message': holder['error']})}\n\n"
            return

        try:
            result = holder["result"]
            provider, model_name = _model_from_stages(stages)
            generation_seconds = round(time.perf_counter() - generation_started_at, 2)
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
            resume_pdf, cover_pdf = _render_pdfs(resume, resume_path, cover_path)
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
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                est_cost_usd=usage.get("est_cost_usd"),
                company=(_job.get("company") or None),
                role=(_job.get("role") or None),
                status="not_applied",
            )
            db.session.add(run)
            db.session.commit()

            # The credit was already reserved atomically before generation started
            # (refunded on failure), so nothing to charge here.
            credits = _credits_payload(current_user)

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
        except Exception:
            app.logger.exception("Post-generation processing failed")
            try:
                db.session.rollback()
            except Exception:
                pass
            _refund_generation(current_user)   # nothing usable was saved — refund
            yield f"data: {_json.dumps({'type': 'error', 'message': 'Something went wrong while building your documents. Please try again.'})}\n\n"
            return

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


def _nice_download_name(fname, user):
    """A clean download filename: Lastname-Company_resume.pdf (no random suffix).
    Pulls the person's last name + target company from the GenerationRun."""
    ext = os.path.splitext(fname)[1] or ".pdf"
    kind = "cover-letter" if "coverletter" in fname.lower() else "resume"
    job_id = fname.split("__", 1)[0] if "__" in fname else ""
    last, company = "", ""
    run = (GenerationRun.query.filter_by(job_id=job_id, user_id=user.id).first()
           if job_id else None)
    if run:
        company = (run.company or "").strip()
        try:
            nm = (_json.loads(run.resume_json or "{}").get("name") or "").strip()
        except Exception:
            nm = ""
        last = nm.split()[-1] if nm else ""
    stem = _slugify(last, company) or "tailorback"
    return f"{stem}_{kind}{ext}"


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
    return send_from_directory(GENERATED, fname, as_attachment=True,
                               download_name=_nice_download_name(fname, current_user))


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
@rate_limit(40, 600)   # generous for real editing; blocks scripted LLM-spend abuse
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
        out = _clean_skills(refined.get("skills"))
    elif kind == "bullets":
        out = _clean_str_list(refined.get("bullets"), limit=20, item_limit=1000)
    else:
        out = _sanitize_cover(refined)
    return jsonify({"status": "ok", "kind": kind, "content": out})


@app.route("/api/writing-check", methods=["POST"])
@rate_limit(20, 600)
def writing_check():
    """Phase 9: grammar / writing-quality suggestions for the resume. Free."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    if not any(os.environ.get(k) for k in (
            "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")):
        return jsonify({"status": "error", "message": "Server missing an LLM API key."}), 500
    text = _clean_str((request.get_json(silent=True) or {}).get("text"), 12000)
    if not text:
        return jsonify({"status": "error", "message": "Nothing to check."}), 400
    try:
        result = llm.writing_check(text)
    except Exception:
        app.logger.exception("Writing check failed")
        return jsonify({"status": "error", "message": "Could not check the writing. Try again."}), 502
    issues = []
    for it in (result.get("issues") or [])[:12]:
        if not isinstance(it, dict):
            continue
        sev = it.get("severity") if it.get("severity") in ("high", "medium", "low") else "medium"
        issues.append({
            "excerpt": _clean_str(it.get("excerpt"), 240),
            "problem": _clean_str(it.get("problem"), 300),
            "suggestion": _clean_str(it.get("suggestion"), 400),
            "severity": sev,
        })
    return jsonify({"status": "ok", "issues": issues})


@app.route("/api/rescore", methods=["POST"])
def rescore():
    """Phase 12: re-score the edited resume against the original job (deterministic).
    Owner-scoped by job_id; free, no LLM call."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    data = request.get_json(silent=True) or {}
    run = GenerationRun.query.filter_by(job_id=data.get("job_id"), user_id=current_user.id).first()
    if not run:
        return jsonify({"status": "error", "message": "Generation not found."}), 404
    resume = data.get("resume") if isinstance(data.get("resume"), dict) else {}
    analysis = scoring.score_resume(_resume_to_text(resume), run.jd_text)
    return jsonify({
        "status": "ok",
        "overall_score": analysis.get("overall_score"),
        "dimensions": analysis.get("dimensions"),
        "missing_keywords": analysis.get("missing_keywords"),
    })


@app.route("/api/interview-prep", methods=["POST"])
@rate_limit(20, 600)
def interview_prep():
    """Phase 11: likely interview questions for a past generation. Owner-scoped, free."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    if not any(os.environ.get(k) for k in (
            "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")):
        return jsonify({"status": "error", "message": "Server missing an LLM API key."}), 500
    run = GenerationRun.query.filter_by(
        job_id=(request.get_json(silent=True) or {}).get("job_id"), user_id=current_user.id).first()
    if not run:
        return jsonify({"status": "error", "message": "Generation not found."}), 404
    try:
        resume = _json.loads(run.resume_json or "{}")
    except ValueError:
        resume = {}
    try:
        result = llm.interview_questions(
            run.jd_text, _resume_to_text(resume), company=run.company, role=run.role)
    except Exception:
        app.logger.exception("Interview prep failed")
        return jsonify({"status": "error", "message": "Could not generate questions. Try again."}), 502
    allowed = ("technical", "behavioral", "role-specific", "gap")
    questions = []
    for q in (result.get("questions") or [])[:12]:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        cat = q.get("category") if q.get("category") in allowed else "role-specific"
        questions.append({
            "question": _clean_str(q.get("question"), 400),
            "category": cat,
            "why": _clean_str(q.get("why"), 400),
            "tip": _clean_str(q.get("tip"), 600),
        })
    return jsonify({"status": "ok", "questions": questions})


@app.route("/api/draft", methods=["POST"])
@rate_limit(120, 600)
def save_draft():
    """Autosave edited content WITHOUT rebuilding documents (cheap), so a refresh
    or closed tab never loses edits — reopening the generation restores them.
    Owner-scoped; no credit cost."""
    current_user = _current_user()
    if not current_user:
        return jsonify({"status": "error", "message": "Please sign in."}), 401
    data = request.get_json(silent=True) or {}
    job_id = (data.get("job_id") or "").strip()
    run = GenerationRun.query.filter_by(job_id=job_id, user_id=current_user.id).first()
    if not run:
        return jsonify({"status": "error", "message": "Unknown document."}), 404
    try:
        run.resume_json = _json.dumps(_sanitize_resume(data.get("resume")))
        run.cover_letter_json = _json.dumps(_sanitize_cover(data.get("cover_letter")))
        run.style_json = _json.dumps(_sanitize_style(data.get("style")))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Draft save failed")
        return jsonify({"status": "error", "message": "Could not save."}), 500
    return jsonify({"status": "ok"})


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


def _send_password_changed_email(user):
    """Security notice sent after a password change (from noreply@tailorback.com)."""
    first = (user.full_name or "").split()[0] if user.full_name else ""
    body = (
        f"Hi{(' ' + first) if first else ''},\n\n"
        "The password for your TailorBack account was just changed.\n\n"
        "If this was you, no action is needed.\n"
        "If you did NOT make this change, reset your password right away from the "
        "sign-in screen (\"Forgot password?\") and let us know at "
        f"{OPERATOR_EMAIL}.\n\n"
        "— TailorBack"
    )
    if _mail_configured():
        try:
            _smtp_send(user.email, "Your TailorBack password was changed", body)
            app.logger.info("Password-changed email sent to %s", user.email)
        except Exception:
            app.logger.exception("Failed to send password-changed email to %s", user.email)


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
    if not (8 <= len(new_password) <= 128):
        return jsonify({"status": "error", "message": "New password must be 8 to 128 characters."}), 400
    if new_password != repeat_password:
        return jsonify({"status": "error", "message": "New passwords do not match."}), 400
    if check_password_hash(current_user.password_hash, new_password):
        return jsonify({"status": "error", "message": "Choose a new password that is different."}), 400
    current_user.password_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
    current_user.session_version = (current_user.session_version or 0) + 1  # invalidate other sessions
    db.session.commit()
    session["sv"] = current_user.session_version   # keep THIS session valid
    _send_password_changed_email(current_user)
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
    # One review per account: update the existing row if there is one.
    fb = Feedback.query.filter_by(user_id=user.id).first()
    updated = fb is not None
    if not fb:
        fb = Feedback(user_id=user.id)
        db.session.add(fb)
    fb.rating = rating
    fb.comment = comment or None
    fb.consent_to_publish = consent
    fb.display_name = display_name or None
    fb.role = role or None
    fb.created_at = datetime.utcnow()
    db.session.commit()
    published = bool(consent and rating >= 4 and comment)
    return jsonify({"status": "ok", "published": published, "updated": updated})


def _api_usage_stats():
    """Per-provider usage, estimated spend, balance left, and refill status."""
    from datetime import timedelta
    from sqlalchemy import func

    def _sum(col, *filters):
        q = db.session.query(func.coalesce(func.sum(col), 0))
        for f in filters:
            q = q.filter(f)
        return q.scalar() or 0

    budgets = {b.provider: b for b in ProviderBudget.query.all()}
    total_runs = GenerationRun.query.count()
    week_ago = datetime.utcnow() - timedelta(days=7)
    providers = []
    for p in LLM_PROVIDERS:
        is_p = GenerationRun.model_provider == p
        runs = GenerationRun.query.filter(is_p).count()
        tokens = int(_sum(GenerationRun.total_tokens, is_p))
        spend = float(_sum(GenerationRun.est_cost_usd, is_p))
        b = budgets.get(p)
        since = b.balance_since if b else None
        spend_since = float(_sum(GenerationRun.est_cost_usd, is_p, GenerationRun.created_at >= since)) if since else spend
        starting = b.starting_balance if b else None
        threshold = b.refill_threshold if b else None
        balance = round(starting - spend_since, 2) if starting is not None else None
        wk_spend = float(_sum(GenerationRun.est_cost_usd, is_p, GenerationRun.created_at >= week_ago))
        daily = wk_spend / 7.0
        days_left = round(balance / daily, 1) if (balance is not None and daily > 0) else None
        status = "ok"
        if balance is not None and threshold is not None and balance <= threshold:
            status = "low"
        elif days_left is not None and days_left < 5:
            status = "low"
        providers.append({
            "provider": p,
            "runs": runs,
            "pct": round(runs / total_runs * 100) if total_runs else 0,
            "tokens": tokens,
            "spend": round(spend, 2),
            "balance": balance,
            "starting_balance": starting,
            "refill_threshold": threshold,
            "days_left": days_left,
            "status": status,
            "configured": b is not None and starting is not None,
        })
    return {
        "providers": providers,
        "total_spend": round(float(_sum(GenerationRun.est_cost_usd)), 2),
        "total_tokens": int(_sum(GenerationRun.total_tokens)),
        "refill_needed": [p["provider"] for p in providers if p["status"] == "low"],
    }


def _admin_ok():
    return bool(session.get("admin_ok"))


_DEFAULT_ADMIN_PASSWORD = "tailorback-admin"


@app.route("/admin", methods=["GET", "POST"])
@rate_limit(8, 900, methods={"POST"})   # throttle brute-force on admin login attempts, not page loads
def admin_portal():
    admin_user = os.environ.get("TAILORBACK_ADMIN_USER", "admin")
    admin_password = os.environ.get("TAILORBACK_ADMIN_PASSWORD", _DEFAULT_ADMIN_PASSWORD)
    # SECURITY: never expose the admin panel with the default password in
    # production. If the operator hasn't set a real password, lock it out.
    if os.environ.get("FLASK_ENV") == "production" and (
            not os.environ.get("TAILORBACK_ADMIN_PASSWORD")
            or admin_password == _DEFAULT_ADMIN_PASSWORD):
        app.logger.error("Admin portal disabled: TAILORBACK_ADMIN_PASSWORD not set in production.")
        abort(503)
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if hmac.compare_digest(username, admin_user) and hmac.compare_digest(password, admin_password):
            session["admin_ok"] = True
            return redirect(url_for("admin_portal"))
        app.logger.warning("Failed admin login attempt from %s", _rl_identity())
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
        row = _generation_payload(run, include_inputs=False)
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
    contact_rows = [{
        "id": cm.id, "name": cm.name, "email": cm.email,
        "mobile": cm.mobile or "", "message": cm.message, "created_at": cm.created_at,
    } for cm in ContactMessage.query.order_by(ContactMessage.created_at.desc()).limit(100).all()]
    total_credits_remaining = sum(row["credits_remaining"] for row in user_rows)
    verified_users = sum(1 for row in user_rows if row["email_verified"])
    paid_credit_grants = sum(max(0, row["credits"]) for row in grant_rows)
    avg_seconds_values = [row["generation_seconds"] for row in run_rows if row.get("generation_seconds") is not None]
    api_usage = _api_usage_stats()
    unverified_users = len(user_rows) - verified_users
    fallback_runs = sum(1 for row in run_rows if row.get("model_provider") in ("gemini", "claude"))
    attention_items = []
    if api_usage["refill_needed"]:
        attention_items.append({
            "level": "warn",
            "tab": "api",
            "title": "Provider refill recommended",
            "detail": ", ".join(api_usage["refill_needed"]),
        })
    if unverified_users:
        attention_items.append({
            "level": "warn",
            "tab": "users",
            "title": "Unverified users",
            "detail": f"{unverified_users} account(s) need email verification.",
        })
    if contact_rows:
        attention_items.append({
            "level": "info",
            "tab": "messages",
            "title": "Contact messages",
            "detail": f"{len(contact_rows)} message(s) in the inbox.",
        })
    if fallback_runs:
        attention_items.append({
            "level": "info",
            "tab": "generations",
            "title": "Fallback provider usage",
            "detail": f"{fallback_runs} recent run(s) used Gemini or Claude.",
        })

    stats = {
        "total_users": len(user_rows),
        "verified_users": verified_users,
        "unverified_users": unverified_users,
        "total_generations": GenerationRun.query.count(),
        "visible_generations": len(run_rows),
        "credits_remaining": total_credits_remaining,
        "credits_granted": paid_credit_grants,
        "avg_generation_seconds": round(sum(avg_seconds_values) / len(avg_seconds_values), 1) if avg_seconds_values else None,
        "feedback_count": len(feedback_rows),
        "avg_rating": round(sum(r["rating"] for r in feedback_rows) / len(feedback_rows), 1) if feedback_rows else None,
        "fallback_runs": fallback_runs,
    }
    return render_template(
        "admin.html",
        users=user_rows,
        runs=run_rows,
        recent_runs=run_rows[:5],
        grants=grant_rows,
        feedback=feedback_rows,
        messages=contact_rows,
        api_usage=api_usage,
        billing_sync={
            "openai": provider_billing.openai_admin_configured(),
            "anthropic": provider_billing.anthropic_admin_configured(),
        },
        stats=stats,
        attention_items=attention_items,
    )


@app.route("/admin/api/generation/<job_id>")
def admin_generation_detail(job_id):
    """Fetch sensitive generation details on demand instead of embedding every
    CV/JD/LLM payload in the initial admin page."""
    if not _admin_ok():
        abort(403)
    run = GenerationRun.query.filter_by(job_id=job_id).first()
    if not run:
        return jsonify({"status": "error", "message": "Generation not found."}), 404
    user = db.session.get(User, run.user_id)
    row = _generation_payload(run, include_inputs=True)
    row["user_email"] = user.email if user else "deleted-user"
    return jsonify({"status": "ok", "generation": row})


@app.route("/admin/api/sync")
def admin_api_sync():
    """Pull official billed cost from providers that expose it (admin keys required)."""
    if not _admin_ok():
        abort(403)
    return jsonify(provider_billing.sync_all(days=30))


@app.route("/admin/budgets", methods=["POST"])
def admin_set_budgets():
    if not _admin_ok():
        abort(403)

    def _num(x):
        try:
            return float(x) if x not in (None, "") else None
        except (TypeError, ValueError):
            return None

    for p in LLM_PROVIDERS:
        b = ProviderBudget.query.filter_by(provider=p).first()
        if not b:
            b = ProviderBudget(provider=p)
            db.session.add(b)
        new_balance = _num(request.form.get(f"{p}_balance"))
        b.refill_threshold = _num(request.form.get(f"{p}_threshold"))
        # Each save snapshots "balance as of now": reset the spend countdown
        # whenever a balance is entered (the admin enters their current balance).
        if new_balance is not None:
            b.starting_balance = new_balance
            b.balance_since = datetime.utcnow()
        else:
            b.starting_balance = None
            b.balance_since = None
        b.updated_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for("admin_portal", saved="budgets"))


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


def _delete_user_generation_data(user):
    """Delete a user's generated documents + generation records (and files on
    disk). Does NOT touch the account, credits, or feedback."""
    for doc in GeneratedDocument.query.filter_by(user_id=user.id).all():
        try:
            os.remove(os.path.join(GENERATED, doc.filename))
        except OSError:
            pass
    GeneratedDocument.query.filter_by(user_id=user.id).delete()
    GenerationRun.query.filter_by(user_id=user.id).delete()


def _erase_user_completely(user):
    """Permanently delete a user AND all associated data (account included).
    Shared by the admin 'Delete account' action and self-service deletion."""
    email = user.email
    _delete_user_generation_data(user)
    Feedback.query.filter_by(user_id=user.id).delete()
    CreditGrant.query.filter_by(user_id=user.id).delete()
    # Contact messages are keyed by email, not user_id.
    ContactMessage.query.filter_by(email=email).delete()
    db.session.delete(user)
    db.session.commit()


@app.route("/admin/users/<int:user_id>/delete-data", methods=["POST"])
def admin_delete_user_data(user_id):
    """Delete only the documents/generations a user has produced. The account,
    credits, and feedback stay; the user can keep using TailorBack."""
    if not _admin_ok():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    _delete_user_generation_data(user)
    db.session.commit()
    app.logger.warning("Admin deleted generation data for user %s (id %s)", user.email, user_id)
    return redirect(url_for("admin_portal", datadeleted=1))


@app.route("/admin/users/<int:user_id>/export", methods=["GET"])
def admin_export_user(user_id):
    """Right of access / portability: download everything we hold on a user as a
    single JSON file, so a 'send me my data' request is one click."""
    if not _admin_ok():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    def cols(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    account = cols(user)
    # Never export secrets, even to the data subject.
    account.pop("password_hash", None)
    account.pop("email_verification_token", None)

    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "export_note": (
            "Personal data held by TailorBack for this user, provided under the "
            "GDPR right of access / data portability. Password hashes and "
            "verification tokens are intentionally excluded."
        ),
        "account": account,
        "generations": [
            cols(g) for g in GenerationRun.query.filter_by(user_id=user.id)
            .order_by(GenerationRun.created_at).all()
        ],
        "generated_documents": [
            cols(d) for d in GeneratedDocument.query.filter_by(user_id=user.id)
            .order_by(GeneratedDocument.created_at).all()
        ],
        "feedback": [
            cols(f) for f in Feedback.query.filter_by(user_id=user.id)
            .order_by(Feedback.created_at).all()
        ],
        "credit_grants": [
            cols(c) for c in CreditGrant.query.filter_by(user_id=user.id)
            .order_by(CreditGrant.created_at).all()
        ],
        # Contact messages are keyed by email, not user_id.
        "contact_messages": [
            cols(m) for m in ContactMessage.query.filter_by(email=user.email)
            .order_by(ContactMessage.created_at).all()
        ],
    }

    body = _json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    safe = _re.sub(r"[^A-Za-z0-9_.-]", "_", user.email)
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="tailorback-export-{safe}.json"'
    )
    app.logger.info("Admin exported data for user %s (id %s)", user.email, user_id)
    return resp


@app.route("/admin/users/<int:user_id>/erase", methods=["POST"])
def admin_erase_user(user_id):
    """Right-to-erasure: permanently delete a user AND all of their data
    (account included). Use only when a user requests full deletion."""
    if not _admin_ok():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    email = user.email
    _erase_user_completely(user)
    app.logger.warning("Admin erased account + all data for user %s (id %s)", email, user_id)
    return redirect(url_for("admin_portal", erased=1))


LEGAL_UPDATED = "June 8, 2026"


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


@app.route("/accessibility")
def accessibility_page():
    return render_template("accessibility.html", updated=LEGAL_UPDATED)


@app.route("/impressum")
def impressum_page():
    return render_template("impressum.html", updated=LEGAL_UPDATED)


@app.route("/contact")
def contact_page():
    return render_template("contact.html")


@app.route("/api/contact", methods=["POST"])
@rate_limit(5, 600)
def contact_submit():
    """Public contact form. Stores the message; admins read it in the dashboard."""
    data = request.get_json(silent=True) or {}
    name = _clean_str(data.get("name"), 160)
    email = _clean_str(data.get("email"), 255)
    mobile = _clean_str(data.get("mobile"), 40)
    message = _clean_str(data.get("message"), 4000)
    if not name or not message:
        return jsonify({"status": "error", "message": "Please enter your name and a message."}), 400
    if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"status": "error", "message": "Please enter a valid email address."}), 400
    db.session.add(ContactMessage(name=name, email=email, mobile=mobile or None, message=message))
    db.session.commit()
    return jsonify({"status": "ok", "message": "Thanks! We'll get back to you soon."})


@app.route("/favicon.ico")
def favicon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="10" fill="#1a1714"/>
<text x="14" y="43" font-family="Georgia,serif" font-size="36" font-weight="700" fill="#f4efe6">T</text>
<circle cx="48" cy="43" r="4" fill="#c8462e"/>
</svg>"""
    return Response(svg, mimetype="image/svg+xml")


# ---------------------------------------------------------------------------
# Phase 14: error handlers + safe security headers
# ---------------------------------------------------------------------------
def _wants_json():
    return request.path.startswith("/api/") or request.path.startswith("/stripe/")


@app.errorhandler(404)
def _handle_404(e):
    if _wants_json():
        return jsonify({"status": "error", "message": "Not found."}), 404
    return render_template("error.html", code="404", title="Page not found",
                           message="That page doesn't exist or may have moved."), 404


@app.errorhandler(413)
def _handle_413(e):
    return jsonify({"status": "error",
                    "message": "That file is too large. The maximum upload size is 8 MB."}), 413


@app.errorhandler(500)
def _handle_500(e):
    if _wants_json():
        return jsonify({"status": "error", "message": "Something went wrong. Please try again."}), 500
    return render_template("error.html", code="500", title="Something went wrong",
                           message="An unexpected error occurred on our end. Please try again."), 500


@app.after_request
def _security_headers(resp):
    # Safe, framing-compatible headers. CSP and X-Frame-Options are intentionally
    # left to the production reverse proxy so they can't break local previews.
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Force static JS/CSS to revalidate so a deploy's frontend changes reach users
    # immediately (ETag makes this a cheap 304 when unchanged) — no stale app.js.
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5000)
