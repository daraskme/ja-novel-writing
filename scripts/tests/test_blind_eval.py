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
if mode in ("always-a", "exit3"):
    pref = "A"
elif mode == "garbage":
    pref = None
else:
    pref = "A" if "スキルあり" in a else "B"
seen = sorted(os.listdir("."))
text = "1. 条件\\n2. 理由\\n作業ディレクトリ: " + repr(seen) + "\\n" + ("選好: " + pref if pref else "今回は結論を出せません。\\n応答からの引用：\\n> 選好: A") + "\\n"
Path(sys.argv[1]).write_text(text, encoding="utf-8")
if mode == "exit3":
    sys.exit(3)      # 選好を書いてから失敗で終わる
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
        # 引用された「> 選好: A」しか無い判定は、読めない判定として失敗に数える（終了コードは 0 でも）
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " garbage", "--force", "--retries", "0"]), 1)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"失敗"})

    def test_failed_judge_with_a_written_verdict_is_not_adopted(self):
        """判定役が選好を書いてから失敗で終わっても、その選好は採用しない。次の judge でも済んだものとして飛ばさない。"""
        self.assertEqual(self.generate(), 0)
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " exit3", "--retries", "0"]), 1)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"失敗"})
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"with"})

    def test_old_verdicts_are_not_attached_to_new_texts(self):
        """応答を作り直したら、前の応答への判定は使わない（judge を走らせ直す前の report では失敗扱い。judge は読み直す）。"""
        self.assertEqual(self.generate(), 0)
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        # 生成役を差し替えて作り直す（系統の名前は同じ）
        other = self.tmp / "gen_stub2.py"
        other.write_text(GEN_STUB.replace("スキルありの応答", "別の版の応答").replace("スキルなしの応答", "スキルありの応答"), encoding="utf-8")
        generator2 = self.generator.replace("gen_stub.py", "gen_stub2.py")
        self.assertEqual(rbe.main(["generate", "--run", str(self.run_dir), "--arm", "with=skill", "--arm", "base=none",
                                   "--only", "graduation-confession,flash-talkative-comedy", "--trials", "2", "--generator", generator2]), 0)
        run = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual({g["generator"] for g in run["generations"]}, {generator2})      # コマンドが変わった応答は使い回さない
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"失敗"})               # 古い判定は新しい本文に結び付かない
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"base"})               # 作り直した本文では「スキルあり」と書くのは base 側

    def test_hand_edited_response_is_regenerated(self):
        self.assertEqual(self.generate(), 0)
        target = self.run_dir / "gen/graduation-confession/t1/with.md"
        target.write_text("手で直した応答", encoding="utf-8")
        self.assertEqual(self.generate(), 0)
        self.assertIn("スキルありの応答", target.read_text(encoding="utf-8"))

    def test_run_dir_inside_another_arm_is_refused(self):
        old = self.tmp / "old-skill"
        (old / "references").mkdir(parents=True)
        (old / "SKILL.md").write_text("# old", encoding="utf-8")
        code = rbe.main(["generate", "--run", str(old / "references" / "blind-run"), "--arm", "new=skill", "--arm", f"old={old}",
                         "--only", "graduation-confession", "--generator", self.generator])
        self.assertEqual(code, 2)

    def test_audit_flags_forbidden_names_in_logs(self):
        self.assertEqual(self.generate(), 0)
        rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"])
        log = self.run_dir / "gen/graduation-confession/t1/base.log"
        log.write_text(log.read_text(encoding="utf-8") + "\nexec: type ..\\..\\x\\ja-novel-writing\\SKILL.md\n", encoding="utf-8")
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual([a["log"] for a in results["audit"]["found"]], ["gen/graduation-confession/t1/base.log"])
        self.assertIn("人が読んで確かめる", (self.run_dir / "results.md").read_text(encoding="utf-8"))

    def test_audit_reports_missing_logs_as_unverifiable(self):
        """ログが無いのに「該当なし」とは言わない（Codex レビュー 12）。"""
        self.assertEqual(self.generate(), 0)
        rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"])
        rbe.main(["report", "--run", str(self.run_dir)])
        report = (self.run_dir / "results.md").read_text(encoding="utf-8")
        self.assertIn("12 本を点検して該当なし", report)      # スキルなしの生成 4 本 + 判定 8 本
        for log in self.run_dir.rglob("*.log"):
            log.unlink()
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(len(results["audit"]["missing"]), 12)
        report = (self.run_dir / "results.md").read_text(encoding="utf-8")
        self.assertIn("ログ欠落 12 本は確認不能", report)
        self.assertNotIn("該当なし", report)

    def test_edited_response_is_not_judged_or_reported(self):
        """generate を挟まずに応答の本文を書き換えても、元の生成条件の成果としては扱わない（Codex レビュー 12）。"""
        self.assertEqual(self.generate(), 0)
        target = self.run_dir / "gen/graduation-confession/t1/with.md"
        target.write_text("あとから書き換えた応答。スキルありの応答。", encoding="utf-8")
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 1)
        self.assertFalse((self.run_dir / "judge/graduation-confession/t1/order1.md").exists())      # その対は読ませない
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        by_key = {(r["prompt"], r["trial"]): r["result"] for r in results["rows"]}
        self.assertIn("本文が、記録したときと違う", by_key[("graduation-confession", 1)])
        self.assertEqual(by_key[("graduation-confession", 2)], "with")

    def test_edited_verdict_and_other_judge_command_are_not_reported(self):
        self.assertEqual(self.generate(), 0)
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        verdict = self.run_dir / "judge/graduation-confession/t1/order1.md"
        text = verdict.read_text(encoding="utf-8")
        flipped = text.replace("選好: A", "選好: X").replace("選好: B", "選好: A").replace("選好: X", "選好: B")
        verdict.write_text(flipped, encoding="utf-8")      # meta は元のまま
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        by_key = {(r["prompt"], r["trial"]): r["result"] for r in results["rows"]}
        self.assertEqual(by_key[("graduation-confession", 1)], "失敗")
        self.assertEqual(by_key[("graduation-confession", 2)], "with")
        # 再開すると、その判定だけ読み直す
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        self.assertEqual(verdict.read_text(encoding="utf-8"), text)
        # run.json の判定コマンドと meta の判定コマンドが違えば、その判定は集計しない
        run_path = self.run_dir / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["judge"]["command"] = "another-judge {out}"
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"失敗"})

    def test_resume_skips_finished_generations(self):
        self.assertEqual(self.generate(), 0)
        before = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))["generations"]
        self.assertEqual(self.generate(), 0)
        after = json.loads((self.run_dir / "run.json").read_text(encoding="utf-8"))["generations"]
        self.assertEqual(before, after)      # 同じ条件なら作り直さない（試行の記録もそのまま）

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

    def test_edited_prompt_text_is_detected(self):
        """run.json の依頼の本文だけを書き換えても、前の依頼への応答を新しい依頼への応答として扱わない（Codex レビュー 13）。"""
        self.assertEqual(self.generate(), 0)
        run_path = self.run_dir / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["prompts"][0]["text"] = "別の依頼。ホラーを書いて。"
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 1)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        by_prompt = {}
        for r in results["rows"]:
            by_prompt.setdefault(r["prompt"], set()).add(r["result"])
        self.assertEqual(by_prompt["graduation-confession"], {"生成の記録が不整合（依頼文が違う）"})
        self.assertEqual(by_prompt["flash-talkative-comedy"], {"with"})

    def test_audit_keeps_verdict_logs_of_pairs_dropped_from_the_tally(self):
        """判定のあとで生成の記録が不整合になっても、その対の判定のログは点検の対象のまま（Codex レビュー 14）。"""
        self.assertEqual(self.generate(), 0)
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        run_path = self.run_dir / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["prompts"][0]["text"] = "別の依頼。"
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["audit"]["expected"], 12)
        self.assertEqual(results["audit"]["missing"], [])

    def test_force_does_not_inherit_the_migration_note(self):
        self.assertEqual(self.generate(), 0)
        run_path = self.run_dir / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["migrated"] = "旧実行器の記録"
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.generate(), 0)
        self.assertIn("migrated", json.loads(run_path.read_text(encoding="utf-8")))
        self.assertEqual(self.generate("--force"), 0)
        self.assertNotIn("migrated", json.loads(run_path.read_text(encoding="utf-8")))

    def test_resuming_generate_keeps_valid_verdicts(self):
        """同じ条件で generate を再開しても、済んだ判定は有効なまま（Codex レビュー 13 の回帰）。"""
        self.assertEqual(self.generate(), 0)
        self.assertEqual(rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " skill"]), 0)
        self.assertEqual(self.generate(), 0)
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual({r["result"] for r in results["rows"]}, {"with"})

    def test_audit_expects_logs_of_failed_verdicts_too(self):
        self.assertEqual(self.generate(), 0)
        rbe.main(["judge", "--run", str(self.run_dir), "--judge", self.judge + " garbage", "--retries", "0"])
        for log in (self.run_dir / "judge").rglob("*.log"):
            log.unlink()
        rbe.main(["report", "--run", str(self.run_dir)])
        results = json.loads((self.run_dir / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["audit"]["expected"], 12)
        self.assertEqual(len(results["audit"]["missing"]), 8)      # 失敗した判定 8 本のログも「確認不能」に数える
        self.assertIn("ログ欠落 8 本は確認不能", (self.run_dir / "results.md").read_text(encoding="utf-8"))

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
        self.assertEqual(rbe.parse_preference("理由…\n選好: A\n追記\n選好：Ｂ\n\n"), "B")
        self.assertIsNone(rbe.parse_preference("どちらとも言えない"))
        self.assertEqual(rbe.parse_preference("4. **選好: 差なし**"), "差なし")
        self.assertEqual(rbe.parse_preference("- `選好: A`"), "A")
        # 指示文を写しただけの行は選好ではない
        self.assertIsNone(rbe.parse_preference("最後の行に `選好: A` / `選好: B` / `選好: 差なし` / `選好: 判定不能` のどれかを書く"))
        # 最終行が選好でなければ、途中の一致へは戻らない
        self.assertIsNone(rbe.parse_preference("選好: B\n形式は `選好: A` / `選好: B` のどれか、とのことでした"))
        self.assertIsNone(rbe.parse_preference("今回は結論を出せません。\n応答からの引用：\n> 選好: A"))
        self.assertIsNone(rbe.parse_preference("結論は出せません。\n```\n選好: A\n```"))
        self.assertEqual(rbe.parse_preference("```\n選好: A\n```\n選好: 判定不能"), "判定不能")
        # 閉じていないフェンス、長いフェンス、字下げされたコード行（Codex レビュー 12）
        self.assertIsNone(rbe.parse_preference("引用:\n```\n選好: A"))
        self.assertIsNone(rbe.parse_preference("引用:\n~~~\n選好: A"))
        self.assertIsNone(rbe.parse_preference("引用:\n````\n```\n選好: A\n```"))
        self.assertIsNone(rbe.parse_preference("引用:\n    選好: A"))
        self.assertIsNone(rbe.parse_preference("引用:\n\t選好: A"))
        self.assertIsNone(rbe.parse_preference("引用:\n\n \t選好: A"))      # 空白とタブの混在も桁数で見る（Codex レビュー 13）
        self.assertIsNone(rbe.parse_preference("引用:\n   \t選好: A"))
        self.assertIsNone(rbe.parse_preference("引用:\n  \t 選好: A"))
        self.assertEqual(rbe.parse_preference("引用:\n~~~\n選好: A\n~~~\n   - 選好: B"), "B")


if __name__ == "__main__":
    unittest.main()
