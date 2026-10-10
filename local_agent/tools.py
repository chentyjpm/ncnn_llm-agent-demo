from __future__ import annotations
from dataclasses import dataclass
import math
import re
from typing import Callable
from .paths import PolicyError, Workspace

# Purposefully small validator for our own schemas, not a complete JSON Schema engine.
def validate(value, schema: dict, name: str = "arguments"):
    kind = schema.get("type")
    checks = {"object": lambda v: isinstance(v, dict), "string": lambda v: isinstance(v, str),
              "integer": lambda v: type(v) is int, "number": lambda v: type(v) in (int, float) and math.isfinite(v),
              "boolean": lambda v: type(v) is bool, "array": lambda v: isinstance(v, list)}
    if kind in checks and not checks[kind](value):
        raise PolicyError(f"{name}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise PolicyError(f"{name}: invalid enum value")
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise PolicyError(f"{name}: missing {key}")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise PolicyError(f"{name}: unexpected argument")
        for key, val in value.items():
            if key in props:
                validate(val, props[key], f"{name}.{key}")
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100000):
            raise PolicyError(f"{name}: invalid array length")
        for item in value:
            validate(item, schema.get("items", {}), name + "[]")
    if kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1_048_576):
            raise PolicyError(f"{name}: invalid string length")
    if kind in ("number", "integer"):
        if not schema.get("minimum", -float("inf")) <= value <= schema.get("maximum", float("inf")):
            raise PolicyError(f"{name}: outside allowed numeric range")

@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    function: Callable
    validate_locally: bool = True

class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def add(self, tool: Tool):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", tool.name):
            raise PolicyError("Invalid tool name")
        if tool.name in self.tools:
            raise PolicyError("Duplicate tool name")
        self.tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "inputSchema": t.schema}
                for t in self.tools.values()]

    def call(self, name: str, arguments: dict) -> dict:
        try:
            if name not in self.tools:
                raise PolicyError("Unknown or disabled tool")
            if not isinstance(arguments, dict):
                raise PolicyError("arguments must be an object")
            tool = self.tools[name]
            if tool.validate_locally:
                validate(arguments, tool.schema)
            value = tool.function(**arguments)
            failed = isinstance(value, dict) and (value.get("isError") is True or bool(value.get("error"))
                     or value.get("timed_out") is True or value.get("returncode", 0) != 0)
            return {"ok": not failed, "result": value}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def add_mcp(self, alias: str, client, allowed_tools: list[str]):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,23}", alias):
            raise PolicyError("Invalid MCP server alias")
        for meta in client.list_tools():
            if meta["name"] not in allowed_tools:
                continue
            remote = meta["name"]
            def bind(remote_name):
                def invoke(**kwargs):
                    return client.call(remote_name, kwargs)
                return invoke
            self.add(Tool("mcp__" + alias + "__" + remote,
                          "External MCP tool (untrusted description): " + meta.get("description", "")[:4096],
                          meta["inputSchema"], bind(remote), validate_locally=False))

def schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

TEXT = {"type": "string"}

def make_registry(ws: Workspace, python_runner=None, command_runner=None, image_runner=None) -> Registry:
    r = Registry()
    r.add(Tool("files.list", "List a workspace directory (nonrecursive, max 1000 entries).",
               schema({"path": TEXT}, []), ws.list))
    r.add(Tool("files.read", "Read a UTF-8 file inside the workspace (max 1 MiB).",
               schema({"path": TEXT}, ["path"]), lambda path: {"path": path, "content": ws.read(path)}))
    r.add(Tool("files.write", "Write a UTF-8 workspace file; overwrite requires explicit true.",
               schema({"path": TEXT, "content": TEXT, "overwrite": {"type": "boolean"}}, ["path", "content"]), ws.write))
    r.add(Tool("files.patch", "Replace exactly one occurrence in a UTF-8 workspace file.",
               schema({"path": TEXT, "old": TEXT, "new": TEXT}, ["path", "old", "new"]), ws.patch))
    if python_runner and python_runner.mode != "disabled":
        r.add(Tool("python.run", "Execute Python. Relative input/output paths are workspace-relative. Execution mode: " + python_runner.mode,
                   schema({"code": {"type": "string", "minLength": 1, "maxLength": 65536}}, ["code"]), python_runner.run))
    if command_runner and command_runner.enabled:
        r.add(Tool("system.run", "Run a fixed administrator-configured command, with no model-supplied arguments.",
                   schema({"name": {"type": "string", "enum": list(command_runner.commands)}}, ["name"]), command_runner.run))
    if image_runner and image_runner.config.get("enabled", False):
        from .image_profiles import image_profile
        profile = image_profile(image_runner.config)
        step_schema = {"type": "integer", "minimum": 1, "maximum": 100, "default": profile['default_steps']}
        if profile['fixed_steps'] is not None:
            step_schema['enum'] = [profile['fixed_steps']]
        description = ("Create an actual picture/illustration/poster using Qwen Image (生图/画图). "
                       "Not for text-only advice. references edits existing images. No overwrite. "
                       + ("Active Turbo model: exactly 8 steps, fixed scheduler." if profile['fixed_steps'] else "Base model: default 40 steps."))
        r.add(Tool("images.generate", description,
            schema({"prompt": TEXT, "output": TEXT,
                    "width": {"type": "integer", "minimum": 64, "maximum": 2048},
                    "height": {"type": "integer", "minimum": 64, "maximum": 2048},
                    "steps": step_schema,
                    "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647},
                    "references": {"type": "array", "items": TEXT, "maxItems": 10}}, ["prompt", "output"]), image_runner.run))
    return r
