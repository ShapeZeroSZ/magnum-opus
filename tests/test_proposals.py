"""Open-loop proposals: an agent suggests, you decide, only then a note changes."""
import re

import pytest

from magnum_opus import proposals as props
from magnum_opus.agents import Server, ToolError
from magnum_opus.cli import main
from magnum_opus.vault import Vault

from test_converge import _add, _files


def _vault(tmp_path):
    v = Vault(tmp_path)
    _add(v, "a1:s1:e1", "router", "Expert routing collapse",
         "The router collapses onto one expert.",
         ["rerun the load balancing ablation", "write up the capacity results"])
    _add(v, "b1:s3:e3", "paper", "Draft on sparse experts",
         "Paper section with the ablation as the main figure; the ablation was rerun.")
    _add(v, "c1:s4:e4", "garden", "Tomato seedlings", "Seedlings.", ["buy compost"])
    v.rebuild_rollups()
    v.save()
    return v


def _server(v, write=True):
    s = Server(v.root, allow_write=write)
    s.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "helper", "version": "1"}}})
    return s


def _tool(s, name, **arguments):
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": name, "arguments": arguments}})["result"]
    return r["content"][0]["text"], r["isError"]


def _loop_id(s, text):
    listing = _tool(s, "list_open_loops")[0]
    return re.search(rf"^- (l-[0-9a-f]{{8}}) \[[^\]]*\] {re.escape(text)} ", listing,
                     re.MULTILINE).group(1)


def _note(tmp_path, project="router"):
    return next((tmp_path / "projects" / project / "chats").glob("*.md"))


def _propose(s, text, reason="The ablation was rerun: see the paper draft.", **kw):
    return _tool(s, "propose_close", loop_id=_loop_id(s, text), reason=reason, **kw)


def _tick(tmp_path, pid, box):
    p = tmp_path / "PROPOSALS.md"
    head, _, rest = p.read_text().partition(f"<!--proposal:{pid}-->")
    p.write_text(head + f"<!--proposal:{pid}-->" + rest.replace(f"- [ ] {box}", f"- [x] {box}", 1))


def _untick(tmp_path, pid, box):
    p = tmp_path / "PROPOSALS.md"
    head, _, rest = p.read_text().partition(f"<!--proposal:{pid}-->")
    p.write_text(head + f"<!--proposal:{pid}-->" + rest.replace(f"- [x] {box}", f"- [ ] {box}", 1))


def _pid(text):
    return re.search(r"Proposed (p-[0-9a-f]{6})", text).group(1)


# --- closing a loop changes one checkbox, nothing else ----------------------------------

def test_close_loop_changes_exactly_one_checkbox(tmp_path):
    v = _vault(tmp_path)
    note = _note(tmp_path)
    # The same words in a section of your own, above the loop, must not be touched.
    text = note.read_text().replace("\n## Open loops", "\n## My notes\n"
                                    "- [ ] rerun the load balancing ablation\n\n## Open loops")
    original = "\ufeff" + text.replace("\n", "\r\n")
    note.write_bytes(original.encode("utf-8"))
    v.sync_from_disk()

    assert v.close_loop("a1:s1:e1", "rerun the load balancing ablation")
    after = note.read_bytes().decode("utf-8")
    assert after == original.replace("- [ ] rerun the load balancing ablation  ",
                                     "- [x] rerun the load balancing ablation  ", 1)
    assert "## My notes\r\n- [ ] rerun the load balancing ablation\r\n" in after
    assert not v.close_loop("a1:s1:e1", "rerun the load balancing ablation")   # already
    assert not v.close_loop("a1:s1:e1", "something never written")
    assert note.read_bytes().decode("utf-8") == after


# --- an agent proposes; nothing changes --------------------------------------------------

def test_a_proposal_changes_no_note(tmp_path):
    v = _vault(tmp_path)
    (tmp_path.parent / "outside.md").write_text("x")
    before = _files(tmp_path)
    s = _server(v)
    assert "propose_close" in {t["name"] for t in
                               s.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                               ["result"]["tools"]}
    text, err = _propose(s, "rerun the load balancing ablation",
                         evidence=["projects/paper/chats/b1--s3", "../outside.md", "nope.md"])
    assert not err and "Nothing is closed" in text
    after = _files(tmp_path)
    assert {p for p in after if before.get(p) != after[p]} == {
        "PROPOSALS.md", ".magnum/proposals.json"}

    view = (tmp_path / "PROPOSALS.md").read_text()
    assert "### Close “rerun the load balancing ablation”?" in view
    assert "proposed by **agent:helper**" in view
    assert "- evidence: [[projects/paper/chats/b1--s3]]\n" in view    # only real notes
    assert "The ablation was rerun" in view
    assert "closing it is proposed" in _tool(s, "list_open_loops")[0]


def test_read_only_agents_cannot_propose(tmp_path):
    v = _vault(tmp_path)
    before = _files(tmp_path)
    s = _server(v, write=False)
    loop = _loop_id(s, "buy compost")
    text, err = _tool(s, "propose_close", loop_id=loop, reason="done")
    assert err and "--allow-write" in text
    with pytest.raises(ToolError):                   # refused by the tool itself too
        s.propose_close({"loop_id": loop, "reason": "done"})
    assert _files(tmp_path) == before


