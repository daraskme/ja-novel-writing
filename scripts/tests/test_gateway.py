# -*- coding: utf-8 -*-
"""evals/gateway.py のテスト。ネットワークには出ない（鍵の探し方と伏せ方、入れ子の Claude Code の起動の組み立て、記録の読み取り、時間切れの後始末）。"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
import gateway  # noqa: E402

FAKE_KEY = "vck_TESTKEY_0123456789abcdefghijklmnopqrstuvwxyz"


class KeyLookup(unittest.TestCase):
    def test_env_wins_and_missing_key_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": " k-env ", "AI_GATEWAY_KEY_FILE": str(Path(tmp) / "none")}):
                self.assertEqual(gateway.load_key(), "k-env")
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "", "AI_GATEWAY_KEY_FILE": str(Path(tmp) / "none")}):
                with self.assertRaises(gateway.GatewayError):
                    gateway.load_key()
                with mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(gateway.main(["credits"]), 2)

    def test_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key"
            path.write_text("k-file\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "", "AI_GATEWAY_KEY_FILE": str(path)}):
                self.assertEqual(gateway.load_key(), "k-file")

    def test_key_file_inside_the_repository_is_refused(self):
        path = ROOT / "evals" / "_test-key-do-not-commit"
        path.write_text("k-repo", encoding="utf-8")
        try:
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "", "AI_GATEWAY_KEY_FILE": str(path)}):
                with self.assertRaises(gateway.GatewayError):
                    gateway.load_key()
        finally:
            path.unlink()


class Redaction(unittest.TestCase):
    """鍵は、切り詰めや JSON 化の前に伏せる。応答・エラー・使用量の記録・道具の一覧のどこにも出さない（Codex レビュー 15）。"""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": FAKE_KEY})
        patcher.start()
        self.addCleanup(patcher.stop)
        gateway.load_key()
        self.addCleanup(setattr, gateway, "_KEY", "")

    def chat(self, fake_request, usage_path):
        out, err = io.StringIO(), io.StringIO()
        stdin = mock.Mock(buffer=io.BytesIO("依頼".encode("utf-8")))
        with mock.patch.object(gateway, "request_json", fake_request), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), \
                mock.patch("sys.stdin", stdin):
            code = gateway.main(["chat", "--model", "m", "--usage-log", str(usage_path)])
        return code, out.getvalue(), err.getvalue()

    def test_error_text_and_usage_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = Path(tmp) / "usage.jsonl"

            def boom(*a, **k):
                raise RuntimeError(f"HTTP 401: bad token {FAKE_KEY}")
            code, out, err = self.chat(boom, usage)
            self.assertEqual(code, 1)
            for text in (out, err, usage.read_text(encoding="utf-8")):
                self.assertNotIn(FAKE_KEY, text)
                self.assertNotIn(FAKE_KEY[:20], text)
            self.assertIn("<key>", err)

    def test_successful_response_containing_the_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self.chat(lambda *a, **k: {"choices": [{"message": {"content": f"鍵は {FAKE_KEY} です"}}], "usage": {"note": FAKE_KEY}},
                                       Path(tmp) / "usage.jsonl")
            self.assertEqual(code, 0)
            self.assertNotIn(FAKE_KEY, out)
            self.assertIn("<key>", out)
            self.assertNotIn(FAKE_KEY, (Path(tmp) / "usage.jsonl").read_text(encoding="utf-8"))

    def test_http_error_body_is_redacted_before_truncation(self):
        body = ("x" * 380 + FAKE_KEY).encode("utf-8")      # 400 字で切ると、鍵の前半だけが残る位置
        error = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(body))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                gateway.request_json("https://example.invalid", FAKE_KEY, retries=1)
        self.assertNotIn(FAKE_KEY[:10], str(ctx.exception))

    def test_tool_inputs_are_redacted_before_truncation(self):
        command = "echo " + "x" * 270 + FAKE_KEY
        raw = json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}})
        _, tools = gateway.parse_stream(raw)
        self.assertNotIn(FAKE_KEY[:8], tools[0])


class NestedClaudeCode(unittest.TestCase):
    def args(self, *extra):
        return gateway.build_parser().parse_args(["claude-code", "--model", "anthropic/claude-sonnet-5", *extra])

    def allowed(self, cmd):
        return cmd[cmd.index("--allowedTools") + 1:cmd.index("--permission-mode")]

    def test_command_limits_tools_and_budget(self):
        cwd = Path(tempfile.gettempdir()) / "w123" / "ja-novel-writing"
        cmd = gateway.claude_command(self.args(), "claude", cwd)
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "2.0")
        for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        allowed = self.allowed(cmd)
        self.assertNotIn("Bash", allowed)                 # 何でも実行できる許可は渡さない
        self.assertNotIn("Bash(python:*)", allowed)       # 任意の python は既定では通さない（python の中では権限の制限が効かない）
        self.assertNotIn("Write", allowed)                # 書き込みは確認なしの許可リストに入れない（権限モードの範囲に任せる）
        self.assertIn("Bash(python -X utf8 scripts/novel_lint.py:*)", allowed)
        self.assertIn(f'Bash(python -X utf8 "{cwd.as_posix()}/scripts/novel_lint.py":*)', allowed)
        self.assertIn(f"Bash(python {cwd}{os.sep}scripts{os.sep}count_chars.py:*)", allowed)
        self.assertFalse(any("rm" in a or "curl" in a or "pip" in a for a in allowed))

    def test_any_python_is_an_explicit_opt_in(self):
        allowed = self.allowed(gateway.claude_command(self.args("--allow-any-python"), "claude", Path(".")))
        self.assertIn("Bash(python:*)", allowed)
        self.assertIn("信頼できる入力にだけ", gateway.build_parser().format_help() + " ".join(
            a.help or "" for a in gateway.build_parser()._subparsers._group_actions[0].choices["claude-code"]._actions))

    def test_env_routes_to_the_gateway_without_leaking_host_session_vars(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "host-key", "AI_GATEWAY_API_KEY": "K", "AI_GATEWAY_KEY_FILE": "x"}):
            env = gateway.claude_env("K", "token")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://ai-gateway.vercel.sh/claude-code")
        self.assertEqual((env["ANTHROPIC_AUTH_TOKEN"], env["ANTHROPIC_API_KEY"]), ("K", ""))
        for name in ("CLAUDECODE", "AI_GATEWAY_API_KEY", "AI_GATEWAY_KEY_FILE"):
            self.assertNotIn(name, env)

    def test_stream_parser_is_type_tolerant(self):
        raw = "\n".join([
            '{"type":"system","subtype":"init","message":"hello"}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"x"},{"type":"tool_use","name":"Read","input":{"file_path":"SKILL.md"}}]}}',
            '{"type":"user","message":{"content":"plain string content"}}',
            '{"type":"assistant","message":{"content":null}}',
            "not json", "[1,2]", '"just a string"',
            '{"type":"result","result":"本文","total_cost_usd":0.1,"num_turns":3,"is_error":false}',
        ])
        res, tools = gateway.parse_stream(raw)
        self.assertEqual(res["result"], "本文")
        self.assertEqual(tools, ['[tool] Read {"file_path": "SKILL.md"}'])
        self.assertEqual(gateway.parse_stream(""), ({}, []))

    def test_timeout_kills_the_whole_process_tree(self):
        """制限時間を過ぎたら、子が起動した孫も残さない。"""
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "grandchild-alive.txt"
            grandchild = f"import time, pathlib; time.sleep(4); pathlib.Path({str(marker)!r}).write_text('alive')"
            child = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(30)"
            started = time.time()
            code, _, _ = gateway.run_tree([sys.executable, "-c", child], b"", 1, dict(os.environ))
            self.assertIsNone(code)
            self.assertLess(time.time() - started, 15)
            time.sleep(5)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
