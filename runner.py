#!/usr/bin/env python3
"""Universal execution entrypoint for task packages in Docker containers."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import sync_playwright


def _parse_args() -> dict:
    parser = argparse.ArgumentParser(description="Task executor", exit_on_error=False)
    parser.add_argument("--task", required=True, help="Task name to execute")
    parser.add_argument("--inputs", default="{}", help="JSON string of runtime inputs")
    args = parser.parse_args()
    try:
        inputs = json.loads(args.inputs)
    except json.JSONDecodeError as e:
        print(json.dumps({"task": args.task, "status": "failed", "result": None, "execution_time_ms": 0, "error": f"Invalid inputs JSON: {e}"}), file=sys.stderr)
        sys.exit(1)
    return {"task": args.task, "inputs": inputs if isinstance(inputs, dict) else {}}


def _load_manifest(task_name: str) -> dict:
    manifest_path = Path("tasks") / task_name / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    with open(manifest_path) as f:
        return json.load(f)


def _load_flow_module(task_name: str):
    flow_path = Path("tasks") / task_name / "flow.py"
    if not flow_path.exists():
        raise FileNotFoundError(f"Flow module not found: {flow_path}")

    with open(flow_path) as f:
        code = f.read()

    namespace: dict[str, Any] = {}
    exec(code, namespace)

    if "execute" not in namespace:
        raise AttributeError(f"Flow module missing 'execute' function")

    return namespace["execute"]


def _create_browser_context(task_name: str, manifest: dict):
    auth_path = Path("tasks") / task_name / "auth.json"
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    storage_state = str(auth_path) if auth_path.exists() else None

    context = browser.new_context(storage_state=storage_state)

    if manifest.get("block_media", False):
        media_types = {"image", "font", "stylesheet", "media"}

        def _route_handler(route, request):
            if request.resource_type in media_types:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", _route_handler)

    return context, browser, playwright


def _execute(flow_fn, ctx: dict, inputs: dict) -> dict:
    start = time.perf_counter()
    try:
        result = flow_fn(ctx, inputs)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {"success": True, "result": result, "execution_time_ms": elapsed_ms}
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {"success": False, "result": None, "execution_time_ms": elapsed_ms, "error": str(e)}


def main():
    args = _parse_args()
    task_name = args["task"]
    inputs = args["inputs"]
    start_time = time.perf_counter()

    ctx = {}
    browser_context = None
    http_client = None

    try:
        manifest = _load_manifest(task_name)
        flow_fn = _load_flow_module(task_name)

        timeout_seconds = manifest.get("timeout_seconds", 60)

        if manifest.get("type") == "browser":
            browser_context, browser, playwright = _create_browser_context(task_name, manifest)
            ctx = {
                "timeout_seconds": timeout_seconds,
                "headless": True,
                "browser_context": browser_context,
                "browser": browser,
                "playwright": playwright,
            }
        else:
            http_client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)
            ctx = {
                "timeout_seconds": timeout_seconds,
                "http_client": http_client,
            }

        exec_result = _execute(flow_fn, ctx, inputs)

        total_ms = int((time.perf_counter() - start_time) * 1000)

        output = {
            "task": task_name,
            "status": "success" if exec_result["success"] else "failed",
            "result": exec_result["result"],
            "execution_time_ms": max(exec_result["execution_time_ms"], 0),
        }

        if exec_result.get("error"):
            output["error"] = exec_result["error"]

        print(json.dumps(output))
        sys.exit(0 if exec_result["success"] else 1)

    except FileNotFoundError as e:
        output = {"task": task_name, "status": "failed", "result": None, "execution_time_ms": 0, "error": f"File not found: {e}"}
        print(json.dumps(output))
        sys.exit(1)
    except Exception as e:
        output = {"task": task_name, "status": "failed", "result": None, "execution_time_ms": 0, "error": str(e)}
        print(json.dumps(output))
        sys.exit(1)
    finally:
        if browser_context:
            browser_context.close()
        if "browser" in locals():
            browser.close()
        if "playwright" in locals():
            playwright.stop()
        if http_client:
            http_client.close()