# Deploying TailorBack

This is the launch guide. I've done everything that doesn't require your accounts,
secrets, or money. The rest is a short numbered checklist below — each item says
**where** to go and **what** to paste.

Recommended host: **Render** (free tier, supports Docker, gives a free Postgres
database). The setup also works on Fly.io or Railway with minor tweaks.

---

## What's already done (no action needed)

- **`Dockerfile`** — production image with LibreOffice bundled, so server-side
  PDF export works (not just DOCX). Runs the app under gunicorn.
- **`render.yaml`** — one-click "Blueprint": creates the web service **and** a
  free Postgres database, and auto-generates the Flask secret key. It lists every
  secret and prompts you for them in the dashboard (nothing secret is in the repo).
- **Postgres switch** — the app reads `DATABASE_URL` automatically and rewrites
  the `postgres://` URL that managed hosts hand out. Local dev still uses SQLite.
- **Production hardening** — secure/HTTP-only cookies, HTTPS-aware proxy handling
  (so activation & password-reset email links come out as `https://…`), DB
  keep-alive pinging. All gated on `FLASK_ENV=production`, which the blueprint sets.
- **Graceful PDF fallback** — if PDF conversion ever fails, downloads fall back to
  DOCX instead of erroring.

---

## Your part — numbered checklist

### 1. Decide your admin login
Pick an admin username and a **strong** password (not the old `tailorback-admin`).
You'll paste these as `TAILORBACK_ADMIN_USER` and `TAILORBACK_ADMIN_PASSWORD` in
step 5. Generate a password however you like (a password manager is ideal).

### 2. Collect the keys you already have
You'll paste these in step 5. You already have most of them in your local `.env`:
- `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` (at least one is required)
- `SMTP_PASSWORD` = your **Resend** API key (host `smtp.resend.com`, already set
  in the blueprint)

> I never see or handle these — you paste them straight into Render.

### 3. Google sign-in redirect (only if you want "Sign in with Google")
Google OAuth ties to an exact URL, which you won't know until after the first
deploy. So:
- For now, you can launch **without** Google (email/password works on its own) —
  just leave `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` blank.
- To enable it later: in **Google Cloud Console → APIs & Services → Credentials →
  your OAuth client**, add this to *Authorized redirect URIs*:
  `https://YOUR-APP.onrender.com/auth/google/callback`
  (replace with your real URL from step 6), then paste the client ID/secret into
  Render and redeploy.

### 4. Create the host accounts
- Sign up at **https://render.com** (free; "Sign in with GitHub" is easiest since
  the repo is on GitHub).
- Make sure Render can see the **`pashyamisoul/tailorback`** repo (authorize the
  GitHub app for that repo when prompted).

### 5. Deploy with the Blueprint
1. In Render: **New → Blueprint**.
2. Select the **`pashyamisoul/tailorback`** repo. Render reads `render.yaml` and
   shows a plan: one web service + one free Postgres DB.
3. It will prompt for every `sync: false` secret. Paste the values from steps 1–2
   (and step 3 if doing Google now). Leave the **Stripe** ones blank for a free
   launch — see step 8.
4. Click **Apply**. First build takes a while (it installs LibreOffice). When it
   finishes you get a URL like `https://tailorback.onrender.com`.

### 6. Smoke-test the live site
Open the URL and check:
- Landing page loads, theme toggle works.
- Sign up with email → you receive the activation email (Resend) → activate.
- Run one generation → résumé + cover letter + score appear → **download the PDF**
  (this proves LibreOffice works in the container).
- Visit `/admin`, log in with your step-1 credentials.

### 7. (Optional) Point your domain
You own `tailorback.com` (Cloudflare). In Render → your service → **Settings →
Custom Domains**, add `tailorback.com` (and `www`). Render shows a CNAME/DNS
record; add it in Cloudflare DNS. Then redo the Google redirect URI (step 3) and,
if used, the Stripe webhook (step 8) with the real domain.

### 8. (Optional, later) Turn on payments
You can launch free and add this anytime. In **Stripe** (test mode first):
1. Create **3 one-time products** in EUR and copy each *Price ID* (`price_…`):
   - Starter €7 → `STRIPE_PRICE_STARTER`
   - Hunt €19 → `STRIPE_PRICE_HUNT`
   - Sprint €49 → `STRIPE_PRICE_SPRINT`
2. Copy your **Secret key** (`sk_…`) → `STRIPE_SECRET_KEY`.
3. In **Developers → Webhooks**, add an endpoint:
   `https://YOUR-URL/stripe/webhook`, event `checkout.session.completed`. Copy the
   **Signing secret** (`whsec_…`) → `STRIPE_WEBHOOK_SECRET`.
4. Paste all five into Render → Environment, save (it redeploys). The "TailorBack
   Pro" buy buttons activate automatically once `STRIPE_SECRET_KEY` is present.

### 9. (Recommended) Set AI spend caps
So a runaway loop can't run up a bill:
- **OpenAI:** Settings → Billing → *Usage limits* → set a monthly hard cap.
- **Anthropic:** Console → Billing → spend limit.
- **Gemini:** Google Cloud → Billing → *Budgets & alerts*.

---

## Quick reference: environment variables

| Variable | Needed? | Notes |
|---|---|---|
| `FLASK_ENV` | auto | set to `production` by the blueprint |
| `FLASK_SECRET_KEY` | auto | generated by Render |
| `DATABASE_URL` | auto | wired from the Postgres DB |
| `TAILORBACK_ADMIN_USER` / `TAILORBACK_ADMIN_PASSWORD` | **yes** | your admin login |
| `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` | **one+** | AI providers |
| `SMTP_PASSWORD` | yes | Resend API key (host/user/from preset) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | optional | Google sign-in |
| `STRIPE_*` (5 vars) | optional | leave blank to launch free |

Optional tuning knobs (sensible defaults if unset): `FREE_GENERATION_LIMIT`,
`GENERATED_RETENTION_DAYS`, `OPENAI_MODEL`, `GEMINI_MODEL`, `ANTHROPIC_MODEL`.
