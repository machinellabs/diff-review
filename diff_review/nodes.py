import json
import os
import anthropic
from concurrent.futures import ThreadPoolExecutor

from .schema import ReviewState, ReviewOutput

_client = anthropic.Anthropic()
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
                '\"file\": \"string\", \"description\": \"string\", \"suggestion\": \"string\", '
                '\"evidence\": \"exact quote of the lines from the diff that demonstrate the issue\"}], '
                '\"highlights\": [\"string\"]}. '
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
        return response.content[0].text
    except anthropic.APIError as e:
        raise RuntimeError(f"API error while reviewing chunk: {e}") from e


def review_chunks(state: ReviewState) -> dict:
    chunks = state["file_chunks"]
    if not chunks:
        return {"file_reviews": []}
    with ThreadPoolExecutor(max_workers=min(len(chunks), 8)) as executor:
        reviews = list(executor.map(_review_chunk, chunks))
    return {"file_reviews": reviews}


def synthesize(state: ReviewState) -> dict:
    if not state["file_reviews"]:
        raise RuntimeError("No file reviews to synthesize — diff may be empty.")

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
                '\"description\": \"string\", \"suggestion\": \"string\", '
                '\"evidence\": \"exact quote of the lines that demonstrate the issue\"}], '
                '\"highlights\": [\"string\"]}. '
                "Only include an issue if the evidence field can be populated with a direct quote from the diff. "
                "Return valid JSON only, no markdown."
            ),
            messages=[{"role": "user", "content": f"Synthesize these file reviews:\n\n{combined}"}],
        )
        if not response.content or response.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Incomplete API response during synthesis (stop_reason={response.stop_reason!r})"
            )
        text = response.content[0].text
        if not text.strip():
            raise RuntimeError("Empty response from API during synthesis.")
        data = json.loads(text)
    except anthropic.APIError as e:
        raise RuntimeError(f"API error during synthesis: {e}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in synthesis response: {e}") from e

    return {"output": ReviewOutput(**data)}
