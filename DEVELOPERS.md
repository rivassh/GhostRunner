# Developer's Guide

## Prerequisites

- Python 3.10+
- Docker with Docker Compose v2 (optional)
- Playwright Python package

## Architecture

GhostRunner is a two-stage automation pipeline:

1. **`recorder.py`** — Headful Playwright browser that captures a human-driven session, exporting `session.har` (network traces), `auth.json` (cookies + localStorage), and `raw_meta.json` (session summary).
2. **`runner.py`** — Universal Docker entrypoint that loads a task package (`manifest.json` + `flow.py`) and executes it deterministically, returning pure JSON to stdout.

Task packages live under `tasks/<task_name>/`:
- `manifest.json` — execution type (`api` or `browser`), timeout, media blocking
- `auth.json` — cleaned browser state (sensitive, never commit)
- `flow.py` — `execute(ctx, inputs)` entrypoint

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run the Recorder

```bash
python recorder.py
```

The browser opens headful. Complete login, 2FA, captcha, navigation, and submission manually, then press Enter in the terminal.

Artifacts generated:
- `auth.json` — cookies & localStorage (sensitive, never commit)
- `session.har` — network traces (sensitive, never commit)
- `raw_meta.json` — session summary

## Run with Docker Compose

```bash
docker compose up --build
```

The container mounts the working directory, so artifacts appear on the host.

## Execute a Task

```bash
python runner.py --task web_workflow --inputs '{"target_url":"https://example.com"}'
```

Or inside Docker:

```bash
docker compose run --rm recorder python runner.py --task web_workflow --inputs '{}'
```

Output is pure JSON to stdout:

```json
{
  "task": "web_workflow",
  "status": "success",
  "result": {},
  "execution_time_ms": 1234
}
```

Exit codes: `0` = success, `1` = failure/exception.

## Creating a New Task Package

1. Record a session with `recorder.py`.
2. Analyze `session.har` to identify the business API endpoint.
3. Create `tasks/<task_name>/` with:
   - `manifest.json` (type, timeout_seconds, block_media)
   - `auth.json` (copy from recorder output, cleaned)
   - `flow.py` implementing `execute(ctx, inputs) -> dict`
4. Prefer API replay via `httpx`; fall back to Playwright only when dynamic signatures or anti-bot mechanisms block direct HTTP calls.

## Verify a Flow

```bash
python3 -m py_compile tasks/<task_name>/flow.py
```

## Security

- Never commit `auth.json`, `session.har`, `*.har`, `.env`, or credential files.
- Rotate tokens captured in HAR files before sharing.
- Store production secrets in environment variables or a secret manager.
- `runner.py` prints only JSON to stdout; any debug output breaks the output contract.

## Troubleshooting

- **Browser fails to launch in Docker**: ensure `playwright install --with-deps chromium` ran and `shm_size: 1gb` is set in `docker-compose.yml`.
- **HAR not flushed**: ensure context/page are closed before process exit.
- **Auth not persisted**: verify `auth.json` was written before closing the browser context.
- **Task timeout**: increase `timeout_seconds` in `manifest.json` or optimize the flow.

## Contribution Workflow

1. Create a feature branch.
2. Keep changes focused and minimal.
3. Run `python3 -m py_compile` on modified Python files.
4. Push and open a pull request.
