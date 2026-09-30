"""Open-loop proposals: an agent suggests, you decide, the tool carries it out.

An AI assistant connected with `magnum serve --allow-write` can propose that
an open loop is finished (or no longer needed), with its reason and the notes
that show it. Proposals collect in PROPOSALS.md. Nothing changes until you
decide:

- Tick **accept** and run `magnum proposals`: the loop is ticked in its note,
  exactly as if you had ticked it yourself in Obsidian. Only that checkbox
  changes; the rest of the note is untouched.
- Tick **reject**: the loop stays open, and no agent can propose closing it
  again unless you untick.
- Or ignore it. Ticking the loop in the note yourself works too; the proposal
  then quietly resolves.

The agent never ticks anything. Recording your ticks is safe at any time (the
server does it before adding a proposal, so your ticks are never lost), but
only `magnum proposals`, which you run, changes a note.

Who proposed what, why, and when you decided, is kept in
.magnum/proposals.json.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from . import converge as conv
from .vault import _done, slugify

BLOCK = re.compile(r"<!--proposal:(?P<id>p-[0-9a-f]{6})-->")
MAX_PENDING_PER_AGENT = 20
SHOWN = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def loop_id(segment_key: str, text: str) -> str:
    return "l-" + hashlib.sha1(f"{segment_key}\n{text}".encode()).hexdigest()[:8]


def open_loops(vault) -> list:
    """Every open loop, with a stable id. The vault must be synced."""
    out = []
    for rec in vault.state["notes"]:
        key = rec["segment_key"]
        for loop in rec.get("open_loops", []):
            if isinstance(loop, dict) and not _done(loop):
                out.append({"id": loop_id(key, loop["text"]), "key": key,
                            "text": loop["text"],
                            "project": slugify(rec.get("project", "")),
                            "title": rec.get("title", ""),
                            "path": vault.state["segments"].get(key, {}).get("path") or ""})
    return out


# --- state ---------------------------------------------------------------------------

def load_state(vault) -> dict:
    p = vault.root / ".magnum" / "proposals.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"proposals": {}}


def save_state(vault, state):
    vault._atomic_write(vault.root / ".magnum" / "proposals.json",
                        json.dumps(state, indent=2))


def record_ticks(vault, state) -> list:
    """Your ticks in PROPOSALS.md become decisions. Unticking a decision that
    has not been carried out yet takes it back."""
    changes = []
    ticks = conv.read_ticks(vault.root / "PROPOSALS.md", BLOCK)
    for pid, tick in sorted(ticks.items()):
        p = state["proposals"].get(pid)
        if not p or p["status"] not in ("proposed", "accepted", "rejected"):
            continue
        if tick == "conflict":
            changes.append(f"{pid}: both accept and reject ticked; left as it was")
            continue
        new = tick or "proposed"
        if new != p["status"]:
            p["status"] = new
            if new == "proposed":
                p.pop("decided_at", None)
                p.pop("decided_by", None)
            else:
                p.update(decided_at=_now(), decided_by="human")
            changes.append(f"{pid}: {new}" if tick else f"{pid}: back to undecided")
    return changes


def resolve_stale(vault, state) -> list:
    """Proposals whose loop is no longer open: you ticked it, edited it or
    deleted its note. There is nothing left to decide."""
    live = {l["id"] for l in open_loops(vault)}
    changes = []
    for pid, p in sorted(state["proposals"].items()):
        if p["status"] in ("proposed", "accepted") and p["loop_id"] not in live:
            p.update(status="resolved", resolved_at=_now())
            changes.append(f"{pid}: the loop is no longer open; nothing to do")
    return changes


# --- proposing (the agent) --------------------------------------------------------------

class ProposalError(Exception):
    pass


def propose(vault, state, loop: str, reason: str, evidence=(), agent: str = "agent") -> str:
    """Record an agent's proposal to close an open loop. Never changes a note."""
    loops = {l["id"]: l for l in open_loops(vault)}
    target = loops.get(str(loop).strip())
    if not target:
        raise ProposalError(f"No open loop {loop!r}. Use list_open_loops for ids.")
    reason = re.sub(r"\s+", " ", str(reason)).strip()
    if not reason:
        raise ProposalError("Say why the loop can be closed.")
    reason = reason[:500]
    for p in state["proposals"].values():
        if p["loop_id"] != target["id"]:
            continue
        if p["status"] == "rejected":
            raise ProposalError("The person rejected closing this loop. It stays open "
                                "unless they untick that rejection.")
        if p["status"] in ("proposed", "accepted"):
            raise ProposalError("Closing this loop is already proposed.")
    pending = sum(1 for p in state["proposals"].values()
                  if p["by"] == f"agent:{agent}" and p["status"] == "proposed")
    if pending >= MAX_PENDING_PER_AGENT:
        raise ProposalError(f"You already have {pending} proposals waiting for the "
                            "person. Wait until they have decided some.")
    files = []
    for e in evidence or []:
        rel = str(e).strip()
        rel = rel if rel.endswith(".md") else rel + ".md"
        try:
            path = (vault.root / rel).resolve()
            path.relative_to(vault.root.resolve())
        except (ValueError, OSError):
            continue
        if path.is_file() and ".magnum" not in path.parts and rel not in files:
            files.append(rel)
    pid = "p-" + hashlib.sha1(f"{target['id']}\n{_now()}\n{agent}".encode()).hexdigest()[:6]
    state["proposals"][pid] = {
        "loop_id": target["id"], "segment_key": target["key"], "loop": target["text"],
        "project": target["project"], "action": "close", "reason": reason,
        "evidence": files[:5], "by": f"agent:{agent}", "at": _now(), "status": "proposed"}
    return pid


