#!/usr/bin/env python3
"""Layer 2: Transformer - Parse HAR files and extract API endpoints.

GhostRunner Architecture:
  Layer 1 (Recorder): Playwright captures session.har + auth.json
  Layer 2 (Transformer): Parses HAR → extracts API endpoints, params, payloads, auth
  Layer 3 (Runner): Executes deterministic API-first task packages

This utility reads a recorded session.har, filters out static assets
(images, fonts, stylesheets, media), and produces a minimal JSON summary
that an LLM can use to generate flow.py automatically.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────
# HAR parsing helpers
# ──────────────────────────────────────────────────────────────────────

MEDIA_RESOURCE_TYPES = {
    "image",
    "font",
    "stylesheet",
    "media",
    "script",  # typically not a business API endpoint
}


def _is_media_request(entry: Dict) -> bool:
    """Determine if a HAR log entry is a static asset to exclude."""
    request = entry.get("request", {})
    resource_type = request.get("resourceType", "")
    return resource_type in MEDIA_RESOURCE_TYPES


def _extract_headers(headers_list: List[Dict]) -> Dict[str, str]:
    """Convert HAR headers list to a flat dict.

    HAR format: [{ "name": "Authorization", "value": "Bearer ..." }, ...]
    """
    result: Dict[str, str] = {}
    for h in headers_list or []:
        name = h.get("name", "")
        value = h.get("value", "")
        if name and value:
            result[name] = value
    return result


def _parse_entry(entry: Dict) -> Optional[Dict]:
    """Parse a single HAR log entry into a simplified API spec.

    Returns None if the entry should be skipped (media, telemetry, etc.).
    """
    request = entry.get("request", {})
    response = entry.get("response", {})

    # Skip media/asset requests
    if _is_media_request(entry):
        return None

    # Skip if no URL
    url = request.get("url", "")
    if not url:
        return None

    # Filter out about:blank, data:, chrome://, etc. that aren't real APIs
    if url.startswith("about:") or url.startswith("data:") or url.startswith("chrome:"):
        return None

    method = request.get("method", "GET").upper()
    query_string = request.get("queryString", [])

    # Extract query params
    params: Dict[str, str] = {}
    for p in query_string:
        name = p.get("name", "")
        value = p.get("value", "")
        if name:
            params[name] = value

    # Extract headers (auth, content-type, etc.)
    headers = _extract_headers(request.get("headers", []))

    # Extract cookies if present
    cookies = request.get("cookies", [])

    # Build the extracted spec
    spec: Dict[str, Any] = {
        "url": url,
        "method": method,
        "headers": headers,
        "params": params,
        "cookies": cookies,
    }

    # Add response status if available
    if response:
        spec["status"] = response.get("status", 0)

    return spec


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────

def parse_har_file(har_path: str) -> Dict[str, Any]:
    """Parse a session.har file and extract core API endpoints.

    Args:
        har_path: Path to the recorded session.har file.

    Returns:
        A dict with:
        - "endpoints": list of extracted API spec dicts
        - "start_url": the first navigated URL (page.goto)
        - "filtered_count": how many requests were filtered out (media)
        - "total_count": total number of HAR entries
    """
    har_path = Path(har_path)

    if not har_path.exists():
        raise FileNotFoundError(f"HAR file not found: {har_path}")

    with open(har_path, "r", encoding="utf-8") as f:
        har_data = json.load(f)

    # Navigate HAR structure: {"log": {"entries": [...]}}
    log = har_data.get("log", {})
    entries = log.get("entries", [])

    total_count = len(entries)
    filtered_count = 0
    endpoints: List[Dict[str, Any]] = []
    start_url = ""

    for entry in entries:
        # Find the first navigational entry (page.goto)
        if not start_url and entry.get("request", {}).get("pageType") == "document":
            start_url = entry.get("request", {}).get("url", "")

        parsed = _parse_entry(entry)
        if parsed is None:
            filtered_count += 1
            continue

        # Deduplicate by normalized URL + method
        normalized = (
            parsed["url"].split("?")[0].rstrip("/"),
            parsed["method"],
        )
        # Simple dedup: only add if we haven't seen this exact spec
        already = any(
            (e["url"].split("?")[0].rstrip("/"), e["method"]) == normalized
            for e in endpoints
        )
        if not already:
            endpoints.append(parsed)
        else:
            filtered_count += 1

    return {
        "start_url": start_url or inputs.get("target_url", ""),
        "endpoints": endpoints,
        "total_count": total_count,
        "filtered_count": filtered_count,
        "summary": f"Extracted {len(endpoints)} API endpoints from {total_count} total requests "
        f"(filtered {filtered_count} media/asset requests).",
    }


def generate_flow_prompt(har_summary: Dict[str, Any]) -> str:
    """Generate a system prompt for LLMs to create flow.py from HAR data.

    This is the dedicated prompt that goes into PROMPTS.md.
    The LLM should output valid Python flow.py with execute(ctx, inputs) -> dict.
    """
    endpoints = har_summary.get("endpoints", [])
    start_url = har_summary.get("start_url", "")

    prompt_parts = [
        "# GhostRunner LLM Prompt - Generate flow.py from HAR data",
        "",
        "## Context",
        "",
        "You are generating a deterministic, API-First automation flow for GhostRunner.",
        "The workflow was recorded in Layer 1 (Recorder) using Playwright.",
        "Layer 2 (Transformer) has extracted the core API endpoints from session.har.",
        "",
        "## Extracted Data",
    ]

    if start_url:
        prompt_parts.append(f"- start_url: {start_url}")

    prompt_parts.append(f"- total_endpoints_extracted: {len(endpoints)}")
    prompt_parts.append("")

    if endpoints:
        prompt_parts.append("### Endpoints")
        prompt_parts.append("")

        for i, ep in enumerate(endpoints, 1):
            prompt_parts.append(f"#### Endpoint {i}")
            prompt_parts.append(f"- URL: {ep.get('url', 'N/A')}")
            prompt_parts.append(f"- Method: {ep.get('method', 'GET')}")
            if ep.get("headers"):
                prompt_parts.append("- Headers:")
                for h_name, h_val in ep["headers"].items():
                    prompt_parts.append(f"  - {h_name}: {h_val}")
            if ep.get("params"):
                prompt_parts.append("- Query Parameters:")
                for p_name, p_val in ep["params"].items():
                    prompt_parts.append(f"  - {p_name}: {p_val}")
            if ep.get("status"):
                prompt_parts.append(f"- Response Status: {ep['status']}")
            prompt_parts.append("")

    prompt_parts += [
        "## Required flow.py Contract",
        "",
        "The generated file MUST expose exactly one function:",
        "",
        "def execute(ctx: dict, inputs: dict) -> dict:",
        "",
        "where the return value is a dict with this schema:",
        "",
        '  {"success": bool, "data": Any, "error": str | None}',
        "",
        "Guidelines:",
        "- Prefer httpx.Client for HTTP requests (API-First, deterministic, fast)",
        "- Inject auth headers from ctx or inputs (never hardcode tokens)",
        "- Validate response status codes with raise_for_status()",
        "- If a request fails, return {\"success\": False, \"data\": None, \"error\": \"...\"}",
        "- Do NOT import playwright unless the fallback path is absolutely required",
        "- Keep the function pure-deterministic: same inputs → same outputs",
        "",
        "## Example Output",
        "",
        'flow.py should look like:',
        "",
        '```python',
        'import json',
        'from typing import Any',
        '',
        'import httpx',
        '',
        'def execute(ctx: dict, inputs: dict) -> dict:',
        '    """Execute the automated workflow (API-First)."""',
        '    try:',
        '        headers = ctx.get("pre_injected_headers", {})',
        '        target = inputs.get("target_url")',
        '',
        '        if not target:',
        '            return {"success": False, "data": None, "error": "Missing target_url"}',
        '',
        '        method = inputs.get("method", "GET").upper()',
        '        payload = inputs.get("payload")',
        '',
        '        with httpx.Client(timeout=ctx.get("timeout_seconds", 30)) as client:',
        '            resp = client.request(',
        '                method=method,',
        '                url=target,',
        '                headers=headers,',
        '                json=payload if method in ('"POST'"', '"PUT'"', '"PATCH"') else None,',
        '            )',
        '            resp.raise_for_status()',
        '',
        '            try:',
        '                data = resp.json()',
        '            except Exception:',
        '                data = resp.text',
        '',
        '            return {"success": True, "data": data, "error": None}',
        '',
        '        except httpx.HTTPStatusError as e:',
        '            return {"success": False, "data": None, "error": f"HTTP {e.response.status_code}"}',
        '        except Exception as e:',
        '            return {"success": False, "data": None, "error": str(e)}',
        "",
        '```',
        "",
        "# End of prompt - generate valid Python flow.py",
    ]

    return "\n".join(prompt_parts)