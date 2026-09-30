"""Redaction: decide which messages are eligible to become evidence.

Granularity is **whole message**, deliberately. Span-level redaction would leave
partially-citable messages, forcing the verifier to reason about which fragments
survived. Whole-message keeps a clean invariant: a message either is evidence or
it is not, and the count of what was excluded is auditable.

Redacted messages are dropped before distillation, so no distilled item can ever
carry a locator pointing at one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = {
    "api_key": re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|xai-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,})"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    # Narrow by construction: either 13-16 contiguous digits, or the 4-4-4-4
    # grouped form with a single consistent separator. The looser
    # "digit-then-optional-space" form walks across whitespace and merges
    # unrelated fields -- a file size and a timestamp in an `ls -l` dump become
    # one phantom card number. Real card numbers never span separate fields.
    "card_number": re.compile(
        r"(?<![\d.\-/])(?:\d{13,16}|\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4})(?![\d.\-/])"
    ),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?1[ -.]?)?\(?\d{3}\)?[ -.]\d{3}[ -.]\d{4}\b"),
}

# HARD patterns exclude a message outright. SOFT patterns are counted and
# reported but do not exclude, because an email or phone number in a technical
# corpus is usually a code sample or a public address -- excluding on sight
# would silently delete evidence. --strict-pii promotes SOFT to HARD.
HARD = {"api_key", "aws_key", "private_key", "ssn", "card_number"}
SOFT = {"email", "phone"}


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _is_card(text: str) -> bool:
    """Only treat a digit run as a card number if it passes Luhn.

    The bare pattern fires on hashes, concatenated timestamps and parameter
    dumps, which are everywhere in an ML corpus. Luhn removes almost all of
    those without weakening real detection.
    """
    for m in PATTERNS["card_number"].finditer(text):
        digits = re.sub(r"[^0-9]", "", m.group())
        if 13 <= len(digits) <= 16 and _luhn(digits):
            return True
    return False


@dataclass
class RedactionReport:
    kept: int = 0
    dropped: int = 0
    reasons: dict = None
    flagged: dict = None

    def __post_init__(self):
        self.reasons = self.reasons or {}
        self.flagged = self.flagged or {}

    def __str__(self):
        parts = [f"redaction: {self.kept} kept"]
        if self.dropped:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items()))
            parts.append(f"{self.dropped} excluded ({detail})")
        else:
            parts.append("none excluded")
        if self.flagged:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(self.flagged.items()))
            parts.append(f"flagged but kept: {detail}")
        return " | ".join(parts)


def message_reasons(text: str, extra_terms=None, strict: bool = False):
    """Return (hard_reasons, soft_flags). Only hard reasons exclude a message."""
    active = HARD | SOFT if strict else HARD
    hard = []
    for name, rx in PATTERNS.items():
        if name not in active:
            continue
        if name == "card_number":
            if _is_card(text):
                hard.append(name)
        elif rx.search(text):
            hard.append(name)
    soft = [] if strict else [n for n in SOFT if PATTERNS[n].search(text)]
    for term in (extra_terms or []):
        if term and term.lower() in text.lower():
            hard.append("account_identifier")
            break
    return hard, soft


def redact_conversation(conv, extra_terms=None, report=None, strict: bool = False):
    """Return the conversation with ineligible messages removed."""
    report = report or RedactionReport()
    keep = []
    for m in conv.messages:
        hard, soft = message_reasons(m.text, extra_terms, strict)
        for s in soft:
            report.flagged[s] = report.flagged.get(s, 0) + 1
        if hard:
            report.dropped += 1
            for reason in hard:
                report.reasons[reason] = report.reasons.get(reason, 0) + 1
        else:
            report.kept += 1
            keep.append(m)
    conv.messages = keep
    return conv, report


def redact_all(conversations, extra_terms=None, strict: bool = False):
    report = RedactionReport()
    out = []
    for c in conversations:
        c, report = redact_conversation(c, extra_terms, report, strict)
        if c.messages:
            out.append(c)
    return out, report
