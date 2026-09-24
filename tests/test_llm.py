"""LLM strategy test with a fake Anthropic client (no live API)."""

import json
from types import SimpleNamespace

from server import LLMStrategy


class _FakeMessages:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text='{"ok": true, "source": "fake"}')]
        )


class _FakeAnthropic:
    def __init__(self, api_key=None):
        self.messages = _FakeMessages()


def test_llm_strategy_uses_fake_client_and_records_history(monkeypatch):
    fake = _FakeAnthropic()
    monkeypatch.setattr("anthropic.Anthropic", lambda api_key=None: fake)

    strategy = LLMStrategy(model="fake-model")
    schema = {
        "description": "demo",
        "outputSchema": {"type": "object"},
        "outputExample": {"ok": False},
    }
    parsed = json.loads(strategy.generate("demo_tool", schema, {"name": "prod"}))
    assert parsed == {"ok": True, "source": "fake"}
    assert fake.messages.calls[0]["model"] == "fake-model"
    assert "demo_tool" in fake.messages.calls[0]["messages"][0]["content"]

    strategy.generate("second", schema, {"b": 2})
    second_prompt = fake.messages.calls[1]["messages"][0]["content"]
    assert "demo_tool" in second_prompt
    assert "Previous tool calls" in second_prompt
