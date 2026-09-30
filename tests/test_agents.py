"""The agent layer: agents read, may add labelled notes, and never change yours."""
import io
import json
import subprocess
import sys

from magnum_opus import converge as conv, thesis as th
import pytest

from magnum_opus.agents import Server, ToolError
from magnum_opus.reindex import reindex
from magnum_opus.vault import Vault

from test_converge import _files, _vault


def _call(server, method, params=None, mid=1):
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": method,
                          "params": params or {}})


def _tool(server, name, **arguments):
    r = _call(server, "tools/call", {"name": name, "arguments": arguments})["result"]
    return r["content"][0]["text"], r["isError"]


def _init(server, client="claude-desktop", version="2025-06-18"):
    return _call(server, "initialize", {"protocolVersion": version, "capabilities": {},
                                        "clientInfo": {"name": client, "version": "1"}})


# --- the handshake -------------------------------------------------------------------

def test_initialize_and_list_tools(tmp_path):
    s = Server(_vault(tmp_path).root)
    r = _init(s)["result"]
    assert r["protocolVersion"] == "2025-06-18"
    assert r["serverInfo"]["name"] == "magnum-opus"
    assert "read-only" in r["instructions"]
    assert _init(s, version="1999-01-01")["result"]["protocolVersion"] == "2025-06-18"
    names = {t["name"] for t in _call(s, "tools/list")["result"]["tools"]}
    assert names == {"read_queue", "list_projects", "read_status", "list_open_loops",
                     "search", "read_note", "read_guidance"}   # no write_note by default


