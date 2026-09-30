"""Parsers for AI chat export formats.

Every message keeps a **stable identity** so distilled items can point back at the
immutable corpus:

    Locator = (conversation_id, message_id, role)

For Claude exports, ``message_id`` is the export's own per-message ``uuid``.
For ChatGPT, it is the mapping node id. Both are stable across re-exports.
``raw_index`` is kept only as a fallback and must never be the primary identity:
parsers filter empty messages, so derived positions shift when the filter changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass
class Message:
    id: str            # stable, provider-native (uuid / node id / content hash)
    role: str          # "user" | "assistant"
    text: str
    created_at: str = ""
    parent_id: str = ""            # reply-tree edge; branches vs sequence
    raw_index: int = -1            # position in source array (fallback only)
    id_is_synthetic: bool = False  # True when derived from a content hash


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str
    provider: str
    messages: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(f"{m.role.upper()}: {m.text}" for m in self.messages)

    @property
    def char_count(self) -> int:
        return sum(len(m.text) for m in self.messages)

    def by_id(self) -> dict:
        return {m.id: m for m in self.messages}

    def span(self, start_id: str, end_id: str) -> list:
        """Inclusive slice between two message ids, tolerant of missing bounds."""
        ids = [m.id for m in self.messages]
        i = ids.index(start_id) if start_id in ids else 0
        j = ids.index(end_id) if end_id in ids else len(ids) - 1
        if j < i:
            i, j = j, i
        return self.messages[i:j + 1]


def _iso(ts) -> str:
    if ts is None:
        return ""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return str(ts)


def _synthetic_id(conversation_id: str, index: int, text: str) -> str:
    """Fallback identity: stable against re-export, unlike a bare index."""
    h = hashlib.sha1(f"{conversation_id}:{text}".encode("utf-8")).hexdigest()[:16]
    return f"h{index}-{h}"


# ---------------------------------------------------------------- Claude ----

def _claude_text(msg: dict) -> str:
    parts = []
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
    if not parts and msg.get("text"):
        parts.append(msg["text"])
    return "\n".join(p for p in parts if p)


def parse_claude(data: list) -> Iterator[Conversation]:
    for conv in data:
        conv_id = conv.get("uuid", "")
        messages = []
        for i, msg in enumerate(conv.get("chat_messages") or []):
            text = _claude_text(msg)
            if not text.strip():
                continue
            mid = msg.get("uuid") or ""
            messages.append(Message(
                id=mid or _synthetic_id(conv_id, i, text),
                role="user" if msg.get("sender") == "human" else "assistant",
                text=text,
                created_at=_iso(msg.get("created_at")),
                parent_id=msg.get("parent_message_uuid") or "",
                raw_index=i,
                id_is_synthetic=not mid,
            ))
        yield Conversation(
            id=conv_id,
            title=conv.get("name") or "(untitled)",
            created_at=_iso(conv.get("created_at")),
            updated_at=_iso(conv.get("updated_at")),
            provider="claude",
            messages=messages,
        )


# --------------------------------------------------------------- ChatGPT ----

def parse_chatgpt(data: list) -> Iterator[Conversation]:
    for conv in data:
        conv_id = conv.get("conversation_id") or conv.get("id", "")
        mapping = conv.get("mapping", {}) or {}
        node_id = conv.get("current_node")
        if not node_id:
            parents = {n.get("parent") for n in mapping.values()}
            leaves = [k for k in mapping if k not in parents]
            node_id = leaves[0] if leaves else None

        chain = []
        while node_id:
            node = mapping.get(node_id)
            if node is None:
                break
            msg = node.get("message")
            if msg:
                role = (msg.get("author") or {}).get("role", "")
                content = msg.get("content") or {}
                parts = content.get("parts") if isinstance(content, dict) else None
                text = "\n".join(str(p) for p in parts if isinstance(p, str)) if parts else ""
                if role in ("user", "assistant") and text.strip():
                    chain.append(Message(
                        id=node_id,
                        role=role,
                        text=text,
                        created_at=_iso(msg.get("create_time")),
                        parent_id=node.get("parent") or "",
                    ))
            node_id = node.get("parent")
        chain.reverse()
        for i, m in enumerate(chain):
            m.raw_index = i

        yield Conversation(
            id=conv_id,
            title=conv.get("title") or "(untitled)",
            created_at=_iso(conv.get("create_time")),
            updated_at=_iso(conv.get("update_time")),
            provider="chatgpt",
            messages=chain,
        )


# ------------------------------------------------------------------ auto ----

def detect_provider(data: list) -> str:
    if not data:
        raise ValueError("Export file is empty.")
    sample = data[0]
    if "chat_messages" in sample:
        return "claude"
    if "mapping" in sample:
        return "chatgpt"
    raise ValueError("Unrecognized export format (expected Claude or ChatGPT).")


def parse_conversations(raw: list, provider: str = "auto") -> list:
    if not isinstance(raw, list):
        raise ValueError("Expected a JSON array of conversations.")
    if provider == "auto":
        provider = detect_provider(raw)
    parser = {"claude": parse_claude, "chatgpt": parse_chatgpt}[provider]
    convs = [c for c in parser(raw) if c.messages]
    convs.sort(key=lambda c: c.updated_at or c.created_at, reverse=True)
    return convs


def load_export(path, provider: str = "auto") -> list:
    """Load a bare conversations.json (see archive.load_archive for zips)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_conversations(raw, provider)
