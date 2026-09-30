"""Archive loading for Claude data exports.

A Claude export arrives as several category zips, optionally sharded:

    conversations-000.zip    the corpus
    memories-000.zip         assistant-written state about the user
    projects-000.zip         project containers and docs
    light_metadata-000.zip   account PII -- NEVER ingested

Categories are decided here, not downstream, so the exclusion is structural
rather than a filter someone can forget to apply.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from .parsers import parse_conversations

# Structural exclusion. light_metadata holds email, full name, phone number and
# login history with zero corpus value; there is no mode in which it is read.
NEVER_INGEST = {"light_metadata"}

SHARD = re.compile(r"^(?P<category>[a-z_]+)-(?P<part>\d+)\.zip$", re.IGNORECASE)


def discover(folder: str | Path) -> dict[str, list[Path]]:
    """Group export zips by category, shards sorted by part number."""
    folder = Path(folder)
    found: dict[str, list[tuple[int, Path]]] = {}
    for p in sorted(folder.glob("*.zip")):
        m = SHARD.match(p.name)
        if m:
            cat, part = m.group("category").lower(), int(m.group("part"))
        else:
            cat, part = p.stem.lower(), 0
        found.setdefault(cat, []).append((part, p))
    return {c: [p for _, p in sorted(v)] for c, v in found.items()}


def _json_members(path: Path, prefix: str = "") -> list:
    out = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".json") and name.startswith(prefix):
                out.append((name, json.load(z.open(name))))
    return out


def load_conversations(folder: str | Path, provider: str = "auto") -> list:
    """Load and merge every conversations shard in a folder."""
    shards = discover(folder).get("conversations", [])
    if not shards:
        raise FileNotFoundError(
            f"No conversations-*.zip found in {folder}. "
            "Point --export at the folder holding the export zips."
        )
    raw: list = []
    for path in shards:
        for _, data in _json_members(path):
            if isinstance(data, list):
                raw.extend(data)
    return parse_conversations(raw, provider)


def load_memories(folder: str | Path) -> list[dict]:
    """Load the memories corpus as flat records.

    These are **assistant-written** observations about the user. They are
    legitimate corpus material but carry a distinct provenance class and must
    never be attributed to the user as their own words.
    """
    records: list[dict] = []
    for path in discover(folder).get("memories", []):
        for name, data in _json_members(path):
            if not isinstance(data, dict):
                continue
            for key in ("memory_files", "conversations_memory", "project_memories"):
                blob = data.get(key)
                if not blob:
                    continue
                items = blob if isinstance(blob, list) else [blob]
                for i, item in enumerate(items):
                    text = item if isinstance(item, str) else json.dumps(item, indent=2)
                    records.append({
                        "id": f"{Path(name).stem}:{key}:{i}",
                        "kind": key,
                        "text": text,
                        "author": "assistant-memory",
                    })
    return records


def report(folder: str | Path) -> str:
    """Human-readable inventory of an export folder."""
    lines = []
    for cat, paths in sorted(discover(folder).items()):
        mark = "  EXCLUDED (account PII)" if cat in NEVER_INGEST else ""
        lines.append(f"  {cat}: {len(paths)} shard(s){mark}")
    return "\n".join(lines) or "  (no zips found)"
