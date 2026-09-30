"""Master sort: derive the project taxonomy from the corpus, not from a guess.

Distillation deliberately does not assign projects. A segment seen in isolation
cannot know whether "the convergence engine" and "the PWA queue" are two projects
or two weeks of one -- and guessing per segment fragments one body of work into
several names. Nor should the user hand-write the taxonomy up front: that is the
labour the tool exists to remove, and it freezes their current self-description
into the data before the convergence layer has a chance to disagree with it.

So sorting happens once, over everything, after distillation:

  1. Collect every note's topic label and one-line summary (cheap -- a few
     hundred notes fit in a single context).
  2. One pass proposes the taxonomy: clusters, canonical names, and which notes
     belong to each.
  3. Notes are rewritten into their assigned projects and rollups rebuilt.

Re-running is safe and idempotent: the taxonomy is recomputed from the notes,
so a corpus that has grown produces a corrected structure rather than an
accreted one.
"""

from __future__ import annotations

import json
import re

from .llm import response_text

TAXONOMY_PROMPT = """You are deriving the project structure of someone's body of work \
from their distilled notes. Below is one line per note: a topic label and a one-line \
summary.

Identify the coherent projects this work consists of. Judge by what the work *is*, not \
by wording: notes describing the same body of work belong together even when they use \
different names for it. Prefer fewer, truer projects over many thin ones -- if two \
candidates are phases or facets of one effort, merge them. Aim for roughly 5-20 \
projects.

Then rank them by what would most repay attention next. You are seeing the whole body \
of work at once, so judge rather than count -- a project with many loose ends may be \
the live one, and one with few may be abandoned. Weigh how close it is to a finishable \
state, whether finishing it unblocks other work, whether it is still live, and how much \
is genuinely undone rather than already superseded.

Do NOT list note ids. Respond with ONLY a JSON object, no markdown fences:
{{"projects": [
   {{"slug": "<lowercase-hyphenated>",
     "description": "<one line>",
     "aliases": ["<other names this work goes by>"],
     "status": "active|dormant|done",
     "next_action": "<the single most useful next step>",
     "rank_reason": "<one line: why it sits where it does>"}}
 ],
 "priority_order": ["<slug>", "<slug>", ...]}}

"priority_order" lists every slug, most worth attention first.

NOTES ({n}):
{index}"""


ASSIGN_PROMPT = """Assign each note below to exactly one project slug from this list.

{projects}

Use "misc" only if a note genuinely belongs to none of them. Respond with ONLY a JSON \
object mapping note id to slug, no markdown fences:
{{"n0": "<slug>", "n1": "<slug>", ...}}

NOTES:
{index}"""


def build_index(notes: list[dict]) -> tuple[str, dict]:
    """Compact one-line-per-note view, plus a short-id lookup."""
    lines, lookup = [], {}
    for i, n in enumerate(notes):
        sid = f"n{i}"
        lookup[sid] = n["segment_key"]
        topic = (n.get("topic") or n.get("label") or n.get("title") or "").strip()
        summary = re.sub(r"\s+", " ", n.get("summary", ""))[:160]
        lines.append(f"{sid} | {topic} | {summary}")
    return "\n".join(lines), lookup


def _call_json(client, model, prompt, max_tokens, debug_path=None):
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response_text(resp)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        if debug_path:
            debug_path.write_text(text, encoding="utf-8")
        stop = getattr(resp, "stop_reason", "?")
        raise RuntimeError(
            f"Model response was not valid JSON ({e}; stop_reason={stop}). "
            + (f"Raw response saved to {debug_path}." if debug_path else "")
        )


