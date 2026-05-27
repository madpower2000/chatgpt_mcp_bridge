# chatgpt_mcp_bridge

Async MCP interface for ChatGPT Web to Hermes Agent.

## Overview

This plugin provides a standalone FastMCP server that exposes 9 tools for
dispatching, monitoring, and managing Hermes Agent jobs from ChatGPT Web
(or any MCP-compatible client), plus systemd service management for the
standalone MCP server and Cloudflare tunnel.

## Installation

```bash
# Plugin is already in ~/.hermes/plugins/chatgpt_mcp_bridge/
# Just enable it:

hermes plugins enable chatgpt_mcp_bridge
```

## Running the bridge server

### Standalone MCP server

```bash
# Option 1: Run as standalone server (default port 9100)
python -m chatgpt_mcp_bridge

# Option 2: Run with custom port
python -m chatgpt_mcp_bridge --port 9101 --host 0.0.0.0
```

### Via systemd (recommended for production)

```bash
# 1. Install services (dry run first)
chatgpt_bridge_install_services(dry_run=true, tunnel_mode="quick", port=9100)

# 2. Install and enable
chatgpt_bridge_install_services(dry_run=false, tunnel_mode="quick", port=9100, enable=true)

# 3. Start services
chatgpt_bridge_start_services()

# 4. Check status
chatgpt_bridge_status()

# 5. Stop services
chatgpt_bridge_stop_services()
```

### Manual systemd commands

```bash
# View Quick Tunnel URL
journalctl --user -u chatgpt-mcp-cloudflared.service -f

# View bridge logs
journalctl --user -u chatgpt-mcp-bridge.service -f

# Start/stop manually
systemctl --user start chatgpt-mcp-bridge.service
systemctl --user start chatgpt-mcp-cloudflared.service
systemctl --user stop chatgpt-mcp-cloudflared.service
systemctl --user stop chatgpt-mcp-bridge.service
```

### Named tunnel

```bash
chatgpt_bridge_install_services(
  dry_run=false,
  tunnel_mode="named",
  named_tunnel="hermes-mcp",
  port=9100,
  enable=true
)
```

### Auto-start after reboot (no login needed)

```bash
loginctl enable-linger $USER
```

## Tools

### Agent lifecycle tools

#### chatgpt_agent_start

Start a new agent job.

**Parameters:**
- `prompt` (required) — User prompt to send to Hermes Agent
- `model` — Model to use (empty = default)
- `max_iterations` — Max tool-use iterations (default 50)
- `tools` — JSON array of tool names to enable (e.g. `["web","terminal"]`)
- `context` — Additional context for the agent
- `rules` — Additional rules/instructions
- `system_prompt` — Override system prompt
- `mirror_to_telegram` — Mirror messages to Telegram (default false)
- `telegram_target` — Telegram target (e.g. `telegram:528368879`)

**Returns:** JSON with `job_id`, `status`, timestamps.

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

**Parameters:**
- `job_id` — Job ID to cancel

**Returns:** JSON with cancellation result.

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

**Returns:** JSON with `ok`, `dry_run`, `bridge_unit`, `cloudflared_unit`, `paths`, `warnings`.

**Examples:**

```python
# Dry run — see what would be generated
chatgpt_bridge_install_services(dry_run=true, tunnel_mode="quick", port=9100)

# Install and enable
chatgpt_bridge_install_services(dry_run=false, tunnel_mode="quick", port=9100, enable=true)

# Named tunnel
chatgpt_bridge_install_services(
  dry_run=false,
  tunnel_mode="named",
  named_tunnel="hermes-mcp",
  port=9100,
  enable=true
)
```

#### chatgpt_bridge_start_services

Start both bridge and tunnel systemd user services.

**Parameters:** none

**Returns:** JSON with start results for each service + hint to check tunnel URL via journalctl.

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
    +-- chatgpt_agent_start    -> JobStore.create() -> background thread
    +-- chatgpt_agent_status   -> JobStore.get()
    +-- chatgpt_agent_result   -> JobStore.get()
    +-- chatgpt_agent_cancel   -> JobStore.update() + cancel event
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

Systemd services (optional, production mode):
    chatgpt-mcp-bridge.service  -> python -m chatgpt_mcp_bridge
    chatgpt-mcp-cloudflared.service -> cloudflared tunnel -> public URL
```

## File structure

```
chatgpt_mcp_bridge/
    plugin.yaml           # Plugin manifest
    __init__.py           # register() + standalone server entry point
    tools.py              # Tool implementations (9 tools)
    jobs.py               # SQLite JobStore
    telegram_mirror.py    # Telegram notification mirror
    services.py           # systemd service management (NEW)
    schemas.py            # Pydantic schemas
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
- Tools appear in `hermes mcp serve` after enabling the plugin
- JobStore is independent of SessionDB
- Telegram mirror uses Hermes' own bot, not project-specific configs
- Systemd services use user-level management (no sudo)
- Quick Tunnel URL visible via: `journalctl --user -u chatgpt-mcp-cloudflared.service -f`
- Standalone server locally accessible: `curl -i http://127.0.0.1:9100/mcp`
