"""Every reference can be found: which conversation, when, and a link to open it."""
import json
import re

from magnum_opus import converge as conv, sources, thesis as th
from magnum_opus.agents import Server
from magnum_opus.cli import main
from magnum_opus.distill import Item, Note
from magnum_opus.reindex import read_note
from magnum_opus.vault import Vault

from conftest import FakeClient
from test_converge import _vault
from test_thesis import _reply, _run

ROUTER = "[open](https://claude.ai/chat/a1)"


def test_chat_urls():
    assert sources.chat_url("claude", "4c03bad0-b999") == "https://claude.ai/chat/4c03bad0-b999"
    assert sources.chat_url("chatgpt", "67a1-bc") == "https://chatgpt.com/c/67a1-bc"
    assert sources.chat_url("other", "x") == ""
    assert sources.chat_url("claude", "../../evil?x=1") == ""
    assert sources.chat_url("claude", "") == ""


def test_new_notes_say_where_they_came_from(tmp_path):
    v = Vault(tmp_path)
    path = v.write_note(Note(
        conversation_id="67a1-bc", segment_key="67a1-bc:s:e", title="Orbit social app",
        provider="chatgpt", updated_at="2026-05-02T10:00:00Z", topic="orbit",
        summary="An idea for a social app built around orbits.", start_id="s", end_id="e",
        open_loops=[Item("sketch the feed", "", None)]))
    text = path.read_text()
    assert ("**Source:** the ChatGPT chat “Orbit social app” (2026-05-02) "
            "[open](https://chatgpt.com/c/67a1-bc)") in text
    rec = read_note(path)                           # the source line is not content
    assert rec["summary"] == "An idea for a social app built around orbits."
    assert [i["text"] for i in rec["open_loops"]] == ["sketch the feed"]


def test_status_says_where_each_loop_came_from(tmp_path):
    _vault(tmp_path)
    status = (tmp_path / "projects" / "router" / "STATUS.md").read_text()
    line = next(l for l in status.splitlines() if "rerun the load balancing ablation" in l)
    assert ("_(in [[projects/router/chats/a1--s1|Expert routing collapse]], from the "
            "Claude chat “Expert routing collapse” (2026-07-01) " + ROUTER + ")_") in line


def test_convergence_and_thesis_evidence_can_be_found(tmp_path):
    v = _vault(tmp_path)
    conv.converge(v, conv.TfidfBackend())
    text = (tmp_path / "CONVERGENCE.md").read_text()
    assert ("  - [[projects/router/chats/a1--s1|Expert routing collapse]], from the Claude "
            "chat “Expert routing collapse” (2026-07-01) " + ROUTER) in text
    _run(v, FakeClient(_reply()))
    thesis = (tmp_path / "EMERGENT_THESIS.md").read_text()
    assert ", from the Claude chat “Expert routing collapse” (2026-07-01) " + ROUTER in thesis
    for block in re.findall(r"- evidence:\n((?:  - .*\n)+)", thesis):
        for line in block.splitlines():
            assert "[open](https://claude.ai/chat/" in line


def test_the_model_still_never_sees_dates(tmp_path):
    v = _vault(tmp_path)
    prep = th.prepare(v, th.load_state(v))
    assert "2026" not in prep.prompt and "claude.ai" not in prep.prompt


def test_agents_get_the_link_to_pass_on(tmp_path):
    v = _vault(tmp_path)
    s = Server(v.root)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "search", "arguments": {"query": "tomato"}}})
    text = r["result"]["content"][0]["text"]
    assert "from the Claude chat “Tomato seedlings” (2026-07-01) " \
           "[open](https://claude.ai/chat/c1)" in text


# --- magnum find ------------------------------------------------------------------

def _export(tmp_path):
    """A raw export with a conversation distillation never kept."""
    data = [{"uuid": "0rb17-aaaa", "name": "Late night ideas", "created_at": "2026-02-01T00:00:00Z",
             "updated_at": "2026-02-03T00:00:00Z",
             "chat_messages": [
                 {"uuid": "m1", "sender": "human", "text": "Random thought about lunch."},
                 {"uuid": "m2", "sender": "human",
                  "text": "What if Orbit were a social media app where friends circle "
                          "around shared interests instead of a feed?"}]}]
    p = tmp_path / "conversations.json"
    p.write_text(json.dumps(data))
    return p


def test_find_shows_the_note_and_the_original_chat(tmp_path, capsys):
    _vault(tmp_path / "vault")
    assert main(["find", "tomato", "compost", "--vault", str(tmp_path / "vault")]) == 0
    out = capsys.readouterr().out
    assert "1. Tomato seedlings  [garden]" in out
    assert "note:  projects/garden/chats/c1--s4.md" in out
    assert "from:  Claude chat, 2026-07-01  https://claude.ai/chat/c1" in out


def test_find_searches_the_export_for_what_the_notes_left_out(tmp_path, capsys):
    _vault(tmp_path / "vault")
    export = _export(tmp_path)
    assert main(["find", "orbit", "social", "--vault", str(tmp_path / "vault")]) == 1
    out = capsys.readouterr().out
    assert "Nothing matches" in out and "--export" in out

    assert main(["find", "orbit", "social", "--vault", str(tmp_path / "vault"),
                 "--export", str(export)]) == 0
    out = capsys.readouterr().out
    assert ("1. Claude chat “Late night ideas”, 2026-02-03  "
            "https://claude.ai/chat/0rb17-aaaa") in out
    assert "What if Orbit were a social media app" in out      # the message itself
    assert "lunch" not in out


def test_find_writes_nothing(tmp_path):
    from test_converge import _files
    v = _vault(tmp_path)
    before = _files(v.root)
    main(["find", "router", "--vault", str(v.root)])
    assert _files(v.root) == before
