"""Exercise the installed CLI and real Anthropic SDK over loopback HTTP."""

import json
import os
from pathlib import Path
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

import pytest


@pytest.fixture
def api_server():
    requests = []
    lock = Lock()
    mode = {"value": "success"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                requests.append({"body": body, "path": self.path, "key": self.headers.get("x-api-key")})
                number = len(requests)
            if mode["value"] == "http400":
                status = 400
                payload = {"type": "error", "error": {"type": "invalid_request_error", "message": "Synthetic billing error"}}
            else:
                status = 200
                content = body["messages"][0]["content"]
                if mode["value"] == "invalid" or (mode["value"] == "retry" and number == 1):
                    text = "not JSON"
                elif content.startswith("Review this diff:"):
                    issues = []
                    if "+eval(user_input)" in content:
                        issues = [{"file": "app.py", "severity": "high", "description": "Unsafe eval", "suggestion": "Remove eval", "evidence": "+eval(user_input)", "rule": "SEC-INJ"}]
                    text = json.dumps({"issues": issues, "highlights": []})
                else:
                    # Ensure synthesis cannot erase the earlier high finding.
                    text = json.dumps({"verdict": "approve", "summary": "Synthetic review", "issues": [], "highlights": []})
                payload = {"id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
                           "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 20, "output_tokens": 10}}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    # Do not inherit real credentials, proxy settings, or tracing configuration.
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8",
           "ANTHROPIC_API_KEY": "test-only", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
           "NO_PROXY": "127.0.0.1,localhost"}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    try:
        yield env, requests, mode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_cli(env, diff, *args):
    command = Path(sys.executable).with_name("diff-review")
    return subprocess.run([str(command), *args], input=diff, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("format_flag,extension", [("--json", "json"), ("--markdown", "md"), (None, "md")])
def test_installed_cli_real_sdk_redacts_and_writes_reports(api_server, patch_text, tmp_path, format_flag, extension):
    env, requests, _ = api_server
    key = "AKIA" + "Q7M2" * 4
    diff = patch_text([f'+token = "{key}"', "+eval(user_input)"])
    destination = tmp_path / f"review.{extension}"
    args = ["--security", "--output", str(destination), "--fail-on", "request_changes"]
    if format_flag:
        args.append(format_flag)
    result = run_cli(env, diff, *args)
    assert result.returncode == 2, result.stderr
    assert destination.exists()
    assert key not in result.stdout + result.stderr + destination.read_text() + json.dumps(requests)
    assert len(requests) == 2
    assert all(request["path"] == "/v1/messages" and request["key"] == "test-only" for request in requests)
    if format_flag == "--json":
        output = json.loads(result.stdout)
        assert output["verdict"] == "request_changes"
        assert output["coverage"]["complete"]
        assert {issue["rule"] for issue in output["issues"]} == {"SEC-SECRET", "SEC-INJ"}
        assert json.loads(destination.read_text()) == output
    else:
        assert "REQUEST CHANGES" in destination.read_text()
        assert "SEC-SECRET" in result.stdout


def test_real_sdk_retry_and_usage_accounting(api_server, patch_text):
    env, requests, mode = api_server
    mode["value"] = "retry"
    result = run_cli(env, patch_text())
    assert result.returncode == 0, result.stderr
    assert len(requests) == 3
    assert "60 in / 30 out" in " ".join(result.stdout.split())


@pytest.mark.parametrize("mode_name,expected_count,diagnostic", [("invalid", 2, "after two attempts"), ("http400", 1, "HTTP 400")])
def test_real_sdk_failures_produce_no_artifact(api_server, patch_text, tmp_path, mode_name, expected_count, diagnostic):
    env, requests, mode = api_server
    mode["value"] = mode_name
    destination = tmp_path / "review.json"
    result = run_cli(env, patch_text(), "--json", "--output", str(destination))
    assert result.returncode == 1
    assert result.stdout == ""
    assert diagnostic in result.stderr
    assert "Traceback" not in result.stderr
    assert not destination.exists()
    assert len(requests) == expected_count


@pytest.mark.parametrize("option,expected", [("--help", "usage: diff-review"), ("--version", "diff-review 0.4.0")])
def test_cli_help_and_version_without_credentials(option, expected):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
    result = run_cli(env, "", option)
    assert result.returncode == 0
    assert expected in result.stdout
    assert result.stderr == ""
