# GhostRunner

API-First, Deterministic, Zero-Bloat browser automation orchestration.

GhostRunner eliminates browser overhead and over-engineering (no Redis, Kafka, or heavy cloud browser dependencies). It uses a 3-tier architecture to capture browser sessions, transform them into deterministic API-first task packages, and execute those tasks in a lightweight, isolated runtime.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         GhostRunner 3-Tier Architecture                 │
└─────────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐       ┌──────────────┐       ┌──────────────┐
  │  Layer 1      │       │  Layer 2      │       │  Layer 3      │
  │  Recorder     │ ────▶ │  Transformer  │ ────▶ │  Runner       │
  │  (Playwright) │       │  (HAR Parser) │       │  (Deterministic)│
  └──────────────┘       └──────────────┘       └──────────────┘
        │                        │                        │
   Captures               Parses HAR               Executes flow.py
   session.har +          → Extracts             via httpx (API-First)
   auth.json              endpoints, params,       or Playwright fallback
                          payloads, auth             with strict timeout
                          headers
```

### Data Flow

1. **Recorder** launches a real browser, navigates to a target URL, and records all network traffic into `session.har` while saving browser state (cookies, tokens, localStorage) into `auth.json`.
2. **Transformer** parses the HAR file, filters static assets (images, fonts, CSS), extracts API endpoints, and generates a structured spec used by an LLM (or developer) to create `flow.py`.
3. **Runner** loads the task package (`manifest.json`, `auth.json`, `flow.py`), injects auth headers and config into the execution context, and runs `execute(ctx, inputs)` deterministically.

## Task Package Specification

All tasks live under `tasks/<task_name>/` and must follow this strict contract:

```
tasks/
  <task_name>/
    ├── manifest.json    # Task metadata, schedule/timeout configs, inputs schema
    ├── auth.json        # State/session snapshot (cookies, tokens, localStorage)
    └── flow.py          # Entrypoint: def execute(ctx, inputs: dict) -> dict:
```

### `manifest.json`

| Field              | Type     | Required | Description                                         |
|--------------------|----------|----------|-----------------------------------------------------|
| `type`             | string   | Yes      | `"api"` or `"browser"`                              |
| `timeout_seconds`  | integer  | No       | Default: 30. Max execution time before timeout.     |
| `block_media`      | boolean  | No       | Default: false. Block static assets in browser mode.|
| `inputs_schema`    | object   | No       | JSON Schema describing valid runtime inputs.        |

Example:

```json
{
  "type": "api",
  "timeout_seconds": 30,
  "block_media": true,
  "inputs_schema": {
    "type": "object",
    "properties": {
      "target_url": {"type": "string"},
      "method": {"enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
      "payload": {"type": ["object", "null"]}
    },
    "required": ["target_url"]
  }
}
```

### `auth.json`

Session snapshot containing authentication state. Standardized format:

```json
{
  "cookies": [
    {"name": "session_id", "value": "...", "domain": "...", "path": "/"}
  ],
  "localStorage": [
    {"name": "Authorization", "value": "Bearer ..."}
  ]
}
```

### `flow.py`

The canonical entrypoint. Must expose exactly one function:

```python
def execute(ctx: dict, inputs: dict) -> dict:
    """Execute the automated workflow.

    Args:
        ctx: Execution context injected by runner.py.
             Contains: timeout_seconds, pre_injected_headers, block_media, task_type.
        inputs: Runtime arguments validated against manifest.inputs_schema.
                Required: target_url. Optional: method, payload, headers.

    Returns:
        {"success": bool, "data": Any, "error": str | None}
    """
```

**Rules:**
- Priority 1: Use `httpx` for HTTP requests (fast, deterministic, resource-efficient).
- Priority 2: Fallback to Playwright only if dynamic signatures or anti-bot mechanisms require it.
- Never hardcode credentials. Use `ctx["pre_injected_headers"]` or read from `auth.json`.
- Keep the function deterministic: same inputs → same outputs.

## Quickstart Guide

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (for containerized runs)

### Local Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### 1. Record a Session (Layer 1)

```bash
python recorder.py
```

This will:
1. Launch a browser (headed mode)
2. Navigate to the target URL
3. Wait for you to perform login, 2FA, navigation, etc.
4. Save `session.har` (network traffic) and `auth.json` (session state)
5. Save `raw_meta.json` (optional human annotations)

### 2. Generate Flow via Transformer (Layer 2)

```bash
# Extract API endpoints from the HAR file
python -c "
from transformer import parse_har_file, generate_flow_prompt
har = parse_har_file('session.har')
prompt = generate_flow_prompt(har)
print(prompt)
" > llm_prompt.txt
```

Send `llm_prompt.txt` to an LLM (e.g., via API). The LLM will return a `flow.py` file.

Save the generated `flow.py` as `tasks/<task_name>/flow.py`.

Verify syntax:

```bash
python3 -m py_compile tasks/<task_name>/flow.py
```

### 3. Run a Task Locally (Layer 3)

```bash
# Basic execution
python runner.py --task example_task --inputs '{"target_url":"https://api.example.com/endpoint"}'

# With custom inputs
python runner.py --task example_task --inputs '{"target_url":"https://api.example.com/data","method":"POST","payload":{"key":"value"}}'
```

**Output:** Pure JSON on stdout:

```json
{"task":"example_task","status":"success","data":{"result":"..."}}
```

or on error:

```json
{"task":"example_task","status":"error","error":"File not found: ..."}
```

Exit code: `0` = success, `1` = failure.

### 4. Run via Docker Compose

```bash
# Build all services
docker compose build

# Run a task via the runner service
docker compose run --rm runner --task example_task --inputs '{"target_url":"https://api.example.com/"}'
```

See `docker-compose.yml` for service definitions and volume mounts.

## Project Structure

```
GhostRunner/
├── recorder.py           # Layer 1: Playwright session recorder
├── transformer.py        # Layer 2: HAR parser & endpoint extractor
├── runner.py             # Layer 3: Deterministic task executor
├── PROMPTS.md            # LLM system prompts for flow.py generation
├── README.md             # This file
├── requirements.txt      # Python dependencies
├── Dockerfile            # Container image
├── docker-compose.yml    # Multi-service orchestration
├── .gitignore
└── tasks/
    └── example_task/
        ├── manifest.json # Task metadata and config
        ├── auth.json     # Session/auth state
        └── flow.py       # Execution entrypoint
```

## Configuration

### Environment Variables

| Variable               | Default | Description                     |
|------------------------|---------|---------------------------------|
| `PYTHONUNBUFFERED`     | (none)  | Set to `1` for real-time logs in Docker |

### Docker Compose Services

| Service  | Purpose                        | Command                      |
|----------|--------------------------------|------------------------------|
| recorder | Launch browser & capture     | `python recorder.py`         |
| runner   | Execute task packages          | `python runner.py --task X`  |

## Troubleshooting

| Issue                          | Solution                                       |
|--------------------------------|------------------------------------------------|
| `Manifest not found`           | Ensure `tasks/<task_name>/manifest.json` exists  |
| `Flow module missing execute`  | Verify `flow.py` has `def execute(ctx, inputs)` |
| `Invalid inputs JSON`          | Check `--inputs` is valid JSON object string    |
| Browser tasks timeout          | Increase `timeout_seconds` in manifest.json     |
| Auth failures                  | Re-record session; verify `auth.json` structure  |

## Development

```bash
# Run linter/typechecker (if configured)
ruff check .
mypy .
```

## License

Internal use only.