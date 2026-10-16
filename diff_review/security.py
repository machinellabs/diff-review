"""Security review support: the prompt ruleset and a local secrets pre-scan.

The ruleset steers Claude toward security-relevant findings when --security
is set. The secrets scan runs locally with regexes, so hardcoded credentials
are flagged even if the model misses them and without sending anything extra
over the network.
"""

import re

from .diff import file_path, file_sections
from .schema import Coverage, Issue, ReviewOutput

SECURITY_RULES = [
    (
        "SEC-INJ",
        "Injection",
        "Unsanitized input reaching SQL queries, shell commands, eval/exec, "
        "or template engines.",
    ),
    (
        "SEC-SECRET",
        "Hardcoded secrets",
        "API keys, tokens, passwords, or private keys committed in code or "
        "configuration.",
    ),
    (
        "SEC-AUTHZ",
        "Authorization changes",
        "Weakened permission checks, removed auth middleware, widened role "
        "access, or object references that skip ownership checks.",
    ),
    (
        "SEC-DESER",
        "Unsafe deserialization",
        "pickle, yaml.load without SafeLoader, or eval on untrusted data.",
    ),
    (
        "SEC-PATH",
        "Path traversal",
        "User input joined into filesystem paths without normalization or "
        "containment checks.",
    ),
    (
        "SEC-SSRF",
        "Server-side request forgery",
        "User-controlled URLs fetched server-side without an allowlist.",
    ),
    (
        "SEC-CRYPTO",
        "Weak cryptography",
        "MD5/SHA1 for security purposes, ECB mode, hardcoded IVs or salts, "
        "or disabled TLS verification.",
    ),
    (
        "SEC-XSS",
        "Cross-site scripting",
        "Unescaped user input rendered into HTML, innerHTML, or "
        "dangerouslySetInnerHTML.",
    ),
]


def rules_prompt() -> str:
    """Render the ruleset as a bullet list for the system prompt."""
    return "\n".join(
        f"- {rule_id} ({name}): {description}"
        for rule_id, name, description in SECURITY_RULES
    )


# Strong signatures are never suppressed by surrounding comments or syntax.
_SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key ID committed in the diff"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token committed in the diff"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "GitHub token committed in the diff"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token committed in the diff"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic API key committed in the diff"),
]
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")
_PRIVATE_KEY_END = re.compile(r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----")
_ASSIGNMENT = re.compile(
    r'''(?i)[a-z0-9_-]*(?:api[_-]?key|secret|token|passwd|password)\b["']?\s*[:=]\s*'''
    r'''(?P<quote>["'])(?P<value>[^"'\r\n]{8,})(?P=quote)'''
)
_PLACEHOLDER = re.compile(
    r"(?i)(?:your[-_].*|<[^>]+>|\$\{[^}]+\}|example|placeholder|changeme|x{3,}|\[REDACTED\])"
)


def _credential_matches(line: str):
    for pattern, description in _SECRET_PATTERNS:
        for match in pattern.finditer(line):
            yield match.span(), description, "high"
    for match in _ASSIGNMENT.finditer(line):
        if not _PLACEHOLDER.fullmatch(match["value"]):
            yield match.span("value"), "Possible hardcoded credential assignment", "medium"


def redact_secrets(text: str) -> str:
    """Mask recognized values, including removed/context lines and PEM bodies.

    This is best-effort pattern detection, not a guarantee that arbitrary
    sensitive data can safely be shared with a remote model.
    """
    result = []
    in_private_key = False
    for line in text.splitlines(keepends=True):
        # A truncated PEM block must not swallow later patch headers.
        if line.startswith(("diff --git ", "@@", "--- ", "+++ ")):
            in_private_key = False
        begin = _PRIVATE_KEY.search(line)
        end = _PRIVATE_KEY_END.search(line)
        if begin:
            in_private_key = end is None
            if end and end.start() >= begin.end():
                line = line[:begin.end()] + "[REDACTED]" + line[end.start():]
            elif line[begin.end():].strip():
                line = line[:begin.end()] + "[REDACTED]" + ("\n" if line.endswith("\n") else "")
        elif _PRIVATE_KEY_END.search(line):
            in_private_key = False
        elif in_private_key:
            prefix = line[0] if line.startswith(("+", "-", " ")) else ""
            line = prefix + "[REDACTED]" + ("\n" if line.endswith("\n") else "")
        spans = sorted({span for span, _, _ in _credential_matches(line)})
        # Merge overlaps before replacing, so a token inside an assignment is
        # redacted once without invalidating offsets for another credential.
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        for start, end in reversed(merged):
            line = line[:start] + "[REDACTED]" + line[end:]
        result.append(line)
    return "".join(result)


def redact_output(output: ReviewOutput) -> ReviewOutput:
    """Sanitize every string, including model summaries and suggestions."""
    def clean(value):
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value
    return ReviewOutput.model_validate(clean(output.model_dump()))


def scan_secrets(diff: str) -> list[Issue]:
    """Scan added hunk lines; findings contain masked evidence only."""
    issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for section in file_sections(diff):
        path = file_path(section)
        in_hunk = False
        for line in section.splitlines():
            if line.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk or not line.startswith("+"):
                continue
            added = line[1:]
            matches = list(_credential_matches(added))
            if _PRIVATE_KEY.search(added):
                description, severity = "Private key material committed in the diff", "high"
            elif matches:
                _, description, severity = matches[0]
            else:
                continue
            key = (path, added.strip())
            if key in seen:
                continue
            seen.add(key)
            issues.append(Issue(
                severity=severity, file=path, description=description,
                suggestion="Remove and rotate the credential; load it from a secrets manager or the environment.",
                evidence=redact_secrets(line.strip()), rule="SEC-SECRET",
            ))
    return issues


def merge_secret_issues(output_issues: list[Issue], found: list[Issue]) -> list[Issue]:
    """Scanner findings take precedence; never dedupe across different files."""
    scanned = {(issue.file, issue.evidence.strip()): issue for issue in found}
    remaining = []
    for issue in output_issues:
        key = (issue.file, issue.evidence.strip())
        if key not in scanned or issue.rule not in ("", "SEC-SECRET"):
            remaining.append(issue)
        elif issue.severity == "high" and scanned[key].severity != "high":
            # A generic assignment match must not downgrade model evidence.
            scanned[key] = issue.model_copy(update={"rule": "SEC-SECRET"})
    return list(scanned.values()) + remaining


def scan_only_output(found: list[Issue], skipped_files: list[str] | None = None) -> ReviewOutput:
    return ReviewOutput(
        verdict="request_changes" if any(i.severity == "high" for i in found) else "needs_discussion",
        summary="No files received model review. Only a local secrets scan was completed; this is not an approval.",
        issues=found, highlights=[],
        coverage=Coverage(skipped_files=skipped_files or []),
    )
