"""Show exactly what the redactor excluded, so you can judge it yourself.

Standalone: defines its own patterns, so it runs against any version.

Usage:
    python check_redactions.py C:\\export
"""

import re
import sys

from magnum_opus.archive import load_conversations

CONTEXT = 60

PATTERNS = {
    "api_key": re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|xai-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,})"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "card_number": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}


def luhn(digits):
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def fragments(text):
    out = []
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            passes_luhn = None
            if name == "card_number":
                digits = re.sub(r"[^0-9]", "", m.group())
                passes_luhn = 13 <= len(digits) <= 16 and luhn(digits)
            a = max(0, m.start() - CONTEXT)
            b = min(len(text), m.end() + CONTEXT)
            ctx = re.sub(r"\s+", " ", text[a:b]).strip()
            out.append((name, m.group()[:60], ctx, passes_luhn))
    return out


def main(folder):
    convs = load_conversations(folder)
    total = luhn_pass = 0
    for conv in convs:
        for msg in conv.messages:
            hits = fragments(msg.text)
            if not hits:
                continue
            total += 1
            print("=" * 78)
            print(conv.title[:60])
            print(f"  {msg.role} | {msg.created_at[:10]} | msg {msg.id[:8]}")
            for reason, matched, ctx, pl in hits:
                tag = "" if pl is None else (" LUHN-PASS" if pl else " luhn-fail (v0.2.1 keeps this)")
                if pl:
                    luhn_pass += 1
                print(f"  [{reason}{tag}] matched: {matched}")
                print(f"      ...{ctx}...")
    print("=" * 78)
    print(f"{total} messages contain at least one pattern hit.")
    print(f"{luhn_pass} card_number hits actually pass the Luhn check.")
    print("\nIf these are digit runs from your own work (hashes, seeds, parameter")
    print("dumps, concatenated numbers), they are false positives and that rule")
    print("should be narrowed or disabled for your corpus.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "./export")
