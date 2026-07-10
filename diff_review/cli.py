import argparse
import os
import sys
from . import __version__
from .github import fetch_pr_diff
from .graph import build_graph
from .formatter import format_json, format_markdown, print_review, print_token_usage
from .nodes import parse_diff
from .providers import configure, get_provider, provider_name
from .security import merge_secret_issues, scan_only_output, scan_secrets


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
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        help="Model provider (default: anthropic, or DIFF_REVIEW_PROVIDER). "
        "openai covers the OpenAI API and local OpenAI-compatible servers like Ollama.",
    )
    parser.add_argument(
        "--model",
        help="Model name. Default for anthropic: claude-sonnet-4-6 (or ANTHROPIC_MODEL); "
        "required for openai (or set OPENAI_MODEL).",
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        help="Base URL for the openai provider, e.g. http://localhost:11434/v1 "
        "for Ollama (or set OPENAI_BASE_URL).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args()

    if args.pr and args.file:
        print("Error: --pr and a diff file are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    configure(provider=args.provider, model=args.model, base_url=args.base_url)

    if provider_name() == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            print("Error: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
            sys.exit(1)
    else:
        if not args.model and not os.environ.get("OPENAI_MODEL", "").strip():
            print(
                "Error: no model set for the openai provider. "
                "Pass --model or set OPENAI_MODEL (e.g. qwen3.6:27b for Ollama).",
                file=sys.stderr,
            )
            sys.exit(1)
        has_base_url = bool(args.base_url or os.environ.get("OPENAI_BASE_URL", "").strip())
        if not has_base_url and not os.environ.get("OPENAI_API_KEY", "").strip():
            print(
                "Error: OPENAI_API_KEY is not set. Set it, or pass --base-url for a "
                "local OpenAI-compatible server (e.g. http://localhost:11434/v1).",
                file=sys.stderr,
            )
            sys.exit(1)

    source_label = ""
    if args.pr:
        try:
            diff = fetch_pr_diff(args.pr)
            source_label = args.pr
        except (ValueError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.file:
        with open(args.file) as f:
            diff = f.read()
        source_label = args.file
    else:
        diff = sys.stdin.read()
        source_label = "stdin"

    if not diff.strip():
        print("Error: no diff provided.", file=sys.stderr)
        sys.exit(1)

    # The local scan runs before the model review so its findings survive even
    # when the diff has nothing the model can look at.
    found = scan_secrets(diff) if args.security else []

    if args.security and not parse_diff({"diff": diff})["file_chunks"]:
        output = scan_only_output(found)
        result = {}
    else:
        graph = build_graph()
        result = graph.invoke(
            {
                "diff": diff,
                "file_chunks": [],
                "file_reviews": [],
                "output": None,
                "security": args.security,
            }
        )
        output = result["output"]

        if args.security:
            output.issues = merge_secret_issues(output.issues, found)
            if output.verdict == "approve" and any(i.severity == "high" for i in found):
                output.verdict = "request_changes"

    print_review(output, as_json=args.json, as_markdown=args.markdown)
    if not args.json and not args.markdown:
        usage = result.get("token_usage", {})
        print_token_usage(usage, pricing=get_provider().pricing if usage else None)

    if args.output:
        content = format_json(output) if args.json else format_markdown(output, source=source_label)
        with open(args.output, "w") as f:
            f.write(content)
        print(f"\nSaved to {args.output}", file=sys.stderr)
