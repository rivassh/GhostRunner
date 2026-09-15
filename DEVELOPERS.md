# Developer's Guide

## Prerequisites

- Python 3.10+
- Docker with Docker Compose v2 (optional)
- Playwright Python package

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

## Task Package

Generated automation flows live under `tasks/<task_name>/`:

- `manifest.json` — execution type, timeout, media blocking
- `auth.json` — cleaned browser state
- `flow.py` — `execute(ctx, inputs)` entrypoint

Verify a flow:

```bash
python3 -m py_compile tasks/<task_name>/flow.py
```

## Security

- Never commit `auth.json`, `session.har`, `*.har`, `.env`, or credential files.
- Rotate tokens captured in HAR files before sharing.
- Store production secrets in environment variables or a secret manager.

## Workflow

1. Record a session with `recorder.py`.
2. Analyze `session.har` to identify the business API endpoint.
3. Generate a task package under `tasks/<task_name>/`.
4. Prefer API replay via `httpx`; fall back to Playwright only when needed.
5. Validate the flow with `py_compile` and a test execution.
