"""
Telegram mirror module.

Sends notifications to a Telegram target using Hermes' own bot credentials
(loaded from the HERMES_HOME .env file). Uses the send_message_tool path
identical to how mcp_serve.py does it internally.

Formats:
  - Start:      "ChatGPT → Hermes: <prompt truncated>"
  - Complete:   "Hermes → ChatGPT: <response truncated>"
  - Error:      "Hermes error: <error>"
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("chatgpt_mcp_bridge.telegram_mirror")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_PREVIEW = 200  # chars to include in Telegram preview


def _truncate(text: str, max_len: int = _MAX_PREVIEW) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _get_hermes_home() -> Path:
    """Resolve HERMES_HOME the same way core does."""
    try:
        from hermes_constants import get_hermes_home as _ghh
        return _ghh()
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _load_bot_credentials() -> tuple[str, str]:
    """Load BOT_TOKEN and BOT_USERNAME from Hermes .env.

    Returns (token, username) or raises if not found.
    """
    env_file = _get_hermes_home() / ".env"
    if not env_file.exists():
        raise RuntimeError(f"HERMES_HOME .env not found at {env_file}")

    creds = {}
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()

    token = creds.get("BOT_TOKEN") or creds.get("HERMES_BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN not found in Hermes .env")
    return token


def _resolve_target(target: str) -> str:
    """Normalize telegram target to the format send_message_tool expects.

    Accepts:
      - telegram:528368879   (already correct)
      - 528368879           -> telegram:528368879
      - telegram            -> home channel (let send_message_tool resolve)
    """
    target = target.strip()
    if target.startswith("telegram:"):
        return target
    if target.isdigit() or target.startswith("-"):
        return f"telegram:{target}"
    if target == "telegram":
        return "telegram"
    return target


def send_telegram_message(target: str, message: str) -> dict:
    """Send a single message via Hermes' send_message_tool.

    Uses the same internal tool path as mcp_serve.py — no direct API calls.
    Returns {"ok": True} or {"ok": False, "error": "..."}
    """
    target = _resolve_target(target)

    # Try the Python tool import first (same path as mcp_serve.py)
    try:
        from tools.send_message_tool import send_message_tool
        result_str = send_message_tool({
            "action": "send",
            "target": target,
            "message": message,
        })
        # send_message_tool returns JSON string
        import json
        result = json.loads(result_str)
        if result.get("error"):
            return {"ok": False, "error": result["error"]}
        return {"ok": True}
    except ImportError:
        logger.debug("send_message_tool not importable — falling back to CLI")
    except Exception as e:
        logger.debug("send_message_tool failed: %s — falling back to CLI", e)

    # Fallback: call hermes CLI directly
    try:
        result = subprocess.run(
            [sys.executable, "-m", "hermes_cli", "send", target, message],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return {"ok": True}
        return {"ok": False, "error": result.stderr.strip()[:200]}
    except Exception as e:
        logger.debug("CLI fallback failed: %s", e)
        return {"ok": False, "error": str(e)[:200]}


class TelegramMirror:
    """Manages Telegram notification for a single job."""

    def __init__(self, store):
        self._store = store
        self._sent = threading.local()  # track sent states per thread

    def notify_start(self, job_id: str, prompt: str, telegram_target: str):
        """Send start notification."""
        if not telegram_target:
            return
        target = _resolve_target(telegram_target)
        preview = _truncate(prompt)
        message = f"ChatGPT → Hermes: {preview}"
        self._send(target, message, job_id, "start")

    def notify_complete(self, job_id: str, response: str, telegram_target: str):
        """Send completion notification."""
        if not telegram_target:
            return
        target = _resolve_target(telegram_target)
        preview = _truncate(response)
        message = f"Hermes → ChatGPT: {preview}"
        self._send(target, message, job_id, "complete")

    def notify_error(self, job_id: str, error: str, telegram_target: str):
        """Send error notification."""
        if not telegram_target:
            return
        target = _resolve_target(telegram_target)
        message = f"Hermes error: {_truncate(error, 300)}"
        self._send(target, message, job_id, "error")

    def _send(self, target: str, message: str, job_id: str, kind: str):
        """Send a single message with dedup protection."""
        flag = f"{job_id}:{kind}"
        if getattr(self._sent, "events", None) is None:
            self._sent.events = set()
        if flag in self._sent.events:
            return
        self._sent.events.add(flag)

        try:
            result = send_telegram_message(target, message)
            if result.get("ok"):
                logger.debug("Telegram %s sent for job %s", kind, job_id)
            else:
                logger.warning("Telegram %s failed for job %s: %s", kind, job_id, result.get("error"))
        except Exception as e:
            logger.warning("Telegram %s exception for job %s: %s", kind, job_id, e)
