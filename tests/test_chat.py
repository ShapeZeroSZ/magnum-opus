"""The chat: it reads freely, every change asks first, it never runs a full pass."""
import json
import re

import pytest

from magnum_opus import brief, cli, guidance, proposals as props, sort as sortmod, thesis as th
from magnum_opus.agents import Server
from magnum_opus.chat import MAX_STEPS, BudgetReached, Chat
from magnum_opus.cli import main
from magnum_opus.llm import Response, TextBlock
from magnum_opus.vault import Vault

from test_converge import _files
from test_proposals import _note, _vault


class Model:
    """A scripted model: each reply is a string or a function of the last message."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.messages = self

    def create(self, model, max_tokens, messages, system="", **_):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        text = r(messages[-1]["content"]) if callable(r) else r
        return Response(content=[TextBlock(text)], stop_reason="end_turn")


def tool(name, **args):
    return f"<tool>{json.dumps({'name': name, 'args': args})}</tool>"


def _chat(v, model, answers=(), **kw):
    asked, shown = [], []
    answers = list(answers)

    def confirm(q):
        asked.append(q)
        return answers.pop(0) if answers else False
    c = Chat(v.root, model, "m", confirm, show=shown.append, **kw)
    return c, asked, shown


def _loop(v, text):
    v.sync_from_disk()
    return next(l["id"] for l in props.open_loops(v) if l["text"] == text)


# --- reading ------------------------------------------------------------------------

def test_it_looks_before_answering_and_passes_links_on(tmp_path):
    v = _vault(tmp_path)
    before = _files(tmp_path)
    model = Model(tool("search", query="compost"),
                  lambda result: "It is in the garden project: " +
                  re.search(r"\[open\]\([^)]+\)", result).group(0))
    c, asked, _ = _chat(v, model)
    reply = c.turn("Where did I mention compost?")
    assert reply == "It is in the garden project: [open](https://claude.ai/chat/c1)"
    assert asked == []                                         # reading asks nothing
    after = _files(tmp_path)
    assert {p for p in after if before.get(p) != after[p]} <= {
        p for p in after if p.startswith(".magnum/chats/")}    # only the transcript
    assert "tomato" in model.calls[1]["messages"][-1]["content"].lower()


def test_the_model_knows_the_rules_projects_and_guidance(tmp_path):
    v = _vault(tmp_path)
    guidance.append(v, "Router and paper are one effort.")
    model = Model("ok")
    c, _, _ = _chat(v, model)
    c.turn("hi")
    system = model.calls[0]["system"]
    assert "Never start a full pass" in system
    assert "Router and paper are one effort." in system
    assert "- router" in system and "- garden" in system


# --- every change asks first ----------------------------------------------------------

def test_closing_a_loop_asks_and_only_yes_closes_it(tmp_path):
    v = _vault(tmp_path)
    lid = _loop(v, "buy compost")
    note = _note(tmp_path, "garden")
    before = note.read_text()

    c, asked, _ = _chat(v, Model(tool("close_loop", loop_id=lid, reason="bought it"), "ok"),
                        answers=[False])
    c.turn("I bought the compost")
    assert asked == ["Close “buy compost” [garden]?\n  why: bought it"]
    assert note.read_text() == before

    c, _, _ = _chat(v, Model(tool("close_loop", loop_id=lid, reason="bought it"), "done"),
                    answers=[True])
    c.turn("I bought the compost, close it")
    assert note.read_text() == before.replace("- [ ] buy compost", "- [x] buy compost")
    assert "buy compost" not in (tmp_path / "QUEUE.md").read_text()
    rec = next(p for p in props.load_state(v)["proposals"].values() if p["loop"] == "buy compost")
    assert rec["by"] == "agent:chat" and rec["decided_by"] == "human"
    assert rec["status"] == "applied"


def test_moving_a_note_asks_and_marks_it_as_yours(tmp_path):
    v = _vault(tmp_path)
    model = Model(tool("move_note", path="projects/garden/chats/c1--s4", project="router"), "ok")
    c, asked, _ = _chat(v, model, answers=[True])
    c.turn("That tomato note belongs with router")
    assert asked == ["Move “Tomato seedlings” from garden to router?"]
    moved = tmp_path / "projects" / "router" / "chats" / "c1--s4.md"
    assert "project_set_by: human" in moved.read_text()


def test_reordering_asks_and_updates_the_queue(tmp_path):
    v = _vault(tmp_path)
    c, asked, _ = _chat(v, Model(tool("reorder", order=["garden"]), "ok"), answers=[True])
    c.turn("Garden first")
    assert asked[0].startswith("Set the priority order to:\n  1. garden")
    queue = (tmp_path / "QUEUE.md").read_text()
    assert queue.index("garden") < queue.index("router")
    assert Vault(tmp_path).config["project_meta"]["garden"]["rank_reason"] == "you set this order"


def test_remember_asks_then_every_step_reads_it(tmp_path):
    v = _vault(tmp_path)
    c, asked, _ = _chat(v, Model(tool("remember", text="Leave the garden out of the thesis."),
                                 "ok"), answers=[True])
    c.turn("Remember: garden is personal")
    assert asked == ["Add to GUIDANCE.md: “Leave the garden out of the thesis.”?"]
    assert "- Leave the garden out of the thesis. _(" in (tmp_path / "GUIDANCE.md").read_text()

    prompt = th.prepare(v, th.load_state(v)).prompt           # the thesis follows it...
    assert "Leave the garden out of the thesis." in prompt
    assert not re.search(r"\d{4}-\d{2}-\d{2}", prompt)          # ...without its date

    seen = []
    fake = Model(lambda p: seen.append(p) or json.dumps(
        {"projects": [{"slug": "a"}], "priority_order": ["a"]}))
    sortmod.propose_taxonomy([], fake, "m", progress=lambda *_: None,
                             guidance=guidance.section(v))
    assert "Leave the garden out of the thesis." in seen[0]    # ...and so does sort

    s = Server(v.root)
    init = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert "Leave the garden out" in init["result"]["instructions"]  # ...and agents


def test_guidance_append_keeps_what_you_wrote(tmp_path):
    v = Vault(tmp_path)
    mine = "# My rules\r\n\r\nNo work on Sundays.\r\n"
    (tmp_path / "GUIDANCE.md").write_bytes(mine.encode())
    guidance.append(v, "Paper first.")
    text = (tmp_path / "GUIDANCE.md").read_bytes().decode()
    assert text.startswith(mine) and "\r\n## Remembered from chat\r\n- Paper first. _(" in text
    with pytest.raises(ValueError):
        guidance.append(v, "   ")


# --- it never runs a full pass ----------------------------------------------------------

def test_a_full_pass_is_only_ever_suggested(tmp_path):
    v = _vault(tmp_path)
    model = Model(tool("suggest_pass", kind="thesis", reason="a new model is out"), "ok")
    c, _, shown = _chat(v, model)
    c.turn("A new model dropped, reassess everything")
    assert shown == [f"[suggested, not run] magnum thesis --vault {v.root}\n"
                     "  why: a new model is out"]
    assert not (tmp_path / "EMERGENT_THESIS.md").exists()
    assert "runs only if they run it themselves" in model.calls[1]["messages"][-1]["content"]


def test_unknown_and_broken_tool_calls_are_reported_not_run(tmp_path):
    v = _vault(tmp_path)
    model = Model(tool("sort_everything"), "<tool>{not json</tool>", "fine")
    c, _, _ = _chat(v, model)
    assert c.turn("go") == "fine"
    results = [m["content"] for m in model.calls[2]["messages"] if m["role"] == "user"]
    assert "there is no tool 'sort_everything'" in results[1]
    assert "not valid JSON" in results[2]


def test_it_stops_after_too_many_steps(tmp_path):
    v = _vault(tmp_path)
    model = Model(tool("read_queue"))
    c, _, _ = _chat(v, model)
    assert "too many steps" in c.turn("loop forever")
    assert len(model.calls) == MAX_STEPS


# --- cost -----------------------------------------------------------------------------

def test_spending_stops_before_the_limit(tmp_path):
    v = _vault(tmp_path)
    model = Model("ok")
    c, _, _ = _chat(v, model, max_cost=0.0001)
    with pytest.raises(BudgetReached, match="limit of"):
        c.turn("hello")
    assert model.calls == []                                   # nothing was sent

    c, _, _ = _chat(v, Model("ok"), max_cost=1.0, input_rate=5.0, output_rate=25.0)
    c.turn("hello")
    assert 0 < c.spent < 0.05


# --- the brief ----------------------------------------------------------------------------

def test_the_brief_has_what_another_assistant_needs(tmp_path):
    v = _vault(tmp_path)
    guidance.append(v, "Paper first.")
    s = Server(v.root, allow_write=True)
    s.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"clientInfo": {"name": "helper"}}})
    s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "propose_close", "arguments": {"loop_id": _loop(v, "buy compost"),
                                               "reason": "bought"}}})
    before = _files(tmp_path)
    path = brief.write(v)
    text = path.read_text()
    after = _files(tmp_path)
    assert {p for p in after if before.get(p) != after[p]} == {"BRIEF.md"}
    assert "## How I want the work handled" in text and "Paper first." in text
    assert "### router" in text
    lid = _loop(v, "rerun the load balancing ablation")
    assert (f"- [ ] rerun the load balancing ablation. {lid}, from the Claude chat "
            "“Expert routing collapse” (2026-07-01) [open](https://claude.ai/chat/a1)") in text
    assert "- [ ] buy compost (closing it is proposed)." in text
    assert "## Waiting for my decision" in text and "Proposed by agent:helper: bought" in text


def test_cli_brief_and_chat(tmp_path, monkeypatch, capsys):
    v = _vault(tmp_path)
    assert main(["brief", "--vault", str(v.root)]) == 0
    assert (tmp_path / "BRIEF.md").exists()
    model = Model(tool("read_queue"), "Garden is third.")
    monkeypatch.setattr(cli, "make_client", lambda *a, **k: model)
    assert main(["chat", "--vault", str(v.root), "--say", "what is first?"]) == 0
    out = capsys.readouterr().out
    assert "never runs a full pass" in out and "Garden is third." in out
    assert "(about $" in out
    assert main(["chat", "--vault", str(tmp_path / "none"), "--say", "x"]) == 2
    assert main(["chat", "--vault", str(v.root), "--llm", "openai", "--say", "x"]) == 2
