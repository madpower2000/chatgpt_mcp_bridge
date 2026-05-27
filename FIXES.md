# FIXES.md — Changelog

## v0.3.0 (2026-05-27)

### Finding 1 — Real Hermes invocation (was placeholder)

**Problem:** `chatgpt_agent_start()` returned a fake response:
`"Job {job_id} executed. Prompt: {prompt[:100]}"`.

**Fix:** Implemented `run_hermes_agent_job()` that invokes the real Hermes Agent
via subprocess: `hermes chat -q <prompt> --quiet [--model <m>] [--max-turns <n>]
[--tools <t>]`. The subprocess is monitored with a 10-minute hard timeout.
Response is parsed from stdout; session_id is extracted from stderr.

**Files changed:** `tools.py` (complete rewrite of agent invocation logic)

### Finding 2 — Telegram start notification sent twice

**Problem:** `notify_start()` was called in both `chatgpt_agent_start()` (MCP handler)
and `_start_background_job()` (background thread), resulting in duplicate
Telegram messages.

**Fix:** Removed the `notify_start()` call from `_start_background_job()`.
Now only `chatgpt_agent_start()` sends the start notification. The background
thread only sends `notify_complete()` or `notify_error()`.

**Files changed:** `tools.py`

### Finding 3 — install_services() returns ok=true even when unit files not written

**Problem:** When `python3` or `cloudflared` were not found, the function
would skip writing unit files but still return `ok=true`.

**Fix:** Added comprehensive validation in `_resolve_config()`. If any required
binary is missing or any validation fails, `errors` list is populated,
`ok=false`, `ready=false`, and no files are written. No `daemon-reload`
or `systemctl enable` is attempted on failure.

**Files changed:** `services.py` (complete rewrite of validation logic)

### Finding 4 — Double JSON encoding

**Problem:** `services.py` functions returned JSON strings via `json.dumps()`,
and `tools.py` called `json.dumps()` again, producing double-encoded output
like `"\"{...}\""` instead of `{...}`.

**Fix:** All `services.py` functions now return Python dicts. `tools.py`
calls `json.dumps()` exactly once when returning to the MCP client.

**Files changed:** `services.py`, `tools.py`, `schemas.py`, `cli.py`

### Finding 5 — Cancellation was cosmetic

**Problem:** `chatgpt_agent_cancel()` set the cancel event and updated status
to cancelled, but `_start_background_job()` never checked the cancel token.
The agent would keep running.

**Fix:** `_start_background_job()` now:
1. Checks cancel event before starting the subprocess
2. Uses `subprocess.Popen` instead of `subprocess.run` for monitoring
3. Polls the process with `proc.poll()` every 0.5s
4. If cancel event fires: sends SIGTERM, waits 5s, then SIGKILL
5. After subprocess exits, checks if cancel was requested — if so,
   sets status to `cancelled` instead of `done`
6. Telegram mirror sends cancellation notification

**Files changed:** `tools.py`

### Finding 6 — Standalone MCP parameter name mismatch

**Problem:** Standalone server in `__init__.py` used parameter name `tools_arg`
while schemas/docs used `tools`.

**Fix:** Renamed parameter to `tools` in the standalone server wrapper.
Used `from . import tools as bridge_tools` to avoid name conflict with
the `tools` parameter.

**Files changed:** `__init__.py`

### Finding 7 — Systemd unit generation missing validation

**Problem:** Unit files were generated with raw values — no validation of
host, port, tunnel_mode, named_tunnel, or paths.

**Fix:** Added comprehensive validation:
- `host`: must be one of 127.0.0.1, localhost, 0.0.0.0
- `port`: must be integer 1024-65535
- `tunnel_mode`: must be "quick" or "named"
- `named_tunnel`: required for named mode, must match `[A-Za-z0-9_.-]+`
- `python_path`/`cloudflared_path`: must be absolute paths if provided
- `working_dir`: must exist and be a directory
- Path values checked for control characters
- No `shell=True` used anywhere
- Unit file values use simple absolute paths

**Files changed:** `services.py`

### Additional changes

- **README.md:** Updated with real Hermes invocation docs, cancellation
  limitations, quick vs named tunnel docs, tools parameter format,
  log commands, architecture diagram, validation details
- **plugin.yaml:** Version bumped to 0.3.0
- **schemas.py:** Updated to match new function signatures, added
  `InstallServicesResult` and `ServiceStatus` models
- **telegram_mirror.py:** Added `notify_cancelled()` method
- **cli.py:** Updated to handle dict return values from services.py
- **__main__.py:** Simplified entry point logic
