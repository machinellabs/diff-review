import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from diff_review import nodes
from diff_review.graph import build_graph
from diff_review.schema import ReviewOutput


def issue(**changes):
    return {"severity": "high", "file": "app.py", "description": "Unsafe evaluation",
            "suggestion": "Remove eval", "evidence": "+eval(user_input)", **changes}


def test_valid_pipeline_reports_complete_coverage(patch_text, fake_model):
    calls = fake_model()
    result = build_graph().invoke({"diff": patch_text(), "security": False})
    output = result["output"]
    assert output.verdict == "approve"
    assert output.coverage.complete
    assert output.coverage.reviewed_files == ["app.py"]
    assert output.coverage.reviewed_chunks == 1
    assert len(calls) == 2


def test_malformed_review_retries_and_counts_usage(patch_text, fake_model):
    calls = fake_model(lambda content, n: "bad json" if n == 1 else {"issues": [], "highlights": []})
    raw, incoming, outgoing = nodes._review_chunk(patch_text())
    assert json.loads(raw)["issues"] == []
    assert (incoming, outgoing) == (20, 10)
    assert len(calls) == 2


def test_permanent_invalid_review_fails_instead_of_synthesizing(patch_text, fake_model):
    calls = fake_model(lambda content, n: "invalid")
    with pytest.raises(RuntimeError, match="after two attempts"):
        build_graph().invoke({"diff": patch_text()})
    assert len(calls) == 2
    assert all(call["messages"][0]["content"].startswith("Review this diff:") for call in calls)


def test_partial_malformed_reviews_are_not_silently_dropped(patch_text):
    state = {"file_chunks": [patch_text(), patch_text(path="other.py")],
             "file_reviews": ['{"issues":[],"highlights":[]}', "bad"]}
    with pytest.raises(RuntimeError, match="Invalid file review for other.py"):
        nodes.synthesize(state)


def test_missing_chunk_review_blocks_verdict(patch_text):
    with pytest.raises(RuntimeError, match="Not every"):
        nodes.synthesize({"file_chunks": [patch_text()], "file_reviews": []})


@pytest.mark.parametrize("changes", [{"severity": "critical"}, {"evidence": ""}, {"evidence": "+invented()"}, {"file": "wrong.py"}])
def test_invalid_issue_data_and_unverified_evidence_fail(patch_text, fake_model, changes):
    fake_model(lambda content, n: {"issues": [issue(**changes)], "highlights": []})
    with pytest.raises(RuntimeError, match="after two attempts"):
        nodes._review_chunk(patch_text(["+eval(user_input)"]))


def test_invalid_verdict_fails_validation():
    with pytest.raises(ValidationError):
        ReviewOutput(verdict="bananas", summary="", issues=[], highlights=[])


def test_synthesis_cannot_drop_or_downgrade_high_findings(patch_text, fake_model):
    fake_model(lambda content, n: {"issues": [issue()], "highlights": []} if content.startswith("Review this diff:")
               else {"verdict": "approve", "summary": "Looks good", "issues": [], "highlights": []})
    output = build_graph().invoke({"diff": patch_text(["+eval(user_input)"])})["output"]
    assert output.verdict == "request_changes"
    assert output.issues[0].severity == "high"


def test_synthesis_cannot_invent_evidence(patch_text, fake_model):
    fake_model(lambda content, n: {"issues": [], "highlights": []} if content.startswith("Review this diff:")
               else {"verdict": "request_changes", "summary": "Bad", "issues": [issue()], "highlights": []})
    with pytest.raises(RuntimeError, match="during synthesis"):
        build_graph().invoke({"diff": patch_text()})


def test_mixed_excluded_files_prevent_approval(patch_text, fake_model):
    fake_model()
    output = build_graph().invoke({"diff": patch_text() + patch_text(path="poetry.lock")})["output"]
    assert output.verdict == "needs_discussion"
    assert not output.coverage.complete
    assert output.coverage.skipped_files == ["poetry.lock"]


def test_all_excluded_needs_no_model_and_never_approves(patch_text):
    output = build_graph().invoke({"diff": patch_text(path="poetry.lock")})["output"]
    assert output.verdict == "needs_discussion"
    assert not output.coverage.complete
    assert output.coverage.reviewed_chunks == 0


def test_incomplete_response_retried_then_fails(patch_text, fake_model):
    calls = fake_model(lambda content, n: SimpleNamespace(content=[], stop_reason="max_tokens", usage=SimpleNamespace(input_tokens=10, output_tokens=5)))
    with pytest.raises(RuntimeError, match="after two attempts"):
        nodes._review_chunk(patch_text())
    assert len(calls) == 2


def test_synthesis_budget_fails_before_api(patch_text, monkeypatch):
    monkeypatch.setattr(nodes, "_MAX_SYNTHESIS_BYTES", 1)
    with pytest.raises(RuntimeError, match="synthesis budget"):
        nodes.synthesize({"file_chunks": [patch_text()], "file_reviews": ['{"issues":[],"highlights":[]}']})
