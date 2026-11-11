"""Regression tests for llm/explain.generate_explanation.

A refusal (empty content list) or any API exception must degrade gracefully to
None rather than raising. No real network call is ever made: the Anthropic
client is monkeypatched.
"""

import llm.client as llm_client
import llm.explain as explain


class _FakeMessages:
    def __init__(self, behavior):
        self._behavior = behavior

    def create(self, **kwargs):
        return self._behavior()


class _FakeClient:
    def __init__(self, behavior):
        self._messages = _FakeMessages(behavior)

    # explain.py calls client.messages.create(...)
    def with_options(self, **kwargs):
        return self

    @property
    def messages(self):
        return self._messages


def _install_fake_client(monkeypatch, behavior):
    monkeypatch.setattr(explain, "ANTHROPIC_API_KEY", "dummy-key")
    monkeypatch.setattr(explain, "get_client", lambda: _FakeClient(behavior))


def test_get_client_uses_60_second_timeout(monkeypatch):
    """The shared factory must cap every Claude call at 60 s."""
    captured = {}

    def _fake_anthropic(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(llm_client.anthropic, "Anthropic", _fake_anthropic)
    llm_client.get_client()

    assert captured["timeout"] == 60


def test_generate_explanation_handles_api_exception(monkeypatch):
    def raise_error():
        raise RuntimeError("simulated overload / rate limit")

    _install_fake_client(monkeypatch, raise_error)
    assert explain.generate_explanation({}) is None


def test_generate_explanation_handles_empty_content(monkeypatch):
    class _EmptyMessage:
        content = []
        stop_reason = "refusal"

    _install_fake_client(monkeypatch, lambda: _EmptyMessage())
    assert explain.generate_explanation({}) is None


def test_generate_explanation_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(explain, "ANTHROPIC_API_KEY", "")
    # Should never even construct a client; make construction explode if it tries.
    monkeypatch.setattr(
        explain, "get_client",
        lambda: (_ for _ in ()).throw(AssertionError("must not call API")),
    )
    assert explain.generate_explanation({}) is None
