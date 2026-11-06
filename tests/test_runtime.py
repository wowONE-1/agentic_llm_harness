import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from baf.runner import run_task
from runtime.client import Client
from runtime.gateway import Gateway
from runtime.harness import context, run
from runtime.http import APIError, request, server
from runtime.sandbox import Sandbox


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.box = Sandbox(Path(self.tmp.name) / "work")
        self.box.dispatch("POST", "/v0/provision", {"session_id": "test"})
        self.counter = 0

    def execute(self, tool, arguments, **fields):
        self.counter += 1
        return self.box.dispatch("POST", "/v0/exec", dict(
            session_id="test", operation_id=str(self.counter), tool=tool,
            arguments=arguments, **fields))

    def test_paths_and_symlink(self):
        (self.box.root / "escape").symlink_to(self.tmp.name)
        for path in ["../outside", "/etc/passwd", "escape/outside", ".git/config", ".runtime/key"]:
            with self.subTest(path=path):
                result = self.execute("WriteFile", {"path": path, "content": "bad"})
                self.assertEqual(result["status"], "sandbox_error")

    def test_result_exit_timeout_output_cap_and_secret_env(self):
        result = self.execute("RunCommand", {"command": "echo OK; exit 1"})
        self.assertEqual(result["status"], "command_error")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["type"], "result")
        self.assertEqual(self.execute("RunCommand", {"command": "sleep 5"}, timeout=.1)["status"], "timeout")
        with patch.dict(os.environ, {"CANARY_SECRET": "never-send-this"}):
            result = self.execute("RunCommand", {"command": "env"})
        self.assertNotIn("never-send-this", result["output"])
        result = self.execute("RunCommand", {"command": "head -c 40000 /dev/zero"})
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["output"]), 32768)

    def test_idempotency_and_conflict(self):
        data = dict(session_id="test", operation_id="stable", tool="RunCommand",
                    arguments={"command": "echo line >> counter"})
        first = self.box.dispatch("POST", "/v0/exec", data)
        self.assertEqual(first, self.box.dispatch("POST", "/v0/exec", data))
        self.assertEqual((self.box.root / "counter").read_text(), "line\n")
        with self.assertRaises(APIError):
            self.box.dispatch("POST", "/v0/exec", dict(data, timeout=2))

    def test_cancel_background_descendants_and_release(self):
        results = []
        worker = threading.Thread(target=lambda: results.append(self.execute(
            "RunCommand", {"command": "(sleep 0.5; echo leaked > survived) & wait"})))
        worker.start()
        deadline = time.monotonic() + 2
        while self.box.process is None and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(self.box.process)
        self.box.dispatch("POST", "/v0/cancel", {"session_id": "test"})
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["status"], "cancelled")
        time.sleep(.6)
        self.assertFalse((self.box.root / "survived").exists())
        self.box.dispatch("POST", "/v0/release", {"session_id": "test"})
        self.assertEqual(list(self.box.root.iterdir()), [])
        self.assertIsNone(self.box.process)


class HarnessTests(unittest.TestCase):
    def test_whole_turn_memory_and_zero_memory(self):
        turns = [[{"role": "user", "content": str(i)}, {"role": "assistant", "content": "ok"}]
                 for i in range(4)]
        with patch.dict(os.environ, {"MEMORY_TURNS": "2"}):
            messages = context(turns, "new")
            self.assertEqual([m["content"] for m in messages[1:]], ["2", "ok", "3", "ok", "new"])
        with patch.dict(os.environ, {"MEMORY_TURNS": "0"}):
            self.assertEqual(len(context(turns, "new")), 2)

    def test_step_limit_and_bad_tool(self):
        response = ({"role": "assistant", "tool_calls": [{"id": "1", "function": {
            "name": "bad", "arguments": "{}"}}]}, {})
        events = []
        with patch("runtime.harness.complete", return_value=response), patch.dict(os.environ, {"MAX_STEPS": "2"}):
            status, _ = run([], "x", lambda *a: self.fail("must not execute"),
                            lambda kind, **data: events.append((kind, data)), threading.Event())
        self.assertEqual(status, "step_limit")
        self.assertEqual([d["result"]["status"] for k, d in events if k == "tool_result"],
                         ["tool_error", "tool_error"])


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LLM_BACKEND": "mock", "MAX_STEPS": "8", "MEMORY_TURNS": "2"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.box = Sandbox(Path(self.tmp.name) / "work")
        sandbox_url = self.start_server(self.box.dispatch, "sandbox-test-token")
        self.gateway = Gateway(lambda op, data: request(sandbox_url, "/v0/" + op, data, "sandbox-test-token"),
                               Path(self.tmp.name) / "traces")
        self.url = self.start_server(self.gateway.dispatch, "test-token")
        self.client = Client(self.url, "test-token", "test")

    def start_server(self, dispatch, token):
        httpd = server(("127.0.0.1", 0), dispatch, token)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def close():
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)
        self.addCleanup(close)
        return f"http://127.0.0.1:{httpd.server_port}"

    def test_auth_session_conflict_reconnect_and_single_result(self):
        with self.assertRaises(HTTPError) as caught:
            Client(self.url, "wrong", "test").connect()
        self.assertEqual(caught.exception.code, 401)
        caught.exception.close()
        self.client.connect()
        with self.assertRaises(HTTPError) as caught:
            Client(self.url, "test-token", "second").connect()
        self.assertEqual(caught.exception.code, 409)
        caught.exception.close()
        result = self.client.call("message", request_id="stable", prompt="hello")
        self.assertEqual(result, self.client.call("message", request_id="stable", prompt="hello"))
        reconnect = Client(self.url, "test-token", "test")
        reconnect.connect()
        final = reconnect.watch(result["run_id"], emit=lambda _: None)
        events = reconnect.call("events", after=0)["events"]
        self.assertEqual(sum(e["type"] == "result" for e in events), 1)
        self.assertEqual(reconnect.call("events", after=final["seq"])["events"], [])
        trace = Path(self.tmp.name, "traces", "test.jsonl").read_text()
        self.assertIn('"trace_id": "test"', trace)
        self.assertNotIn("test-token", trace)

    def test_cli_connect_chat_events_release(self):
        base = [sys.executable, "-m", "runtime.client", "--url", self.url,
                "--token", "test-token", "--session", "cli"]
        for args in [["connect"], ["chat", "hello"], ["events"], ["cancel"], ["release"]]:
            result = subprocess.run(base + args, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip())

    def test_upstream_http_error_is_visible_without_secret(self):
        self.client.connect()
        error = HTTPError("https://provider.invalid", 429, "private-provider-detail", {}, None)
        with patch("runtime.harness.complete", side_effect=error):
            run_id = self.client.call("message", request_id="rate-limit", prompt="hello")["run_id"]
            final = self.client.watch(run_id, emit=lambda _: None)
        self.assertEqual(final["status"], "transport_error")
        self.assertIn("429", final["answer"])
        self.assertNotIn("private-provider-detail", final["answer"])

    def test_b1_to_b5_scripted_smoke_over_http(self):
        for directory in sorted(Path("baf").glob("B*/*")):
            with self.subTest(task=directory.name):
                result = run_task(self.client, directory, "smoke")
                self.assertTrue(result["passed"], result)


if __name__ == "__main__":
    unittest.main()
