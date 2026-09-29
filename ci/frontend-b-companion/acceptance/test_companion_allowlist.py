#!/usr/bin/env python3
"""Offline companion tool allowlist acceptance using local synthetic services only.

The runner first makes a real synthetic Responses turn through the candidate
app-server as a loopback connectivity probe. The full new/multi-turn/resume
acceptance runs only if that probe succeeds. Provider capture stores schemas and
input media types only, never prompt text or credentials.
"""
from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import unittest
import zlib
import struct


HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("COMPANION_ROOT", HERE.parents[3]))
SOURCE = Path(os.environ.get("CODEX_SOURCE", ROOT / "codex"))
APP_SERVER = Path(os.environ.get("COMPANION_APP_SERVER", ROOT / "bin" / "codex-app-server"))
MOCK_MCP = HERE / "mock_memory_search_mcp.py"
CAPTURED: list[dict] = []
CAPTURE_LOCK = threading.Lock()
RESPONSE_NUMBER = 0

MEMORY_DESCRIPTION = (
    "Read-only private long-term-memory lookup. Use only when the current "
    "conversation indicates a specific past detail would materially help. "
    "Formulate the query from the full current conversation, not only the "
    "latest wording. At most one call is permitted per user turn. Never "
    "claim a memory exists unless this tool returns it."
)
# Captured from stock Codex 0.146.0 after its native MCP-to-Responses schema
# conversion. The raw MCP minLength/maxLength bounds are not represented by
# codex_tools' JsonSchema and are an existing upstream behavior.
STOCK_PROVIDER_PARAMETERS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}
FORBIDDEN_EXACT = {
    "tool_search", "exec", "exec_command", "wait", "write_stdin", "shell_command",
    "apply_patch", "terminal", "pty", "read_file", "write_file", "edit_file",
    "patch", "code_mode", "breath", "breath_advanced", "dream", "pulse",
}
FORBIDDEN_PARTS = (
    "terminal", "pty", "filesystem", "file_system", "read_file", "write_file",
    "edit_file", "patch", "code_mode", "computer", "shell", "exec",
)
PATCH_PATHS = {
    "codex-rs/app-server/Cargo.toml",
    "codex-rs/core/Cargo.toml",
    "codex-rs/core/src/tools/handlers/mcp.rs",
    "codex-rs/core/src/tools/registry.rs",
    "codex-rs/core/src/tools/spec_plan.rs",
    "codex-rs/core/src/tools/spec_plan_tests.rs",
}


