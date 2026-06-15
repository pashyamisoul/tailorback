from __future__ import annotations
"""
LLM-powered tailoring engine.

Primary provider: OpenAI Responses API using a fast GPT model. Gemini remains
the first fallback, and Claude Sonnet is the final fallback to control cost.
"""
import json
import os
import time

import httpx
from json_repair import repair_json

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
OPENAI_TIMEOUT = float(os.environ.get("OPENAI_TIMEOUT_SECONDS", "30"))
GEMINI_TIMEOUT = float(os.environ.get("GEMINI_TIMEOUT_SECONDS", "30"))
CLAUDE_TIMEOUT = float(os.environ.get("CLAUDE_TIMEOUT_SECONDS", "45"))
ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"

# Approximate list prices in USD per 1,000,000 tokens (input / output). These
# drive the admin cost estimate; override per deployment via env if needed.
PRICING = {
    "openai": {"input": float(os.environ.get("OPENAI_PRICE_IN", "0.40")),
               "output": float(os.environ.get("OPENAI_PRICE_OUT", "1.60"))},
    "gemini": {"input": float(os.environ.get("GEMINI_PRICE_IN", "0.10")),
               "output": float(os.environ.get("GEMINI_PRICE_OUT", "0.40"))},
    "claude": {"input": float(os.environ.get("CLAUDE_PRICE_IN", "3.00")),
               "output": float(os.environ.get("CLAUDE_PRICE_OUT", "15.00"))},
}


def estimate_cost(provider, prompt_tokens, completion_tokens):
    """Estimated USD cost for one call, from PRICING. Returns a float (0.0 if unknown)."""
    p = PRICING.get((provider or "").lower())
    if not p:
        return 0.0
    pt = float(prompt_tokens or 0)
    ct = float(completion_tokens or 0)
    return round((pt * p["input"] + ct * p["output"]) / 1_000_000.0, 6)


def _record_usage(sink, provider, model, prompt_tokens, completion_tokens, total_tokens=None):
    """Populate an optional usage-sink dict with token + cost info for one call."""
    if sink is None:
        return
    pt = int(prompt_tokens or 0)
    ct = int(completion_tokens or 0)
    tt = int(total_tokens) if total_tokens else (pt + ct)
    sink.clear()
    sink.update({
        "provider": provider,
        "model": model,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "est_cost_usd": estimate_cost(provider, pt, ct),
    })


def _key() -> str:
    k = os.environ.get("GEMINI_API_KEY")
    if not k:
        raise RuntimeError("GEMINI_API_KEY is not set. Get a free key at "
                           "https://aistudio.google.com/apikey")
    return k

def _openai_key() -> str:
    k = os.environ.get("OPENAI_API_KEY")
    if not k:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return k

def _claude_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return k


def _json_call(system: str, user: str, max_retries: int = 2, on_status=None, usage_sink=None) -> dict:
    def _say(stage):
        if on_status:
            on_status(stage)

    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    last_err = None

    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _openai_call(system, user, on_status=on_status, usage_sink=usage_sink)
        except Exception as exc:
            last_err = exc
            _say("switching_to_gemini" if has_gemini else "switching_to_claude")

    if has_gemini:
        try:
            return _gemini_call(system, user, max_retries=max_retries, on_status=on_status, usage_sink=usage_sink)
        except Exception as exc:
            last_err = exc
            if has_claude:
                _say("switching_to_claude")

    if has_claude:
        try:
            return _claude_call(system, user, on_status=on_status, usage_sink=usage_sink)
        except Exception as exc:
            last_err = exc

    # Every configured provider failed (or none configured) — surface the real error.
    raise last_err or RuntimeError("No LLM provider is configured.")


