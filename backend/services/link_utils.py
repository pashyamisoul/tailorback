import re


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URLISH_RE = re.compile(r"^(www\.|[a-z0-9][a-z0-9.-]*\.[a-z]{2,})(/.*)?$", re.I)


def external_link_target(value):
    """Return a browser/PDF/Word-safe external target for contact-style links.

    Keeps display text unchanged while allowing inputs such as
    ``github.com/user`` or ``example.com`` to become clickable.
    """
    text = str(value or "").strip()
    if not text or any(c in text for c in "\r\n\t "):
        return None
    lower = text.lower()
    if lower.startswith(("http://", "https://", "mailto:")):
        return text
    if _EMAIL_RE.match(text):
        return "mailto:" + text
    if _URLISH_RE.match(text):
        return "https://" + text
    return None
