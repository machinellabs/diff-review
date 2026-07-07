import json

from diff_review.formatter import format_json, format_markdown
from diff_review.schema import Issue, ReviewOutput


def sample_output() -> ReviewOutput:
    return ReviewOutput(
        verdict="request_changes",
        summary="Fix the cache expiry condition before merging.",
        issues=[
            Issue(
                severity="high",
                file="src/cache.py",
                description="TTL comparison excludes the boundary.",
                suggestion="Use >= so entries expire at the configured TTL.",
                evidence="+    if elapsed > ttl:",
            )
        ],
        highlights=["Clean separation between cache and storage."],
    )


def test_format_json_returns_structured_review() -> None:
    payload = json.loads(format_json(sample_output()))

    assert payload["verdict"] == "request_changes"
    assert payload["issues"][0]["file"] == "src/cache.py"
    assert payload["highlights"] == ["Clean separation between cache and storage."]


def test_format_markdown_includes_source_and_evidence() -> None:
    markdown = format_markdown(sample_output(), source="example.diff")

    assert "# diff-review: REQUEST CHANGES" in markdown
    assert "**Source:** example.diff" in markdown
    assert "TTL comparison excludes the boundary." in markdown
    assert "+    if elapsed > ttl:" in markdown
