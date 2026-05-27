# Changelog

All notable changes to this project will be documented here.

Format: `[version] - YYYY-MM-DD` followed by `Added`, `Changed`, `Fixed`, or `Removed` sections.

---

## [0.2.0] - 2026-05-27

### Added
- `--pr URL` flag to review any GitHub PR directly by URL
- `diff_review/github.py` — fetches PR diff from the GitHub API
- Supports public repos with no auth; set `GITHUB_TOKEN` for private repos or to raise rate limits
- Clear error messages for 401, 403, and 404 responses from GitHub

---

## [0.1.0] - 2026-05-27

### Added
- Initial release
- Three-step LangGraph pipeline: parse → review → synthesize
- Per-file parallel review using Claude (up to 8 concurrent API calls)
- Final verdict: `approve`, `request_changes`, or `needs_discussion`
- Issues with severity (`high`, `medium`, `low`), file, description, suggestion, and evidence quote
- Highlights for positive observations
- `--json` flag for structured output
- `--version` flag
- Rich terminal output with color-coded severity
- Reads diff from stdin or a file path
- `ANTHROPIC_MODEL` env var to override model (default: `claude-sonnet-4-6`)
