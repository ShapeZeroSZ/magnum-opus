"""Shared test helpers. No network and no API key: models are faked."""
import json
from pathlib import Path

import pytest

from magnum_opus.llm import Response, TextBlock

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    """Stands in for any provider client. `replies` is a list of strings or
    callables(prompt) -> str, consumed in order; the last one repeats."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.messages = self

    def create(self, model, max_tokens, messages, **_):
        prompt = messages[-1]["content"]
        self.prompts.append({"model": model, "prompt": prompt})
        r = self.replies[0] if len(self.replies) == 1 else self.replies.pop(0)
        text = r(prompt) if callable(r) else r
        return Response(content=[TextBlock(text)], stop_reason="end_turn")


@pytest.fixture
def fake():
    return FakeClient


@pytest.fixture
def export_dir():
    return FIXTURES / "export"


def js(obj) -> str:
    return json.dumps(obj)
