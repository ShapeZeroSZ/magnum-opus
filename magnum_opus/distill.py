"""Distillation: reduce a conversation segment to its durable residue.

Every extracted item carries a **locator** back to the message it came from, so
nothing in the vault is an unverifiable claim about the corpus. The model does
not produce locators directly -- that would ask it to count, which it does
confidently and wrongly. Instead it emits a short verbatim ``anchor`` per item,
and locators are resolved deterministically in code by matching anchors against
the segment's messages. An anchor that matches nothing yields an item marked
``unverified`` rather than a silently wrong citation.

Role matters as much as position: a "decision" lifted from an assistant turn is
a *suggestion*, not the user's decision. The locator records the role so the two
can never be conflated downstream.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from .llm import response_text

CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
MAX_SEGMENT_CHARS = 400_000   # ~100k tokens, safely under a 200k context window


@dataclass
class Locator:
    conversation_id: str
    message_id: str
    role: str = ""
    verified: bool = False
    ambiguous: bool = False

    def to_dict(self):
        return asdict(self)

    def __str__(self):
        mark = "" if self.verified else (" ambiguous" if self.ambiguous else " unverified")
        return f"{self.conversation_id}#{self.message_id} ({self.role}{mark})"


@dataclass
class Item:
    text: str
    anchor: str = ""
    locator: Locator | None = None

    def to_dict(self):
        return {"text": self.text, "anchor": self.anchor,
                "locator": self.locator.to_dict() if self.locator else None}


@dataclass
class Note:
    conversation_id: str
    segment_key: str
    title: str
    provider: str
    updated_at: str
    project: str = "unsorted"
    topic: str = ""
    summary: str = ""
    label: str = ""
    start_id: str = ""
    end_id: str = ""
    author: str = "distiller"
    decisions: list = field(default_factory=list)
    ideas: list = field(default_factory=list)
    open_loops: list = field(default_factory=list)
    links: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("decisions", "ideas", "open_loops"):
            d[k] = [i.to_dict() if isinstance(i, Item) else i for i in getattr(self, k)]
        return d


def strip_for_budget(messages, strip_code: bool = True) -> str:
    """Render a segment as text, dropping assistant code blocks.

    Assistant turns dominate an export by volume and are the least informative
    per token -- the same artifact rewritten eight times. User turns are never
    stripped: they are the primary evidence.
    """
    parts = []
    for m in messages:
        text = m.text
        if strip_code and m.role == "assistant":
            text = CODE_BLOCK.sub("[code omitted]", text)
        parts.append(f"[{m.id[:8]}] {m.role.upper()}: {text}")
    out = "\n\n".join(parts)
    if len(out) > MAX_SEGMENT_CHARS:
        head = out[:int(MAX_SEGMENT_CHARS * 0.7)]
        tail = out[-int(MAX_SEGMENT_CHARS * 0.3):]
        out = f"{head}\n\n[... segment truncated ...]\n\n{tail}"
    return out


DISTILL_PROMPT = """You are the distillation engine of a personal knowledge system. \
Reduce the transcript segment below to only its durable residue. Be ruthless: exclude \
pleasantries, dead ends, and anything re-derivable.

Attribution rules, which matter more than completeness:
- A decision is something the USER decided. An assistant suggestion the user did not \
adopt is NOT a decision -- at most it is an idea.
- Every item must include an "anchor": a short verbatim phrase (5-12 words) copied \
exactly from the message it came from. Anchors are how items are traced back to the \
record, so they must be exact, not paraphrased.

Do NOT assign this to a project. Instead give a "topic": a short free-form label \
(3-8 words) describing what this segment is actually about. Project structure is \
derived later across the whole corpus, so a guess made here in isolation would \
fragment one body of work into several names.

Respond with ONLY a JSON object, no markdown fences:
{{"topic": "<3-8 words>",
  "summary": "<1-3 sentences>",
  "decisions": [{{"text": "...", "anchor": "..."}}],
  "ideas": [{{"text": "...", "anchor": "..."}}],
  "open_loops": [{{"text": "...", "anchor": "..."}}],
  "links": ["<slug or concept>"]}}

