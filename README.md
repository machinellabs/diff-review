# diff-review

An AI-powered CLI that runs a code review on any git diff using [LangGraph](https://github.com/langchain-ai/langgraph) and the [Claude API](https://www.anthropic.com).

## How it works

`diff-review` runs a three-step pipeline instead of a single API call:

1. **Parse** — splits the diff into per-file chunks
2. **Review** — sends each file to Claude in parallel (up to 8 at once)
3. **Synthesize** — aggregates all file reviews into a final verdict

This mirrors how a real engineer reviews a PR: read each file carefully, then form an overall opinion.

## Install

```bash
git clone https://github.com/machinellabs/diff-review
cd diff-review
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Setup

Get an API key from [console.anthropic.com](https://console.anthropic.com) and export it:

```bash
export ANTHROPIC_API_KEY=your-key-here
```

Each user runs against their own Anthropic account. Your key is never shared.

## Usage

```bash
# Review staged changes before committing
git diff --cached | diff-review

# Review all uncommitted changes (staged + unstaged)
git diff HEAD | diff-review

# Review your branch vs main before opening a PR
git diff main...HEAD | diff-review

# Review the last commit
git show HEAD | diff-review

# Review a specific commit
git show abc1234 | diff-review

# Review a saved diff file
diff-review path/to/changes.diff

# Get structured JSON output
git diff main...HEAD | diff-review --json

# Check version
diff-review --version
```

## Output

```
╭─────────────────╮
│ REQUEST_CHANGES │
╰─────────────────╯

Summary
The changes introduce a new caching layer but the TTL logic has an off-by-one
error and there is no test coverage for the expiry path.

Severity   File            Issue                         Suggestion
high       src/cache.py    TTL comparison uses > not >=  Change to >= to include boundary
medium     src/cache.py    No test for expired entries   Add a test with a frozen clock

Highlights
  ✓ Clean separation between cache interface and storage backend
  ✓ Good use of type hints throughout
```

**Verdicts:**
- `APPROVE` — looks good to merge
- `REQUEST_CHANGES` — issues found that should be addressed
- `NEEDS_DISCUSSION` — not wrong, but warrants a conversation

Each issue includes the exact lines from the diff as evidence — Claude will not report an issue it can't quote directly.

## JSON output

Use `--json` to get structured output for scripting or CI:

```bash
git diff main...HEAD | diff-review --json
```

```json
{
  "verdict": "request_changes",
  "summary": "...",
  "issues": [
    {
      "severity": "high",
      "file": "src/cache.py",
      "description": "TTL comparison uses > not >=",
      "suggestion": "Change to >= to include boundary",
      "evidence": "+    if elapsed > ttl:"
    }
  ],
  "highlights": ["Clean separation between cache interface and storage backend"]
}
```

## Git aliases

Add to `~/.gitconfig` for quick access from any repo:

```ini
[alias]
  review         = "!git diff main...HEAD | diff-review"
  review-staged  = "!git diff --cached | diff-review"
  review-last    = "!git show HEAD | diff-review"
```

Then use:

```bash
git review
git review-staged
git review-last
```

## CI / GitHub Actions

```yaml
- name: AI code review
  run: git diff ${{ github.base_ref }}...HEAD | diff-review
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

## Configuration

| Environment variable | Default             | Description                        |
|----------------------|---------------------|------------------------------------|
| `ANTHROPIC_API_KEY`  | *(required)*        | Your Anthropic API key             |
| `ANTHROPIC_MODEL`    | `claude-sonnet-4-6` | Model to use for review            |

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` environment variable set

## Project structure

```
diff_review/
├── schema.py     # Pydantic output models + LangGraph state
├── nodes.py      # Three agent steps: parse, review, synthesize
├── graph.py      # LangGraph workflow wiring
├── formatter.py  # Rich terminal display + JSON output
└── cli.py        # Entry point and argument parsing
```

## Versioning

This project follows [Semantic Versioning](https://semver.org/). See [CHANGELOG.md](CHANGELOG.md) for the full history.
