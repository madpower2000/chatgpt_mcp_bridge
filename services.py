"""
Systemd service management for chatgpt_mcp_bridge.

Generates and manages two systemd user services:
  - chatgpt-mcp-bridge.service   — standalone FastMCP server
  - chatgpt-mcp-cloudflared.service — Cloudflare tunnel

All functions return Python dicts (not JSON strings) to avoid double-encoding.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chatgpt_mcp_bridge")

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}
_VALID_TUNNEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _validate_host(host: str) -> str | None:
    """Validate bind address. Returns error string or None."""
    host = host.strip()
    if not host:
        return "host must not be empty"
    if host not in _ALLOWED_HOSTS:
        return f"host '{host}' is not allowed. Use one of: {', '.join(sorted(_ALLOWED_HOSTS))}"
    return None


def _validate_port(port: int) -> str | None:
    """Validate port number. Returns error string or None."""
    if not isinstance(port, int) or port < 1024 or port > 65535:
        return f"port must be an integer between 1024 and 65535 (got {port})"
    return None


def _validate_tunnel_mode(mode: str) -> str | None:
    """Validate tunnel mode. Returns error string or None."""
    if mode not in ("quick", "named"):
        return f"tunnel_mode must be 'quick' or 'named' (got '{mode}')"
    return None


def _validate_named_tunnel(named: str, mode: str) -> str | None:
    """Validate named tunnel name. Returns error string or None."""
    if mode == "named":
        if not named or not named.strip():
            return "named_tunnel is required when tunnel_mode='named'"
        if not _VALID_TUNNEL_RE.match(named):
            return f"named_tunnel '{named}' contains invalid characters. Only [A-Za-z0-9_.-] allowed"
    return None


def _validate_path(path: str | None, must_exist: bool = False, must_be_dir: bool = False) -> str | None:
    """Validate a filesystem path. Returns error string or None."""
    if path is None or path.strip() == "":
        return None  # Optional — auto-detect
    path = path.strip()
    if not os.path.isabs(path):
        return f"path must be absolute: '{path}'"
    # Reject paths with newlines, control chars, or dangerous characters
    if re.search(r"[\n\r\t\x00]", path):
        return f"path contains control characters: '{path}'"
    if must_exist:
        if not os.path.exists(path):
            return f"path does not exist: '{path}'"
        if must_be_dir and not os.path.isdir(path):
            return f"path is not a directory: '{path}'"
    return None


def _which(name: str) -> str | None:
    """Wrapper around shutil.which."""
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def _resolve_config(
    mode: str,
    tunnel_mode: str,
    host: str,
    port: int,
    named_tunnel: str,
    working_dir: str,
    python_path: str,
    cloudflared_path: str,
) -> tuple[dict, list[str], list[str]]:
    """Resolve and validate all configuration.

    Returns:
        (config_dict, warnings_list, errors_list)
    """
    errors: list[str] = []
    warnings: list[str] = []
    config: dict = {}

    # Validate host
    err = _validate_host(host)
    if err:
        errors.append(err)
    config["host"] = host.strip()

    # Validate port
    err = _validate_port(port)
    if err:
        errors.append(err)
    config["port"] = port

    # Validate tunnel_mode
    err = _validate_tunnel_mode(tunnel_mode)
    if err:
        errors.append(err)
    config["tunnel_mode"] = tunnel_mode

    # Validate named_tunnel
    err = _validate_named_tunnel(named_tunnel, tunnel_mode)
    if err:
        errors.append(err)
    config["named_tunnel"] = named_tunnel.strip()

    # Validate working_dir
    if working_dir and working_dir.strip():
        err = _validate_path(working_dir.strip(), must_exist=True, must_be_dir=True)
        if err:
            errors.append(err)
        config["working_dir"] = os.path.abspath(working_dir.strip())
    else:
        config["working_dir"] = str(Path.home())

    # Resolve python_path
    if python_path and python_path.strip():
        err = _validate_path(python_path.strip(), must_exist=True)
        if err:
            errors.append(err)
            config["python_path"] = ""
        else:
            config["python_path"] = os.path.abspath(python_path.strip())
    else:
        found = _which("python3")
        if found:
            config["python_path"] = found
        else:
            errors.append("python3 not found in PATH. Set python_path explicitly.")
            config["python_path"] = ""

    # Resolve cloudflared_path
    if cloudflared_path and cloudflared_path.strip():
        err = _validate_path(cloudflared_path.strip(), must_exist=True)
        if err:
            errors.append(err)
            config["cloudflared_path"] = ""
        else:
            config["cloudflared_path"] = os.path.abspath(cloudflared_path.strip())
    else:
        found = _which("cloudflared")
        if found:
            config["cloudflared_path"] = found
        else:
            errors.append("cloudflared not found in PATH. Install it or set cloudflared_path explicitly.")
            config["cloudflared_path"] = ""

    # Warnings for non-standard hosts
    if config["host"] == "0.0.0.0":
        warnings.append("host is 0.0.0.0 — server will listen on all interfaces (not recommended)")

    return config, warnings, errors


# ---------------------------------------------------------------------------
# Unit file generation
# ---------------------------------------------------------------------------

def _render_bridge_unit(config: dict) -> str:
    """Render the bridge systemd unit file."""
    home = str(Path.home())
    plugin_dir = os.path.join(home, ".hermes", "plugins")
    python = config["python_path"] or "python3"
    host = config["host"]
    port = config["port"]
    workdir = config["working_dir"]

    return (
        f"[Unit]\n"
        f"Description=ChatGPT MCP Bridge — standalone FastMCP server\n"
        f"After=network.target\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={python} -m chatgpt_mcp_bridge --host {host} --port {port}\n"
        f"WorkingDirectory={workdir}\n"
        f"Environment=PYTHONPATH={plugin_dir}:{python}\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"StandardOutput=journal\n"
        f"StandardError=journal\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


def _render_cloudflared_unit(config: dict) -> str:
    """Render the Cloudflare tunnel systemd unit file."""
    home = str(Path.home())
    cloudflared = config["cloudflared_path"] or "cloudflared"
    host = config["host"]
    port = config["port"]
    workdir = config["working_dir"]
    tunnel_mode = config["tunnel_mode"]
    named = config["named_tunnel"]

    if tunnel_mode == "quick":
        tunnel_arg = f"tunnel --url http://{host}:{port}"
    else:
        tunnel_arg = f"tunnel run {named}"

    return (
        f"[Unit]\n"
        f"Description=ChatGPT MCP Bridge — Cloudflare tunnel\n"
        f"After=network.target\n"
        f"Requires=chatgpt-mcp-bridge.service\n"
        f"\n"
        f"[Service]\n"
        f"Type=simple\n"
        f"ExecStart={cloudflared} {tunnel_arg}\n"
        f"WorkingDirectory={workdir}\n"
        f"Restart=on-failure\n"
        f"RestartSec=5\n"
        f"StandardOutput=journal\n"
        f"StandardError=journal\n"
        f"\n"
        f"[Install]\n"
        f"WantedBy=default.target\n"
    )


# ---------------------------------------------------------------------------
# Public API — all return Python dicts
# ---------------------------------------------------------------------------

def install_services(
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
) -> dict:
    """Install systemd user services for the bridge and Cloudflare tunnel.

    Returns a dict with:
        ok, ready, dry_run, warnings, errors,
        bridge_unit, cloudflared_unit, paths,
        daemon_reload, enable_results
    """
    result: dict = {
        "ok": False,
        "ready": False,
        "dry_run": dry_run,
        "warnings": [],
        "errors": [],
        "paths": {},
        "daemon_reload": False,
        "enable_results": [],
    }

    # Validate and resolve config
    config, warnings, errors = _resolve_config(
        mode=mode,
        tunnel_mode=tunnel_mode,
        host=host,
        port=port,
        named_tunnel=named_tunnel,
        working_dir=working_dir,
        python_path=python_path,
        cloudflared_path=cloudflared_path,
    )

    result["warnings"] = warnings
    result["errors"] = errors

    # If there are errors (missing binaries, invalid config), return immediately
    if errors:
        result["ready"] = False
        result["ok"] = False
        return result

    # Render unit files
    bridge_unit = _render_bridge_unit(config)
    cloudflared_unit = _render_cloudflared_unit(config)

    result["bridge_unit"] = bridge_unit
    result["cloudflared_unit"] = cloudflared_unit

    if dry_run:
        result["ready"] = True
        result["ok"] = True
        return result

    # Write unit files
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    bridge_path = unit_dir / "chatgpt-mcp-bridge.service"
    cloudflared_path = unit_dir / "chatgpt-mcp-cloudflared.service"

    try:
        bridge_path.write_text(bridge_unit)
        result["paths"]["bridge"] = str(bridge_path)
    except Exception as exc:
        errors.append(f"Failed to write bridge unit: {exc}")
        result["errors"] = errors
        result["ok"] = False
        return result

    try:
        cloudflared_path.write_text(cloudflared_unit)
        result["paths"]["cloudflared"] = str(cloudflared_path)
    except Exception as exc:
        errors.append(f"Failed to write cloudflared unit: {exc}")
        result["errors"] = errors
        result["ok"] = False
        return result

    # Daemon reload
    daemon_ok = False
    try:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True, text=True, timeout=10,
        )
        daemon_ok = True
        result["daemon_reload"] = True
    except Exception as exc:
        errors.append(f"daemon-reload failed: {exc}")
        result["daemon_reload"] = False

    # Enable services
    enable_ok = True
    for svc_name in ["chatgpt-mcp-bridge.service", "chatgpt-mcp-cloudflared.service"]:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "enable", svc_name],
                capture_output=True, text=True, timeout=10,
            )
            ok = out.returncode == 0
            if not ok:
                enable_ok = False
                errors.append(f"enable {svc_name} failed: {out.stderr.strip()}")
            result["enable_results"].append({
                "service": svc_name,
                "success": ok,
                "error": out.stderr.strip() if out.returncode != 0 else "",
            })
        except Exception as exc:
            enable_ok = False
            errors.append(f"enable {svc_name} failed: {exc}")
            result["enable_results"].append({
                "service": svc_name,
                "success": False,
                "error": str(exc),
            })

    # Overall ok = daemon reload succeeded AND all enables succeeded AND no errors
    result["ok"] = daemon_ok and enable_ok and not errors
    result["ready"] = True

    return result


def start_services() -> dict:
    """Start both bridge and tunnel systemd user services.

    Returns dict with service statuses and hint.
    """
    result: dict = {}
    errors: list[str] = []

    for svc in ["chatgpt-mcp-bridge.service", "chatgpt-mcp-cloudflared.service"]:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "start", svc],
                capture_output=True, text=True, timeout=15,
            )
            result[svc] = {
                "success": out.returncode == 0,
                "error": out.stderr.strip() if out.returncode != 0 else "",
                "exit_code": out.returncode,
            }
        except Exception as exc:
            result[svc] = {
                "success": False,
                "error": str(exc),
                "exit_code": -1,
            }
            errors.append(f"{svc}: {exc}")

    if errors:
        result["error"] = "; ".join(errors)

    result["hint"] = (
        "View logs:\n"
        "  journalctl --user -u chatgpt-mcp-bridge.service -f\n"
        "  journalctl --user -u chatgpt-mcp-cloudflared.service -f\n"
        "Tunnel URL:\n"
        "  journalctl --user -u chatgpt-mcp-cloudflared.service -n 50 | grep trycloudflare.com"
    )

    return result


def stop_services() -> dict:
    """Stop tunnel first, then bridge.

    Returns dict with service statuses.
    """
    result: dict = {}
    errors: list[str] = []

    for svc in ["chatgpt-mcp-cloudflared.service", "chatgpt-mcp-bridge.service"]:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "stop", svc],
                capture_output=True, text=True, timeout=15,
            )
            result[svc] = {
                "success": out.returncode == 0,
                "error": out.stderr.strip() if out.returncode != 0 else "",
                "exit_code": out.returncode,
            }
        except Exception as exc:
            result[svc] = {
                "success": False,
                "error": str(exc),
                "exit_code": -1,
            }
            errors.append(f"{svc}: {exc}")

    if errors:
        result["error"] = "; ".join(errors)

    result["hint"] = "Stopped tunnel first, then bridge."
    return result


def status_services() -> dict:
    """Get status of all managed services.

    Returns dict with service statuses.
    """
    result: dict = {}

    for svc in ["chatgpt-mcp-bridge.service", "chatgpt-mcp-cloudflared.service"]:
        status = check_service(svc)
        result[svc] = {
            "is_active": status["is_active"],
            "pid": status.get("pid"),
            "memory": status.get("memory"),
            "result": status.get("result"),
            "error": status.get("error"),
        }

    # Also check local MCP endpoint
    try:
        check = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "http://127.0.0.1:9100/mcp",
             "-H", "Content-Type: application/json",
             "-H", "Accept: application/json, text/event-stream",
             "-d", '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'],
            capture_output=True, text=True, timeout=5,
        )
        code = check.stdout.strip()
        result["mcp_endpoint"] = {
            "url": "http://127.0.0.1:9100/mcp",
            "http_code": code,
            "reachable": code == "200",
        }
    except Exception as exc:
        result["mcp_endpoint"] = {
            "url": "http://127.0.0.1:9100/mcp",
            "reachable": False,
            "error": str(exc),
        }

    return result


def check_service(service_name: str) -> dict:
    """Check the status of a single systemd service.

    Returns dict with is_active, pid, memory, result, error.
    """
    result: dict = {
        "name": service_name,
        "is_active": False,
        "pid": None,
        "memory": None,
        "result": None,
        "error": None,
    }

    try:
        # Get active state
        out = subprocess.run(
            ["systemctl", "--user", "is-active", service_name],
            capture_output=True, text=True, timeout=5,
        )
        result["result"] = out.stdout.strip()
        result["is_active"] = out.stdout.strip() == "active"

        if result["is_active"]:
            # Get PID
            out = subprocess.run(
                ["systemctl", "--user", "show", service_name, "--property=MainPID"],
                capture_output=True, text=True, timeout=5,
            )
            pid_str = out.stdout.strip().split("=")[-1].strip()
            if pid_str and pid_str != "0":
                result["pid"] = int(pid_str)

            # Get memory
            out = subprocess.run(
                ["systemctl", "--user", "show", service_name, "--property=MemoryCurrent"],
                capture_output=True, text=True, timeout=5,
            )
            mem_str = out.stdout.strip().split("=")[-1].strip()
            if mem_str and mem_str != "0":
                mem_bytes = int(mem_str)
                if mem_bytes > 1048576:
                    result["memory"] = f"{mem_bytes / 1048576:.1f} MB"
                else:
                    result["memory"] = f"{mem_bytes / 1024:.1f} KB"

    except Exception as exc:
        result["error"] = str(exc)

    return result
