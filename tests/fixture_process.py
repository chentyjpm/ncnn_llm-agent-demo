"""FAKE backends for protocol/argv tests only. No model inference occurs here."""
import json
from pathlib import Path
import sys
import time
mode = sys.argv[1]
if mode == "fake-image":
    import base64
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    # Fixed one-pixel PNG fixture; never represented as a generated model image.
    output.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    print("FAKE_IMAGE_FIXTURE_NO_INFERENCE")
    raise SystemExit(0)
if mode == "fake-cli":
    print("llm_ncnn_run (cli). Type 'exit' or 'quit' to end the conversation.")
    print("Using template: ChatML")
    print("User: ", end="", flush=True)
    prompt = input()
    assert "\\n" in prompt  # Multiline prompt serialized into a single input line.
    print('Assistant: {"final":"adapter transport test, not inference"}')
    print("User: ", end="", flush=True)
    input()
    raise SystemExit(0)
initialized = False
for line in sys.stdin:
    req = json.loads(line)
    method = req.get("method")
    if "id" not in req:
        if method == "notifications/initialized":
            initialized = True
        continue
    ident = req["id"]
    out = {"jsonrpc": "2.0", "id": ident}
    if mode == "hang":
        time.sleep(60)
    elif mode == "bad-json":
        print("Not JSON", flush=True)
        continue
    elif mode == "bad-id":
        out["id"] = ident + 999
        out["result"] = {}
    elif mode == "native":
        if method == "infer":
            out["result"] = {"text": '{"final":"native protocol fixture, not inference"}'}
        else:
            out["result"] = {}
    elif method == "initialize":
        out["result"] = {"protocolVersion": "2099-01-01" if mode == "bad-version" else "2025-11-25",
                          "capabilities": {} if mode == "no-tools" else {"tools": {}},
                          "serverInfo": {"name": "fixture", "version": "0"}}
    elif method == "tools/list":
        if not initialized:
            out["error"] = {"code": -32000, "message": "initialized notification missing"}
        else:
            cursor = req.get("params", {}).get("cursor")
            tool = {"name": "first" if not cursor else "second", "inputSchema": {"type": "object"}}
            out["result"] = {"tools": [tool]}
            if not cursor or mode == "repeat-cursor":
                out["result"]["nextCursor"] = "page2"
    elif method == "tools/call":
        out["result"] = {"content": [{"type": "text", "text": "fixture"}], "isError": False}
    elif method == "ping":
        out["result"] = {}
    else:
        out["result"] = {"echo": req.get("params", {})}
    if mode == "notify":
        print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {"data": "fixture notice"}}), flush=True)
    if mode == "server-ping":
        print(json.dumps({"jsonrpc": "2.0", "id": "server-ping", "method": "ping"}), flush=True)
        response = json.loads(input())
        assert response["id"] == "server-ping" and response["result"] == {}
    print(json.dumps(out), flush=True)
