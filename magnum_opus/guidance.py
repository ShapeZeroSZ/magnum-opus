"""GUIDANCE.md: your standing instructions, read by every step.

A correction you make should stick. GUIDANCE.md is a plain file in the vault
that you own: write in it directly in Obsidian, or confirm additions from
`magnum chat`. It is never generated and never rewritten; tools only ever
append to it, and only what you confirmed.

Every step that uses a model reads it first: sort, thesis, the chat, and any
assistant connected with `magnum serve`. `magnum brief` includes it, so an
assistant elsewhere knows how you want the work done.

Examples of what belongs here:
    - Grey and Shape Zero are one project.
    - Focus on finishing the paper before anything new.
    - The garden notes are personal; leave them out of the thesis.
"""

from __future__ import annotations

from datetime import datetime, timezone

FILE = "GUIDANCE.md"
MAX_CHARS = 6000
HEADER = ("# GUIDANCE — how I want my work handled\n\n"
          "_Mine. Every magnum step and connected assistant reads this first. "
          "Write anything here; tools only add what I confirm._\n")
REMEMBERED = "## Remembered from chat"


def read(vault) -> str:
    """The guidance text, bounded, or "" if there is none."""
    p = vault.root / FILE
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8").lstrip("﻿").strip()
    if not text or text == HEADER.strip():
        return ""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip() + "\n[... GUIDANCE.md continues; shortened here]"
    return text


def section(vault, heading="THE PERSON'S STANDING GUIDANCE (follow it)") -> str:
    """A block to put in a prompt, or "" when there is no guidance."""
    text = read(vault)
    return f"\n\n{heading}:\n{text}\n" if text else ""


def append(vault, line: str) -> str:
    """Add one confirmed line under 'Remembered from chat'. Never rewrites
    anything already there, including the person's line endings."""
    line = " ".join(str(line).split()).strip()
    if not line:
        raise ValueError("Nothing to remember.")
    p = vault.root / FILE
    raw = p.read_bytes().decode("utf-8") if p.is_file() else HEADER
    nl = "\r\n" if "\r\n" in raw else "\n"
    day = datetime.now(timezone.utc).date().isoformat()
    entry = f"- {line} _({day})_"
    if REMEMBERED not in raw:
        raw = raw.rstrip("\r\n") + nl + nl + REMEMBERED + nl
    elif not raw.endswith(("\n", "\r")):
        raw += nl
    vault._atomic_write(p, raw + entry + nl)
    return entry
