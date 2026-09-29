#!/usr/bin/env python3
"""Fail-closed, one-turn CI diagnostic for stock search_tool_enabled()."""
import http.server
import importlib.util
import json
import os
import re
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("stock_baseline", HERE / "test_stock_provider_baseline.py")
baseline = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(baseline)


def decision_line(lines):
    marker = "FRONTEND_B_STOCK_DIAG search_tool_enabled "
    matched = [line for line in lines if marker in line]
    if not matched:
        raise SystemExit("stock diagnostic emitted no decision line")
    parsed = []
    for line in matched:
        fields = dict(re.findall(r"(slug|supports_search_tool|namespace_tools|enabled)=(\"[^\"]*\"|true|false)", line))
        if set(fields) != {"slug", "supports_search_tool", "namespace_tools", "enabled"}:
            raise SystemExit("stock diagnostic line was malformed")
        fields["slug"] = fields["slug"].strip('"')
        for name in ("supports_search_tool", "namespace_tools", "enabled"):
            fields[name] = fields[name] == "true"
        parsed.append(fields)
    if any(item != parsed[0] for item in parsed[1:]):
        raise SystemExit("stock diagnostic decision values were inconsistent within one turn")
    return parsed[0]


def main() -> None:
    stock = Path(os.environ["STOCK_APP_SERVER"])
    source = Path(os.environ["STOCK_SOURCE"])
    if not stock.is_file():
        raise SystemExit("diagnostic stock app-server missing")

    with baseline.m.CAPTURE_LOCK:
        baseline.m.CAPTURED.clear()
        baseline.m.RESPONSE_NUMBER = 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), baseline.m.ProviderHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app = None
    try:
        with tempfile.TemporaryDirectory(prefix="stock-search-diagnostic-") as raw:
            root = Path(raw)
            home = root / "codex-home"
            catalog = json.loads((source / "codex-rs/models-manager/models.json").read_text(encoding="utf-8"))
            selected = next(model for model in catalog["models"] if model["slug"] == "gpt-5.4")
            selected["supports_search_tool"] = True
            catalog_path = root / "search-capable-models.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            catalog["_frontend_b_catalog_path"] = str(catalog_path)
            baseline.write_stock_startup_config(home, f"http://127.0.0.1:{server.server_address[1]}/v1", catalog_path)
            baseline.m.APP_SERVER = stock
            app = baseline.m.AppServer(home)
            runtime = baseline.runtime_startup_gates(app, catalog)
            thread_id = app.request("thread/start", {"cwd": raw, "ephemeral": False})["thread"]["id"]
            app.wait_mcp_ready(thread_id)
            baseline.m.send_turn(app, thread_id)
            with baseline.m.CAPTURE_LOCK:
                requests = list(baseline.m.CAPTURED)
            if len(requests) != 1:
                raise SystemExit(f"diagnostic expected one provider request, got {len(requests)}")
            tools = [leaf.get("name") for _, leaf in baseline.m.leaf_tools(requests[0].get("tools", []))]
            result = {"runnerRuntime": runtime, "turnDecision": decision_line(app.stderr_lines), "providerTools": tools}
            print(json.dumps(result, sort_keys=True))
            turn = result["turnDecision"]
            if not turn["supports_search_tool"]:
                raise SystemExit("diagnostic mismatch: turn ModelInfo supports_search_tool=false")
            if not turn["namespace_tools"]:
                raise SystemExit("diagnostic mismatch: turn provider namespace_tools=false")
            if not turn["enabled"] or "tool_search" not in tools:
                raise SystemExit("diagnostic assembly mismatch: search enabled but tool_search absent")
    finally:
        if app is not None:
            app.close()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
