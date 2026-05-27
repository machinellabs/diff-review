# diff-review

A CLI tool that runs an agentic code review on a git diff using [LangGraph](https://github.com/langchain-ai/langgraph) and the [Claude API](https://www.anthropic.com).

## How it works

Instead of a single API call, `diff-review` runs a three-step LangGraph pipeline:

1. **Parse** — splits the diff into per-file chunks
2. **Review** — sends each file chunk to Claude independently for analysis
3. **Synthesize** — aggregates all file reviews into a final verdict

This map-reduce pattern mirrors how a real engineer reviews a PR: read each file carefully, then form an overall opinion.

## Output

```
╭─────────────╮
│ REQUEST_CHANGES │
╰─────────────╯

Summary
The changes introduce a new caching layer but the TTL logic has an off-by-one
error and there is no test coverage for the expiry path.

Severity   File              Issue                        Suggestion
high       src/cache.py      TTL comparison uses > not >=  Change to >= to include boundary
medium     src/cache.py      No test for expired entries   Add a test with a frozen clock

Highlights
  ✓ Clean separation between cache interface and storage backend
  ✓ Good use of type hints throughout
```

Add `--json` to get structured output you can pipe to other tools.

## Install

```bash
git clone https://github.com/machinellabs/diff-review
cd diff-review
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

```bash
# Review uncommitted changes
git diff | diff-review

# Review the last commit
git diff HEAD~1 | diff-review

# Review a saved diff file
diff-review path/to/changes.diff

# Get structured JSON output
git diff | diff-review --json
```

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` environment variable

```bash
export ANTHROPIC_API_KEY=your-key-here
```

## Project structure

```
diff_review/
├── schema.py     # Pydantic output models + LangGraph state
├── nodes.py      # Three agent steps: parse, review, synthesize
├── graph.py      # LangGraph workflow wiring
├── formatter.py  # Rich terminal display + JSON output
└── cli.py        # Entry point and argument parsing
```
