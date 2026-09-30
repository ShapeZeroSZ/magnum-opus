"""The agent layer: an MCP server over the vault.

`magnum serve --vault ~/vault` lets an AI assistant that speaks the Model
Context Protocol (Claude Desktop, Claude Code, and others) read the vault:
the queue, projects, their status, notes, open loops, and a search over them.

It is built so that you always know what an agent did and what you did:

- **Read-only unless you say otherwise.** Without `--allow-write` the server
  cannot change a single file. Reading never writes, not even the index.
- **Agents add, they never change.** With `--allow-write`, an agent can add
  a note of its own in `agents/<name>/`. It cannot edit, move or delete any
  note, and it never overwrites a file.
- **Agents propose, you decide.** With `--allow-write`, an agent can also
  propose closing one of your open loops. The proposal goes to PROPOSALS.md;
  the loop closes only if you accept and run `magnum proposals`.
- **Labelled as the agent's.** Every note an agent adds says so, in its
  properties (`author: agent:<name>`) and in its first line, so a suggestion
  is never mistaken for a decision of yours. It is a suggestion until you act
  on it; to make it yours, edit it, move it, or change its `author`.
- **Never evidence.** Agent notes are not Magnum Opus notes: sort, converge
  and thesis do not read them, so an agent's guess cannot become the basis of
  a relationship or a claim about your work.
- **No spending.** Nothing here calls a paid API; `ingest`, `sort` and
  `thesis` stay with you, where the cost is shown first.

The protocol is JSON-RPC 2.0 over stdio, one message per line. It uses only
the standard library.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, guidance, proposals as props, sources
from .converge import UNPLACED, terms
from .vault import Vault, _done, slugify

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY = 20_000
MAX_READ = 200_000

TOOLS = [
    {"name": "read_queue",
     "description": "The person's queue: at most three projects most worth their "
                    "attention next, with the next step for each.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_projects",
     "description": "Every project in the vault, in priority order, with its "
                    "description, status, number of notes and open loops.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "read_status",
     "description": "A project's STATUS.md: its open loops and recent decisions.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string",
                                                "description": "Project slug"}},
                     "required": ["project"]}},
    {"name": "list_open_loops",
     "description": "Open loops (unfinished items) across the vault or in one "
                    "project, each with its id and the note it comes from, and "
                    "whether closing it was already proposed or rejected.",
     "inputSchema": {"type": "object",
                     "properties": {"project": {"type": "string"},
                                    "limit": {"type": "integer", "minimum": 1,
                                              "maximum": 200}}}},
    {"name": "search",
     "description": "Find notes by content. Returns path, title, project, summary "
                    "and the original conversation (with a link) for the best "
                    "matches. When you mention a note to the person, include that "
                    "link so they can find it.",
     "inputSchema": {"type": "object",
                     "properties": {"query": {"type": "string"},
                                    "limit": {"type": "integer", "minimum": 1,
                                              "maximum": 50}},
                     "required": ["query"]}},
    {"name": "read_guidance",
     "description": "The person's standing instructions (GUIDANCE.md): how they "
                    "want their work handled. Follow them.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "read_note",
     "description": "The full text of one markdown file in the vault, by its "
                    "path relative to the vault (as returned by search).",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string"}},
                     "required": ["path"]}},
]

WRITE_TOOL = {
    "name": "write_note",
    "description": "Add a new note of your own to the vault, in agents/<your name>/. "
                   "It is labelled as written by you, an agent, and is a suggestion "
                   "for the person, not a decision. It cannot change, move or "
                   "delete any existing note.",
    "inputSchema": {"type": "object",
                    "properties": {"title": {"type": "string"},
                                   "body": {"type": "string",
                                            "description": "Markdown"},
                                   "project": {"type": "string",
                                               "description": "Optional: the project "
                                                              "slug this is about"}},
                    "required": ["title", "body"]},
}


PROPOSE_TOOL = {
    "name": "propose_close",
    "description": "Propose to the person that one of their open loops is finished "
                   "or no longer needed. It does not close anything: the proposal "
                   "waits in PROPOSALS.md until the person accepts or rejects it. "
                   "Give a specific reason and, if you can, the notes that show it.",
    "inputSchema": {"type": "object",
                    "properties": {"loop_id": {"type": "string",
                                               "description": "From list_open_loops"},
                                   "reason": {"type": "string"},
                                   "evidence": {"type": "array",
                                                "items": {"type": "string"},
                                                "description": "Note paths, as returned "
                                                               "by search"}},
                    "required": ["loop_id", "reason"]},
}


class ToolError(Exception):
    """A problem to report to the agent as a tool result, not a crash."""


class Server:
    def __init__(self, vault_root, allow_write: bool = False, agent_name: str | None = None):
        self.root = Path(vault_root).resolve()
        self.allow_write = allow_write
        self.agent_name = slugify(agent_name) if agent_name else None

    # --- the vault, fresh from disk on every call -------------------------------

    def _vault(self) -> Vault:
        if not (self.root / ".magnum").is_dir():
            raise ToolError(f"No Magnum Opus vault at {self.root}.")
        for f in (self.root / ".magnum" / "state.json", self.root / "config.json"):
            try:
                if f.exists():
                    json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # Opening would set the corrupt file aside, a write; leave it
                # for the person to repair.
                raise ToolError(f"{f.name} is unreadable. Ask the person to run "
                                "`magnum reindex`.")
        v = Vault(self.root)
        v.sync_from_disk()                 # in memory only; nothing is written
        return v

    def _projects(self, v) -> list:
        meta = v.config.get("project_meta", {})
        counts = {}
        for n in v.state["notes"]:
            p = slugify(n.get("project", ""))
            c = counts.setdefault(p, [0, 0])
            c[0] += 1
            c[1] += sum(1 for l in n.get("open_loops", []) if not _done(l))
        return sorted(counts.items(), key=lambda kv: (meta.get(kv[0], {}).get("rank", 10 ** 6),
                                                       kv[0]))

    def _path_of(self, v, rec) -> str:
        return v.state["segments"].get(rec["segment_key"], {}).get("path") or ""

    # --- tools ------------------------------------------------------------------

    def read_queue(self, args):
        self._vault()
        q = self.root / "QUEUE.md"
        return q.read_text(encoding="utf-8") if q.exists() else \
            "No queue yet: the person has not run `magnum ingest`."

    def list_projects(self, args):
        v = self._vault()
        meta = v.config.get("project_meta", {})
        lines = []
        for slug, (n_notes, n_loops) in self._projects(v):
            m = meta.get(slug, {})
            desc = m.get("description", "")
            status = m.get("status", "")
            lines.append(f"- {slug}" + (f": {desc}" if desc else "")
                         + (f" [{status}]" if status else "")
                         + f" ({n_notes} notes, {n_loops} open loops)")
        return "\n".join(lines) or "No notes yet."

    def read_status(self, args):
        v = self._vault()
        slug = slugify(str(args.get("project", "")))
        known = {p for p, _ in self._projects(v)}
        if slug not in known:
            raise ToolError(f"No project {slug!r}. Known: {', '.join(sorted(known))}")
        p = self.root / "projects" / slug / "STATUS.md"
        if not p.exists():
            raise ToolError(f"{slug} has no STATUS.md (unsorted notes have none).")
        return p.read_text(encoding="utf-8")

    def list_open_loops(self, args):
        v = self._vault()
        want = slugify(args["project"]) if args.get("project") else None
        limit = int(args.get("limit") or 50)
        status = props.status_of(props.load_state(v))
        note = {"proposed": "; closing it is proposed",
                "accepted": "; the person accepted closing it",
                "rejected": "; the person rejected closing it"}
        out = []
        for loop in props.open_loops(v):
            if want and loop["project"] != want:
                continue
            out.append(f"- {loop['id']} [{loop['project']}] {loop['text']}  "
                       f"(from {loop['path']}{note.get(status.get(loop['id']), '')})")
        more = len(out) - limit
        return ("\n".join(out[:limit]) + (f"\n... and {more} more" if more > 0 else "")) \
            or "No open loops."

    def search(self, args):
        v = self._vault()
        if not terms(str(args.get("query", ""))):
            raise ToolError("The query has no searchable words.")
        lines = []
        for _, rec in sources.search_notes(v, str(args["query"]), int(args.get("limit") or 10)):
            project = rec.get("project", "")
            lines.append(f"- {self._path_of(v, rec)} | {rec.get('title', '')} | "
                         f"{project if project not in UNPLACED else 'unsorted'} | "
                         + re.sub(r"\s+", " ", rec.get("summary", ""))[:240]
                         + f" | from {sources.origin(rec)}")
        return "\n".join(lines) or "No notes match."

    def read_guidance(self, args):
        return guidance.read(self._vault()) or "The person has written no guidance yet."

    def read_note(self, args):
        self._vault()
        rel = str(args.get("path", "")).strip()
        if not rel.endswith(".md"):
            rel += ".md"
        path = (self.root / rel).resolve()
        try:
            inside = path.relative_to(self.root)
        except ValueError:
            raise ToolError("That path is outside the vault.")
        if ".magnum" in inside.parts or ".obsidian" in inside.parts:
            raise ToolError("That path is not a note.")
        if not path.is_file():
            raise ToolError(f"No file {inside.as_posix()}.")
        return path.read_text(encoding="utf-8")[:MAX_READ]

    def write_note(self, args):
        if not self.allow_write:
            raise ToolError("This vault is read-only for agents. The person can allow "
                            "notes with `magnum serve --allow-write`.")
        self._vault()
        title = re.sub(r"\s+", " ", str(args.get("title", ""))).strip()[:120]
        body = str(args.get("body", ""))
        if not title or not body.strip():
            raise ToolError("A note needs a title and a body.")
        if len(body) > MAX_BODY:
            raise ToolError(f"The body is longer than {MAX_BODY:,} characters.")
        project = slugify(args["project"]) if args.get("project") else ""
        name = self.agent_name or "agent"
        folder = self.root / "agents" / name
        folder.mkdir(parents=True, exist_ok=True)
        stem = slugify(title)[:80]
        path, k = folder / f"{stem}.md", 2
        while path.exists():                              # never overwrite anything
            path, k = folder / f"{stem}-{k}.md", k + 1
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        front = ["---", f"author: agent:{name}", f"title: {json.dumps(title)}"]
        if project:
            front.append(f"about_project: {project}")
        front += [f"created: {now}", "---", ""]
        text = ("\n".join(front)
                + f"_Written by the agent **{name}**: a suggestion, not your decision. "
                  "Edit it, move it or change its `author` to make it yours; "
                  "delete it if it is wrong._\n\n"
                + f"# {title}\n\n" + body.rstrip() + "\n")
        # "x": fail rather than replace, even if a file appears in between.
        with open(path, "x", encoding="utf-8", newline="") as fh:
            fh.write(text)
        return f"Added {path.relative_to(self.root).as_posix()} (labelled as written by {name})."

    def propose_close(self, args):
        if not self.allow_write:
            raise ToolError("This vault is read-only for agents. The person can allow "
                            "proposals with `magnum serve --allow-write`.")
        v = self._vault()
        state = props.load_state(v)
        props.record_ticks(v, state)          # the person's ticks first, never lost
        try:
            pid = props.propose(v, state, args.get("loop_id", ""), args.get("reason", ""),
                                args.get("evidence") or [], self.agent_name or "agent")
        except props.ProposalError as e:
            raise ToolError(str(e))
        props.refresh(v, state)
        return (f"Proposed {pid}. Nothing is closed: the person decides in PROPOSALS.md.")

    # --- protocol ---------------------------------------------------------------

    def _guidance_note(self) -> str:
        try:
            text = guidance.read(self._vault())
        except ToolError:
            return ""
        return (f"\n\nThe person's standing guidance (GUIDANCE.md), to follow:\n{text}"
                if text else "")

    def tools(self) -> list:
        return TOOLS + ([WRITE_TOOL, PROPOSE_TOOL] if self.allow_write else [])

    def handle(self, msg):
        """One JSON-RPC message in, one response out (None for notifications)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None,
                          -32600, "Invalid request")
        method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
        if "id" not in msg:
            return None                                   # notification: no reply
        if method == "initialize":
            client = (params.get("clientInfo") or {}).get("name")
            if not self.agent_name:
                self.agent_name = slugify(client) if client else "agent"
            asked = params.get("protocolVersion")
            return _result(mid, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "magnum-opus", "version": __version__},
                "instructions": (
                    "This is a person's Magnum Opus vault: notes distilled from their AI "
                    "conversations, grouped into projects. Read it to help them finish "
                    "their work. "
                    + ("You may add notes of your own with write_note; they are labelled "
                       "as yours and are suggestions until the person acts on them. You "
                       "may propose closing an open loop with propose_close; the person "
                       "decides. "
                       if self.allow_write else "The vault is read-only for you. ")
                    + "You cannot change the person's notes."
                    + self._guidance_note())})
        if method == "ping":
            return _result(mid, {})
        if method == "tools/list":
            return _result(mid, {"tools": self.tools()})
        if method == "tools/call":
            name = params.get("name")
            if name not in {t["name"] for t in self.tools()}:
                if name in ("write_note", "propose_close"):
                    return _tool(mid, "This vault is read-only for agents. The person "
                                      "can allow notes with `magnum serve --allow-write`.",
                                 error=True)
                return _error(mid, -32602, f"Unknown tool: {name}")
            try:
                return _tool(mid, getattr(self, name)(params.get("arguments") or {}))
            except ToolError as e:
                return _tool(mid, str(e), error=True)
            except (KeyError, TypeError, ValueError) as e:
                return _tool(mid, f"Bad arguments: {e}", error=True)
        return _error(mid, -32601, f"Method not found: {method}")

    def serve(self, stdin=None, stdout=None):
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                reply = _error(None, -32700, "Parse error")
            else:
                reply = self.handle(msg)
            if reply is not None:
                stdout.write(json.dumps(reply) + "\n")
                stdout.flush()


def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _tool(mid, text, error=False):
    return _result(mid, {"content": [{"type": "text", "text": text}], "isError": error})
