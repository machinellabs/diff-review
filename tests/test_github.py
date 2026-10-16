import httpx
import pytest

from diff_review.github import fetch_pr_diff


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500, 503])
def test_http_failures_are_reported_as_runtime_errors(monkeypatch, status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://api.github.com/repos/test/repo/pulls/1"))
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError):
        fetch_pr_diff("https://github.com/test/repo/pull/1")


def test_network_error_does_not_echo_credentials(monkeypatch):
    def fail(*args, **kwargs):
        raise httpx.ConnectError("sensitive request body")
    monkeypatch.setattr(httpx, "get", fail)
    with pytest.raises(RuntimeError, match="^Network error fetching PR diff.$"):
        fetch_pr_diff("https://github.com/test/repo/pull/1")


def test_success_sends_diff_accept_header_and_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-only")
    captured = {}
    def get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return httpx.Response(200, text="diff --git a/x b/x\n")
    monkeypatch.setattr(httpx, "get", get)
    assert fetch_pr_diff("https://github.com/test/repo/pull/42").startswith("diff --git")
    assert captured["url"] == "https://api.github.com/repos/test/repo/pulls/42"
    assert captured["headers"]["Accept"] == "application/vnd.github.v3.diff"
    assert captured["headers"]["Authorization"] == "Bearer test-only"
    assert captured["timeout"] == 15


@pytest.mark.parametrize("text", ["", " \n"])
def test_empty_response_is_not_a_review(monkeypatch, text):
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, text=text))
    with pytest.raises(RuntimeError, match="empty diff"):
        fetch_pr_diff("https://github.com/test/repo/pull/42")
