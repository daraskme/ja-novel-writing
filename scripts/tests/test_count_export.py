# -*- coding: utf-8 -*-
"""count_chars.py と export.py のテスト（標準ライブラリの unittest のみ）。

実行: scripts/ で `python -m unittest discover -s tests -v`
      または `python -m unittest tests.test_count_export`（scripts/ をカレントにして）。

サンプル文はすべてこのテスト用の自作で、実在作品の文章は使っていない。
"""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import count_chars  # noqa: E402
import export  # noqa: E402

try:  # novel.toml を読むのは Python 3.11 以降（tomllib）。古い Python では該当テストだけ飛ばす
    import tomllib  # noqa: F401
    HAS_TOMLLIB = True
except ModuleNotFoundError:
    HAS_TOMLLIB = False

FW = chr(0x3000)  # 全角空白。目で見分けられないのでコードポイントで書く

# 内部記法をひととおり含む原稿。行番号はテストから参照する。
SAMPLE = "\n".join([
    "# 第一話　岬の灯",                                          # 1 見出し
    "",                                                          # 2
    "岬の｜灯台《とうだい》は、冬になると《《音》》を変える。",  # 3 地の文（ルビ・傍点）
    FW + "真帆はそれを知っていた。",                             # 4 地の文（字下げ済み）
    "「今夜は荒れるよ」",                                        # 5 会話
    "（嘘だ、と思った）",                                        # 6 心内語
    "",                                                          # 7
    "＊",                                                        # 8 場面転換
    "",                                                          # 9
    "《《あれ》》が来たのは夜半だった。",                        # 10 傍点で始まる地の文
    "『聞こえる？』",                                            # 11 二重鉤
    "",
])


def run_cli(module, argv):
    """main() を呼び、(終了コード, 標準出力, 標準エラー) を返す。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = module.main(argv)
        except SystemExit as e:  # argparse の引数エラーは SystemExit(2)
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TempDirCase(unittest.TestCase):
    """一時ディレクトリに原稿を書くテストの土台。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text, newline="\n", bom=False):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.replace("\n", newline).encode("utf-8")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
        return path


# ===========================================================================
# 共有定義
# ===========================================================================
class SharedNotationTest(unittest.TestCase):
    """内部記法の定義は 2 本のスクリプトに同じものがある。ずれると字数と変換が食い違う。"""

    def test_constants_are_identical(self):
        for name in ("FW_SPACE", "KANJI", "OPEN_BRACKETS", "DIALOGUE_BRACKETS"):
            self.assertEqual(getattr(count_chars, name), getattr(export, name), name)

    def test_regexes_are_identical(self):
        for name in ("EMPHASIS_RE", "RUBY_RE", "BARE_RUBY_RE", "HEADING_RE", "SCENE_BREAK_RE",
                     "SILENT_PROSE_RE"):
            self.assertEqual(getattr(count_chars, name).pattern, getattr(export, name).pattern, name)

    def test_line_classification_agrees(self):
        lines = SAMPLE.split("\n") + ["◇", "【注記】", "〈声〉がした。", "＊　＊　＊", FW + "「字下げされた会話」",
                                     "……", "――――", "！？"]
        for line in lines:
            self.assertEqual(count_chars.classify_line(line), export.classify_line(line), line)


class ClassifyLineTest(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "": "blank",
            FW: "blank",
            "# 題": "heading",
            "＊": "scene",
            "＊　＊　＊": "scene",
            "◇": "symbol",
            "「やあ」": "dialogue",
            "『やあ』": "dialogue",
            "（まさか）": "bracket",
            "【速報】": "bracket",
            "朝だった。": "narration",
            FW + "朝だった。": "narration",
            # 《《 は傍点つきの語で始まる地の文。括弧始まりと取り違えると字下げが落ちる。
            "《《それ》》は動いた。": "narration",
            # 沈黙・絶句だけの行は地の文。ダッシュだけの罫線や ※ は飾り。
            "……": "narration",
            "――……": "narration",
            "！？": "narration",
            "――――――": "symbol",
            "※": "symbol",
        }
        for line, kind in cases.items():
            self.assertEqual(export.classify_line(line), kind, repr(line))


# ===========================================================================
# count_chars.py
# ===========================================================================
class StripMarkupTest(unittest.TestCase):
    def test_ruby_keeps_parent_only(self):
        self.assertEqual(count_chars.strip_markup("｜灯台《とうだい》の下"), "灯台の下")

    def test_halfwidth_bar_is_accepted(self):
        self.assertEqual(count_chars.strip_markup("|灯台《とうだい》"), "灯台")

    def test_emphasis_keeps_word(self):
        self.assertEqual(count_chars.strip_markup("《《音》》を変える"), "音を変える")

    def test_emphasis_is_processed_before_ruby(self):
        # 順序が逆だと「岬《《音》》」の内側の《音》が省略形ルビとして消える。
        self.assertEqual(count_chars.strip_markup("岬《《音》》がする"), "岬音がする")

    def test_bare_ruby_is_removed(self):
        self.assertEqual(count_chars.strip_markup("灯台《とうだい》へ"), "灯台へ")


