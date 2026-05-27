import json
import os
import anthropic

from .schema import ReviewState, ReviewOutput

_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_MODEL = "claude-sonnet-4-6"


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


def review_chunks(state: ReviewState) -> dict:
    reviews = []
    for chunk in state["file_chunks"]:
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
        reviews.append(response.content[0].text)
    return {"file_reviews": reviews}


def synthesize(state: ReviewState) -> dict:
    combined = "\n\n---\n\n".join(state["file_reviews"])
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
    data = json.loads(response.content[0].text)
    return {"output": ReviewOutput(**data)}
