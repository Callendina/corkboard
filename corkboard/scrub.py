"""Write-time sensitive data scrubbing and read-time display masking.

scrub_sensitive() MUST be called on all user-supplied text BEFORE any
persistence or logging. It destructively removes PII/secrets so they are
never stored.

mask_author() is called at read time to control how author emails are
displayed depending on the viewer's role.
"""
import re


# ---------------------------------------------------------------------------
# Write-time scrubbing
# ---------------------------------------------------------------------------

def _luhn_check(digits: str) -> bool:
    """Validate a string of digits with the Luhn algorithm."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# Credit/debit card numbers: 13-19 digits with optional spaces/dashes
_CARD_RE = re.compile(r'\b(\d[\d \-]{11,22}\d)\b')

# NZ bank account: XX-XXXX-XXXXXXX-XX or XX-XXXX-XXXXXXX-XXX
_NZ_BANK_RE = re.compile(r'\b(\d{2}-\d{4}-\d{7}-\d{2,3})\b')

# Bearer tokens
_BEARER_RE = re.compile(r'Bearer\s+[A-Za-z0-9_\-\.]{20,}', re.IGNORECASE)

# Long hex strings (32+ chars, likely keys/hashes)
_HEX_RE = re.compile(r'\b[0-9a-fA-F]{32,}\b')

# Long base64 strings (40+ chars)
_BASE64_RE = re.compile(r'\b[A-Za-z0-9+/]{40,}={0,2}\b')


def scrub_sensitive(text: str) -> tuple[str, bool]:
    """Remove sensitive data from text before storage.

    Returns (cleaned_text, was_scrubbed). The original text is never
    stored anywhere — this function must be called before any persistence
    or logging.
    """
    was_scrubbed = False

    # NZ bank accounts (check before card numbers since they overlap in digit length)
    if _NZ_BANK_RE.search(text):
        text = _NZ_BANK_RE.sub("[REDACTED bank account]", text)
        was_scrubbed = True

    # Credit/debit card numbers (Luhn-validated)
    def _replace_card(match: re.Match) -> str:
        nonlocal was_scrubbed
        raw = match.group(1)
        digits = re.sub(r'[\s\-]', '', raw)
        if 13 <= len(digits) <= 19 and digits.isdigit() and _luhn_check(digits):
            was_scrubbed = True
            return "[REDACTED card number]"
        return raw

    text = _CARD_RE.sub(_replace_card, text)

    # Bearer tokens
    if _BEARER_RE.search(text):
        text = _BEARER_RE.sub("[REDACTED bearer token]", text)
        was_scrubbed = True

    # Long hex strings
    if _HEX_RE.search(text):
        text = _HEX_RE.sub("[REDACTED hex string]", text)
        was_scrubbed = True

    # Long base64 strings
    if _BASE64_RE.search(text):
        text = _BASE64_RE.sub("[REDACTED base64 string]", text)
        was_scrubbed = True

    return text, was_scrubbed


# ---------------------------------------------------------------------------
# Read-time display masking
# ---------------------------------------------------------------------------

def mask_author(email: str, viewer_role: str, post_number: int = 0) -> str:
    """Mask an author's email based on the viewer's role.

    - admin: full email
    - user: j***@e***.com
    - anon: User #N (or "Anonymous" if no post number)
    """
    if not email:
        return "Anonymous"

    if viewer_role == "admin":
        return email

    if viewer_role == "user":
        parts = email.split("@")
        if len(parts) == 2:
            local, domain = parts
            tld = domain.rsplit(".", 1)[-1] if "." in domain else domain
            return f"{local[0]}***@{domain[0]}***.{tld}"
        return email

    # anon viewer
    if post_number > 0:
        return f"User #{post_number}"
    return "Anonymous"
