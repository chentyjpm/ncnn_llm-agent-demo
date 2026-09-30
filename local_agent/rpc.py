"""Synchronous, bounded JSON-RPC 2.0 over newline-delimited stdio."""
from __future__ import annotations
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from .process import clean_env, kill_tree, background_options

class RPCError(RuntimeError):
    pass

class StdioRPC:
    def __init__(self, command: list[str], *, cwd: Path, timeout: float = 30,
                 env: dict[str, str] | None = None, max_message: int = 1_048_576):
        import os
        self.timeout, self.max_message = timeout, max_message
        self.process = subprocess.Popen(command, cwd=cwd, env=clean_env(env),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"), shell=False, **background_options())
        self.inbox: queue.Queue = queue.Queue(maxsize=128)
        self.stderr = bytearray()
        self.notifications: list[dict] = []
        self.closed = False
        self.counter = 0
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.read_thread = threading.Thread(target=self._read, daemon=True)
        self.err_thread = threading.Thread(target=self._read_err, daemon=True)
        self.read_thread.start()
        self.err_thread.start()

    def _put(self, item):
        try:
            self.inbox.put(item, timeout=1)
        except queue.Full:
            kill_tree(self.process)

    def _read(self):
        try:
            while not self.closed:
                line = self.process.stdout.readline(self.max_message + 1)
                if not line:
                    break
                if len(line) > self.max_message:
                    raise RPCError("Incoming RPC message too large")
                msg = json.loads(line)
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    raise RPCError("Invalid JSON-RPC message")
                self._put(msg)
        except Exception as e:
            self._put(RPCError(f"RPC reader failed: {e}"))
        finally:
            self._put(RPCError("RPC process closed stdout"))

    def _read_err(self):
        try:
            while chunk := self.process.stderr.read(4096):
                self.stderr.extend(chunk[:max(0, 65536 - len(self.stderr))])
        except (OSError, ValueError):
            pass

    def send(self, msg: dict):
        data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(data) > self.max_message:
            raise RPCError("Outgoing RPC message too large")
        with self.write_lock:
            if self.closed or self.process.poll() is not None:
                raise RPCError("RPC process is not running")
            try:
                self.process.stdin.write(data)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise RPCError("RPC write failed") from e

    def notify(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)

    def request(self, method: str, params: dict | None = None):
        with self.lock:
            self.counter += 1
            ident = self.counter
            self.send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:
                        raise queue.Empty
                    msg = self.inbox.get(timeout=remaining)
                except queue.Empty:
                    try:
                        self.notify("notifications/cancelled", {"requestId": ident, "reason": "Timeout"})
                    except RPCError:
                        pass
                    self.close()
                    raise RPCError(f"RPC timeout: {method}")
                if isinstance(msg, Exception):
                    raise msg
                if "method" in msg:
                    if "id" in msg:
                        reply = {"jsonrpc": "2.0", "id": msg["id"]}
                        if msg["method"] == "ping":
                            reply["result"] = {}
                        else:
                            reply["error"] = {"code": -32601, "message": "Client method not supported"}
                        self.send(reply)
                    else:
                        self.notifications.append(msg)
                        self.notifications = self.notifications[-100:]
                    continue
                if msg.get("id") != ident:
                    raise RPCError("Unexpected response id")
                if "error" in msg:
                    raise RPCError(f"Remote RPC error: {msg['error']}")
                if "result" not in msg:
                    raise RPCError("RPC result is missing")
                return msg["result"]

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                kill_tree(self.process)
                self.process.wait(timeout=3)
        kill_tree(self.process)
        for t in (self.read_thread, self.err_thread):
            t.join(timeout=1)
        for stream in (self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
