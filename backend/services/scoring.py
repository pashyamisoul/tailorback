from __future__ import annotations

import re

STOPWORDS = {
    "about", "above", "after", "again", "against", "also", "and", "any", "are",
    "as", "at", "be", "been", "but", "by", "can", "company", "for", "from",
    "have", "how", "in", "into", "is", "it", "its", "job", "more", "not",
    "of", "on", "or", "our", "role", "that", "the", "their", "this", "to",
    "we", "with", "will", "you", "your",
}

# Generic English / resume-filler words that are NOT job-specific keywords.
# Used to keep the "missing keywords" list meaningful (drop words like
# "around", "attending", "attentive", "accountable" that aren't real skills).
COMMON_WORDS = {
    "ability", "able", "about", "above", "accessible", "accordingly", "account",
    "accountable", "accurate", "accurately", "achieve", "achieved", "achievement",
    "achievements", "across", "actively", "activities", "activity", "adaptable",
    "additional", "additionally", "adept", "affiliated", "after", "again", "against",
    "all", "along", "already", "also", "although", "always", "among", "amount",
    "analytical", "another", "any", "anyone", "applicant", "applicants", "application",
    "applications", "apply", "appropriate", "approximately", "are", "area", "areas",
    "around", "aspects", "aspirations", "assets", "assist", "assistance", "attendance",
    "attending", "attentive", "available", "based", "basic", "because", "become",
    "been", "before", "being", "below", "benefit", "benefits", "best", "better",
    "between", "beyond", "both", "build", "building", "business", "but", "can",
    "candidate", "candidates", "capable", "career", "change", "clear", "closely",
    "collaborate", "collaborative", "come", "comfortable", "committed", "common",
    "company", "competitive", "complete", "comprehensive", "confident", "consider",
    "consistent", "consistently", "contribute", "could", "create", "culture",
    "current", "currently", "daily", "day", "days", "deliver", "demonstrate",
    "demonstrated", "department", "described", "description", "desirable", "detail",
    "detailed", "details", "develop", "different", "diverse", "down", "drive",
    "driven", "during", "duties", "each", "eager", "effective", "effectively",
    "efficient", "efficiently", "either", "employee", "employees", "employer",
    "employment", "enable", "ensure", "ensuring", "environment", "equal", "essential",
    "etc", "even", "every", "everyone", "everything", "excellent", "experience",
    "experienced", "expected", "extensive", "fast", "first", "flexible", "focus",
    "focused", "following", "for", "friendly", "from", "full", "fully", "further",
    "general", "genuine", "get", "give", "given", "goals", "good", "great", "group",
    "grow", "growing", "growth", "had", "hands", "has", "have", "having", "help",
    "here", "high", "highly", "his", "hold", "hours", "how", "however", "ideal",
    "ideally", "important", "include", "includes", "including", "individual",
    "information", "innovative", "into", "join", "keen", "key", "knowledge", "large",
    "least", "level", "like", "look", "looking", "made", "maintain", "make", "making",
    "many", "may", "meet", "meeting", "member", "members", "minimum", "month",
    "months", "more", "most", "motivated", "much", "multiple", "must", "name",
    "necessary", "need", "needs", "new", "next", "not", "now", "offer", "offers",
    "office", "one", "ongoing", "only", "opportunities", "opportunity", "order",
    "organisation", "organised", "organization", "organized", "other", "others",
    "our", "out", "over", "overview", "own", "part", "particular", "passion",
    "passionate", "people", "per", "performance", "person", "personal", "place",
    "plus", "position", "positive", "possible", "potential", "preferred", "previous",
    "proactive", "process", "professional", "proficient", "provide", "providing",
    "purpose", "qualifications", "qualified", "quality", "range", "rapidly", "rate",
    "really", "reliable", "remote", "required", "requirement", "requirements",
    "requires", "responsibilities", "responsibility", "responsible", "right", "role",
    "roles", "salary", "same", "seeking", "self", "set", "several", "shift", "should",
    "similar", "since", "skill", "skilled", "skills", "small", "some", "someone",
    "specific", "staff", "standard", "start", "strong", "successful", "such",
    "suitable", "support", "sure", "take", "task", "tasks", "team", "teams", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "thing",
    "things", "this", "those", "through", "throughout", "time", "together", "tools",
    "top", "toward", "towards", "training", "trustworthy", "type", "under",
    "understand", "understanding", "until", "upon", "use", "used", "using", "valid",
    "various", "very", "want", "way", "ways", "week", "weeks", "well", "what", "when",
    "where", "whether", "which", "while", "who", "whole", "will", "willing", "with",
    "within", "without", "work", "working", "world", "would", "year", "years", "you",
    "your",
}


