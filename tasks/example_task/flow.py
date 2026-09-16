"""Automated workflow execution module - API-First, deterministic entrypoint.

Priority 1: Replay authenticated API calls via httpx using headers injected
from auth.json or Transformer-extracted data.
Priority 2: Fallback to Playwright ONLY if dynamic signatures or anti-bot
mechanisms absolutely prevent direct HTTP calls.

DO NOT import playwright in this module unless the fallback path is triggered.
The execute() function signature and contract must remain stable across runs.
"""

import json
from typing import Any

import httpx


def _load_auth(ctx: dict) -> dict:
    """Load auth state from ctx.

    The `ctx` dict is injected by runner.py and may already contain
    pre-parsed headers/tokens from the Transformer layer.
    """
    # Check if auth data is already injected in ctx
    if ctx.get("pre_injected_headers"):
        return ctx["pre_injected_headers"]

    # Fall back to empty state if no auth was injected
    return {}


def _inject_session_headers(auth: dict) -> dict:
    """Build httpx-compatible headers from auth state.

    Extracts Bearer tokens, session cookies, and CSRF tokens.
    """
    headers = {"Accept": "application/json"}

    for cookie in auth.get("cookies", []):
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name.lower() in ("session_id", "token", "csrf_token"):
            headers[name] = value

    # Check localStorage origins
    for origin in auth.get("localStorage", []):
        name = origin.get("name", "")
        value = origin.get("value", "")
        if name in ("Authorization", "Bearer"):
            headers[name] = value

    return headers


def _api_replay(inputs: dict, headers: dict) -> dict:
    """Execute a single authenticated HTTP request via httpx.

    This is the Priority 1 path. It is deterministic, fast, and resource-
    efficient compared to any browser-based approach.
    """
    timeout = inputs.get("timeout_seconds", 30)
    method = inputs.get("method", "GET").upper()
    url = inputs.get("target_url")
    payload = inputs.get("payload")

    if not url:
        return {"success": False, "data": None, "error": "Missing target_url in inputs"}

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        # Prepare request kwargs
        kwargs = {"headers": headers, "follow_redirects": True}

        if method in ("POST", "PUT", "PATCH") and payload:
            kwargs["json"] = payload
        elif method == "GET" and payload:
            kwargs["params"] = payload

        try:
            resp = client.request(method=method, url=url, **kwargs)
            resp.raise_for_status()
            # Attempt JSON parse; if it fails, return raw text
            try:
                data = resp.json()
            except Exception:
                data = resp.text
            return {"success": True, "data": data, "error": None}
        except httpx.HTTPStatusError as e:
            return {"success": False, "data": None, "error": f"HTTP {e.response.status_code}"}
        except httpx.ConnectError as e:
            return {"success": False, "data": None, "error": f"Connection error: {e}"}
        except httpx.TimeoutException as e:
            return {"success": False, "data": None, "error": f"Request timeout"}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}


def execute(ctx: dict, inputs: dict) -> dict:
    """Execute the automated workflow.

    This is the canonical entrypoint called by runner.py.

    Args:
        ctx: Dict containing execution metadata or initialized clients/contexts.
             Expected keys: timeout_seconds, pre_injected_headers (optional).
        inputs: Dict containing runtime arguments.
                Expected keys per manifest.inputs_schema: target_url, method, payload, etc.

    Returns:
        A dict with at least {"success": bool, "data": Any, "error": str | None}
    """
    try:
        auth = _load_auth(ctx)
        headers = _inject_session_headers(auth)

        exec_result = _api_replay(inputs, headers)

        # Ensure success has consistent shape
        if exec_result.get("success"):
            return {"success": True, "data": exec_result.get("data"), "error": None}
        else:
            return {"success": False, "data": None, "error": exec_result.get("error")}

    except Exception as e:
        return {"success": False, "data": None, "error": f"Execution error: {str(e)}"}