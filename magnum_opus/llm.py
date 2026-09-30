"""Model providers.

Magnum Opus asks a model three things -- where a long thread's topics change,
what a segment's durable residue is, and how the whole body of work sorts into
projects. None of that is specific to one vendor, so the provider is a choice:

    anthropic   Claude via the official Anthropic SDK (the default).
    openai      any server speaking the OpenAI-compatible chat completions
                API: OpenAI itself, a local model under Ollama / llama.cpp /
                vLLM / LM Studio, or a self-hosted orchestration layer.

Both return a client with the same small surface the rest of the code uses:

    client.messages.create(model=..., max_tokens=..., messages=[...])
        -> response with .content (a list of blocks with .type == "text" and
           .text) and .stop_reason

The OpenAI-compatible client uses only the standard library, so choosing a
local model adds no dependency. Nothing here sends data anywhere the user did
not point it: the base URL is always explicit.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

PROVIDERS = ("anthropic", "openai")


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class Response:
    content: list = field(default_factory=list)
    stop_reason: str = ""


class _Messages:
    def __init__(self, client):
        self._client = client

    def create(self, model: str, max_tokens: int, messages: list, **_):
        return self._client._chat(model, max_tokens, messages)


class OpenAICompatibleClient:
    """Minimal client for POST {base_url}/chat/completions."""

    def __init__(self, base_url: str, api_key: str = "", timeout: float = 600.0):
        if not base_url:
            raise RuntimeError(
                "The openai provider needs a base URL: pass --base-url or set "
                "MAGNUM_BASE_URL (e.g. http://localhost:11434/v1 for Ollama).")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.messages = _Messages(self)

    def _chat(self, model: str, max_tokens: int, messages: list) -> Response:
        body = json.dumps({"model": model, "max_tokens": max_tokens,
                           "messages": messages}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"{self.base_url} returned HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Could not reach {self.base_url}: {e.reason}") from e
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise RuntimeError(f"Unexpected response shape from {self.base_url}") from e
        finish = choice.get("finish_reason") or ""
        # Map the OpenAI finish reason onto the names the callers report.
        stop = {"stop": "end_turn", "length": "max_tokens"}.get(finish, finish)
        return Response(content=[TextBlock(text)], stop_reason=stop)


def anthropic_client():
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError('The anthropic provider needs: pip install "magnum-opus[llm]"') from e
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    # Identity-linked API keys must declare the workspace they act in.
    headers = {}
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return anthropic.Anthropic(default_headers=headers or None)


def make_client(provider: str = "anthropic", base_url: str | None = None,
                api_key: str | None = None):
    """Build a client for a provider. Settings fall back to MAGNUM_* env vars."""
    provider = provider or os.environ.get("MAGNUM_PROVIDER", "anthropic")
    if provider == "anthropic":
        return anthropic_client()
    if provider == "openai":
        return OpenAICompatibleClient(
            base_url or os.environ.get("MAGNUM_BASE_URL", ""),
            api_key if api_key is not None else os.environ.get("MAGNUM_API_KEY", ""))
    raise ValueError(f"Unknown provider {provider!r}; choose one of {PROVIDERS}")


def response_text(resp) -> str:
    """All text blocks of a response, joined."""
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
