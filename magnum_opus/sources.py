"""Where things came from, so every reference can be found.

A reference is only useful if you can get back to it. Anything that points at
a note (STATUS, CONVERGENCE, the thesis, proposals, the agent tools) also
says which original conversation it came from, when, and gives a link that
opens that conversation in Claude or ChatGPT.

`magnum find` searches the notes, and optionally the raw export itself, so an
idea you remember but cannot place is found even if distillation left it out.

Dates here answer *where and when*, for finding. They are provenance, never
evidence: nothing here feeds convergence or the thesis.
"""

from __future__ import annotations

import math
import re

from .converge import terms

PROVIDER_NAMES = {"claude": "Claude", "chatgpt": "ChatGPT", "shapezero": "Shape Zero"}
CHAT_URLS = {"claude": "https://claude.ai/chat/{id}",
             "chatgpt": "https://chatgpt.com/c/{id}"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def chat_url(provider: str, conversation_id: str) -> str:
    """A link that opens the original conversation, or "" if there is none."""
    template = CHAT_URLS.get((provider or "").lower())
    cid = str(conversation_id or "")
    if not template or not SAFE_ID.match(cid):
        return ""
    return template.format(id=cid)


def origin(rec: dict) -> str:
    """'the Claude chat “Title” (2026-07-01) [open](url)', in markdown."""
    provider = (rec.get("provider") or "").lower()
    name = PROVIDER_NAMES.get(provider, provider or "the")
    title = (rec.get("title") or "").strip() or "untitled"
    day = (rec.get("updated_at") or "")[:10]
    url = chat_url(provider, rec.get("conversation_id", ""))
    return (f"the {name} chat “{title}”" + (f" ({day})" if day else "")
            + (f" [open]({url})" if url else ""))


def note_link(vault, key: str, label: str | None = None) -> str:
    """[[path|title]] for a note, by identity."""
    path = vault.state["segments"].get(key, {}).get("path") or ""
    if not path:
        return label or "(a note no longer in the vault)"
    rec = next((r for r in vault.state["notes"] if r["segment_key"] == key), {})
    return f"[[{path[:-3]}|{label or rec.get('title') or path}]]"


def where(vault, key: str) -> str:
    """The note, and the conversation it came from."""
    rec = next((r for r in vault.state["notes"] if r["segment_key"] == key), None)
    if rec is None:
        return "(a note no longer in the vault)"
    return f"{note_link(vault, key)}, from {origin(rec)}"


# --- finding -------------------------------------------------------------------

def _score(query: list, docs: list) -> list:
    """Smoothed-idf overlap of the query's words with each document's words."""
    n = len(docs)
    qs = set(query)
    df = {t: sum(1 for ts in docs if t in ts) for t in qs}
    return [sum(math.log((1 + n) / (1 + df[t])) + 1 for t in qs if t in ts)
            for ts in docs]


def note_text(rec: dict) -> str:
    parts = [rec.get("title", ""), rec.get("label", ""), rec.get("topic", ""),
             rec.get("summary", "")]
    for k in ("decisions", "ideas", "open_loops"):
        parts += [i.get("text", "") for i in rec.get(k, []) if isinstance(i, dict)]
    return "\n".join(p for p in parts if p)


def search_notes(vault, query: str, limit: int = 10) -> list:
    """[(score, record)] best first. The vault must be synced."""
    q = terms(query)
    if not q:
        return []
    recs = vault.state["notes"]
    scores = _score(q, [set(terms(note_text(r))) for r in recs])
    hits = sorted(((s, r) for s, r in zip(scores, recs) if s > 0),
                  key=lambda x: (-x[0], x[1]["segment_key"]))
    return hits[:limit]


def _snippet(text: str, words: set, width: int = 160) -> str:
    low = text.lower()
    at = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    start = max(0, at - width // 3)
    s = re.sub(r"\s+", " ", text[start:start + width]).strip()
    return ("…" if start else "") + s + ("…" if start + width < len(text) else "")


def search_conversations(convs, query: str, limit: int = 10) -> list:
    """[(score, conversation, snippet)] over the raw export, best first.
    Local and free: nothing is sent anywhere."""
    q = terms(query)
    if not q:
        return []
    docs = [set(terms(c.title + "\n" + c.text)) for c in convs]
    scores = _score(q, docs)
    words = set(q)
    hits = []
    for s, c in zip(scores, convs):
        if s <= 0:
            continue
        best = max(c.messages, key=lambda m: len(words & set(terms(m.text))),
                   default=None)
        hits.append((s, c, _snippet(best.text, words) if best else ""))
    hits.sort(key=lambda x: (-x[0], x[1].id))
    return hits[:limit]
