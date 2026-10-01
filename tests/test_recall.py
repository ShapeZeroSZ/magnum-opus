"""Recall: small cited items, no model calls, time shown but never scored."""
from magnum_opus.agents import Server
from magnum_opus.distill import Item, Locator, Note
from magnum_opus.recall import check_citations, item_id, items, recall
from magnum_opus.vault import Vault

from test_converge import _files, _vault


def _decision(v, key, day, text):
    conv, start, end = key.split(":")
    v.write_note(Note(conversation_id=conv, segment_key=key, title=f"Plan {day}",
                      provider="claude", updated_at=f"{day}T00:00:00Z", project="orbit",
                      topic="orbit", summary="Orbit planning.", start_id=start, end_id=end,
                      decisions=[Item(text, "", None)]))


def test_items_are_small_sourced_and_stable(tmp_path):
    v = _vault(tmp_path)
    v.sync_from_disk()
    got = items(v)
    loop = next(i for i in got if i.text == "rerun the load balancing ablation")
    assert loop.kind == "open_loop" and loop.project == "router"
    assert loop.note == "projects/router/chats/a1--s1.md" and loop.date == "2026-07-01"
    assert loop.url == "https://claude.ai/chat/a1"
    assert loop.id == item_id("a1:s1:e1", "open_loop", "rerun the load balancing ablation")
    assert [i.id for i in items(v)] == [i.id for i in got]                 # stable


def test_recall_finds_the_relevant_items_and_writes_nothing(tmp_path):
    _vault(tmp_path)
    before = _files(tmp_path)
    hits = recall(tmp_path, "load balancing ablation", limit=3)
    assert hits[0].text == "rerun the load balancing ablation"
    assert {h.project for h in hits} <= {"router", "paper"}
    assert recall(tmp_path, "load balancing", project="paper")[0].project == "paper"
    assert all(h.kind == "open_loop" for h in recall(tmp_path, "ablation", kinds=["open_loop"]))
    assert _files(tmp_path) == before


def test_dates_never_make_something_relevant(tmp_path):
    _vault(tmp_path)                                  # summaries mention dates and times
    assert recall(tmp_path, "2026-07-01 14:30 Tuesday July") == []


def test_a_later_decision_comes_before_the_one_it_replaced(tmp_path):
    v = Vault(tmp_path)
    _decision(v, "o1:s1:e1", "2026-03-01", "Orbit ships the feed first.")
    _decision(v, "o2:s2:e2", "2026-09-01", "Orbit ships the ring view first.")
    hits = recall(tmp_path, "orbit ships first", kinds=["decision"])
    assert [h.date for h in hits] == ["2026-09-01", "2026-03-01"]


def test_agent_notes_are_never_recalled(tmp_path):
    v = _vault(tmp_path)
    s = Server(v.root, allow_write=True)
    s.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
    s.write_note({"title": "Load balancing is solved", "body": "load balancing ablation done"})
    assert all(not h.note.startswith("agents/") for h in recall(tmp_path, "load balancing"))


def test_embeddings_fuse_with_keywords(tmp_path):
    _vault(tmp_path)

    def embed(texts):                                  # "meaning": gardening words
        garden = ("tomato", "compost", "seedlings", "watering", "plants")
        return [[1.0 if any(w in t.lower() for w in garden) else 0.0, 1.0] for t in texts]
    lexical = recall(tmp_path, "plants", limit=3)
    fused = recall(tmp_path, "plants", limit=3, embed=embed)
    assert lexical == []                               # no shared words
    assert fused and fused[0].project == "garden"      # found by meaning


def test_the_citation_lock(tmp_path):
    _vault(tmp_path)
    ids = [h.id for h in recall(tmp_path, "load balancing")]
    assert check_citations(ids[:1], ids) == {"ok": True, "cited": ids[:1], "unrecalled": []}
    bad = check_citations([ids[0], "m-invented00"], ids)
    assert not bad["ok"] and bad["unrecalled"] == ["m-invented00"]


def test_connected_assistants_can_recall_with_ids(tmp_path):
    v = _vault(tmp_path)
    r = Server(v.root).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "recall", "arguments": {"query": "load balancing ablation"}}})
    text = r["result"]["content"][0]["text"]
    first = text.splitlines()[0]
    assert first.startswith("- m-") and "[open_loop, router] rerun the load balancing ablation" in first
    assert "[open](https://claude.ai/chat/a1)" in first


def test_items_say_who_said_them_only_when_verified(tmp_path):
    """A decision lifted from an assistant turn is a suggestion: recall carries
    the verified speaker so it can never be passed off as the person's."""
    v = Vault(tmp_path / "v")
    v.write_note(Note(conversation_id="c1", segment_key="c1:s:e", title="Garden",
                      provider="shapezero", updated_at="2026-09-29T00:00:00Z",
                      project="garden", topic="garden", summary="Garden planning.",
                      decisions=[Item("Beds go along the south fence.", "south fence",
                                      Locator("c1", "m1-user", "user", verified=True))],
                      ideas=[Item("Plant marigolds to keep pests away.", "marigolds",
                                  Locator("c1", "m2-assistant", "assistant", verified=True)),
                             Item("Try drip irrigation.", "drip",
                                  Locator("c1", "m3-user", "user", verified=False,
                                          ambiguous=True)),
                             Item("Add a compost bin.", "", None)]))
    v.rebuild_rollups()
    v.save()
    v.sync_from_disk()
    by = {i.text: i for i in items(v)}
    assert by["Beds go along the south fence."].speaker == "user"
    assert by["Plant marigolds to keep pests away."].speaker == "assistant"
    assert by["Try drip irrigation."].speaker == ""          # ambiguous: not a guess
    assert by["Add a compost bin."].speaker == ""            # unlocated
    assert by["Garden planning."].speaker == ""              # the distiller's summary
    fence = by["Beds go along the south fence."]
    assert (fence.conversation, fence.message) == ("c1", "m1-user")   # openable at its source
    assert by["Add a compost bin."].message == ""
    from magnum_opus.proposals import open_loops
    v2 = Vault(tmp_path / "v2")
    v2.write_note(Note(conversation_id="c2", segment_key="c2:s:e", title="Orbit",
                       provider="shapezero", updated_at="2026-09-29T00:00:00Z",
                       open_loops=[Item("Name the rings feature.", "name",
                                        Locator("c2", "m1-user", "user", verified=True)),
                                   Item("Draft a privacy policy.", "policy",
                                        Locator("c2", "m2-assistant", "assistant",
                                                verified=True))]))
    v2.rebuild_rollups()
    v2.save()
    v2.sync_from_disk()
    assert {l["text"]: l["speaker"] for l in open_loops(v2)} == {
        "Name the rings feature.": "user", "Draft a privacy policy.": "assistant"}
    out = Server(tmp_path / "v").recall({"query": "marigolds pests"})
    assert "said by the assistant] Plant marigolds" in out
