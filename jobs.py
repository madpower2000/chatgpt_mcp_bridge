"""
Persistent JobStore backed by SQLite.

Stores job state (status, prompt, response, error, model, session_id,
timestamps, iterations, telegram_target) independently of SessionDB.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chatgpt_mcp_bridge.jobs")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_DB = os.path.join(
    os.path.expanduser("~"), ".hermes", "chatgpt_mcp_bridge", "jobs.sqlite"
)


@dataclass
class JobRecord:
    """In-memory representation of a stored job."""
    job_id: str
    status: str = "queued"          # queued | running | done | error | cancelled
    prompt: str = ""
    response: str = ""
    error: str = ""
    model: str = ""
    max_iterations: int = 0
    tools: str = "[]"               # JSON-encoded list
    context: str = ""
    rules: str = ""
    system_prompt: str = ""
    session_id: str = ""
    telegram_target: str = ""
    mirror_to_telegram: bool = False
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    iterations: int = 0
    heartbeat_at: float = 0.0     # last heartbeat (for liveness)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JobRecord":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "JobRecord":
        return cls(**{
            "job_id": row["job_id"],
            "status": row["status"],
            "prompt": row.get("prompt", ""),
            "response": row.get("response", ""),
            "error": row.get("error", ""),
            "model": row.get("model", ""),
            "max_iterations": row.get("max_iterations", 0),
            "tools": row.get("tools", "[]"),
            "context": row.get("context", ""),
            "rules": row.get("rules", ""),
            "system_prompt": row.get("system_prompt", ""),
            "session_id": row.get("session_id", ""),
            "telegram_target": row.get("telegram_target", ""),
            "mirror_to_telegram": bool(row.get("mirror_to_telegram", 0)),
            "created_at": float(row.get("created_at", 0)),
            "started_at": float(row.get("started_at", 0)),
            "completed_at": float(row.get("completed_at", 0)),
            "iterations": int(row.get("iterations", 0)),
            "heartbeat_at": float(row.get("heartbeat_at", 0)),
        })


# ---------------------------------------------------------------------------
# JobStore
# ---------------------------------------------------------------------------

class JobStore:
    """Thread-safe SQLite-backed job store."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _DEFAULT_DB
        self._lock = threading.Lock()
        self._ensure_db()

    # -- lifecycle ---------------------------------------------------------

    def _ensure_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id        TEXT PRIMARY KEY,
                    status        TEXT NOT NULL DEFAULT 'queued',
                    prompt        TEXT NOT NULL DEFAULT '',
                    response      TEXT NOT NULL DEFAULT '',
                    error         TEXT NOT NULL DEFAULT '',
                    model         TEXT NOT NULL DEFAULT '',
                    max_iterations INTEGER NOT NULL DEFAULT 0,
                    tools         TEXT NOT NULL DEFAULT '[]',
                    context       TEXT NOT NULL DEFAULT '',
                    rules         TEXT NOT NULL DEFAULT '',
                    system_prompt TEXT NOT NULL DEFAULT '',
                    session_id    TEXT NOT NULL DEFAULT '',
                    telegram_target TEXT NOT NULL DEFAULT '',
                    mirror_to_telegram INTEGER NOT NULL DEFAULT 0,
                    created_at    REAL NOT NULL,
                    started_at    REAL NOT NULL DEFAULT 0,
                    completed_at  REAL NOT NULL DEFAULT 0,
                    iterations    INTEGER NOT NULL DEFAULT 0,
                    heartbeat_at  REAL NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # -- CRUD --------------------------------------------------------------

    def create_job(
        self,
        prompt: str,
        model: str = "",
        max_iterations: int = 0,
        tools: List[str] = None,
        context: str = "",
        rules: str = "",
        system_prompt: str = "",
        telegram_target: str = "",
        mirror_to_telegram: bool = False,
    ) -> JobRecord:
        job_id = uuid.uuid4().hex[:16]
        now = time.time()
        record = JobRecord(
            job_id=job_id,
            status="queued",
            prompt=prompt,
            model=model,
            max_iterations=max_iterations,
            tools=json.dumps(tools or []),
            context=context,
            rules=rules,
            system_prompt=system_prompt,
            telegram_target=telegram_target,
            mirror_to_telegram=mirror_to_telegram,
            created_at=now,
        )
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT INTO jobs (
                        job_id, status, prompt, model, max_iterations,
                        tools, context, rules, system_prompt,
                        telegram_target, mirror_to_telegram, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.job_id, record.status, record.prompt,
                        record.model, record.max_iterations,
                        record.tools, record.context, record.rules,
                        record.system_prompt, record.telegram_target,
                        1 if record.mirror_to_telegram else 0,
                        record.created_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return record

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    return None
                return JobRecord.from_row(dict(row))
            finally:
                conn.close()

    def update_job(self, job_id: str, **fields):
        if not fields:
            return
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                sets = []
                vals = []
                for k, v in fields.items():
                    sets.append(f"{k} = ?")
                    vals.append(v)
                vals.append(job_id)
                conn.execute(
                    f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?",
                    vals,
                )
                conn.commit()
            finally:
                conn.close()

    def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[JobRecord]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.row_factory = sqlite3.Row
                query = "SELECT * FROM jobs"
                params = []
                if status:
                    query += " WHERE status = ?"
                    params.append(status)
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                return [JobRecord.from_row(dict(r)) for r in rows]
            finally:
                conn.close()

    def delete_job(self, job_id: str):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                conn.commit()
            finally:
                conn.close()
