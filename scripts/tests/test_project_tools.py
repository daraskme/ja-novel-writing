# -*- coding: utf-8 -*-
"""init_project / ledger_lint / build_context / calibrate の結合テスト。作品例は自作（『索道日誌』）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import build_context  # noqa: E402
import calibrate  # noqa: E402
import init_project  # noqa: E402
import ledger_lint  # noqa: E402

BEAT = '''chapter = 1
title = "四月十九日の欄"   # 例: "見本の題"
pov = "C01"
characters = ["C01", "C02"]
locations = []
intent = "ちづるが日誌の食い違いに気づき、所長に言わない"
reader_should = ["筆跡が祖父のものだと知る"]
intensity = 40
ending_type = "行為の途中で切る"
plant = ["F001"]
touch = []
payoff = []

[[scenes]]
title = "機械室"
location = ""
time = "朝七時"
pov = "C01"
characters = ["C01", "C02"]
goal = "日誌を所長に見られずに戻す"   # 例: "見本の goal # を含む"
conflict = "所長が先に来ている"
change = "ちづるは嘘をつく"
anchor = "所長の鍵束"
knowledge = "ちづる: 筆跡に気づいた。所長: 何も知らない"
direction = ""
'''
FORESHADOW = '''[[items]]
id = "F001"
content = "点検日誌の 4/19 の欄が祖父の筆跡"
status = "planned"
planted_ch = 0
payoff_plan_ch = 9
payoff_ch = 0
max_gap = 5
note = "日付を読み上げるだけにする"

[[items]]
id = "F002"
content = "鍵束の鍵が一本足りない"
status = "planted"
planted_ch = 1
payoff_plan_ch = 12
payoff_ch = 0
max_gap = 6
note = ""
'''
CHAPTER = ("　索道の機械室は、朝の点検の前だけ油の匂いが薄く、ちづるは毎朝その短い時間のうちに日誌を開くことにしていた。"
           "四月十九日の欄に、祖父の字があった。祖父が死んだのは四月五日である。\n「所長、これ」\n　言いかけて、やめた。"
           "三枝は鍵束を指で繰りながら、運休の札を裏返しに行った。\n") * 12

STYLE = "# 文体シート@@- 人称: 三人称一元（ちづる）@@- 距離: 寄り気味。内言は地の文に溶かす@@- 基調文末: 過去形基調、状態はル形@@".replace("@@", "\n")


def make_project(root: Path):
    init_project.main([str(root), "--title", "索道日誌", "--profile", "bungei", "--medium", "none"])
    init_project.main([str(root), "--add-character", "C01", "--name", "真壁ちづる"])
    init_project.main([str(root), "--add-character", "C02", "--name", "三枝"])
    (root / "plot" / "beats" / "ch001.toml").write_text(BEAT, encoding="utf-8")
    (root / "plot" / "foreshadowing.toml").write_text(FORESHADOW, encoding="utf-8")
    (root / "style" / "style-sheet.md").write_text(STYLE, encoding="utf-8")


class ProjectFlow(unittest.TestCase):
    def test_init_ledger_pack(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            self.assertTrue((root / "novel.toml").is_file())
            self.assertIn(ledger_lint.main(["--project", str(root)]), (0, 1))
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1"]), 0)
            pack = (root / "work" / "ch001.pack.md").read_text(encoding="utf-8")
            self.assertIn("【この章で張る】F001", pack)          # 当該章で張る伏線が載る
            self.assertIn("【維持中】F002", pack)                # 維持中の伏線は 1 行
            self.assertIn("真壁ちづる", pack)
            self.assertNotIn("例:", pack)                        # 雛形の見本が執筆役の文脈に混ざらない
            self.assertNotIn("見本の goal", pack)
            self.assertIn("このパックから落としたもの", pack)
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1", "--check"]), 0)
            (root / "style" / "style-sheet.md").write_text(STYLE + "- 比喩: 索道の仕事の物から\n", encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1", "--check"]), 1)   # 原典が変われば古い

    def test_budget_never_drops_core_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            # 落とせない部分だけで予算を超えるなら、欠けたパックを作らずに終了コード 2
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1", "--budget", "50"]), 2)
            self.assertFalse((root / "work" / "ch001.pack.md").is_file())
            (root / "plan" / "decisions.md").write_text("# 決定ログ\n\n" + "".join(
                f"## D{i:03d} 2026-09-0{i % 9 + 1} note\n- 内容: 機械室の描写を削る案その {i}。" + "理由の説明が続く。" * 8 + "\n- 理由: 冗長\n- 影響: ch001\n\n"
                for i in range(1, 12)), encoding="utf-8")
            full = build_context.main(["--project", str(root), "--chapter", "1"])
            self.assertEqual(full, 0)
            full_size = build_context.size((root / "work" / "ch001.pack.md").read_text(encoding="utf-8"))
            budget = full_size - 300
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1", "--budget", str(budget)]), 0)
            pack = (root / "work" / "ch001.pack.md").read_text(encoding="utf-8")
            for must in ("## 0 執筆条件", "## 1 章ビート", "## 2 伏線", "## 3 登場人物", "## 4 文体シート"):
                self.assertIn(must, pack)
            self.assertIn(f"予算 {budget} 字を超えたため", pack)
            self.assertNotIn("## 9 決定ログ", pack)
            body = pack[:pack.index("<!-- sources-sha256")]
            self.assertLessEqual(build_context.size(body), budget)      # 予算は完成形で守る

    def test_contract_premise_supersedes_and_future_changes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            novel = (root / "novel.toml").read_text(encoding="utf-8")
            novel = novel.replace('promise = ""', 'promise = "仕事の手つきで解く静かな謎"')
            (root / "novel.toml").write_text(novel, encoding="utf-8")
            (root / "bible" / "world.md").write_text("# 世界設定\n\n## 常時前提\n山頂駅は携帯が圏外。\n\n## 詳細\nここはパックに入らない。\n", encoding="utf-8")
            sheet = next((root / "bible" / "characters").glob("C01-*.md"))
            sheet.write_text(sheet.read_text(encoding="utf-8") + "\n## 変化の記録\n- ch001〜: 日誌を持ち出した\n- ch002〜: 所長に嘘をついた\n"
                             "- ch009〜: 相手の正体を知る\n  正体は父の弟。\n  - 以後は叔父さんと呼ぶ\n- ch010〜: 町を出る\n", encoding="utf-8")
            (root / "style" / "glossary.toml").write_text(
                '[[address]]\nfrom = "C01"\nto = "C02"\ncall = "所長"\n\n'
                '[[address]]\nfrom = "C01"\nto = "C02"\ncall = "三枝さん"\nfrom_ch = 2\n\n'
                '[[address]]\nfrom = "C01"\nto = "C02"\ncall = "叔父さん"\nfrom_ch = 9\n\n[constraints]\nforbidden_words = []\n', encoding="utf-8")
            (root / "plot" / "beats" / "ch003.toml").write_text(BEAT.replace("chapter = 1", "chapter = 3").replace('plant = ["F001"]', "plant = []"), encoding="utf-8")
            (root / "canon" / "facts.jsonl").write_text(
                '{"id":"ch001-01","ch":1,"fact":"小椋は平成二十五年に退職した","chars":["C01"],"status":"confirmed"}\n'
                '{"id":"ch002-01","ch":2,"fact":"小椋は平成二十七年に退職した","chars":["C01"],"status":"confirmed","supersedes":"ch001-01"}\n',
                encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "3"]), 2)      # 前章末の状態が無い
            (root / "canon" / "state").mkdir(parents=True, exist_ok=True)
            (root / "canon" / "state" / "ch002.md").write_text("# 章末の状態 ch002\n- 作中日時: 二月の月曜、夜\n", encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "3"]), 0)
            pack = (root / "work" / "ch003.pack.md").read_text(encoding="utf-8")
            self.assertIn("仕事の手つきで解く静かな謎", pack)
            self.assertIn("山頂駅は携帯が圏外", pack)
            self.assertNotIn("ここはパックに入らない", pack)
            self.assertIn("所長に嘘をついた", pack)
            self.assertNotIn("町を出る", pack)                 # 書く章より先の変化は見せない
            self.assertNotIn("正体は父の弟", pack)             # 補足行・子項目も一緒に落とす
            self.assertNotIn("叔父さん", pack)
            self.assertIn("「三枝さん」", pack)                # 呼称は、書く章より前に始まった最新のもの
            self.assertNotIn("「所長」", pack)
            self.assertIn("平成二十七年", pack)
            self.assertNotIn("平成二十五年", pack)             # 訂正された旧事実は載せない
            self.assertIn("ch002-01", pack)

    def test_missing_required_material_stops(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            (root / "style" / "style-sheet.md").unlink()
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1"]), 2)      # 文体シートなしでは作らない
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1", "--allow-missing"]), 0)
            self.assertIn("欠けている必須資料", (root / "work" / "ch001.pack.md").read_text(encoding="utf-8"))
            (root / "style" / "style-sheet.md").write_text(STYLE, encoding="utf-8")
            (root / "manuscript" / "ch001.md").write_text(CHAPTER, encoding="utf-8")
            (root / "plot" / "beats" / "ch002.toml").write_text(BEAT.replace("chapter = 1", "chapter = 2").replace('plant = ["F001"]', "plant = []"), encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "2"]), 2)      # 前章末の状態が無い
            (root / "canon" / "state").mkdir(parents=True, exist_ok=True)
            (root / "canon" / "state" / "ch001.md").write_text("# 章末の状態 ch001\n- 作中日時:\n<!-- 例: 月曜 -->\n", encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "2"]), 2)      # 見出しと未記入の雛形だけでは足りない
            (root / "canon" / "state" / "ch001.md").write_text("# ch001 章末の状態@@- ちづる: 日誌を鞄に入れたまま帰宅@@".replace("@@", "\n"), encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "2"]), 0)

    def test_short_but_filled_style_sheet_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            (root / "style" / "style-sheet.md").write_text("- 人称: 一人称\n- 距離: 近い\n- 基調文末: 常体\n", encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1"]), 0)
            (root / "style" / "style-sheet.md").write_text("# 文体シート\n- 人称と視点:\n- 語りの距離:\n- 基調文末:\n", encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1"]), 2)      # 見出し語だけの雛形は未記入

    def test_filled_items_ignores_empty_structure(self):
        f = build_context.filled_items
        self.assertEqual(f("# 状態\n-\n- \n・\n"), 0)                                   # 空の箇条書き
        self.assertEqual(f("| 所在 | 持ち物 |\n| --- | --- |\n| | |\n"), 0)            # 未記入の表
        self.assertEqual(f("| 所在 | 持ち物 |\n| --- | --- |\n| 納戸 | 日誌 |\n"), 1)  # データ行だけを数える
        self.assertEqual(f("- 人称:\n- 距離:\n- 基調文末:\n"), 0)
        self.assertEqual(f("- 人称: 一人称\n- 距離: 近い\n- 基調文末: 常体\n"), 3)
        self.assertEqual(f("-\n-\n-\n"), 0)

    def test_empty_structure_state_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            (root / "manuscript" / "ch001.md").write_text(CHAPTER, encoding="utf-8")
            (root / "plot" / "beats" / "ch002.toml").write_text(BEAT.replace("chapter = 1", "chapter = 2").replace('plant = ["F001"]', "plant = []"), encoding="utf-8")
            (root / "canon" / "state").mkdir(parents=True, exist_ok=True)
            for empty in ("# 状態\n-\n", "| 所在 | 持ち物 |\n| --- | --- |\n| | |\n"):
                (root / "canon" / "state" / "ch001.md").write_text(empty, encoding="utf-8")
                self.assertEqual(build_context.main(["--project", str(root), "--chapter", "2"]), 2, empty)

    def test_change_log_table_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            sheet = next((root / "bible" / "characters").glob("C01-*.md"))
            sheet.write_text(sheet.read_text(encoding="utf-8") + "@@## 変化の記録@@| 章 | 内容 |@@| ch012〜 | 町を出る |@@".replace("@@", "\n"), encoding="utf-8")
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "1"]), 2)

    def test_missing_beat_and_missing_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            self.assertEqual(build_context.main(["--project", str(root), "--chapter", "7"]), 2)
            self.assertEqual(build_context.main(["--project", str(Path(d) / "none"), "--chapter", "1"]), 2)

    def test_strip_toml_comment(self):
        f = build_context._strip_toml_comment
        self.assertEqual(f('goal = "a # b"   # 例: "x"').rstrip(), 'goal = "a # b"')
        self.assertEqual(f("plant = []  # 例").rstrip(), "plant = []")


class Calibrate(unittest.TestCase):
    def test_needs_three_chapters_and_keeps_manual_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "sakudo"
            make_project(root)
            files = []
            for i in (1, 2, 3):
                p = root / "manuscript" / f"ch00{i}.md"
                p.write_text(CHAPTER, encoding="utf-8")
                files.append(str(p))
            self.assertEqual(calibrate.main(["--project", str(root)] + files[:2]), 1)
            self.assertFalse((root / "style" / "lint.json").is_file())
            (root / "style" / "lint.json").write_text(
                json.dumps({"overrides": {"R01": {"info": 9, "warn": 12, "reason": "報告体の語り"}}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(calibrate.main(["--project", str(root)] + files), 0)
            data = json.loads((root / "style" / "lint.json").read_text(encoding="utf-8"))
            self.assertEqual(data["overrides"]["R01"]["reason"], "報告体の語り")     # 手書きが勝つ
            self.assertIn("D01", data["overrides"])
            self.assertTrue(all(str(v.get("reason", "")) for v in data["overrides"].values()))
            self.assertEqual(ledger_lint.main(["--project", str(root)]) in (0, 1), True)


if __name__ == "__main__":
    unittest.main()
