"""Explicitly opt-in Python execution and configured commands."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import sys
import uuid
from .paths import PolicyError, Workspace
from .process import run_process

class PythonRunner:
    def __init__(self, workspace: Workspace, mode: str = "disabled", *,
                 allow_unsafe_host: bool = False, timeout: float = 20,
                 docker_image: str = "python:3.12-slim"):
        if mode not in ("disabled", "docker", "unsafe-host"):
            raise ValueError("Unknown Python execution mode")
        if mode == "unsafe-host" and not allow_unsafe_host:
            raise PolicyError("unsafe-host requires --allow-unsafe-host-python")
        if not 0 < timeout <= 300:
            raise ValueError("Python timeout must be in (0, 300]")
        self.workspace, self.mode = workspace, mode
        self.timeout, self.docker_image = timeout, docker_image

    def docker_command(self, name: str) -> list[str]:
        root = str(self.workspace.root)
        if "," in root:
            raise PolicyError("Docker bind source cannot contain commas")
        user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") and os.getuid() else "65534:65534"
        return ["docker", "run", "--rm", "--pull=never", "--name", name,
                "--network=none", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--pids-limit=64",
                "--memory=512m", "--memory-swap=512m", "--cpus=1",
                "--ulimit", "nofile=64:64", "--ulimit", "fsize=16777216:16777216",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--user", user,
                "--mount", f"type=bind,src={root},dst=/workspace",
                "--workdir", "/workspace", "-i", self.docker_image,
                "python", "-I", "-X", "utf8", "-"]

    def run(self, code: str) -> dict:
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > 65536:
            raise PolicyError("Python code must be nonempty and at most 64 KiB")
        if self.mode == "disabled":
            raise PolicyError("Python execution is disabled")
        if self.mode == "unsafe-host":
            result = run_process([sys.executable, "-I", "-X", "utf8", "-"],
                                 cwd=self.workspace.root, timeout=self.timeout, input_text=code)
            result["isolation"] = "NONE: host execution; cwd and -I are not a sandbox"
            return result
        if not shutil.which("docker"):
            raise PolicyError("Docker executable not found; no unsafe fallback")
        name = "ncnn-agent-" + uuid.uuid4().hex
        try:
            result = run_process(self.docker_command(name), cwd=self.workspace.root,
                                 timeout=self.timeout, input_text=code)
            result["isolation"] = "docker-hardened (not a VM; workspace disk quota is not enforced)"
            return result
        finally:
            # Killing the docker CLI alone does not reliably stop its container.
            run_process(["docker", "rm", "-f", name], cwd=self.workspace.root,
                        timeout=10, max_output=4096)

class CommandRunner:
    def __init__(self, workspace: Workspace, commands: dict[str, list[str]], *, enabled: bool = False):
        self.workspace, self.commands, self.enabled = workspace, commands, enabled

    def run(self, name: str) -> dict:
        if not self.enabled:
            raise PolicyError("Configured command execution is disabled")
        if name not in self.commands:
            raise PolicyError("Command is not in the administrator-defined allowlist")
        argv = self.commands[name]
        if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
            raise PolicyError("Configured command must be an argv array")
        # No model-supplied args, shell expansion, PATH mutation or executable paths.
        return run_process(argv, cwd=self.workspace.root, timeout=60)
