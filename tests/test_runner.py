"""Unit tests for runner.py - Layer 3 execution runner."""

import json
import os
import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import runner


# ─── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def project_root():
    return Path(__file__).parent.parent


@pytest.fixture
def valid_inputs():
    return {"target_url": "https://httpbin.org/get"}


@pytest.fixture
def example_task_dir(project_root):
    return project_root / "tasks" / "example_task"


# ─── Tests for _parse_args ────────────────────────────────────────

class TestParseArgs:
    def test_requires_task(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["runner.py"])
        with pytest.raises(SystemExit) as exc_info:
            runner._parse_args()
        # argparse exits with code 2; our handler converts to 1
        assert exc_info.value.code == 1

    def test_accepts_task_only(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["runner.py", "--task", "example_task"])
        result = runner._parse_args()
        assert result["task"] == "example_task"
        assert result["inputs"] == {}

    def test_accepts_task_with_inputs(self, monkeypatch):
        inputs_json = '{"target_url":"https://api.example.com"}'
        monkeypatch.setattr(
            sys, "argv", ["runner.py", "--task", "example_task", "--inputs", inputs_json]
        )
        result = runner._parse_args()
        assert result["task"] == "example_task"
        assert result["inputs"] == {"target_url": "https://api.example.com"}

    def test_default_inputs_is_empty_dict(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["runner.py", "--task", "example_task"])
        result = runner._parse_args()
        assert result["inputs"] == {}

    def test_invalid_json_inputs_exits(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["runner.py", "--task", "x", "--inputs", "not-json"]
        )
        with pytest.raises(SystemExit) as exc_info:
            runner._parse_args()
        assert exc_info.value.code == 1

    def test_non_dict_inputs_exits(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["runner.py", "--task", "x", "--inputs", "[1,2,3]"]
        )
        with pytest.raises(SystemExit) as exc_info:
            runner._parse_args()
        assert exc_info.value.code == 1


# ─── Tests for _load_manifest ─────────────────────────────────────

