# Changelog

All notable changes to this project will be documented here.

Format: `[version] - YYYY-MM-DD` followed by `Added`, `Changed`, `Fixed`, or `Removed` sections.

---

## [0.5.0] - 2026-11-10

### Added
- Local and self-hosted models: `--provider ollama` and `--provider lmstudio` presets (no key or URL needed; `OLLAMA_HOST` respected), and `--provider openai --base-url URL` for llama.cpp, vLLM, LocalAI, Jan, or the OpenAI API itself
- `--model`, `--base-url`, `--reasoning none|low|medium|high`, and `--concurrency N` flags, plus `DIFF_REVIEW_PROVIDER`, `DIFF_REVIEW_REASONING`, `DIFF_REVIEW_TIMEOUT`, `OPENAI_MODEL`, and `OPENAI_BASE_URL`
- `openai` optional dependency extra (`pip install 'diff-review[openai]'`)
- Structured output for OpenAI-compatible servers: the review JSON Schema is sent as `response_format: json_schema`, falling back to JSON mode and then prompt-only JSON
- Evidence matching tolerant of re-indented, marker-less, labeled, or elided quotes; matched quotes are replaced with the verbatim diff lines
- `coverage.unverified_findings`: findings whose evidence never matched are discarded and counted instead of failing the whole run; a discarded high finding blocks `approve`
- Server-side refusal fallback on current Claude models, and a clear error naming the refusal category when a request is declined
- Installed-CLI integration tests using the real Anthropic SDK against a local HTTP server, plus randomized patch reconstruction checks.
- Coverage metadata and optional `--fail-on` policies for CI, while retaining advisory exit behavior by default.
- Regression tests for the pipeline, CLI, chunking, scanner, credential redaction, and GitHub failures.
- Pinned development/CI dependencies and a Python 3.11–3.14 CI matrix.

### Changed
- Default Claude model is now `claude-sonnet-5-5` (was `claude-sonnet-4-6`); `max_tokens` raised to 16,000 because current models think adaptively within that budget
- Cost estimates use per-model Claude list prices and are omitted for local or unknown models
- Local and LAN servers default to one request at a time; hosted APIs keep 8
- Supports `anthropic` SDK 2.x (requires `anthropic>=2.7.0`); API error messages explain the status code without echoing the response body

### Fixed
- Reject truncated or malformed patch hunks before review, including excluded files.
- Preserve private-key redaction across hunk boundaries and keep the highest scanner severity when masked evidence collides.
- Avoid generating context-only hunks when splitting large changes.
- Preserve all changed lines by splitting large hunks with accurate ranges and explicit byte/line budgets.
- Retry invalid model responses once, validate file/evidence/schema fields, and stop on incomplete reviews.
- Prevent synthesis from dropping or downgrading verified high-severity findings.
- Detect secrets despite surrounding comments, HTML, environment fallbacks, and quoted JSON keys.
- Mask recognized credentials and private-key bodies before model requests and in all output formats.
- Match exclusions by basename/suffix, report excluded files, and prevent approval of incomplete reviews.
- Handle lockfile-only reviews without model credentials and report expected input/API failures cleanly.
- Thinking and other non-text response blocks are ignored when reading a review


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
