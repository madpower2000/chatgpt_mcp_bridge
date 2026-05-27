"""
Pydantic schemas for chatgpt_mcp_bridge tools.

These schemas are used for documentation and validation.
The actual tool implementations are in tools.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


# ── Agent lifecycle ───────────────────────────────────────────────────────

class AgentStartInput(BaseModel):
    """Input for chatgpt_agent_start."""
    prompt: str = Field(description="The user prompt to send to Hermes Agent")
    model: str = Field(default="", description="Model to use (empty = default)")
    max_iterations: int = Field(default=50, description="Max tool-use iterations")
    tools: str = Field(default="[]", description="JSON array of tool names, e.g. '[\"web\",\"terminal\"]'")
    context: str = Field(default="", description="Additional context for the agent")
    rules: str = Field(default="", description="Additional rules/instructions")
    system_prompt: str = Field(default="", description="Override system prompt")
    mirror_to_telegram: bool = Field(default=False, description="Mirror messages to Telegram")
    telegram_target: str = Field(default="", description="Telegram target, e.g. 'telegram:528368879'")


class AgentStatusInput(BaseModel):
    """Input for chatgpt_agent_status."""
    job_id: str = Field(description="Job ID from chatgpt_agent_start")


class AgentResultInput(BaseModel):
    """Input for chatgpt_agent_result."""
    job_id: str = Field(description="Job ID from chatgpt_agent_start")


class AgentCancelInput(BaseModel):
    """Input for chatgpt_agent_cancel."""
    job_id: str = Field(description="Job ID to cancel")


# ── Bridge management ─────────────────────────────────────────────────────

class BridgeStatusInput(BaseModel):
    """Input for chatgpt_bridge_status."""
    job_id: str = Field(default="", description="Optional job ID. Empty = general status")


class BridgeInstallServicesInput(BaseModel):
    """Input for chatgpt_bridge_install_services."""
    mode: str = Field(default="user", description="Service mode (only 'user' supported)")
    tunnel_mode: str = Field(default="quick", description="Tunnel mode: 'quick' or 'named'")
    host: str = Field(default="127.0.0.1", description="Bind address")
    port: int = Field(default=9100, description="Port for MCP server")
    named_tunnel: str = Field(default="", description="Tunnel name (required for named mode)")
    working_dir: str = Field(default="", description="Working directory")
    python_path: str = Field(default="", description="Path to python3")
    cloudflared_path: str = Field(default="", description="Path to cloudflared")
    dry_run: bool = Field(default=False, description="Only render unit files without writing")
    enable: bool = Field(default=True, description="Enable via systemctl --user enable")


class BridgeStartServicesInput(BaseModel):
    """Input for chatgpt_bridge_start_services."""
    pass


class BridgeStopServicesInput(BaseModel):
    """Input for chatgpt_bridge_stop_services."""
    pass


# ── Job record ────────────────────────────────────────────────────────────

class JobRecord(BaseModel):
    """Persistent job record stored in SQLite."""
    job_id: str
    prompt: str
    model: str = ""
    max_iterations: int = 50
    tools: str = "[]"
    context: str = ""
    rules: str = ""
    system_prompt: str = ""
    status: str = "queued"  # queued | running | done | error | cancelled
    response: str = ""
    error: str = ""
    iterations: int = 0
    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    heartbeat_at: str = ""
    telegram_target: str = ""


# ── Service management results ────────────────────────────────────────────

class InstallServicesResult(BaseModel):
    """Return type for install_services (Python dict, not JSON string)."""
    ok: bool = Field(description="Whether the operation succeeded")
    ready: bool = Field(description="Whether all prerequisites are met")
    dry_run: bool = Field(description="Whether files were actually written")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings")
    errors: list[str] = Field(default_factory=list, description="Fatal errors")
    paths: dict = Field(default_factory=dict, description="Installed file paths")
    daemon_reload: bool = Field(description="Whether systemctl daemon-reload succeeded")
    enable_results: list[dict] = Field(default_factory=list, description="Per-service enable results")
    bridge_unit: Optional[str] = Field(default=None, description="Generated bridge unit file content")
    cloudflared_unit: Optional[str] = Field(default=None, description="Generated cloudflared unit file content")


class ServiceStatus(BaseModel):
    """Return type for service status checks."""
    name: str
    is_active: bool
    pid: Optional[int] = None
    memory: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
