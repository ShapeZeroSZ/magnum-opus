"""Shape Zero chat logs as a source: one user's logs become their vault."""
from pathlib import Path

from magnum_opus import archive
from magnum_opus.cli import main
from magnum_opus.parsers import parse_shapezero_logs
from magnum_opus.vault import Vault

LOGS = Path(__file__).parent / "fixtures" / "shapezero" / "user-42"


def test_each_day_is_a_conversation_with_stable_ids():
    convs = parse_shapezero_logs(LOGS)
    assert [c.id for c in convs] == ["shapezero-user-42-2026-09-30",
                                     "shapezero-user-42-2026-09-29"]
    day = convs[1]
    assert day.provider == "shapezero"
    assert day.title == "Let's plan the Orbit social app: friends circle around…"
    assert [m.id for m in day.messages] == ["r1-user", "r1-assistant", "r2-user", "r2-assistant"]
    assert not any(m.id_is_synthetic for m in day.messages)
    assert day.created_at == "2026-09-29T10:00:00" and day.updated_at == "2026-09-29T11:30:04"
    assert parse_shapezero_logs(LOGS)[1].messages[0].id == "r1-user"     # stable


def test_a_log_folder_is_recognised_without_being_told():
    assert [c.provider for c in archive.load_conversations(LOGS)] == ["shapezero"] * 2


def test_metadata_only_and_broken_lines_are_skipped(tmp_path):
    d = tmp_path / "anon-x"
    d.mkdir()
    (d / "2026-09-29.jsonl").write_text(
        '{"timestamp": "2026-09-29T11:00:00", "owner": "anon-x", "mode": "fast"}\n{oops\n')
    assert parse_shapezero_logs(d) == []


def test_logs_ingest_into_a_vault_and_can_be_found(tmp_path, capsys):
    vault = tmp_path / "vault"
    assert main(["ingest", "--export", str(LOGS), "--vault", str(vault), "--dry-run"]) == 0
    v = Vault(vault)
    v.sync_from_disk()
    assert {n["provider"] for n in v.state["notes"]} == {"shapezero"}
    note = next(p for p in (vault / "unsorted").glob("*.md") if "Orbit" in p.read_text())
    assert "**Source:** the Shape Zero chat “Let's plan the Orbit social app" in note.read_text()
    capsys.readouterr()
    assert main(["find", "orbit", "ring", "--vault", str(vault)]) == 0
    assert "from:  Shape Zero chat, 2026-09-29" in capsys.readouterr().out

    # Re-reading the same logs distills nothing twice.
    before = sorted(p.name for p in vault.rglob("*.md"))
    main(["ingest", "--export", str(LOGS), "--vault", str(vault), "--dry-run"])
    assert sorted(p.name for p in vault.rglob("*.md")) == before
