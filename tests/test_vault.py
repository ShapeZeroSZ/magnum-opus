"""Distillation, locators, the vault, reindexing and sorting."""

from magnum_opus import sort as sortmod
from magnum_opus.distill import (LLMDistiller, AnthropicDistiller, Note, Item,
                                 resolve_anchor, get_distiller)
from magnum_opus.parsers import Message
from magnum_opus.reindex import reindex
from magnum_opus.segment import Segment
from magnum_opus.vault import Vault

from conftest import js

MSGS = [
    Message("m1", "user", "We decided to ship the parser before the web app."),
    Message("m2", "assistant", "You could also consider adding a caching layer later."),
    Message("m3", "user", "Next step: write the migration guide for version two."),
    Message("m4", "user", "Remember to write the migration guide for version two."),
]


# --- locators ----------------------------------------------------------------

def test_exact_unique_anchor_is_verified():
    loc = resolve_anchor("ship the parser before the web app", MSGS, "c")
    assert (loc.message_id, loc.role, loc.verified) == ("m1", "user", True)


def test_assistant_source_is_recorded_as_assistant():
    loc = resolve_anchor("consider adding a caching layer", MSGS, "c")
    assert loc.role == "assistant"


def test_anchor_in_several_messages_is_ambiguous_never_verified():
    loc = resolve_anchor("write the migration guide for version two", MSGS, "c")
    assert loc.ambiguous and not loc.verified


def test_short_or_missing_anchor_gives_no_locator():
    assert resolve_anchor("the parser", MSGS, "c") is None          # < 4 words
    assert resolve_anchor("something nobody ever said at all", MSGS, "c") is None


def test_paraphrased_prefix_degrades_to_unverified():
    loc = resolve_anchor("so we ship the parser before the web app", MSGS, "c")
    assert loc.message_id == "m1" and not loc.verified


# --- distillation ----------------------------------------------------------------

class Conv:
    id, title, provider, updated_at = "c", "Plans", "claude", "2026-07-01T00:00:00Z"


DISTILLED = {
    "topic": "parser release plan", "summary": "Ship order decided.",
    "decisions": [{"text": "Ship parser first", "anchor": "ship the parser before the web app"}],
    "ideas": [{"text": "Caching layer", "anchor": "consider adding a caching layer"}],
    "open_loops": [{"text": "Write migration guide", "anchor": "no such words appear here"}],
    "links": ["Parser Work"],
}


def test_llm_distiller_resolves_locators_in_code(fake):
    client = fake("```json\n" + js(DISTILLED) + "\n```")
    d = LLMDistiller(client=client, model="any-model")
    note = d.distill(Conv, Segment("c", "m1", "m4", "all"), MSGS)
    assert note.project == "unsorted" and note.topic == "parser release plan"
    assert note.decisions[0].locator.verified
    assert note.open_loops[0].locator is None        # unmatched anchor: honest gap
    assert client.prompts[0]["model"] == "any-model"


def test_non_object_reply_becomes_a_summary_not_a_crash(fake):
    d = LLMDistiller(client=fake(js(["a", "list"])), model="m")
    note = d.distill(Conv, Segment("c", "m1", "m4"), MSGS)
    assert note.decisions == [] and note.summary


def test_other_providers_must_name_a_model(fake):
    import pytest
    with pytest.raises(RuntimeError, match="--model"):
        LLMDistiller(client=fake("{}"), provider="openai")


def test_old_names_still_work(fake):
    assert isinstance(get_distiller("anthropic", [], client=fake("{}")), AnthropicDistiller)
    assert get_distiller("llm", [], model="m", client=fake("{}"),
                         provider="openai").model == "m"


# --- vault --------------------------------------------------------------------

def _note(key="c:m1:m4", title="Plans", project="unsorted", loops=1):
    return Note(conversation_id=key.split(":")[0], segment_key=key, title=title,
                provider="claude", updated_at="2026-07-01T00:00:00Z", project=project,
                topic="topic", summary="A summary.", start_id=key.split(":")[1],
                end_id=key.split(":")[2],
                open_loops=[Item(f"loop {i}", "", None) for i in range(loops)])


def test_note_frontmatter_and_generated_views(tmp_path):
    v = Vault(tmp_path)
    for i in range(5):
        v.write_note(_note(f"c{i}:a{i}:b{i}", project=f"proj-{i}", loops=i + 1))
    v.rebuild_rollups()
    v.save()
    queue = (tmp_path / "QUEUE.md").read_text()
    assert queue.count("**[[projects/") == 3                     # never more than 3
    assert "2 other projects intentionally hidden" in queue
    assert (tmp_path / "PRIORITIES.md").read_text().count("**[[projects/") == 5
    assert (tmp_path / "projects" / "proj-0" / "STATUS.md").exists()


