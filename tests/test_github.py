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
