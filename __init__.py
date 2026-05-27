"""
chatgpt_mcp_bridge — Async MCP interface for ChatGPT Web → Hermes Agent.

This plugin provides a standalone FastMCP server that exposes 4 public MCP
tools for dispatching, monitoring, and cancelling Hermes Agent jobs from
ChatGPT Web (or any MCP-compatible client).

Service management (bridge status, systemd install/start/stop) is available
via local CLI only — it is intentionally NOT exposed over MCP for safety.

Usage:
    hermes plugins enable chatgpt_mcp_bridge
    # Then start the bridge MCP server:
    python -m chatgpt_mcp_bridge
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chatgpt_mcp_bridge")

# ---------------------------------------------------------------------------
# Tool schemas — match the Pydantic schemas for consistency
# ---------------------------------------------------------------------------

CHATGPT_AGENT_START_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "The user prompt to send to the Hermes Agent.",
        },
        "model": {
            "type": "string",
            "description": "Model to use (e.g. 'claude-sonnet-4', 'openai-gpt-4o'). Empty = default model.",
        },
        "max_iterations": {
            "type": "integer",
            "description": "Maximum number of tool-use iterations (0 = unlimited, capped by system).",
            "default": 50,
        },
        "tools": {
            "type": "string",
            "description": "JSON array of tool names to enable (e.g. '[\"web\", \"terminal\", \"file\"]'). Empty = all.",
        },
        "context": {
            "type": "string",
            "description": "Additional context to inject into the agent run.",
        },
        "rules": {
            "type": "string",
            "description": "Additional rules/instructions for the agent run.",
        },
        "system_prompt": {
            "type": "string",
            "description": "Override the system prompt for this run.",
        },
        "mirror_to_telegram": {
            "type": "boolean",
            "description": "If true, mirror start/end messages to Telegram.",
        },
        "telegram_target": {
            "type": "string",
            "description": "Telegram target (e.g. 'telegram:528368879'). Ignored if mirror_to_telegram is false.",
        },
    },
    "required": ["prompt"],
}

CHATGPT_AGENT_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "The job ID returned by chatgpt_agent_start.",
        },
    },
    "required": ["job_id"],
}

CHATGPT_AGENT_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "The job ID returned by chatgpt_agent_start.",
        },
    },
    "required": ["job_id"],
}

CHATGPT_AGENT_CANCEL_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "The job ID to cancel.",
        },
    },
    "required": ["job_id"],
}

CHATGPT_BRIDGE_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "Optional job ID to get status for. Empty = general bridge status.",
        },
    },
}

CHATGPT_BRIDGE_INSTALL_SERVICES_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "description": "Service mode. Only 'user' is supported.",
            "default": "user",
        },
        "tunnel_mode": {
            "type": "string",
            "description": "Tunnel mode: 'quick' for anonymous tunnel, 'named' for persistent named tunnel.",
            "default": "quick",
        },
        "host": {
            "type": "string",
            "description": "Bind address for the MCP server.",
            "default": "127.0.0.1",
        },
        "port": {
            "type": "integer",
            "description": "Port for the MCP server.",
            "default": 9100,
        },
        "named_tunnel": {
            "type": "string",
            "description": "Tunnel name (required if tunnel_mode='named').",
            "default": "",
        },
        "working_dir": {
            "type": "string",
            "description": "Working directory for services. Default: home directory.",
            "default": "",
        },
        "python_path": {
            "type": "string",
            "description": "Path to python3. Default: auto-detect.",
            "default": "",
        },
        "cloudflared_path": {
            "type": "string",
            "description": "Path to cloudflared. Default: auto-detect.",
            "default": "",
        },
        "dry_run": {
            "type": "boolean",
            "description": "If true, only render unit files without writing or enabling.",
            "default": False,
        },
        "enable": {
            "type": "boolean",
            "description": "If true, enable both services via systemctl --user enable.",
            "default": True,
        },
    },
}

CHATGPT_BRIDGE_START_SERVICES_SCHEMA = {
    "type": "object",
    "properties": {},
}

CHATGPT_BRIDGE_STOP_SERVICES_SCHEMA = {
    "type": "object",
    "properties": {},
}

# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:  # noqa: D401
    """Register the chatgpt_mcp_bridge plugin.

    Registers 4 public MCP tools (agent lifecycle) with the plugin context.
    Service/admin functions (bridge_status, install_services, start_services,
    stop_services) remain available via local CLI but are NOT exposed over MCP.
    """
    # Import tools module with alias to avoid conflict with 'tools' parameter name
    from . import tools as bridge_tools

    # Initialize shared state
    from .jobs import JobStore
    from .telegram_mirror import TelegramMirror

    bridge_tools._store = JobStore()
    bridge_tools._mirror = TelegramMirror(bridge_tools._store, default_target="telegram:528368879")

    # Register 4 public MCP tools (agent lifecycle only)
    # Service/admin tools are intentionally NOT exposed over MCP for safety.
    # They remain callable via local CLI: python -m chatgpt_mcp_bridge cli <cmd>
    ctx.register_tool(
        name="chatgpt_agent_start",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_START_SCHEMA,
        handler=bridge_tools.chatgpt_agent_start,
        description="Start a new ChatGPT → Hermes Agent job. Creates a job and returns job_id immediately.",
    )
    ctx.register_tool(
        name="chatgpt_agent_status",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_STATUS_SCHEMA,
        handler=bridge_tools.chatgpt_agent_status,
        description="Get the status of a job (queued/running/done/error/cancelled).",
    )
    ctx.register_tool(
        name="chatgpt_agent_result",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_RESULT_SCHEMA,
        handler=bridge_tools.chatgpt_agent_result,
        description="Get the result of a completed job (response/error).",
    )
    ctx.register_tool(
        name="chatgpt_agent_cancel",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_CANCEL_SCHEMA,
        handler=bridge_tools.chatgpt_agent_cancel,
        description="Cancel a running job.",
    )

    logger.info(
        "chatgpt_mcp_bridge registered: 4 public MCP tools "
        "(chatgpt_agent_start, chatgpt_agent_status, "
        "chatgpt_agent_result, chatgpt_agent_cancel). "
        "Service/admin functions available via CLI."
    )


# ===========================================================================
# Standalone MCP server
# ===========================================================================

def run_server(host: str = "127.0.0.1", port: int = 9100) -> None:
    """Run the chatgpt_mcp_bridge as a standalone MCP server.

    Usage:
        python -m chatgpt_mcp_bridge
        # or
        python -m chatgpt_mcp_bridge --port 9101 --host 0.0.0.0

    Args:
        host: Bind address (default: 127.0.0.1).
        port: Bind port (default: 9100).
    """
    import argparse

    parser = argparse.ArgumentParser(description="ChatGPT MCP Bridge Server")
    parser.add_argument("--host", default=host, help="Bind address")
    parser.add_argument("--port", type=int, default=port, help="Bind port")
    args = parser.parse_args()

    # Initialize
    from .jobs import JobStore
    from .telegram_mirror import TelegramMirror
    from . import tools as bridge_tools

    bridge_tools._store = JobStore()
    bridge_tools._mirror = TelegramMirror(bridge_tools._store, default_target="telegram:528368879")

    # Import MCP SDK
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print(
            "Error: MCP server requires the 'mcp' package.\n"
            "Install with: pip install 'mcp'",
            file=sys.stderr,
        )
        sys.exit(1)

    # Create the MCP server — 4 public tools only (agent lifecycle)
    # Service/admin tools are intentionally NOT exposed over MCP for safety.
    mcp = FastMCP(
        "chatgpt-mcp-bridge",
        instructions=(
            "ChatGPT Web → Hermes Agent bridge. "
            "Use chatgpt_agent_start to dispatch agent jobs, "
            "chatgpt_agent_status to poll status, "
            "chatgpt_agent_result to get results, "
            "chatgpt_agent_cancel to stop a job. "
            "Service management is available via local CLI only."
        ),
    )

    # Register tools
    @mcp.tool()
    def chatgpt_agent_start(
        prompt: str,
        model: str = "",
        max_iterations: int = 50,
        tools: str = "[]",
        context: str = "",
        rules: str = "",
        system_prompt: str = "",
        mirror_to_telegram: bool = False,
        telegram_target: str = "",
    ) -> str:
        """Start a new ChatGPT → Hermes Agent job.

        Creates a job and returns job_id immediately.
        The agent runtime runs asynchronously in background.

        Args:
            prompt: The user prompt to send to Hermes Agent.
            model: Model to use (empty = default).
            max_iterations: Max tool-use iterations (default 50).
            tools: JSON array of tool names to enable (e.g. '["web","terminal"]').
            context: Additional context for the agent.
            rules: Additional rules/instructions.
            system_prompt: Override system prompt.
            mirror_to_telegram: Mirror to Telegram (default false).
            telegram_target: Telegram target (e.g. 'telegram:528368879').

        Returns:
            JSON with job_id and status.
        """
        return bridge_tools.chatgpt_agent_start(
            prompt=prompt,
            model=model,
            max_iterations=max_iterations,
            tools=tools,
            context=context,
            rules=rules,
            system_prompt=system_prompt,
            mirror_to_telegram=mirror_to_telegram,
            telegram_target=telegram_target,
        )

    @mcp.tool()
    def chatgpt_agent_status(job_id: str) -> str:
        """Get status of a job.

        Args:
            job_id: The job ID from chatgpt_agent_start.

        Returns:
            JSON with status, timestamps, and job metadata.
        """
        return bridge_tools.chatgpt_agent_status(job_id=job_id)

    @mcp.tool()
    def chatgpt_agent_result(job_id: str) -> str:
        """Get the result of a completed job.

        Args:
            job_id: The job ID from chatgpt_agent_start.

        Returns:
            JSON with response, error, and job metadata.
        """
        return bridge_tools.chatgpt_agent_result(job_id=job_id)

    @mcp.tool()
    def chatgpt_agent_cancel(job_id: str) -> str:
        """Cancel a running job.

        Args:
            job_id: The job ID to cancel.

        Returns:
            JSON with cancellation result.
        """
        return bridge_tools.chatgpt_agent_cancel(job_id=job_id)

    # NOTE: Service/admin tools (chatgpt_bridge_status, install_services,
    # start_services, stop_services) are intentionally NOT registered as MCP
    # tools. They operate on local systemd/cloudflared state and are admin
    # operations. Use the local CLI instead:
    #   python -m chatgpt_mcp_bridge cli status
    #   python -m chatgpt_mcp_bridge cli install
    #   python -m chatgpt_mcp_bridge cli start
    #   python -m chatgpt_mcp_bridge cli stop

    # Start the server (use StreamableHTTP transport for TCP port listening)
    logger.info("Starting chatgpt_mcp_bridge server on %s:%d", args.host, args.port)
    print(f"ChatGPT MCP Bridge server starting on {args.host}:{args.port}")
    print("Press Ctrl+C to stop.")

    try:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        import asyncio
        asyncio.run(mcp.run_streamable_http_async())
    except KeyboardInterrupt:
        print("\nServer stopped.")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    from .__main__ import main
    main()
    run_server()