def _parse_json_text(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return repair_json(raw, return_objects=True)


def _extract_openai_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    chunks = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if content.get("type") in ("output_text", "text") and content.get("text"):
                chunks.append(content["text"])
    if chunks:
        return "\n".join(chunks)
    raise RuntimeError(f"Unexpected OpenAI response: {json.dumps(data)[:300]}")


def _openai_call(system: str, user: str, max_retries: int = 2, on_status=None, usage_sink=None) -> dict:
    if on_status:
        on_status("generating_openai")
    body = {
        "model": OPENAI_MODEL,
        "instructions": system + "\n\nRespond with ONLY valid JSON.",
        "input": user,
        "temperature": 0,
        "max_output_tokens": 16000,   # one call does resume+cover+gaps+analysis; avoid truncation
        "text": {"format": {"type": "json_object"}},
    }
    headers = {
        "authorization": f"Bearer {_openai_key()}",
        "content-type": "application/json",
    }
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                OPENAI_RESPONSES_ENDPOINT,
                headers=headers,
                json=body,
                timeout=OPENAI_TIMEOUT,
            )
        except httpx.RequestError:
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code in (408, 409, 429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        resp.raise_for_status()
        j = resp.json()
        u = j.get("usage") or {}
        _record_usage(usage_sink, "openai", OPENAI_MODEL,
                      u.get("input_tokens"), u.get("output_tokens"), u.get("total_tokens"))
        return _parse_json_text(_extract_openai_text(j))
    raise RuntimeError("OpenAI is unavailable right now.")


def _gemini_call(system: str, user: str, max_retries: int = 2, on_status=None, usage_sink=None) -> dict:
    def _say(stage):
        if on_status:
            on_status(stage)
    _say("trying_gemini")

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                ENDPOINT, params={"key": _key()}, json=body, timeout=GEMINI_TIMEOUT)
        except httpx.RequestError:
            time.sleep(2 * (attempt + 1))
            continue

        if resp.status_code in (429, 500, 502, 503):
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code != 200:
            break  # any other Gemini error -> stop retrying, fall back to Claude
        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError):
            break  # malformed Gemini response -> fall back to Claude∏
        um = data.get("usageMetadata") or {}
        _record_usage(usage_sink, "gemini", GEMINI_MODEL,
                      um.get("promptTokenCount"), um.get("candidatesTokenCount"), um.get("totalTokenCount"))
        return _parse_json_text(text)
    raise RuntimeError("Gemini is unavailable right now.")


