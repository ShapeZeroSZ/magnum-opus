"""Reindex: rebuild the vault index from the notes on disk.

The markdown notes are the record; `.magnum/state.json` is only an index over
them. Losing the index -- a power cut mid-write, a botched sync, a manual delete
-- must never cost the work it points at. This rebuilds state by reading the
note files back.

Notes written before v0.3.3 truncated locator ids to 8 characters for display,
so those recover with partial ids and are marked unverified rather than being
silently trusted. Notes written since carry full ids and anchors and round-trip
exactly.
"""

from __future__ import annotations

import re

FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
ITEM = re.compile(
    r"^- (?P<text>.*?)(?:  `(?P<loc>[^`]*)`)?(?:<!--anchor:(?P<anchor>.*?)-->)?\s*$"
)
LOC = re.compile(r"^(?P<conv>[^#]*)#(?P<msg>\S*)\s*(?P<role>user|assistant)?"
                 r"\s*(?P<mark>ambiguous|unverified)?\s*$")

SECTIONS = {"Decisions": "decisions", "Ideas": "ideas", "Open loops": "open_loops"}


def _parse_front(text: str) -> dict:
    m = FRONT.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"')
    return out


def _parse_items(body: str) -> dict:
    found = {v: [] for v in SECTIONS.values()}
    current = None
    for line in body.splitlines():
        if line.startswith("## "):
            current = SECTIONS.get(line[3:].strip())
            continue
        if not current or not line.startswith("- "):
            continue
        m = ITEM.match(line)
        if not m:
            continue
        loc = None
        raw = (m.group("loc") or "").strip()
        if raw and raw != "no locator":
            lm = LOC.match(raw)
            if lm:
                partial = len(lm.group("msg")) <= 8      # pre-v0.3.3 truncation
                loc = {
                    "conversation_id": lm.group("conv"),
                    "message_id": lm.group("msg"),
                    "role": lm.group("role") or "",
                    "verified": lm.group("mark") is None and not partial,
                    "ambiguous": lm.group("mark") == "ambiguous",
                }
        found[current].append({
            "text": m.group("text").strip(),
            "anchor": (m.group("anchor") or "").strip(),
            "locator": loc,
        })
    return found


def _parse_note(text: str) -> dict | None:
    front = _parse_front(text)
    if not front.get("conversation_id"):
        return None
    body = FRONT.sub("", text, count=1)
    summary = ""
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "-", "**", "<!--")):
            summary = s
            break
    conv, start, end = (front.get("conversation_id", ""),
                        front.get("start_message", ""), front.get("end_message", ""))
    record = {
        "conversation_id": conv,
        "segment_key": f"{conv}:{start}:{end}",
        "title": front.get("title", ""),
        "provider": front.get("provider", "claude"),
        "updated_at": front.get("updated", ""),
        "project": front.get("project", "unsorted"),
        "topic": front.get("topic", ""),
        "summary": summary,
        "label": front.get("segment", ""),
        "start_id": start,
        "end_id": end,
        "author": front.get("author", "distiller"),
        "links": [],
    }
    record.update(_parse_items(body))
    return record


def reindex(vault) -> dict:
    """Rebuild state["notes"] and state["segments"] from disk. Returns counts."""
    notes, partial, failed = [], 0, 0
    for path in sorted(vault.root.rglob("*.md")):
        if path.name in ("QUEUE.md", "PRIORITIES.md", "STATUS.md", "MANIFESTO.md"):
            continue
        try:
            record = _parse_note(path.read_text(encoding="utf-8"))
        except Exception:
            record = None
        if record is None:
            failed += 1
            continue
        items = (record["decisions"] + record["ideas"] + record["open_loops"])
        if any(i["locator"] and not i["locator"]["verified"] and
               len(i["locator"]["message_id"]) <= 8 for i in items):
            partial += 1
        notes.append(record)
        vault.state["segments"][record["segment_key"]] = {
            "path": str(path.relative_to(vault.root)), "project": record["project"],
        }

    seen = set()
    merged = []
    for r in reversed(notes):                     # last write wins on duplicates
        if r["segment_key"] in seen:
            continue
        seen.add(r["segment_key"])
        merged.append(r)
    vault.state["notes"] = list(reversed(merged))
    vault.save()
    return {"notes": len(vault.state["notes"]), "partial_locators": partial,
            "unreadable": failed}
