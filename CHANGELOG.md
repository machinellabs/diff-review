# Changelog

All notable changes to this project will be documented here.

Format: `[version] - YYYY-MM-DD` followed by `Added`, `Changed`, `Fixed`, or `Removed` sections.

---

## [0.5.0] - 2026-07-09

### Added
- `--provider openai` — run reviews against any OpenAI-compatible endpoint: the OpenAI API, or a local server such as Ollama for fully offline reviews
- `--model` and `--base-url` flags, plus `DIFF_REVIEW_PROVIDER`, `OPENAI_MODEL`, and `OPENAI_BASE_URL` environment variables
- `openai` optional dependency extra (`pip install 'diff-review[openai]'`)
- One-shot retry with a stricter instruction when a model returns malformed JSON (local models violate the JSON-only contract more often than hosted APIs)

### Changed
- Cost estimate in the token summary is now provider-aware and omitted when pricing is unknown (OpenAI-compatible endpoints)

---

## [0.4.0] - 2026-07-07

### Added
- `--security` flag — security-focused review mode with a fixed ruleset (injection, secrets, authz, deserialization, path traversal, SSRF, weak crypto, XSS)
- Local regex pre-scan for committed secrets on added lines; findings merge into the review and a high-severity hit blocks an `approve` verdict
- `rule` field on issues — rule IDs shown in terminal, Markdown, and JSON output

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