def _tokens(text):
    out = []
    for raw in re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}", text.lower()):
        t = raw.strip(".-")  # drop trailing punctuation: "engineer." -> "engineer"
        if len(t) >= 3 and t not in STOPWORDS:
            out.append(t)
    return out


def _sentences(text):
    parts = re.split(r"(?:\n+|(?<=[.!?])\s+|[•\u2022])", text)
    return [p.strip() for p in parts if len(p.strip()) >= 35]


def _keyword_set(text):
    toks = _tokens(text)
    # Keep distinctive terms: long-enough words or acronyms, minus generic
    # English / resume-filler words so "missing keywords" stays meaningful.
    keep = {
        t for t in toks
        if (len(t) >= 4 or t.isupper())
        and t not in COMMON_WORDS
        and not (t.isalpha() and len(t) < 4)
    }
    # Preserve obvious multi-word technical phrases from slash/comma-heavy JDs.
    phrases = re.findall(r"\b[A-Z][A-Za-z0-9+#.-]*(?:\s+[A-Z][A-Za-z0-9+#.-]*){1,2}\b", text)
    keep.update(p.lower() for p in phrases if len(p) <= 40)
    return keep


def _metric_bullets(text):
    lines = [p.strip() for p in re.split(r"\n+|[•\u2022]", text) if len(p.strip()) >= 25]
    if not lines:
        lines = _sentences(text)
    with_metric = [p for p in lines if re.search(r"\d|%|\$|€|£", p)]
    return len(with_metric), max(1, len(lines))


def score_resume(cv_text, jd_text, model_analysis=None):
    """Deterministic ATS/recruiter score for repeatable UI testing."""
    model_analysis = model_analysis or {}
    cv_low = cv_text.lower()
    cv_tokens = set(_tokens(cv_text))

    jd_sentences = _sentences(jd_text)
    if not jd_sentences:
        jd_sentences = [jd_text]
    evidenced = 0
    for sent in jd_sentences:
        sent_terms = {t for t in _tokens(sent) if len(t) >= 4}
        if not sent_terms:
            continue
        overlap = sent_terms & cv_tokens
        if len(overlap) / max(1, len(sent_terms)) >= 0.18:
            evidenced += 1
    job_match = round((evidenced / max(1, len(jd_sentences))) * 100)

    jd_keywords = sorted(_keyword_set(jd_text))
    present_keywords = [k for k in jd_keywords if k in cv_low or k in cv_tokens]
    keyword_coverage = round((len(present_keywords) / max(1, len(jd_keywords))) * 100)

    section_checks = {
        "summary": bool(re.search(r"\b(summary|profile|objective)\b", cv_low)),
        "skills": bool(re.search(r"\b(skills|technologies|tools|competencies)\b", cv_low)),
        "experience": bool(re.search(r"\b(experience|employment|work history|projects)\b", cv_low)),
        "education": bool(re.search(r"\b(education|degree|university|college|school)\b", cv_low)),
    }
    structure = 100 - (10 * list(section_checks.values()).count(False))
    if not re.search(r"\d|%|\$|€|£", cv_text):
        structure -= 10
    if not re.search(r"[\w.+-]+@[\w.-]+\.\w+", cv_text):
        structure -= 10
    structure = max(0, structure)

    metric_count, bullet_count = _metric_bullets(cv_text)
    impact = round((metric_count / bullet_count) * 100)
    overall = round(0.40 * job_match + 0.30 * keyword_coverage +
                    0.15 * structure + 0.15 * impact)

    missing = [k for k in jd_keywords if k not in present_keywords][:14]
    dimensions = [
        {"name": "Job Match", "score": job_match,
         "note": f"{evidenced} of {len(jd_sentences)} job requirement statements show clear evidence in the current resume."},
        {"name": "Keyword Coverage", "score": keyword_coverage,
         "note": f"{len(present_keywords)} of {len(jd_keywords)} extracted job keywords appear in the current resume."},
        {"name": "Structure & Format", "score": structure,
         "note": "Score is based on standard sections, contact info, and whether quantified achievements are present."},
        {"name": "Impact & Quantification", "score": impact,
         "note": f"{metric_count} of {bullet_count} resume lines include a concrete number or metric."},
    ]

    # Prefer the model's missing keywords (it picks real hard skills, not generic
    # words); fall back to the deterministic list only when the model didn't supply one.
    model_missing = [k for k in (model_analysis.get("missing_keywords") or []) if k]
    return {
        **model_analysis,
        "overall_score": overall,
        "dimensions": dimensions,
        "missing_keywords": model_missing or missing,
        "verdict": model_analysis.get("verdict") or
                   "This score is calculated deterministically from keyword coverage, requirement evidence, structure, and quantified impact.",
    }
