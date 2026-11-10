from typing import Literal
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class Issue(BaseModel):
    severity: Literal["low", "medium", "high"]
    file: str
    description: str
    suggestion: str
    evidence: str = Field(min_length=1)
    rule: str = ""


class FileReview(BaseModel):
    issues: list[Issue]
    highlights: list[str]


class CheckedReview(FileReview):
    """A file review after evidence verification (internal, not a model schema)."""
    # Severities of findings discarded because their evidence never matched the diff.
    unverified: list[Literal["low", "medium", "high"]] = Field(default_factory=list)


class Coverage(BaseModel):
    complete: bool = False
    reviewed_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    reviewed_chunks: int = 0
    unverified_findings: int = 0


class ReviewOutput(FileReview):
    verdict: Literal["approve", "request_changes", "needs_discussion"]
    summary: str
    coverage: Coverage = Field(default_factory=Coverage)


class ReviewState(TypedDict):
    diff: str
    file_chunks: list[str]
    file_reviews: list[str]
    output: ReviewOutput | None
    token_usage: dict
    security: bool
    concurrency: int
    reviewed_files: list[str]
    skipped_files: list[str]


# Plain JSON Schemas (no $refs) for constrained decoding on servers that
# support response_format json_schema: Ollama, LM Studio, llama.cpp, vLLM, OpenAI.
_ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "file": {"type": "string"},
        "description": {"type": "string"},
        "suggestion": {"type": "string"},
        "evidence": {"type": "string", "minLength": 1},
        "rule": {"type": "string"},
    },
    "required": ["severity", "file", "description", "suggestion", "evidence"],
}
FILE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {"type": "array", "items": _ISSUE_SCHEMA},
        "highlights": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issues", "highlights"],
}
SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "request_changes", "needs_discussion"]},
        "summary": {"type": "string"},
        **FILE_REVIEW_SCHEMA["properties"],
    },
    "required": ["verdict", "summary", "issues", "highlights"],
}
