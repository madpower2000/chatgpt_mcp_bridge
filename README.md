# chatgpt_mcp_bridge

Async MCP interface for ChatGPT Web → Hermes Agent.

## Overview

This plugin provides a standalone FastMCP server that exposes **4 public MCP tools**
for dispatching, monitoring, and cancelling Hermes Agent jobs from ChatGPT Web
(or any MCP-compatible client).

**Service management** (bridge status, systemd install/start/stop) is available
via **local CLI only** — it is intentionally NOT exposed over MCP for safety,
since the MCP endpoint is reachable through Cloudflare and these tools operate
on local systemd/cloudflared state.

## Installation

```bash
# Plugin is already in ~/.hermes/plugins/chatgpt_mcp_bridge/
# Just enable it:

hermes plugins enable chatgpt_mcp_bridge
```

## Public MCP Tools (4)

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

## Local CLI / Admin Commands

Service management is available via the local CLI. These commands are NOT
exposed over MCP — they must be run from the terminal on the host machine.

```bash
# Check status (services, PID, memory, MCP endpoint)
python -m chatgpt_mcp_bridge cli status

# Install systemd services (dry run first)
python -m chatgpt_mcp_bridge cli install --dry-run

# Install and enable systemd services
python -m chatgpt_mcp_bridge cli install

# Install with named tunnel
python -m chatgpt_mcp_bridge cli install --tunnel-mode named --named-tunnel hermes-mcp

# Start bridge + tunnel
python -m chatgpt_mcp_bridge cli start

# Stop tunnel then bridge
python -m chatgpt_mcp_bridge cli stop

# Uninstall (stop, disable, remove unit files)
python -m chatgpt_mcp_bridge cli uninstall

# Show Cloudflare tunnel URL
python -m chatgpt_mcp_bridge cli tunnel-url
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
python -m chatgpt_mcp_bridge cli install --dry-run

# 2. Install and enable
python -m chatgpt_mcp_bridge cli install

# 3. Start services
python -m chatgpt_mcp_bridge cli start

# 4. Check status
python -m chatgpt_mcp_bridge cli status

# 5. Stop services
python -m chatgpt_mcp_bridge cli stop
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

### Service management (local CLI only, NOT over MCP)

```
Local Terminal
    |
    +-- python -m chatgpt_mcp_bridge cli status     -> services.check_service()
    +-- python -m chatgpt_mcp_bridge cli install     -> services.install_services()
    +-- python -m chatgpt_mcp_bridge cli start       -> services.start_services()
    +-- python -m chatgpt_mcp_bridge cli stop        -> services.stop_services()
    +-- python -m chatgpt_mcp_bridge cli uninstall   -> stop + disable + remove units
    +-- python -m chatgpt_mcp_bridge cli tunnel-url  -> journalctl grep
```

## File structure

```
chatgpt_mcp_bridge/
    plugin.yaml           # Plugin manifest
    __init__.py           # register() + standalone server entry point
    __main__.py           # CLI dispatch (python -m chatgpt_mcp_bridge cli ...)
    cli.py                # CLI wrapper (status, install, start, stop, etc.)
    tools.py              # Tool implementations (8 functions, 4 exposed via MCP)
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
- CLI commands are independent bash tools
- JobStore is independent of SessionDB
- Telegram mirror uses Hermes' own bot, not project-specific configs
- Systemd services use user-level management (no sudo)
- Quick Tunnel URL visible via: `python -m chatgpt_mcp_bridge cli tunnel-url`
- Standalone server locally accessible: `curl http://127.0.0.1:9100/mcp`
- Agent jobs run via `hermes chat` subprocess with cancellation support
- All service management functions return Python dicts (no double-encoded JSON)
- **Service tools are NOT exposed over MCP** — use local CLI for admin operations
