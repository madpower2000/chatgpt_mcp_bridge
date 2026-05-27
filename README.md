# chatgpt_mcp_bridge

Async MCP interface for ChatGPT Web → Hermes Agent.

## Overview

This plugin provides a standalone FastMCP server that exposes 8 tools for
dispatching, monitoring, and managing Hermes Agent jobs from ChatGPT Web
(or any MCP-compatible client), plus systemd service management for the
standalone MCP server and Cloudflare tunnel.

## Installation

```bash
# Plugin is already in ~/.hermes/plugins/chatgpt_mcp_bridge/
# Just enable it:

hermes plugins enable chatgpt_mcp_bridge
```

## CLI Commands

Manage the bridge server and systemd services from the terminal:

```bash
# Check status (services, PID, memory, MCP endpoint)
chatgpt_mcp_bridge status

# Install systemd services (dry run first)
chatgpt_mcp_bridge install --dry-run

# Install and enable systemd services
chatgpt_mcp_bridge install

# Install with named tunnel
chatgpt_mcp_bridge install --tunnel-mode named --named-tunnel hermes-mcp

# Start bridge + tunnel
chatgpt_mcp_bridge start

# Stop tunnel then bridge
chatgpt_mcp_bridge stop

# Uninstall (stop, disable, remove unit files)
chatgpt_mcp_bridge uninstall

# Show Cloudflare tunnel URL
chatgpt_mcp_bridge tunnel-url
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--dry-run` | false | Only render unit files without writing |
| `--tunnel-mode` | quick | `quick` or `named` |
| `--named-tunnel` | "" | Tunnel name (required for named mode) |
| `--host` | 127.0.0.1 | Bind address |
| `--port` | 9100 | Port for MCP server |

## Running the bridge server

### Standalone MCP server

```bash
# Run as standalone server (default port 9100)
python -m chatgpt_mcp_bridge

# Custom port
python -m chatgpt_mcp_bridge --port 9101 --host 0.0.0.0
```

### Via systemd (recommended for production)

```bash
# 1. Install services (dry run first)
chatgpt_mcp_bridge install --dry-run

# 2. Install and enable
chatgpt_mcp_bridge install

# 3. Start services
chatgpt_mcp_bridge start

# 4. Check status
chatgpt_mcp_bridge status

# 5. Stop services
chatgpt_mcp_bridge stop
```

### Manual systemd commands

```bash
# View logs
journalctl --user -u chatgpt-mcp-bridge.service -f
journalctl --user -u chatgpt-mcp-cloudflared.service -f

# Start/stop manually
systemctl --user start chatgpt-mcp-bridge.service
systemctl --user start chatgpt-mcp-cloudflared.service
systemctl --user stop chatgpt-mcp-cloudflared.service
systemctl --user stop chatgpt-mcp-bridge.service
```

### Quick tunnel vs Named tunnel

- **Quick tunnel** (`tunnel_mode="quick"`): Cloudflare generates a random
  URL like `https://abc123.trycloudflare.com`. Changes every restart.
  Good for testing.

- **Named tunnel** (`tunnel_mode="named"`): Uses a persistent URL like
  `https://hermes-mcp.trycloudflare.com`. Requires `--named-tunnel NAME`.
  Good for production use.

## MCP Tools

These tools are available via the MCP interface (from ChatGPT Web or any MCP client).

### Agent lifecycle tools

#### chatgpt_agent_start

Start a new agent job. Runs a real Hermes Agent via subprocess fallback
(`hermes chat -q ...`). Returns job_id immediately.

**Parameters:**
- `prompt` (required) — User prompt to send to Hermes Agent
- `model` — Model to use (empty = default)
- `max_iterations` — Max tool-use iterations (default 50)
- `tools` — JSON array of tool names to enable (e.g. `'["web","terminal"]'`)
- `context` — Additional context for the agent
- `rules` — Additional rules/instructions
- `system_prompt` — Override system prompt
- `mirror_to_telegram` — Mirror messages to Telegram (default false)
- `telegram_target` — Telegram target (e.g. `telegram:528368879`)

**Returns:** JSON with `job_id`, `status`, `prompt_preview`.

**Hermes Agent execution:**
The job runs via `hermes chat -q <prompt>` subprocess with optional
`--model`, `--max-turns`, `--tools` flags. The subprocess is monitored
with a 10-minute hard timeout. Cancellation sends SIGTERM then SIGKILL.

#### chatgpt_agent_status

Get job status.

**Parameters:**
- `job_id` — Job ID from chatgpt_agent_start

**Returns:** JSON with status (`queued`|`running`|`done`|`error`|`cancelled`),
timestamps, iterations.

#### chatgpt_agent_result

Get job result.

**Parameters:**
- `job_id` — Job ID from chatgpt_agent_start

**Returns:** JSON with `response`, `error`, and job metadata.

#### chatgpt_agent_cancel

Cancel a running job.

For subprocess-based backend, sends SIGTERM to the `hermes` process,
waits 5 seconds, then SIGKILL if still running.

**Parameters:**
- `job_id` — Job ID to cancel

**Returns:** JSON with cancellation result.

**Cancellation limitations:**
- Only works for jobs that are `running` or `queued`.
- Subprocess cancellation terminates the `hermes chat` process.
- If the job has already completed (done/error), cancellation is rejected.

