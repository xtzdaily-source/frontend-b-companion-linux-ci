#!/usr/bin/env python3
"""Offline stdio MCP used only by execution-disabled companion v2 CI."""
import argparse
import json
import sys


parser = argparse.ArgumentParser()
parser.add_argument("--tool", required=True)
args = parser.parse_args()

tool = {
    "name": args.tool,
    "description": f"Synthetic read-only {args.tool} tool.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
}


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        message = json.loads(line)
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "synthetic-policy-mcp", "version": "1"},
                },
            })
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [tool]}})
        elif method == "tools/call":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"synthetic:{args.tool}:ok"}],
                    "isError": False,
                },
            })
        elif request_id is not None:
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method_not_found"},
            })
    except Exception:
        continue