class CountModesTest(unittest.TestCase):
    TEXT = "\n".join([
        "# 題名",                # 見出し（題 2 字）
        "",
        FW + "朝だ。",            # 字下げ 1 + 3 字
        "「｜海《うみ》へ」",     # 「海へ」= 4 字
        "＊",                     # 場面転換
        "《《光》》る。",         # 光る。= 3 字
        "",
    ])

    def test_body_counts_prose_only(self):
        # 3 + 4 + 3。見出し・場面転換・空白・記法は数えない。
        self.assertEqual(count_chars.count_body(self.TEXT), 10)

    def test_narou_excludes_space_newline_ruby(self):
        # body に場面転換の ＊ 1 字が加わる。見出しは題欄へ回すので数えない。
        self.assertEqual(count_chars.count_narou(self.TEXT), 11)

    def test_raw_counts_spaces_and_newlines(self):
        # 行の長さ 2+0+4+4+1+3 = 14、改行 5。ファイル末尾の改行は数えない。
        self.assertEqual(count_chars.count_raw(self.TEXT), 19)

    def test_raw_with_markup_is_literal_length(self):
        self.assertEqual(count_chars.count_raw(self.TEXT, with_markup=True), len(self.TEXT.rstrip("\n")))

    def test_trailing_newlines_do_not_change_counts(self):
        a = "朝だ。"
        self.assertEqual(count_chars.count_raw(a), count_chars.count_raw(a + "\n\n"))

    def test_ruby_excluded_in_every_mode(self):
        plain, marked = "海へ行く。", "｜海《うみ》へ行く。"
        for fn in (count_chars.count_body, count_chars.count_narou, count_chars.count_raw):
            self.assertEqual(fn(plain), fn(marked), fn.__name__)

    def test_word_count_for_enumeration_check(self):
        # 設計仕様 1.4: 「ありがとう」は 5 文字。本文に字数を書く前の検算に使う。
        self.assertEqual(count_chars.count_body("ありがとう"), 5)
        self.assertEqual(count_chars.count_body("さようなら"), 5)
        self.assertEqual(count_chars.count_body("好き"), 2)

    def test_silence_line_is_prose_but_ornament_is_not(self):
        self.assertEqual(count_chars.count_body("朝だ。\n……\n夜だ。\n"), 8)
        self.assertEqual(count_chars.count_body("朝だ。\n◇\n夜だ。\n"), 6)

    def test_invisible_characters_are_reported(self):
        self.assertEqual(count_chars.count_invisible("朝" + chr(0x200B) + "だ。\n"), 1)
        self.assertEqual(count_chars.count_invisible(FW + "朝だ。\n"), 0)


class GridTest(unittest.TestCase):
    def test_parse_grid(self):
        self.assertEqual(count_chars.parse_grid("42x34"), (42, 34))
        self.assertEqual(count_chars.parse_grid("40×16"), (40, 16))
        self.assertEqual(count_chars.parse_grid(" 20X20 "), (20, 20))
        for bad in ("40", "0x20", "axb", "40x"):
            with self.assertRaises(count_chars.CountError):
                count_chars.parse_grid(bad)

    def test_rows_wrap_at_width(self):
        self.assertEqual(count_chars.rows_for_cells(list("あ" * 20), 20, False), 1)
        self.assertEqual(count_chars.rows_for_cells(list("あ" * 21), 20, False), 2)
        self.assertEqual(count_chars.rows_for_cells([], 20, False), 1)  # 空行も 1 行

    def test_hanging_punctuation(self):
        cells = list("あ" * 20 + "。")
        self.assertEqual(count_chars.rows_for_cells(cells, 20, False), 2)
        self.assertEqual(count_chars.rows_for_cells(cells, 20, True), 1)
        # ぶら下げるのは句読点だけ。
        self.assertEqual(count_chars.rows_for_cells(list("あ" * 20 + "い"), 20, True), 2)

    def test_pair_marks_take_one_cell(self):
        line = "あ" * 19 + "!?"
        self.assertEqual(count_chars.flow_rows(line, 20), 2)
        self.assertEqual(count_chars.flow_rows(line, 20, pair_marks=True), 1)

    def test_genko_is_not_chars_divided_by_400(self):
        # 5 字の段落が 10 本 = 50 字でも、原稿用紙では 10 行を使う。
        text = "\n".join(["短い段落"] * 10 + [""])
        self.assertEqual(count_chars.flow_rows(text, 20), 10)
        self.assertEqual(count_chars.pages_for(10, 20), (1, 10))
        self.assertEqual(count_chars.pages_for(21, 20), (2, 1))
        self.assertEqual(count_chars.pages_for(0, 20), (0, 0))

    def test_blank_lines_and_heading_take_rows(self):
        text = "# 題\n\n朝だ。\n"
        self.assertEqual(count_chars.flow_rows(text, 20), 3)

    def test_ruby_is_not_flowed(self):
        line = "｜" + "海" * 20 + "《うみ》"
        self.assertEqual(count_chars.flow_rows(line, 20), 1)

    def test_assume_indent_adds_one_cell_to_narration_only(self):
        self.assertEqual(count_chars.flow_rows("あ" * 20, 20, assume_indent=True), 2)
        self.assertEqual(count_chars.flow_rows(FW + "あ" * 19, 20, assume_indent=True), 1)
        self.assertEqual(count_chars.flow_rows("「" + "あ" * 18 + "」", 20, assume_indent=True), 1)


