# Changelog

All notable changes to this project will be documented here.

Format: `[version] - YYYY-MM-DD` followed by `Added`, `Changed`, `Fixed`, or `Removed` sections.

---

## [0.3.0] - 2026-05-28

### Added
- `--markdown` flag — outputs a clean Markdown review (renders on GitHub, Notion, VS Code, and any text editor)
- `--output FILE` flag — saves the review to a file; Markdown by default, JSON if `--json` is also set
- `format_markdown()` and `format_json()` exported from `formatter.py` for programmatic use
- Markdown output includes date, source label (PR URL, file path, or stdin), emoji-coded severity, and evidence code blocks

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
