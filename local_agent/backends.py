"""Real ncnn adapters and an explicitly labelled deterministic test backend."""
from __future__ import annotations
import json
from pathlib import Path
from .paths import PolicyError
from .process import run_process
from .rpc import StdioRPC

class ScriptedBackend:
    """Test double only: never called a language model and never auto-selected."""
    label = "SCRIPTED_TEST_DOUBLE_NOT_LLM"
    def __init__(self, actions: list):
        self.actions = iter(actions)
    def complete(self, messages: list[dict]) -> str:
        try:
            action = next(self.actions)
        except StopIteration as e:
            raise RuntimeError("Scripted test actions exhausted") from e
        return action if isinstance(action, str) else json.dumps(action, ensure_ascii=False)
    def release(self):
        pass

class NcnnCLIBackend:
    """Compatible with upstream llm_ncnn_run's interactive stdin interface.
    Starts a fresh process each turn; suitable for smoke tests, not high throughput.
    """
    label = "NCNN_CLI_REAL_MODEL"
    def __init__(self, config: dict, project: Path):
        self.config, self.project = config, project
        if not Path(config.get("model", "")).joinpath("model.json").is_file():
            raise PolicyError("ncnn model/model.json not found; no fallback to a fake model")
        if not config.get("command"):
            raise PolicyError("ncnn CLI command is not configured")
    def complete(self, messages: list[dict]) -> str:
        c = self.config
        argv = list(c["command"]) + ["--model", str(Path(c["model"]).resolve()),
                                      "--threads", str(int(c.get("threads", 4)))]
        if c.get("vulkan", False):
            argv += ["--vulkan", "--vulkan-device", str(int(c.get("gpu", 0)))]
        # Upstream getline() reads one line; serialize embedded newlines as JSON escapes.
        prompt = "Follow the system instructions and conversation encoded below. Return the next assistant message only. /no_think " + json.dumps(messages, ensure_ascii=False)
        result = run_process(argv, cwd=self.project, timeout=float(c.get("timeout", 180)),
                             input_text=prompt + "\nexit\n", max_output=1_048_576)
        if result["timed_out"] or result["returncode"] != 0:
            raise RuntimeError("ncnn CLI failed: " + json.dumps(result, ensure_ascii=False))
        text = result["stdout"]
        marker = "Assistant: "
        if marker not in text:
            raise RuntimeError("Unrecognized upstream CLI output; use native JSON-RPC bridge")
        text = text.split(marker, 1)[1]
        # Remove ONLY the final CLI prompt. Do not split on 'User:' inside generated code.
        if text.rstrip().endswith("User:"):
            text = text.rstrip()[:-5]
        return text.strip()
    def release(self):
        pass

class NcnnBridgeBackend:
    """Persistent C++ ncnn process. Model is released before heavy image calls."""
    label = "NCNN_BRIDGE_REAL_MODEL"
    def __init__(self, config: dict, project: Path):
        self.config, self.project, self.rpc = config, project, None
        if not Path(config.get("model", "")).joinpath("model.json").is_file():
            raise PolicyError("ncnn model/model.json not found; no fake fallback")
        if not config.get("command"):
            raise PolicyError("Native bridge command is not configured")
    def complete(self, messages: list[dict]) -> str:
        c = self.config
        if self.rpc is None:
            argv = list(c["command"]) + ["--model", str(Path(c["model"]).resolve()),
                                          "--threads", str(int(c.get("threads", 4)))]
            if c.get("vulkan", False):
                argv += ["--vulkan", "--vulkan-device", str(int(c.get("gpu", 0)))]
            self.rpc = StdioRPC(argv, cwd=self.project, timeout=float(c.get("timeout", 180)))
        result = self.rpc.request("infer", {"messages": messages, "max_new_tokens": int(c.get("max_new_tokens", 1024))})
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise RuntimeError("Malformed native bridge result")
        return result["text"]
    def release(self):
        if self.rpc:
            self.rpc.close()
            self.rpc = None
