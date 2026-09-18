# -*- coding: utf-8 -*-
"""evals/run_blind_eval.py のテスト。生成役と判定役は、このテストが作る小さなスクリプトで代用する（外部の CLI は呼ばない）。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evals"))
import run_blind_eval as rbe  # noqa: E402

# 生成役の代用: 作業ディレクトリに SKILL.md があるかどうかと、見えてはいけない資料の有無を応答に書く。
GEN_STUB = """import sys, os
from pathlib import Path
prompt = sys.stdin.buffer.read().decode("utf-8")
has_skill = Path("SKILL.md").is_file()
leak = [n for n in ("evals", "docs", "README.md", ".git") if Path(n).exists()] + [str(p) for p in Path(".").rglob("tests")]
body = ("　スキルありの応答。" if has_skill else "　スキルなしの応答。") + "彼は「好きだ」と言った。" + ("漏れ:" + ",".join(leak) if leak else "")
Path(sys.argv[1]).write_text(body + "\\n", encoding="utf-8")
"""
# 判定役の代用。mode=skill なら「スキルあり」と書かれた応答を選ぶ。mode=always-a なら常に A。
JUDGE_STUB = """import sys, re, os
from pathlib import Path
req = sys.stdin.buffer.read().decode("utf-8")
mode = sys.argv[2]
a = req.split("## 応答 A")[1].split("## 応答 B")[0]
if mode == "always-a":
    pref = "A"
elif mode == "garbage":
    pref = None
else:
    pref = "A" if "スキルあり" in a else "B"
