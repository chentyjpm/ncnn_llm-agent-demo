"""Qwen Image CLI adapter with per-engine Vulkan-first preflight."""
from __future__ import annotations
from pathlib import Path
from .paths import PolicyError, Workspace
from .process import run_process
from .device import select, engine_env

class ImageRunner:
    def __init__(self, workspace: Workspace, config: dict):
        self.workspace, self.config = workspace, config

    def command(self, *, prompt: str, output: str, width: int = 512,
                height: int = 512, steps: int = 40, seed: int = 42,
                references: list[str] | None = None) -> list[str]:
        if not self.config.get("enabled", False):
            raise PolicyError("Qwen Image backend is disabled")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise PolicyError("Invalid image prompt")
        references = references or []
        if not isinstance(references, list) or len(references) > 10:
            raise PolicyError("At most 10 reference images are allowed")
        divisor = 32 if references else 16
        for dim in (width, height):
            if type(dim) is not int or not 64 <= dim <= 2048 or dim % divisor:
                raise PolicyError(f"Dimensions must be 64..2048 and multiples of {divisor}")
        if type(steps) is not int or not 1 <= steps <= 100:
            raise PolicyError("steps must be in 1..100; four steps alone is not a LoRA accelerator")
        if type(seed) is not int or not 0 <= seed <= 2147483647:
            raise PolicyError("Invalid random seed")
        out = self.workspace.path(output)
        if out.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            raise PolicyError("Unsupported output image extension")
        if out.exists():
            raise PolicyError("Image output already exists; choose a new name")
        prefix = self.config.get("command", [])
        model = Path(self.config.get("model", ""))
        if not prefix or not isinstance(prefix, list) or not model.is_dir():
            raise PolicyError("Configure an executable argv array and an existing model directory")
        self.device_selection = select(self.config, "image")
        argv = list(prefix) + ["-m", str(model.resolve()), "-p", prompt, "-o", str(out),
                              "-s", f"{width},{height}", "-l", str(steps), "-r", str(seed),
                              "-g", str(self.device_selection["gpu"])]
        for ref in references:
            p = self.workspace.path(ref)
            if not p.is_file():
                raise PolicyError("Reference image does not exist")
            argv += ["-i", str(p)]
        return argv

    def run(self, **arguments) -> dict:
        argv = self.command(**arguments)
        out = self.workspace.path(arguments["output"])
        out.parent.mkdir(parents=True, exist_ok=True)
        result = run_process(argv, cwd=self.workspace.root,
                             timeout=float(self.config.get("timeout", 1800)), env=engine_env(self.config["command"]))
        valid = out.is_file() and out.stat().st_size > 0
        result.update({"path": arguments["output"], "file_created": valid, "device_selection": self.device_selection})
        if result["returncode"] == 0 and not valid:
            result["error"] = "Backend exited successfully but did not create an output file"
        return result
