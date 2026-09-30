"""Living in the vault from Obsidian: hand edits are the record, never overwritten.

Every test here edits note files the way a person would in Obsidian (add a
property, write a paragraph, tick a checkbox, rename or delete a file, change
a note's project) and then runs the tools that touch notes.
"""
from magnum_opus import sort as sortmod
from magnum_opus.cli import main
from magnum_opus.distill import Item, Note
from magnum_opus.vault import Vault

from conftest import FakeClient, js


def _note(i, loops=("follow up with the printer",)):
    return Note(conversation_id=f"conv{i}", segment_key=f"conv{i}:start{i}xx:end{i}",
                title=f"Thread {i}", provider="claude",
                updated_at=f"2026-07-0{i + 1}T00:00:00Z", topic=f"topic {i}",
                summary=f"Summary {i}.", start_id=f"start{i}xx", end_id=f"end{i}",
                open_loops=[Item(t, "", None) for t in loops])


def _vault(tmp_path, n=3, **kw):
    v = Vault(tmp_path)
    for i in range(n):
        v.write_note(_note(i, **kw))
    v.rebuild_rollups()
    v.save()
    return v


def _sort(v, assign, slugs=("alpha", "beta")):
    """Run the real sort path with a fake model: taxonomy, then assignment."""
    v.sync_from_disk()
    tax = js({"projects": [{"slug": s, "description": s, "status": "active",
                            "next_action": "", "rank_reason": ""} for s in slugs],
              "priority_order": list(slugs)})
    client = FakeClient(tax, js({f"n{i}": s for i, s in enumerate(assign)}))
    assignment, meta = sortmod.propose_taxonomy(v.state["notes"], client, "m",
                                                progress=lambda *_: None)
    sortmod.apply_taxonomy(v, assignment, meta)
    sortmod.cleanup_unsorted(v)
    return v


def _path(v, i):
    return v.root / v.state["segments"][f"conv{i}:start{i}xx:end{i}"]["path"]


def _front(text):
    return dict(line.split(": ", 1) for line in text.split("---")[1].strip().splitlines()
                if ": " in line)


def test_sort_preserves_hand_edits_and_added_properties(tmp_path):
    v = _vault(tmp_path)
    p = _path(v, 0)
    edited = p.read_text().replace(
        "author: distiller\n", "author: distiller\ntags: [printing, urgent]\ncssclass: wide\n")
    edited += "\n## My notes\nCall them Tuesday. This paragraph is mine.\n"
    p.write_text(edited)

    _sort(v, ["alpha", "alpha", "beta"])

    moved = _path(v, 0)
    assert moved.parent.as_posix().endswith("projects/alpha/chats")
    assert not p.exists()                                  # moved, not copied
    after = moved.read_text()
    assert after == edited.replace("project: unsorted", "project: alpha")


def test_a_project_you_set_by_hand_wins(tmp_path):
    v = _vault(tmp_path)
    p = _path(v, 1)
    p.write_text(p.read_text().replace("project: unsorted", "project: garden"))

    _sort(v, ["alpha", "alpha", "beta"])

    note = _path(v, 1)
    front = _front(note.read_text())
    assert front["project"] == "garden" and front["project_set_by"] == "human"
    assert note.parent.as_posix().endswith("projects/garden/chats")
    # ...and it stays yours on the next sort, even though the model disagrees.
    _sort(v, ["beta", "beta", "beta"])
    assert _front(_path(v, 1).read_text())["project"] == "garden"
    assert "garden" in (tmp_path / "PRIORITIES.md").read_text()