class TestLoadManifest:
    def test_loads_valid_manifest(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        assert manifest["type"] == "api"
        assert "timeout_seconds" in manifest
        assert "block_media" in manifest

    def test_missing_manifest_raises(self):
        with pytest.raises(FileNotFoundError):
            runner._load_manifest("nonexistent_task")

    def test_defaults_timeout(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        assert manifest["timeout_seconds"] >= 1


# ─── Tests for _load_flow_module ──────────────────────────────────

class TestLoadFlowModule:
    def test_loads_valid_flow(self, example_task_dir):
        flow_fn = runner._load_flow_module("example_task")
        assert callable(flow_fn)
        assert hasattr(flow_fn, "__call__")

    def test_missing_flow_raises(self):
        with pytest.raises(FileNotFoundError):
            runner._load_flow_module("nonexistent_task")


# ─── Tests for _validate_inputs ────────────────────────────────────

class TestValidateInputs:
    def test_valid_inputs_pass(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": "https://api.example.com"}
        runner._validate_inputs(manifest, inputs, "example_task")

    def test_missing_required_inputs_fail(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {}
        with pytest.raises(Exception):
            runner._validate_inputs(manifest, inputs, "example_task")

    def test_invalid_type_fail(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": 123}
        with pytest.raises(Exception):
            runner._validate_inputs(manifest, inputs, "example_task")

    def test_additional_properties_fail(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": "https://api.example.com", "extra": "value"}
        with pytest.raises(Exception):
            runner._validate_inputs(manifest, inputs, "example_task")

    def test_valid_method_pass(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": "https://api.example.com", "method": "POST"}
        runner._validate_inputs(manifest, inputs, "example_task")

    def test_invalid_method_fail(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": "https://api.example.com", "method": "INVALID"}
        with pytest.raises(Exception):
            runner._validate_inputs(manifest, inputs, "example_task")

    def test_valid_payload_pass(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": "https://api.example.com", "payload": {"key": "value"}}
        runner._validate_inputs(manifest, inputs, "example_task")

    def test_invalid_payload_fail(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        inputs = {"target_url": "https://api.example.com", "payload": "not-an-object"}
        with pytest.raises(Exception):
            runner._validate_inputs(manifest, inputs, "example_task")

    def test_builds_ctx_with_pre_injected_headers(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        auth = {"cookies": [{"name": "session_id", "value": "abc"}]}
        ctx = runner._build_ctx(manifest, lambda ctx, inputs: None, auth_data=auth)
        assert ctx["pre_injected_headers"] == auth
        assert ctx["timeout_seconds"] == manifest["timeout_seconds"]

    def test_builds_ctx_without_headers(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        ctx = runner._build_ctx(manifest, lambda ctx, inputs: None, auth_data=None)
        assert "pre_injected_headers" not in ctx
        assert ctx["task_type"] == "api"

    def test_uses_default_timeout(self, example_task_dir):
        manifest = runner._load_manifest("example_task")
        ctx = runner._build_ctx(manifest, lambda ctx, inputs: None, auth_data=None)
        assert ctx["timeout_seconds"] >= 1


# ─── Tests for _execute (via flow.execute) ────────────────────────

class TestFlowExecution:
    def test_execute_returns_valid_schema(self, example_task_dir):
        flow_fn = runner._load_flow_module("example_task")
        ctx = {"timeout_seconds": 30, "block_media": True, "task_type": "api"}
        inputs = {"target_url": "https://httpbin.org/get"}
        result = flow_fn(ctx, inputs)
        assert "success" in result
        assert "data" in result
        assert "error" in result
        assert isinstance(result["success"], bool)

    def test_execute_with_missing_url(self, example_task_dir):
        flow_fn = runner._load_flow_module("example_task")
        ctx = {"timeout_seconds": 30}
        inputs = {}
        result = flow_fn(ctx, inputs)
        assert result["success"] is False
        assert result["error"] is not None

    def test_execute_success_response(self, example_task_dir):
        flow_fn = runner._load_flow_module("example_task")
        ctx = {"timeout_seconds": 30, "pre_injected_headers": {"Accept": "application/json"}}
        inputs = {"target_url": "https://httpbin.org/get", "method": "GET"}
        result = flow_fn(ctx, inputs)
        assert result["success"] is True, f"Expected success, got: {result}"
        assert result["data"] is not None

    def test_execute_post_with_payload(self, example_task_dir):
        flow_fn = runner._load_flow_module("example_task")
        ctx = {"timeout_seconds": 30}
        inputs = {
            "target_url": "https://httpbin.org/post",
            "method": "POST",
            "payload": {"test": "data"},
        }
        result = flow_fn(ctx, inputs)
        assert result["success"] is True, f"Expected success, got: {result}"


# ─── Tests for runner.main() via subprocess ──────────────────────

class TestRunnerMain:
    def test_runs_successfully(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", '{"target_url":"https://httpbin.org/get"}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["task"] == "example_task"
        assert output["status"] == "success"
        assert "data" in output
        assert "execution_time_ms" in output

    def test_runs_post_request(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", '{"target_url":"https://httpbin.org/post","method":"POST","payload":{"x":1}}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["status"] == "success"

    def test_fails_on_404(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", '{"target_url":"https://httpbin.org/status/404"}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert output["status"] != "success"
        assert "error" in output or result.returncode == 1

    def test_fails_on_missing_task(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "nonexistent",
                "--inputs", '{}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=10,
        )
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert output["status"] == "error"
        assert "error" in output

    def test_fails_on_invalid_inputs(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", 'not-json',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=10,
        )
        assert result.returncode == 1
        output = json.loads(result.stderr)
        assert output["status"] == "error"
        assert "error" in output

    def test_fails_on_schema_validation(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", '{"method":"INVALID"}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=10,
        )
        assert result.returncode == 1
        output = json.loads(result.stdout)
        assert output["status"] == "error"
        assert "schema validation" in output["error"]

    def test_stdout_is_valid_json(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", '{"target_url":"https://httpbin.org/get"}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )
        try:
            json.loads(result.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"stdout is not valid JSON: {result.stdout}")

    def test_no_extra_logging_on_stdout(self, project_root):
        result = subprocess.run(
            [
                sys.executable, "runner.py",
                "--task", "example_task",
                "--inputs", '{"target_url":"https://httpbin.org/get"}',
            ],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )
        lines = result.stdout.strip().split("\n")
        assert len(lines) == 1, f"Expected exactly 1 line on stdout, got {len(lines)}"
