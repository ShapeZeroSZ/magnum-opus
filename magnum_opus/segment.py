"""Segmentation: split a long conversation into topic-coherent segments.

A single thread can run for months and cover several unrelated projects, so
"one note per conversation" loses most of what happened. Segmentation runs in
two passes:

  1. **Skeleton** -- a compact view (id, role, timestamp, first ~200 chars of
     each message). A 300-message thread is roughly 15k tokens this way, so the
     model sees the entire arc at once and picks boundaries with full context.
  2. **Distillation** -- each segment is distilled at full text, separately.

Segmenting on the skeleton is what keeps boundaries stable as a thread grows:
appending messages does not rewrite the earlier skeleton, so earlier segments
keep their identity and only changed ranges need re-distilling.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm import response_text

SKELETON_CHARS = 200
DEFAULT_MAX_MESSAGES = 40    # heuristic fallback window
MIN_SEGMENT_MESSAGES = 4


@dataclass
class Segment:
    conversation_id: str
    start_id: str
    end_id: str
    label: str = ""

    @property
    def key(self) -> str:
        """Stable identity for incremental processing."""
        return f"{self.conversation_id}:{self.start_id}:{self.end_id}"


def skeleton(conv, chars: int = SKELETON_CHARS) -> str:
    lines = []
    for m in conv.messages:
        head = re.sub(r"\s+", " ", m.text).strip()[:chars]
        stamp = (m.created_at or "")[:10]
        lines.append(f"{m.id} | {m.role} | {stamp} | {head}")
    return "\n".join(lines)


SEGMENT_PROMPT = """You are segmenting a long AI chat transcript into topic-coherent \
sections. Below is a skeleton: one line per message, formatted as \
`message_id | role | date | opening text`.

Identify where the subject genuinely changes -- a different project, a different \
problem. Ignore incidental digressions; a segment should be a body of work, not a \
single exchange. Most threads have between 2 and 15 real segments.

Respond with ONLY a JSON array, no markdown fences. Each element:
  {{"start_id": "<message_id>", "end_id": "<message_id>", "label": "<3-6 words>"}}

Segments must be contiguous, non-overlapping, in order, and must cover every \
message from the first to the last.

SKELETON ({title}, {n} messages):
{skeleton}"""


def heuristic_segments(conv, max_messages: int = DEFAULT_MAX_MESSAGES) -> list:
    """Offline fallback: fixed windows on message boundaries."""
    msgs = conv.messages
    out = []
    for i in range(0, len(msgs), max_messages):
        window = msgs[i:i + max_messages]
        if not window:
            continue
        if len(window) < MIN_SEGMENT_MESSAGES and out:
            out[-1].end_id = window[-1].id      # fold a tiny tail into the last
            continue
        out.append(Segment(conv.id, window[0].id, window[-1].id,
                           label=f"messages {i + 1}-{i + len(window)}"))
    return out


def llm_segments(conv, client, model: str, max_tokens: int = 4000) -> list:
    """Ask the model for boundaries, validated against real message ids."""
    prompt = SEGMENT_PROMPT.format(
        title=conv.title, n=len(conv.messages), skeleton=skeleton(conv)
    )
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response_text(resp)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return heuristic_segments(conv)

    valid = {m.id for m in conv.messages}
    segs = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        s, e = str(item.get("start_id", "")), str(item.get("end_id", ""))
        if s in valid and e in valid:
            segs.append(Segment(conv.id, s, e, str(item.get("label", ""))[:60]))
    if not segs:
        return heuristic_segments(conv)

    # Guarantee full coverage: the model must not silently drop the tail.
    order = {m.id: i for i, m in enumerate(conv.messages)}
    segs.sort(key=lambda s: order[s.start_id])
    if order[segs[0].start_id] != 0:
        segs[0].start_id = conv.messages[0].id
    if order[segs[-1].end_id] != len(conv.messages) - 1:
        segs[-1].end_id = conv.messages[-1].id
    return segs


def segment_conversation(conv, client=None, model: str | None = None,
                         max_messages: int = DEFAULT_MAX_MESSAGES) -> list:
    if len(conv.messages) <= max_messages:
        return [Segment(conv.id, conv.messages[0].id, conv.messages[-1].id,
                        label=conv.title)]
    if client is None:
        return heuristic_segments(conv, max_messages)
    try:
        return llm_segments(conv, client, model)
    except Exception:
        return heuristic_segments(conv, max_messages)
