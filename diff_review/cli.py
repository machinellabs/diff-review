import argparse
import os
import sys
from . import __version__
from .github import fetch_pr_diff
from .graph import build_graph
from .formatter import format_json, format_markdown, print_review, print_token_usage


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
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    args = parser.parse_args()

    if args.pr and args.file:
        print("Error: --pr and a diff file are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("Error: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
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

    graph = build_graph()
    result = graph.invoke({"diff": diff, "file_chunks": [], "file_reviews": [], "output": None})
    output = result["output"]

    print_review(output, as_json=args.json, as_markdown=args.markdown)
    if not args.json and not args.markdown:
        print_token_usage(result.get("token_usage", {}))

    if args.output:
        content = format_json(output) if args.json else format_markdown(output, source=source_label)
        with open(args.output, "w") as f:
            f.write(content)
        print(f"\nSaved to {args.output}", file=sys.stderr)
