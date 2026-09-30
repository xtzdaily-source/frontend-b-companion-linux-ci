#!/usr/bin/env python3
"""Fail-closed synthetic acceptance for execution-disabled companion v2."""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP_SERVER = Path(os.environ["V2_APP_SERVER"])
POLICY_PATH = Path("/etc/tidal-codex-poc/companion-exec-policy.toml")
POLICY = ROOT / "ci/execution-disabled-companion-v2/policy/companion-exec-policy.toml"
POLICY_SECOND = ROOT / "ci/execution-disabled-companion-v2/policy/companion-exec-policy-second-mcp.toml"
MOCK_MCP = HERE / "mock_policy_mcp.py"

spec = importlib.util.spec_from_file_location(
    "base_acceptance", ROOT / "ci/frontend-b-companion/acceptance/test_companion_allowlist.py"
)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m)
m.APP_SERVER = APP_SERVER


def sudo(*args: str):
    subprocess.run(["sudo", *args], check=True)


def install_policy(source: Path):
    sudo("install", "-o", "root", "-g", "root", "-m", "0644", str(source), str(POLICY_PATH))
    stat = subprocess.run(
        ["stat", "-c", "%u:%g:%a", str(POLICY_PATH)],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    if stat != "0:0:644":
        raise AssertionError(f"policy ownership/mode mismatch: {stat}")


def assert_startup_rejected(label: str):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix=f"v2-{label}-") as raw:
        env["CODEX_HOME"] = raw
        proc = subprocess.Popen(
            [str(APP_SERVER), "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        try:
            return_code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise AssertionError(f"{label}: app-server became/stayed ready with invalid policy")
        if return_code == 0:
            raise AssertionError(f"{label}: invalid policy exited successfully")


def negative_startup_gates(work: Path):
    sudo("rm", "-f", str(POLICY_PATH))
    assert_startup_rejected("missing")

    bad_toml = work / "bad.toml"
    bad_toml.write_text("not valid toml = [", encoding="utf-8")
    install_policy(bad_toml)
    assert_startup_rejected("bad-toml")

    unknown = work / "unknown-version.toml"
    unknown.write_text(POLICY.read_text(encoding="utf-8").replace("version = 1", "version = 2"), encoding="utf-8")
    install_policy(unknown)
    assert_startup_rejected("unknown-version")

    install_policy(POLICY)
    sudo("chmod", "0664", str(POLICY_PATH))
    assert_startup_rejected("bad-mode")

    install_policy(POLICY)
    runner_uid = str(os.getuid())
    runner_gid = str(os.getgid())
    sudo("chown", f"{runner_uid}:{runner_gid}", str(POLICY_PATH))
    assert_startup_rejected("bad-owner")


def leaf_tools(specs):
    for spec in specs:
        if isinstance(spec.get("tools"), list):
            for tool in spec["tools"]:
                yield spec.get("name"), tool
        else:
            yield None, spec


def sse(handler, item, sequence):
    response = {
        "id": f"v2-response-{sequence}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": "gpt-5.5",
        "output": [item],
        "parallel_tool_calls": False,
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    frames = [
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    body = b"".join(
        b"event: " + frame["type"].encode() + b"\ndata: "
        + json.dumps(frame, separators=(",", ":")).encode() + b"\n\n"
        for frame in frames
    )
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Provider(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "dangerous"
    requests: list[dict] = []

    def log_message(self, *_args):
        return

    def do_POST(self):
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        type(self).requests.append(request)
        sequence = len(type(self).requests)
        mode = type(self).mode
        target = {
            "dangerous": "exec_command",
            "memory": "memory_search",
            "second": "weather",
        }[mode]
        if sequence == 1 and mode == "dangerous":
            matches = [
                (namespace, tool)
                for namespace, tool in leaf_tools(request.get("tools", []))
                if tool.get("name") == target
            ]
            if len(matches) != 1:
                raise AssertionError(f"{target} schema count is not one: {matches}")
            namespace, _tool = matches[0]
            item = {
                "type": "function_call",
                "call_id": f"v2-call-{target}",
                "name": target,
                "arguments": json.dumps({"cmd": "true"} if target == "exec_command" else {"query": "synthetic"}),
            }
            if namespace:
                item["namespace"] = namespace
        elif sequence == 1:
            tool_search = [
                tool
                for tool in request.get("tools", [])
                if tool.get("type") == "tool_search"
            ]
            if len(tool_search) != 1:
                raise AssertionError(f"tool_search schema count is not one: {tool_search}")
            if any(tool.get("name") == target for _namespace, tool in leaf_tools(request.get("tools", []))):
                raise AssertionError(f"deferred {target} was exposed directly before search")
            item = {
                "type": "tool_search_call",
                "call_id": f"v2-search-{target}",
                "execution": "client",
                "arguments": {"query": target, "limit": 8},
            }
        elif sequence == 2 and mode in {"memory", "second"}:
            search_outputs = [
                item
                for item in request.get("input", [])
                if item.get("type") == "tool_search_output"
                and item.get("call_id") == f"v2-search-{target}"
            ]
            if len(search_outputs) != 1:
                raise AssertionError(f"{target} tool_search output count is not one: {search_outputs}")
            expected_namespace = {
                "memory": "mcp__memory_search",
                "second": "mcp__second_synthetic",
            }[mode]
            matches = [
                (namespace, tool)
                for namespace, tool in leaf_tools(search_outputs[0].get("tools", []))
                if tool.get("name") == target
            ]
            if len(matches) != 1:
                raise AssertionError(f"{target} deferred schema count is not one: {matches}")
            if matches[0][0] != expected_namespace:
                raise AssertionError(
                    f"{target} deferred namespace is {matches[0][0]!r}, expected {expected_namespace!r}"
                )
            if matches[0][1].get("defer_loading") is not True:
                raise AssertionError(f"{target} schema was not marked defer_loading")
            item = {
                "type": "function_call",
                "call_id": f"v2-call-{target}",
                "namespace": expected_namespace,
                "name": target,
                "arguments": json.dumps({"query": "synthetic"}),
            }
        else:
            if mode in {"memory", "second"}:
                output = json.dumps(request.get("input", []))
                if f"synthetic:{target}:ok" not in output:
                    raise AssertionError(f"{target} result missing from provider input")
            item = {
                "id": f"v2-message-{sequence}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "synthetic complete", "annotations": []}],
            }
        sse(self, item, sequence)


def config_for(home: Path, base_url: str):
    home.mkdir(parents=True)
    python = shutil.which("python3") or "python3"
    home.joinpath("config.toml").write_text(f'''model = "gpt-5.5"
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
args = [{json.dumps(str(MOCK_MCP))}, "--tool", "memory_search"]
enabled_tools = ["memory_search"]

[mcp_servers.second_synthetic]
command = {json.dumps(python)}
args = [{json.dumps(str(MOCK_MCP))}, "--tool", "weather"]
enabled_tools = ["weather"]

[features]
shell_tool = true
unified_exec = true
code_mode = true
code_mode_host = true
code_mode_only = false
''', encoding="utf-8")


def wait_inventory(app, thread_id: str, server: str, tool: str):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = app.request("mcpServerStatus/list", {"threadId": thread_id, "detail": "toolsAndAuthOnly"})
        entry = next((item for item in result.get("data", []) if item.get("name") == server), None)
        if isinstance(entry, dict) and tool in entry.get("tools", {}):
            return
        time.sleep(0.05)
    raise AssertionError(f"MCP inventory missing {server}/{tool}")


def run_turn(mode: str, policy: Path, work: Path, with_image: bool = False):
    install_policy(policy)
    Provider.mode = mode
    Provider.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = work / f"home-{mode}"
    config_for(home, f"http://127.0.0.1:{server.server_address[1]}/v1")
    app = m.AppServer(home)
    try:
        thread_id = app.request("thread/start", {"cwd": str(work), "ephemeral": True})["thread"]["id"]
        wait_inventory(app, thread_id, "memory_search", "memory_search")
        wait_inventory(app, thread_id, "second_synthetic", "weather")
        image = None
        if with_image:
            image = work / "one-pixel.png"
            m.write_valid_test_png(image)
        m.send_turn(app, thread_id, image)
    finally:
        app.close()
        server.shutdown()
        server.server_close()
    expected_requests = 2 if mode == "dangerous" else 3
    if len(Provider.requests) != expected_requests:
        raise AssertionError(
            f"{mode}: expected {expected_requests} provider requests, got {len(Provider.requests)}"
        )
    return Provider.requests


def runtime_gates(work: Path):
    for policy in (POLICY, POLICY_SECOND):
        policy_text = policy.read_text(encoding="utf-8")
        allowed_local_tools = policy_text.split("allowed_local_tools = [", 1)[1].split("]", 1)[0]
        if '"tool_search"' not in allowed_local_tools:
            raise AssertionError(f"tool_search is not explicitly allowed by {policy.name}")

    dangerous = run_turn("dangerous", POLICY, work)
    schemas = list(leaf_tools(dangerous[0].get("tools", [])))
    if not any(tool.get("name") == "exec_command" for _namespace, tool in schemas):
        raise AssertionError("dangerous exec_command schema was not visible")
    if "execution_disabled_by_runtime_policy" not in json.dumps(dangerous[1].get("input", [])):
        raise AssertionError("dangerous tool did not return execution_disabled_by_runtime_policy")
    if any(tool.get("type") == "web_search" for _namespace, tool in schemas):
        raise AssertionError("hosted/provider web_search was exposed")

    memory = run_turn("memory", POLICY, work, with_image=True)
    if "synthetic:memory_search:ok" not in json.dumps(memory[2].get("input", [])):
        raise AssertionError("memory_search did not execute successfully")
    if "input_image" not in json.dumps(memory[0].get("input", [])):
        raise AssertionError("image input did not reach the synthetic provider")

    second = run_turn("second", POLICY_SECOND, work)
    if "synthetic:weather:ok" not in json.dumps(second[2].get("input", [])):
        raise AssertionError("second MCP was not enabled by policy-only change")


def main():
    if not APP_SERVER.is_file():
        raise SystemExit("V2 app-server missing")
    with tempfile.TemporaryDirectory(prefix="execution-disabled-v2-") as raw:
        work = Path(raw)
        negative_startup_gates(work)
        runtime_gates(work)
    install_policy(POLICY)
    print("EXECUTION_DISABLED_COMPANION_V2_SYNTHETIC=PASS")


if __name__ == "__main__":
    main()
