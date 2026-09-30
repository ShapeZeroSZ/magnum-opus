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

import json
import re

FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
ITEM = re.compile(
    r"^- (?P<text>.*?)(?:  `(?P<loc>[^`]*)`)?(?:<!--anchor:(?P<anchor>.*?)-->)?\s*$"
)
TASK = re.compile(r"^\[(?P<mark>[ xX])\]\s+")
LINKS = re.compile(r"\[\[([^\]|#]+)")
GENERATED = {"QUEUE.md", "PRIORITIES.md", "STATUS.md", "MANIFESTO.md",
             "CONVERGENCE.md", "EMERGENT_THESIS.md"}
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
        v = v.strip()
        if v.startswith('"'):
            try:
                v = json.loads(v)            # written with json.dumps (v0.3.5+)
            except ValueError:
                v = v.strip('"')             # older notes: best effort
        out[k.strip()] = v
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
                # Pre-v0.3.3 notes truncated ids to exactly 8 characters. A
                # shorter id cannot be a truncation, so it is not suspect.
                partial = len(lm.group("msg")) == 8
                loc = {
                    "conversation_id": lm.group("conv"),
                    "message_id": lm.group("msg"),
                    "role": lm.group("role") or "",
                    "verified": lm.group("mark") is None and not partial,
                    "ambiguous": lm.group("mark") == "ambiguous",
                }
        text = m.group("text").strip()
        # Task syntax: "- [ ] loop" is open, "- [x] loop" was ticked off (e.g.
        # in Obsidian). The checkbox is state, not part of the item's text.
        done = False
        t = TASK.match(text)
        if t:
            done = t.group("mark").lower() == "x"
            text = text[t.end():]
        item = {"text": text, "anchor": (m.group("anchor") or "").strip(),
                "locator": loc}
        if done:
            item["done"] = True
        found[current].append(item)
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
    if front.get("project_set_by"):
        record["project_set_by"] = front["project_set_by"]
    for line in body.splitlines():
        if line.startswith("**Touches:**"):
            record["links"] = [l.strip() for l in LINKS.findall(line)]
    record.update(_parse_items(body))
    return record


def normalize(text: str) -> str:
    """Parse-side view of a file: no byte-order mark, LF line endings. Editors
    and sync tools on Windows add both; neither may hide a note."""
    return text.lstrip("\ufeff").replace("\r\n", "\n")


def read_note(path) -> dict | None:
    """A Magnum Opus note's record, or None for any other file."""
    return _parse_note(normalize(path.read_text(encoding="utf-8")))


def scan(root) -> tuple:
    """Every Magnum Opus note under root, found by identity, not by path.

    Returns ({segment_key: (path, record)}, other_files, unreadable). A note
    is recognised by its frontmatter (a conversation_id), wherever it lives
    and whatever it is called, so renaming or moving a note in Obsidian never
    loses it. Files without that frontmatter are the user's own and are only
    counted, never read further or touched. On duplicate identities (a note
    copied by hand) the first path in sorted order wins, deterministically.
    """
    found, other, unreadable = {}, 0, 0
    for path in sorted(root.rglob("*.md")):
        if path.name in GENERATED or ".magnum" in path.parts:
            continue
        try:
            text = normalize(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            unreadable += 1
            continue
        if "conversation_id:" not in _parse_front_block(text):
            other += 1
            continue
        try:
            record = _parse_note(text)
        except Exception:
            record = None
        if record is None:
            unreadable += 1
            continue
        found.setdefault(record["segment_key"], (path, record))
    return found, other, unreadable


def _parse_front_block(text: str) -> str:
    m = FRONT.match(text)
    return m.group(1) if m else ""


def reindex(vault) -> dict:
    """Rebuild state["notes"] and state["segments"] from disk. Returns counts."""
    found, other, unreadable = scan(vault.root)
    partial = 0
    notes = []
    for key, (path, record) in found.items():
        items = (record["decisions"] + record["ideas"] + record["open_loops"])
        if any(i["locator"] and not i["locator"]["verified"] and
               len(i["locator"]["message_id"]) == 8 for i in items):
            partial += 1
        notes.append(record)
        vault.state["segments"][key] = {
            "path": path.relative_to(vault.root).as_posix(),
            "project": record["project"],
        }
    vault.state["notes"] = notes
    vault.save()
    return {"notes": len(notes), "partial_locators": partial,
            "unreadable": unreadable, "other_files": other}
