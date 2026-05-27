# chatgpt_mcp_bridge

Async MCP interface for ChatGPT Web to Hermes Agent.

## Overview

This plugin provides a standalone FastMCP server that exposes 5 tools for
dispatching, monitoring, and managing Hermes Agent jobs from ChatGPT Web
(or any MCP-compatible client).

## Installation

```bash
# Plugin is already in ~/.hermes/plugins/chatgpt_mcp_bridge/
# Just enable it:

hermes plugins enable chatgpt_mcp_bridge
```

## Running the bridge server

The plugin registers tools in the Hermes agent tool registry. Additionally,
it provides a standalone MCP server:

```bash
# Option 1: Run as standalone server (default port 9100)
python -m chatgpt_mcp_bridge

# Option 2: Run with custom port
python -m chatgpt_mcp_bridge --port 9101 --host 0.0.0.0
```

## Tools

### chatgpt_agent_start

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

### chatgpt_agent_status

Get job status.

**Parameters:**
- `job_id` — Job ID from chatgpt_agent_start

**Returns:** JSON with status (`queued`|`running`|`done`|`error`|`cancelled`),
timestamps, iterations.

### chatgpt_agent_result

Get job result.

**Parameters:**
- `job_id` — Job ID from chatgpt_agent_start

**Returns:** JSON with `response`, `error`, and job metadata.

### chatgpt_agent_cancel

Cancel a running job.

**Parameters:**
- `job_id` — Job ID to cancel

**Returns:** JSON with cancellation result.

### chatgpt_bridge_status

Get bridge health or specific job details.

**Parameters:**
- `job_id` — Optional. Empty = general bridge status.

**Returns:** JSON with total jobs, status counts, recent jobs.

## Architecture

```
ChatGPT Web (MCP client)
    |
    v
Standalone FastMCP Server (port 9100)
    |
    +-- chatgpt_agent_start  -> JobStore.create() -> background thread
    +-- chatgpt_agent_status -> JobStore.get()
    +-- chatgpt_agent_result -> JobStore.get()
    +-- chatgpt_agent_cancel -> JobStore.update() + cancel event
    +-- chatgpt_bridge_status -> JobStore.list()
    |
    v
SQLite JobStore (~/.hermes/chatgpt_mcp_bridge/jobs.sqlite)
    |
    v
TelegramMirror -> send_message_tool -> Telegram bot
```

## File structure

```
chatgpt_mcp_bridge/
    plugin.yaml           # Plugin manifest
    __init__.py           # register() + standalone server entry point
    tools.py              # Tool implementations
    jobs.py               # SQLite JobStore
    telegram_mirror.py    # Telegram notification mirror
    schemas.py            # Pydantic schemas
    README.md             # This file
```

## Configuration

No config.yaml changes needed. The plugin uses default paths:
- JobStore: `~/.hermes/chatgpt_mcp_bridge/jobs.sqlite`
- Server port: 9100
- Telegram: reads BOT_TOKEN from `~/.hermes/.env`

## Important

- Does NOT modify any core Hermes files (mcp_serve.py, etc.)
- Tools appear in `hermes mcp serve` after enabling the plugin
- JobStore is independent of SessionDB
- Telegram mirror uses Hermes' own bot, not project-specific configs
