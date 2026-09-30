"""Loading, parsing, redaction and segmentation."""
import zipfile

import pytest

from magnum_opus import archive, redact
from magnum_opus.parsers import load_export, parse_conversations
from magnum_opus.segment import heuristic_segments, llm_segments, segment_conversation

from conftest import FIXTURES, js


# --- archive ----------------------------------------------------------------

def test_discover_groups_shards_and_never_reads_account_pii(export_dir, tmp_path):
    cats = archive.discover(export_dir)
    assert {"conversations", "memories", "projects", "light_metadata"} <= set(cats)
    assert "EXCLUDED (account PII)" in archive.report(export_dir)
    # Corrupt the PII zip: if anything ever opened it, loading would now fail.
    folder = tmp_path / "exp"
    folder.mkdir()
    for p in export_dir.glob("*.zip"):
        (folder / p.name).write_bytes(p.read_bytes())
    (folder / "light_metadata-000.zip").write_bytes(b"not a zip at all")
    convs = archive.load_conversations(folder)
    assert len(convs) == 4


def test_shards_are_merged_in_part_order(export_dir, tmp_path):
    src = zipfile.ZipFile(export_dir / "conversations-000.zip").read("conversations.json")
    import json
    data = json.loads(src)
    for i, chunk in enumerate((data[:2], data[2:])):
        with zipfile.ZipFile(tmp_path / f"conversations-00{i}.zip", "w") as z:
            z.writestr("conversations.json", json.dumps(chunk))
    assert len(archive.load_conversations(tmp_path)) == 4


def test_missing_conversations_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="conversations"):
        archive.load_conversations(tmp_path)


def test_memories_keep_assistant_provenance(export_dir):
    recs = archive.load_memories(export_dir)
    assert recs and all(r["author"] == "assistant-memory" for r in recs)


# --- parsers ------------------------------------------------------------------

def test_claude_parser_keeps_native_ids_and_roles():
    convs = load_export(FIXTURES / "claude_export.json")
    assert {c.provider for c in convs} == {"claude"}
    grey = next(c for c in convs if c.id == "aaa-111")
    assert [m.role for m in grey.messages] == ["user", "assistant"]
    # The fixture's messages have no uuid: identity falls back to a content
    # hash, and is flagged as synthetic rather than passed off as native.
    assert all(m.id_is_synthetic and m.id.startswith("h") for m in grey.messages)
    assert "z-loss" in grey.messages[1].text          # content blocks are read


def test_synthetic_ids_are_stable_across_reloads():
    a = load_export(FIXTURES / "claude_export.json")
    b = load_export(FIXTURES / "claude_export.json")
    assert [m.id for c in a for m in c.messages] == [m.id for c in b for m in c.messages]


def test_chatgpt_parser_follows_the_current_branch():
    (conv,) = load_export(FIXTURES / "chatgpt_export.json")
    assert conv.provider == "chatgpt"
    assert [(m.id, m.role) for m in conv.messages] == [("n2", "user"), ("n3", "assistant")]


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError, match="Unrecognized"):
        parse_conversations([{"something": "else"}])


# --- redaction ----------------------------------------------------------------

def test_redaction_drops_whole_messages_with_secrets(export_dir):
    convs = archive.load_conversations(export_dir)
    kept, rep = redact.redact_all(convs)
    assert rep.dropped == 4 and rep.reasons == {"api_key": 4, "ssn": 4}
    assert not any("sk-ant-" in m.text for c in kept for m in c.messages)


@pytest.mark.parametrize("text,hard", [
    ("my key is sk-abcdefghijklmnopqrstuv", ["api_key"]),
    ("AKIAABCDEFGHIJKLMNOP", ["aws_key"]),
    ("card 4111 1111 1111 1111", ["card_number"]),     # passes Luhn
    ("hash 1234567890123456", []),                     # fails Luhn: not a card
    ("-rw-r--r-- 1 u g 4096 1700000000123", []),       # separate fields never merge
])
def test_hard_patterns(text, hard):
    assert redact.message_reasons(text)[0] == hard


def test_email_and_phone_are_flagged_not_dropped_unless_strict():
    hard, soft = redact.message_reasons("mail me at a@b.com or 555-123-4567")
    assert hard == [] and set(soft) == {"email", "phone"}
    hard, soft = redact.message_reasons("mail me at a@b.com", strict=True)
    assert "email" in hard and soft == []


# --- segmentation ---------------------------------------------------------------

def _long(export_dir):
    convs = archive.load_conversations(export_dir)
    return max(convs, key=lambda c: len(c.messages))


def test_short_conversation_is_one_segment():
    conv = load_export(FIXTURES / "claude_export.json")[0]
    (seg,) = segment_conversation(conv)
    assert (seg.start_id, seg.end_id) == (conv.messages[0].id, conv.messages[-1].id)


def test_heuristic_segments_cover_everything_without_gaps(export_dir):
    conv = _long(export_dir)
    segs = heuristic_segments(conv)
    ids = [m.id for m in conv.messages]
    covered = [m.id for s in segs for m in conv.span(s.start_id, s.end_id)]
    assert covered == ids


def test_llm_segments_are_validated_and_forced_to_cover(export_dir, fake):
    conv = _long(export_dir)
    ids = [m.id for m in conv.messages]
    mid = len(ids) // 2
    reply = js([{"start_id": ids[3], "end_id": ids[mid], "label": "first"},
                {"start_id": ids[mid + 1], "end_id": ids[-5], "label": "second"},
                {"start_id": "invented-id", "end_id": ids[-1], "label": "bogus"}])
    segs = llm_segments(conv, fake(reply), "m")
    assert [s.label for s in segs] == ["first", "second"]      # invented id dropped
    assert segs[0].start_id == ids[0] and segs[-1].end_id == ids[-1]


@pytest.mark.parametrize("reply", ["not json", js({"a": 1}), js(["x", 3]), js([])])
def test_bad_segmentation_falls_back_to_heuristic(export_dir, fake, reply):
    conv = _long(export_dir)
    assert llm_segments(conv, fake(reply), "m") == heuristic_segments(conv)