def test_protocol_edges(tmp_path):
    s = Server(_vault(tmp_path).root)
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert _call(s, "ping")["result"] == {}
    assert _call(s, "resources/list")["error"]["code"] == -32601
    assert _call(s, "tools/call", {"name": "rm_rf"})["error"]["code"] == -32602
    assert s.handle({"id": 1})["error"]["code"] == -32600
    out = io.StringIO()
    s.serve(io.StringIO("not json\n\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n"), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700 and replies[1] == {
        "jsonrpc": "2.0", "id": 7, "result": {}}


# --- reading ------------------------------------------------------------------------

def test_read_tools_answer_and_never_write(tmp_path):
    v = _vault(tmp_path)
    (tmp_path / "ideas.md").write_text("my own file\n")
    before = _files(tmp_path)
    s = Server(v.root)
    _init(s)

    assert "QUEUE" in _tool(s, "read_queue")[0]
    projects = _tool(s, "list_projects")[0]
    assert "- router" in projects and "(2 notes, 1 open loops)" in projects
    assert "Open loops" in _tool(s, "read_status", project="router")[0]
    assert _tool(s, "read_status", project="nope")[1] is True
    loops = _tool(s, "list_open_loops")[0]
    assert "[router] rerun the load balancing ablation" in loops
    assert _tool(s, "list_open_loops", project="garden")[0] == "No open loops."
    hits = _tool(s, "search", query="tomato compost")[0].splitlines()
    assert "Tomato seedlings" in hits[0] and len(hits) == 1
    path = hits[0].split(" | ")[0].removeprefix("- ")
    assert "Watering schedule" in _tool(s, "read_note", path=path)[0]
    assert _tool(s, "read_note", path="ideas")[0] == "my own file\n"

    assert _files(tmp_path) == before                           # not one byte changed


def test_read_note_stays_inside_the_vault(tmp_path):
    v = _vault(tmp_path / "vault")
    (tmp_path / "secret.md").write_text("SECRET-CONTENTS\n")
    (v.root / ".magnum" / "internal.md").write_text("SECRET-CONTENTS\n")
    (v.root / ".obsidian").mkdir()
    (v.root / ".obsidian" / "workspace.md").write_text("SECRET-CONTENTS\n")
    s = Server(v.root)
    for bad in ("../secret.md", str(tmp_path / "secret.md"), ".magnum/state.json",
                ".magnum/internal.md", ".obsidian/workspace", "missing.md"):
        text, err = _tool(s, "read_note", path=bad)
        assert err and "SECRET-CONTENTS" not in text


def test_a_corrupt_index_is_reported_not_moved(tmp_path):
    v = _vault(tmp_path)
    state = tmp_path / ".magnum" / "state.json"
    state.write_text("{broken")
    text, err = _tool(Server(v.root), "list_projects")
    assert err and "magnum reindex" in text
    assert state.read_text() == "{broken"


def test_search_needs_words(tmp_path):
    s = Server(_vault(tmp_path).root)
    assert _tool(s, "search", query="the 2026-07-01")[1] is True


# --- agents add, they never change ------------------------------------------------------

def test_writing_is_off_unless_the_person_allows_it(tmp_path):
    v = _vault(tmp_path)
    before = _files(tmp_path)
    text, err = _tool(Server(v.root), "write_note", title="Plan", body="Do things.")
    assert err and "--allow-write" in text
    with pytest.raises(ToolError):                   # refused by the tool itself too
        Server(v.root).write_note({"title": "Plan", "body": "Do things."})
    assert _files(tmp_path) == before


def test_an_agent_note_is_new_labelled_and_never_overwrites(tmp_path):
    v = _vault(tmp_path)
    before = _files(tmp_path)
    s = Server(v.root, allow_write=True)
    assert "may add notes" in _init(s, client="Claude Desktop")["result"]["instructions"]
    assert "write_note" in {t["name"] for t in _call(s, "tools/list")["result"]["tools"]}

    text, err = _tool(s, "write_note", title="Next step for the router",
                      body="Rerun the ablation with the new loss.", project="router")
    assert not err and "agents/claude-desktop/next-step-for-the-router.md" in text
    note = (tmp_path / "agents" / "claude-desktop" / "next-step-for-the-router.md").read_text()
    assert "author: agent:claude-desktop\n" in note and "about_project: router" in note
    assert "_Written by the agent **claude-desktop**: a suggestion, not your decision." in note

    text, _ = _tool(s, "write_note", title="Next step for the router", body="Again.")
    assert "next-step-for-the-router-2.md" in text
    assert "Rerun the ablation" in (tmp_path / "agents" / "claude-desktop" /
                                    "next-step-for-the-router.md").read_text()

    after = _files(tmp_path)
    assert {p: h for p, h in after.items() if not p.startswith("agents/")} == before

    assert _tool(s, "write_note", title="", body="x")[1] is True
    assert _tool(s, "write_note", title="Big", body="x" * 20_001)[1] is True


def test_the_agent_name_can_be_set_by_the_person(tmp_path):
    s = Server(_vault(tmp_path).root, allow_write=True, agent_name="Research Helper")
    _init(s, client="something-else")
    assert "agents/research-helper/" in _tool(s, "write_note", title="T", body="B")[0]


def test_agent_notes_are_never_evidence(tmp_path):
    v = _vault(tmp_path)
    s = Server(v.root, allow_write=True)
    _init(s)
    _tool(s, "write_note", title="Tomato experts", project="garden",
          body="The garden is really about mixture of experts load balancing.")
    v = Vault(tmp_path)
    v.sync_from_disk()
    assert len(v.state["notes"]) == 4                      # not adopted as a note
    assert all(d.origin == "note" and not d.link.startswith("agents/")
               for d in conv.vault_documents(v))
    assert "Tomato experts" not in th.prepare(v, th.load_state(v)).prompt
    assert reindex(v)["other_files"] == 1                  # counted as not ours


# --- over a real pipe ---------------------------------------------------------------

def test_cli_serve_speaks_only_protocol_on_stdout(tmp_path):
    v = _vault(tmp_path)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "list_projects", "arguments": {}}}]
    p = subprocess.run([sys.executable, "-m", "magnum_opus.cli", "serve", "--vault",
                        str(v.root)], input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    replies = [json.loads(line) for line in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2]
    assert "router" in replies[1]["result"]["content"][0]["text"]
    assert "read-only" in p.stderr


def test_cli_serve_needs_a_vault(tmp_path):
    from magnum_opus.cli import main
    assert main(["serve", "--vault", str(tmp_path / "none")]) == 2
    assert not (tmp_path / "none").exists()
