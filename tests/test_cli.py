import io
import json
import sys

import anthropic
import httpx
import pytest

from diff_review import cli


def invoke(monkeypatch, diff, *args, key=True):
    monkeypatch.setattr(sys, "argv", ["diff-review", *args])
    monkeypatch.setattr(sys, "stdin", io.StringIO(diff))
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "audit-dummy")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cli.main()


@pytest.mark.parametrize("security", [False, True])
def test_full_cli_redacts_before_model_and_output(monkeypatch, capsys, patch_text, fake_model, tmp_path, security):
    calls = fake_model()
    key = "AKIA" + "Q7M2" * 4
    target = tmp_path / "review.json"
    invoke(monkeypatch, patch_text([f'+aws_id = "{key}" # example']), "--json", "--output", str(target), *(["--security"] if security else []))
    printed = capsys.readouterr().out
    output = json.loads(printed)
    assert output["verdict"] == "request_changes"
    assert output["issues"][0]["rule"] == "SEC-SECRET"
    assert key not in printed + target.read_text() + json.dumps(calls)
    assert "[REDACTED]" in calls[0]["messages"][0]["content"]
    assert json.loads(target.read_text()) == output


@pytest.mark.parametrize("security", [False, True])
def test_lockfile_only_without_key_has_incomplete_result(monkeypatch, capsys, patch_text, security):
    invoke(monkeypatch, patch_text(path="poetry.lock"), "--json", *(["--security"] if security else []), key=False)
    output = json.loads(capsys.readouterr().out)
    assert output["verdict"] == "needs_discussion"
    assert output["coverage"]["skipped_files"] == ["poetry.lock"]


@pytest.mark.parametrize("args,code", [(('--fail-on', 'non-approval'), 2), (('--fail-on', 'request_changes'), None)])
def test_fail_on_policy_for_incomplete_review(monkeypatch, capsys, patch_text, args, code):
    if code:
        with pytest.raises(SystemExit) as exc:
            invoke(monkeypatch, patch_text(path="poetry.lock"), "--json", *args, key=False)
        assert exc.value.code == code
    else:
        invoke(monkeypatch, patch_text(path="poetry.lock"), "--json", *args, key=False)
    assert json.loads(capsys.readouterr().out)["verdict"] == "needs_discussion"


def test_fail_on_writes_report_before_exit(monkeypatch, patch_text, tmp_path):
    target = tmp_path / "review.md"
    key = "AKIA" + "Q7M2" * 4
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, patch_text([f'+key = "{key}"'], path="poetry.lock"), "--output", str(target), "--fail-on", "request_changes", key=False)
    assert exc.value.code == 2
    assert "REQUEST CHANGES" in target.read_text()
    assert key not in target.read_text()


@pytest.mark.parametrize("diff,match", [("", "No diff"), ("nonsense", "No supported")])
def test_invalid_input_exits_cleanly(monkeypatch, capsys, diff, match):
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, diff)
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert match in captured.err
    assert "Traceback" not in captured.err


def test_missing_key_exits_cleanly(monkeypatch, capsys, patch_text):
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, patch_text(), key=False)
    assert exc.value.code == 1
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_missing_file_exits_cleanly(monkeypatch, capsys, tmp_path):
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, "", str(tmp_path / "missing.diff"))
    assert exc.value.code == 1
    assert "No such file" in capsys.readouterr().err


def test_provider_failure_does_not_echo_sensitive_body(monkeypatch, capsys, patch_text, fake_model):
    key = "AKIA" + "Q7M2" * 4
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request)
    fake_model(lambda content, n: anthropic.AuthenticationError(key, response=response, body={"secret": key}))
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, patch_text(), "--json")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "HTTP 401" in captured.err
    assert key not in captured.err
    assert "Traceback" not in captured.err


def test_malformed_model_response_does_not_produce_review(monkeypatch, capsys, patch_text, fake_model):
    fake_model(lambda content, n: "invalid")
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, patch_text(), "--json")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "after two attempts" in captured.err


def test_output_write_failure_has_no_success_output(monkeypatch, capsys, patch_text, fake_model, tmp_path):
    fake_model()
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, patch_text(), "--json", "--output", str(tmp_path / "missing" / "review.json"))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "No such file" in captured.err


def test_pr_input_success_records_source(monkeypatch, capsys, patch_text, fake_model, tmp_path):
    fake_model()
    url = "https://github.com/test/repo/pull/1"
    fetched = []
    def fetch(value):
        fetched.append(value)
        return patch_text()
    monkeypatch.setattr(cli, "fetch_pr_diff", fetch)
    target = tmp_path / "review.md"
    invoke(monkeypatch, "", "--pr", url, "--markdown", "--output", str(target))
    assert fetched == [url]
    assert "**Source:** " + url in target.read_text()
    assert "APPROVE" in capsys.readouterr().out


def test_file_and_pr_are_mutually_exclusive(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        invoke(monkeypatch, "", "file.diff", "--pr", "https://github.com/test/repo/pull/1")
    assert exc.value.code == 1
    assert "mutually exclusive" in capsys.readouterr().err
