import pytest

from diff_review.nodes import parse_diff
from diff_review.schema import Issue, ReviewOutput
from diff_review.security import (
    SECURITY_RULES,
    merge_secret_issues,
    rules_prompt,
    scan_only_output,
    scan_secrets,
    redact_secrets,
    redact_output,
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

    assert merged == [scanner_issue]


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
    assert scan_only_output([]).verdict == "needs_discussion"
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


# Regression cases from the repository audit. These are synthetic credentials.
KEY = "AKIA" + "Q7M2" * 4


@pytest.mark.parametrize("line", [
    f'aws_id = "{KEY}" # example integration',
    f'<input value="{KEY}">',
    f'aws_id = os.environ.get("AWS_ID", "{KEY}")',
    f'aws_id = "{KEY}" # xxx placeholder',
    '"password": "supersecret123"',
    "'DB_PASSWORD': 'supersecret123'",
    'password = "myexamplepass123"',
])
def test_surrounding_syntax_does_not_hide_credentials(line):
    found = scan_secrets(make_diff(line))
    assert found
    assert KEY not in found[0].evidence
    assert "supersecret123" not in found[0].evidence
    assert "[REDACTED]" in found[0].evidence


@pytest.mark.parametrize("line", [
    'password = "your-password"', 'password = "<fill-me-in>"',
    'password = "placeholder"', 'password = "${PASSWORD}"',
    'password = "[REDACTED]"',
])
def test_only_placeholder_values_are_ignored(line):
    assert scan_secrets(make_diff(line)) == []


def test_redaction_covers_context_removed_lines_and_multiple_values():
    diff = make_diff(f'key = "{KEY}"; password = "supersecret123"')
    diff += f'-old = "{KEY}"\n kept = "{KEY}"\n'
    safe = redact_secrets(diff)
    assert KEY not in safe
    assert "supersecret123" not in safe
    assert redact_secrets(safe) == safe
    assert "+++ b/src/config.py" in safe


def test_private_key_body_is_redacted_and_patch_structure_preserved():
    diff = make_diff("-----BEGIN RSA PRIVATE KEY-----")
    diff += "+SYNTHETICKEYMATERIAL\n+-----END RSA PRIVATE KEY-----\n+safe = 1\n"
    safe = redact_secrets(diff)
    assert "SYNTHETICKEYMATERIAL" not in safe
    assert "+[REDACTED]" in safe
    assert "+safe = 1" in safe
    assert len(diff.splitlines()) == len(safe.splitlines())
    assert "PRIVATE KEY" in safe


def test_inline_private_key_is_redacted():
    text = r'key = "-----BEGIN PRIVATE KEY-----\nSYNTHETICKEYMATERIAL\n-----END PRIVATE KEY-----"'
    assert "SYNTHETICKEYMATERIAL" not in redact_secrets(text)


def test_output_redacts_summary_suggestions_and_highlights():
    output = ReviewOutput(verdict="request_changes", summary=f"Found {KEY}", issues=[], highlights=[f"{KEY}"])
    assert KEY not in redact_output(output).model_dump_json()


def test_merge_keeps_separate_files_and_scanner_severity():
    first = scan_secrets(make_diff(f'aws_id = "{KEY}"'))[0]
    second = first.model_copy(update={"file": "other.py"})
    downgraded = first.model_copy(update={"severity": "low", "rule": ""})
    merged = merge_secret_issues([downgraded, second], [first])
    assert merged == [first, second]


def test_scanner_understands_quoted_paths():
    diff = make_diff(f'aws_id = "{KEY}"').replace("+++ b/src/config.py", '+++ "b/folder/config file.py"')
    assert scan_secrets(diff)[0].file == "folder/config file.py"


def test_scanner_does_not_downgrade_model_or_hide_different_rule():
    scanned = scan_secrets(make_diff('password = "supersecret123"'))[0]
    high = scanned.model_copy(update={"severity": "high", "description": "Privileged credential"})
    injection = scanned.model_copy(update={"rule": "SEC-INJ", "description": "Injection on same line"})
    merged = merge_secret_issues([high, injection], [scanned])
    assert merged[0].severity == "high"
    assert injection in merged
