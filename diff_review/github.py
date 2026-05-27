import os
import re
import httpx

_PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)


def fetch_pr_diff(url: str) -> str:
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            f"Could not parse GitHub PR URL: {url!r}\n"
            "Expected format: https://github.com/owner/repo/pull/123"
        )

    owner = match.group("owner")
    repo = match.group("repo")
    number = match.group("number")
    api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"

    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = httpx.get(api_url, headers=headers, follow_redirects=True, timeout=15)
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error fetching PR diff: {e}") from e

    if response.status_code == 401:
        raise RuntimeError("GitHub authentication failed. Check your GITHUB_TOKEN.")
    if response.status_code == 403:
        raise RuntimeError(
            "GitHub request forbidden. You may have hit the rate limit — "
            "set GITHUB_TOKEN to raise it."
        )
    if response.status_code == 404:
        raise RuntimeError(
            f"PR not found: {url}\n"
            "Check the URL is correct. For private repos, set GITHUB_TOKEN."
        )
    response.raise_for_status()

    diff = response.text
    if not diff.strip():
        raise RuntimeError(f"PR #{number} returned an empty diff.")

    return diff