class CountCliTest(TempDirCase):
    def test_text_option_json(self):
        code, out, _ = run_cli(count_chars, ["--text", "ありがとう", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"]["body"], 5)
        self.assertEqual(data["files"][0]["grids"], [])  # 語の検算に原稿用紙換算は出さない

    def test_default_output_is_short_and_has_three_modes(self):
        path = self.write("a.md", SAMPLE)
        code, out, _ = run_cli(count_chars, [str(path)])
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out.splitlines()), 40)
        for word in ("body", "narou", "raw", "400字詰"):
            self.assertIn(word, out)

    def test_no_overall_score(self):
        path = self.write("a.md", SAMPLE)
        code, out, _ = run_cli(count_chars, [str(path), "--json"])
        self.assertEqual(code, 0)
        self.assertNotIn("score", out.lower())
        self.assertNotIn("スコア", out)

    def test_max_violation_exits_1(self):
        path = self.write("a.md", SAMPLE)
        code, out, _ = run_cli(count_chars, [str(path), "--mode", "body", "--max", "10"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        code, _, _ = run_cli(count_chars, [str(path), "--mode", "body", "--max", "10000", "--min", "10"])
        self.assertEqual(code, 0)

    def test_page_bounds(self):
        path = self.write("a.md", "\n".join(["あ" * 20] * 5) + "\n")
        args = [str(path), "--grid", "20x2"]
        code, out, _ = run_cli(count_chars, args + ["--json"])
        self.assertEqual(code, 0)
        grids = {g["grid"]: g for g in json.loads(out)["total"]["grids"]}
        self.assertEqual(grids["20x2"]["pages"], 3)
        self.assertEqual(grids["20x2"]["last_page_rows"], 1)
        self.assertEqual(run_cli(count_chars, args + ["--max-pages", "2"])[0], 1)
        self.assertEqual(run_cli(count_chars, args + ["--min-pages", "3", "--max-pages", "3"])[0], 0)

    def test_usage_errors_exit_2(self):
        path = self.write("a.md", SAMPLE)
        cases = [
            [],                                          # 対象なし
            [str(self.dir / "missing.md")],              # ファイルなし
            [str(path), "--max", "100"],                 # --mode all のまま規定と比べようとした
            [str(path), "--max-pages", "3"],             # --grid なし
            [str(path), "--grid", "40"],                 # グリッドの形が違う
        ]
        for argv in cases:
            code, _, err = run_cli(count_chars, argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("エラー", err, argv)

    def test_non_utf8_file_exits_2(self):
        path = self.dir / "sjis.txt"
        path.write_bytes("岬の灯台".encode("cp932"))
        code, _, err = run_cli(count_chars, [str(path)])
        self.assertEqual(code, 2)
        self.assertIn("UTF-8", err)

    def test_directory_input_and_total(self):
        self.write("m/ch001.md", "朝だ。\n")
        self.write("m/ch002.md", "夜だった。\n")
        self.write("m/memo.json", "{}")  # .md / .txt 以外は拾わない
        code, out, _ = run_cli(count_chars, [str(self.dir / "m"), "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["total"]["files"], 2)
        self.assertEqual(data["total"]["body"], 3 + 5)

    def test_wildcard_is_expanded_by_the_script(self):
        self.write("m/ch001.md", "朝だ。\n")
        self.write("m/ch002.md", "夜だった。\n")
        code, out, _ = run_cli(count_chars, [str(self.dir / "m" / "ch*.md"), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["total"]["files"], 2)

    def test_crlf_and_bom_do_not_change_counts(self):
        lf = self.write("lf.md", SAMPLE)
        crlf = self.write("crlf.md", SAMPLE, newline="\r\n", bom=True)
        a = json.loads(run_cli(count_chars, [str(lf), "--json"])[1])["total"]
        b = json.loads(run_cli(count_chars, [str(crlf), "--json"])[1])["total"]
        for mode in ("body", "narou", "raw"):
            self.assertEqual(a[mode], b[mode], mode)

    def test_bare_ruby_note(self):
        path = self.write("a.md", "灯台《とうだい》へ行く。\n")
        _, out, _ = run_cli(count_chars, [str(path)])
        self.assertIn("｜を省略したルビ", out)


# ===========================================================================
# export.py: 変換
# ===========================================================================
def convert(text, target, **kw):
    opts = export.Options(target=target, **kw)
    if "heading" not in kw:
        opts.heading = export.resolve_heading(target, "auto")
    out, report = export.convert_text(text, opts)
    return out, report, opts


def warning_codes(report):
    return {w["code"] for w in report.warnings}


class IndentTest(unittest.TestCase):
    def test_indent_goes_to_narration_only(self):
        out, report, _ = convert(SAMPLE, "kakuyomu")
        lines = out.split("\n")
        self.assertEqual(lines[0], FW + "岬の｜灯台《とうだい》は、冬になると《《音》》を変える。")
        self.assertEqual(lines[1], FW + "真帆はそれを知っていた。")  # 字下げ済みは二重にしない
        self.assertEqual(lines[2], "「今夜は荒れるよ」")
        self.assertEqual(lines[3], "（嘘だ、と思った）")
        self.assertEqual(lines[4], "")                                # 空行
        self.assertEqual(lines[5], "＊")                              # 場面転換行
        self.assertEqual(lines[7], FW + "《《あれ》》が来たのは夜半だった。")
        self.assertEqual(lines[8], "『聞こえる？』")
        self.assertEqual(report.stats["indent_added"], 2)
        self.assertEqual(report.stats["indent_already"], 1)

    def test_no_line_gets_double_indent(self):
        out, _, _ = convert(SAMPLE, "narou")
        again, _, _ = convert(out, "narou")
        self.assertEqual(out, again)  # 出力をもう一度通しても変わらない
        self.assertFalse(any(line.startswith(FW + FW) for line in again.split("\n")))

    def test_other_open_brackets_are_not_indented(self):
        text = "【速報】\n〈声〉\n《注》\n"
        out, _, _ = convert(text, "plain")
        self.assertEqual(out, "【速報】\n〈声〉\n《注》")

    def test_symbol_line_is_not_indented(self):
        out, _, _ = convert("朝だ。\n\n◇\n\n夜だ。\n", "plain")
        self.assertIn("\n◇\n", out)

    def test_silence_line_is_indented_as_narration(self):
        out, _, _ = convert("朝だ。\n……\n「……」\n", "plain")
        self.assertEqual(out, FW + "朝だ。\n" + FW + "……\n「……」")

    def test_keep_and_remove(self):
        text = "朝だ。\n" + FW + "夜だ。\n「やあ」\n"
        keep, _, _ = convert(text, "plain", indent="keep")
        self.assertEqual(keep, "朝だ。\n" + FW + "夜だ。\n「やあ」")
        removed, _, _ = convert(text, "plain", indent="remove")
        self.assertEqual(removed, "朝だ。\n夜だ。\n「やあ」")

    def test_odd_indent_is_reported_not_fixed(self):
        # 半角空白の字下げは原稿の誤り。変換で直さず知らせる。
        text = " 朝だ。\n" + FW + FW + "夜だ。\n" + FW + "「やあ」\n"
        out, report, _ = convert(text, "plain")
        self.assertEqual(out, text.rstrip("\n"))
        codes = [w["code"] for w in report.warnings]
        self.assertEqual(codes.count("indent-odd"), 2)
        self.assertIn("indent-bracket", codes)

    def test_mixed_indent_warning(self):
        _, report, _ = convert(SAMPLE, "kakuyomu")
        self.assertIn("indent-mixed", warning_codes(report))
        _, report, _ = convert("朝だ。\n夜だ。\n", "kakuyomu")
        self.assertNotIn("indent-mixed", warning_codes(report))


class NotationTest(unittest.TestCase):
    LINE = "｜灯台《とうだい》の《《音》》。"

    def test_kakuyomu_is_passthrough(self):
        out, report, _ = convert(self.LINE, "kakuyomu", indent="keep")
        self.assertEqual(out, self.LINE)
        self.assertEqual(report.warnings, [])

    def test_narou_keeps_ruby_and_warns_about_emphasis(self):
        out, report, _ = convert(self.LINE, "narou", indent="keep")
        self.assertEqual(out, self.LINE)  # 傍点タグの実体は未確認なので変換しない
        self.assertIn("bouten-unsupported", warning_codes(report))

    def test_narou_bouten_strip(self):
        out, report, _ = convert(self.LINE, "narou", indent="keep", bouten="strip")
        self.assertEqual(out, "｜灯台《とうだい》の音。")
        self.assertNotIn("bouten-unsupported", warning_codes(report))

    def test_alphapolis_ruby(self):
        out, report, _ = convert(self.LINE, "alphapolis", indent="keep")
        self.assertEqual(out, "#灯台__とうだい__#の《《音》》。")
        self.assertIn("bouten-unsupported", warning_codes(report))

    def test_pixiv_tags(self):
        out, report, _ = convert(self.LINE, "pixiv", indent="keep")
        self.assertEqual(out, "[[rb:灯台 > とうだい]]の[[emphasismark:音>﹅]]。")
        self.assertEqual(report.warnings, [])

    def test_pixiv_custom_emphasis_mark(self):
        out, _, _ = convert("《《音》》", "pixiv", indent="keep", emphasis_mark="・")
        self.assertEqual(out, "[[emphasismark:音>・]]")

    def test_plain_options(self):
        keep, report, _ = convert(self.LINE, "plain", indent="keep")
        self.assertEqual(keep, self.LINE)
        self.assertNotIn("bouten-unsupported", warning_codes(report))  # 記法を残すのが plain の仕様
        paren, _, _ = convert(self.LINE, "plain", indent="keep", plain_ruby="paren", bouten="strip")
        self.assertEqual(paren, "灯台（とうだい）の音。")
        drop, _, _ = convert(self.LINE, "plain", indent="keep", plain_ruby="drop")
        self.assertEqual(drop, "灯台の《《音》》。")

    def test_emphasis_is_not_mistaken_for_ruby(self):
        # 傍点を先に処理しないと、漢字直後の《《音》》の内側がルビとして拾われる。
        out, report, _ = convert("岬｜灯《ひ》《《音》》", "pixiv", indent="keep")
        self.assertEqual(out, "岬[[rb:灯 > ひ]][[emphasismark:音>﹅]]")
        self.assertEqual((report.stats["ruby"], report.stats["emphasis"]), (1, 1))

    def test_converted_text_is_not_rescanned(self):
        # 変換結果が別の規則に再マッチしないこと（ふりがなに | を含む意地悪な入力）。
        out, _, _ = convert("｜a《b》｜c《d》", "alphapolis", indent="keep")
        self.assertEqual(out, "#a__b__##c__d__#")

    def test_headings(self):
        out, report, _ = convert(SAMPLE, "narou")
        self.assertNotIn("第一話", out)  # 題はサイトの題欄へ
        self.assertEqual(report.stats["headings"], ["第一話　岬の灯"])
        out, _, _ = convert(SAMPLE, "pixiv")
        self.assertTrue(out.startswith("[chapter:第一話　岬の灯]\n"))
        out, _, _ = convert(SAMPLE, "plain")
        self.assertTrue(out.startswith("第一話　岬の灯\n"))
        out, _, _ = convert(SAMPLE, "plain", heading="keep")
        self.assertTrue(out.startswith("# 第一話　岬の灯\n"))

    def test_heading_chapter_is_pixiv_only(self):
        with self.assertRaises(export.ExportError):
            export.resolve_heading("narou", "chapter")

    def test_scene_break_replacement(self):
        mark = FW * 3 + "◇"
        out, report, opts = convert(SAMPLE, "narou", scene_break=mark)
        self.assertIn("\n" + mark + "\n", out)
        self.assertNotIn("＊", out)
        self.assertEqual(report.stats["scene_breaks"], 1)
        self.assertTrue(export.verify([SAMPLE], out, opts)["ok"])

    def test_join_puts_newpage_between_files_only(self):
        opts = export.Options(target="pixiv", heading="chapter")
        joined = export.join_outputs(["一話", "二話", "三話"], opts)
        self.assertEqual(joined, "一話\n[newpage]\n二話\n[newpage]\n三話")
        self.assertFalse(joined.startswith("[newpage]"))  # 先頭に置くと 1 ページ目が空になる
        plain = export.join_outputs(["一話", "二話"], export.Options(target="plain"))
        self.assertEqual(plain, "一話\n\n二話")


class BlankPolicyTest(unittest.TestCase):
    TEXT = "朝だ。\n\n\n昼だ。\n「やあ」\n「おう」\n夜だ。\n\n＊\n\n翌朝。\n"

    def body(self, policy):
        out, _, _ = convert(self.TEXT, "plain", indent="keep", blank=policy)
        return out.split("\n")

    def test_keep(self):
        self.assertEqual(self.body("keep"), self.TEXT.rstrip("\n").split("\n"))

    def test_none_keeps_blank_only_around_scene_break(self):
        self.assertEqual(self.body("none"),
                         ["朝だ。", "昼だ。", "「やあ」", "「おう」", "夜だ。", "", "＊", "", "翌朝。"])

    def test_dialogue(self):
        self.assertEqual(self.body("dialogue"),
                         ["朝だ。", "昼だ。", "", "「やあ」", "「おう」", "", "夜だ。", "", "＊", "", "翌朝。"])

    def test_para(self):
        lines = self.body("para")
        self.assertEqual([x for x in lines if x], [x for x in self.TEXT.split("\n") if x])
        self.assertEqual(lines[1::2], [""] * (len(lines) // 2))  # 1 行おきに空行

    def test_every_policy_passes_verify(self):
        for policy in ("keep", "none", "dialogue", "para"):
            out, _, opts = convert(self.TEXT, "plain", blank=policy)
            self.assertTrue(export.verify([self.TEXT], out, opts)["ok"], policy)


class BodyIsNeverFixedTest(unittest.TestCase):
    """export は本文を直さない。表記の誤りも原稿どおりに出し、直すのは原稿側。"""

    DIRTY = "「だめだ。」\n…と彼は言った!?それきり黙る。\n時計は3時を指していた--はずだ。\n"

    def test_notation_errors_pass_through_verbatim(self):
        for target in export.TARGETS:
            out, _, _ = convert(self.DIRTY, target, indent="keep")
            self.assertEqual(out, self.DIRTY.rstrip("\n"), target)

    def test_only_indent_differs_with_default_options(self):
        out, _, _ = convert(self.DIRTY, "narou")
        stripped = [line.lstrip(FW) for line in out.split("\n")]
        self.assertEqual(stripped, self.DIRTY.rstrip("\n").split("\n"))


class SourceInspectionTest(unittest.TestCase):
    def test_raw_double_angle_brackets(self):
        _, report, _ = convert("彼は《約束》と書いた。\n", "kakuyomu")
        self.assertIn("raw-kakko", warning_codes(report))

    def test_paren_ruby_warning_is_narou_only(self):
        text = "宇宙（そら）を見上げた。\n"
        self.assertIn("paren-ruby", warning_codes(convert(text, "narou")[1]))
        self.assertNotIn("paren-ruby", warning_codes(convert(text, "kakuyomu")[1]))

    def test_ruby_length_limit(self):
        text = "｜" + "灯" * 11 + "《ひ》が並ぶ。\n"
        self.assertIn("ruby-length", warning_codes(convert(text, "narou")[1]))
        self.assertNotIn("ruby-length", warning_codes(convert(text, "kakuyomu")[1]))
        # pixiv・アルファポリスの上限は未確認なので検査しない。
        self.assertNotIn("ruby-length", warning_codes(convert(text, "pixiv")[1]))

    def test_bare_ruby_is_not_converted(self):
        out, report, _ = convert("灯台《とうだい》へ。\n", "alphapolis", indent="keep")
        self.assertEqual(out, "灯台《とうだい》へ。")
        self.assertIn("bare-ruby", warning_codes(report))

    def test_halfwidth_bar_warning(self):
        _, report, _ = convert("|灯台《とうだい》へ。\n", "narou")
        self.assertIn("half-bar", warning_codes(report))

    def test_title_markup_warning(self):
        _, report, _ = convert("# ｜岬《みさき》の灯\n\n朝だ。\n", "narou")
        self.assertIn("title-markup", warning_codes(report))

    def test_collision_with_target_tag(self):
        text = "掲示板には[newpage]とだけ書かれていた。\n"
        self.assertIn("collision", warning_codes(convert(text, "pixiv")[1]))
        self.assertNotIn("collision", warning_codes(convert(text, "narou")[1]))


# ===========================================================================
# export.py: 検証
# ===========================================================================
class VerifyTest(unittest.TestCase):
    def check(self, target, **kw):
        out, _, opts = convert(SAMPLE, target, **kw)
        return out, opts, export.verify([SAMPLE], out, opts)

    def test_every_target_roundtrips(self):
        for target in export.TARGETS:
            _, _, result = self.check(target)
            self.assertTrue(result["ok"], target)
            self.assertEqual(result["diffs"], [])

    def test_option_combinations_roundtrip(self):
        combos = [
            ("narou", dict(bouten="strip")),
            ("narou", dict(indent="remove", blank="para")),
            ("alphapolis", dict(heading="text", blank="none")),
            ("pixiv", dict(emphasis_mark="・", heading="text")),
            ("plain", dict(plain_ruby="paren", bouten="strip", blank="dialogue")),
            ("plain", dict(plain_ruby="drop", heading="keep")),
        ]
        for target, kw in combos:
            _, _, result = self.check(target, **kw)
            self.assertTrue(result["ok"], (target, kw))

    def test_changed_character_is_detected(self):
        for target in export.TARGETS:
            out, opts, _ = self.check(target)
            result = export.verify([SAMPLE], out.replace("真帆", "真穂"), opts)
            self.assertFalse(result["ok"], target)
            self.assertEqual(result["diffs"][0]["kind"], "変わった")
            self.assertEqual(result["diffs"][0]["source_line"], 4)

    def test_dropped_and_added_lines_are_detected(self):
        out, opts, _ = self.check("narou")
        lines = out.split("\n")
        dropped = "\n".join(line for line in lines if "嘘だ" not in line)
        result = export.verify([SAMPLE], dropped, opts)
        self.assertFalse(result["ok"])
        self.assertEqual(result["diffs"][0]["kind"], "出力に無い")
        added = out + "\n" + FW + "そして夜が明けた。"
        result = export.verify([SAMPLE], added, opts)
        self.assertFalse(result["ok"])
        self.assertEqual(result["diffs"][0]["kind"], "出力に増えた")

    def test_changed_ruby_reading_is_detected(self):
        out, opts, _ = self.check("pixiv")
        result = export.verify([SAMPLE], out.replace("とうだい", "とうたい"), opts)
        self.assertFalse(result["ok"])

    def test_silently_stripped_emphasis_is_detected(self):
        # --bouten strip を指定していないのに傍点が消えていたら、それは本文の変質。
        out, opts, _ = self.check("narou")
        result = export.verify([SAMPLE], out.replace("《《音》》", "音"), opts)
        self.assertFalse(result["ok"])

    def test_indent_and_blank_lines_are_normalized(self):
        out, opts, _ = self.check("kakuyomu")
        relaxed = out.replace("\n", "\n\n").replace(FW, "")
        self.assertTrue(export.verify([SAMPLE], relaxed, opts)["ok"])

    def test_bar_width_is_normalized(self):
        src = "|灯台《とうだい》へ。\n"
        out, _, opts = convert(src, "alphapolis")
        self.assertTrue(export.verify([src], out, opts)["ok"])

    def test_joined_sources(self):
        second = "# 第二話\n\n翌朝、｜霧《きり》が出た。\n"
        opts = export.Options(target="pixiv", heading="chapter")
        outs = [export.convert_text(t, opts)[0] for t in (SAMPLE, second)]
        joined = export.join_outputs(outs, opts)
        self.assertTrue(export.verify([SAMPLE, second], joined, opts)["ok"])
        # 章の順番が入れ替わっていれば不一致。
        swapped = export.join_outputs(outs[::-1], opts)
        self.assertFalse(export.verify([SAMPLE, second], swapped, opts)["ok"])

    def test_tag_collision_in_source_fails_verification(self):
        # 原稿に pixiv のタグと同じ形の文字列があると、逆変換で原稿に戻らない。黙って通さない。
        src = "札には[[rb:海 > うみ]]と書かれていた。\n"
        out, _, opts = convert(src, "pixiv")
        self.assertFalse(export.verify([src], out, opts)["ok"])


# ===========================================================================
# export.py: コマンドライン
# ===========================================================================
class ExportCliTest(TempDirCase):
    def test_writes_next_to_source_and_verifies(self):
        src = self.write("draft.md", SAMPLE)
        before = hashlib.sha256(src.read_bytes()).hexdigest()
        code, out, _ = run_cli(export, [str(src), "--to", "narou", "--verify"])
        self.assertEqual(code, 0)
        self.assertIn("検証: 一致", out)
        self.assertLessEqual(len(out.splitlines()), 40)
        written = self.dir / "export" / "narou" / "draft.narou.txt"
        data = written.read_bytes()
        self.assertNotIn(b"\r", data)              # LF で書く
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))
        self.assertTrue(data.decode("utf-8").startswith(FW + "岬の｜灯台《とうだい》"))
        self.assertEqual(before, hashlib.sha256(src.read_bytes()).hexdigest())  # 原稿は書き換えない

    def test_ai_notice_is_always_shown(self):
        src = self.write("draft.md", SAMPLE)
        _, out, _ = run_cli(export, [str(src), "--to", "kakuyomu"])
        self.assertIn("生成 AI の利用申告", out)

    def test_crlf_bom_source(self):
        src = self.write("draft.md", SAMPLE, newline="\r\n", bom=True)
        code, _, _ = run_cli(export, [str(src), "--to", "pixiv", "--verify"])
        self.assertEqual(code, 0)
        text = (self.dir / "export" / "pixiv" / "draft.pixiv.txt").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("[chapter:第一話　岬の灯]\n"))

    def test_refuses_to_overwrite_source(self):
        src = self.write("draft.md", SAMPLE)
        code, _, err = run_cli(export, [str(src), "--to", "narou", "-o", str(src)])
        self.assertEqual(code, 2)
        self.assertIn("原稿は書き換えない", err)
        self.assertEqual(src.read_text(encoding="utf-8"), SAMPLE)

    def test_usage_errors_exit_2(self):
        src = self.write("draft.md", SAMPLE)
        cases = [
            [str(src)],                                           # 出力先が決まらない
            [str(self.dir / "missing.md"), "--to", "narou"],      # ファイルなし
            [str(src), "--to", "narou", "--join"],                # --join は pixiv / plain だけ
            [str(src), "--to", "narou", "--heading", "chapter"],  # pixiv 専用
            [str(src), "--to", "pixiv", "--emphasis-mark", "ab"],
            [str(src), "--to", "narou", "--project", str(self.dir / "nowhere")],
        ]
        for argv in cases:
            code, _, err = run_cli(export, argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("エラー", err, argv)

    def test_unknown_target_is_rejected(self):
        # 未確認の出力先（ハーメルン等）は実装していない。
        src = self.write("draft.md", SAMPLE)
        code, _, _ = run_cli(export, [str(src), "--to", "hameln"])
        self.assertEqual(code, 2)

    def test_verify_file_detects_edited_output(self):
        src = self.write("draft.md", SAMPLE)
        run_cli(export, [str(src), "--to", "alphapolis"])
        posted = self.dir / "export" / "alphapolis" / "draft.alphapolis.txt"
        self.assertEqual(run_cli(export, [str(src), "--to", "alphapolis", "--verify-file", str(posted)])[0], 0)
        posted.write_text(posted.read_text(encoding="utf-8").replace("荒れる", "あれる"), encoding="utf-8")
        code, out, _ = run_cli(export, [str(src), "--to", "alphapolis", "--verify-file", str(posted)])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)

    def test_mismatch_is_not_written_unless_forced(self):
        src = self.write("draft.md", "札には[[rb:海 > うみ]]と書かれていた。\n")
        target = self.dir / "out.txt"
        code, out, _ = run_cli(export, [str(src), "--to", "pixiv", "-o", str(target)])
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())
        self.assertIn("書き出していない", out)
        code, _, _ = run_cli(export, [str(src), "--to", "pixiv", "-o", str(target), "--force"])
        self.assertEqual(code, 1)  # 書き出しても FAIL は FAIL
        self.assertTrue(target.exists())

    @unittest.skipUnless(HAS_TOMLLIB, "tomllib が無い Python では novel.toml を読まない")
    def test_project_settings_are_used(self):
        (self.dir / "novel.toml").write_text(
            '[work]\ntitle = "岬の灯"\nmedium = "kakuyomu"\n\n[format]\nindent = "fullwidth"\n',
            encoding="utf-8",
        )
        src = self.write("manuscript/ch001.md", SAMPLE)
        code, out, _ = run_cli(export, [str(src)])
        self.assertEqual(code, 0)
        written = self.dir / "work" / "export" / "kakuyomu" / "ch001.kakuyomu.txt"
        self.assertTrue(written.is_file())
        # indent = fullwidth の作品は原稿の字下げを正とし、export では足さない。
        self.assertTrue(written.read_text(encoding="utf-8").startswith("岬の｜灯台"))
        self.assertIn("novel.toml", out)

    def test_command_line_beats_project_settings(self):
        (self.dir / "novel.toml").write_text(
            '[work]\nmedium = "kakuyomu"\n\n[format]\nindent = "none"\n', encoding="utf-8")
        src = self.write("manuscript/ch001.md", SAMPLE)
        code, _, _ = run_cli(export, [str(src), "--to", "narou", "--indent", "add"])
        self.assertEqual(code, 0)
        written = self.dir / "work" / "export" / "narou" / "ch001.narou.txt"
        self.assertTrue(written.read_text(encoding="utf-8").startswith(FW + "岬の"))

    def test_project_medium_none_requires_to(self):
        (self.dir / "novel.toml").write_text('[work]\nmedium = "none"\n', encoding="utf-8")
        src = self.write("manuscript/ch001.md", SAMPLE)
        code, _, err = run_cli(export, [str(src)])
        self.assertEqual(code, 2)
        self.assertIn("--to", err)

    @unittest.skipUnless(HAS_TOMLLIB, "tomllib が無い Python では novel.toml を読まない")
    def test_broken_project_toml_exits_2(self):
        (self.dir / "novel.toml").write_text("[work\nmedium = ", encoding="utf-8")
        src = self.write("manuscript/ch001.md", SAMPLE)
        code, _, err = run_cli(export, [str(src), "--to", "narou"])
        self.assertEqual(code, 2)
        self.assertIn("novel.toml", err)

    def test_directory_to_separate_files(self):
        self.write("m/ch001.md", SAMPLE)
        self.write("m/ch002.md", "# 第二話\n\n翌朝、霧が出た。\n")
        out_dir = self.dir / "out"
        code, _, _ = run_cli(export, [str(self.dir / "m"), "--to", "narou", "-o", str(out_dir)])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(p.name for p in out_dir.iterdir()), ["ch001.narou.txt", "ch002.narou.txt"])

    def test_join_for_pixiv(self):
        self.write("m/ch001.md", SAMPLE)
        self.write("m/ch002.md", "# 第二話\n\n翌朝、霧が出た。\n")
        target = self.dir / "all.txt"
        code, _, _ = run_cli(export, [str(self.dir / "m"), "--to", "pixiv", "--join", "-o", str(target), "--verify"])
        self.assertEqual(code, 0)
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text.count("[newpage]"), 1)
        self.assertTrue(text.startswith("[chapter:"))

    def test_stdout_mode_keeps_summary_off_stdout(self):
        src = self.write("draft.md", "朝だ。\n「やあ」\n")
        code, out, err = run_cli(export, [str(src), "--to", "narou", "--stdout"])
        self.assertEqual(code, 0)
        self.assertEqual(out, FW + "朝だ。\n「やあ」\n")
        self.assertIn("検証: 一致", err)
        self.assertFalse((self.dir / "export").exists())

    def test_json_has_everything_and_no_score(self):
        src = self.write("draft.md", SAMPLE)
        code, out, _ = run_cli(export, [str(src), "--to", "narou", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertFalse(data["fail"])
        result = data["results"][0]
        self.assertTrue(result["verify"]["ok"])
        self.assertEqual(result["stats"]["ruby"], 1)
        self.assertIn("bouten-unsupported", {w["code"] for w in result["warnings"]})
        self.assertNotIn("score", out.lower())

    def test_size_limit_warning(self):
        # なろうの 1 話上限 70,000 字の 95 パーセントを超えたら知らせる（止めはしない）。
        src = self.write("long.md", "\n".join(["あ" * 99] * 680) + "\n")
        code, out, _ = run_cli(export, [str(src), "--to", "narou", "--json"])
        self.assertEqual(code, 0)
        codes = {w["code"] for w in json.loads(out)["results"][0]["warnings"]}
        self.assertIn("size-limit", codes)


# ===========================================================================
# 実プロセスでの確認（Windows のコンソール既定エンコーディングに左右されないこと）
# ===========================================================================
class SubprocessTest(TempDirCase):
    def run_script(self, name, *argv):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / name), *argv],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(self.dir),
        )
        return proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8")

    def test_count_chars_outputs_utf8(self):
        self.write("draft.md", SAMPLE)
        code, out, _ = self.run_script("count_chars.py", "draft.md")
        self.assertEqual(code, 0)
        self.assertIn("400字詰", out)

    def test_export_outputs_utf8_and_accepts_backslash_paths(self):
        src = self.write("sub/draft.md", SAMPLE)
        code, out, _ = self.run_script("export.py", str(src).replace("/", "\\"), "--to", "pixiv", "--verify")
        self.assertEqual(code, 0)
        self.assertIn("検証: 一致", out)

    def test_help_is_the_reference(self):
        for name in ("count_chars.py", "export.py"):
            code, out, _ = self.run_script(name, "--help")
            self.assertEqual(code, 0, name)
            self.assertIn("終了コード", out, name)
        _, out, _ = self.run_script("export.py", "--help")
        self.assertIn("未対応", out)  # 未確認の記法は実装せず、help に明記する
        _, out, _ = self.run_script("count_chars.py", "--help")
        self.assertIn("未対応", out)

    def test_error_exit_code_in_real_process(self):
        code, _, err = self.run_script("count_chars.py", "missing.md")
        self.assertEqual(code, 2)
        self.assertIn("エラー", err)


if __name__ == "__main__":
    unittest.main()
