#!/usr/bin/env python3
"""Fail-closed runtime capture of stock's native tool_search exchange."""
import http.server
import importlib.util
import json
import os
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("acceptance", HERE / "test_companion_allowlist.py")
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m)
m.HERE = HERE
m.MOCK_MCP = HERE / "mock_memory_search_mcp.py"
CAPTURES = []

def send_sse(handler, response):
    done = {"type": "response.output_item.done", "item": response["output"][0]}
    body = b"event: response.output_item.done\ndata: " + json.dumps(done, separators=(",", ":")).encode() + b"\n\n"
    body += b"event: response.completed\ndata: " + json.dumps({"type": "response.completed", "response": response}, separators=(",", ":")).encode() + b"\n\n"
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers(); handler.wfile.write(body)

class Provider(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *_): pass
    def do_POST(self):
        if self.path != "/v1/responses": self.send_error(404); return
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        CAPTURES.append(request)
        index = len(CAPTURES)
        if index == 1:
            item = {"type": "tool_search_call", "call_id": "stock-search-1", "execution": "client", "arguments": {"query": "memory_search", "limit": 1}}
        else:
            item = {"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "synthetic response", "annotations": []}]}
        send_sse(self, {"id": f"stock-{index}", "object": "response", "created_at": int(time.time()), "status": "completed", "model": "gpt-5.5", "output": [item]})

def tool_search_definition(value, inside=False):
    if isinstance(value, dict):
        now_inside = inside or value.get("type") == "tool_search_output"
        if now_inside and value.get("name") == "memory_search" and {"description", "parameters"} <= set(value):
            return {key: value[key] for key in ("name", "description", "parameters")}
        for child in value.values():
            found = tool_search_definition(child, now_inside)
            if found: return found
    if isinstance(value, list):
        for child in value:
            found = tool_search_definition(child, inside)
            if found: return found
    return None


def require_native_tool_search(specs, label):
    identities = [
        {"type": leaf.get("type"), "name": leaf.get("name")}
        for _, leaf in m.leaf_tools(specs)
    ]
    native = [
        identity for identity in identities
        if identity == {"type": "tool_search", "name": None}
    ]
    if len(native) != 1:
        raise SystemExit(f"{label} lacks one native type=tool_search/name=null entry: {identities}")
    return identities


def write_stock_startup_config(home, base_url, catalog_path):
    """Write the complete, startup-only stock configuration before app launch."""
    home.mkdir(parents=True, exist_ok=True)
    python = os.environ.get("PYTHON", "python3")
    config = f'''model = "gpt-5.4"
model_provider = "synthetic"
approval_policy = "never"
sandbox_mode = "read-only"
model_catalog_json = {json.dumps(str(catalog_path))}

[model_providers.synthetic]
name = "synthetic-local"
base_url = {json.dumps(base_url)}
wire_api = "responses"
env_key = "SYNTHETIC_ONLY_KEY"
requires_openai_auth = false

[mcp_servers.memory_search]
command = {json.dumps(python)}
args = [{json.dumps(str(m.MOCK_MCP))}]
enabled_tools = ["memory_search"]

[features]
shell_tool = true
unified_exec = true
code_mode = false
code_mode_host = false
code_mode_only = false
'''
    (home / "config.toml").write_text(config, encoding="utf-8")
    return config


def runtime_startup_gates(app, expected_catalog, expected_model="gpt-5.4"):
    """Fail closed on app-server's actual read-only capability/config views."""
    capabilities = app.request("modelProvider/capabilities/read", {})
    config = app.request("config/read", {"includeLayers": True})
    models = app.request("model/list", {"includeHidden": True, "limit": 200})
    actual_model = config.get("config", {}).get("model")
    actual_catalog = config.get("config", {}).get("model_catalog_json")
    listed = [item for item in models.get("data", []) if item.get("model") == expected_model or item.get("id") == expected_model]
    catalog_model = next((item for item in expected_catalog["models"] if item["slug"] == expected_model), None)
    evidence = {
        "capabilities": {"namespaceTools": capabilities.get("namespaceTools")},
        "config": {"model": actual_model, "model_catalog_json": actual_catalog},
        "modelList": {"selected": [item.get("model") or item.get("id") for item in listed]},
        "derivedCatalog": {"selected": expected_model, "supportsSearchTool": None if catalog_model is None else catalog_model.get("supports_search_tool")},
    }
    if capabilities.get("namespaceTools") is not True:
        raise SystemExit("runtime namespace_tools gate failed: " + json.dumps(evidence, sort_keys=True))
    if actual_model != expected_model or not listed:
        raise SystemExit("runtime selected-model gate failed: " + json.dumps(evidence, sort_keys=True))
    # 0.146.0's public model/list payload deliberately does not expose
    # supportsSearchTool. Verify the catalog selected by the startup config,
    # then require the first request's native tool_search as the runtime proof.
    if actual_catalog != str(expected_catalog_path(expected_catalog)):
        raise SystemExit("runtime model_catalog_json gate failed: " + json.dumps(evidence, sort_keys=True))
    if catalog_model is None or catalog_model.get("supports_search_tool") is not True:
        raise SystemExit("derived catalog supports_search_tool gate failed: " + json.dumps(evidence, sort_keys=True))
    return evidence


def expected_catalog_path(catalog):
    return Path(catalog["_frontend_b_catalog_path"])

def main():
    stock = Path(os.environ["STOCK_APP_SERVER"])
    if not stock.is_file(): raise SystemExit("stock app-server missing")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    with tempfile.TemporaryDirectory(prefix="stock-baseline-") as raw:
        home = Path(raw) / "codex-home"
        catalog = json.loads(
            (Path(os.environ["STOCK_SOURCE"]) / "codex-rs/models-manager/models.json").read_text(encoding="utf-8")
        )
        search_model = next(model for model in catalog["models"] if model["slug"] == "gpt-5.4")
        search_model["supports_search_tool"] = True
        catalog_path = Path(raw) / "search-capable-models.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        catalog["_frontend_b_catalog_path"] = str(catalog_path)
        write_stock_startup_config(home, f"http://127.0.0.1:{server.server_address[1]}/v1", catalog_path)
        m.APP_SERVER = stock
        app = m.AppServer(home)
        try:
            runtime_startup_gates(app, catalog)
            thread_id = app.request("thread/start", {"cwd": raw, "ephemeral": False})["thread"]["id"]
            app.wait_mcp_ready(thread_id)
            m.send_turn(app, thread_id)
        finally:
            app.close(); server.shutdown(); server.server_close()
    if len(CAPTURES) != 2: raise SystemExit(f"expected two provider requests, got {len(CAPTURES)}")
    require_native_tool_search(CAPTURES[0].get("tools", []), "stock first request")
    baseline = tool_search_definition(CAPTURES[1].get("input", []))
    if baseline is None: raise SystemExit("stock tool_search output omitted memory_search definition")
    report = Path(os.environ["STOCK_BASELINE_PATH"])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(baseline, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
