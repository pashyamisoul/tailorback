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
STRIPE_API = "https://api.stripe.com/v1"
STRIPE_PACKS = [
    {
        "id": "starter",
        "name": "Starter Pack",
        "credits": 10,
        "price": "€7",
        "price_env": "STRIPE_PRICE_STARTER",
    },
    {
        "id": "hunt",
        "name": "Job Hunt Pack",
        "credits": 40,
        "price": "€19",
        "price_env": "STRIPE_PRICE_HUNT",
        "featured": True,
    },
    {
        "id": "sprint",
        "name": "Application Sprint",
        "credits": 150,
        "price": "€49",
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
    email = db.Column(db.String(255), unique=True, nullable=False)
    provider = db.Column(db.String(32), nullable=False)
    provider_id = db.Column(db.String(255), nullable=False)
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
    stripe_session_id = db.Column(db.String(255), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


with app.app_context():
    db.create_all()


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


def _public_credit_packs():
    return [
        {
            "id": pack["id"],
            "name": pack["name"],
            "credits": pack["credits"],
            "price": pack["price"],
            "featured": bool(pack.get("featured")),
            "configured": bool(os.environ.get(pack["price_env"])),
        }
        for pack in STRIPE_PACKS
    ]


def _credit_pack(pack_id):
    return next((pack for pack in STRIPE_PACKS if pack["id"] == pack_id), None)


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
        user = User(email=email, provider="google", provider_id=sub or "")
        db.session.add(user)
        db.session.commit()
    session["user_id"] = user.id
    session["email"] = user.email
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
                        "message": "Please sign in with Google to generate."}), 401
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

        def on_status(stage):
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
                **credits,
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
