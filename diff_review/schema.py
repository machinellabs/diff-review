from typing_extensions import TypedDict
from pydantic import BaseModel


class Issue(BaseModel):
    severity: str
    file: str
    description: str
    suggestion: str
    evidence: str


class ReviewOutput(BaseModel):
    verdict: str
    summary: str
    issues: list[Issue]
    highlights: list[str]


class ReviewState(TypedDict):
    diff: str
    file_chunks: list[str]
    file_reviews: list[str]
    output: ReviewOutput | None
    token_usage: dict
