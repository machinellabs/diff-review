import json
from types import SimpleNamespace

import pytest

from diff_review import cli, nodes, providers
from diff_review.formatter import print_token_usage
from diff_review.providers import (
    DEFAULT_ANTHROPIC_MODEL,
    Pricing,
    ProviderError,
    configure,
    get_provider,
    provider_name,
)

openai = pytest.importorskip("openai")


def _chat(text, finish="stop", refusal=None):
    message = SimpleNamespace(content=text, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _bad_request(message):
    response = SimpleNamespace(status_code=400, headers={}, request=None)
    return openai.BadRequestError(message, response=response, body=None)


def fake_openai(provider, responder):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        result = responder(kwargs, len(calls))
        if isinstance(result, Exception):
            raise result
        return result

    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return calls


def local(model="qwen3.8:27b-q4_K_M", provider="ollama", **kwargs):
    configure(provider=provider, model=model, **kwargs)
    return get_provider()


# --- selection and presets ---


def test_default_provider_is_anthropic_on_current_sonnet():
    assert provider_name() == "anthropic"
    provider = get_provider()
    assert provider.model == DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5-5"
    assert provider.pricing == Pricing(2.0 / 1e6, 10.0 / 1e6)


def test_env_var_selects_provider_and_flag_overrides(monkeypatch):
    monkeypatch.setenv("DIFF_REVIEW_PROVIDER", "Ollama")
    assert provider_name() == "ollama"
    configure(provider="anthropic")
    assert provider_name() == "anthropic"


def test_unknown_provider_raises():
    configure(provider="llamacpp")
    with pytest.raises(ProviderError, match="Unknown provider.*ollama"):
        get_provider()


def test_anthropic_requires_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY.*--provider ollama"):
        get_provider()
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    configure()
    assert get_provider().name == "anthropic"


def test_unknown_anthropic_model_has_no_cost_estimate(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-future-9")
    assert get_provider().pricing is None


def test_ollama_preset_needs_no_key_or_url():
    provider = local()
    assert provider.name == "ollama"
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.pricing is None
    assert provider.default_concurrency == 1


@pytest.mark.parametrize("host,expected", [
    ("0.0.0.0:11434", "http://localhost:11434/v1"),
    ("http://studio.local:11434/", "http://studio.local:11434/v1"),
    ("https://gpu-box:9999/v1", "https://gpu-box:9999/v1"),
])
def test_ollama_host_env_is_honored(monkeypatch, host, expected):
    monkeypatch.setenv("OLLAMA_HOST", host)
    assert local().base_url == expected


def test_lmstudio_preset_and_base_url_override():
    assert local(provider="lmstudio").base_url == "http://localhost:1234/v1"
    assert local(base_url="http://127.0.0.1:8080/v1").base_url == "http://127.0.0.1:8080/v1"


def test_local_presets_require_a_model():
    configure(provider="ollama")
    with pytest.raises(ProviderError, match="No model set for the ollama provider.*ollama list"):
        get_provider()


def test_openai_requires_key_unless_a_server_url_is_given():
    configure(provider="openai", model="gpt-5")
    with pytest.raises(ProviderError, match="OPENAI_API_KEY.*--provider ollama"):
        get_provider()
    provider = local(provider="openai", model="m", base_url="http://localhost:8000/v1")
    assert provider.name == "openai" and provider.default_concurrency == 1


def test_hosted_openai_keeps_parallel_requests(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    assert local(provider="openai", model="gpt-5").default_concurrency == 8


def test_provider_is_cached_until_reconfigured():
    assert get_provider() is get_provider()
    assert local(model="other").model == "other"


# --- Anthropic request shape ---


def test_current_models_use_server_side_refusal_fallback(fake_model):
    calls = fake_model()
    get_provider().complete("sys", "Review this diff:\n")
    assert calls[0]["fallbacks"] == "default"
    assert calls[0]["betas"] == ["server-side-fallback-2026-07-01"]
    assert calls[0]["model"] == "claude-sonnet-5-5"
    assert calls[0]["max_tokens"] == 16_000


@pytest.mark.parametrize("env", [{"ANTHROPIC_MODEL": "claude-haiku-4-5"},
                                 {"ANTHROPIC_BASE_URL": "http://proxy:8080"}])
def test_fallback_is_off_for_old_models_and_custom_endpoints(fake_model, monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    calls = fake_model()
    get_provider().complete("sys", "Review this diff:\n")
    assert "fallbacks" not in calls[0] and "betas" not in calls[0]


def test_anthropic_refusal_stops_without_retry(fake_model, patch_text):
    refusal = SimpleNamespace(content=[], stop_reason="refusal",
                              stop_details=SimpleNamespace(category="cyber"),
                              usage=SimpleNamespace(input_tokens=10, output_tokens=0))
    calls = fake_model(lambda content, number: refusal)
    with pytest.raises(RuntimeError, match=r"declined the request.*refusal: cyber"):
        nodes._review_chunk(patch_text())
    assert len(calls) == 1


def test_thinking_blocks_are_ignored(fake_model, patch_text):
    response = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text='{"issues": [], "highlights": []}')],
        stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    fake_model(lambda content, number: response)
    raw, _, _ = nodes._review_chunk(patch_text())
    assert json.loads(raw)["issues"] == []


# --- OpenAI-compatible request handling ---


def test_structured_output_degrades_and_remembers():
    provider = local()
    def responder(kw, n):
        mode = kw.get("response_format", {}).get("type")
        if mode == "json_schema":
            return _bad_request("json_schema is not supported by this server")
        if mode == "json_object":
            return _bad_request("response_format json_object unsupported")
        return _chat("{}")
    calls = fake_openai(provider, responder)
    assert provider.complete("s", "u", json_schema={"type": "object"}).text == "{}"
    assert [c.get("response_format", {}).get("type") for c in calls] == ["json_schema", "json_object", None]
    provider.complete("s", "u", json_schema={"type": "object"})
    assert len(calls) == 4  # the degraded mode is remembered


def test_rejected_json_mode_without_a_schema_terminates():
    provider = local()
    calls = fake_openai(provider, lambda kw, n: _bad_request("response_format unsupported")
                        if "response_format" in kw else _chat("{}"))
    assert provider.complete("s", "u").text == "{}"
    assert [c.get("response_format", {}).get("type") for c in calls] == ["json_object", None]


def test_concurrent_rejections_degrade_once():
    import threading
    provider = local()
    barrier = threading.Barrier(4)
    def responder(kw, n):
        if kw.get("response_format", {}).get("type") == "json_schema":
            barrier.wait(timeout=5)
            return _bad_request("json_schema unsupported")
        return _chat("{}")
    fake_openai(provider, responder)
    errors = []
    def run():
        try:
            provider.complete("s", "u", json_schema={"type": "object"})
        except Exception as exc:  # pragma: no cover - the assertion reports it
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and provider._format_modes == ["json_object", None]


def test_review_requests_send_the_review_schema(patch_text):
    from diff_review.schema import FILE_REVIEW_SCHEMA
    provider = local()
    calls = fake_openai(provider, lambda kw, n: _chat('{"issues": [], "highlights": []}'))
    nodes._review_chunk(patch_text())
    assert calls[0]["response_format"]["json_schema"]["schema"] == FILE_REVIEW_SCHEMA
    assert calls[0]["model"] == "qwen3.8:27b-q4_K_M"


def test_max_completion_tokens_fallback():
    provider = local(provider="openai", model="m", base_url="http://localhost:1/v1")
    calls = fake_openai(provider, lambda kw, n: _bad_request("use max_completion_tokens instead")
                        if "max_tokens" in kw else _chat("{}"))
    provider.complete("s", "u")
    assert calls[1]["max_completion_tokens"] == 16_000


@pytest.mark.parametrize("response,complete,refusal", [
    (_chat("{}", finish="length"), False, None),
    (_chat("{}", finish=None), True, None),
    (_chat("   "), False, None),
    (_chat(None, refusal="no"), False, "content_filter"),
    (SimpleNamespace(choices=[], usage=None), False, None),
])
def test_openai_completion_states(response, complete, refusal):
    provider = local()
    fake_openai(provider, lambda kw, n: response)
    result = provider.complete("s", "u")
    assert (result.complete, result.refusal) == (complete, refusal)


def test_connection_error_names_the_server_without_echoing_content():
    provider = local()
    fake_openai(provider, lambda kw, n: openai.APIConnectionError(request=None))
    with pytest.raises(ProviderError, match="localhost:11434.*is the server running"):
        provider.complete("s", "SECRET diff body")


def test_missing_local_model_hint():
    provider = local()
    response = SimpleNamespace(status_code=404, headers={}, request=None)
    fake_openai(provider, lambda kw, n: openai.NotFoundError("model 'x' echo SECRET", response=response, body=None))
    with pytest.raises(ProviderError) as exc:
        provider.complete("s", "u")
    assert "HTTP 404" in str(exc.value) and "installed" in str(exc.value)
    assert "SECRET" not in str(exc.value)


# --- local-model output tolerance through the real pipeline ---


@pytest.mark.parametrize("wrap", [
    "<think>The diff looks fine.</think>\n{body}",
    "Here is the review:\n```json\n{body}\n```",
    "Sure! {body} Hope that helps.",
])
def test_local_models_wrapped_json_is_accepted(patch_text, wrap):
    body = json.dumps({"issues": [{"severity": "high", "file": "app.py", "description": "eval",
                                   "suggestion": "remove", "evidence": "+eval(user_input)"}],
                       "highlights": []})
    provider = local()
    fake_openai(provider, lambda kw, n: _chat(wrap.replace("{body}", body)))
    raw, _, _ = nodes._review_chunk(patch_text(["+eval(user_input)"]))
    assert json.loads(raw)["issues"][0]["evidence"] == "+eval(user_input)"


def test_full_pipeline_on_a_local_model(patch_text):
    provider = local()

    def responder(kwargs, number):
        if kwargs["messages"][1]["content"].startswith("Review this diff:"):
            return _chat('{"issues": [], "highlights": ["clear naming"]}')
        return _chat('{"verdict": "approve", "summary": "Fine.", "issues": [], "highlights": []}')

    calls = fake_openai(provider, responder)
    from diff_review.graph import build_graph
    result = build_graph().invoke({"diff": patch_text() + patch_text(path="b.py"), "file_chunks": [],
                                   "file_reviews": [], "output": None, "security": False})
    assert result["output"].verdict == "approve"
    assert result["output"].coverage.complete
    assert len(calls) == 3


def test_concurrency_defaults_to_provider_and_can_be_overridden(patch_text, monkeypatch):
    seen = []

    class Pool:
        def __init__(self, max_workers):
            seen.append(max_workers)
            raise RuntimeError("stop")

    monkeypatch.setattr(nodes, "ThreadPoolExecutor", Pool)
    local()
    chunks = [patch_text()] * 5
    for state in ({"file_chunks": chunks}, {"file_chunks": chunks, "concurrency": 3}):
        with pytest.raises(RuntimeError, match="stop"):
            nodes.review_chunks(state)
    assert seen == [1, 3]


# --- CLI ---


def _run_cli(monkeypatch, capsys, argv, stdin):
    import io
    monkeypatch.setattr("sys.argv", ["diff-review", *argv])
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code, capsys.readouterr()


def test_cli_reports_provider_setup_errors_cleanly(monkeypatch, capsys, patch_text):
    code, out = _run_cli(monkeypatch, capsys, ["--provider", "ollama"], patch_text())
    assert code == 1
    assert "Error: No model set for the ollama provider" in out.err
    assert "Traceback" not in out.err


@pytest.mark.parametrize("value", ["0", "-2", "two"])
def test_cli_rejects_bad_concurrency_as_usage_error(monkeypatch, capsys, patch_text, value):
    code, out = _run_cli(monkeypatch, capsys, ["--concurrency", value], patch_text())
    assert code == 2 and "--concurrency" in out.err


def test_cli_passes_provider_flags_and_skips_cost_for_local(monkeypatch, capsys, patch_text):
    seen = {}

    def responder(kwargs, number):
        seen.setdefault("model", kwargs["model"])
        if kwargs["messages"][1]["content"].startswith("Review this diff:"):
            return _chat('{"issues": [], "highlights": []}')
        return _chat('{"verdict": "approve", "summary": "Fine.", "issues": [], "highlights": []}')

    real_get = providers.get_provider

    def patched():
        provider = real_get()
        if not hasattr(provider, "_faked"):
            fake_openai(provider, responder)
            provider._faked = True
        return provider

    monkeypatch.setattr(cli, "get_provider", patched)
    monkeypatch.setattr(nodes, "get_provider", patched)
    monkeypatch.setattr("sys.argv", ["diff-review", "--provider", "lmstudio", "--model", "qwen-local"])
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(patch_text()))
    cli.main()
    out = capsys.readouterr().out
    assert seen["model"] == "qwen-local"
    assert "APPROVE" in out and "Tokens" in out and "$" not in out


# --- token usage printing ---


def _usage():
    return {"review_input_tokens": 1000, "review_output_tokens": 100,
            "synthesis_input_tokens": 200, "synthesis_output_tokens": 50}


def test_token_usage_omits_cost_when_pricing_unknown(capsys):
    print_token_usage(_usage())
    out = capsys.readouterr().out
    assert "1,200 in" in out and "$" not in out


def test_token_usage_uses_provider_pricing(capsys):
    print_token_usage(_usage(), pricing=Pricing(2.0 / 1e6, 10.0 / 1e6))
    assert "$0.0039" in capsys.readouterr().out
