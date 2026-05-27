import argparse
import os
import sys
from . import __version__
from .github import fetch_pr_diff
from .graph import build_graph
from .formatter import print_review


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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON.",
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

    if args.pr:
        try:
            diff = fetch_pr_diff(args.pr)
        except (ValueError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.file:
        with open(args.file) as f:
            diff = f.read()
    else:
        diff = sys.stdin.read()

    if not diff.strip():
        print("Error: no diff provided.", file=sys.stderr)
        sys.exit(1)

    graph = build_graph()
    result = graph.invoke({"diff": diff, "file_chunks": [], "file_reviews": [], "output": None})
    print_review(result["output"], as_json=args.json)
