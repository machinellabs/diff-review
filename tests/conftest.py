import json
from types import SimpleNamespace

import pytest

from diff_review import nodes


@pytest.fixture(autouse=True)
def no_live_model(monkeypatch):
    def blocked():
        raise AssertionError("Tests must mock the model client")
    monkeypatch.setattr(nodes, "_get_client", blocked)


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
        monkeypatch.setattr(nodes, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
        return calls
    return install
