"""
MCP tool implementations for chatgpt_mcp_bridge.

Each tool is a plain function that operates on the shared JobStore.
They are registered with FastMCP via the plugin's register() function.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Optional

from .jobs import JobStore, JobRecord
from .telegram_mirror import TelegramMirror
from . import services as svc

logger = logging.getLogger("chatgpt_mcp_bridge.tools")

# ---------------------------------------------------------------------------
# Shared state — set by register()
# ---------------------------------------------------------------------------

_store: Optional[JobStore] = None
_mirror: Optional[TelegramMirror] = None
_cancel_tokens: Dict[str, threading.Event] = {}  # job_id -> cancel event
_lock = threading.Lock()


def _ensure():
    global _store, _mirror
    if _store is None:
        _store = JobStore()
    if _mirror is None:
        _mirror = TelegramMirror(_store)


# ===========================================================================
# Tool: chatgpt_agent_start
# ===========================================================================

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

    Creates a job in the persistent store and returns the job_id immediately.
    The actual agent runtime is launched asynchronously in a background task.

    Args:
        prompt: The user prompt to send to Hermes Agent.
        model: Model to use (empty = default).
        max_iterations: Max tool-use iterations.
        tools: JSON array of tool names to enable.
        context: Additional context for the agent.
        rules: Additional rules/instructions.
        system_prompt: Override system prompt.
        mirror_to_telegram: Whether to mirror to Telegram.
        telegram_target: Telegram target (e.g. 'telegram:528368879').

    Returns:
        JSON with job_id, status, and timestamps.
    """
    _ensure()

    # Parse tools list
    try:
        tools_list = json.loads(tools) if tools else []
        if not isinstance(tools_list, list):
            tools_list = []
    except (json.JSONDecodeError, TypeError):
        tools_list = []

    # Create the job record
    record = _store.create_job(
        prompt=prompt,
        model=model,
        max_iterations=max_iterations,
        tools=tools_list,
        context=context,
        rules=rules,
        system_prompt=system_prompt,
        telegram_target=telegram_target,
        mirror_to_telegram=mirror_to_telegram,
    )

    # Send Telegram start notification
    if mirror_to_telegram and telegram_target:
        try:
            _mirror.notify_start(record.job_id, prompt, telegram_target)
        except Exception as e:
            logger.warning("Telegram start notification failed: %s", e)

    # Register cancel token
    with _lock:
        _cancel_tokens[record.job_id] = threading.Event()

    # Launch background runtime (placeholder — actual implementation hooks
    # into the Hermes agent lifecycle via the plugin context).
    _start_background_job(record)

    return json.dumps({
        "job_id": record.job_id,
        "status": record.status,
        "model": record.model,
        "created_at": record.created_at,
        "mirror_to_telegram": record.mirror_to_telegram,
        "telegram_target": record.telegram_target,
    }, indent=2)


def _start_background_job(record: JobRecord):
    """Launch the agent runtime in a background thread.

    This is the bridge point where ChatGPT Web's async request connects
    to the Hermes Agent lifecycle. The actual implementation depends on
    how the plugin context exposes the agent runtime.
    """
    def _run():
        try:
            # Update status to running
            _store.update_job(record.job_id, status="running", started_at=time.time())

            if _mirror:
                try:
                    _mirror.notify_start(record.job_id, record.prompt, record.telegram_target)
                except Exception:
                    pass

            # --- Placeholder: actual agent invocation ---
            # In the real implementation, this is where we:
            # 1. Load the agent with the specified model/tools/context
            # 2. Run the agent loop with the given prompt
            # 3. Capture the response
            # 4. Update the job record with the result
            #
            # The plugin's register() function has access to the
            # PluginContext which can invoke agent.run() or delegate_task.
            #
            # For now, simulate a quick response:
            response = f"Job {record.job_id} executed. Prompt: {record.prompt[:100]}"
            error = ""

            # Update final state
            _store.update_job(
                record.job_id,
                status="done",
                response=response,
                error=error,
                completed_at=time.time(),
                heartbeat_at=time.time(),
            )

            if _mirror:
                try:
                    _mirror.notify_complete(record.job_id, response, record.telegram_target)
                except Exception:
                    pass

        except Exception as e:
            error = str(e)
            _store.update_job(
                record.job_id,
                status="error",
                error=error,
                completed_at=time.time(),
            )
            logger.error("Background job %s failed: %s", record.job_id, e)
            if _mirror:
                try:
                    _mirror.notify_error(record.job_id, error, record.telegram_target)
                except Exception:
                    pass
        finally:
            # Clean up cancel token
            with _lock:
                _cancel_tokens.pop(record.job_id, None)

    t = threading.Thread(target=_run, daemon=True, name=f"cgb-job-{record.job_id}")
    t.start()


# ===========================================================================
# Tool: chatgpt_agent_status
# ===========================================================================

def chatgpt_agent_status(job_id: str) -> str:
    """Get the status of a job.

    Args:
        job_id: The job ID from chatgpt_agent_start.

    Returns:
        JSON with status, timestamps, iterations, and prompt preview.
    """
    _ensure()
    record = _store.get_job(job_id)

    if record is None:
        return json.dumps({
            "job_id": job_id,
            "error": f"Job not found: {job_id}",
        }, indent=2)

    return json.dumps({
        "job_id": record.job_id,
        "status": record.status,
        "model": record.model,
        "prompt_preview": record.prompt[:200],
        "created_at": record.created_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
        "iterations": record.iterations,
        "heartbeat_at": record.heartbeat_at,
        "mirror_to_telegram": record.mirror_to_telegram,
        "telegram_target": record.telegram_target,
    }, indent=2)


