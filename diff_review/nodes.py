import json
import os
import anthropic
from concurrent.futures import ThreadPoolExecutor

from .schema import ReviewState, ReviewOutput

_client = anthropic.Anthropic()
_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()



_MAX_CHUNK_LINES = 300


def _truncate_chunk(chunk: str) -> str:
    lines = chunk.splitlines(keepends=True)
    if len(lines) <= _MAX_CHUNK_LINES:
        return chunk
    header = lines[0]
    kept = lines[1:_MAX_CHUNK_LINES]
    skipped = len(lines) - _MAX_CHUNK_LINES
    return "".join([header] + kept) + f"\n[... {skipped} lines truncated for review ...]\n"


_SKIP_PATTERNS = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Gemfile.lock",
    "poetry.lock",
    "Cargo.lock",
    ".min.js",
    ".min.css",
)


def _should_skip(chunk: str) -> bool:
    first_line = chunk.splitlines()[0] if chunk else ""
    return any(p in first_line for p in _SKIP_PATTERNS)


def parse_diff(state: ReviewState) -> dict:
    raw = state["diff"]
    chunks, current = [], []
    for line in raw.splitlines(keepends=True):
        if line.startswith("diff --git") and current:
            chunks.append("".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("".join(current))
    return {"file_chunks": [_truncate_chunk(c) for c in chunks if not _should_skip(c)]}


def _review_chunk(chunk: str) -> tuple[str, int, int]:
    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=8192,
            system=(
                "You are a senior software engineer reviewing a code diff. "
                "Respond with a JSON object: "
                '{\"issues\": [{\"severity\": \"low\"|\"medium\"|\"high\", '
                '\"file\": \"string\", \"description\": \"string\", \"suggestion\": \"string\", '
                '\"evidence\": \"exact quote of the lines from the diff that demonstrate the issue\"}], '
                '\"highlights\": [\"string\"]}. '
                "Report only the top 3 most important issues. "
                "For each issue you must quote the specific lines from the diff that prove it exists. "
                "Do not report an issue if you cannot point to specific lines. "
                "Return valid JSON only, no markdown."
            ),
            messages=[{"role": "user", "content": f"Review this diff:\n\n{chunk}"}],
        )
        if not response.content or response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Incomplete API response for chunk (stop_reason={response.stop_reason!r})"
            )
        return (
            _extract_json(response.content[0].text),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"API error while reviewing chunk: {e}") from e


def review_chunks(state: ReviewState) -> dict:
    chunks = state["file_chunks"]
    if not chunks:
        return {
            "file_reviews": [],
            "token_usage": {
                "review_input_tokens": 0,
                "review_output_tokens": 0,
                "synthesis_input_tokens": 0,
                "synthesis_output_tokens": 0,
            },
        }
    with ThreadPoolExecutor(max_workers=min(len(chunks), 8)) as executor:
        results = list(executor.map(_review_chunk, chunks))
    return {
        "file_reviews": [r[0] for r in results],
        "token_usage": {
            "review_input_tokens": sum(r[1] for r in results),
            "review_output_tokens": sum(r[2] for r in results),
            "synthesis_input_tokens": 0,
            "synthesis_output_tokens": 0,
        },
    }


def synthesize(state: ReviewState) -> dict:
    if not state["file_reviews"]:
        raise RuntimeError("No file reviews to synthesize — diff may be empty.")

    parsed = []
    for raw in state["file_reviews"]:
        try:
            parsed.append(json.loads(raw))
        except json.JSONDecodeError:
            pass

    if not parsed:
        raise RuntimeError("All file reviews returned malformed JSON — cannot synthesize.")

    combined = json.dumps(parsed, indent=2)

    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=8192,
            system=(
                "You are a senior software engineer writing a final code review. "
                "Synthesize the individual file reviews into one concise verdict. "
                "Report only the top issues — do not list every minor finding. "
                "Respond with a JSON object: "
                '{\"verdict\": \"approve\"|\"request_changes\"|\"needs_discussion\", '
                '\"summary\": \"string (2-4 sentences)\", '
                '\"issues\": [{\"severity\": \"low\"|\"medium\"|\"high\", \"file\": \"string\", '
                '\"description\": \"string\", \"suggestion\": \"string\", '
                '\"evidence\": \"exact quote of the lines that demonstrate the issue\"}], '
                '\"highlights\": [\"string\"]}. '
                "Limit issues to the 5 most important. "
                "Only include an issue if the evidence field can be populated with a direct quote from the diff. "
                "Return valid JSON only, no markdown."
            ),
            messages=[{"role": "user", "content": f"Synthesize these file reviews:\n\n{combined}"}],
        )
        if not response.content or response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Incomplete API response during synthesis (stop_reason={response.stop_reason!r})"
            )
        text = _extract_json(response.content[0].text)
        if not text:
            raise RuntimeError("Empty response from API during synthesis.")
        data = json.loads(text)
    except anthropic.APIError as e:
        raise RuntimeError(f"API error during synthesis: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in synthesis response: {e}") from e

    prior = state.get("token_usage") or {}
    return {
        "output": ReviewOutput(**data),
        "token_usage": {
            "review_input_tokens": prior.get("review_input_tokens", 0),
            "review_output_tokens": prior.get("review_output_tokens", 0),
            "synthesis_input_tokens": response.usage.input_tokens,
            "synthesis_output_tokens": response.usage.output_tokens,
        },
    }
