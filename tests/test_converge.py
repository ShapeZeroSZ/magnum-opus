"""The convergence engine: a lens over the vault, never a blender."""
import hashlib
import json
import re

import pytest

from magnum_opus import converge as conv
from magnum_opus.cli import main
from magnum_opus.distill import Item, Note
from magnum_opus.vault import Vault

DATE_NOISE = "On 2026-07-01T14:30:00Z, Tuesday July 1 at 14:30 (07/01/2026), today."


def _add(v, key, project, title, summary, loops=()):
    conv_id, start, end = key.split(":")
    v.write_note(Note(conversation_id=conv_id, segment_key=key, title=title,
                      provider="claude", updated_at="2026-07-01T00:00:00Z",
                      project=project, topic=title, summary=summary,
                      start_id=start, end_id=end,
                      open_loops=[Item(t, "", None) for t in loops]))


def _vault(tmp_path):
    v = Vault(tmp_path)
    _add(v, "a1:s1:e1", "router", "Expert routing collapse",
         "The mixture of experts router collapses onto one expert; add a load "
         "balancing loss. " + DATE_NOISE, ["rerun the load balancing ablation"])
    _add(v, "a2:s2:e2", "router", "Router capacity factor",
         "Capacity factor and token dropping for the experts router.")
    _add(v, "b1:s3:e3", "paper", "Draft on sparse experts",
         "Paper section explaining load balancing loss for mixture of experts "
         "routing, with the collapse ablation as the main figure.")
    _add(v, "c1:s4:e4", "garden", "Tomato seedlings",
         "Watering schedule for tomato seedlings and compost for the raised bed. "
         + DATE_NOISE)
    v.rebuild_rollups()
    v.save()
    return v


def _files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def _run(v, **kw):
    return conv.converge(v, conv.TfidfBackend(), **kw)


def _ids(v):
    return re.findall(r"<!--convergence:(c-[0-9a-f]{6})-->",
                      (v.root / "CONVERGENCE.md").read_text())


# --- time is provenance, never evidence -------------------------------------------

def test_terms_strip_every_trace_of_time():
    assert conv.terms(DATE_NOISE) == []
    assert conv.terms("Load-balancing loss for the experts router") == [
        "load-balancing", "loss", "experts", "router"]


def test_shared_dates_never_make_a_relationship(tmp_path):
    v = _vault(tmp_path)
    result = _run(v)
    pairs = [set(r["groups"]) for r in result["proposed"]]
    assert {"router", "paper"} in pairs                  # shared content: found
    assert not any("garden" in p for p in pairs)         # shared dates only: not
    # Not merely below the floor: dates contribute nothing at all.
    docs = conv.vault_documents(v)
    every = conv.relationships(docs, conv.TfidfBackend().fit(docs))
    assert not any("garden" in r["groups"] for r in every)


# --- a lens, never a blender ---------------------------------------------------------

def test_notes_are_never_touched(tmp_path):
    v = _vault(tmp_path)
    before = _files(tmp_path)
    _run(v)
    after = _files(tmp_path)
    changed = {k for k in after if before.get(k) != after[k]}
    assert changed == {"CONVERGENCE.md", ".magnum/convergence.json"}


def test_only_cross_project_relationships_are_proposed(tmp_path):
    v = _vault(tmp_path)
    for r in _run(v)["proposed"]:
        assert r["groups"][0] != r["groups"][1]


def test_evidence_cites_real_notes_and_the_words_it_rests_on(tmp_path):
    v = _vault(tmp_path)
    r = next(r for r in _run(v)["proposed"] if set(r["groups"]) == {"router", "paper"})
    assert {"balancing", "experts"} <= set(r["shared"])
    text = (tmp_path / "CONVERGENCE.md").read_text()
    for target in re.findall(r"\[\[([^|\]]+)\|", text):
        assert (tmp_path / f"{target}.md").exists(), target


