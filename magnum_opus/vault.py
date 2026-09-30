"""Vault writer: distilled Notes into an Obsidian-compatible vault.

Layout:
  vault/
    .magnum/state.json              processed segments + last message seen
    config.json                     known projects, slug registry
    QUEUE.md                        <= 3 items (default view)
    PRIORITIES.md                   complete ordered list
    projects/<slug>/STATUS.md       rolling per-project state
    projects/<slug>/chats/*.md      distilled notes (one per segment)
    unsorted/*.md                   notes not yet placed by `magnum sort`

Incremental state is tracked at **message** level, not conversation level.
These threads are living documents; keying on a conversation timestamp would
re-distill four megabytes to capture twenty new messages.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SAFE = re.compile(r"[^a-z0-9\-]+")


def slugify(s: str) -> str:
    return SAFE.sub("-", s.lower().strip()).strip("-") or "inbox"


class Vault:
    def __init__(self, root):
        self.root = Path(root)
        self.state_path = self.root / ".magnum" / "state.json"
        self.config_path = self.root / "config.json"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".magnum").mkdir(exist_ok=True)
        self.state = self._load(self.state_path,
                                {"segments": {}, "last_message": {}, "notes": []})
        self.config = self._load(self.config_path, {"projects": [], "slug_aliases": {}})
        self.state.setdefault("segments", {})
        self.state.setdefault("last_message", {})
        self.state.setdefault("notes", [])
        self.config.setdefault("projects", [])
        self.config.setdefault("slug_aliases", {})

    @staticmethod
    def _load(path: Path, default: dict) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                salvage = path.with_suffix(".corrupt")
                path.replace(salvage)
                raise RuntimeError(
                    f"{path.name} is corrupt (moved to {salvage.name}). "
                    "Your notes on disk are unaffected -- run `magnum reindex` "
                    "to rebuild the index from them before ingesting again."
                )
        return default

    @staticmethod
    def _atomic_write(path, text: str):
        """Write via a temp file + rename, so an interrupted write cannot
        truncate or corrupt the existing file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def save(self):
        self._atomic_write(self.state_path, json.dumps(self.state, indent=2))
        self._atomic_write(self.config_path, json.dumps(self.config, indent=2))

    # ------------------------------------------------------------- state ----

    def is_processed(self, segment_key: str) -> bool:
        return segment_key in self.state["segments"]

    def last_message(self, conversation_id: str) -> str:
        return self.state["last_message"].get(conversation_id, "")

    def note_last_message(self, conversation_id: str, message_id: str):
        self.state["last_message"][conversation_id] = message_id

    def known_projects(self) -> list:
        return list(self.config.get("projects", []))

    # ------------------------------------------------- slug registry ----

    def canonical_slug(self, name: str) -> str:
        """Resolve a project name to a canonical slug.

        slugify() collapses any non-alphanumeric run to a hyphen, so distinct
        names ("Shape/Zero", "shape zero") can land on the same slug. The
        registry records which original names map to a slug so a genuine
        collision is visible rather than a silent merge.
        """
        slug = slugify(name)
        aliases = self.config["slug_aliases"]
        known = aliases.setdefault(slug, [])
        if name not in known:
            known.append(name)
        return slug

    def collisions(self) -> dict:
        return {s: n for s, n in self.config["slug_aliases"].items() if len(n) > 1}

    # ------------------------------------------------------------- write ----

    def write_note(self, note) -> Path:
        project = self.canonical_slug(note.project)
        if project in ("inbox", "unsorted"):
            note_dir = self.root / project
        else:
            note_dir = self.root / "projects" / project / "chats"
            if project not in self.config["projects"]:
                self.config["projects"].append(project)
        note_dir.mkdir(parents=True, exist_ok=True)

        stem = f"{note.conversation_id}--{note.start_id[:8]}"
        path = note_dir / f"{stem}.md"
        path.write_text(self._render(note, project), encoding="utf-8")
        # A note that moved (re-sorted into another project) must not leave its
        # old file behind: two copies of one note would both be read back by
        # reindex, and the stale one could win.
        prev = self.state["segments"].get(note.segment_key, {}).get("path")
        if prev and (self.root / prev) != path and (self.root / prev).exists():
            (self.root / prev).unlink()

        self.state["segments"][note.segment_key] = {
            "path": str(path.relative_to(self.root)), "project": project,
        }
        d = note.to_dict()
        self.state["notes"] = [n for n in self.state["notes"]
                               if n["segment_key"] != note.segment_key] + [d]
        return path

    @staticmethod
    def _item_line(item) -> str:
        if isinstance(item, dict):
            text, loc = item.get("text", ""), item.get("locator") or {}
        else:
            text = item.text
            loc = item.locator.to_dict() if item.locator else {}
        anchor = (item.get("anchor", "") if isinstance(item, dict)
                  else getattr(item, "anchor", ""))
        meta = f'<!--anchor:{anchor}-->' if anchor else ""
        if not loc:
            return f"- {text}  `no locator`{meta}"
        mark = ("" if loc.get("verified")
                else (" ambiguous" if loc.get("ambiguous") else " unverified"))
        # Full ids, not truncated: the markdown is the record, so it must carry
        # everything needed to rebuild the index or verify a citation.
        return (f"- {text}  "
                f"`{loc.get('conversation_id','')}#{loc.get('message_id','')}"
                f" {loc.get('role','')}{mark}`{meta}")

    def _render(self, note, project: str) -> str:
        def section(title, items):
            if not items:
                return ""
            body = "\n".join(self._item_line(i) for i in items)
            return f"\n## {title}\n{body}\n"

        links = " ".join(f"[[{slugify(l)}]]" for l in note.links)
        head = note.label or note.title
        # Free text goes through json.dumps: a JSON string is always a valid
        # YAML double-quoted scalar, so quotes, backslashes and newlines in a
        # chat title can never break the frontmatter for Obsidian or any
        # other YAML reader.
        q = json.dumps
        return (
            "---\n"
            f"title: {q(note.title)}\n"
            f"segment: {q(note.label or '')}\n"
            f"conversation_id: {note.conversation_id}\n"
            f"start_message: {note.start_id}\n"
            f"end_message: {note.end_id}\n"
            f"provider: {note.provider}\n"
            f"updated: {note.updated_at}\n"
            f"project: {project}\n"
            f"topic: {q(getattr(note, 'topic', '') or '')}\n"
            f"author: {note.author}\n"
            'spec: "0.2"\n'
            "---\n\n"
            f"# {head}\n\n"
            f"{note.summary}\n"
            + section("Decisions", note.decisions)
            + section("Ideas", note.ideas)
            + section("Open loops", note.open_loops)
            + (f"\n**Touches:** {links}\n" if links else "")
        )

    # ---------------------------------------------------------- rollups ----

    def rebuild_rollups(self):
        by_project = {}
        for n in self.state["notes"]:
            by_project.setdefault(slugify(n["project"]), []).append(n)
        for project, notes in by_project.items():
            if project not in ("inbox", "unsorted"):
                notes.sort(key=lambda n: n["updated_at"], reverse=True)
                self._write_status(project, notes)
        self._write_queue(by_project)

    @staticmethod
    def _text(i):
        return i.get("text", "") if isinstance(i, dict) else str(i)

    def _write_status(self, project, notes):
        pdir = self.root / "projects" / project
        pdir.mkdir(parents=True, exist_ok=True)
        loops = [(self._item_line(l), n["title"]) for n in notes for l in n["open_loops"]]
        decisions = [(self._item_line(d), n["updated_at"][:10])
                     for n in notes for d in n["decisions"]]
        lines = [f"# {project} — STATUS",
                 f"\n_Updated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
                 f"{len(notes)} distilled segments_\n"]
        if loops:
            lines.append("## Open loops")
            lines += [f"{l}  _(from: {t})_" for l, t in loops[:30]]
        if decisions:
            lines.append("\n## Recent decisions")
            lines += [f"{d} _({when})_" for d, when in decisions[:20]]
        (pdir / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_queue(self, by_project):
        """Rank by the sort pass's judgment; fall back to loop count if unsorted."""
        meta = self.config.get("project_meta", {})
        ranked = []
        for project, notes in by_project.items():
            if project in ("inbox", "unsorted"):
                continue
            m = meta.get(project, {})
            if m.get("status") == "done":
                continue
            loops = [self._text(l) for n in notes for l in n["open_loops"]]
            nxt = m.get("next_action") or (loops[0] if loops else "")
            if not nxt:
                continue
            rank = m.get("rank", 10_000 + len(loops))
            ranked.append((rank, project, nxt, m.get("rank_reason", ""),
                           m.get("status", ""), len(loops)))
        ranked.sort()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        judged = bool(meta)

        lines = ["# QUEUE — what's most worth your attention",
                 f"\n_Generated {now}. The only file you need to open._\n"]
        for i, (_, project, nxt, why, status, nloops) in enumerate(ranked[:3], 1):
            tag = f" · {status}" if status else ""
            lines.append(f"{i}. **[[projects/{project}/STATUS|{project}]]**"
                         f" ({nloops} open loops{tag})\n"
                         f"   next: {nxt}" + (f"\n   why: {why}" if why else ""))
        if not ranked:
            lines.append("_Nothing ranked yet. Run `magnum ingest`, then `magnum sort`._")
        if not judged and ranked:
            lines.append("\n_Ordering is provisional (loop count). "
                         "Run `magnum sort` for a judged ranking._")
        hidden = max(0, len(ranked) - 3)
        if hidden:
            lines.append(f"\n_{hidden} other projects intentionally hidden — "
                         f"see [[PRIORITIES]] for the full ordered list._")
        (self.root / "QUEUE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        plines = ["# PRIORITIES — the whole picture, in order",
                  f"\n_Generated {now} · ranked by the sort pass. "
                  "Open by choice; QUEUE.md is the default view._\n"]
        for i, (_, project, nxt, why, status, nloops) in enumerate(ranked, 1):
            tag = f" · {status}" if status else ""
            plines.append(f"{i}. **[[projects/{project}/STATUS|{project}]]**"
                          f" ({nloops} open loops{tag})\n"
                          f"   next: {nxt}" + (f"\n   why: {why}" if why else ""))
        if not ranked:
            plines.append("_Nothing ranked yet._")
        (self.root / "PRIORITIES.md").write_text("\n".join(plines) + "\n", encoding="utf-8")
