"""The CLI end to end (offline), and the OpenAI-compatible provider over real HTTP."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from magnum_opus.cli import main
from magnum_opus.llm import OpenAICompatibleClient, make_client


def test_dry_run_ingest_writes_notes_and_is_incremental(export_dir, tmp_path, capsys):
    vault = tmp_path / "vault"
    assert main(["ingest", "--export", str(export_dir), "--vault", str(vault),
                 "--dry-run"]) == 0
    notes = list((vault / "unsorted").glob("*.md"))
    assert len(notes) == 21
    text = notes[0].read_text()
    assert text.startswith("---\n") and "author: distiller" in text
    assert "sk-ant-" not in "".join(p.read_text() for p in notes)   # redacted
    capsys.readouterr()
    assert main(["ingest", "--export", str(export_dir), "--vault", str(vault),
                 "--dry-run"]) == 0
    assert "0 segments to distill" in capsys.readouterr().out


def test_inspect_and_estimate_spend_nothing(export_dir, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["inspect", "--export", str(export_dir)]) == 0
    assert main(["estimate", "--export", str(export_dir)]) == 0
    out = capsys.readouterr().out
    assert "4 conversations" in out and "ESTIMATED COST" in out


def test_openai_provider_requires_a_model(export_dir, tmp_path, capsys):
    rc = main(["ingest", "--export", str(export_dir), "--vault", str(tmp_path / "v"),
               "--llm", "openai", "--base-url", "http://127.0.0.1:9", "--yes"])
    assert rc == 2 and "--model is required" in capsys.readouterr().err


def test_missing_credentials_fail_before_the_cost_prompt(export_dir, tmp_path, capsys,
                                                         monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("prompted first"))
    rc = main(["ingest", "--export", str(export_dir), "--vault", str(tmp_path / "v")])
    assert rc == 2


def test_openai_provider_requires_a_base_url(monkeypatch):
    monkeypatch.delenv("MAGNUM_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="base URL"):
        make_client("openai")


# --- a real HTTP round trip against a stub OpenAI-compatible server ------------

class _Stub(BaseHTTPRequestHandler):
    seen = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Stub.seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                           "body": body})
        self.send_response(_Stub.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        reply = {"choices": [{"message": {"role": "assistant", "content": "hello"},
                              "finish_reason": "length"}]}
        self.wfile.write(json.dumps(reply if _Stub.status == 200 else
                                    {"error": "nope"}).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_url():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Stub.seen.clear()
    _Stub.status = 200
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def test_openai_compatible_round_trip(stub_url):
    c = OpenAICompatibleClient(stub_url, api_key="k")
    resp = c.messages.create(model="local-model", max_tokens=50,
                             messages=[{"role": "user", "content": "hi"}])
    assert resp.content[0].text == "hello" and resp.stop_reason == "max_tokens"
    (req,) = _Stub.seen
    assert req["path"] == "/v1/chat/completions" and req["auth"] == "Bearer k"
    assert req["body"] == {"model": "local-model", "max_tokens": 50,
                           "messages": [{"role": "user", "content": "hi"}]}


def test_openai_compatible_errors_are_readable(stub_url):
    _Stub.status = 500
    with pytest.raises(RuntimeError, match="HTTP 500"):
        OpenAICompatibleClient(stub_url).messages.create(
            model="m", max_tokens=5, messages=[{"role": "user", "content": "x"}])


def test_full_ingest_and_sort_through_a_local_model(export_dir, tmp_path, stub_url,
                                                    monkeypatch):
    """The whole pipeline on an OpenAI-compatible server, no Anthropic key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    distilled = {"topic": "grey routing", "summary": "Routing work.",
                 "decisions": [], "ideas": [], "open_loops":
                 [{"text": "rerun", "anchor": "I decided to change the approach"}],
                 "links": []}
    taxonomy = {"projects": [{"slug": "grey", "description": "model", "status": "active",
                              "next_action": "rerun ablation", "rank_reason": "live"}],
                "priority_order": ["grey"]}

    def handler(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        if "segmenting a long AI chat transcript" in prompt:
            text = "not json -> heuristic fallback"
        elif "deriving the project structure" in prompt:
            text = json.dumps(taxonomy)
        elif "Assign each note" in prompt:
            text = json.dumps({f"n{i}": "grey" for i in range(100)})
        else:
            text = json.dumps(distilled)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": text},
                                                  "finish_reason": "stop"}]}).encode())
    monkeypatch.setattr(_Stub, "do_POST", handler)

    vault = tmp_path / "vault"
    common = ["--llm", "openai", "--base-url", stub_url, "--model", "local"]
    assert main(["ingest", "--export", str(export_dir), "--vault", str(vault),
                 "--yes"] + common) == 0
    assert main(["sort", "--vault", str(vault)] + common) == 0
    notes = list((vault / "projects" / "grey" / "chats").glob("*.md"))
    assert len(notes) == 21
    assert "rerun ablation" in (vault / "QUEUE.md").read_text()
