import argparse
import sys
from . import __version__
from .github import fetch_pr_diff
from .graph import build_graph
from .formatter import format_json, format_markdown, print_review, print_token_usage
from .nodes import parse_diff
from .providers import (DEFAULT_ANTHROPIC_MODEL, PROVIDERS, REASONING_LEVELS, configure,
                        get_provider)
from .security import merge_secret_issues, redact_output, redact_secrets, scan_secrets


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="diff-review",
        description="Run an agentic code review on a git diff.",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Path to a diff file. Reads from stdin if omitted.",
    )
    parser.add_argument(
        "--pr",
        metavar="URL",
        help="GitHub PR URL to review (e.g. https://github.com/owner/repo/pull/123).",
    )

    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON.",
    )
    fmt.add_argument(
        "--markdown",
        action="store_true",
        help="Output results as Markdown (renders on GitHub, Notion, VS Code).",
    )

    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Save review to a file. Markdown by default; JSON if --json is set.",
    )
    parser.add_argument(
        "--security",
        action="store_true",
        help="Security-focused review: injection, secrets, authz, deserialization, and more.",
    )
    model = parser.add_argument_group(
        "model provider",
        "Claude by default. Local models run through any OpenAI-compatible server.",
    )
    model.add_argument(
        "--provider", choices=PROVIDERS,
        help="anthropic (default, or DIFF_REVIEW_PROVIDER); openai for the OpenAI API or any "
        "OpenAI-compatible server via --base-url; ollama and lmstudio for those local servers "
        "at their default addresses.",
    )
    model.add_argument(
        "--model",
        help=f"Model name. anthropic default: {DEFAULT_ANTHROPIC_MODEL} (or ANTHROPIC_MODEL). "
        "Required for the other providers (or OPENAI_MODEL), e.g. qwen3.8:27b-q4_K_M.",
    )
    model.add_argument(
        "--base-url", metavar="URL",
        help="OpenAI-compatible server URL, e.g. http://localhost:8080/v1 for llama.cpp "
        "(or OPENAI_BASE_URL). Overrides the ollama/lmstudio default address.",
    )
    model.add_argument(
        "--reasoning", choices=REASONING_LEVELS,
        help="Thinking depth (or DIFF_REVIEW_REASONING). Claude: low/medium/high effort. "
        "Local thinking models: none is much faster. Default: the model's own.",
    )
    model.add_argument(
        "--concurrency", type=int, metavar="N",
        help="Parallel model requests (default: 8 hosted, 1 for a local server).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--fail-on", choices=("never", "request_changes", "non-approval"), default="never",
        help="Exit 2 for the selected verdict policy (default: advisory output only).",
    )
    args = parser.parse_args()

    if args.pr and args.file:
        print("Error: --pr and a diff file are mutually exclusive.", file=sys.stderr)
        sys.exit(1)
    if args.concurrency is not None and args.concurrency < 1:
        print("Error: --concurrency must be at least 1.", file=sys.stderr)
        sys.exit(1)
    configure(provider=args.provider, model=args.model, base_url=args.base_url,
              reasoning=args.reasoning)

    try:
        _run(args)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"Error: {redact_secrets(str(exc))}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Review cancelled.", file=sys.stderr)
        sys.exit(130)


def _run(args) -> None:
    if args.pr:
        diff = fetch_pr_diff(args.pr)
        source_label = args.pr
    elif args.file:
        with open(args.file, encoding="utf-8") as source:
            diff = source.read()
        source_label = args.file
    else:
        diff = sys.stdin.read()
        source_label = "stdin"
    if not diff.strip():
        raise ValueError("No diff provided.")

    # Detect locally before masking. Even ordinary reviews must not transmit
    # recognized credentials, and findings must survive redaction.
    found = scan_secrets(diff)
    safe_diff = redact_secrets(diff)
    parsed = parse_diff({"diff": safe_diff})
    # Set up the provider before the graph runs so misconfiguration (missing
    # key, model or openai extra) exits cleanly. Scan-only diffs need none.
    provider = get_provider() if parsed["file_chunks"] else None
    result = build_graph().invoke({
        "diff": safe_diff, "file_chunks": [], "file_reviews": [],
        "output": None, "security": args.security, "concurrency": args.concurrency,
    })
    output = result["output"]
    output.issues = merge_secret_issues(output.issues, found)
    if any(issue.severity == "high" for issue in output.issues):
        output.verdict = "request_changes"
    elif found and output.verdict == "approve":
        output.verdict = "needs_discussion"
    if found:
        output.summary += " The local secrets scan found credentials that require review and rotation."
    output = redact_output(output)

    if args.output:
        content = format_json(output) if args.json else format_markdown(output, source=source_label)
        with open(args.output, "w", encoding="utf-8") as destination:
            destination.write(content)
        print(f"Saved to {redact_secrets(args.output)}", file=sys.stderr)
    print_review(output, as_json=args.json, as_markdown=args.markdown)
    if not args.json and not args.markdown:
        print_token_usage(result.get("token_usage", {}),
                          pricing=provider.pricing if provider else None)
    if ((args.fail_on == "request_changes" and output.verdict == "request_changes")
            or (args.fail_on == "non-approval" and output.verdict != "approve")):
        sys.exit(2)