TRANSCRIPT SEGMENT ({title}):
{transcript}"""


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


MIN_ANCHOR_WORDS = 4          # below this, a match is coincidence, not evidence
MIN_DEGRADED_WORDS = 5        # a sliding fragment must be at least this specific


def _matches(fragment: str, messages) -> list:
    return [m for m in messages if fragment in _normalize(m.text)]


def resolve_anchor(anchor: str, messages, conversation_id: str):
    """Match an anchor to the message containing it, or return None.

    Returning None is deliberate. A fallback locator pointing at the segment's
    first message would resolve cleanly downstream and pass verification while
    citing a message the anchor never appeared in -- an internally consistent
    falsehood, which is exactly what this architecture exists to prevent. An
    item with no locator is honest and can be filtered.
    """
    needle = _normalize(anchor)
    words = needle.split()
    if len(words) < MIN_ANCHOR_WORDS:
        return None                        # too short to be evidence of anything

    hits = _matches(needle, messages)
    if len(hits) == 1:
        return Locator(conversation_id, hits[0].id, hits[0].role, verified=True)
    if len(hits) > 1:
        # Ambiguous: the anchor appears in several messages. Record the first but
        # never call it verified -- the source is genuinely undetermined.
        return Locator(conversation_id, hits[0].id, hits[0].role,
                       verified=False, ambiguous=True)

    # Degraded match: slide a shrinking window across the anchor rather than
    # truncating its prefix, so a dropped leading article does not defeat a
    # tail that matches exactly.
    for size in range(len(words) - 1, MIN_DEGRADED_WORDS - 1, -1):
        for start in range(0, len(words) - size + 1):
            frag = " ".join(words[start:start + size])
            hits = _matches(frag, messages)
            if len(hits) == 1:
                return Locator(conversation_id, hits[0].id, hits[0].role,
                               verified=False)
    return None


def _items(raw, messages, conversation_id) -> list:
    out = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, dict):
            text, anchor = str(entry.get("text", "")), str(entry.get("anchor", ""))
        else:
            text, anchor = str(entry), ""
        if not text.strip():
            continue
        out.append(Item(text, anchor, resolve_anchor(anchor, messages, conversation_id)))
    return out


class HeuristicDistiller:
    """Offline distiller: no API. For --dry-run and tests."""

    name = "heuristic"

    def __init__(self, known_projects=None, **_):
        self.known_projects = known_projects or []

    def distill(self, conv, segment, messages) -> Note:
        first_user = next((m for m in messages if m.role == "user"), None)
        summary = re.sub(r"\s+", " ", first_user.text).strip()[:280] if first_user else ""
        return Note(
            conversation_id=conv.id, segment_key=segment.key, title=conv.title,
            provider=conv.provider, updated_at=conv.updated_at, project="unsorted",
            topic=(segment.label or conv.title)[:80],
            summary=summary or "(no user text)", label=segment.label,
            start_id=segment.start_id, end_id=segment.end_id,
        )


DEFAULT_MODEL = "claude-haiku-4-5-20251001"   # cheap extraction tier (anthropic)


class LLMDistiller:
    """LLM distiller over any provider client (see llm.py)."""

    name = "llm"

    def __init__(self, client=None, known_projects=None, model: str | None = None,
                 max_tokens: int = 3000, strip_code: bool = True,
                 provider: str = "anthropic"):
        if client is None:
            from .llm import make_client
            client = make_client(provider)
        if not model:
            if provider != "anthropic":
                raise RuntimeError(f"--model is required with the {provider} provider.")
            model = DEFAULT_MODEL
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.strip_code = strip_code
        self.known_projects = known_projects or []

    def distill(self, conv, segment, messages) -> Note:
        prompt = DISTILL_PROMPT.format(
            title=f"{conv.title} :: {segment.label}" if segment.label else conv.title,
            transcript=strip_for_budget(messages, self.strip_code),
        )
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response_text(resp)
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"summary": text[:500]}
        if not isinstance(data, dict):
            data = {"summary": text[:500]}

        return Note(
            conversation_id=conv.id, segment_key=segment.key, title=conv.title,
            provider=conv.provider, updated_at=conv.updated_at,
            project="unsorted",
            topic=str(data.get("topic", ""))[:80],
            summary=str(data.get("summary", "")), label=segment.label,
            start_id=segment.start_id, end_id=segment.end_id,
            decisions=_items(data.get("decisions"), messages, conv.id),
            ideas=_items(data.get("ideas"), messages, conv.id),
            open_loops=_items(data.get("open_loops"), messages, conv.id),
            links=[str(x) for x in (data.get("links") or []) if isinstance(x, str)],
        )


class AnthropicDistiller(LLMDistiller):
    """Backward-compatible name: LLMDistiller on the anthropic provider."""

    name = "anthropic"

    def __init__(self, known_projects=None, model: str = DEFAULT_MODEL,
                 max_tokens: int = 3000, strip_code: bool = True, client=None):
        super().__init__(client=client, known_projects=known_projects, model=model,
                         max_tokens=max_tokens, strip_code=strip_code,
                         provider="anthropic")


def get_distiller(backend: str, known_projects, model=None, strip_code=True,
                  client=None, provider: str = "anthropic"):
    """backend: "heuristic" (offline) or "llm" ("anthropic" is accepted as an
    alias for llm on the anthropic provider, as in earlier versions)."""
    if backend == "heuristic":
        return HeuristicDistiller(known_projects)
    if backend == "anthropic":
        return AnthropicDistiller(known_projects=known_projects,
                                  model=model or DEFAULT_MODEL,
                                  strip_code=strip_code, client=client)
    if backend == "llm":
        return LLMDistiller(client=client, known_projects=known_projects,
                            model=model, strip_code=strip_code, provider=provider)
    raise ValueError(f"Unknown distiller backend: {backend}")
