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


def test_markdown_reports_incomplete_coverage_and_masks_credentials():
    from diff_review.schema import Coverage
    output = sample_output()
    key = "AKIA" + "Q7M2" * 4
    output.summary = f"Detected {key}"
    output.coverage = Coverage(reviewed_files=["app.py"], skipped_files=["poetry.lock"], reviewed_chunks=2)
    markdown = format_markdown(output)
    assert key not in markdown
    assert "Incomplete" in markdown
    assert "poetry.lock" in markdown
    assert "2 chunks" in markdown


def test_markdown_evidence_cannot_close_its_own_fence():
    output = sample_output()
    output.issues[0].evidence = "+```\n+payload"
    markdown = format_markdown(output)
    assert "**Evidence:**\n````\n+```\n+payload\n````" in markdown


def test_terminal_keeps_redaction_marker_and_literal_brackets(capsys, monkeypatch):
    from rich.console import Console
    from diff_review import formatter
    from diff_review.formatter import print_review
    monkeypatch.setattr(formatter, "console", Console(width=240))
    output = sample_output()
    output.issues[0].evidence = '+password = "[REDACTED]" [red]literal[/red]'
    print_review(output)
    printed = capsys.readouterr().out
    assert "[REDACTED]" in printed
    assert "[red]" in printed