def safe_text(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
    return re.sub(
        r"(?i)(api[_-]?key|token|secret)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )


def input_types(value):
    result = set()

    def visit(node):
        if isinstance(node, dict):
            if isinstance(node.get("type"), str):
                result.add(node["type"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return sorted(result)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    content = kind + payload
    return struct.pack(">I", len(payload)) + content + struct.pack(">I", zlib.crc32(content) & 0xFFFFFFFF)


def write_valid_test_png(path: Path):
    # One opaque white RGBA pixel, encoded with valid CRCs (no third-party library).
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\xff\xff\xff")
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", pixels)
        + png_chunk(b"IEND", b"")
    )


class ProviderHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def do_POST(self):
        global RESPONSE_NUMBER
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            request = json.loads(raw)
        except Exception:
            self.send_error(400)
            return
        with CAPTURE_LOCK:
            CAPTURED.append({
                "tools": request.get("tools", []),
                "input_types": input_types(request.get("input")),
            })
            RESPONSE_NUMBER += 1
            sequence = RESPONSE_NUMBER

        response_id = f"resp_synthetic_{sequence}"
        message_id = f"msg_synthetic_{sequence}"
        message = {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "synthetic response", "annotations": []}],
        }
        response = {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": "gpt-5.5",
            "output": [message],
            "parallel_tool_calls": False,
            "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
        }
        frames = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0,
             "item": {**message, "status": "in_progress", "content": []}},
            {"type": "response.content_part.added", "item_id": message_id, "output_index": 0,
             "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
            {"type": "response.output_text.delta", "item_id": message_id, "output_index": 0,
             "content_index": 0, "delta": "synthetic response"},
            {"type": "response.output_text.done", "item_id": message_id, "output_index": 0,
             "content_index": 0, "text": "synthetic response"},
            {"type": "response.content_part.done", "item_id": message_id, "output_index": 0,
             "content_index": 0,
             "part": {"type": "output_text", "text": "synthetic response", "annotations": []}},
            {"type": "response.output_item.done", "output_index": 0, "item": message},
            {"type": "response.completed", "response": response},
        ]
        body = b"".join(
            b"event: " + frame["type"].encode() + b"\ndata: "
            + json.dumps(frame, separators=(",", ":")).encode() + b"\n\n"
            for frame in frames
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def config_for(home: Path, base_url: str):
    home.mkdir(parents=True, exist_ok=True)
    python = os.environ.get("PYTHON", "python3")
    text = f'''model = "gpt-5.5"
model_provider = "synthetic"
approval_policy = "never"
sandbox_mode = "read-only"

[model_providers.synthetic]
name = "synthetic-local"
base_url = {json.dumps(base_url)}
wire_api = "responses"
env_key = "SYNTHETIC_ONLY_KEY"
requires_openai_auth = false

[mcp_servers.memory_search]
command = {json.dumps(python)}
args = [{json.dumps(str(MOCK_MCP))}]
enabled_tools = ["memory_search"]

[features]
shell_tool = true
unified_exec = true
code_mode = true
code_mode_host = true
code_mode_only = false
'''
    (home / "config.toml").write_text(text, encoding="utf-8")
    return text


class AppServer:
    def __init__(self, home: Path):
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        env["SYNTHETIC_ONLY_KEY"] = "local-test-only"
        env.pop("OPENAI_API_KEY", None)
        self.removed_proxy_env_names = sorted(k for k in env if "proxy" in k.lower())
        # Local-only provider must never be diverted by inherited proxy variables.
        for key in self.removed_proxy_env_names:
            env.pop(key, None)
        self.inherited_proxy_names = sorted(k for k in env if "proxy" in k.lower())
        self.proc = subprocess.Popen(
            [
                str(APP_SERVER), "--listen", "stdio://", "--strict-config",
                "-c", "analytics.enabled=false", "-c", "features.plugins=false",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        self.sequence = 0
        self.notifications = []
        self.stderr_lines = []
        self._condition = threading.Condition()
        self._responses = {}
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        self.request("initialize", {"clientInfo": {"name": "frontend-b-allowlist-test", "version": "1"}})
        self.notify("initialized", {})
        self.mcp_status = []
        self.mcp_startup = []

    def _read_stderr(self):
        assert self.proc.stderr
        for line in self.proc.stderr:
            self.stderr_lines.append(safe_text(line.rstrip()))

    def _read_stdout(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            try:
                value = json.loads(line)
            except Exception:
                continue
            with self._condition:
                if "id" in value:
                    self._responses[value["id"]] = value
                else:
                    self.notifications.append(value)
                self._condition.notify_all()

    def notify(self, method, params):
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps({"method": method, "params": params}) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params, timeout=90):
        assert self.proc.stdin
        self.sequence += 1
        request_id = self.sequence
        self.proc.stdin.write(json.dumps({"method": method, "id": request_id, "params": params}) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        with self._condition:
            while request_id not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"candidate app-server RPC timed out: {method}")
                self._condition.wait(remaining)
            result = self._responses.pop(request_id)
        if "error" in result:
            raise RuntimeError(f"candidate app-server RPC {method} returned error: {safe_text(str(result['error']))}")
        return result.get("result", {})

    def wait_mcp_ready(self, thread_id, expected_name="memory_search", timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            seen = [
                item for item in self.notifications
                if item.get("method") == "mcpServer/startupStatus/updated"
            ]
            self.mcp_startup = seen
            matching = [
                item.get("params", {}) for item in seen
                if item.get("params", {}).get("threadId") == thread_id
                and item.get("params", {}).get("name") == expected_name
            ]
            if any(item.get("status") in {"failed", "cancelled"} for item in matching):
                raise AssertionError(f"synthetic MCP failed startup: {self.safe_diagnostics()}")

            # In 0.146.0 mcpServerStatus/list has no startup-state field. Pair
            # the per-thread Ready notification with its read-only final tool
            # inventory; require the original memory_search MCP tool to exist.
            status = self.request("mcpServerStatus/list", {
                "threadId": thread_id,
                "detail": "toolsAndAuthOnly",
            })
            servers = status.get("data", [])
            server = next((item for item in servers if item.get("name") == expected_name), None)
            tools = server.get("tools", {}) if isinstance(server, dict) else {}
            self.mcp_status = sorted((str(name), "present") for name in tools)
            notification_ready = any(item.get("status") == "ready" for item in matching)
            inventory_ready = isinstance(tools, dict) and "memory_search" in tools
            if notification_ready and inventory_ready:
                return
            time.sleep(0.05)
        raise AssertionError(f"synthetic MCP readiness/inventory gate did not pass: {self.safe_diagnostics()}")

    def wait_turn(self, turn_id, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for item in self.notifications:
                if item.get("method") == "turn/completed":
                    params = item.get("params", {})
                    observed = str(params.get("turnId", "") or params.get("turn", {}).get("id", ""))
                    if observed == str(turn_id):
                        return
                if item.get("method") == "error":
                    params = item.get("params", {})
                    if str(params.get("turnId", "")) == str(turn_id):
                        error = params.get("error", {})
                        raise AssertionError(
                            "candidate turn emitted error: "
                            + safe_text(json.dumps(error, ensure_ascii=False))
                        )
            time.sleep(0.05)
        raise TimeoutError(f"candidate turn did not complete: {self.safe_diagnostics()}")

    def safe_diagnostics(self):
        events = []
        for item in self.notifications:
            method = item.get("method")
            params = item.get("params", {})
            entry = {"method": method}
            for field in ("threadId", "turnId", "status", "name", "error", "failureReason"):
                if field in params:
                    entry[field] = params[field]
            events.append(entry)
        return safe_text(json.dumps({
            "mcp_status": self.mcp_status,
            "removed_proxy_env_names": self.removed_proxy_env_names,
            "inherited_proxy_env_names": self.inherited_proxy_names,
            "events": events,
            "stderr": self.stderr_lines[-12:],
        }, ensure_ascii=False))

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)


def send_turn(app: AppServer, thread_id: str, image_path: Path | None = None):
    inputs = [{"type": "text", "text": "synthetic harmless input"}]
    if image_path:
        inputs.append({"type": "localImage", "path": str(image_path)})
    result = app.request("turn/start", {"threadId": thread_id, "input": inputs})
    turn_id = result.get("turn", {}).get("id")
    if not turn_id:
        raise AssertionError("turn/start response omitted turn id")
    app.wait_turn(turn_id)


def leaf_tools(specs):
    leaves = []
    for spec in specs:
        if isinstance(spec.get("tools"), list):
            namespace = str(spec.get("name", ""))
            leaves.extend((namespace, leaf) for leaf in spec["tools"])
        else:
            leaves.append(("", spec))
    return leaves


def forbidden_tools(specs):
    names = [str(leaf.get("name", "")).lower() for _, leaf in leaf_tools(specs)]
    bad = [name for name in names if name in FORBIDDEN_EXACT or any(part in name for part in FORBIDDEN_PARTS)]
    return sorted(set(bad))


class CompanionAllowlistAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not APP_SERVER.is_file():
            raise AssertionError(f"dedicated candidate app-server missing: {APP_SERVER}")
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProviderHandler)
        cls.port = cls.httpd.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}/v1"
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.temp = tempfile.TemporaryDirectory(prefix="frontend-b-companion-", dir=HERE)
        cls.root = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.temp.cleanup()

    def test_00_candidate_appserver_loopback_connectivity_probe(self):
        home = self.root / "probe"
        config_text = config_for(home, self.base_url)
        self.assertIn(f'base_url = "{self.base_url}"', config_text)
        self.assertEqual(self.base_url, f"http://127.0.0.1:{self.port}/v1")
        app = AppServer(home)
        try:
            thread_id = app.request("thread/start", {"cwd": str(self.root), "ephemeral": False})["thread"]["id"]
            app.wait_mcp_ready(thread_id)
            with CAPTURE_LOCK:
                before = len(CAPTURED)
            send_turn(app, thread_id)
            with CAPTURE_LOCK:
                after = len(CAPTURED)
            self.assertEqual(after, before + 1, "candidate app-server did not reach the exact loopback provider")
            self.assertFalse(app.inherited_proxy_names, "candidate app-server inherited proxy environment variables")
        finally:
            app.close()

    def test_public_thread_config_removes_execution_schemas_on_start_resume_and_each_turn(self):
        with CAPTURE_LOCK:
            CAPTURED.clear()
        changed = subprocess.run(
            ["git", "diff", "--name-only"], cwd=SOURCE, check=True,
            capture_output=True, text=True,
        ).stdout.splitlines()
        self.assertEqual(set(changed), PATCH_PATHS, "source changes escaped the isolated allowlist patch")

        home = self.root / "persistent"
        config_text = config_for(home, self.base_url)
        self.assertIn(f'base_url = "{self.base_url}"', config_text)
        app = AppServer(home)
        thread_id = None
        image_path = self.root / "valid-synthetic.png"
        write_valid_test_png(image_path)
        try:
            thread_id = app.request("thread/start", {"cwd": str(self.root), "ephemeral": False})["thread"]["id"]
            app.wait_mcp_ready(thread_id)
            send_turn(app, thread_id, image_path)
            send_turn(app, thread_id)
        finally:
            app.close()

        resumed = AppServer(home)
        try:
            result = resumed.request("thread/resume", {"threadId": thread_id})
            if result.get("thread", {}).get("id"):
                self.assertEqual(result["thread"]["id"], thread_id)
            resumed.wait_mcp_ready(thread_id)
            send_turn(resumed, thread_id)
        finally:
            resumed.close()

        with CAPTURE_LOCK:
            captures = list(CAPTURED)
        self.assertEqual(len(captures), 3, "expected exactly three provider requests: two turns + one resumed turn")
        expected_namespace = None
        expected_schema = None
        for index, capture in enumerate(captures, 1):
            tools = capture["tools"]
            leaves = leaf_tools(tools)
            self.assertEqual(len(leaves), 1, f"turn {index}: final tool collection must contain one leaf: {tools}")
            namespace, memory = leaves[0]
            self.assertEqual(memory.get("name"), "memory_search")
            self.assertFalse(forbidden_tools(tools), f"turn {index}: forbidden execution tools leaked")
            self.assertNotIn("defer_loading", memory, f"turn {index}: memory_search must be Direct")
            self.assertEqual(memory.get("description"), MEMORY_DESCRIPTION)
            self.assertEqual(memory.get("parameters"), STOCK_PROVIDER_PARAMETERS)
            self.assertIn(namespace, {"", "mcp__memory_search"})
            if expected_namespace is None:
                expected_namespace = namespace
                expected_schema = memory
            else:
                self.assertEqual(namespace, expected_namespace)
                self.assertEqual(memory, expected_schema, f"turn {index}: memory_search schema changed")
        self.assertTrue(
            any(any("image" in kind.lower() for kind in capture["input_types"]) for capture in captures),
            "valid synthetic image input did not reach a provider request",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
