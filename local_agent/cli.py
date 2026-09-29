from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import shutil
import sys
import uuid
from .agent import Agent, Audit
from .backends import NcnnBridgeBackend, NcnnCLIBackend, ScriptedBackend
from .execution import CommandRunner, PythonRunner
from .images import ImageRunner
from .mcp import MCPClient
from .paths import Workspace, PolicyError
from .tools import make_registry

PROJECT = Path(__file__).resolve().parents[1]

def expand(value: str) -> str:
    if value == "@python":
        return sys.executable
    if value.startswith("@project/"):
        return str(PROJECT / value[len("@project/"):])
    return value

def path_value(value: str) -> str:
    p = Path(expand(value)).expanduser()
    return str((PROJECT / p).resolve()) if not p.is_absolute() else str(p)

def load_config(path: str | None) -> dict:
    p = Path(path) if path else PROJECT / "configs/local.example.json"
    c = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(c, dict):
        raise ValueError("Config must be a JSON object")
    c["workspace"] = path_value(c.get("workspace", "workspace"))
    for key in ("llm", "image"):
        section = c.setdefault(key, {})
        if "command" in section:
            section["command"] = [expand(x) for x in section["command"]]
            if section["command"] and ("/" in section["command"][0] or "\\" in section["command"][0]):
                section["command"][0] = path_value(section["command"][0])
        if section.get("model"):
            section["model"] = path_value(section["model"])
    c["commands"] = {name: [expand(x) for x in argv] for name, argv in c.get("commands", {}).items()}
    for server in c.get("mcp_servers", []):
        server["command"] = [expand(x) for x in server["command"]]
    return c

def make_runtime(c: dict, args):
    ws = Workspace(args.workspace or c["workspace"])
    pc = c.get("python", {})
    py = PythonRunner(ws, pc.get("mode", "disabled"),
                      allow_unsafe_host=args.allow_unsafe_host_python,
                      timeout=float(pc.get("timeout", 20)),
                      docker_image=pc.get("docker_image", "python:3.12-slim"))
    commands = CommandRunner(ws, c.get("commands", {}), enabled=args.allow_commands)
    images = ImageRunner(ws, c.get("image", {}))
    registry = make_registry(ws, py, commands, images)
    clients = []
    try:
        for s in c.get("mcp_servers", []):
            if not s.get("enabled", False):
                continue
            if not args.trust_mcp:
                raise PolicyError("Review configured MCP server executables, then pass --trust-mcp")
            client = MCPClient(s["command"], cwd=PROJECT, timeout=float(s.get("timeout", 30)), env=s.get("env"))
            clients.append(client)
            registry.add_mcp(s["name"], client, s.get("allowed_tools", []))
        return ws, registry, clients
    except Exception:
        for client in clients:
            client.close()
        raise

def doctor(c: dict) -> dict:
    def executable(section):
        cmd = section.get("command", [])
        return bool(cmd and (shutil.which(cmd[0]) or Path(cmd[0]).is_file()))
    llm, img = c.get("llm", {}), c.get("image", {})
    return {"python": platform.python_version(), "platform": platform.platform(),
            "orchestrator": "stdlib Python; native inference is separate",
            "llm_backend": llm.get("backend"), "llm_executable_found": executable(llm),
            "llm_model_json_found": bool(llm.get("model") and Path(llm["model"]).joinpath("model.json").is_file()),
            "image_enabled": img.get("enabled", False), "image_executable_found": executable(img),
            "image_model_directory_found": bool(img.get("model") and Path(img["model"]).is_dir()),
            "docker_executable_found": bool(shutil.which("docker")),
            "gpu_device_nodes_linux": [str(p) for p in list(Path("/dev").glob("nvidia*")) + list(Path("/dev/dri").glob("renderD*"))],
            "gpu_note": "Empty Linux device nodes do not diagnose Windows/macOS GPU availability. No Vulkan inference probe was performed.",
            "python_mode": c.get("python", {}).get("mode", "disabled"),
            "mcp_enabled": [s["name"] for s in c.get("mcp_servers", []) if s.get("enabled")],
            "weights_bundled": False}

