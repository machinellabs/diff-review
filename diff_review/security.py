"""Security review support: the prompt ruleset and a local secrets pre-scan.

The ruleset steers Claude toward security-relevant findings when --security
is set. The secrets scan runs locally with regexes, so hardcoded credentials
are flagged even if the model misses them and without sending anything extra
over the network.
"""

import re

from .schema import Issue

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


# (pattern, description, severity) — applied to added lines only.
_SECRET_PATTERNS = [
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "AWS access key ID committed in the diff",
        "high",
    ),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        "Private key material committed in the diff",
        "high",
    ),
    (
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
        "GitHub token committed in the diff",
        "high",
    ),
    (
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        "Slack token committed in the diff",
        "high",
    ),
    (
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
        "Anthropic API key committed in the diff",
        "high",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*"
            r"[\"'][^\"']{8,}[\"']"
        ),
        "Possible hardcoded credential assignment",
        "medium",
    ),
]

# Values that look like docs or config plumbing, not real secrets.
_PLACEHOLDER_MARKERS = (
    "example",
    "your-",
    "your_",
    "placeholder",
    "changeme",
    "xxx",
    "<",
    "${",
    "os.environ",
    "process.env",
    "getenv",
    "secrets.",
)


def _is_placeholder(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def scan_secrets(diff: str) -> list[Issue]:
    """Scan the added lines of a diff for secrets. Returns one Issue per hit."""
    issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    current_file = "unknown"

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue

        added = line[1:]
        if _is_placeholder(added):
            continue

        for pattern, description, severity in _SECRET_PATTERNS:
            if not pattern.search(added):
                continue
            key = (current_file, added.strip())
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                Issue(
                    severity=severity,
                    file=current_file,
                    description=description,
                    suggestion=(
                        "Remove the value from the diff, rotate the "
                        "credential, and load it from the environment or a "
                        "secrets manager."
                    ),
                    evidence=line.strip(),
                    rule="SEC-SECRET",
                )
            )
            break

    return issues


def merge_secret_issues(output_issues: list[Issue], found: list[Issue]) -> list[Issue]:
    """Prepend scanner findings, skipping any the model already reported."""
    existing = {issue.evidence.strip() for issue in output_issues if issue.evidence}
    fresh = [issue for issue in found if issue.evidence.strip() not in existing]
    return fresh + output_issues