def test_titles_with_quotes_survive_write_and_reindex(tmp_path):
    import pytest
    yaml = pytest.importorskip("yaml")
    v = Vault(tmp_path)
    title = 'The "Grey" router: a\\b test'
    path = v.write_note(_note(title=title))
    v.save()
    # Obsidian reads frontmatter as YAML: it must parse, and say the same thing.
    front = yaml.safe_load(path.read_text().split("---")[1])
    assert front["title"] == title and front["spec"] == "0.2"
    v.state["notes"] = []
    reindex(v)
    assert v.state["notes"][0]["title"] == title


def test_reindex_rebuilds_the_index_from_notes_alone(tmp_path):
    v = Vault(tmp_path)
    n = _note()
    n.decisions = [Item("Ship parser first", "ship the parser before the web app",
                        resolve_anchor("ship the parser before the web app", MSGS, "c"))]
    v.write_note(n)
    v.save()
    (tmp_path / ".magnum" / "state.json").unlink()
    v2 = Vault(tmp_path)
    result = reindex(v2)
    assert result == {"notes": 1, "partial_locators": 0, "unreadable": 0,
                      "other_files": 0}
    loc = v2.state["notes"][0]["decisions"][0]["locator"]
    assert loc["message_id"] == "m1" and loc["verified"]


def test_corrupt_index_is_moved_aside_never_lost(tmp_path):
    import pytest
    Vault(tmp_path).save()
    (tmp_path / ".magnum" / "state.json").write_text("{broken")
    with pytest.raises(RuntimeError, match="reindex"):
        Vault(tmp_path)
    assert (tmp_path / ".magnum" / "state.corrupt").exists()


# --- sorting -------------------------------------------------------------------

def _taxonomy(slugs):
    return js({"projects": [{"slug": s, "description": f"{s} work", "status": "active",
                             "next_action": f"finish {s}", "rank_reason": "why"}
                            for s in slugs],
               "priority_order": list(reversed(slugs))})


def _sorted_vault(tmp_path, fake, assign):
    v = Vault(tmp_path)
    for i in range(3):
        v.write_note(_note(f"c{i}:a{i}:b{i}"))
    v.save()
    client = fake(_taxonomy(["alpha", "beta"]), js(assign))
    assignment, meta = sortmod.propose_taxonomy(v.state["notes"], client, "m",
                                                progress=lambda *_: None)
    sortmod.apply_taxonomy(v, assignment, meta)
    sortmod.cleanup_unsorted(v)
    return v


def test_sort_files_notes_ranks_and_keeps_unassigned_visible(tmp_path, fake):
    v = _sorted_vault(tmp_path, fake, {"n0": "alpha", "n1": "beta", "n2": "invented"})
    projects = {n["segment_key"]: n["project"] for n in v.state["notes"]}
    assert projects == {"c0:a0:b0": "alpha", "c1:a1:b1": "beta", "c2:a2:b2": "misc"}
    meta = v.config["project_meta"]
    assert meta["beta"]["rank"] < meta["alpha"]["rank"]          # priority_order honoured
    assert not (tmp_path / "unsorted").exists()
    queue = (tmp_path / "QUEUE.md").read_text()
    assert queue.index("beta") < queue.index("alpha") and "finish beta" in queue


def test_resorting_moves_notes_without_leaving_stale_copies(tmp_path, fake):
    v = _sorted_vault(tmp_path, fake, {"n0": "alpha", "n1": "alpha", "n2": "alpha"})
    client = fake(_taxonomy(["alpha", "beta"]), js({"n0": "beta", "n1": "alpha", "n2": "alpha"}))
    assignment, meta = sortmod.propose_taxonomy(v.state["notes"], client, "m",
                                                progress=lambda *_: None)
    sortmod.apply_taxonomy(v, assignment, meta)
    files = sorted(p.relative_to(tmp_path).as_posix()
                   for p in tmp_path.rglob("chats/*.md"))
    assert len(files) == 3, files          # one file per note, not one per sort
    v.state["notes"] = []
    reindex(v)
    assert {n["segment_key"]: n["project"] for n in v.state["notes"]}["c0:a0:b0"] == "beta"