def test_bad_proposals_are_refused(tmp_path):
    s = _server(_vault(tmp_path))
    assert _tool(s, "propose_close", loop_id="l-00000000", reason="x")[1]
    assert _propose(s, "buy compost", reason="   ")[1]
    assert not _propose(s, "buy compost")[1]
    text, err = _propose(s, "buy compost")
    assert err and "already proposed" in text


def test_an_agent_cannot_flood_you(tmp_path, monkeypatch):
    monkeypatch.setattr(props, "MAX_PENDING_PER_AGENT", 1)
    s = _server(_vault(tmp_path))
    assert not _propose(s, "buy compost")[1]
    text, err = _propose(s, "write up the capacity results")
    assert err and "waiting for the person" in text


# --- you decide -----------------------------------------------------------------------

def test_accept_then_run_closes_the_loop_in_the_note(tmp_path, capsys):
    v = _vault(tmp_path)
    s = _server(v)
    pid = _pid(_propose(s, "rerun the load balancing ablation")[0])
    note = _note(tmp_path)
    before = note.read_text()
    _tick(tmp_path, pid, "accept")
    assert note.read_text() == before              # ticking alone changes no note

    assert main(["proposals", "--vault", str(tmp_path)]) == 0
    assert f"{pid}: closed “rerun the load balancing ablation”" in capsys.readouterr().out
    assert note.read_text() == before.replace("- [ ] rerun the load balancing ablation",
                                              "- [x] rerun the load balancing ablation")
    status = (tmp_path / "projects" / "router" / "STATUS.md").read_text()
    assert "rerun the load balancing ablation" not in status
    assert "write up the capacity results" in status
    p = props.load_state(v)["proposals"][pid]
    assert p["status"] == "applied" and p["decided_by"] == "human"
    assert p["by"] == "agent:helper"
    assert f"<!--proposal:{pid}-->" not in (tmp_path / "PROPOSALS.md").read_text()


def test_reject_keeps_it_open_for_good_until_you_untick(tmp_path):
    v = _vault(tmp_path)
    s = _server(v)
    pid = _pid(_propose(s, "buy compost")[0])
    note = _note(tmp_path, "garden")
    before = note.read_text()
    _tick(tmp_path, pid, "reject")
    assert main(["proposals", "--vault", str(tmp_path)]) == 0
    assert note.read_text() == before
    assert "the person rejected closing it" in _tool(s, "list_open_loops")[0]
    text, err = _propose(s, "buy compost")
    assert err and "rejected" in text
    view = (tmp_path / "PROPOSALS.md").read_text()
    assert "## Rejected by you" in view and view.count(f"<!--proposal:{pid}-->") == 1

    _untick(tmp_path, pid, "reject")
    main(["proposals", "--vault", str(tmp_path)])
    assert props.load_state(v)["proposals"][pid]["status"] == "proposed"


def test_your_ticks_survive_an_agent_proposing_more(tmp_path):
    v = _vault(tmp_path)
    s = _server(v)
    pid = _pid(_propose(s, "buy compost")[0])
    _tick(tmp_path, pid, "accept")
    _propose(s, "write up the capacity results")        # rewrites PROPOSALS.md
    view = (tmp_path / "PROPOSALS.md").read_text()
    assert "## Accepted, not yet carried out" in view
    assert props.load_state(v)["proposals"][pid]["status"] == "accepted"
    main(["proposals", "--vault", str(tmp_path)])
    assert "- [x] buy compost" in _note(tmp_path, "garden").read_text()


def test_both_boxes_ticked_changes_nothing(tmp_path, capsys):
    v = _vault(tmp_path)
    pid = _pid(_propose(_server(v), "buy compost")[0])
    _tick(tmp_path, pid, "accept")
    _tick(tmp_path, pid, "reject")
    main(["proposals", "--vault", str(tmp_path)])
    assert "both accept and reject ticked" in capsys.readouterr().out
    assert "- [ ] buy compost" in _note(tmp_path, "garden").read_text()


def test_ticking_the_loop_yourself_resolves_the_proposal(tmp_path):
    v = _vault(tmp_path)
    pid = _pid(_propose(_server(v), "buy compost")[0])
    note = _note(tmp_path, "garden")
    note.write_text(note.read_text().replace("- [ ] buy compost", "- [x] buy compost"))
    main(["proposals", "--vault", str(tmp_path)])
    assert props.load_state(v)["proposals"][pid]["status"] == "resolved"
    assert f"<!--proposal:{pid}-->" not in (tmp_path / "PROPOSALS.md").read_text()


def test_an_accepted_loop_you_since_edited_is_left_alone(tmp_path):
    v = _vault(tmp_path)
    pid = _pid(_propose(_server(v), "buy compost")[0])
    _tick(tmp_path, pid, "accept")
    note = _note(tmp_path, "garden")
    note.write_text(note.read_text().replace("- [ ] buy compost", "- [ ] buy peat-free compost"))
    edited = note.read_text()
    main(["proposals", "--vault", str(tmp_path)])
    assert note.read_text() == edited
    assert props.load_state(v)["proposals"][pid]["status"] == "resolved"


def test_proposals_command_with_nothing_to_do(tmp_path, capsys):
    _vault(tmp_path)
    assert main(["proposals", "--vault", str(tmp_path)]) == 0
    assert "No proposals yet" in capsys.readouterr().out
    assert not (tmp_path / "PROPOSALS.md").exists()
