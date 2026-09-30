"""The emergent thesis: the model judges, the code checks every citation."""
import json
import re

import pytest

from magnum_opus import cli, converge as conv, thesis as th
from magnum_opus.cli import main
from magnum_opus.vault import Vault

from conftest import FakeClient
from test_converge import _files, _vault


def _ids(prompt):
    """{title: short id} as the model sees them."""
    return {m.group(2): m.group(1)
            for m in re.finditer(r"^(n\d+) \[[^\]]*\] ([^|]+?) \|", prompt, re.MULTILINE)}


def _reply(statement="Sparse expert routing, studied and written up.",
           claims=(("The paper is the router work written up.",
                    ["Expert routing collapse", "Draft on sparse experts"]),),
           statement_cites=("Expert routing collapse",)):
    """A fake model that cites notes by title, as a real one cites by id."""
    def answer(prompt):
        ids = _ids(prompt)
        cite = lambda titles: [ids.get(t, t) for t in titles]
        return json.dumps({"statement": statement,
                           "statement_cites": cite(statement_cites),
                           "claims": [{"text": t, "cites": cite(c)} for t, c in claims]})
    return answer


def _run(v, client, **kw):
    state = th.load_state(v)
    th.record_ticks(v, state)
    prep = th.prepare(v, state, **{k: kw.pop(k) for k in ("max_notes",) if k in kw})
    return th.thesis(v, client, "m", prep, state, **kw), prep


def _tick(path, cid, box):
    text = path.read_text()
    head, _, rest = text.partition(f"<!--thesis:{cid}-->")
    rest = rest.replace(f"- [ ] {box}", f"- [x] {box}", 1)
    path.write_text(head + f"<!--thesis:{cid}-->" + rest)


def _untick(path, cid, box):
    text = path.read_text()
    head, _, rest = text.partition(f"<!--thesis:{cid}-->")
    path.write_text(head + f"<!--thesis:{cid}-->" + rest.replace(f"- [x] {box}", f"- [ ] {box}", 1))


# --- every claim cites notes that exist ---------------------------------------------

def test_claims_citing_no_real_note_are_dropped(tmp_path):
    v = _vault(tmp_path)
    client = FakeClient(_reply(claims=[
        ("The paper is the router work written up.",
         ["Expert routing collapse", "Draft on sparse experts"]),
        ("An invented claim.", ["n99", "Nonexistent note"]),
        ("Another invented claim.", [])]))
    version, _ = _run(v, client)
    assert [c["text"] for c in version["claims"]] == ["The paper is the router work written up."]
    assert version["dropped"]["uncited"] == 2
    text = (tmp_path / "EMERGENT_THESIS.md").read_text()
    assert "invented" not in text
    assert "2 claims dropped because they cited no note that exists" in text
    for target in re.findall(r"\[\[([^|\]]+)\|", text):          # every link resolves
        assert (tmp_path / f"{target}.md").exists()
    assert "across paper, router" in text                         # computed, not claimed


def test_a_baseless_statement_writes_nothing(tmp_path):
    v = _vault(tmp_path)
    client = FakeClient(_reply(statement_cites=("n42",)))
    with pytest.raises(RuntimeError, match="cited no note that exists"):
        _run(v, client)
    assert not (tmp_path / "EMERGENT_THESIS.md").exists()
    assert th.load_state(v)["versions"] == []


# --- time is provenance -----------------------------------------------------------

def test_dates_never_reach_the_model(tmp_path):
    v = _vault(tmp_path)                    # summaries carry dates and times
    prep = th.prepare(v, th.load_state(v))
    assert "2026" not in prep.prompt and "14:30" not in prep.prompt
    assert "07/01" not in prep.prompt
    for word in ("July", "Tuesday", "today", "On ,", " at ,"):
        assert word not in prep.prompt
    assert "add a load balancing loss. | open:" in prep.prompt       # the rest reads cleanly
    assert "Tomato seedlings" in prep.prompt


@pytest.mark.parametrize("raw,clean", [
    ("Shipped on March 3rd, 2026 at 9:15 am.", "Shipped."),
    ("Met Monday (2026-07-01) to decide scope.", "Met to decide scope."),
    ("Due 3 May; then review.", "Due; then review."),
    ("May we add a march of progress?", "May we add a march of progress?"),
    ("Remember where it came from: the router work.", "Remember where it came from: the router work."),
    ("Know what it is made of.", "Know what it is made of."),
])
def test_clean_removes_dates_not_words(raw, clean):
    assert th._clean(raw) == clean


# --- convergence feedback is honoured -------------------------------------------------

def _router_paper(v):
    return conv.pair_id("paper", "router")


def test_relationships_you_rejected_are_never_sent(tmp_path):
    v = _vault(tmp_path)
    conv.converge(v, conv.TfidfBackend())
    cid = _router_paper(v)
    path = tmp_path / "CONVERGENCE.md"
    prep = th.prepare(v, th.load_state(v))
    assert "PROPOSED paper <-> router" in prep.prompt

    # Ticked in Obsidian, converge not re-run yet: already honoured.
    text = path.read_text()
    head, _, rest = text.partition(f"<!--convergence:{cid}-->")
    path.write_text(head + f"<!--convergence:{cid}-->" + rest.replace("- [ ] reject", "- [x] reject", 1))
    prep = th.prepare(v, th.load_state(v))
    assert "paper <-> router" not in prep.prompt

    path.write_text(head + f"<!--convergence:{cid}-->" + rest.replace("- [ ] accept", "- [x] accept", 1))
    prep = th.prepare(v, th.load_state(v))
    assert "CONFIRMED paper <-> router" in prep.prompt