def _claude_call(system: str, user: str, max_retries: int = 3, on_status=None, usage_sink=None) -> dict:
    if on_status:
        on_status("generating_claude")
    """Fallback engine: Claude Opus 4.8. Same JSON-in/JSON-out contract."""
    body = {
        "model": CLAUDE_MODEL,
        "max_tokens": 16000,
        "system": system + "\n\nRespond with ONLY valid JSON, no markdown fences.",
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": _claude_key(),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    for attempt in range(max_retries):
        try:
            resp = httpx.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=body, timeout=CLAUDE_TIMEOUT)
        except httpx.RequestError:
            time.sleep(3 * (attempt + 1))
            continue
        if resp.status_code in (429, 500, 502, 503, 529):
            time.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        data = resp.json()
        try:
            text = data["content"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected Claude response: {json.dumps(data)[:300]}")
        u = data.get("usage") or {}
        _record_usage(usage_sink, "claude", CLAUDE_MODEL,
                      u.get("input_tokens"), u.get("output_tokens"))
        return _parse_json_text(text)
    raise RuntimeError(
        "Both Gemini and Claude are unavailable right now. Please try again shortly.")


def analyse_jd(jd_text: str) -> dict:
    system = (
        "You analyse job descriptions. Respond with ONLY a JSON object, no prose, "
        "no markdown fences. Schema: {\"title\": str, \"company\": str|null, "
        "\"hard_skills\": [str], \"soft_skills\": [str], \"keywords\": [str], "
        "\"responsibilities\": [str], \"must_have\": [str], \"nice_to_have\": [str]}. "
        "keywords are exact ATS terms a parser would match (tools, technologies, "
        "methodologies, certifications). Be specific and verbatim where the JD is."
    )
    return _json_call(system, f"Job description:\n\n{jd_text}")


def structure_cv(cv_text: str) -> dict:
    system = (
        "You convert raw CV text into structured JSON. Respond with ONLY JSON, no "
        "prose. Schema: {\"name\": str, \"email\": str|null, \"phone\": str|null, "
        "\"location\": str|null, \"links\": [str], \"summary\": str|null, "
        "\"experience\": [{\"title\": str, \"company\": str, \"dates\": str, "
        "\"bullets\": [str]}], \"education\": [{\"degree\": str, \"institution\": str, "
        "\"dates\": str}], \"skills\": [str], \"certifications\": [str]}. "
        "Extract only what is present. Never invent. Use null/empty for missing fields."
    )
    return _json_call(system, f"CV text:\n\n{cv_text}")


def tailor(profile: dict, requirements: dict) -> dict:
    system = (
        "You are an expert resume writer optimising for ATS keyword matching AND "
        "human readability.\n\n"
        "ABSOLUTE RULE - TRUTHFULNESS: Use ONLY facts present in the candidate "
        "profile. Never invent employers, job titles, dates, degrees, "
        "certifications, metrics, or skills the candidate does not have. You may "
        "rephrase, reorder, emphasise, and surface relevant real experience, and "
        "incorporate job keywords ONLY where the candidate genuinely has that "
        "experience. If the job wants something the candidate lacks, DO NOT add it "
        "to the resume - list it under 'gaps'.\n\n"
        "Optimise by: reordering bullets to lead with job-relevant achievements; "
        "mirroring the job's exact terminology for skills the candidate truly has; "
        "using standard ATS section names; keeping formatting plain. Quantify "
        "achievements only with numbers already in the profile.\n\n"
        "Respond with ONLY JSON, no prose, no markdown fences. Schema: {\"resume\": "
        "{\"name\": str, \"contact\": {\"email\": str|null, \"phone\": str|null, "
        "\"location\": str|null, \"links\": [str]}, \"summary\": str, \"skills\": "
        "[str], \"experience\": [{\"title\": str, \"company\": str, \"dates\": str, "
        "\"bullets\": [str]}], \"education\": [{\"degree\": str, \"institution\": "
        "str, \"dates\": str}], \"certifications\": [str]}, \"cover_letter\": "
        "{\"greeting\": str, \"body_paragraphs\": [str], \"closing\": str}, "
        "\"gaps\": [str], \"match_summary\": {\"covered\": [str], \"missing\": [str]}}"
    )
    user = (
        "CANDIDATE PROFILE (the only facts you may use):\n"
        f"{json.dumps(profile, indent=2)}\n\n"
        "JOB REQUIREMENTS to tailor toward:\n"
        f"{json.dumps(requirements, indent=2)}\n\n"
        "Produce the tailored resume, a matching cover letter, and an honest list "
        "of gaps (job requirements the candidate does not evidence)."
    )
    return _json_call(system, user)

def analyse_resume(cv_text, requirements):
    """Score the user's UPLOADED resume against the job. Honest critique."""
    system = (
        "You are a senior technical recruiter and ATS expert. You evaluate the "
        "candidate's CURRENT resume against a specific job and give an honest, "
        "specific, useful critique. Be candid: name real weaknesses, do not "
        "inflate the score. Respond with ONLY JSON, no prose, no markdown fences. "
        "Schema: {"
        "\"overall_score\": int (0-100, realistic ATS+recruiter match score), "
        "\"verdict\": str (one candid sentence on where this resume stands), "
        "\"dimensions\": ["
        "  {\"name\": \"Job Match\", \"score\": int, \"note\": str}, "
        "  {\"name\": \"Keyword Coverage\", \"score\": int, \"note\": str}, "
        "  {\"name\": \"Structure & Format\", \"score\": int, \"note\": str}, "
        "  {\"name\": \"Impact & Quantification\", \"score\": int, \"note\": str}"
        "], "
        "\"strengths\": [str], "
        "\"improvements\": [str], "
        "\"missing_keywords\": [str]}"
    )
    user = (
        "JOB REQUIREMENTS:\n" + json.dumps(requirements, indent=2) +
        "\n\nCANDIDATE'S CURRENT RESUME (raw text):\n" + cv_text +
        "\n\nEvaluate the resume against this job. Be specific and honest."
    )
    return _json_call(system, user)

def generate_all(cv_text, jd_text, on_status=None, usage_sink=None):
    """ONE LLM call that does everything: tailored resume + cover letter +
    gaps + match + critique of the uploaded resume. Replaces 4 separate calls."""
    system = (
        "You are an expert technical recruiter, ATS specialist, and resume writer. "
        "Given a JOB DESCRIPTION and a candidate's CURRENT RESUME, you will:\n"
        "1. Tailor the candidate's REAL experience into an ATS-optimised resume.\n"
        "2. Write a matching cover letter.\n"
        "3. List honest gaps (job requirements the CV does not evidence).\n"
        "4. Critique the candidate's CURRENT (uploaded) resume honestly.\n\n"
        "NAME ACCURACY: Output the candidate's real name cleanly. If the CV text shows it "
        "with letter-spacing or spurious internal spaces from PDF extraction (e.g. "
        "'R A J O L K A R', 'Ra Jolkar', or 'AMITH A RA JOLKAR'), reconstruct the correct "
        "spelling, cross-checking the email address and LinkedIn URL for the true surname. "
        "Use normal capitalisation (e.g. 'Amith A. Rajolkar', not all-caps). Never invent "
        "or change the actual name.\n\n"
        "ABSOLUTE RULE - TRUTHFULNESS: In the tailored resume use ONLY facts present "
        "in the candidate's resume. Never invent employers, titles, dates, degrees, "
        "certifications, metrics, or skills. Rephrase and reorder to surface relevant "
        "real experience and mirror the job's exact terminology where the candidate "
        "genuinely has it. Requirements the candidate lacks go in 'gaps', never the resume.\n\n"
        "KEYWORD COVERAGE (CRITICAL): The tailored resume must cover the job's relevant "
        "keywords AT LEAST as well as the candidate's current resume - never fewer. "
        "Retain EVERY job-relevant skill, tool, technology, and term the candidate already "
        "lists; do not drop one or swap it for a vaguer synonym. Use the job's exact "
        "wording where the candidate truthfully has it, and weave those exact terms "
        "naturally into the summary and experience bullets too.\n\n"
        "SKILLS GROUPING: Output 'skills' as 3-6 labelled groups, each {\"category\": "
        "a short category label (e.g. 'Languages', 'Cloud & DevOps', 'Identity & Access', "
        "'Tools'), \"items\": [the specific skills in that group]}. Group by theme so a "
        "recruiter can scan capabilities fast. Include every job-relevant skill the "
        "candidate truly has; do not invent categories with no real skills.\n"
        "EXPERIENCE LOCATION: For each role set 'location' to its city/country if the "
        "candidate's resume states it, else null. Never invent a location.\n"
        "HEADLINE: 'headline' is a short professional title line for the resume header "
        "(e.g. 'IT Support & Endpoint Engineer | Identity & Access'), drawn from the "
        "candidate's real role and the target job. Keep it truthful and concise.\n"
        "LANGUAGES: Populate 'languages' only with languages the candidate's resume "
        "states (name + level such as 'Native', 'C1', 'Fluent'); else use an empty list. "
        "Never invent languages or levels.\n\n"
        "Write naturally and specifically (concrete tools, real numbers) so the prose "
        "reads as genuine human writing, not generic filler.\n\n"
        "PUNCTUATION: Never use em dashes (the long dash). Use commas, periods, "
        "colons, or short hyphenated words instead. Em dashes read as AI-generated; avoid them entirely.\n\n"
        "If the candidate's resume includes a projects/portfolio/side-projects section, "
        "populate 'projects' with their REAL projects (name plus achievement bullets, and "
        "a link or dates only if present). Do not invent projects, and do not duplicate "
        "paid roles already under 'experience'. If there are no projects, use an empty list.\n\n"
        "In 'job', extract the hiring company's name and the role title FROM THE JOB "
        "DESCRIPTION. If not clearly stated, set the field to null. Never guess or invent.\n\n"
        "Respond with ONLY JSON, no prose, no markdown fences. Schema:\n"
        "{\"resume\": {\"name\": str, \"headline\": str, \"contact\": {\"email\": str|null, "
        "\"phone\": str|null, \"location\": str|null, \"links\": [str]}, \"summary\": str, "
        "\"skills\": [{\"category\": str, \"items\": [str]}], "
        "\"languages\": [{\"name\": str, \"level\": str}], "
        "\"experience\": [{\"title\": str, \"company\": str, \"location\": str|null, "
        "\"dates\": str, \"bullets\": [str]}], \"projects\": [{\"name\": str, "
        "\"link\": str|null, \"dates\": str|null, \"bullets\": [str]}], "
        "\"education\": [{\"degree\": str, "
        "\"institution\": str, \"dates\": str}], \"certifications\": [str]}, "
        "\"cover_letter\": {\"greeting\": str, \"body_paragraphs\": [str], \"closing\": str}, "
        "\"gaps\": [str], "
        "\"match_summary\": {\"covered\": [str], \"missing\": [str]}, "
        "\"job\": {\"company\": str|null, \"role\": str|null}, "
        "\"analysis\": {\"overall_score\": int, \"verdict\": str, \"dimensions\": "
        "[{\"name\": \"Job Match\", \"score\": int, \"note\": str}, "
        "{\"name\": \"Keyword Coverage\", \"score\": int, \"note\": str}, "
        "{\"name\": \"Structure & Format\", \"score\": int, \"note\": str}, "
        "{\"name\": \"Impact & Quantification\", \"score\": int, \"note\": str}], "
        "\"strengths\": [str], \"improvements\": [str], \"missing_keywords\": [str]}}\n\n"
        "The 'analysis' must evaluate the CURRENT uploaded resume (not the tailored "
        "one), candidly, against the job. SCORE DETERMINISTICALLY using this exact "
        "rubric so the same resume and job always produce the same score:\n"
        "- Job Match (0-100): percentage of the job's must-have responsibilities the "
        "resume clearly evidences. Count them: (evidenced / total) * 100, rounded.\n"
        "- Keyword Coverage (0-100): of the job's keywords/hard-skills, the percentage "
        "that appear in the resume. Count them: (present / total) * 100, rounded.\n"
        "- Structure & Format (0-100): start at 100, subtract 10 for each missing "
        "standard section (summary, skills, experience, education), subtract 10 if no "
        "quantified achievements exist, subtract 10 if contact info is incomplete.\n"
        "- Impact & Quantification (0-100): percentage of experience bullets that "
        "contain a concrete metric or number, times 100, rounded.\n"
        "overall_score = round(0.40*JobMatch + 0.30*KeywordCoverage + "
        "0.15*Structure + 0.15*Impact). Compute each number from the rubric above; "
        "do not estimate or vary it. Identical inputs MUST yield an identical score."
    )
    
    user = (
        "JOB DESCRIPTION:\n\"\"\"\n" + jd_text + "\n\"\"\"\n\n"
        "CANDIDATE'S CURRENT RESUME:\n\"\"\"\n" + cv_text + "\n\"\"\"\n\n"
        "Produce the full JSON now."
    )
    return _json_call(system, user, on_status=on_status, usage_sink=usage_sink)


# ---------------------------------------------------------------------------
# Per-section refinement (powers the in-app editor's "regenerate" controls)
# ---------------------------------------------------------------------------

_TONE_GUIDANCE = {
    "formal": "Use a polished, professional, slightly formal register.",
    "confident": "Use a confident, assertive, achievement-forward voice.",
    "concise": "Be tight and economical; cut filler, keep only high-signal wording.",
    "friendly": "Use a warm, approachable, human tone while staying professional.",
}

_REFINE_SCHEMAS = {
    "summary": "{\"summary\": str}",
    "skills": "{\"skills\": [{\"category\": str, \"items\": [str]}]}",
    "bullets": "{\"bullets\": [str]}",
    "cover_letter": "{\"greeting\": str, \"body_paragraphs\": [str], \"closing\": str}",
}


def refine_section(kind, content, instruction="", tone="", length="", context=None):
    """Rewrite ONE resume/cover-letter section in place.

    kind: 'summary' | 'skills' | 'bullets' | 'cover_letter'
    content: the current value for that section (str / list / dict)
    instruction: free-text user ask (e.g. "make it punchier")
    tone: one of _TONE_GUIDANCE keys (optional)
    length: 'shorter' | 'longer' | '' (optional)
    context: optional dict with 'job' / 'role' / 'company' for grounding

    Returns a dict matching _REFINE_SCHEMAS[kind]. Never invents facts.
    """
    if kind not in _REFINE_SCHEMAS:
        raise ValueError(f"Unknown refine kind: {kind}")

    asks = []
    if instruction:
        asks.append(instruction.strip())
    if tone and tone in _TONE_GUIDANCE:
        asks.append(_TONE_GUIDANCE[tone])
    if length == "shorter":
        asks.append("Make it noticeably shorter without losing key substance.")
    elif length == "longer":
        asks.append("Expand with more relevant detail, staying truthful.")
    ask_text = " ".join(asks) or "Improve clarity and impact."

    system = (
        "You are an expert resume writer and editor. You rewrite a SINGLE section "
        "of a candidate's resume or cover letter.\n\n"
        "ABSOLUTE RULE - TRUTHFULNESS: Use ONLY facts already present in the provided "
        "content. Never invent employers, titles, dates, metrics, tools, or skills the "
        "candidate has not stated. You may rephrase, reorder, tighten, and re-emphasise. "
        "Do not add numbers or technologies that are not already there.\n\n"
        "PUNCTUATION: Never use em dashes (the long dash); use commas, periods, "
        "colons, or hyphens instead. Em dashes read as AI-generated.\n\n"
        "Respond with ONLY a JSON object, no prose, no markdown fences. Schema: "
        + _REFINE_SCHEMAS[kind] + "\n"
        "Return the same shape you were given (a skills list stays a list of strings; "
        "bullets stay a list of strings; the summary stays a single string)."
    )
    parts = [f"SECTION TYPE: {kind}", f"EDITING INSTRUCTION: {ask_text}"]
    if context:
        parts.append("JOB CONTEXT (for relevance only, not new facts):\n"
                      + json.dumps(context, indent=2))
    parts.append("CURRENT CONTENT:\n" + json.dumps(content, indent=2, ensure_ascii=False))
    parts.append("Rewrite it now and return ONLY the JSON object.")
    return _json_call(system, "\n\n".join(parts))


# ---------------------------------------------------------------------------
# Phase 9: writing-quality check (grammar/clarity, never invents facts)
# ---------------------------------------------------------------------------
def writing_check(resume_text):
    """Return writing-quality issues for a resume. Suggestions only; no new facts."""
    system = (
        "You are a meticulous resume editor. Review the RESUME TEXT for writing-quality "
        "issues ONLY: grammar, spelling, punctuation, verb-tense consistency, passive or "
        "weak phrasing, wordiness, and vague wording. Do NOT invent facts and do NOT "
        "suggest adding skills, metrics, or experience the candidate has not stated. "
        "Do NOT use em dashes (use commas, periods, colons, or hyphens).\n\n"
        "Respond with ONLY JSON, no markdown fences: "
        "{\"issues\": [{\"excerpt\": str, \"problem\": str, \"suggestion\": str, "
        "\"severity\": \"high\"|\"medium\"|\"low\"}]}. "
        "If the writing is already clean, return an empty list. At most 12 issues, "
        "most important first. 'excerpt' must be a short quote from the text."
    )
    user = "RESUME TEXT:\n\"\"\"\n" + (resume_text or "") + "\n\"\"\"\n\nReturn the JSON now."
    return _json_call(system, user)


# ---------------------------------------------------------------------------
# Phase 11: interview preparation (questions grounded in the JD + resume)
# ---------------------------------------------------------------------------
def interview_questions(jd_text, resume_text, company=None, role=None):
    """Likely interview questions tailored to the role and the candidate."""
    system = (
        "You are an experienced hiring manager preparing a candidate for an interview. "
        "Using the JOB DESCRIPTION and the candidate's RESUME, produce likely interview "
        "questions tailored to this specific role and candidate. Ground every question in "
        "the actual job requirements and the candidate's real experience or gaps. Do NOT "
        "invent facts about the candidate. Do NOT use em dashes.\n\n"
        "Respond with ONLY JSON, no markdown fences: {\"questions\": [{\"question\": str, "
        "\"category\": \"technical\"|\"behavioral\"|\"role-specific\"|\"gap\", "
        "\"why\": str, \"tip\": str}]}. Provide 6 to 10 questions spread across categories. "
        "'why' = why an interviewer would ask it; 'tip' = how to answer it well."
    )
    parts = []
    label = " at ".join([x for x in [role, company] if x])
    if label:
        parts.append("ROLE: " + label)
    parts.append("JOB DESCRIPTION:\n\"\"\"\n" + (jd_text or "") + "\n\"\"\"")
    parts.append("CANDIDATE RESUME:\n\"\"\"\n" + (resume_text or "") + "\n\"\"\"")
    parts.append("Return the JSON now.")
    return _json_call(system, "\n\n".join(parts))