seen = sorted(os.listdir("."))
text = "1. 条件\\n2. 理由\\n作業ディレクトリ: " + repr(seen) + "\\n" + ("選好: " + pref if pref else "決められない") + "\\n"
Path(sys.argv[1]).write_text(text, encoding="utf-8")
"""


class BlindEvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rbe-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "gen_stub.py").write_text(GEN_STUB, encoding="utf-8")
        (self.tmp / "judge_stub.py").write_text(JUDGE_STUB, encoding="utf-8")
        py = Path(sys.executable).as_posix()
        self.generator = f'"{py}" "{(self.tmp / "gen_stub.py").as_posix()}" {{out}}'
        self.judge = f'"{py}" "{(self.tmp / "judge_stub.py").as_posix()}" {{out}}'
        self.run_dir = self.tmp / "run"

    def generate(self, *extra):
        return rbe.main(["generate", "--run", str(self.run_dir), "--arm", "with=skill", "--arm", "base=none",
                         "--only", "graduation-confession,flash-talkative-comedy", "--trials", "2",
                         "--generator", self.generator, "--jobs", "4", *extra])

    def test_full_cycle(self):
        self.assertEqual(self.generate(), 0)
        run = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(len(run["generations"]), 8)
        self.assertEqual([p["name"] for p in run["prompts"]], ["graduation-confession", "flash-talkative-comedy"])
        self.assertTrue(all(len(p["hash"]) == 12 for p in run["prompts"]))
        with_text = (self.run_dir / "gen/graduation-confession/t1/with.md").read_text(encoding="utf-8")
        base_text = (self.run_dir / "gen/graduation-confession/t1/base.md").read_text(encoding="utf-8")
        self.assertIn("スキルあり", with_text)
        self.assertIn("スキルなし", base_text)
        self.assertNotIn("漏れ", with_text)      # evals/・docs/・tests/ はスキルの系統へ写さない
        self.assertNotIn("漏れ", base_text)

        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill", "--jobs", "4"]), 0)
        assign = json.loads((self.run_dir / "assign.json").read_text(encoding="utf-8"))
        firsts = [v["order1"][0] for v in assign.values()]
        self.assertEqual(sorted(firsts), ["base", "base", "with", "with"])      # 1 回目の提示順は全体で半々
        request = (self.run_dir / "judge/graduation-confession/t1/order1.request.md").read_text(encoding="utf-8")
        for hidden in ("with", "base", "skill", "codex", "expected", "lint", "スキル" + "の系統"):
            self.assertNotIn(hidden, request.replace("スキルありの応答", "").replace("スキルなしの応答", ""), hidden)
        self.assertIn("余韻", request)           # 告白の問い
        self.assertNotIn("笑い", request)        # 修正の意図を教える問いは入れない
        verdict = (self.run_dir / "judge/graduation-confession/t1/order1.md").read_text(encoding="utf-8")
        self.assertIn("作業ディレクトリ: []", verdict)      # 判定役は空のディレクトリで動く

        self.assertEqual(rbe.main(["report", "--run", str(self.run_dir)]), 0)
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual([r["result"] for r in results["rows"]], ["with"] * 4)
        row = results["rows"][0]
        self.assertEqual(row["arms"]["with"]["length"], "不足")       # 1200〜1500 字の指定に対して短い
        self.assertEqual(results["rows"][2]["arms"]["with"]["length"], "指定なし")
        self.assertIn("lint", row)
        report = (self.run_dir / "results.md").read_text(encoding="utf-8")
        self.assertIn("探索的比較", report)
        self.assertNotIn("勝率 ", report.replace("勝率や", "").replace("勝敗や勝率", "").replace("言えない: 勝率", ""))
        self.assertNotIn("%", report)

    def test_order_instability_and_unreadable_verdicts(self):
        self.assertEqual(self.generate(), 0)
        rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " always-a"])
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"順序不安定"})
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " garbage", "--force", "--retries", "0"]), 0)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"失敗"})

    def test_resume_skips_finished_generations(self):
        self.assertEqual(self.generate(), 0)
        marker = self.run_dir / "gen/graduation-confession/t1/with.md"
        marker.write_text("手で直した応答", encoding="utf-8")
        self.assertEqual(self.generate(), 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "手で直した応答")

    def test_failed_generator_is_recorded(self):
        bad = f'"{Path(sys.executable).as_posix()}" -c "import sys; sys.exit(3)" {{out}}'
        code = rbe.main(["generate", "--run", str(self.run_dir), "--arm", "with=skill", "--arm", "base=none",
                         "--only", "graduation-confession", "--generator", bad, "--retries", "1"])
        self.assertEqual(code, 1)
        run = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(all(not g["ok"] and len(g["attempts"]) == 2 for g in run["generations"]))
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual([r["result"] for r in results["rows"]], ["生成失敗"])

    def test_argument_errors(self):
        self.assertEqual(rbe.main(["generate", "--run", str(self.run_dir), "--arm", "with=skill"]), 2)
        self.assertEqual(rbe.main(["generate", "--run", str(self.run_dir), "--arm", "a=skill", "--arm", "b=none", "--only", "no-such"]), 2)
        self.assertEqual(rbe.main(["generate", "--run", str(ROOT / "evals" / "tmp-run"), "--arm", "a=skill", "--arm", "b=none"]), 2)
        self.assertEqual(rbe.main(["judge", "--run", str(self.tmp / "nothing")]), 2)

    def test_prompts_are_named_and_hashed(self):
        prompts = rbe.load_prompts(ROOT / "evals" / "blind_prompts.json", [])
        names = [p["name"] for p in prompts]
        self.assertEqual(len(names), len(set(names)))
        grad = next(p for p in prompts if p["name"] == "graduation-confession")
        station = next(p for p in prompts if p["name"] == "romance-direct-confession")
        self.assertIn("卒業式", grad["text"])
        self.assertIn("駅前", station["text"])
        self.assertNotEqual(grad["hash"], station["hash"])
        self.assertTrue(all("expected_output" not in p for p in prompts))

    def test_settle(self):
        self.assertEqual(rbe.settle("with", "with"), "with")
        self.assertEqual(rbe.settle("差なし", "差なし"), "差なし")
        self.assertEqual(rbe.settle("with", "base"), "順序不安定")
        self.assertEqual(rbe.settle("with", "差なし"), "順序不安定")
        self.assertEqual(rbe.settle("with", "判定不能"), "判定不能")
        self.assertEqual(rbe.settle(None, "with"), "失敗")
        self.assertEqual(rbe.parse_preference("理由…\n選好: A\n追記\n選好：Ｂ"), "B")
        self.assertIsNone(rbe.parse_preference("どちらとも言えない"))
        self.assertEqual(rbe.parse_preference("4. **選好: 差なし**"), "差なし")
        self.assertEqual(rbe.parse_preference("- `選好: A`"), "A")
        # 指示文を写しただけの行は選好ではない
        self.assertIsNone(rbe.parse_preference("最後の行に `選好: A` / `選好: B` / `選好: 差なし` / `選好: 判定不能` のどれかを書く"))
        self.assertEqual(rbe.parse_preference("選好: B\n形式は `選好: A` / `選好: B` のどれか、とのことでした"), "B")


if __name__ == "__main__":
    unittest.main()
