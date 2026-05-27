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
