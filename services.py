"""
Systemd service management for chatgpt_mcp_bridge.

Generates and manages systemd user unit files for:
  1. chatgpt-mcp-bridge.service  — standalone MCP server
  2. chatgpt-mcp-cloudflared.service  — Cloudflare tunnel to the MCP server

All operations use user-level systemd (no sudo).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chatgpt_mcp_bridge.services")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
_BRIDGE_UNIT_NAME = "chatgpt-mcp-bridge.service"
_TUNNEL_UNIT_NAME = "chatgpt-mcp-cloudflared.service"
_MAX_OUTPUT = 8000  # chars to keep from subprocess output


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ServiceConfig:
    """Configuration for systemd service generation."""
    mode: str = "user"
    tunnel_mode: str = "quick"
    host: str = "127.0.0.1"
    port: int = 9100
    named_tunnel: Optional[str] = None
    working_dir: Optional[str] = None
    python_path: Optional[str] = None
    cloudflared_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.mode != "user":
            raise ValueError(
                f"mode='{self.mode}' is not supported. Only 'user' mode is available."
            )
        if self.tunnel_mode not in ("quick", "named"):
            raise ValueError(
                f"tunnel_mode='{self.tunnel_mode}' must be 'quick' or 'named'."
            )
        if self.tunnel_mode == "named" and not self.named_tunnel:
            raise ValueError(
                "named_tunnel is required when tunnel_mode='named'."
            )
        if self.python_path is None:
            self.python_path = shutil.which("python3") or "python3"
        if self.cloudflared_path is None:
            self.cloudflared_path = shutil.which("cloudflared") or "cloudflared"
        if self.working_dir is None:
            self.working_dir = str(Path.home())


# ---------------------------------------------------------------------------
# Unit file rendering
# ---------------------------------------------------------------------------

def render_bridge_unit(cfg: ServiceConfig) -> str:
    """Render the chatgpt-mcp-bridge.service unit file content."""
    return (
        f"[Unit]\n"
        f"Description=ChatGPT MCP Bridge — standalone MCP server\n"
        f"After=network.target\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={cfg.python_path} -m chatgpt_mcp_bridge\n"
        f"WorkingDirectory={cfg.working_dir}\n"
        f"Environment=CHATGPT_MCP_BRIDGE_HOST={cfg.host}\n"
        f"Environment=CHATGPT_MCP_BRIDGE_PORT={cfg.port}\n"
        f"Environment=PYTHONUNBUFFERED=1\n"
        f"Environment=PYTHONPATH={Path.home()}/.hermes/plugins\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def render_cloudflared_unit(cfg: ServiceConfig) -> str:
    """Render the chatgpt-mcp-cloudflared.service unit file content."""
    if cfg.tunnel_mode == "quick":
        tunnel_args = f"--url http://{cfg.host}:{cfg.port}"
        description = "ChatGPT MCP Bridge — Cloudflare Quick Tunnel"
    else:
        tunnel_args = f"run {cfg.named_tunnel}"
        description = f"ChatGPT MCP Bridge — Cloudflare Named Tunnel ({cfg.named_tunnel})"

    return (
        f"[Unit]\n"
        f"Description={description}\n"
        f"After=network.target chatgpt-mcp-bridge.service\n"
        f"Requires=chatgpt-mcp-bridge.service\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={cfg.cloudflared_path} tunnel {tunnel_args}\n"
        f"WorkingDirectory={cfg.working_dir}\n"
        f"Environment=PYTHONUNBUFFERED=1\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


# ---------------------------------------------------------------------------
# Service operations
# ---------------------------------------------------------------------------

def _run_sysctl(args: List[str]) -> Dict[str, Any]:
    """Run a systemctl --user command and return structured result.

    Args:
        args: Arguments to pass after 'systemctl --user'.

    Returns:
        Dict with success, exit_code, stdout, stderr.
    """
    cmd = ["systemctl", "--user"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = result.stdout[-_MAX_OUTPUT:] if result.stdout else ""
        stderr = result.stderr[-_MAX_OUTPUT:] if result.stderr else ""
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    except FileNotFoundError:
        return {
            "success": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": "systemctl not found",
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "systemctl command timed out",
        }
    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
        }


def _is_active(service_name: str) -> bool:
    """Check if a user service is currently active."""
    result = _run_sysctl(["is-active", service_name])
    return result.get("stdout") == "active"


def install_services(
    mode: str = "user",
    tunnel_mode: str = "quick",
    host: str = "127.0.0.1",
    port: int = 9100,
    named_tunnel: Optional[str] = None,
    working_dir: Optional[str] = None,
    python_path: Optional[str] = None,
    cloudflared_path: Optional[str] = None,
    dry_run: bool = False,
    enable: bool = True,
) -> str:
    """Install (or dry-run) systemd user services for the bridge.

    Args:
        mode: Service mode — only 'user' supported.
        tunnel_mode: 'quick' or 'named'.
        host: Bind address for the MCP server.
        port: Port for the MCP server.
        named_tunnel: Tunnel name (required if tunnel_mode='named').
        working_dir: Working directory for services.
        python_path: Path to python3 binary.
        cloudflared_path: Path to cloudflared binary.
        dry_run: If true, only render and return unit contents.
        enable: If true, systemctl --user enable both services.

    Returns:
        JSON result string.
    """
    cfg = ServiceConfig(
        mode=mode,
        tunnel_mode=tunnel_mode,
        host=host,
        port=port,
        named_tunnel=named_tunnel,
        working_dir=working_dir,
        python_path=python_path,
        cloudflared_path=cloudflared_path,
    )

    bridge_unit = render_bridge_unit(cfg)
    tunnel_unit = render_cloudflared_unit(cfg)

    bridge_path = _SYSTEMD_USER_DIR / _BRIDGE_UNIT_NAME
    tunnel_path = _SYSTEMD_USER_DIR / _TUNNEL_UNIT_NAME

    warnings: List[str] = []

    # Check prerequisites
    if not shutil.which(cfg.python_path):
        warnings.append(
            f"python3 not found at '{cfg.python_path}'. "
            f"Install python3 or specify python_path."
        )
    if not shutil.which(cfg.cloudflared_path):
        warnings.append(
            f"cloudflared not found at '{cfg.cloudflared_path}'. "
            f"Install cloudflared or specify cloudflared_path."
        )

    if dry_run:
        return json.dumps({
            "ok": True,
            "dry_run": True,
            "bridge_unit": bridge_unit,
            "cloudflared_unit": tunnel_unit,
            "paths": {
                "bridge": str(bridge_path),
                "cloudflared": str(tunnel_path),
            },
            "config": {
                "mode": cfg.mode,
                "tunnel_mode": cfg.tunnel_mode,
                "host": cfg.host,
                "port": cfg.port,
                "named_tunnel": cfg.named_tunnel,
                "python_path": cfg.python_path,
                "cloudflared_path": cfg.cloudflared_path,
            },
            "warnings": warnings,
        }, indent=2)

    # Write unit files
    if not warnings:
        try:
            _SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
            bridge_path.write_text(bridge_unit)
            tunnel_path.write_text(tunnel_unit)
        except OSError as e:
            return json.dumps({
                "ok": False,
                "error": f"Failed to write unit files: {e}",
                "bridge_unit": bridge_unit,
                "cloudflared_unit": tunnel_unit,
            }, indent=2)

    # Daemon reload
    daemon_result = _run_sysctl(["daemon-reload"])
    if not daemon_result["success"]:
        warnings.append(
            f"daemon-reload failed: {daemon_result['stderr']}"
        )

    enable_results = []
    if enable:
        for svc in [_BRIDGE_UNIT_NAME, _TUNNEL_UNIT_NAME]:
            er = _run_sysctl(["enable", svc])
            enable_results.append({
                "service": svc,
                "success": er["success"],
                "exit_code": er["exit_code"],
                "stdout": er["stdout"],
                "stderr": er["stderr"],
            })
            if not er["success"]:
                warnings.append(
                    f"enable {svc} failed: {er['stderr']}"
                )

    return json.dumps({
        "ok": True,
        "dry_run": False,
        "paths": {
            "bridge": str(bridge_path),
            "cloudflared": str(tunnel_path),
        },
        "daemon_reload": daemon_result,
        "enable_results": enable_results,
        "warnings": warnings,
    }, indent=2)


def start_services() -> str:
    """Start both bridge and tunnel systemd user services.

    Returns:
        JSON with start results for each service.
    """
    bridge_result = _run_sysctl(["start", _BRIDGE_UNIT_NAME])
    tunnel_result = _run_sysctl(["start", _TUNNEL_UNIT_NAME])

    # Give the bridge a moment to bind before starting tunnel
    if bridge_result["success"]:
        import time
        time.sleep(1)
        # Restart tunnel in case bridge wasn't ready
        tunnel_result = _run_sysctl(["restart", _TUNNEL_UNIT_NAME])

    return json.dumps({
        "bridge": bridge_result,
        "tunnel": tunnel_result,
        "hint": (
            "To see the Quick Tunnel URL, run:\n"
            "  journalctl --user -u chatgpt-mcp-cloudflared.service -f"
        ),
    }, indent=2)


def stop_services() -> str:
    """Stop tunnel first, then bridge.

    Returns:
        JSON with stop results for each service.
    """
    tunnel_result = _run_sysctl(["stop", _TUNNEL_UNIT_NAME])
    bridge_result = _run_sysctl(["stop", _BRIDGE_UNIT_NAME])

    return json.dumps({
        "tunnel": tunnel_result,
        "bridge": bridge_result,
    }, indent=2)


def status_services() -> str:
    """Get status of both services and the bridge.

    Returns:
        JSON with plugin status, JobStore status, service statuses,
        local MCP URL, and helpful commands.
    """
    from .jobs import JobStore

    store = JobStore()
    all_jobs = store.list_jobs(limit=1000)
    status_counts = {}
    for j in all_jobs:
        status_counts[j.status] = status_counts.get(j.status, 0) + 1

    # Service statuses
    bridge_active = _is_active(_BRIDGE_UNIT_NAME)
    tunnel_active = _is_active(_TUNNEL_UNIT_NAME)

    bridge_status_raw = _run_sysctl([
        "status", _BRIDGE_UNIT_NAME, "--no-pager"
    ])
    tunnel_status_raw = _run_sysctl([
        "status", _TUNNEL_UNIT_NAME, "--no-pager"
    ])

    local_mcp_url = f"http://127.0.0.1:9100/mcp"

    return json.dumps({
        "type": "bridge",
        "plugin": "chatgpt_mcp_bridge",
        "version": "0.2.0",
        "local_mcp_url": local_mcp_url,
        "job_store": {
            "total_jobs": len(all_jobs),
            "status_counts": status_counts,
        },
        "bridge_service": {
            "name": _BRIDGE_UNIT_NAME,
            "is_active": bridge_active,
            "status_output": bridge_status_raw.get("stdout", ""),
            "error": bridge_status_raw.get("stderr", ""),
        },
        "tunnel_service": {
            "name": _TUNNEL_UNIT_NAME,
            "is_active": tunnel_active,
            "status_output": tunnel_status_raw.get("stdout", ""),
            "error": tunnel_status_raw.get("stderr", ""),
        },
        "helpful_commands": [
            f"systemctl --user start {_BRIDGE_UNIT_NAME}",
            f"systemctl --user start {_TUNNEL_UNIT_NAME}",
            f"systemctl --user stop {_TUNNEL_UNIT_NAME}",
            f"systemctl --user stop {_BRIDGE_UNIT_NAME}",
            f"journalctl --user -u {_BRIDGE_UNIT_NAME} -f",
            f"journalctl --user -u {_TUNNEL_UNIT_NAME} -f",
        ],
    }, indent=2)