def test_the_default_view_is_small(tmp_path):
    v = Vault(tmp_path)
    for i in range(12):
        _add(v, f"k{i}:s{i}:e{i}", f"project-{i}", f"Note {i}",
             "shared vocabulary about solar panels and battery storage")
    _run(v)
    assert len(_ids(v)) == 5
    assert "weaker proposals not shown" in (tmp_path / "CONVERGENCE.md").read_text()
    _run(v, top=100)
    assert len(_ids(v)) == 66                             # 12 choose 2


def test_same_input_same_output(tmp_path):
    v = _vault(tmp_path)
    strip = lambda s: re.sub(r"_Generated [^ ]+", "", s)
    _run(v)
    first = strip((tmp_path / "CONVERGENCE.md").read_text())
    _run(v)
    assert strip((tmp_path / "CONVERGENCE.md").read_text()) == first


def test_pieces_of_one_conversation_are_never_paired(tmp_path):
    v = Vault(tmp_path)
    _add(v, "same:s1:e1", "unsorted", "Long thread", "solar panels battery storage")
    _add(v, "same:s2:e2", "unsorted", "Long thread", "solar panels battery storage")
    assert _run(v)["proposed"] == []


def test_unsorted_notes_are_compared_individually(tmp_path):
    v = Vault(tmp_path)
    _add(v, "u1:s1:e1", "unsorted", "Solar", "solar panels battery storage inverter")
    _add(v, "u2:s2:e2", "unsorted", "Battery", "battery storage inverter sizing")
    (r,) = _run(v)["proposed"]
    assert "(unsorted)" in r["labels"][0] and "(unsorted)" in r["labels"][1]


# --- your judgment constrains every later pass --------------------------------------

def _tick(v, cid, what, mark="x"):
    p = v.root / "CONVERGENCE.md"
    lines, inside = [], False
    for line in p.read_text().splitlines():
        if "<!--convergence:" in line:
            inside = cid in line
        if inside and re.match(rf"- \[[ x]\] {what}", line):
            line = f"- [{mark}] {what}"
        lines.append(line)
    p.write_text("\n".join(lines) + "\n")


def test_reject_in_obsidian_is_never_proposed_again_until_unticked(tmp_path):
    v = _vault(tmp_path)
    r = _run(v)["proposed"][0]
    _tick(v, r["id"], "reject")
    result = _run(v)
    assert result["feedback_changes"] == [f"{r['id']}: rejected"]
    assert r["id"] not in [p["id"] for p in result["proposed"]]
    text = (tmp_path / "CONVERGENCE.md").read_text()
    marker = f"<!--convergence:{r['id']}-->"
    assert text.count(marker) == 1                          # listed once...
    assert marker in text.split("## Rejected by you")[1]    # ...under Rejected only
    _tick(v, r["id"], "reject", mark=" ")                    # changed my mind
    result = _run(v)
    assert r["id"] in [p["id"] for p in result["proposed"]]


def test_accept_is_pinned_and_survives_regeneration(tmp_path):
    v = _vault(tmp_path)
    r = _run(v)["proposed"][0]
    _tick(v, r["id"], "accept")
    _run(v)
    _run(v)
    state = json.loads((tmp_path / ".magnum" / "convergence.json").read_text())
    assert state["feedback"][r["id"]]["decision"] == "accepted"
    assert state["feedback"][r["id"]]["by"] == "human"
    text = (tmp_path / "CONVERGENCE.md").read_text()
    accepted = text.split("## Accepted by you")[1]
    assert r["id"] in accepted and "- [x] accept" in accepted


def test_ticking_both_boxes_changes_nothing(tmp_path):
    v = _vault(tmp_path)
    r = _run(v)["proposed"][0]
    _tick(v, r["id"], "accept")
    _tick(v, r["id"], "reject")
    result = _run(v)
    assert "both accept and reject" in result["feedback_changes"][0]
    assert r["id"] not in json.loads(
        (tmp_path / ".magnum" / "convergence.json").read_text())["feedback"]


# --- your own notes elsewhere, read-only ----------------------------------------------

