from diff_review.nodes import parse_diff
from diff_review.schema import Issue
from diff_review.security import (
    SECURITY_RULES,
    merge_secret_issues,
    rules_prompt,
    scan_only_output,
    scan_secrets,
)


def make_diff(added_line: str, path: str = "src/config.py") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,3 @@\n"
        " unchanged = True\n"
        f"+{added_line}\n"
    )


def test_rules_prompt_lists_every_rule() -> None:
    prompt = rules_prompt()
    for rule_id, _, _ in SECURITY_RULES:
        assert rule_id in prompt


def test_scan_finds_aws_key_with_file_and_rule() -> None:
    issues = scan_secrets(make_diff('aws_id = "AKIAIOSFODNN7EXAMPL0"'))

    assert len(issues) == 1
    assert issues[0].severity == "high"
    assert issues[0].file == "src/config.py"
    assert issues[0].rule == "SEC-SECRET"


def test_scan_finds_private_key_header() -> None:
    issues = scan_secrets(make_diff("-----BEGIN RSA PRIVATE KEY-----"))

    assert len(issues) == 1
    assert "Private key" in issues[0].description


def test_scan_flags_generic_credential_assignment_as_medium() -> None:
    issues = scan_secrets(make_diff('password = "hunter2hunter2"'))

    assert len(issues) == 1
    assert issues[0].severity == "medium"


def test_scan_matches_credential_keywords_in_compound_names() -> None:
    for line in (
        'DB_PASSWORD = "supersecret123"',
        'JWT_SECRET = "supersecret123"',
        'GITHUB_DEPLOY_TOKEN = "abcdefghijkl"',
    ):
        issues = scan_secrets(make_diff(line))
        assert len(issues) == 1, line
        assert issues[0].severity == "medium"


def test_scan_ignores_removed_lines_and_context() -> None:
    diff = (
        "diff --git a/src/config.py b/src/config.py\n"
        "--- a/src/config.py\n"
        "+++ b/src/config.py\n"
        "@@ -1,2 +1,1 @@\n"
        '-password = "hunter2hunter2"\n'
        ' token = "kept-around-value"\n'
    )

    assert scan_secrets(diff) == []


def test_scan_ignores_placeholders_and_env_lookups() -> None:
    for line in (
        'api_key = "your-key-here"',
        'password = "<fill-me-in>"',
        'token = os.environ["TOKEN"]',
    ):
        assert scan_secrets(make_diff(line)) == []


def test_scan_dedupes_identical_lines() -> None:
    line = 'secret = "abcdefghijklmnop"'
    diff = make_diff(line) + f"+{line}\n"

    assert len(scan_secrets(diff)) == 1


def test_merge_skips_findings_the_model_already_reported() -> None:
    evidence = '+aws_id = "AKIAIOSFODNN7EXAMPL0"'
    model_issue = Issue(
        severity="high",
        file="src/config.py",
        description="AWS key in diff",
        suggestion="Rotate it.",
        evidence=evidence,
    )
    scanner_issue = model_issue.model_copy(update={"rule": "SEC-SECRET"})

    merged = merge_secret_issues([model_issue], [scanner_issue])

    assert merged == [model_issue]


def test_scanner_covers_chunks_the_model_review_skips() -> None:
    # A lockfile-only diff produces no reviewable chunks, but the secrets
    # scan must still see it.
    diff = make_diff(
        '"resolved": "https://ghp_x7K9mQ2vLpR4tW8sNzC5bJfY3hD6aE1gU0oV@registry.npmjs.org/pkg"',
        path="package-lock.json",
    )

    assert parse_diff({"diff": diff})["file_chunks"] == []
    findings = scan_secrets(diff)
    assert len(findings) == 1
    assert findings[0].severity == "high"


def test_scan_only_output_verdicts() -> None:
    high = Issue(
        severity="high",
        file="package-lock.json",
        description="GitHub token committed in the diff",
        suggestion="Rotate it.",
        evidence="+ghp_...",
        rule="SEC-SECRET",
    )
    medium = high.model_copy(update={"severity": "medium"})

    assert scan_only_output([high]).verdict == "request_changes"
    assert scan_only_output([medium]).verdict == "needs_discussion"
    assert scan_only_output([]).verdict == "approve"
    assert scan_only_output([high]).issues == [high]


def test_merge_prepends_new_findings() -> None:
    model_issue = Issue(
        severity="low",
        file="app.py",
        description="Style nit",
        suggestion="Rename.",
        evidence="+x = 1",
    )
    scanner_issue = Issue(
        severity="high",
        file="src/config.py",
        description="AWS key in diff",
        suggestion="Rotate it.",
        evidence='+aws_id = "AKIAIOSFODNN7EXAMPL0"',
        rule="SEC-SECRET",
    )

    merged = merge_secret_issues([model_issue], [scanner_issue])

    assert merged == [scanner_issue, model_issue]