def run_demo(args):
    if not args.allow_unsafe_host_python:
        raise PolicyError("Demo executes supplied, inspectable Python on the host. Add --allow-unsafe-host-python; NOT a sandbox.")
    root = Path(args.workspace or "demo_workspace")
    if root.exists() and any(root.iterdir()):
        raise PolicyError("Demo needs an empty workspace; choose a new --workspace directory")
    ws = Workspace(root)
    py = PythonRunner(ws, "unsafe-host", allow_unsafe_host=True)
    cmds = CommandRunner(ws, {"python_version": [sys.executable, "--version"]}, enabled=True)
    r = make_registry(ws, py, cmds)
    mcp = MCPClient([sys.executable, "-u", str(PROJECT / "examples/mcp_server.py")], cwd=PROJECT)
    log = PROJECT / "reports" / "runs" / ("demo-" + uuid.uuid4().hex + ".jsonl")
    audit = Audit(log)
    try:
        r.add_mcp("math", mcp, ["summarize_numbers"])
        actions = json.loads((PROJECT / "examples/demo_plan.json").read_text(encoding="utf-8"))
        result = Agent(ScriptedBackend(actions), r, max_steps=16, audit=audit).run(
            "SCRIPTED INTEGRATION TEST: write sample CSV, deliberately fail once, repair Python, call MCP and read results. Not real model reasoning.")
        checks = {"agent_finished": result["ok"]}
        if (ws.root / "distance_summary.json").is_file():
            data = json.loads(ws.read("distance_summary.json"))
            checks["python_real_result"] = data == {"count": 3, "mean_m": 234.0, "min_m": 232.0, "max_m": 236.0}
        else:
            checks["python_real_result"] = False
        calls = [e for e in audit.events if e["event"] == "tool_result"]
        checks["expected_error_observed"] = any(e["tool"] == "python.run" and not e["result"]["ok"] for e in calls)
        checks["mcp_real_process_result"] = any(e["tool"].startswith("mcp__") and e["result"]["ok"] and
            e["result"]["result"].get("structuredContent", {}).get("mean") == 234 for e in calls)
        checks["fixed_command_ran"] = any(e["tool"] == "system.run" and e["result"]["ok"] for e in calls)
        checks["only_expected_error"] = sum(not e["result"]["ok"] for e in calls) == 1
        result.update({"integration_checks": checks, "ok": all(checks.values()),
                       "audit_log": str(log), "workspace": str(ws.root),
                       "disclaimer": "Planning/repair decisions are scripted. Python, file operations and MCP subprocess calls are real. No ncnn inference here."})
        return result
    finally:
        mcp.close()

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local ncnn Agent MVP (offline orchestration)")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    for name in ("doctor", "tools", "run", "tool", "demo", "serve"):
        p = sub.add_parser(name)
        p.add_argument("--config")
        p.add_argument("--workspace")
        p.add_argument("--allow-unsafe-host-python", action="store_true")
        p.add_argument("--allow-commands", action="store_true")
        p.add_argument("--trust-mcp", action="store_true")
        if name == "serve":
            p.add_argument("--port", type=int, default=8765)
            p.add_argument("--data-dir", default="web-data")
            p.add_argument("--open", action="store_true", dest="open_browser")
        if name == "run":
            p.add_argument("--task", required=True)
        if name == "tool":
            p.add_argument("--name", required=True)
            p.add_argument("--arguments", default="{}")
    args = parser.parse_args(argv)
    clients = []
    try:
        if args.subcommand == "serve":
            from .web import serve
            return serve(load_config(args.config), args)
        if args.subcommand == "demo":
            result = run_demo(args)
        else:
            c = load_config(args.config)
            if args.subcommand == "doctor":
                result = doctor(c)
            else:
                ws, registry, clients = make_runtime(c, args)
                if args.subcommand == "tools":
                    result = {"tools": registry.schemas()}
                elif args.subcommand == "tool":
                    log = PROJECT / "reports/runs" / ("tool-" + uuid.uuid4().hex + ".jsonl")
                    audit = Audit(log)
                    arguments = json.loads(args.arguments)
                    result = registry.call(args.name, arguments)
                    audit.add("direct_tool", tool=args.name, arguments=arguments, result=result)
                    result["audit_log"] = str(log)
                else:
                    kind = c.get("llm", {}).get("backend", "ncnn_cli")
                    backends = {"ncnn_cli": NcnnCLIBackend, "ncnn_bridge": NcnnBridgeBackend}
                    if kind not in backends:
                        raise PolicyError("run requires a real ncnn backend; scripted mode is only available in demo/tests")
                    backend = backends[kind](c["llm"], PROJECT)
                    log = PROJECT / "reports/runs" / ("run-" + uuid.uuid4().hex + ".jsonl")
                    result = Agent(backend, registry, max_steps=int(c.get("max_steps", 12)), audit=Audit(log)).run(args.task)
                    result["audit_log"] = str(log)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False, indent=2))
        return 1
    finally:
        for c in clients:
            c.close()
