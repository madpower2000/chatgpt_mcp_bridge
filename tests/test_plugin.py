#!/usr/bin/env python3
"""Comprehensive test suite for chatgpt_mcp_bridge plugin.

Tests cover: JobStore CRUD, TelegramMirror, agent lifecycle,
service validation, unit generation, and real agent execution.
"""

import sys
import os
import json
import time
import threading
import tempfile
import importlib

sys.path.insert(0, "/home/max/.hermes/plugins")

# Force fresh import by invalidating pycache
import chatgpt_mcp_bridge.services
importlib.reload(chatgpt_mcp_bridge.services)
import chatgpt_mcp_bridge.tools
importlib.reload(chatgpt_mcp_bridge.tools)
import chatgpt_mcp_bridge.jobs
importlib.reload(chatgpt_mcp_bridge.jobs)
import chatgpt_mcp_bridge.telegram_mirror
importlib.reload(chatgpt_mcp_bridge.telegram_mirror)
import chatgpt_mcp_bridge.schemas
importlib.reload(chatgpt_mcp_bridge.schemas)

import unittest
from unittest.mock import patch, MagicMock

from chatgpt_mcp_bridge.jobs import JobStore, JobRecord
from chatgpt_mcp_bridge.telegram_mirror import (
    TelegramMirror, send_telegram_message, _resolve_target, _truncate,
)
from chatgpt_mcp_bridge import tools
from chatgpt_mcp_bridge.services import (
    _validate_host, _validate_port, _validate_tunnel_mode,
    _validate_named_tunnel, _validate_path, _render_bridge_unit,
    _render_cloudflared_unit, install_services,
)
from chatgpt_mcp_bridge.schemas import AgentStartInput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_store():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    store = JobStore(db_path=tmp.name)
    store._tmp_path = tmp.name
    return store


def _cleanup_store(store):
    if hasattr(store, "_tmp_path") and os.path.exists(store._tmp_path):
        os.unlink(store._tmp_path)


# ---------------------------------------------------------------------------
# JobStore tests
# ---------------------------------------------------------------------------