def test_an_existing_obsidian_vault_is_read_never_written(tmp_path):
    v = _vault(tmp_path / "vault")
    ext = tmp_path / "my-obsidian"
    (ext / "research").mkdir(parents=True)
    (ext / "research" / "MoE reading list.md").write_text(
        "---\ntags: [reading]\n---\nPapers on mixture of experts routing, "
        "router collapse and load balancing.\n")
    (ext / ".obsidian").mkdir()
    (ext / ".obsidian" / "workspace.md").write_text("mixture of experts router")
    before = _files(ext)
    result = _run(v, externals=[ext])
    assert _files(ext) == before
    labels = [l for r in result["proposed"] for l in r["labels"]]
    assert "research/MoE reading list.md (your notes)" in labels
    assert not any("workspace" in l for l in labels)          # app config ignored


# --- embedding backends -------------------------------------------------------------

def test_embedding_backend_uses_vectors_not_words(tmp_path):
    v = _vault(tmp_path)
    topic = {"router": [1, 0, 0], "paper": [0.9, 0.1, 0], "garden": [0, 0, 1]}

    def fake_embed(texts):
        out = []
        for t in texts:
            out.append(topic["garden"] if "Tomato" in t else
                       topic["paper"] if "Paper" in t else topic["router"])
        return out
    result = conv.converge(v, conv.EmbeddingBackend("local", fake_embed))
    (r,) = result["proposed"]
    assert set(r["groups"]) == {"router", "paper"} and r["shared"] == []


def test_local_backend_explains_missing_dependency(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_st(name, *a, **k):
        if name == "sentence_transformers":
            raise ImportError("no")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_st)
    with pytest.raises(RuntimeError, match=r"magnum-opus\[converge\]"):
        conv.local_backend()


def test_cli_openai_backend_asks_before_sending(tmp_path, monkeypatch, capsys):
    _vault(tmp_path)
    sent = []
    monkeypatch.setattr("magnum_opus.llm.OpenAICompatibleClient.embed",
                        lambda self, model, texts: sent.append(texts) or [])
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    rc = main(["converge", "--vault", str(tmp_path), "--backend", "openai",
               "--model", "nomic-embed-text", "--base-url", "http://127.0.0.1:9/v1"])
    assert rc == 1 and sent == [] and "Nothing sent" in capsys.readouterr().out
    assert main(["converge", "--vault", str(tmp_path), "--backend", "openai",
                 "--base-url", "http://127.0.0.1:9/v1", "--yes"]) == 2   # no --model


def test_cli_builtin_end_to_end(tmp_path, capsys):
    _vault(tmp_path)
    assert main(["converge", "--vault", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Compared 4 documents" in out
    assert "router ↔ paper" in (tmp_path / "CONVERGENCE.md").read_text() or \
           "paper ↔ router" in (tmp_path / "CONVERGENCE.md").read_text()


def test_convergence_file_is_generated_not_yours(tmp_path):
    from magnum_opus.reindex import reindex
    v = _vault(tmp_path)
    _run(v)
    assert reindex(v)["other_files"] == 0


def test_openai_embeddings_over_real_http():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from magnum_opus.llm import OpenAICompatibleClient
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            # Out of order on purpose: the client must use each row's index.
            rows = [{"index": i, "embedding": [float(i), 1.0]}
                    for i in range(len(body["input"]))][::-1]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": rows}).encode())

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = OpenAICompatibleClient(f"http://127.0.0.1:{srv.server_address[1]}/v1")
        vecs = c.embed("nomic-embed-text", ["a", "b", "c"], batch=2)
    finally:
        srv.shutdown()
    assert vecs == [[0.0, 1.0], [1.0, 1.0], [0.0, 1.0]]      # batch 2 restarts at 0
    assert [p for p, _ in seen] == ["/v1/embeddings", "/v1/embeddings"]
    assert seen[0][1] == {"model": "nomic-embed-text", "input": ["a", "b"]}
