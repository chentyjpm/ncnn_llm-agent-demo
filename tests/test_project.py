from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from local_agent.agent import Agent, Audit, ActionError, parse_action
from local_agent.backends import NcnnCLIBackend, NcnnBridgeBackend, ScriptedBackend
from local_agent.execution import PythonRunner, CommandRunner
from local_agent.images import ImageRunner
from local_agent.mcp import MCPClient
from local_agent.paths import Workspace, PolicyError
from local_agent.process import run_process
from local_agent.rpc import StdioRPC, RPCError
from local_agent.tools import Registry, Tool, make_registry, validate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixture_process.py")

class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ncnn_agent_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root / "workspace")
    def rpc(self, mode="echo", timeout=3):
        r = StdioRPC([sys.executable, "-u", str(FIXTURE), mode], cwd=ROOT, timeout=timeout)
        self.addCleanup(r.close)
        return r
    def mcp(self, mode="paginate"):
        c = MCPClient([sys.executable, "-u", str(FIXTURE), mode], cwd=ROOT, timeout=3)
        self.addCleanup(c.close)
        return c

class WorkspaceTests(Base):
    def test_unicode_roundtrip(self):
        self.ws.write("数据/测试.txt", "中文 UTF-8 🧪")
        self.assertEqual(self.ws.read("数据/测试.txt"), "中文 UTF-8 🧪")
    def test_parent_traversal_blocked(self):
        with self.assertRaises(PolicyError): self.ws.write("../escape.txt", "x")
    def test_absolute_blocked(self):
        with self.assertRaises(PolicyError): self.ws.read("/etc/passwd")
    def test_windows_drive_unc_ads_blocked(self):
        for p in ["C:/test", "C:\\test", "\\\\server\\share", "file:ads", "a\\..\\x"]:
            with self.subTest(p=p), self.assertRaises(PolicyError): self.ws.path(p)
    def test_platform_reserved_names_blocked(self):
        for p in ["CON", "nul.txt", "x/COM1", "x. ", "x."]:
            with self.subTest(p=p), self.assertRaises(PolicyError): self.ws.path(p)
    def test_no_implicit_overwrite(self):
        self.ws.write("x", "one")
        with self.assertRaises(PolicyError): self.ws.write("x", "two")
        self.assertEqual(self.ws.read("x"), "one")
    def test_explicit_overwrite(self):
        self.ws.write("x", "one")
        self.ws.write("x", "two", overwrite=True)
        self.assertEqual(self.ws.read("x"), "two")
    def test_patch_exactly_one(self):
        self.ws.write("x", "alpha beta")
        self.ws.patch("x", "beta", "gamma")
        self.assertEqual(self.ws.read("x"), "alpha gamma")
    def test_ambiguous_patch_fails(self):
        self.ws.write("x", "a a")
        with self.assertRaises(PolicyError): self.ws.patch("x", "a", "b")
        self.assertEqual(self.ws.read("x"), "a a")
    def test_empty_patch_fails(self):
        self.ws.write("x", "abc")
        with self.assertRaises(PolicyError): self.ws.patch("x", "", "b")
    def test_size_limit_write(self):
        ws = Workspace(self.root / "limited", max_bytes=8)
        with self.assertRaises(PolicyError): ws.write("x", "012345678")
    def test_size_limit_read(self):
        ws = Workspace(self.root / "limited", max_bytes=8)
        (ws.root / "x").write_text("012345678")
        with self.assertRaises(PolicyError): ws.read("x")
    @unittest.skipUnless(os.name == "posix", "symlink creation privileges vary on Windows")
    def test_symlink_escape_blocked(self):
        outside = self.root / "secret.txt"; outside.write_text("secret")
        (self.ws.root / "link").symlink_to(outside)
        with self.assertRaises(PolicyError): self.ws.read("link")
    @unittest.skipUnless(os.name == "posix", "POSIX regression test")
    def test_symlink_directory_blocked(self):
        (self.ws.root / "link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PolicyError): self.ws.write("link/escape", "x")
    @unittest.skipUnless(os.name == "posix", "POSIX hardlink regression test")
    def test_hardlink_blocked(self):
        outside = self.root / "secret"; outside.write_text("secret")
        os.link(outside, self.ws.root / "link")
        with self.assertRaises(PolicyError): self.ws.read("link")
    def test_internal_paths_blocked(self):
        with self.assertRaises(PolicyError): self.ws.write(".agent.log", "x")
    def test_list(self):
        self.ws.write("x.txt", "x")
        self.assertEqual(self.ws.list(), [{"name": "x.txt", "type": "file"}])

class ExecutionTests(Base):
    def test_default_python_disabled(self):
        with self.assertRaises(PolicyError): PythonRunner(self.ws).run("print(1)")
    def test_unsafe_mode_requires_consent(self):
        with self.assertRaises(PolicyError): PythonRunner(self.ws, "unsafe-host")
    def test_real_python_creates_file(self):
        r = PythonRunner(self.ws, "unsafe-host", allow_unsafe_host=True).run(
            "from pathlib import Path\nPath('sum.txt').write_text(str(sum([1,2,3])))\nprint('done')")
        self.assertEqual(r["returncode"], 0)
        self.assertIn("done", r["stdout"])
        self.assertEqual(self.ws.read("sum.txt"), "6")
    def test_real_python_error_reported(self):
        r = PythonRunner(self.ws, "unsafe-host", allow_unsafe_host=True).run("raise ValueError('test error')")
        self.assertNotEqual(r["returncode"], 0)
        self.assertIn("test error", r["stderr"])
    def test_real_python_timeout(self):
        r = PythonRunner(self.ws, "unsafe-host", allow_unsafe_host=True, timeout=.25).run("import time; time.sleep(30)")
        self.assertTrue(r["timed_out"])
    def test_output_is_bounded(self):
        r = run_process([sys.executable, "-c", "print('x'*100000)"], cwd=self.ws.root, max_output=100)
        self.assertTrue(r["output_truncated"])
        self.assertEqual(len(r["stdout"]), 100)
    def test_argv_no_shell_interpolation(self):
        value = "; echo NOT_A_COMMAND && $(whoami)"
        r = run_process([sys.executable, "-c", "import sys; print(sys.argv[1])", value], cwd=self.ws.root)
        self.assertEqual(r["stdout"].strip(), value)
    def test_fixed_command_executes(self):
        r = CommandRunner(self.ws, {"version": [sys.executable, "--version"]}, enabled=True).run("version")
        self.assertEqual(r["returncode"], 0)
        self.assertIn("Python", r["stdout"])
    def test_unknown_command_blocked(self):
        with self.assertRaises(PolicyError): CommandRunner(self.ws, {}, enabled=True).run("rm -rf /")
    def test_command_disabled(self):
        with self.assertRaises(PolicyError): CommandRunner(self.ws, {"x": [sys.executable]}).run("x")
    def test_docker_does_not_fallback(self):
        with patch("local_agent.execution.shutil.which", return_value=None):
            with self.assertRaisesRegex(PolicyError, "no unsafe fallback"):
                PythonRunner(self.ws, "docker").run("print(1)")
    def test_docker_flags_contract_only(self):
        # Command-construction test. This DOES NOT prove Docker isolation works.
        argv = PythonRunner(self.ws, "docker").docker_command("test-container")
        for flag in ["--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pull=never"]:
            self.assertIn(flag, argv)

class RPCTests(Base):
    def test_real_subprocess_echo(self):
        self.assertEqual(self.rpc().request("echo", {"中文": 3}), {"echo": {"中文": 3}})
    def test_notifications_do_not_corrupt_result(self):
        r = self.rpc("notify")
        self.assertEqual(r.request("echo", {"x": 1}), {"echo": {"x": 1}})
        self.assertEqual(len(r.notifications), 1)
    def test_server_ping_handled(self):
        self.assertEqual(self.rpc("server-ping").request("echo", {"x": 1}), {"echo": {"x": 1}})
    def test_bad_json_fails(self):
        with self.assertRaises(RPCError): self.rpc("bad-json").request("echo")
    def test_wrong_id_fails(self):
        with self.assertRaises(RPCError): self.rpc("bad-id").request("echo")
    def test_timeout_closes_process(self):
        r = self.rpc("hang", timeout=.25)
        with self.assertRaisesRegex(RPCError, "timeout"): r.request("echo")
        self.assertIsNotNone(r.process.poll())
    def test_close_idempotent(self):
        r = self.rpc(); r.close(); r.close()
        self.assertIsNotNone(r.process.poll())

class MCPTests(Base):
    def test_real_handshake_and_pagination(self):
        names = [t["name"] for t in self.mcp().list_tools()]
        self.assertEqual(names, ["first", "second"])
    def test_version_mismatch_fails(self):
        with self.assertRaisesRegex(RPCError, "version"): self.mcp("bad-version")
    def test_missing_tools_capability_fails(self):
        with self.assertRaisesRegex(RPCError, "capability"): self.mcp("no-tools")
    def test_repeated_cursor_fails(self):
        with self.assertRaises(RPCError): self.mcp("repeat-cursor").list_tools()
    def test_real_demo_server_statistics(self):
        c = MCPClient([sys.executable, "-u", str(ROOT / "examples/mcp_server.py")], cwd=ROOT)
        self.addCleanup(c.close)
        self.assertEqual(c.list_tools()[0]["name"], "summarize_numbers")
        r = c.call("summarize_numbers", {"values": [232,234,236]})
        self.assertEqual(r["structuredContent"]["mean"], 234)
    def test_real_server_error(self):
        c = MCPClient([sys.executable, "-u", str(ROOT / "examples/mcp_server.py")], cwd=ROOT)
        self.addCleanup(c.close)
        self.assertTrue(c.call("summarize_numbers", {"values": []})["isError"])
    def test_allowlist_and_namespace(self):
        r = Registry(); r.add_mcp("demo", self.mcp(), ["first"])
        self.assertIn("mcp__demo__first", r.tools)
        self.assertNotIn("mcp__demo__second", r.tools)
        self.assertTrue(r.call("mcp__demo__first", {})["ok"])
    def test_mcp_arguments_cannot_override_bound_remote_name(self):
        class Client:
            def list_tools(self): return [{"name":"safe", "inputSchema":{"type":"object"}}]
            def call(self, name, arguments): return {"called_name":name, "arguments":arguments}
        r=Registry(); r.add_mcp("demo", Client(), ["safe"])
        out=r.call("mcp__demo__safe", {"_name":"dangerous"})
        self.assertEqual(out["result"]["called_name"], "safe")
    def test_duplicate_tool_registration_fails(self):
        r=Registry(); r.add_mcp("demo", self.mcp(), ["first"])
        with self.assertRaises(PolicyError): r.add_mcp("demo", self.mcp(), ["first"])

class AgentTests(Base):
    def test_action_json(self):
        self.assertEqual(parse_action('{"final":"ok"}'), {"final":"ok"})
    def test_fenced_action(self):
        self.assertEqual(parse_action('```json\n{"final":"ok"}\n```'), {"final":"ok"})
    def test_completed_think_removed(self):
        self.assertEqual(parse_action('<think>x</think>\n{"final":"ok"}'), {"final":"ok"})
    def test_reject_malformed_or_ambiguous_actions(self):
        for text in ['hello', '{}', '[]', '{"final":"x","tool":"y"}', '{"tool":"x","arguments":[]}',
                     '{"final":"a","final":"b"}', '{"tool":"x","arguments":{"x":NaN}}']:
            with self.subTest(text=text), self.assertRaises(ActionError): parse_action(text)
    def test_tool_types_and_extra_arguments(self):
        r=make_registry(self.ws)
        self.assertFalse(r.call("files.write", {"path":"x","content":42})["ok"])
        self.assertFalse(r.call("files.write", {"path":"x","content":"42","hack":1})["ok"])
        self.assertFalse(r.call("files.write", {"path":"x","content":"42","overwrite":"false"})["ok"])
    def test_unknown_tool_cannot_execute(self):
        self.assertFalse(make_registry(self.ws).call("os.system", {"cmd":"echo bad"})["ok"])
    def test_end_to_end_with_scripted_backend(self):
        backend=ScriptedBackend([{"tool":"files.write","arguments":{"path":"x","content":"hello"}},
                                 {"tool":"files.read","arguments":{"path":"x"}}, {"final":"done"}])
        result=Agent(backend, make_registry(self.ws)).run("test")
        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual(self.ws.read("x"), "hello")
    def test_invalid_json_retry_then_succeed(self):
        a=Agent(ScriptedBackend(["not json", {"final":"ok"}]), make_registry(self.ws))
        self.assertTrue(a.run("test")["ok"])
        self.assertIn("format_error", [x["event"] for x in a.audit.events])
    def test_three_bad_formats_stop(self):
        result=Agent(ScriptedBackend(["x","y","z"]), make_registry(self.ws)).run("test")
        self.assertFalse(result["ok"])
    def test_step_limit(self):
        result=Agent(ScriptedBackend([{"tool":"files.list","arguments":{}}]), make_registry(self.ws), max_steps=1).run("test")
        self.assertFalse(result["ok"])
        self.assertIn("step limit", result["error"])
    def test_repeat_loop_blocked(self):
        action={"tool":"files.list","arguments":{}}
        result=Agent(ScriptedBackend([action]*3), make_registry(self.ws)).run("test")
        self.assertFalse(result["ok"])
        self.assertEqual(result["tool_calls"], 2)
    def test_context_budget(self):
        result=Agent(ScriptedBackend([]), make_registry(self.ws), max_context_chars=1).run("test")
        self.assertFalse(result["ok"])
    def test_audit_written(self):
        log=self.root/"audit.jsonl"
        Agent(ScriptedBackend([{"final":"ok"}]), make_registry(self.ws), audit=Audit(log)).run("test")
        events=[json.loads(s) for s in log.read_text().splitlines()]
        self.assertEqual(events[0]["event"], "start")
        self.assertEqual(events[-1]["event"], "final")

class AdapterTests(Base):
    def fake_model(self):
        model=self.root/"model"; model.mkdir()
        (model/"model.json").write_text("{}")
        return model
    def image_runner(self):
        return ImageRunner(self.ws, {"enabled":True, "command":[sys.executable,str(FIXTURE),"fake-image"],
                                     "model":str(self.fake_model()),"gpu":-1,"timeout":3})
    def test_ncnn_missing_model_fails_without_mock_fallback(self):
        for cls in (NcnnCLIBackend, NcnnBridgeBackend):
            with self.subTest(cls=cls.__name__), self.assertRaises(PolicyError):
                cls({"model":str(self.root/"missing"),"command":["missing"]}, ROOT)
    def test_ncnn_cli_transport_using_fake_process(self):
        backend=NcnnCLIBackend({"model":str(self.fake_model()),"command":[sys.executable,str(FIXTURE),"fake-cli"],"timeout":3}, ROOT)
        text=backend.complete([{"role":"system","content":"line1\nline2"}])
        self.assertEqual(parse_action(text)["final"], "adapter transport test, not inference")
    def test_native_bridge_transport_using_fake_process(self):
        backend=NcnnBridgeBackend({"model":str(self.fake_model()),"command":[sys.executable,str(FIXTURE),"native"],"timeout":3}, ROOT)
        self.addCleanup(backend.release)
        self.assertIn("native protocol fixture", backend.complete([{"role":"user","content":"test"}]))
        backend.release()
        self.assertIsNone(backend.rpc)
    def test_image_cli_arguments(self):
        runner=self.image_runner()
        argv=runner.command(prompt="hello; echo bad",output="out.png",width=512,height=512,steps=40)
        self.assertIn("512,512",argv)
        self.assertEqual(argv[argv.index("-p")+1],"hello; echo bad")
    def test_image_reference_flags(self):
        runner=self.image_runner()
        self.ws.write("ref.png", "fixture")
        argv=runner.command(prompt="edit",output="out.png",references=["ref.png"])
        self.assertIn("-i",argv)
    def test_edit_dimensions_require_32_multiple(self):
        runner=self.image_runner()
        with self.assertRaises(PolicyError): runner.command(prompt="x",output="out.png",width=528,references=["ref.png"])
    def test_image_output_path_escape(self):
        with self.assertRaises(PolicyError): self.image_runner().command(prompt="x",output="../out.png")
    def test_image_output_no_overwrite(self):
        runner=self.image_runner(); self.ws.write("out.png","existing")
        with self.assertRaises(PolicyError): runner.command(prompt="x",output="out.png")
    def test_image_fake_process_contract_not_inference(self):
        result=self.image_runner().run(prompt="fixture",output="out.png")
        self.assertTrue(result["file_created"])
        self.assertIn("FAKE_IMAGE_FIXTURE", result["stdout"])
    def test_missing_image_output_is_error(self):
        runner=self.image_runner()
        with patch("local_agent.images.run_process", return_value={"returncode":0,"timed_out":False}):
            self.assertIn("error", runner.run(prompt="x",output="out.png"))

class CLITests(Base):
    def run_cli(self, *args):
        return run_process([sys.executable,"-m","local_agent",*args], cwd=ROOT,timeout=15,max_output=200000)
    def test_doctor_runs_without_models(self):
        r=self.run_cli("doctor")
        self.assertEqual(r["returncode"],0)
        self.assertFalse(json.loads(r["stdout"])["weights_bundled"])
    def test_demo_requires_consent(self):
        r=self.run_cli("demo","--workspace",str(self.ws.root))
        self.assertNotEqual(r["returncode"],0)
        self.assertIn("--allow-unsafe-host-python",r["stdout"])
    def test_demo_full_actual_tools_scripted_planner(self):
        r=self.run_cli("demo","--workspace",str(self.ws.root),"--allow-unsafe-host-python")
        self.assertEqual(r["returncode"],0,r)
        data=json.loads(r["stdout"])
        self.assertTrue(all(data["integration_checks"].values()))
        self.assertEqual(data["backend"],"SCRIPTED_TEST_DOUBLE_NOT_LLM")
    def test_real_run_missing_weights_is_error(self):
        r=self.run_cli("run","--workspace",str(self.ws.root),"--task","test")
        self.assertNotEqual(r["returncode"],0)
        self.assertIn("no fallback",r["stdout"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
