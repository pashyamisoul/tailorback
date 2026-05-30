# ATS Resume & Cover Letter Builder

Generates an ATS-friendly resume and a matching cover letter from:
1. **A job description** — pasted text, OR a public job-posting link (LinkedIn / Indeed / company career portals). If the link can't be read, the UI falls back to paste.
2. **A CV** — uploaded `.pdf` or `.docx`. If the file can't be read, the UI falls back to paste.

The tailoring engine reframes the candidate's *real* experience against the job's
requirements and never fabricates jobs, skills, or credentials. Where the CV is
missing something the job asks for, it's reported as a gap instead of invented.

## Architecture

```
Browser (templates/index.html + static/js/app.js)
        │  multipart form: cv_file | cv_text , jd_text | jd_url
        ▼
Flask (backend/app.py)
        ├── services/jd_source.py     fetch + extract JD from a URL (graceful fallback)
        ├── services/cv_parser.py     extract text from pdf/docx (graceful fallback)
        ├── services/llm.py           Anthropic calls: structure CV, analyse JD, tailor
        └── services/docx_builder.py  ATS-safe .docx for resume + cover letter
        ▼
generated/*.docx  → returned as download links
```

## Setup

```bash
cd ats-builder
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # for JS-heavy job pages (optional but recommended)
export ANTHROPIC_API_KEY=sk-ant-...  # required for tailoring
python backend/app.py                # http://localhost:5000
```

## Notes on job-link scraping

- **Company portals** (Greenhouse, Lever, Ashby, Workday) parse reliably.
- **LinkedIn / Indeed** actively block automated access and their ToS prohibits
  scraping. The app *attempts* a fetch, but expect it to fail often and fall back
  to paste. This is by design — don't try to defeat their bot protection.

## Honesty guardrail

The tailoring prompt forbids inventing experience. The response separates
`tailored_content` (truthful, reframed) from `gaps` (job requirements the CV does
not evidence). The UI surfaces gaps so the user sees the real picture.