### Bridge & service management tools

#### chatgpt_bridge_status

Get bridge health, JobStore stats, AND systemd service status.

**Parameters:**
- `job_id` — Optional. Empty = general bridge + service status.

**Returns:** JSON with:
- total jobs, status counts, recent jobs
- systemd bridge service status (active/inactive)
- systemd tunnel service status (active/inactive)
- local MCP URL: `http://127.0.0.1:9100/mcp`
- helpful commands (systemctl, journalctl)

#### chatgpt_bridge_install_services

Install systemd user services for the bridge and Cloudflare tunnel.

Generates two unit files in `~/.config/systemd/user/`:
1. `chatgpt-mcp-bridge.service` — runs `python -m chatgpt_mcp_bridge`
2. `chatgpt-mcp-cloudflared.service` — runs Cloudflare tunnel

**Parameters:**
- `mode` — Service mode (only `"user"` supported, default `"user"`)
- `tunnel_mode` — `"quick"` or `"named"` (default `"quick"`)
- `host` — Bind address (default `"127.0.0.1"`)
- `port` — Port for MCP server (default `9100`)
- `named_tunnel` — Tunnel name (required if `tunnel_mode="named"`)
- `working_dir` — Working directory (default: home)
- `python_path` — Path to python3 (default: auto-detect)
- `cloudflared_path` — Path to cloudflared (default: auto-detect)
- `dry_run` — If true, only render unit contents without writing (default `false`)
- `enable` — If true, `systemctl --user enable` both services (default `true`)

**Returns:** JSON with `ok`, `ready`, `dry_run`, `warnings`, `errors`,
`paths`, `daemon_reload`, `enable_results`, `bridge_unit`, `cloudflared_unit`.

**Validation:**
- `host` must be one of: 127.0.0.1, localhost, 0.0.0.0
- `port` must be integer 1024-65535
- `tunnel_mode` must be "quick" or "named"
- `named_tunnel` required for named mode, must match `[A-Za-z0-9_.-]+`
- `python_path` and `cloudflared_path` must be absolute paths if provided
- `working_dir` must exist and be a directory
- If any validation fails, `ok=false` and files are NOT written

#### chatgpt_bridge_start_services

Start both bridge and tunnel systemd user services.

**Parameters:** none

**Returns:** JSON with start results for each service + journalctl hint.

#### chatgpt_bridge_stop_services

Stop tunnel first, then bridge.

**Parameters:** none

**Returns:** JSON with stop results for each service.

## Architecture

```
ChatGPT Web (MCP client)
    |
    v
Standalone FastMCP Server (port 9100)
    |
    +-- chatgpt_agent_start    -> JobStore.create() -> background thread -> hermes subprocess
    +-- chatgpt_agent_status   -> JobStore.get()
    +-- chatgpt_agent_result   -> JobStore.get()
    +-- chatgpt_agent_cancel   -> JobStore.update() + cancel event -> SIGTERM/SIGKILL
    +-- chatgpt_bridge_status  -> JobStore.list() + systemd status
    +-- chatgpt_bridge_install_services -> systemctl install
    +-- chatgpt_bridge_start_services   -> systemctl start
    +-- chatgpt_bridge_stop_services    -> systemctl stop
    |
    v
SQLite JobStore (~/.hermes/chatgpt_mcp_bridge/jobs.sqlite)
    |
    v
TelegramMirror -> send_message_tool -> Telegram bot
    |
    v
hermes chat subprocess (real Hermes Agent invocation)
```

## File structure

```
chatgpt_mcp_bridge/
    plugin.yaml           # Plugin manifest
    __init__.py           # register() + standalone server entry point
    __main__.py           # CLI dispatch (python -m chatgpt_mcp_bridge cli ...)
    cli.py                # CLI wrapper (status, install, start, stop, etc.)
    tools.py              # Tool implementations (8 tools, real Hermes invocation)
    jobs.py               # SQLite JobStore
    telegram_mirror.py    # Telegram notification mirror
    services.py           # systemd service management with validation
    schemas.py            # Pydantic schemas
    FIXES.md              # Changelog of fixes
    README.md             # This file
```

## Configuration

No config.yaml changes needed. The plugin uses default paths:
- JobStore: `~/.hermes/chatgpt_mcp_bridge/jobs.sqlite`
- Server port: 9100
- Telegram: reads BOT_TOKEN from `~/.hermes/.env`
- Systemd units: `~/.config/systemd/user/`

## Important

- Does NOT modify any core Hermes files (mcp_serve.py, etc.)
- Plugin survives `hermes update` (user plugins are separate from core)
- MCP tools appear in `hermes mcp serve` after enabling the plugin
- CLI commands (`chatgpt_mcp_bridge status`, etc.) are independent bash tools
- JobStore is independent of SessionDB
- Telegram mirror uses Hermes' own bot, not project-specific configs
- Systemd services use user-level management (no sudo)
- Quick Tunnel URL visible via: `chatgpt_mcp_bridge tunnel-url`
- Standalone server locally accessible: `curl http://127.0.0.1:9100/mcp`
- Agent jobs run via `hermes chat` subprocess with cancellation support
- All service management functions return Python dicts (no double-encoded JSON)
