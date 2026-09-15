"""Automated workflow execution module.

Priority 1: Replay authenticated API calls via httpx using credentials
from auth.json / HAR session traces.
Priority 2: Fallback to Playwright with saved browser state for
dynamic client-side signatures or anti-bot mechanisms.
"""

import json
import os
from typing import Any

import httpx

from playwright.sync_api import sync_playwright


def _load_auth() -> dict:
    path = os.path.join(os.path.dirname(__file__), "auth.json")
    with open(path) as f:
        return json.load(f)


def _get_session_headers(auth: dict) -> dict:
    headers = {"Accept": "application/json"}
    for cookie in auth.get("cookies", []):
        if cookie.get("name") in ("session_id", "token", "csrf_token"):
            headers[cookie["name"]] = cookie["value"]
    for origin in auth.get("origins", []):
        for item in origin.get("localStorage", []):
            if item.get("name") in ("Authorization", "Bearer"):
                headers["Authorization"] = item["value"]
    return headers


def _api_replay(ctx: dict, inputs: dict, headers: dict) -> dict:
    timeout = ctx.get("timeout_seconds", 60) * 1000
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        url = inputs.get("target_url", inputs.get("endpoint"))
        method = inputs.get("method", "GET").upper()
        payload = inputs.get("payload", None)
        resp = client.request(
            method=method,
            url=url,
            headers=headers,
            json=payload if method in ("POST", "PUT", "PATCH") else None,
            params=payload if method == "GET" else None,
        )
        resp.raise_for_status()
        return {"success": True, "data": resp.json(), "error": None}


def _playwright_fallback(ctx: dict, inputs: dict) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=ctx.get("headless", True))
        context = browser.new_context(storage_state="auth.json")
        page = context.new_page()
        page.goto(inputs["target_url"], wait_until="domcontentloaded")

        for selector, value in inputs.get("form_fill", {}).items():
            page.fill(selector, value)

        if inputs.get("submit_selector"):
            page.click(inputs["submit_selector"])
            page.wait_for_load_state("networkidle")

        result = page.content()
        context.close()
        browser.close()
        return {"success": True, "data": result, "error": None}


def execute(ctx: dict, inputs: dict) -> dict:
    """Execute the automated workflow.

    Args:
        ctx: Execution metadata (timeout_seconds, headless, etc.).
        inputs: Runtime arguments (target_url, method, payload, etc.).

    Returns:
        {"success": bool, "data": Any, "error": str | None}
    """
    try:
        auth = _load_auth()
        headers = _get_session_headers(auth)
        return _api_replay(ctx, inputs, headers)
    except httpx.HTTPStatusError as e:
        return {"success": False, "data": None, "error": f"API error: {e.response.status_code}"}
    except (httpx.ConnectError, httpx.TimeoutError):
        try:
            return _playwright_fallback(ctx, inputs)
        except Exception as fallback_err:
            return {"success": False, "data": None, "error": f"Fallback error: {fallback_err}"}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}