# ===========================================================================
# Tool: chatgpt_agent_result
# ===========================================================================

def chatgpt_agent_result(job_id: str) -> str:
    """Get the result of a completed job.

    Args:
        job_id: The job ID from chatgpt_agent_start.

    Returns:
        JSON with response, error, and job metadata.
    """
    _ensure()
    record = _store.get_job(job_id)

    if record is None:
        return json.dumps({
            "job_id": job_id,
            "error": f"Job not found: {job_id}",
        }, indent=2)

    return json.dumps({
        "job_id": record.job_id,
        "status": record.status,
        "response": record.response,
        "error": record.error,
        "model": record.model,
        "iterations": record.iterations,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
    }, indent=2)


# ===========================================================================
# Tool: chatgpt_agent_cancel
# ===========================================================================

def chatgpt_agent_cancel(job_id: str) -> str:
    """Cancel a running job.

    Args:
        job_id: The job ID to cancel.

    Returns:
        JSON with result status.
    """
    _ensure()

    record = _store.get_job(job_id)
    if record is None:
        return json.dumps({
            "job_id": job_id,
            "error": f"Job not found: {job_id}",
        }, indent=2)

    if record.status not in {"queued", "running"}:
        return json.dumps({
            "job_id": job_id,
            "status": record.status,
            "error": f"Cannot cancel job in status '{record.status}'",
        }, indent=2)

    # Signal cancellation via the cancel token
    with _lock:
        cancel_event = _cancel_tokens.get(job_id)

    if cancel_event is not None:
        cancel_event.set()

    _store.update_job(job_id, status="cancelled", completed_at=time.time())
    logger.info("Job %s cancelled", job_id)

    return json.dumps({
        "job_id": job_id,
        "status": "cancelled",
        "message": "Job cancelled",
    }, indent=2)


# ===========================================================================
# Tool: chatgpt_bridge_status
# ===========================================================================

def chatgpt_bridge_status(job_id: str = "") -> str:
    """Get bridge status — either general stats, a specific job, or systemd service status.

    Args:
        job_id: Optional job ID. Empty = general bridge + service status.

    Returns:
        JSON with bridge stats, JobStore info, systemd service statuses,
        local MCP URL, and helpful commands.
    """
    _ensure()

    if job_id:
        # Specific job status
        record = _store.get_job(job_id)
        if record is None:
            return json.dumps({
                "error": f"Job not found: {job_id}",
            }, indent=2)
        return json.dumps({
            "type": "job",
            "job_id": record.job_id,
            "status": record.status,
            "model": record.model,
            "prompt_preview": record.prompt[:200],
            "iterations": record.iterations,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "mirror_to_telegram": record.mirror_to_telegram,
            "telegram_target": record.telegram_target,
        }, indent=2)

    # General bridge + systemd service status
    all_jobs = _store.list_jobs(limit=1000)
    status_counts = {}
    for j in all_jobs:
        status_counts[j.status] = status_counts.get(j.status, 0) + 1

    # Get systemd service info via the services module
    service_info = svc.status_services()
    service_data = json.loads(service_info)

    return json.dumps({
        "type": "bridge",
        "plugin": "chatgpt_mcp_bridge",
        "version": "0.2.0",
        "total_jobs": len(all_jobs),
        "status_counts": status_counts,
        "recent_jobs": [
            {
                "job_id": j.job_id,
                "status": j.status,
                "prompt_preview": j.prompt[:100],
                "created_at": j.created_at,
            }
            for j in all_jobs[:10]
        ],
        "systemd_services": {
            "bridge": service_data.get("bridge_service", {}),
            "tunnel": service_data.get("tunnel_service", {}),
        },
        "local_mcp_url": service_data.get("local_mcp_url", "http://127.0.0.1:9100/mcp"),
        "helpful_commands": service_data.get("helpful_commands", []),
    }, indent=2)


# ===========================================================================
# Tool: chatgpt_bridge_install_services
# ===========================================================================

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
    """Install (or dry-run) systemd user services for the bridge.

    Generates unit files for:
      1. chatgpt-mcp-bridge.service — standalone MCP server
      2. chatgpt-mcp-cloudflared.service — Cloudflare tunnel

    Args:
        mode: Service mode (only 'user' supported).
        tunnel_mode: 'quick' or 'named'.
        host: Bind address.
        port: Port for MCP server.
        named_tunnel: Tunnel name (required if tunnel_mode='named').
        working_dir: Working directory.
        python_path: Path to python3.
        cloudflared_path: Path to cloudflared.
        dry_run: If true, only render unit contents.
        enable: If true, enable services via systemctl.

    Returns:
        JSON with ok, unit contents, paths, warnings.
    """
    return json.dumps(svc.install_services(
        mode=mode,
        tunnel_mode=tunnel_mode,
        host=host,
        port=port,
        named_tunnel=named_tunnel if named_tunnel else None,
        working_dir=working_dir if working_dir else None,
        python_path=python_path if python_path else None,
        cloudflared_path=cloudflared_path if cloudflared_path else None,
        dry_run=dry_run,
        enable=enable,
    ), indent=2)


# ===========================================================================
# Tool: chatgpt_bridge_start_services
# ===========================================================================

def chatgpt_bridge_start_services() -> str:
    """Start both bridge and tunnel systemd user services.

    Returns:
        JSON with start results and hint to check tunnel URL via journalctl.
    """
    return svc.start_services()


# ===========================================================================
# Tool: chatgpt_bridge_stop_services
# ===========================================================================

def chatgpt_bridge_stop_services() -> str:
    """Stop tunnel first, then bridge systemd user services.

    Returns:
        JSON with stop results for each service.
    """
    return svc.stop_services()
