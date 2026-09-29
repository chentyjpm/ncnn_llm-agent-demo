"""Bounded output + wall-clock timeout. This module is not a security sandbox."""
from __future__ import annotations
import os
from pathlib import Path
import signal
import subprocess
import threading
from typing import Sequence

def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    keys = ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {k: os.environ[k] for k in keys if k in os.environ}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    if extra:
        env.update(extra)
    return env

def kill_tree(p: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif p.poll() is None:
        # Windows descendant cleanup needs an OS job object for adversarial workloads.
        p.kill()

def run_process(argv: Sequence[str], *, cwd: Path, timeout: float = 30,
                input_text: str | None = None, max_output: int = 65536,
                env: dict[str, str] | None = None) -> dict:
    if not argv or not all(isinstance(x, str) and "\x00" not in x for x in argv):
        raise ValueError("argv must be a nonempty string array")
    if timeout <= 0 or max_output < 1:
        raise ValueError("Invalid timeout/output limit")
    p = subprocess.Popen(list(argv), cwd=cwd, env=clean_env(env), shell=False,
                         stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=(os.name == "posix"))
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    def drain(stream, idx):
        try:
            while chunk := stream.read(8192):
                room = max_output - len(buffers[idx])
                buffers[idx].extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    truncated[idx] = True
        finally:
            stream.close()
    threads = [threading.Thread(target=drain, args=(p.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, 1), daemon=True)]
    for t in threads:
        t.start()
    def feed():
        try:
            if p.stdin:
                p.stdin.write(input_text.encode("utf-8"))
                p.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    if input_text is not None:
        threading.Thread(target=feed, daemon=True).start()
    timed_out = False
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(p)
        p.wait(timeout=5)
    # Also remove ordinary descendants after the parent exits on POSIX.
    if os.name == "posix":
        kill_tree(p)
    for t in threads:
        t.join(timeout=2)
    return {"returncode": p.returncode, "timed_out": timed_out,
            "stdout": buffers[0].decode("utf-8", errors="replace"),
            "stderr": buffers[1].decode("utf-8", errors="replace"),
            "output_truncated": any(truncated)}
