#!/usr/bin/env python3
"""Real local stdio MCP example. Deterministic arithmetic, no model required."""
import json
import statistics
import sys

ready = False
initialized = False
SCHEMA = {"type": "object", "properties": {"values": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 10000}}, "required": ["values"], "additionalProperties": False}

def respond(i, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": i}
    out["error" if error is not None else "result"] = error if error is not None else result
    print(json.dumps(out, ensure_ascii=False, allow_nan=False), flush=True)

for line in sys.stdin:
    msg = {}
    try:
        msg = json.loads(line)
        method, ident, params = msg.get("method"), msg.get("id"), msg.get("params", {})
        if method == "initialize":
            initialized = True
            respond(ident, {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                            "serverInfo": {"name": "local-math-demo", "version": "1.0.0"}})
        elif method == "notifications/initialized":
            ready = initialized
        elif method == "ping":
            respond(ident, {})
        elif "id" not in msg:
            continue
        elif not ready:
            respond(ident, error={"code": -32000, "message": "Not initialized"})
        elif method == "tools/list":
            respond(ident, {"tools": [{"name": "summarize_numbers", "description": "Return count, sum, mean, min and max for finite numbers.", "inputSchema": SCHEMA}]})
        elif method == "tools/call":
            import math
            values = params.get("arguments", {}).get("values")
            if params.get("name") != "summarize_numbers":
                respond(ident, error={"code": -32602, "message": "Unknown tool"})
                continue
            if not isinstance(values, list) or not 1 <= len(values) <= 10000 or not all(type(v) in (float, int) and math.isfinite(v) for v in values):
                respond(ident, {"isError": True, "content": [{"type": "text", "text": "values must be nonempty finite numbers"}]})
                continue
            data = {"count": len(values), "sum": sum(values), "mean": statistics.mean(values), "min": min(values), "max": max(values)}
            respond(ident, {"content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data, "isError": False})
        else:
            respond(ident, error={"code": -32601, "message": "Method not found"})
    except Exception as e:
        if isinstance(msg, dict) and "id" in msg:
            respond(msg["id"], error={"code": -32602, "message": str(e)})
