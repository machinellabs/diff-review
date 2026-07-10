import json

import pytest

from diff_review import nodes, providers
from diff_review.formatter import print_token_usage
from diff_review.nodes import review_chunks, synthesize
from diff_review.providers import (
    Completion,
    Pricing,
    ProviderError,
    configure,
    get_provider,
    provider_name,
)


class FakeProvider:
    """Scripted provider: returns each queued response in order."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.pricing = None

    def complete(self, system: str, user: str, max_tokens: int = 8192) -> Completion:
        self.calls.append((system, user))
        return Completion(text=self.responses.pop(0), input_tokens=10, output_tokens=5)


@pytest.fixture(autouse=True)
def reset_provider_config():
    configure()
    yield
    configure()


def use_fake(monkeypatch, responses: list[str]) -> FakeProvider:
    fake = FakeProvider(responses)
    monkeypatch.setattr(nodes, "get_provider", lambda: fake)
    return fake


# --- provider selection ---


def test_default_provider_is_anthropic() -> None:
    assert provider_name() == "anthropic"


def test_env_var_selects_provider(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_REVIEW_PROVIDER", "openai")
    assert provider_name() == "openai"


def test_configure_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("DIFF_REVIEW_PROVIDER", "openai")
    configure(provider="anthropic")
    assert provider_name() == "anthropic"


def test_unknown_provider_raises() -> None:
    configure(provider="llamacpp")
    with pytest.raises(ProviderError, match="Unknown provider"):
        get_provider()


def test_openai_provider_requires_a_model(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    configure(provider="openai", base_url="http://localhost:11434/v1")
    with pytest.raises(ProviderError, match="No model set"):
        get_provider()


def test_openai_provider_requires_key_without_base_url(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    configure(provider="openai", model="gpt-5")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        get_provider()


def test_openai_provider_accepts_base_url_without_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    configure(
        provider="openai", model="qwen3.6:27b", base_url="http://localhost:11434/v1"
    )
    provider = get_provider()
    assert provider.model == "qwen3.6:27b"
    assert provider.pricing is None


def test_get_provider_caches_until_reconfigured(monkeypatch) -> None:
    configure(
        provider="openai", model="qwen3.6:27b", base_url="http://localhost:11434/v1"
    )
    assert get_provider() is get_provider()
    configure(
        provider="openai", model="other", base_url="http://localhost:11434/v1"
    )
    assert get_provider().model == "other"


# --- JSON retry ---


def test_complete_json_passes_through_valid_json(monkeypatch) -> None:
    fake = use_fake(monkeypatch, ['{"issues": []}'])
    text, in_tokens, out_tokens = nodes._complete_json("sys", "user")
    assert json.loads(text) == {"issues": []}
    assert (in_tokens, out_tokens) == (10, 5)
    assert len(fake.calls) == 1


def test_complete_json_retries_once_on_bad_json(monkeypatch) -> None:
    fake = use_fake(monkeypatch, ["Sure! Here is the review:", '{"issues": []}'])
    text, in_tokens, out_tokens = nodes._complete_json("sys", "user")
    assert json.loads(text) == {"issues": []}
    # token counts sum across both attempts
    assert (in_tokens, out_tokens) == (20, 10)
    assert len(fake.calls) == 2
    assert "was not valid JSON" in fake.calls[1][1]


def test_complete_json_returns_bad_text_after_failed_retry(monkeypatch) -> None:
    fake = use_fake(monkeypatch, ["not json", "still not json"])
    text, _, _ = nodes._complete_json("sys", "user")
    assert text == "still not json"
    assert len(fake.calls) == 2


# --- pipeline runs end-to-end on a fake provider ---


def test_review_and_synthesis_with_fake_provider(monkeypatch) -> None:
    review_json = json.dumps(
        {
            "issues": [
                {
                    "severity": "high",
                    "file": "app.py",
                    "description": "SQL injection",
                    "suggestion": "Use parameters",
                    "evidence": '+query = f"SELECT {user_input}"',
                }
            ],
            "highlights": [],
        }
    )
    synthesis_json = json.dumps(
        {
            "verdict": "request_changes",
            "summary": "One exploitable injection.",
            "issues": json.loads(review_json)["issues"],
            "highlights": [],
        }
    )
    use_fake(monkeypatch, [review_json, synthesis_json])

    state = {
        "file_chunks": ["diff --git a/app.py b/app.py\n+bad line\n"],
        "security": False,
    }
    state.update(review_chunks(state))
    result = synthesize(state)

    assert result["output"].verdict == "request_changes"
    usage = result["token_usage"]
    assert usage["review_input_tokens"] == 10
    assert usage["synthesis_input_tokens"] == 10


# --- token usage printing ---


def _usage() -> dict:
    return {
        "review_input_tokens": 1000,
        "review_output_tokens": 100,
        "synthesis_input_tokens": 200,
        "synthesis_output_tokens": 50,
    }


def test_token_usage_omits_cost_when_pricing_unknown(capsys) -> None:
    print_token_usage(_usage(), pricing=None)
    out = capsys.readouterr().out
    assert "1,200 in" in out
    assert "$" not in out


def test_token_usage_uses_provider_pricing(capsys) -> None:
    print_token_usage(_usage(), pricing=Pricing(1 / 1_000_000, 2 / 1_000_000))
    out = capsys.readouterr().out
    assert "$0.0015" in out


def test_token_usage_default_keeps_claude_rates(capsys) -> None:
    print_token_usage(_usage())
    out = capsys.readouterr().out
    assert "$0.0059" in out
