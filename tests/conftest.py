import json
from types import SimpleNamespace

import pytest

from diff_review import providers

_PROVIDER_ENV = (
    "DIFF_REVIEW_PROVIDER", "DIFF_REVIEW_TIMEOUT", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OLLAMA_HOST",
)


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    # Isolate tests from the developer's real credentials and provider setup.
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only")
    providers.configure()

    def blocked():
        raise AssertionError("Tests must mock the model client")
    monkeypatch.setattr(providers, "_anthropic_client", blocked)
    yield
    providers.configure()


@pytest.fixture
def patch_text():
    def make(lines=None, path="app.py"):
        lines = lines if lines is not None else ["+safe = True"]
        old = sum(line.startswith((" ", "-")) for line in lines)
        new = sum(line.startswith((" ", "+")) for line in lines)
        return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                f"@@ -{1 if old else 0},{old} +{1 if new else 0},{new} @@\n"
                + "\n".join(lines) + "\n")
    return make


@pytest.fixture
def fake_model(monkeypatch):
    calls = []
    def install(responder=None):
        def create(**kwargs):
            calls.append(kwargs)
            content = kwargs["messages"][0]["content"]
            if responder:
                payload = responder(content, len(calls))
            elif content.startswith("Review this diff:"):
                payload = {"issues": [], "highlights": []}
            else:
                payload = {"verdict": "approve", "summary": "No issues.", "issues": [], "highlights": []}
            if isinstance(payload, Exception):
                raise payload
            if isinstance(payload, SimpleNamespace):
                return payload
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=payload if isinstance(payload, str) else json.dumps(payload))],
                stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
        messages = SimpleNamespace(create=create)
        client = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
        monkeypatch.setattr(providers, "_anthropic_client", lambda: client)
        return calls
    return install
