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


class Coverage(BaseModel):
    complete: bool = False
    reviewed_files: list[str] = Field(default_factory=list)
    skipped_files: list[str] = Field(default_factory=list)
    reviewed_chunks: int = 0


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
    reviewed_files: list[str]
    skipped_files: list[str]
