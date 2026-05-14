# llm/sanitize.py — Strip prompt-injection vectors from third-party text fields.

import re
import unicodedata

# Tags / role markers used by major LLM chat formats — strip them so user data
# cannot impersonate a system message or terminate the current role context.
_ROLE_MARKERS = re.compile(
    r"</?(?:system|user|assistant|im_start|im_end|tool_call|function_call|sys)>"
    r"|<<\s*(?:SYS|USER|ASSISTANT)\s*>>"
    r"|<\|(?:system|user|assistant|im_start|im_end|endoftext)\|>",
    re.IGNORECASE,
)

# Markdown code fences — collapse to plain text so they can't break out of JSON.
_CODE_FENCES = re.compile(r"```+|~~~+")

# Common prompt-override phrases. Conservative list — false positives are
# tolerated since the field is supposed to contain DTC descriptions, not English.
_OVERRIDE_PHRASES = re.compile(
    r"ignore (?:all |the |previous |above |prior )?instructions?"
    r"|disregard (?:all |the |previous )?(?:instructions?|context)"
    r"|forget (?:everything|all|the previous)"
    r"|you are now"
    r"|new instructions?:"
    r"|system prompt:",
    re.IGNORECASE,
)


def sanitize_user_text(text: object, max_len: int = 500) -> str:
    """Strip injection vectors and bound length on third-party text fields.

    Applied to anything that comes from outside our codebase before it is
    interpolated into an LLM prompt: source MongoDB DTC descriptions, ECU
    names, KB entry text on the rare paths where it might be operator-supplied.

    Args:
        text:    Raw value. Coerced to string. None -> "".
        max_len: Hard truncation cap (default 500 chars per field).

    Returns:
        Sanitized string with control chars, role tags, code fences, and
        prompt-override phrases removed; whitespace collapsed; truncated.
    """
    if text is None:
        return ""
    s = str(text)

    # Normalize unicode so visually-identical attack chars (e.g. fullwidth <)
    # collapse to ASCII before the regex below sees them.
    s = unicodedata.normalize("NFKC", s)

    # Drop control chars except common whitespace.
    s = "".join(ch for ch in s if ch == " " or ch == "\t" or ch == "\n" or unicodedata.category(ch)[0] != "C")

    s = _ROLE_MARKERS.sub(" ", s)
    s = _CODE_FENCES.sub(" ", s)
    s = _OVERRIDE_PHRASES.sub("[redacted]", s)

    # Collapse all whitespace runs to single spaces.
    s = re.sub(r"\s+", " ", s).strip()

    if len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    return s


def sanitize_fault(fault: dict, max_field_len: int = 500) -> dict:
    """Return a copy of a parsed fault dict with all string fields sanitized."""
    return {
        **fault,
        "code": sanitize_user_text(fault.get("code", ""), max_len=80),
        "ecu": sanitize_user_text(fault.get("ecu", ""), max_len=120),
        "description": sanitize_user_text(fault.get("description", ""), max_len=max_field_len),
    }


def sanitize_kb_entry(kb_entry: dict | None, max_field_len: int = 500) -> dict:
    """Sanitize KB entry text fields before they go into a prompt."""
    if not kb_entry or not isinstance(kb_entry, dict):
        return {}
    out = dict(kb_entry)
    for key in ("meaning", "system", "component", "explanation"):
        if key in out:
            out[key] = sanitize_user_text(out[key], max_len=max_field_len)
    if isinstance(out.get("causes"), list):
        out["causes"] = [sanitize_user_text(c, max_len=200) for c in out["causes"][:10]]
    return out
