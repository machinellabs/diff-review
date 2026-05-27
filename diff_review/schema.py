from typing import Annotated
from typing_extensions import TypedDict
from pydantic import BaseModel
from langgraph.graph.message import add_messages


class Issue(BaseModel):
    severity: str
    file: str
    description: str
    suggestion: str


class ReviewOutput(BaseModel):
    verdict: str
    summary: str
    issues: list[Issue]
    highlights: list[str]


class ReviewState(TypedDict):
    diff: str
    file_chunks: list[str]
    file_reviews: Annotated[list[str], add_messages]
    output: ReviewOutput | None
