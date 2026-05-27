import argparse
import sys
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
        "--json",
        action="store_true",
        help="Output results as JSON.",
    )
    args = parser.parse_args()

    if args.file:
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