class TestJobStore(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()

    def tearDown(self):
        _cleanup_store(self.store)

    def test_create_job_returns_record(self):
        rec = self.store.create_job(
            prompt="hello", model="gpt-4", max_iterations=10,
            tools=["web"], context="", rules="", system_prompt="",
            telegram_target="", mirror_to_telegram=False,
        )
        self.assertIsNotNone(rec.job_id)
        self.assertEqual(rec.status, "queued")
        self.assertEqual(rec.prompt, "hello")
        self.assertEqual(rec.model, "gpt-4")
        self.assertEqual(rec.max_iterations, 10)
        # tools stored as JSON string
        parsed = json.loads(rec.tools)
        self.assertEqual(parsed, ["web"])

    def test_get_job(self):
        rec = self.store.create_job(prompt="test")
        found = self.store.get_job(rec.job_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.prompt, "test")

    def test_get_job_missing(self):
        self.assertIsNone(self.store.get_job("nonexistent"))

    def test_update_job(self):
        rec = self.store.create_job(prompt="test")
        self.store.update_job(rec.job_id, status="done", response="ok")
        found = self.store.get_job(rec.job_id)
        self.assertEqual(found.status, "done")
        self.assertEqual(found.response, "ok")

    def test_list_jobs(self):
        self.store.create_job(prompt="a")
        self.store.create_job(prompt="b")
        self.store.create_job(prompt="c")
        jobs = self.store.list_jobs(limit=10)
        self.assertEqual(len(jobs), 3)

    def test_list_jobs_limit(self):
        for i in range(10):
            self.store.create_job(prompt=f"n{i}")
        jobs = self.store.list_jobs(limit=5)
        self.assertEqual(len(jobs), 5)

    def test_create_job_empty_tools(self):
        rec = self.store.create_job(prompt="x", tools=[])
        self.assertEqual(rec.tools, "[]")

    def test_create_job_mirroring_fields(self):
        rec = self.store.create_job(
            prompt="x",
            telegram_target="telegram:12345",
            mirror_to_telegram=True,
        )
        self.assertTrue(rec.mirror_to_telegram)
        self.assertEqual(rec.telegram_target, "telegram:12345")


# ---------------------------------------------------------------------------
# TelegramMirror tests
# ---------------------------------------------------------------------------

class TestTelegramMirror(unittest.TestCase):

    def test_init_requires_store(self):
        mirror = TelegramMirror(store=None, default_target="")
        self.assertIsNone(mirror._store)

    def test_notify_start_no_target_skips(self):
        store = _fresh_store()
        try:
            mirror = TelegramMirror(store=store, default_target="")
            with patch.object(mirror, "_send") as mock_send:
                mirror.notify_start("job-1", "test prompt", "")
                mock_send.assert_not_called()
        finally:
            _cleanup_store(store)

    def test_notify_start_sends(self):
        store = _fresh_store()
        try:
            mirror = TelegramMirror(store=store, default_target="")
            with patch.object(mirror, "_send") as mock_send:
                mirror.notify_start("job-1", "test prompt", "telegram:555")
                mock_send.assert_called_once()
                args = mock_send.call_args[0]
                self.assertIn("ChatGPT", args[1])  # message
        finally:
            _cleanup_store(store)

    def test_notify_complete(self):
        store = _fresh_store()
        try:
            mirror = TelegramMirror(store=store, default_target="")
            with patch.object(mirror, "_send") as mock_send:
                mirror.notify_complete("job-1", "result here", "telegram:555")
                mock_send.assert_called_once()
        finally:
            _cleanup_store(store)

    def test_notify_error(self):
        store = _fresh_store()
        try:
            mirror = TelegramMirror(store=store, default_target="")
            with patch.object(mirror, "_send") as mock_send:
                mirror.notify_error("job-1", "something broke", "telegram:555")
                mock_send.assert_called_once()
        finally:
            _cleanup_store(store)

    def test_notify_cancelled(self):
        store = _fresh_store()
        try:
            rec = store.create_job(prompt="x", telegram_target="telegram:999")
            mirror = TelegramMirror(store=store, default_target="")
            with patch.object(mirror, "_send") as mock_send:
                mirror.notify_cancelled(rec.job_id)
                mock_send.assert_called_once()
        finally:
            _cleanup_store(store)

    def test_resolve_target_telegram_prefix(self):
        self.assertEqual(_resolve_target("telegram:123"), "telegram:123")

    def test_resolve_target_bare_digits(self):
        self.assertEqual(_resolve_target("12345"), "telegram:12345")

    def test_resolve_target_telegram_only(self):
        self.assertEqual(_resolve_target("telegram"), "telegram")

    def test_truncate(self):
        self.assertEqual(len(_truncate("a" * 300, 200)), 200)
        self.assertEqual(_truncate("short", 200), "short")


# ---------------------------------------------------------------------------
# Agent lifecycle tests
# ---------------------------------------------------------------------------

class TestAgentStart(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        tools._store = self.store
        tools._mirror = TelegramMirror(store=self.store, default_target="")
        tools._cancel_events = {}

    def tearDown(self):
        # Cancel any running background jobs to prevent thread bleed
        for jid, evt in list(tools._cancel_events.items()):
            evt.set()
        tools._store = None
        tools._mirror = None
        tools._cancel_events = {}
        time.sleep(0.1)  # let threads drain
        _cleanup_store(self.store)

    def test_returns_job_id(self):
        result = tools.chatgpt_agent_start(prompt="test job")
        data = json.loads(result)
        self.assertIn("job_id", data)
        self.assertEqual(data["status"], "queued")

    def test_no_store_raises_error(self):
        tools._store = None
        result = tools.chatgpt_agent_start(prompt="x")
        data = json.loads(result)
        self.assertIn("error", data)

    def test_mirroring_does_not_block(self):
        start = time.time()
        result = tools.chatgpt_agent_start(
            prompt="fast test", mirror_to_telegram=True, telegram_target="telegram:555"
        )
        elapsed = time.time() - start
        self.assertLess(elapsed, 1.0, f"Start took {elapsed:.2f}s — should be < 1s")

    def test_invalid_tools_json_handled(self):
        result = tools.chatgpt_agent_start(
            prompt="test", tools='["web", invalid]'
        )
        data = json.loads(result)
        self.assertIn("job_id", data)

    def test_telegram_failure_does_not_block(self):
        tools._mirror = MagicMock()
        tools._mirror.notify_start.side_effect = Exception("network error")
        result = tools.chatgpt_agent_start(
            prompt="resilient", mirror_to_telegram=True, telegram_target="telegram:555"
        )
        data = json.loads(result)
        self.assertIn("job_id", data)


class TestAgentStatus(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        tools._store = self.store
        tools._cancel_events = {}

    def tearDown(self):
        for jid, evt in list(tools._cancel_events.items()):
            evt.set()
        tools._store = None
        tools._cancel_events = {}
        time.sleep(0.1)
        _cleanup_store(self.store)

    def test_status_found(self):
        rec = self.store.create_job(prompt="test")
        result = tools.chatgpt_agent_status(rec.job_id)
        data = json.loads(result)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["job_id"], rec.job_id)

    def test_status_missing(self):
        result = tools.chatgpt_agent_status("nonexistent")
        data = json.loads(result)
        self.assertIn("error", data)


class TestAgentResult(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        tools._store = self.store
        tools._cancel_events = {}

    def tearDown(self):
        for jid, evt in list(tools._cancel_events.items()):
            evt.set()
        tools._store = None
        tools._cancel_events = {}
        time.sleep(0.1)
        _cleanup_store(self.store)

    def test_result_done(self):
        rec = self.store.create_job(prompt="test")
        self.store.update_job(rec.job_id, status="done", response="answer!")
        result = tools.chatgpt_agent_result(rec.job_id)
        data = json.loads(result)
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["response"], "answer!")

    def test_result_error(self):
        rec = self.store.create_job(prompt="test")
        self.store.update_job(rec.job_id, status="error", error="boom")
        result = tools.chatgpt_agent_result(rec.job_id)
        data = json.loads(result)
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "boom")


class TestAgentCancel(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        tools._store = self.store
        tools._cancel_events = {}

    def tearDown(self):
        for jid, evt in list(tools._cancel_events.items()):
            evt.set()
        tools._store = None
        tools._cancel_events = {}
        time.sleep(0.1)
        _cleanup_store(self.store)

    def test_cancel_running(self):
        rec = self.store.create_job(prompt="test")
        self.store.update_job(rec.job_id, status="running")
        evt = threading.Event()
        tools._cancel_events[rec.job_id] = evt
        result = tools.chatgpt_agent_cancel(rec.job_id)
        data = json.loads(result)
        self.assertTrue(data["cancelled"])
        self.assertTrue(evt.is_set())

    def test_cancel_done(self):
        rec = self.store.create_job(prompt="test")
        self.store.update_job(rec.job_id, status="done")
        result = tools.chatgpt_agent_cancel(rec.job_id)
        data = json.loads(result)
        self.assertFalse(data["cancelled"])

    def test_cancel_missing(self):
        result = tools.chatgpt_agent_cancel("nonexistent")
        data = json.loads(result)
        self.assertIn("error", data)


# ---------------------------------------------------------------------------
# Service validation tests
# ---------------------------------------------------------------------------

class TestServicesValidation(unittest.TestCase):

    def test_valid_host(self):
        self.assertIsNone(_validate_host("127.0.0.1"))
        self.assertIsNone(_validate_host("localhost"))

    def test_invalid_host(self):
        self.assertIsNotNone(_validate_host("8.8.8.8"))
        self.assertIsNotNone(_validate_host(""))

    def test_valid_port(self):
        self.assertIsNone(_validate_port(9100))

    def test_invalid_port(self):
        self.assertIsNotNone(_validate_port(80))
        self.assertIsNotNone(_validate_port(70000))

    def test_tunnel_modes(self):
        self.assertIsNone(_validate_tunnel_mode("quick"))
        self.assertIsNone(_validate_tunnel_mode("named"))
        self.assertIsNotNone(_validate_tunnel_mode("invalid"))

    def test_named_tunnel_required(self):
        self.assertIsNotNone(_validate_named_tunnel("", "named"))
        self.assertIsNone(_validate_named_tunnel("my-tunnel", "named"))
        self.assertIsNone(_validate_named_tunnel("", "quick"))

    def test_path_validation(self):
        self.assertIsNotNone(_validate_path("relative/path", must_exist=True))
        self.assertIsNone(_validate_path("/tmp", must_exist=True, must_be_dir=True))


class TestServiceUnitGeneration(unittest.TestCase):

    def _config(self, **overrides):
        base = {
            "host": "127.0.0.1", "port": 9100,
            "python_path": "/usr/bin/python3",
            "cloudflared_path": "/usr/bin/cloudflared",
            "working_dir": "/home/max",
            "tunnel_mode": "quick",
            "named_tunnel": "",
        }
        base.update(overrides)
        return base

    def test_bridge_unit_pythonpath(self):
        """PYTHONPATH must NOT include python binary path."""
        unit = _render_bridge_unit(self._config())
        line = [l for l in unit.splitlines() if "PYTHONPATH=" in l][0]
        value = line.split("=", 1)[1]
        self.assertNotIn("python3", value)
        self.assertIn("/home/max/.hermes/plugins", value)

    def test_bridge_unit_exec_start(self):
        unit = _render_bridge_unit(self._config())
        self.assertIn("-m chatgpt_mcp_bridge", unit)
        self.assertIn("--host 127.0.0.1", unit)
        self.assertIn("--port 9100", unit)

    def test_cloudflared_quick_mode(self):
        unit = _render_cloudflared_unit(self._config(tunnel_mode="quick"))
        self.assertIn("--url http://127.0.0.1:9100", unit)

    def test_cloudflared_named_mode(self):
        unit = _render_cloudflared_unit(
            self._config(tunnel_mode="named", named_tunnel="my-tunnel")
        )
        self.assertIn("tunnel run my-tunnel", unit)

    def test_install_services_dry_run_quick(self):
        result = install_services(dry_run=True, tunnel_mode="quick", port=9100)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertIn("bridge_unit", result)
        self.assertIn("cloudflared_unit", result)

    def test_install_services_dry_run_named(self):
        result = install_services(
            dry_run=True, tunnel_mode="named", named_tunnel="my-tunnel", port=9100
        )
        self.assertTrue(result["ok"])
        self.assertIn("tunnel run my-tunnel", result["cloudflared_unit"])

    def test_install_services_invalid_port(self):
        result = install_services(dry_run=True, port=80)
        self.assertFalse(result["ok"])
        self.assertTrue(len(result["errors"]) > 0)

    def test_install_services_invalid_host(self):
        result = install_services(dry_run=True, host="8.8.8.8")
        self.assertFalse(result["ok"])
        self.assertTrue(len(result["errors"]) > 0)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestSchemas(unittest.TestCase):

    def test_valid_params(self):
        params = AgentStartInput(
            prompt="test", model="gpt-4", max_iterations=10,
            tools='["web"]', context="", rules="", system_prompt="",
            mirror_to_telegram=False, telegram_target="",
        )
        self.assertEqual(params.prompt, "test")

    def test_default_values(self):
        params = AgentStartInput(prompt="x")
        self.assertEqual(params.max_iterations, 50)
        self.assertEqual(params.tools, "[]")
        self.assertFalse(params.mirror_to_telegram)


# ---------------------------------------------------------------------------
# Real agent execution (optional, skipped if hermes not available)
# ---------------------------------------------------------------------------

class TestRealAgentExecution(unittest.TestCase):

    def setUp(self):
        self.store = _fresh_store()
        tools._store = self.store
        tools._mirror = None
        tools._cancel_events = {}

    def tearDown(self):
        _cleanup_store(self.store)
        tools._store = None
        tools._cancel_events = {}

    @unittest.skipIf(
        os.system("which hermes > /dev/null 2>&1") != 0,
        "hermes CLI not found",
    )
    def test_full_lifecycle(self):
        """Start -> status -> wait -> result."""
        result = tools.chatgpt_agent_start(
            prompt="echo hello", model="", max_iterations=1, tools="[]"
        )
        data = json.loads(result)
        job_id = data["job_id"]
        self.assertEqual(data["status"], "queued")

        # Wait for completion (poll up to 30s)
        for _ in range(60):
            status = json.loads(tools.chatgpt_agent_status(job_id))
            if status["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.5)

        status = json.loads(tools.chatgpt_agent_status(job_id))
        self.assertIn(status["status"], ("done", "error", "cancelled"))

        res = json.loads(tools.chatgpt_agent_result(job_id))
        self.assertEqual(res["job_id"], job_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
