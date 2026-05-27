"""
MCP tool implementations for chatgpt_mcp_bridge.

Tools:
  chatgpt_agent_start               — dispatch agent job, return job_id immediately
  chatgpt_agent_status              — poll queued/running/done/error/cancelled
  chatgpt_agent_result              — get response/error for a completed job
  chatgpt_agent_cancel              — interrupt a running job
  chatgpt_bridge_status             — bridge health + JobStore stats + systemd status
  chatgpt_bridge_install_services   — install systemd user services
  chatgpt_bridge_start_services     — start bridge + tunnel services
  chatgpt_bridge_stop_services      — stop tunnel then bridge services
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chatgpt_mcp_bridge")

# Shared state — populated by __init__.py register()
_store = None  # JobStore
_mirror = None  # TelegramMirror

# In-memory cancel events: job_id -> threading.Event
_cancel_events: dict[str, threading.Event] = {}


# ---------------------------------------------------------------------------
# Real Hermes Agent invocation (subprocess fallback)
# ---------------------------------------------------------------------------

def run_hermes_agent_job(record, cancel_event: threading.Event | None = None) -> dict:
    """Run a Hermes Agent job via subprocess fallback.

    Invokes ``hermes chat -q <final_prompt>`` with optional model, toolsets,
    max-turns, and --ignore-rules (to prevent auto-injection of AGENTS.md/SOUL.md/
    memory when custom system_prompt or rules are provided).

    Returns a dict with keys: response, error, iterations, session_id.

    Args:
        record: JobRecord with all job parameters.
        cancel_event: If set, the subprocess will be terminated when signaled.

    Returns:
        {"response": str, "error": str, "iterations": int, "session_id": str}
    """
    hermes_bin = shutil.which("hermes")
    if not hermes_bin:
        return {
            "response": "",
            "error": "hermes binary not found in PATH",
            "iterations": 0,
            "session_id": "",
        }

    # Build final_prompt WITHOUT mutating record.prompt (Fix 2)
    # Order: system_prompt override first, then context, then rules, then the original prompt
    final_prompt = record.prompt

    if record.system_prompt:
        final_prompt = f"[System: {record.system_prompt}]\n\n{final_prompt}"

    if record.context:
        final_prompt = f"[Context: {record.context}]\n\n{final_prompt}"

    if record.rules:
        final_prompt = f"{final_prompt}\n\nAdditional rules:\n{record.rules}"

    # Build cmd — confirmed CLI flags from `hermes chat --help`:
    #   -q QUERY     (required)
    #   -m MODEL     (optional)
    #   -t TOOLSETS  (comma-separated)
    #   -s SKILLS    (comma-separated, for context/roles)
    #   --max-turns N (optional, default 90)
    #   -Q           (quiet mode)
    #   --ignore-rules (skip AGENTS.md/SOUL.md/memory when custom rules/system_prompt used)
    cmd = [hermes_bin, "chat", "-q", final_prompt, "-Q"]

    if record.model:
        cmd.extend(["-m", record.model])

    # tools parameter is a JSON array string like '["web","terminal"]' (Fix 5: robust parsing)
    if record.tools and record.tools.strip() != "[]":
        try:
            tool_list = json.loads(record.tools)
            if isinstance(tool_list, list) and tool_list:
                cmd.extend(["-t", ",".join(tool_list)])
        except (json.JSONDecodeError, TypeError):
            # Invalid JSON — skip tools, run with defaults (Fix 5)
            pass

    if record.max_iterations > 0:
        cmd.extend(["--max-turns", str(record.max_iterations)])

    # If custom system_prompt or rules are provided, skip auto-injection of
    # AGENTS.md, SOUL.md, .cursorrules, memory, and preloaded skills
    if record.system_prompt or record.rules:
        cmd.append("--ignore-rules")

    logger.info("Running hermes agent: %s", " ".join(cmd[:5]) + "...")

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait with cancel support
        while True:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return {
                    "response": "",
                    "error": "Job was cancelled by user",
                    "iterations": 0,
                    "session_id": record.session_id or "",
                }

            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(0.5)

        stdout = proc.stdout.read() if proc.stdout else ""
        stderr = proc.stderr.read() if proc.stderr else ""

        # Parse session_id from stderr (hermes outputs it at the end)
        session_id = record.session_id or ""
        for line in stderr.splitlines():
            if line.startswith("session_id:"):
                session_id = line.split(":", 1)[1].strip()
                break

        if proc.returncode != 0:
            return {
                "response": "",
                "error": stderr.strip() or f"hermes exited with code {proc.returncode}",
                "iterations": 0,
                "session_id": session_id,
            }

        # Extract response: hermes --quiet outputs the response first, then session info
        lines = stdout.strip().splitlines()
        response_lines = []
        for line in lines:
            if line.startswith("session_id:"):
                break
            response_lines.append(line)
        response = "\n".join(response_lines).strip()

        return {
            "response": response,
            "error": "",
            "iterations": 1,  # Single-query mode = 1 iteration
            "session_id": session_id,
        }

    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
        return {
            "response": "",
            "error": "Job timed out after 600 seconds",
            "iterations": 0,
            "session_id": record.session_id or "",
        }
    except Exception as exc:
        return {
            "response": "",
            "error": str(exc),
            "iterations": 0,
            "session_id": record.session_id or "",
        }


# ---------------------------------------------------------------------------
# chatgpt_agent_start
# ---------------------------------------------------------------------------

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
    """Start a new agent job. Creates a record, sends Telegram start (if requested),
    launches background thread, and returns job_id immediately."""

    if _store is None:
        return json.dumps({"error": "JobStore not initialized. Call register() first."})

    # 1. Create job record (status=queued)
    # Parse tools JSON robustly (Fix 5: protect create_job from invalid JSON too)
    parsed_tools = []
    if tools and tools.strip() != "[]":
        try:
            parsed_tools = json.loads(tools)
            if not isinstance(parsed_tools, list):
                parsed_tools = []
        except (json.JSONDecodeError, TypeError):
            parsed_tools = []

    record = _store.create_job(
        prompt=prompt,
        model=model,
        max_iterations=max_iterations,
        tools=parsed_tools,
        context=context,
        rules=rules,
        system_prompt=system_prompt,
        telegram_target=telegram_target,
        mirror_to_telegram=mirror_to_telegram,
    )
    job_id = record.job_id

    # 2. Send Telegram start notification ONCE here (not in background thread)
    # Wrap in try/except so Telegram failures cannot break job creation
    if mirror_to_telegram and _mirror is not None:
        tg_target = telegram_target or ""
        try:
            _mirror.notify_start(job_id, prompt, tg_target)
        except Exception:
            logger.exception("Telegram notify_start failed for %s", job_id)

    # 3. Create cancel event and store it
    cancel_evt = threading.Event()
    _cancel_events[job_id] = cancel_evt

    # 4. Launch background thread (non-blocking, <1s)
    # Pass store and mirror by value so the thread has stable references
    # even if module-level globals change (e.g., during test teardown)
    t = threading.Thread(
        target=_start_background_job,
        args=(record, cancel_evt, _store, _mirror),
        daemon=True,
        name=f"cgpt-job-{job_id}",
    )
    t.start()

    return json.dumps({
        "job_id": job_id,
        "status": "queued",
        "prompt_preview": prompt[:200],
    })


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _start_background_job(
    record,
    cancel_event: threading.Event,
    store,
    mirror,
) -> None:
    """Background thread: set status=running, run agent, set status=done/error/cancelled.

    All Telegram mirror calls are wrapped in try/except (Fix 6) so that
    notification failures never break job execution.

    ``store`` and ``mirror`` are passed by value (not module-level globals) so
    the thread keeps working even if the caller resets ``tools._store`` /
    ``tools._mirror`` (e.g. test teardown).
    """
    if store is None:
        return

    job_id = record.job_id
    logger.info("Background job %s starting", job_id)

    try:
        # Check if already cancelled before starting
        if cancel_event.is_set():
            store.update_job(job_id, status="cancelled", completed_at=time.time())
            if mirror is not None:
                try:
                    mirror.notify_cancelled(job_id)
                except Exception:
                    logger.exception("Telegram notify_cancelled failed for %s", job_id)
            return

        # Set to running
        store.update_job(job_id, status="running", started_at=time.time())

        # Run the actual Hermes Agent
        result = run_hermes_agent_job(record, cancel_event)

        # Check if cancelled during execution
        if cancel_event.is_set():
            store.update_job(
                job_id,
                status="cancelled",
                completed_at=time.time(),
                iterations=result.get("iterations", 0),
                session_id=result.get("session_id", ""),
            )
            if mirror is not None:
                try:
                    mirror.notify_cancelled(job_id)
                except Exception:
                    logger.exception("Telegram notify_cancelled failed for %s", job_id)
            return

        # Success or error
        if result["error"]:
            store.update_job(
                job_id,
                status="error",
                response="",
                error=result["error"],
                iterations=result.get("iterations", 0),
                session_id=result.get("session_id", ""),
                completed_at=time.time(),
            )
            if mirror is not None:
                try:
                    tg_target = record.telegram_target or ""
                    mirror.notify_error(job_id, result["error"], tg_target)
                except Exception:
                    logger.exception("Telegram notify_error failed for %s", job_id)
        else:
            store.update_job(
                job_id,
                status="done",
                response=result["response"],
                error="",
                iterations=result.get("iterations", 0),
                session_id=result.get("session_id", ""),
                completed_at=time.time(),
            )
            if mirror is not None:
                try:
                    tg_target = record.telegram_target or ""
                    mirror.notify_complete(job_id, result["response"], tg_target)
                except Exception:
                    logger.exception("Telegram notify_complete failed for %s", job_id)

    except Exception as exc:
        logger.exception("Background job %s failed: %s", job_id, exc)
        if store is not None:
            store.update_job(job_id, status="error", error=str(exc), completed_at=time.time())
        if mirror is not None:
            try:
                tg_target = record.telegram_target or ""
                mirror.notify_error(job_id, str(exc), tg_target)
            except Exception:
                logger.exception("Telegram notify_error failed for %s", job_id)
    finally:
        # Cleanup cancel event reference (Fix 7: don't delete in cancel(), clean up here)
        _cancel_events.pop(job_id, None)


# ---------------------------------------------------------------------------
# chatgpt_agent_status
# ---------------------------------------------------------------------------

def chatgpt_agent_status(job_id: str) -> str:
    """Get status of a job."""
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    record = _store.get_job(job_id)
    if not record:
        return json.dumps({"error": f"Job {job_id} not found"})

    return json.dumps({
        "job_id": record.job_id,
        "status": record.status,
        "prompt": record.prompt[:200],
        "model": record.model,
        "iterations": record.iterations,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
    })


# ---------------------------------------------------------------------------
# chatgpt_agent_result
# ---------------------------------------------------------------------------

def chatgpt_agent_result(job_id: str) -> str:
    """Get the result of a completed job."""
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    record = _store.get_job(job_id)
    if not record:
        return json.dumps({"error": f"Job {job_id} not found"})

    return json.dumps({
        "job_id": record.job_id,
        "status": record.status,
        "response": record.response,
        "error": record.error,
        "iterations": record.iterations,
        "session_id": record.session_id,
        "created_at": record.created_at,
        "completed_at": record.completed_at,
    })


# ---------------------------------------------------------------------------
# chatgpt_agent_cancel
# ---------------------------------------------------------------------------

def chatgpt_agent_cancel(job_id: str) -> str:
    """Cancel a running job.

    Sets the cancel event and updates status to 'cancelled'.
    The background thread checks the cancel event before and after
    each polling cycle.

    For subprocess-based backend, the process is terminated with
    SIGTERM followed by SIGKILL after a grace period.

    Fix 7: Does NOT delete the cancel event — cleanup happens in
    _start_background_job's finally block.
    """
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    record = _store.get_job(job_id)
    if not record:
        return json.dumps({"error": f"Job {job_id} not found"})

    if record.status not in ("running", "queued"):
        return json.dumps({
            "job_id": job_id,
            "status": record.status,
            "cancelled": False,
            "reason": f"Job is already {record.status}, cannot cancel",
        })

    # Signal cancellation — do NOT delete the event (Fix 7)
    cancel_evt = _cancel_events.get(job_id)
    if cancel_evt:
        cancel_evt.set()

    # Update status
    _store.update_job(job_id, status="cancelled", completed_at=time.time())

    return json.dumps({
        "job_id": job_id,
        "cancelled": True,
        "cancellation_supported": True,
        "status": "cancelled",
    })


# ---------------------------------------------------------------------------
# chatgpt_bridge_status
# ---------------------------------------------------------------------------

def chatgpt_bridge_status(job_id: str = "") -> str:
    """Get bridge health, JobStore stats, and systemd service status."""
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    stats = {}
    try:
        all_jobs = _store.list_jobs(limit=100)
        stats = {"total": len(all_jobs)}
        for s in ("queued", "running", "done", "error", "cancelled"):
            stats[s] = sum(1 for j in all_jobs if j.status == s)
    except Exception:
        stats = {"total": 0, "error": "failed to count jobs"}

    result: dict = {
        "bridge": "running",
        "job_stats": stats,
        "services": {},
    }

    # Check systemd services via subprocess
    for svc in ["chatgpt-mcp-bridge.service", "chatgpt-mcp-cloudflared.service"]:
        try:
            out = subprocess.run(
                ["systemctl", "--user", "is-active", svc],
                capture_output=True, text=True, timeout=5,
            )
            result["services"][svc] = out.stdout.strip()
        except Exception:
            result["services"][svc] = "unreachable"

    # If job_id provided, also include job detail
    if job_id:
        record = _store.get_job(job_id)
        if record:
            result["job"] = {
                "job_id": record.job_id,
                "status": record.status,
                "prompt": record.prompt[:200],
            }
        else:
            result["job"] = {"error": f"Job {job_id} not found"}

    return json.dumps(result)


# ---------------------------------------------------------------------------
# Service management tools
# ---------------------------------------------------------------------------

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

    Returns a JSON object (not double-encoded).
    """
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    from . import services as svc

    # Call services module — it returns a Python dict now (Finding 4 fix)
    result = svc.install_services(
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
    return json.dumps(result)


def chatgpt_bridge_start_services() -> str:
    """Start both bridge and tunnel systemd user services."""
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    from . import services as svc

    result = svc.start_services()
    return json.dumps(result)


def chatgpt_bridge_stop_services() -> str:
    """Stop tunnel first, then bridge systemd user services."""
    if _store is None:
        return json.dumps({"error": "JobStore not initialized"})

    from . import services as svc

    result = svc.stop_services()
    return json.dumps(result)
