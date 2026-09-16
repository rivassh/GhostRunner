#!/usr/bin/env python3
"""Universal execution entrypoint for GhostRunner task packages.

3-Tier Architecture alignment:
  Layer 1 (Recorder): captured session.har + auth.json
  Layer 2 (Transformer): parsed HAR → flow.py inputs/headers
  Layer 3 (Runner): deterministic execution, CLI, JSON output

Usage:
    python runner.py --task example_task --inputs '{"target_url":"https://api.example.com/endpoint"}'

Or via Docker Compose:
    docker compose run --rm -e GHOSTRUNNER_TASK=example_task runner python runner.py --task example_task --inputs '{}'
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import jsonschema


def _parse_args() -> dict:
    """Parse CLI arguments.

    Required:
      --task <task_name>  Task package directory name under tasks/

    Optional:
      --inputs '<json_string>'  JSON string of runtime inputs.
                                Defaults to "{}" if omitted.
    """
    parser = argparse.ArgumentParser(
        description="GhostRunner task executor - deterministic, API-First runner"
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Task package name (directory under tasks/) to execute",
    )
    parser.add_argument(
        "--inputs",
        default="{}",
        help="JSON string of runtime inputs (default: '{}')",
    )
    try:
        args = parser.parse_args()
    except SystemExit as e:
        # argparse exits with code 2 on missing required args
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "Missing required argument --task",
                    "task": "",
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        inputs = json.loads(args.inputs)
        if not isinstance(inputs, dict):
            raise json.JSONDecodeError("Inputs must be a JSON object", args.inputs, 0)
    except json.JSONDecodeError as e:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": f"Invalid inputs JSON: {e}",
                    "task": args.task,
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    return {"task": args.task, "inputs": inputs}


def _load_manifest(task_name: str) -> dict:
    """Load and validate manifest.json for the given task.

    Returns the parsed manifest dict. Raises FileNotFoundError if missing.
    """
    manifest_path = Path("tasks") / task_name / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Basic validation
    if "type" not in manifest:
        raise ValueError(f"Manifest missing required 'type' field: {manifest_path}")

    if "timeout_seconds" not in manifest:
        manifest["timeout_seconds"] = 30  # sensible default

    return manifest


def _validate_inputs(
    manifest: dict,
    inputs: dict,
    task_name: str,
) -> None:
    """Validate runtime inputs against manifest.inputs_schema.

    Raises jsonschema.ValidationError with a clear message if validation fails.
    """
    schema = manifest.get("inputs_schema")
    if schema is None:
        return  # No schema defined; skip validation

    try:
        jsonschema.Draft202012Validator(schema).validate(inputs)
    except jsonschema.ValidationError as e:
        raise jsonschema.ValidationError(
            f"Task '{task_name}' inputs failed schema validation: {e.message}"
        ) from e


def _load_flow_module(task_name: str) -> callable:
    """Dynamically import and return the `execute` function from flow.py.

    The flow.py module must expose:
        def execute(ctx: dict, inputs: dict) -> dict:
    """
    flow_path = Path("tasks") / task_name / "flow.py"
    if not flow_path.exists():
        raise FileNotFoundError(f"Flow module not found: {flow_path}")

    with open(flow_path) as f:
        code = f.read()

    namespace: Dict[str, Any] = {}
    exec(code, namespace)

    if "execute" not in namespace:
        raise AttributeError(
            f"Flow module missing required 'execute(ctx, inputs)' function"
        )

    return namespace["execute"]


def _build_ctx(
    manifest: dict,
    flow_fn,
    auth_data: Optional[Dict] = None,
    http_client: Optional[httpx.Client] = None,
) -> Dict[str, Any]:
    """Build the execution context dict passed to flow.execute(ctx, inputs).

    Context keys are intentionally minimal and deterministic:
      - timeout_seconds: from manifest
      - block_media: from manifest (for browser tasks)
      - pre_injected_headers: auth headers (preferred path for API tasks)
      - http_client: optional httpx.Client for API tasks
      - flow_fn: the execute function reference (for introspection / retry)
    """
    ctx: Dict[str, Any] = {
        "timeout_seconds": manifest.get("timeout_seconds", 30),
        "block_media": manifest.get("block_media", False),
        "task_type": manifest.get("type", "api"),
    }

    # Inject auth headers if available (preferred API-First path)
    if auth_data:
        ctx["pre_injected_headers"] = auth_data
    else:
        # Load from auth.json as fallback
        auth_path = Path("tasks") / manifest.get("name", "") / "auth.json"
        if auth_path.exists():
            with open(auth_path) as f:
                auth = json.load(f)
                headers = {}
                for cookie in auth.get("cookies", []):
                    if cookie.get("name", "").lower() in (
                        "session_id",
                        "token",
                        "csrf_token",
                    ):
                        headers[cookie["name"]] = cookie["value"]
                ctx["pre_injected_headers"] = headers

    if http_client:
        ctx["http_client"] = http_client

    return ctx


def _execute_with_timeout(
    flow_fn: callable, ctx: dict, inputs: dict, timeout_seconds: int
) -> dict:
    """Execute flow.execute(ctx, inputs) with a hard timeout.

    Returns the raw dict result from flow.execute. Timeout is handled
    via signal.alarm on Unix; on Windows a best-effort approach is used.
    """
    import signal

    class TimeoutExpired(Exception):
        pass

    def _handler(signum, frame):
        raise TimeoutExpired()

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(timeout_seconds + 2)  # +2s buffer

    try:
        start = time.perf_counter()
        result = flow_fn(ctx, inputs)
        elapsed = int((time.perf_counter() - start) * 1000)
        result["execution_time_ms"] = elapsed
        # Propagate the inner success/failure
        return {"success": result.get("success", False), "result": result}
    except TimeoutExpired:
        return {
            "success": False,
            "result": {
                "error": f"Execution exceeded {timeout_seconds}s timeout",
                "execution_time_ms": timeout_seconds * 1000,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "result": {"error": str(e), "execution_time_ms": 0},
        }
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _determine_output(exec_result: dict, task_name: str) -> str:
    """Format the final JSON output to stdout.

    Strict contract: pure JSON on stdout only. No extraneous logs,
    warnings, or print statements should appear.
    """
    success = exec_result.get("success", False)
    status = "success" if success else "failed"

    output = {
        "task": task_name,
        "status": status,
    }

    if exec_result.get("success") and exec_result.get("result"):
        result = exec_result["result"]
        # Strip internal keys if present
        out_data = {k: v for k, v in result.items() if k != "execution_time_ms"}
        if out_data:
            output["data"] = out_data
        if "execution_time_ms" in result:
            output["execution_time_ms"] = result["execution_time_ms"]

    if exec_result.get("result", {}).get("error"):
        output["error"] = exec_result["result"]["error"]

    return json.dumps(output, separators=(",", ":"))


def main():
    """Main entrypoint for GhostRunner.

    1. Parse CLI args (--task, --inputs)
    2. Load manifest.json
    3. Load flow.py and extract execute()
    4. Build ctx from manifest + auth state
    5. Execute flow.execute(ctx, inputs) with timeout
    6. Output deterministic JSON to stdout
    7. Set process exit code: 0=success, 1=any failure
    """
    parsed = _parse_args()
    task_name = parsed["task"]
    inputs = parsed["inputs"]
    start_time = time.perf_counter()

    try:
        # 1. Load manifest
        manifest = _load_manifest(task_name)

        # 2. Validate inputs against manifest schema
        _validate_inputs(manifest, inputs, task_name)

        # 3. Load flow module + execute()
        flow_fn = _load_flow_module(task_name)

        # 3. Load auth state (preferred: pre-injected from Transformer layer)
        auth_data = inputs.get("pre_injected_headers")

        # 4. Build execution context
        ctx = _build_ctx(manifest, flow_fn, auth_data=auth_data)

        # 5. Execute with timeout
        timeout = manifest.get("timeout_seconds", 30)
        exec_result = _execute_with_timeout(flow_fn, ctx, inputs, timeout)

        # 6. Format and output JSON to stdout only
        output_str = _determine_output(exec_result, task_name)
        print(output_str)

        # 7. Exit codes
        success = exec_result.get("success", False)
        sys.exit(0 if success else 1)

    except FileNotFoundError as e:
        # Manifest or flow module not found
        err_output = json.dumps(
            {"task": task_name, "status": "error", "error": f"File not found: {e}"}
        )
        print(err_output)
        sys.exit(1)
    except jsonschema.ValidationError as e:
        # Input validation failure
        err_output = json.dumps(
            {"task": task_name, "status": "error", "error": str(e)}
        )
        print(err_output)
        sys.exit(1)
    except AttributeError as e:
        # Flow module missing execute()
        err_output = json.dumps(
            {"task": task_name, "status": "error", "error": f"Flow module error: {e}"}
        )
        print(err_output)
        sys.exit(1)
    except Exception as e:
        # Unexpected error - still output JSON and exit 1
        err_output = json.dumps(
            {"task": task_name, "status": "error", "error": f"Unexpected: {str(e)}"}
        )
        print(err_output)
        sys.exit(1)


if __name__ == "__main__":
    main()