# --- carrying out your decisions (you) ------------------------------------------------------

def apply(vault, state) -> list:
    """Tick every loop you accepted closing. Only that checkbox changes."""
    changes = []
    for pid, p in sorted(state["proposals"].items()):
        if p["status"] != "accepted":
            continue
        if vault.close_loop(p["segment_key"], p["loop"]):
            p.update(status="applied", applied_at=_now())
            changes.append(f"{pid}: closed “{p['loop']}”")
        else:
            p.update(status="resolved", resolved_at=_now())
            changes.append(f"{pid}: the loop is no longer open; nothing to do")
    return changes


# --- the generated view ------------------------------------------------------------------

def _link(vault, rel) -> str:
    return f"[[{rel[:-3]}]]" if rel.endswith(".md") else rel


def _block(vault, pid, p) -> list:
    note = vault.state["segments"].get(p["segment_key"], {}).get("path") or ""
    lines = [f"### Close “{p['loop']}”? <!--proposal:{pid}-->",
             f"{p['project']} · proposed by **{p['by']}**"
             + (f" · in {_link(vault, note)}" if note else ""),
             f"- why: {p['reason']}"]
    if p["evidence"]:
        lines.append("- evidence: " + ", ".join(_link(vault, e) for e in p["evidence"]))
    lines.append(f"- [{'x' if p['status'] == 'accepted' else ' '}] accept")
    lines.append(f"- [{'x' if p['status'] == 'rejected' else ' '}] reject")
    return lines + [""]


def render(vault, state) -> str:
    ps = sorted(state["proposals"].items(), key=lambda kv: (kv[1]["at"], kv[0]))
    waiting = [(i, p) for i, p in ps if p["status"] == "proposed"]
    accepted = [(i, p) for i, p in ps if p["status"] == "accepted"]
    rejected = [(i, p) for i, p in ps if p["status"] == "rejected"]
    lines = ["# PROPOSALS — agents' suggestions, for you to decide", "",
             f"_Generated {_now()}. Each is an agent's suggestion to close one of your "
             "open loops; nothing changes until you decide. Tick **accept** and run "
             "`magnum proposals` to close the loop in its note, or **reject** to keep it "
             "open for good. Ticking the loop in the note yourself works too._", ""]
    if accepted:
        lines += ["## Accepted, not yet carried out (run `magnum proposals`)", ""]
        for i, p in accepted:
            lines += _block(vault, i, p)
    lines += ["## Waiting for you", ""]
    for i, p in waiting[:SHOWN]:
        lines += _block(vault, i, p)
    if not waiting:
        lines += ["_Nothing waiting._", ""]
    if len(waiting) > SHOWN:
        lines += [f"_{len(waiting) - SHOWN} more waiting; they appear here as you "
                  "decide these._", ""]
    if rejected:
        lines += ["## Rejected by you (the loop stays open; untick to reconsider)", ""]
        for i, p in rejected:
            lines += _block(vault, i, p)
    return "\n".join(lines).rstrip() + "\n"


def refresh(vault, state) -> list:
    """Record your ticks, drop what is moot, rewrite PROPOSALS.md, save.
    Changes no note."""
    changes = record_ticks(vault, state) + resolve_stale(vault, state)
    vault._atomic_write(vault.root / "PROPOSALS.md", render(vault, state))
    save_state(vault, state)
    return changes


def status_of(state) -> dict:
    """{loop_id: status} for loops with a live or rejected proposal."""
    out = {}
    for p in state["proposals"].values():
        if p["status"] in ("proposed", "accepted", "rejected"):
            out[p["loop_id"]] = p["status"]
    return out
