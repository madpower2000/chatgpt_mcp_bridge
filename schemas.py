"""
Pydantic schemas for chatgpt_mcp_bridge MCP tools.

Used by FastMCP's tool decorator for parameter validation and OpenAPI schema
generation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class AgentStartInput(BaseModel):
    """Input for chatgpt_agent_start tool."""
    prompt: str = Field(
        description="The user prompt to send to the Hermes Agent.",
        min_length=1,
    )
    model: str = Field(
        default="",
        description="Model to use (e.g. 'claude-sonnet-4', 'openai-gpt-4o'). Empty = default model.",
    )
    max_iterations: int = Field(
        default=50,
        description="Maximum number of tool-use iterations (0 = unlimited, capped by system).",
        ge=0,
    )
    tools: str = Field(
        default="[]",
        description="JSON array of tool names to enable (e.g. '[\"web\", \"terminal\", \"file\"]'). Empty = all.",
    )
    context: str = Field(
        default="",
        description="Additional context to inject into the agent run.",
    )
    rules: str = Field(
        default="",
        description="Additional rules/instructions for the agent run.",
    )
    system_prompt: str = Field(
        default="",
        description="Override the system prompt for this run.",
    )
    mirror_to_telegram: bool = Field(
        default=False,
        description="If true, mirror start/end messages to Telegram.",
    )
    telegram_target: str = Field(
        default="",
        description="Telegram target (e.g. 'telegram:528368879'). Ignored if mirror_to_telegram is false.",
    )


class AgentStatusInput(BaseModel):
    """Input for chatgpt_agent_status tool."""
    job_id: str = Field(
        description="The job ID returned by chatgpt_agent_start.",
        min_length=1,
    )


class AgentResultInput(BaseModel):
    """Input for chatgpt_agent_result tool."""
    job_id: str = Field(
        description="The job ID returned by chatgpt_agent_start.",
        min_length=1,
    )


class AgentCancelInput(BaseModel):
    """Input for chatgpt_agent_cancel tool."""
    job_id: str = Field(
        description="The job ID to cancel.",
        min_length=1,
    )


class BridgeStatusInput(BaseModel):
    """Input for chatgpt_bridge_status tool."""
    job_id: str = Field(
        default="",
        description="Optional job ID to get status for. Empty = general bridge status.",
    )


# ---------------------------------------------------------------------------
# Service management schemas
# ---------------------------------------------------------------------------

class BridgeInstallServicesInput(BaseModel):
    """Input for chatgpt_bridge_install_services tool."""
    mode: str = Field(
        default="user",
        description="Service mode. Only 'user' is supported.",
    )
    tunnel_mode: str = Field(
        default="quick",
        description="Tunnel mode: 'quick' for anonymous tunnel, 'named' for persistent named tunnel.",
    )
    host: str = Field(
        default="127.0.0.1",
        description="Bind address for the MCP server.",
    )
    port: int = Field(
        default=9100,
        description="Port for the MCP server.",
        ge=1,
        le=65535,
    )
    named_tunnel: Optional[str] = Field(
        default=None,
        description="Tunnel name (required if tunnel_mode='named').",
    )
    working_dir: Optional[str] = Field(
        default=None,
        description="Working directory for services. Default: home directory.",
    )
    python_path: Optional[str] = Field(
        default=None,
        description="Path to python3. Default: auto-detect via shutil.which.",
    )
    cloudflared_path: Optional[str] = Field(
        default=None,
        description="Path to cloudflared. Default: auto-detect via shutil.which.",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, only render unit files without writing or enabling.",
    )
    enable: bool = Field(
        default=True,
        description="If true, enable both services via systemctl --user enable.",
    )


class BridgeStartServicesInput(BaseModel):
    """Input for chatgpt_bridge_start_services tool."""
    pass


class BridgeStopServicesInput(BaseModel):
    """Input for chatgpt_bridge_stop_services tool."""
    pass
