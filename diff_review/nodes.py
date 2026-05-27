import json
import os
import anthropic
from concurrent.futures import ThreadPoolExecutor

from .schema import ReviewState, ReviewOutput

_api_key = os.environ.get("ANTHROPIC_API_KEY")
if not _api_key:
    raise RuntimeError(
        "ANTHROPIC_API_KEY environment variable is not set. "
        "Export it before running diff-review."
    )

_client = anthropic.Anthropic(api_key=_api_key)
_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")


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
    return {"file_chunks": chunks}


def _review_chunk(chunk: str) -> str:
    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=(
                "You are a senior software engineer reviewing a code diff. "
                "Respond with a JSON object: "
                '{\"issues\": [{\"severity\": \"low\"|\"medium\"|\"high\", '
                '\"file\": \"string\", \"description\": \"string\", \"suggestion\": \"string\"}], '
                '\"highlights\": [\"string\"]}. '
                "Return valid JSON only, no markdown."
            ),
            messages=[{"role": "user", "content": f"Review this diff:\n\n{chunk}"}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"API error while reviewing chunk: {e}") from e

    if not response.content or response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Incomplete API response for chunk (stop_reason={response.stop_reason!r})"
        )

    return response.content[0].text


def review_chunks(state: ReviewState) -> dict:
    chunks = state["file_chunks"]
    with ThreadPoolExecutor(max_workers=min(len(chunks), 8)) as executor:
        reviews = list(executor.map(_review_chunk, chunks))
    return {"file_reviews": reviews}


def synthesize(state: ReviewState) -> dict:
    combined = "\n\n---\n\n".join(state["file_reviews"])

    try:
        response = _client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=(
                "You are a senior software engineer writing a final code review. "
                "Synthesize the individual file reviews into one verdict. "
                "Respond with a JSON object: "
                '{\"verdict\": \"approve\"|\"request_changes\"|\"needs_discussion\", '
                '\"summary\": \"string\", '
                '\"issues\": [{\"severity\": \"low\"|\"medium\"|\"high\", \"file\": \"string\", '
                '\"description\": \"string\", \"suggestion\": \"string\"}], '
                '\"highlights\": [\"string\"]}. '
                "Return valid JSON only, no markdown."
            ),
            messages=[{"role": "user", "content": f"Synthesize these file reviews:\n\n{combined}"}],
        )
    except anthropic.APIError as e:
        raise RuntimeError(f"API error during synthesis: {e}") from e

    if not response.content or response.stop_reason == "max_tokens":
        raise RuntimeError(
            f"Incomplete API response during synthesis (stop_reason={response.stop_reason!r})"
        )

    try:
        data = json.loads(response.content[0].text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in synthesis response: {e}") from e

    return {"output": ReviewOutput(**data)}
