# GhostRunner System Prompts

## Purpose

This directory contains the dedicated system prompts that LLMs can use to
generate deterministic, API-First `flow.py` files from recorded HAR session data.

The GhostRunner 3-tier architecture ensures:

1. **Layer 1 (Recorder):** Captures browser interactions via Playwright → `session.har` + `auth.json`
2. **Layer 2 (Transformer):** Parses HAR → extracts endpoints, params, headers, auth → HAR summary JSON
3. **Layer 3 (Runner):** Executes the generated `flow.py` deterministically via CLI

---

## Available Prompts

### `har_to_flow.md`

**System prompt for LLMs** to generate a valid `flow.py` from extracted HAR data.

See `transformer.py` `generate_flow_prompt()` for the exact prompt text that
can be piped to an LLM (e.g., via `anthropic`, `openrouter`, `openai` APIs).

The generated `flow.py` MUST adhere to this contract:

```python
def execute(ctx: dict, inputs: dict) -> dict:
    """
    ctx: Dict containing execution metadata or initialized clients/contexts.
         Expected keys:
           - timeout_seconds (int)
           - pre_injected_headers (dict) — preferred API-First path
           - block_media (bool) — from manifest
    inputs: Dict containing runtime arguments.
            Expected keys per manifest.inputs_schema:
              - target_url (str, required)
              - method (str, one of GET/POST/PUT/DELETE/PATCH, default GET)
              - payload (dict | None, JSON body for POST/PUT/PATCH)
    Returns: A dict with at least {"success": bool, "data": Any, "error": str | None}
    """
```

---

## Usage Workflow

```text
1. Record a session:    python recorder.py
   → Produces: session.har, auth.json, raw_meta.json

2. Extract endpoints: python -c "
   from transformer import parse_har_file, generate_flow_prompt
   har = parse_har_file('session.har')
   prompt = generate_flow_prompt(har)
   print(prompt)
   " > llm_prompt.txt

3. Send to LLM: pipe llm_prompt.txt to your preferred LLM API.

4. Receive flow.py: LLM returns valid Python.

5. Verify: python3 -m py_compile tasks/<task_name>/flow.py

6. Execute: python runner.py --task <task_name> --inputs '{"target_url":"..."}'
```

---

## Prompt Library

| Prompt Name | Description | Usage |
|-------------|-------------|-------|
| `har_to_flow.md` | Generates `flow.py` from HAR-extracted endpoints | Primary prompt for LLM-assisted task creation |
| `minimal_api_prompt.md` | Minimal prompt for simple GET endpoints | Quick tasks without auth |
| `auth_aware_prompt.md` | Prompt when HAR contains Bearer/CSRF tokens | Tasks with session management |

Each prompt guarantees the output `flow.py` will expose `execute(ctx, inputs) -> dict`
and prioritize API-First HTTP replay via `httpx` over browser-based fallback.
```

---

## Customizing Prompts

You can create additional prompt variations by editing the prompt templates.
The only hard requirement is that the generated `flow.py` must:

1. Expose `def execute(ctx: dict, inputs: dict) -> dict`
2. Return `{"success": bool, "data": Any, "error": str | None}`
3. Prioritize `httpx` over Playwright
4. Not hardcode credentials; use `ctx["pre_injected_headers"]` or `inputs` instead