def test_relationships_with_your_external_files_are_not_sent(tmp_path):
    v = _vault(tmp_path / "vault")
    other = tmp_path / "obsidian"
    other.mkdir()
    (other / "Tomatoes.md").write_text("Tomato seedlings need compost and watering "
                                       "in the raised bed. Private musing.\n")
    conv.converge(v, conv.TfidfBackend(), externals=[other])
    assert any("external:" in k for r in conv.load_state(v)["latest"]
               for p in r["evidence"] for k in p[:2])
    prep = th.prepare(v, th.load_state(v))
    assert prep.skipped_external >= 1
    assert "Private musing" not in prep.prompt and "your notes" not in prep.prompt


# --- your judgment constrains every later version -------------------------------------

def test_accept_is_kept_reject_is_never_repeated_untick_reverses(tmp_path):
    v = _vault(tmp_path)
    path = tmp_path / "EMERGENT_THESIS.md"
    a = ("The paper is the router work written up.",
         ["Expert routing collapse", "Draft on sparse experts"])
    b = ("The garden is secretly about experts.", ["Tomato seedlings"])
    v1, _ = _run(v, FakeClient(_reply(claims=[a, b])))
    ida, idb = th.claim_id(a[0]), th.claim_id(b[0])
    _tick(path, ida, "accept")
    _tick(path, idb, "reject")

    c = ("Capacity and collapse are one problem.", ["Router capacity factor"])
    client = FakeClient(_reply(claims=[a, b, c]))
    v2, prep = _run(v, client)
    sent = client.prompts[0]["prompt"]
    assert f"CLAIMS THE PERSON CONFIRMED:\n- {a[0]}" in sent
    assert f"CLAIMS THE PERSON REJECTED:\n- {b[0]}" in sent
    assert [x["text"] for x in v2["claims"]] == [c[0]]          # b blocked by code
    assert v2["dropped"]["rejected"] == 1
    text = path.read_text()
    confirmed = text.split("## Confirmed by you")[1].split("## Rejected")[0]
    rejected = text.split("## Rejected by you")[1]
    assert a[0] in confirmed and a[0] not in text.split("## Confirmed")[0]
    assert b[0] in rejected and text.count(f"<!--thesis:{idb}-->") == 1

    _untick(path, idb, "reject")
    v3, _ = _run(v, FakeClient(_reply(claims=[b])))
    assert [x["text"] for x in v3["claims"]] == [b[0]]          # reconsidered
    assert "## Rejected by you" not in path.read_text()


def test_versions_are_kept(tmp_path):
    v = _vault(tmp_path)
    _run(v, FakeClient(_reply(statement="First reading of the work.")))
    _run(v, FakeClient(_reply(statement="Second reading of the work.")))
    state = th.load_state(v)
    assert [x["version"] for x in state["versions"]] == [1, 2]
    assert "First reading" in (tmp_path / ".magnum" / "thesis" / "v1.md").read_text()
    current = (tmp_path / "EMERGENT_THESIS.md").read_text()
    assert "Version 2" in current and "Second reading" in current
    assert "Earlier versions are in `.magnum/thesis/`" in current


def test_a_lens_not_a_blender(tmp_path):
    v = _vault(tmp_path)
    before = _files(tmp_path)
    _run(v, FakeClient(_reply()))
    after = _files(tmp_path)
    changed = {p for p in after if before.get(p) != after[p]}
    assert changed <= {"EMERGENT_THESIS.md", ".magnum/thesis.json", ".magnum/thesis/v1.md",
                       ".magnum/state.json"}
    assert {p for p in before if p not in after} == set()


def test_evidence_notes_are_sent_first_and_selection_is_capped(tmp_path):
    v = _vault(tmp_path)
    conv.converge(v, conv.TfidfBackend())
    prep = th.prepare(v, th.load_state(v), max_notes=2)
    evidence = conv.load_state(v)["latest"][0]["evidence"][0][:2]
    assert sorted(prep.ids.values()) == sorted(evidence)
    assert prep.notes_total == 4


# --- cost is visible before it is incurred ------------------------------------------

def _no_client(*_a, **_k):
    raise AssertionError("a client was built")


def test_cli_dry_run_sends_nothing_and_shows_the_prompt(tmp_path, monkeypatch, capsys):
    _vault(tmp_path)
    monkeypatch.setattr(cli, "make_client", _no_client)
    assert main(["thesis", "--vault", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "WORST-CASE COST" in out and "nothing sent" in out
    assert "Expert routing collapse" in (tmp_path / ".magnum" / "thesis" / "prompt.txt").read_text()
    assert not (tmp_path / "EMERGENT_THESIS.md").exists()


def test_cli_asks_before_spending(tmp_path, monkeypatch, capsys):
    _vault(tmp_path)
    client = FakeClient(_reply())
    monkeypatch.setattr(cli, "make_client", lambda *a, **k: client)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert main(["thesis", "--vault", str(tmp_path)]) == 1
    assert "Aborted. Nothing spent." in capsys.readouterr().out
    assert client.prompts == []

    monkeypatch.setattr("builtins.input", lambda *_: "y")
    assert main(["thesis", "--vault", str(tmp_path)]) == 0
    assert len(client.prompts) == 1
    assert "Version 1: 1 claim." in capsys.readouterr().out
    assert (tmp_path / "EMERGENT_THESIS.md").exists()


def test_cli_openai_needs_a_model(tmp_path, capsys):
    _vault(tmp_path)
    assert main(["thesis", "--vault", str(tmp_path), "--llm", "openai"]) == 2


def test_cli_with_an_empty_vault(tmp_path):
    Vault(tmp_path)
    assert main(["thesis", "--vault", str(tmp_path), "--dry-run"]) == 1
