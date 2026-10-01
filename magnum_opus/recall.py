"""Recall: the vault as memory for an agent (Grey's RECALL op, or any other).

Built to what 2026 memory research found works, and to this project's rules:

- **Short structured items, not whole notes.** A recall returns decisions,
  ideas, open loops and summaries, each on its own, because agents act on a
  retrieved fact far more reliably when it is small and specific.
- **No model calls.** Retrieval is deterministic: BM25 over item text,
  optionally fused (reciprocal rank fusion) with an embedding ranking you
  supply. The same memory serves a frontier model, a cheap one or a local one.
- **Every item has a stable id**, derived from the note and the item's text.
  A harness can hold an answer to a citation lock: it may cite only ids that
  recall actually returned in that session (`check_citations`).
- **Time is shown, never scored.** Dates are stripped before matching, so
  when something was said never makes it relevant. Each item still carries its
  date and source. Search decides which items come back; among the decisions
  it returns for one project, the newest comes first, so a later decision is
  seen before the one it may have replaced.
- **Only the person's notes.** Agent-written notes (agents/) are not Magnum
  Opus notes and are never recalled as evidence.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass

from . import sources
from .converge import terms
from .vault import Vault, _done, slugify

K1, B = 1.2, 0.75          # BM25
RRF_K = 60                 # reciprocal rank fusion


@dataclass
class Item:
    id: str
    kind: str              # decision | idea | open_loop | done_loop | summary
    text: str
    project: str
    note: str              # path of the note in the vault
    title: str
    date: str              # when the source conversation was last updated
    source: str            # "the Claude chat “…” (date) [open](url)"
    url: str
    speaker: str = ""      # "user" | "assistant": whose message the item is from; "" unknown
    conversation: str = ""  # the source conversation's id
    message: str = ""      # the source message's id, from the locator; "" if unlocated
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def speaker_of(it: dict) -> str:
    """Whose message an item came from, by its locator: "user" or "assistant".
    Only a verified locator (its anchor matched exactly one message) says so;
    anything less is "" (unknown), never a guess. A decision from an assistant
    turn is that assistant's suggestion, not the user's decision."""
    loc = it.get("locator") or {}
    role = loc.get("role", "")
    return role if loc.get("verified") and role in ("user", "assistant") else ""


def item_id(segment_key: str, kind: str, text: str) -> str:
    return "m-" + hashlib.sha1(f"{segment_key}\n{kind}\n{text}".encode()).hexdigest()[:10]


def items(vault) -> list:
    """Every recallable item in the vault. The vault must be synced."""
    out = []
    for rec in vault.state["notes"]:
        key = rec["segment_key"]
        base = dict(project=slugify(rec.get("project", "")),
                    note=vault.state["segments"].get(key, {}).get("path") or "",
                    title=rec.get("title", ""), date=(rec.get("updated_at") or "")[:10],
                    source=sources.origin(rec), conversation=rec.get("conversation_id", ""),
                    url=sources.chat_url(rec.get("provider", ""), rec.get("conversation_id", "")))
        if rec.get("summary"):
            out.append(Item(item_id(key, "summary", rec["summary"]), "summary",
                            rec["summary"], **base))
        for field, kind in (("decisions", "decision"), ("ideas", "idea"),
                            ("open_loops", "open_loop")):
            for it in rec.get(field, []):
                if not isinstance(it, dict) or not it.get("text"):
                    continue
                k = "done_loop" if kind == "open_loop" and _done(it) else kind
                out.append(Item(item_id(key, kind, it["text"]), k, it["text"], **base,
                                speaker=speaker_of(it),
                                message=(it.get("locator") or {}).get("message_id", "")))
    return out


def _bm25(query: list, docs: list) -> list:
    n = len(docs) or 1
    avg = sum(len(d) for d in docs) / n or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in set(query)}
    scores = []
    for d in docs:
        tf = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in set(query):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * len(d) / avg))
        scores.append(s)
    return scores


def recall(vault, query: str, limit: int = 8, project: str | None = None,
           kinds=None, embed=None) -> list:
    """The items most relevant to `query`, best first.

    `embed`, if given, maps a list of texts to vectors (e.g. a local
    sentence-transformers model); its ranking is fused with BM25. Without it,
    recall is lexical only and needs nothing installed.
    """
    if not isinstance(vault, Vault):
        vault = Vault(vault)
    vault.sync_from_disk()                          # read only: nothing is written
    pool = items(vault)
    if project:
        pool = [i for i in pool if i.project == slugify(project)]
    if kinds:
        pool = [i for i in pool if i.kind in set(kinds)]
    q = terms(query)
    if not pool or not q:
        return []
    docs = [terms(f"{i.text} {i.title}") for i in pool]
    lexical = _bm25(q, docs)
    ranks = {}
    # Equal relevance: newest first, so a later decision precedes what it replaced.
    order = sorted(range(len(pool)), key=lambda k: (-round(lexical[k], 9),
                                                    _neg_date(pool[k].date), pool[k].id))
    for r, k in enumerate(o for o in order if lexical[o] > 0):
        ranks[k] = ranks.get(k, 0.0) + 1.0 / (RRF_K + r + 1)
    if embed is not None:
        vecs = embed([query] + [i.text for i in pool])
        qv, dv = vecs[0], vecs[1:]

        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)
        sem = [cos(qv, v) for v in dv]
        for r, k in enumerate(sorted(range(len(pool)), key=lambda k: -sem[k])):
            ranks[k] = ranks.get(k, 0.0) + 1.0 / (RRF_K + r + 1)
    best = sorted(ranks, key=lambda k: (-round(ranks[k], 9), _neg_date(pool[k].date),
                                        pool[k].id))
    out = []
    for k in best[:limit]:
        pool[k].score = round(ranks[k], 6)
        out.append(pool[k])
    return _newest_decisions_first(out)


def _newest_decisions_first(hits: list) -> list:
    """Reorder each project's decisions by date, newest first, in the places
    those decisions already hold. Nothing else moves."""
    for project in {h.project for h in hits if h.kind == "decision"}:
        slots = [n for n, h in enumerate(hits) if h.kind == "decision" and h.project == project]
        ordered = sorted((hits[n] for n in slots), key=lambda h: _neg_date(h.date))
        for n, h in zip(slots, ordered):
            hits[n] = h
    return hits


def _neg_date(day: str) -> str:
    """Sort key putting later dates first."""
    return "".join(chr(0x10FFFF - ord(c)) for c in day)


def check_citations(cited_ids, recalled_ids) -> dict:
    """The citation lock: an answer may cite only items recall returned.
    Mechanical, so a harness can enforce it and a reward can be computed."""
    cited, allowed = list(dict.fromkeys(cited_ids)), set(recalled_ids)
    bad = [c for c in cited if c not in allowed]
    return {"ok": not bad, "cited": cited, "unrecalled": bad}
