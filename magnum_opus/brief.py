"""BRIEF.md: the state of your work in one file, to hand to any assistant.

`magnum brief` writes a single markdown file you can upload to another
conversation, or give to Claude Code or any agent, so it can pick up the work:
your guidance, priorities, every project's next step and open loops, how your
work relates, the current thesis, and what is waiting for your decision.

It is built from the vault by code, with no model call, so it is free,
instant and exactly what the vault says. Every reference carries the link to
its original conversation, which works outside the vault too.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import converge as conv, guidance, proposals as props, sources, thesis as th
from .vault import _done, slugify

LOOPS_PER_PROJECT = 10
DECISIONS_PER_PROJECT = 5


def _where(rec) -> str:
    return sources.origin(rec)


def build(vault) -> str:
    vault.sync_from_disk()
    notes = vault.state["notes"]
    meta = vault.config.get("project_meta", {})
    by_project = {}
    for rec in notes:
        by_project.setdefault(slugify(rec.get("project", "")), []).append(rec)
    order = sorted(by_project, key=lambda p: (meta.get(p, {}).get("rank", 10 ** 6), p))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out = ["# BRIEF — the state of my work", "",
           f"_Generated {now} by `magnum brief` from {len(notes)} notes in "
           f"{len(by_project)} projects. For an assistant picking up this work: it is "
           "a snapshot of my Magnum Opus vault. Each reference names the conversation "
           "it came from, with a link. Only what is marked as mine is my decision; the "
           "rest was inferred by tools. Follow my guidance first._", ""]

    g = guidance.read(vault)
    out += ["## How I want the work handled", "", g if g else "_No guidance written yet._", ""]

    out += ["## Priorities", ""]
    ranked = [p for p in order if p not in conv.UNPLACED]
    for i, p in enumerate(ranked, 1):
        m = meta.get(p, {})
        line = f"{i}. **{p}**" + (f" [{m['status']}]" if m.get("status") else "")
        if m.get("next_action"):
            line += f": next, {m['next_action']}"
        if m.get("rank_reason"):
            line += f" _({m['rank_reason']})_"
        out.append(line)
    if not ranked:
        out.append("_Not sorted into projects yet._")
    out.append("")

    status = props.status_of(props.load_state(vault))
    out += ["## Projects", ""]
    for p in order:
        recs = by_project[p]
        m = meta.get(p, {})
        out.append(f"### {p}" + (f": {m['description']}" if m.get("description") else ""))
        loops = [(r, l) for r in recs for l in r.get("open_loops", [])
                 if isinstance(l, dict) and not _done(l)]
        if loops:
            out.append("Open loops:")
            for r, l in loops[:LOOPS_PER_PROJECT]:
                lid = props.loop_id(r["segment_key"], l["text"])
                flag = {"proposed": " (closing it is proposed)",
                        "accepted": " (I accepted closing it)",
                        "rejected": " (I decided it stays open)"}.get(status.get(lid), "")
                out.append(f"- [ ] {l['text']}{flag}. {lid}, from {_where(r)}")
            if len(loops) > LOOPS_PER_PROJECT:
                out.append(f"- ... and {len(loops) - LOOPS_PER_PROJECT} more")
        decisions = [(r, d) for r in recs for d in r.get("decisions", [])
                     if isinstance(d, dict)]
        if decisions:
            out.append("Decisions:")
            for r, d in decisions[:DECISIONS_PER_PROJECT]:
                out.append(f"- {d['text']}. From {_where(r)}")
        out.append(f"_{len(recs)} notes._")
        out.append("")

    cstate = conv.load_state(vault)
    accepted = [r for r in cstate.get("latest", []) if r.get("decision") == "accepted"]
    if accepted:
        out += ["## How my work relates (confirmed by me)", ""]
        out += [f"- {r['labels'][0]} ↔ {r['labels'][1]}" for r in accepted]
        out.append("")

    tstate = th.load_state(vault)
    if tstate["versions"]:
        v = tstate["versions"][-1]
        out += [f"## What my work appears to be about (thesis, version {v['version']})", "",
                "> " + v["statement"], ""]
        mine = [c for c in tstate["feedback"].values() if c.get("decision") == "accepted"]
        if mine:
            out.append("Confirmed by me:")
            out += [f"- {c['text']}" for c in mine]
        rest = [c for c in v["claims"]
                if tstate["feedback"].get(c["id"], {}).get("decision") is None]
        if rest:
            out.append("Proposed, not yet judged by me:")
            out += [f"- {c['text']}" for c in rest]
        out.append("")

    waiting = [p for p in props.load_state(vault)["proposals"].values()
               if p["status"] == "proposed"]
    if waiting:
        out += ["## Waiting for my decision", ""]
        out += [f"- Close “{p['loop']}” ({p['project']})? Proposed by {p['by']}: {p['reason']}"
                for p in waiting]
        out.append("")

    out += ["## Working on this", "",
            "- Ask me before anything that spends money or changes my notes.",
            "- Refer to notes by the conversation links above so I can find them.",
            "- If my vault is connected (`magnum serve`), read it there for the full "
            "notes; propose closing loops rather than editing them."]
    return "\n".join(out).rstrip() + "\n"


def write(vault, path=None):
    path = path or (vault.root / "BRIEF.md")
    vault._atomic_write(path, build(vault))
    return path
