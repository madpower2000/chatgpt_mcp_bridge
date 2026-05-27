"""
chatgpt_mcp_bridge — Async MCP interface for ChatGPT Web → Hermes Agent.

This plugin provides a standalone FastMCP server that exposes 9 tools for
dispatching, monitoring, and managing Hermes Agent jobs from ChatGPT Web
(or any MCP-compatible client), plus systemd service management for the
standalone MCP server and Cloudflare tunnel.

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

    Registers 5 MCP tools with the plugin context so they appear alongside
    built-in tools in the agent's tool registry.
    """
    from . import tools

    # Initialize shared state
    from .jobs import JobStore
    from .telegram_mirror import TelegramMirror

    tools._store = JobStore()
    tools._mirror = TelegramMirror(tools._store)

    # Register each tool with the plugin context
    ctx.register_tool(
        name="chatgpt_agent_start",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_START_SCHEMA,
        handler=tools.chatgpt_agent_start,
        description="Start a new ChatGPT → Hermes Agent job. Creates a job and returns job_id immediately.",
    )
    ctx.register_tool(
        name="chatgpt_agent_status",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_STATUS_SCHEMA,
        handler=tools.chatgpt_agent_status,
        description="Get the status of a job (queued/running/done/error/cancelled).",
    )
    ctx.register_tool(
        name="chatgpt_agent_result",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_RESULT_SCHEMA,
        handler=tools.chatgpt_agent_result,
        description="Get the result of a completed job (response/error).",
    )
    ctx.register_tool(
        name="chatgpt_agent_cancel",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_AGENT_CANCEL_SCHEMA,
        handler=tools.chatgpt_agent_cancel,
        description="Cancel a running job.",
    )
    ctx.register_tool(
        name="chatgpt_bridge_status",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_BRIDGE_STATUS_SCHEMA,
        handler=tools.chatgpt_bridge_status,
        description="Get bridge status — general stats, job details, or systemd service status.",
    )
    ctx.register_tool(
        name="chatgpt_bridge_install_services",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_BRIDGE_INSTALL_SERVICES_SCHEMA,
        handler=tools.chatgpt_bridge_install_services,
        description="Install systemd user services for the bridge and Cloudflare tunnel.",
    )
    ctx.register_tool(
        name="chatgpt_bridge_start_services",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_BRIDGE_START_SERVICES_SCHEMA,
        handler=tools.chatgpt_bridge_start_services,
        description="Start both bridge and tunnel systemd user services.",
    )
    ctx.register_tool(
        name="chatgpt_bridge_stop_services",
        toolset="chatgpt_mcp_bridge",
        schema=CHATGPT_BRIDGE_STOP_SERVICES_SCHEMA,
        handler=tools.chatgpt_bridge_stop_services,
        description="Stop tunnel first, then bridge systemd user services.",
    )

    logger.info(
        "chatgpt_mcp_bridge registered: 9 tools loaded "
        "(chatgpt_agent_start, chatgpt_agent_status, chatgpt_agent_result, "
        "chatgpt_agent_cancel, chatgpt_bridge_status, "
        "chatgpt_bridge_install_services, chatgpt_bridge_start_services, "
        "chatgpt_bridge_stop_services)"
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
    from . import tools

    tools._store = JobStore()
    tools._mirror = TelegramMirror(tools._store)

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

    # Create the MCP server
    mcp = FastMCP(
        "chatgpt-mcp-bridge",
        instructions=(
            "ChatGPT Web → Hermes Agent bridge. "
            "Use chatgpt_agent_start to dispatch agent jobs, "
            "chatgpt_agent_status to poll status, "
            "chatgpt_agent_result to get results, "
            "chatgpt_agent_cancel to stop a job, "
            "chatgpt_bridge_status for bridge health and service status, "
            "chatgpt_bridge_install_services to set up systemd units, "
            "chatgpt_bridge_start_services to start them, "
            "chatgpt_bridge_stop_services to stop them."
        ),
    )

    # Register tools
    @mcp.tool()
    def chatgpt_agent_start(
        prompt: str,
        model: str = "",
        max_iterations: int = 50,
        tools_arg: str = "[]",
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
            tools_arg: JSON array of tool names to enable.
            context: Additional context for the agent.
            rules: Additional rules/instructions.
            system_prompt: Override system prompt.
            mirror_to_telegram: Mirror to Telegram (default false).
            telegram_target: Telegram target (e.g. 'telegram:528368879').

        Returns:
            JSON with job_id and status.
        """
        return tools.chatgpt_agent_start(
            prompt=prompt,
            model=model,
            max_iterations=max_iterations,
            tools=tools_arg,
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
        return tools.chatgpt_agent_status(job_id=job_id)

    @mcp.tool()
    def chatgpt_agent_result(job_id: str) -> str:
        """Get the result of a completed job.

        Args:
            job_id: The job ID from chatgpt_agent_start.

        Returns:
            JSON with response, error, and job metadata.
        """
        return tools.chatgpt_agent_result(job_id=job_id)

    @mcp.tool()
    def chatgpt_agent_cancel(job_id: str) -> str:
        """Cancel a running job.

        Args:
            job_id: The job ID to cancel.

        Returns:
            JSON with cancellation result.
        """
        return tools.chatgpt_agent_cancel(job_id=job_id)

    @mcp.tool()
    def chatgpt_bridge_status(job_id: str = "") -> str:
        """Get bridge status — general stats, job details, or systemd service status.

        Args:
            job_id: Optional job ID. Empty = general status.

        Returns:
            JSON with bridge stats, service statuses, and helpful commands.
        """
        return tools.chatgpt_bridge_status(job_id=job_id)

    @mcp.tool()
    def chatgpt_bridge_install_services(
        mode: str = "user",
        tunnel_mode: str = "quick",
        host: str = "127.0.0.1",
        port: int = 9100,
        named_tunnel: str = "",
        working_dir: str = "",
        python_path: str = "",
        cloudflared_path: str = "",
        dry_run: bool = False,
        enable: bool = True,
    ) -> str:
        """Install systemd user services for the bridge and Cloudflare tunnel.

        Args:
            mode: Service mode (only 'user').
            tunnel_mode: 'quick' or 'named'.
            host: Bind address.
            port: Port.
            named_tunnel: Tunnel name (required for named mode).
            working_dir: Working directory.
            python_path: Python path.
            cloudflared_path: Cloudflared path.
            dry_run: Only render unit files.
            enable: Enable via systemctl.

        Returns:
            JSON with ok, unit contents, paths.
        """
        return tools.chatgpt_bridge_install_services(
            mode=mode,
            tunnel_mode=tunnel_mode,
            host=host,
            port=port,
            named_tunnel=named_tunnel,
            working_dir=working_dir,
            python_path=python_path,
            cloudflared_path=cloudflared_path,
            dry_run=dry_run,
            enable=enable,
        )

    @mcp.tool()
    def chatgpt_bridge_start_services() -> str:
        """Start both bridge and tunnel systemd user services.

        Returns:
            JSON with start results and journalctl hint.
        """
        return tools.chatgpt_bridge_start_services()

    @mcp.tool()
    def chatgpt_bridge_stop_services() -> str:
        """Stop tunnel first, then bridge systemd user services.

        Returns:
            JSON with stop results.
        """
        return tools.chatgpt_bridge_stop_services()

    # Start the server
    logger.info("Starting chatgpt_mcp_bridge server on %s:%d", args.host, args.port)
    print(f"ChatGPT MCP Bridge server starting on {args.host}:{args.port}")
    print("Press Ctrl+C to stop.")

    try:
        mcp.run_stdio_async()
    except KeyboardInterrupt:
        print("\nServer stopped.")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    run_server()
