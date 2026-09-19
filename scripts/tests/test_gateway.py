# -*- coding: utf-8 -*-
"""evals/gateway.py のテスト。ネットワークには出ない（鍵の探し方、入れ子の Claude Code の起動の組み立て、記録の読み取りだけ）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
import gateway  # noqa: E402


class KeyLookup(unittest.TestCase):
    def test_env_wins_and_missing_key_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": " k-env ", "AI_GATEWAY_KEY_FILE": str(Path(tmp) / "none")}):
                self.assertEqual(gateway.load_key(), "k-env")
            with mock.patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "", "AI_GATEWAY_KEY_FILE": str(Path(tmp) / "none")}):
                with self.assertRaises(gateway.GatewayError):
                    gateway.load_key()
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


class NestedClaudeCode(unittest.TestCase):
    def args(self, *extra):
        args = gateway.build_parser().parse_args(["claude-code", "--model", "anthropic/claude-sonnet-5", *extra])
        args.allowed_tool = args.allowed_tool or list(gateway.DEFAULT_ALLOWED)
        return args

    def test_command_limits_tools_and_budget(self):
        cmd = gateway.claude_command(self.args(), "claude")
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "acceptEdits")      # ファイルの作成は作業ディレクトリの中だけ通る
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "2.0")
        for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        allowed = cmd[cmd.index("--allowedTools") + 1:cmd.index("--permission-mode")]
        self.assertIn("Bash(python:*)", allowed)
        self.assertNotIn("Bash", allowed)              # 何でも実行できる許可は渡さない
        self.assertNotIn("Write", allowed)             # 書き込みは確認なしの許可リストに入れない（権限モードの範囲に任せる）
        self.assertFalse(any(a.startswith("Bash(rm") or a.startswith("Bash(curl") for a in allowed))

    def test_env_routes_to_the_gateway_without_leaking_host_session_vars(self):
        with mock.patch.dict(os.environ, {"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "host-key"}):
            env = gateway.claude_env("K", "token")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://ai-gateway.vercel.sh/claude-code")
        self.assertEqual((env["ANTHROPIC_AUTH_TOKEN"], env["ANTHROPIC_API_KEY"]), ("K", ""))
        self.assertNotIn("CLAUDECODE", env)

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


if __name__ == "__main__":
    unittest.main()