def test_ticking_a_loop_in_obsidian_closes_it(tmp_path):
    v = _vault(tmp_path, n=1, loops=("follow up with the printer", "order more paper"))
    p = _path(v, 0)
    text = p.read_text()
    assert "- [ ] follow up with the printer" in text      # task syntax (SPEC §1)
    p.write_text(text.replace("- [ ] follow up with the printer",
                              "- [x] follow up with the printer"))

    _sort(v, ["alpha"], slugs=("alpha",))

    status = (tmp_path / "projects" / "alpha" / "STATUS.md").read_text()
    assert "order more paper" in status and "follow up with the printer" not in status
    queue = (tmp_path / "QUEUE.md").read_text()
    assert "1 open loops" in queue
    assert "- [x] follow up with the printer" in _path(v, 0).read_text()   # kept in the note


def test_a_renamed_note_is_found_not_duplicated(tmp_path):
    v = _vault(tmp_path)
    p = _path(v, 2)
    renamed = p.with_name("My favourite thread.md")
    p.rename(renamed)

    _sort(v, ["alpha", "alpha", "beta"])

    files = [f for f in tmp_path.rglob("*.md") if f.parent.name in ("chats", "unsorted")]
    assert len(files) == 3
    assert _path(v, 2).name == "My favourite thread.md"


def test_a_deleted_note_stays_deleted(tmp_path, export_dir):
    vault = tmp_path / "vault"
    assert main(["ingest", "--export", str(export_dir), "--vault", str(vault),
                 "--dry-run"]) == 0
    victim = sorted((vault / "unsorted").glob("*.md"))[0]
    victim.unlink()
    assert main(["ingest", "--export", str(export_dir), "--vault", str(vault),
                 "--dry-run"]) == 0
    assert not victim.exists()                             # not re-created
    v = Vault(vault)
    v.sync_from_disk()
    assert len(v.state["notes"]) == 20


def test_ingest_never_overwrites_a_note_after_the_index_is_lost(tmp_path, export_dir):
    vault = tmp_path / "vault"
    main(["ingest", "--export", str(export_dir), "--vault", str(vault), "--dry-run"])
    note = sorted((vault / "unsorted").glob("*.md"))[0]
    note.write_text(note.read_text() + "\nMy own words.\n")
    before = note.read_text()
    (vault / ".magnum" / "state.json").unlink()

    main(["ingest", "--export", str(export_dir), "--vault", str(vault), "--dry-run"])

    assert note.read_text() == before
    assert len(list((vault / "unsorted").glob("*.md"))) == 21   # nothing duplicated


def test_your_own_files_in_the_vault_are_never_touched(tmp_path):
    v = _vault(tmp_path)
    mine = tmp_path / "unsorted" / "shopping list.md"
    mine.write_text("# Shopping\n- eggs\n")
    other = tmp_path / "journal" / "2026-09-30.md"
    other.parent.mkdir()
    other.write_text("---\ntags: [journal]\n---\nA good day.\n")

    _sort(v, ["alpha", "alpha", "beta"])

    assert mine.read_text() == "# Shopping\n- eggs\n"
    assert other.read_text() == "---\ntags: [journal]\n---\nA good day.\n"


def test_reindex_counts_your_files_as_yours_not_as_errors(tmp_path):
    from magnum_opus.reindex import reindex
    v = _vault(tmp_path, n=1)
    (tmp_path / "ideas.md").write_text("just my ideas\n")
    result = reindex(v)
    assert result["notes"] == 1 and result["unreadable"] == 0
    assert result["other_files"] == 1


def test_windows_line_endings_and_bom_do_not_hide_a_note(tmp_path):
    v = _vault(tmp_path)
    p = _path(v, 0)
    windows = "\ufeff" + p.read_text().replace("\n", "\r\n")
    p.write_bytes(windows.encode("utf-8"))

    _sort(v, ["alpha", "alpha", "beta"])

    moved = _path(v, 0)
    assert moved.parent.as_posix().endswith("projects/alpha/chats")
    assert moved.read_bytes().decode("utf-8") == windows.replace(
        "project: unsorted", "project: alpha")          # BOM and CRLF kept
    assert len(v.state["notes"]) == 3