def propose_taxonomy(notes, client, model: str, max_tokens: int = 16000,
                     assign_model: str = "claude-haiku-4-5-20251001",
                     batch: int = 60, debug_dir=None, progress=print) -> tuple:
    """Two stages, because one call cannot echo hundreds of ids without truncating.

    Stage 1 derives the taxonomy from topics alone -- small output, no ids.
    Stage 2 assigns notes to those slugs in batches, which is mechanical work and
    runs on the cheap tier.
    """
    index, lookup = build_index(notes)
    dbg = (debug_dir / "sort_raw.txt") if debug_dir else None

    progress(f"  deriving taxonomy from {len(notes)} notes...")
    data = _call_json(client, model,
                      TAXONOMY_PROMPT.format(n=len(notes), index=index),
                      max_tokens, dbg)

    meta = {}
    for group in data.get("projects", []):
        slug = re.sub(r"[^a-z0-9\-]+", "-", str(group.get("slug", "")).lower()).strip("-")
        if not slug:
            continue
        meta[slug] = {
            "description": str(group.get("description", ""))[:200],
            "status": str(group.get("status", "active")).lower(),
            "next_action": str(group.get("next_action", ""))[:300],
            "rank_reason": str(group.get("rank_reason", ""))[:200],
            "aliases": [str(a)[:60] for a in (group.get("aliases") or [])][:8],
        }

    # Ranking is the model's judgment over the whole corpus, not a loop count.
    order = [re.sub(r"[^a-z0-9\-]+", "-", str(x).lower()).strip("-")
             for x in data.get("priority_order", [])]
    for i, slug in enumerate([o for o in order if o in meta]):
        meta[slug]["rank"] = i
    for slug in meta:
        meta[slug].setdefault("rank", len(meta))

    # Stage 2: assign notes to the derived slugs, in batches.
    catalogue = "\n".join(
        f"- {slug}: {m['description']}"
        + (f" (also: {', '.join(m['aliases'])})" if m.get("aliases") else "")
        for slug, m in meta.items()
    )
    lines = index.splitlines()
    assignment = {}
    for start in range(0, len(lines), batch):
        chunk = lines[start:start + batch]
        progress(f"  assigning notes {start + 1}-{start + len(chunk)} "
                 f"of {len(lines)}...")
        try:
            mapping = _call_json(
                client, assign_model,
                ASSIGN_PROMPT.format(projects=catalogue, index="\n".join(chunk)),
                4000, dbg)
        except RuntimeError as e:
            progress(f"    batch failed ({e}); those notes go to misc")
            continue
        for sid, slug in (mapping or {}).items():
            slug = re.sub(r"[^a-z0-9\-]+", "-", str(slug).lower()).strip("-")
            key = lookup.get(str(sid))
            if key and slug in meta:
                assignment[key] = slug

    # Anything unassigned stays visible rather than vanishing.
    for n in notes:
        assignment.setdefault(n["segment_key"], "misc")
    meta.setdefault("misc", {"description": "unclustered notes", "status": "dormant",
                             "next_action": "", "rank_reason": "", "rank": len(meta)})
    return assignment, meta


def apply_taxonomy(vault, assignment: dict, meta: dict) -> dict:
    """Rewrite notes into their assigned projects and rebuild rollups."""
    from .distill import Note, Item, Locator

    counts = {}
    for record in list(vault.state["notes"]):
        slug = assignment.get(record["segment_key"], "misc")
        record["project"] = slug
        counts[slug] = counts.get(slug, 0) + 1

        def items(raw):
            out = []
            for i in raw or []:
                loc = i.get("locator") if isinstance(i, dict) else None
                out.append(Item(
                    i.get("text", "") if isinstance(i, dict) else str(i),
                    i.get("anchor", "") if isinstance(i, dict) else "",
                    Locator(**loc) if loc else None,
                ))
            return out

        note = Note(
            conversation_id=record["conversation_id"], segment_key=record["segment_key"],
            title=record["title"], provider=record["provider"],
            updated_at=record["updated_at"], project=slug,
            topic=record.get("topic", ""), summary=record.get("summary", ""),
            label=record.get("label", ""), start_id=record.get("start_id", ""),
            end_id=record.get("end_id", ""), author=record.get("author", "distiller"),
            decisions=items(record.get("decisions")), ideas=items(record.get("ideas")),
            open_loops=items(record.get("open_loops")), links=record.get("links", []),
        )
        vault.write_note(note)

    vault.config["projects"] = sorted(counts)
    vault.config["project_meta"] = meta
    vault.rebuild_rollups()
    vault.save()
    return counts


def cleanup_unsorted(vault):
    """Remove note files left behind in /unsorted after reassignment."""
    d = vault.root / "unsorted"
    if not d.exists():
        return
    for f in d.glob("*.md"):
        f.unlink()
    try:
        d.rmdir()
    except OSError:
        pass
