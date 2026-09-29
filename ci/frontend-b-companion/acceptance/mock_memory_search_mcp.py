#!/usr/bin/env python3
"""Synthetic stdio MCP server; never connects to production Ombre."""
import json
import sys


TOOL = {
    "name": "memory_search",
    "description": (
        "Read-only private long-term-memory lookup. Use only when the current "
        "conversation indicates a specific past detail would materially help. "
        "Formulate the query from the full current conversation, not only the "
        "latest wording. At most one call is permitted per user turn. Never "
        "claim a memory exists unless this tool returns it."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 480},
        },
        "required": ["query"],
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
                    "serverInfo": {"name": "synthetic-memory-search", "version": "1"},
                },
            })
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [TOOL]}})
        elif method == "tools/call":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "synthetic only"}],
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
