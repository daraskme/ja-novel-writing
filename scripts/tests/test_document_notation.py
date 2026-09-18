# -*- coding: utf-8 -*-
"""作中文書の明示記法（行頭の全角 ＞）のテスト。lint・export・字数の 3 本が同じ解釈をすること。

字下げの無い原稿では、チャットやメールの文面を体裁から見分けられず、N01・N03 が誤 FAIL になる。
範囲を ＞ で明示すれば N09（確認）に下がり、統計からも外れる。推定（N10）は案内だけで、判定は変えない。
サンプル文はすべて自作。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import count_chars  # noqa: E402
import export  # noqa: E402
import novel_lint as nl  # noqa: E402

FW = chr(0x3000)
GT = chr(0xFF1E)          # 全角 ＞
BS = chr(0x5C)            # 半角 \
FW_BS = chr(0xFF3C)       # 全角 ＼


def lint(text, length="short"):
    cfg = nl.Config("entertainment", length, None)
    cfg.finalize()
    return nl.lint_text(text, cfg, min_chars=0)


def found(hits, rule, severity=None):
    return [h for h in hits if h["rule"] == rule and (severity is None or h["severity"] == severity)]


def lines_of(hits, rule, severity=None):
    return sorted({loc["line"] for h in found(hits, rule, severity) for loc in h["locations"]})


PLAIN = "\n".join([
    "スマホが震えた。美咲からのメッセージだった。",
    "今どこ？駅前で待ってる",
    "はやく来て…",
    "電話をかけた。三回目の呼び出し音で、彼女が出た。",
    "「もしもし」",
    "彼は走りだした。",
]) + "\n"
MARKED = PLAIN.replace("今どこ", GT + "今どこ").replace("はやく", GT + "はやく")


class LintExplicitDocument(unittest.TestCase):
    def test_unmarked_document_in_flat_manuscript_still_fails_but_is_guided(self):
        """無記法のままなら FAIL は残る（勝手に下げない）。直前の行が文書を導入しているので、範囲の明示を案内する。"""
        _, hits = lint(PLAIN)
        self.assertEqual(lines_of(hits, "N03", "FAIL"), [2])
        self.assertEqual(lines_of(hits, "N01", "FAIL"), [3])
        guide = found(hits, "N10")
        self.assertEqual(len(guide), 1)
        self.assertEqual(guide[0]["severity"], "INFO")
        self.assertEqual(lines_of(hits, "N10"), [2])
        self.assertIn(GT, guide[0]["hint"])

    def test_marked_lines_become_review_items(self):
        _, hits = lint(MARKED)
        self.assertEqual(found(hits, "N01", "FAIL") + found(hits, "N03", "FAIL"), [])
        self.assertEqual(lines_of(hits, "N09", "WARN"), [2, 3])
        self.assertEqual(found(hits, "N10"), [])

    def test_narration_after_the_block_is_judged_as_usual(self):
        _, hits = lint(MARKED.replace("電話をかけた。", "電話をかけた…"))
        self.assertEqual(lines_of(hits, "N01", "FAIL"), [4])
        self.assertEqual(lines_of(hits, "N09", "WARN"), [2, 3])

    def test_clean_document_raises_nothing(self):
        text = "メールを開いた。\n" + GT + "明日の十時に、駅前で。\n" + GT + "\n" + GT + "遅れないでね。\n彼は画面を閉じた。\n"
        _, hits = lint(text)
        self.assertEqual([h["rule"] for h in hits if h["rule"].startswith("N") and h["severity"] != "INFO"], [])

    def test_document_is_excluded_from_sentence_stats_but_counted_in_chars(self):
        plain_stats, _ = lint(PLAIN)
        stats, _ = lint(MARKED)
        self.assertEqual(stats["chars"], plain_stats["chars"])                     # 印は字数に入らない。文面は入る
        self.assertLess(stats["narration_chars"], plain_stats["narration_chars"])  # 文面は地の文に数えない
        self.assertLess(stats["sentences"], plain_stats["sentences"])
        self.assertEqual(stats["utterances"], plain_stats["utterances"])

    def test_quotes_inside_document_are_not_utterances(self):
        text = "メールを開いた。\n" + GT + "課長に「明日でいい」と言われました。\n彼は画面を閉じた。\n"
        stats, _ = lint(text)
        self.assertEqual(stats["utterances"], 0)

    def test_document_cuts_same_ending_runs(self):
        """文書の前後で同じ文末が続いても、1 本の連続としてつながない。"""
        head = ["雨が降っていた。", "彼は傘を開いた。"]
        tail = ["道は濡れていた。", "駅は遠かった。"]
        joined, _ = lint("\n".join(head + tail) + "\n", length="long")
        split, _ = lint("\n".join(head + [GT + "今どこ"] + tail) + "\n", length="long")
        self.assertEqual(joined["same_ending_max_run"], 4)
        self.assertEqual(split["same_ending_max_run"], 2)

    def test_heading_like_and_markdown_inside_document(self):
        text = "メールを開いた。\n" + GT + "# 件名：帰宅時間\n" + GT + "今日は**遅く**なります。\n彼は画面を閉じた。\n"
        stats, hits = lint(text)
        self.assertEqual(lines_of(hits, "N07"), [3])                 # 印は記法として認める。文面の Markdown 装飾は見逃さない
        self.assertGreater(stats["chars"], len("メールを開いた。彼は画面を閉じた。今日は**遅く**なります。") - 4)
        doc = nl.Doc(text)
        self.assertEqual([p["kind"] for p in doc.paras], ["narration", "document", "document", "narration"])
        self.assertEqual(doc.paras[1]["body"], "# 件名：帰宅時間")

    def test_counting_claims_inside_document_are_still_checked(self):
        text = "メールを開いた。\n" + GT + "「ありがとう」の四文字を打った。\n彼は画面を閉じた。\n"
        _, hits = lint(text)
        self.assertEqual(lines_of(hits, "C01", "FAIL"), [2])

    def test_indented_manuscript_inference_is_unchanged(self):
        text = "\n".join([FW + "スマホが震えた。美咲からのメッセージだった。", "今どこ？駅前で待ってる", "はやく来て…",
                          FW + "電話をかけた。三回目の呼び出し音で、彼女が出た。", "「もしもし」", FW + "彼は走りだした。"]) + "\n"
        _, hits = lint(text)
        self.assertEqual(found(hits, "N01", "FAIL") + found(hits, "N03", "FAIL"), [])
        self.assertEqual(lines_of(hits, "N09", "WARN"), [2, 3])
        self.assertEqual(found(hits, "N10"), [])
        _, marked = lint(text.replace("今どこ", GT + "今どこ").replace("はやく", GT + "はやく"))
        self.assertEqual(lines_of(marked, "N09", "WARN"), [2, 3])
        self.assertEqual(found(marked, "N05"), [])                   # 明示した文書に「字下げの無い行のまとまり」は出さない

    def test_indent_inside_document_is_layout(self):
        text = "\n".join([FW + "便箋を開いた。", GT + "拝啓", GT + FW + FW + "お元気ですか。", GT + "      敬具", FW + "彼は便箋を畳んだ。",
                          FW + "窓の外は暗かった。"]) + "\n"
        _, hits = lint(text)
        self.assertEqual(found(hits, "N05", "FAIL"), [])

    def test_escape_keeps_a_literal_mark(self):
        for esc in (BS, FW_BS):
            doc = nl.Doc("掲示板を開いた。\n" + esc + GT + "は不等号だ…\n")
            self.assertEqual(doc.paras[1]["kind"], "narration")
            self.assertEqual(doc.paras[1]["body"], GT + "は不等号だ…")
            self.assertEqual(doc.explicit_doc, set())
        doc = nl.Doc("掲示板を開いた。\n" + GT + GT + GT + "1 それな\n")
        self.assertEqual(doc.paras[1]["kind"], "document")
        self.assertEqual(doc.paras[1]["body"], GT + GT + "1 それな")     # 外すのは 1 字だけ

    def test_halfwidth_mark_is_not_the_notation(self):
        _, hits = lint("メールを開いた。\n> 今どこ？駅前\n彼は走った。\n")
        self.assertEqual(lines_of(hits, "N07"), [2])
        self.assertEqual(lines_of(hits, "N03", "FAIL"), [2])


class LintGuessedDocument(unittest.TestCase):
    """字下げの無い原稿での案内（N10）。FAIL を下げず、統計も変えない。"""

    def guide_lines(self, text):
        _, hits = lint(text)
        return [loc["excerpt"] for h in found(hits, "N10") for loc in h["locations"]], hits

    def test_not_an_intro(self):
        locs, hits = self.guide_lines("メールを読んだが、返事はしなかった。\n外は雨…だった。\n彼は傘を取った。\n")
        self.assertEqual(locs, [])
        self.assertEqual(lines_of(hits, "N01", "FAIL"), [2])

    def test_no_guide_without_a_notation_hit(self):
        locs, _ = self.guide_lines("メッセージが届いた。\n今から行く。\n彼は走った。\n")
        self.assertEqual(locs, [])

    def test_upper_bound(self):
        body = ["一行目…", "二行目…", "三行目…", "四行目…", "五行目。"]
        for n in (2, 3, 4):
            locs, hits = self.guide_lines("メッセージが届いた。\n" + "\n".join(body[:n]) + "\n")
            self.assertEqual(len(locs), 1)
            self.assertIn(f"最大 {min(n, nl.DOC_GUESS_MAX_LINES)} 行", locs[0])
            self.assertEqual(len(lines_of(hits, "N01", "FAIL")), n)      # 案内は FAIL を 1 つも下げない

    def test_hit_beyond_the_bound_is_not_guided(self):
        locs, _ = self.guide_lines("メッセージが届いた。\n一行目。\n二行目。\n三行目。\n四行目…\n")
        self.assertEqual(locs, [])

    def test_stops(self):
        for stopper in ("", "# 見出し", "＊", "「二行目…」", GT + "明示した文面…", FW + "字下げされた行…"):
            text = "メッセージが届いた。\n一行目。\n" + stopper + "\n二行目…\n"
            locs, _ = self.guide_lines(text)
            self.assertEqual(locs, [], repr(stopper))

    def test_one_blank_line_after_the_intro(self):
        locs, _ = self.guide_lines("メッセージが届いた。\n\n今どこ？駅前\n\n彼は走った。\n")
        self.assertEqual(len(locs), 1)
        locs, _ = self.guide_lines("メッセージが届いた。\n\n\n今どこ？駅前\n")
        self.assertEqual(locs, [])

    def test_stats_are_untouched_by_the_guess(self):
        a, _ = lint(PLAIN)
        b, _ = lint(PLAIN.replace("美咲からのメッセージだった。", "美咲の顔が浮かんだ。"))
        self.assertEqual(a["sentences"], b["sentences"])


SRC = "\n".join([
    "# 第一話",
    "彼はスマホを開いた。",
    GT + "# 件名：明日",
    GT,
    GT + "｜明日《あした》、《《必ず》》来て。",
    GT + "＊",
    "",
    GT + "二通目。",
    BS + GT + "は不等号だ。",
    "「うん」",
    "彼は頷いた。",
]) + "\n"


def convert(text, target, **kw):
    opts = export.Options(target=target, **kw)
    out, report = export.convert_text(text, opts)
    return out, report, opts


class ExportDocument(unittest.TestCase):
    def test_classification_agrees(self):
        cases = {GT + "今どこ": "document", GT: "docblank", GT + FW: "docblank", GT + "# 件名": "document", GT + "＊": "document",
                 GT + "「引用」": "document", BS + GT + "は不等号": "narration", FW_BS + GT + "は不等号": "narration",
                 "> 半角": "narration", FW + GT + "字下げの後ろ": "narration"}
        for line, kind in cases.items():
            self.assertEqual(export.classify_line(line), kind, repr(line))
            self.assertEqual(count_chars.classify_line(line), kind, repr(line))
        for name in ("DOC_PREFIX", "DOC_ESCAPES", "DOC_KINDS"):
            self.assertEqual(getattr(export, name), getattr(count_chars, name), name)
        self.assertEqual((export.DOC_PREFIX, export.DOC_ESCAPES), (nl.DOC_PREFIX, nl.DOC_ESCAPES))

    def test_prefix_is_removed_and_body_is_kept(self):
        out, report, _ = convert(SRC, "narou", blank="none")
        self.assertEqual(out.split("\n"), [
            FW + "彼はスマホを開いた。", "",
            "# 件名：明日", "", "｜明日《あした》、《《必ず》》来て。", "＊", "",      # 文面の # と ＊ は見出し・場面転換にしない。中の空行も残す
            "二通目。", "",                                                           # 空行を挟んだ 2 つの文書はつなげない
            FW + GT + "は不等号だ。", "「うん」", FW + "彼は頷いた。"])
        self.assertEqual(report.stats["document_lines"], 5)
        self.assertEqual(report.stats["scene_breaks"], 0)
        self.assertEqual(report.stats["headings"], ["第一話"])

    def test_inline_notation_inside_document_is_converted(self):
        out, _, _ = convert(SRC, "pixiv")
        self.assertIn("[[rb:明日 > あした]]、[[emphasismark:必ず>﹅]]来て。", out)
        out, _, _ = convert(SRC, "alphapolis")
        self.assertIn("#明日__あした__#", out)

    def test_custom_scene_break_does_not_touch_document(self):
        out, _, _ = convert("彼は見た。\n" + GT + "＊\n＊\n彼は閉じた。\n", "plain", scene_break="◇◇◇")
        self.assertEqual([l for l in out.split("\n") if l.strip()], [FW + "彼は見た。", "＊", "◇◇◇", FW + "彼は閉じた。"])

    def test_keep_policy_keeps_source_blank_lines(self):
        text = "彼は見た。\n" + GT + "一行目\n" + GT + "二行目\n彼は閉じた。\n"
        out, _, _ = convert(text, "narou", blank="keep")
        self.assertEqual(out.split("\n"), [FW + "彼は見た。", "一行目", "二行目", FW + "彼は閉じた。"])
        for policy in ("none", "dialogue", "para"):
            out, _, _ = convert(text, "narou", blank=policy)
            self.assertEqual(out.split("\n"), [FW + "彼は見た。", "", "一行目", "二行目", "", FW + "彼は閉じた。"], policy)

    def test_document_at_the_edges(self):
        out, _, _ = convert(GT + "\n" + GT + "冒頭の文面\n彼は読んだ。\n" + GT + "末尾の文面\n" + GT + "\n", "narou", blank="none")
        self.assertEqual(out.split("\n"), ["冒頭の文面", "", FW + "彼は読んだ。", "", "末尾の文面"])

    def test_verify_passes_for_every_target_and_policy(self):
        for target in export.TARGETS:
            for policy in ("keep", "none", "dialogue", "para"):
                for heading in ("drop", "text", "keep"):
                    out, _, opts = convert(SRC, target, blank=policy, heading=heading)
                    self.assertNotIn(GT + "二通目", out)
                    self.assertTrue(export.verify([SRC], out, opts)["ok"], (target, policy, heading))

    def test_verify_catches_a_leftover_mark_and_a_lost_char(self):
        """印を外すのは原稿側だけ。出力に印が残っても、文面が 1 字欠けても不一致になる。"""
        for target in export.TARGETS:
            out, _, opts = convert(SRC, target)
            self.assertFalse(export.verify([SRC], out.replace("二通目。", GT + "二通目。"), opts)["ok"], target)
            self.assertFalse(export.verify([SRC], out.replace("二通目。", "通目。"), opts)["ok"], target)
            self.assertFalse(export.verify([SRC], out.replace(GT + "は不等号だ。", "は不等号だ。"), opts)["ok"], target)

    def test_heading_like_document_line_survives_heading_drop(self):
        out, _, opts = convert(SRC, "narou", heading="drop")
        self.assertIn("# 件名：明日", out)
        self.assertNotIn("第一話", out)
        self.assertFalse(export.verify([SRC], out.replace("# 件名：明日\n", ""), opts)["ok"])


class CountDocument(unittest.TestCase):
    def test_modes_ignore_the_mark_and_count_the_body(self):
        marked = "彼は見た。\n" + GT + "今どこ？\n" + GT + "\n" + GT + "｜駅《えき》の前\n" + BS + GT + "は記号だ。\n"
        bare = "彼は見た。\n今どこ？\n\n｜駅《えき》の前\n" + BS + GT + "は記号だ。\n"      # 行頭の ＞ を文面として残すにはエスケープが要る
        for fn in (count_chars.count_raw, count_chars.count_narou, count_chars.count_body):
            self.assertEqual(fn(marked), fn(bare), fn.__name__)
        self.assertEqual(count_chars.count_raw(marked, with_markup=True), len(marked.rstrip("\n")))
        self.assertEqual(count_chars.count_raw(marked, with_markup=True) - count_chars.count_raw(bare, with_markup=True), 3)

    def test_heading_like_document_line_is_body(self):
        text = "# 第一話\n彼は見た。\n" + GT + "# 件名\n"
        self.assertEqual(count_chars.count_narou(text), len("彼は見た。#件名"))
        self.assertEqual(count_chars.count_body(text), len("彼は見た。#件名"))
        self.assertEqual(count_chars.count_raw(text), len("第一話\n彼は見た。\n# 件名"))

    def test_body_mode_matches_lint_chars(self):
        text = "彼はスマホを開いた。\n" + GT + "# 件名：明日\n" + GT + "\n" + GT + "｜明日《あした》、《《必ず》》来て。\n" + GT + "＊\n「うん」\n"
        stats, _ = lint(text)
        self.assertEqual(count_chars.count_body(text), stats["chars"])

    def test_grid_rows(self):
        text = "彼は見た。\n" + GT + "今どこ\n" + GT + "\n" + GT + "駅前\n"
        self.assertEqual(count_chars.flow_rows(text, 20), 4)
        self.assertEqual(count_chars.flow_rows(text, 20, assume_indent=True), 4)


class CodexReview11(unittest.TestCase):
    """Codex レビュー 11 の再現入力。"""

    def test_intro_is_not_carried_past_an_explicit_document(self):
        """「メールを開いた。」の効力は、直後の明示された文面で使い切る。その後ろの字下げの無い行は、文書と推定しない。"""
        for marked in (GT + "了解。", GT):
            text = "\n".join([FW + "メールを開いた。", marked, "電話をかけた…", FW + "雨が降っていた。", FW + "彼は歩いた。"]) + "\n"
            _, hits = lint(text)
            self.assertEqual(lines_of(hits, "N01", "FAIL"), [3], repr(marked))
            self.assertNotIn(3, lines_of(hits, "N09"), repr(marked))

    def test_symbol_only_document_lines_are_boundaries(self):
        head, tail = ["雨が降っていた。", "彼は傘を開いた。"], ["道は濡れていた。", "駅は遠かった。"]
        for marked in (GT + "＊", GT, GT + "……"):
            stats, _ = lint("\n".join(head + [marked] + tail) + "\n", length="long")
            self.assertEqual(stats["same_ending_max_run"], 2, repr(marked))

    def test_markdown_in_a_symbol_only_document_line(self):
        _, hits = lint("メールを開いた。\n" + GT + "**！**\n彼は画面を閉じた。\n")
        self.assertEqual(lines_of(hits, "N07"), [2])
        _, hits = lint("彼は見た。\n* * *\n彼は歩いた。\n")
        self.assertEqual(lines_of(hits, "N07"), [2])

    def test_silent_lines_are_counted_like_count_chars(self):
        for text in [GT + "……\n", "……\n彼は見た。\n", GT + "！？\n" + GT + "＊\n彼は見た。\n", "（……）\n彼は見た。\n", "【……】\n――――\n彼は見た。\n"]:
            stats, hits = lint(text)
            self.assertEqual(stats["chars"], count_chars.count_body(text), repr(text))
        self.assertEqual(found(lint(GT + "……\n")[1], "M01", "FAIL"), [])

    def test_guide_ignores_disabled_rules(self):
        cfg = nl.Config("entertainment", "short", None)
        cfg.finalize()
        cfg.overrides = {"N01": {"off": True, "reason": "test"}}
        _, hits = nl.lint_text("メッセージが届いた。\n待ってる…\n", cfg, min_chars=0)
        self.assertEqual(found(hits, "N01") + found(hits, "N10"), [])
        _, hits = nl.lint_text("メッセージが届いた。\n待ってる…\n", nl_config(), skip="")
        self.assertEqual(len(found(hits, "N10")), 1)

    def test_conjunction_between_cue_and_verb_is_not_an_intro(self):
        for intro in ["メールを読んだが窓を開いた。", "手紙を持ったまま戸を開けた。", "画面を見ながら箱を開いた。"]:
            _, hits = lint(intro + "\n外は雨…\n")
            self.assertEqual(found(hits, "N10"), [], intro)

    def test_verify_keeps_document_text_literal(self):
        """文面の「## 件名」「＊ ＊」を見出し・場面転換として均さない。欠落も印の残留も不一致になる。"""
        plain = export.Options(target="plain", indent="keep")
        self.assertFalse(export.verify([GT + "## 件名"], "# 件名", plain)["ok"])
        self.assertTrue(export.verify([GT + "## 件名"], "## 件名", plain)["ok"])
        self.assertFalse(export.verify([GT + "＊ ＊"], "＊", plain)["ok"])
        custom = export.Options(target="plain", scene_break=GT + "＊")
        self.assertFalse(export.verify([GT + "＊"], GT + "＊", custom)["ok"])
        out, _ = export.convert_text(GT + "＊\n＊\n彼は見た。\n", custom)
        self.assertEqual(out.split("\n")[0], "＊")
        self.assertTrue(export.verify([GT + "＊\n＊\n彼は見た。\n"], out, custom)["ok"])
        # 地の文の見出し・場面転換は、これまでどおり表記の違いを無視する
        self.assertTrue(export.verify(["## 題\n＊　＊　＊\n本文。"], "# 題\n\n◇\n\n" + FW + "本文。", export.Options(target="plain", heading="keep", scene_break="◇"))["ok"])

    def test_dropped_heading_does_not_merge_documents(self):
        text = GT + "一通目。\n# 次の手紙\n" + GT + "二通目。\n"
        for policy in ("keep", "none", "dialogue", "para"):
            out, _, opts = convert(text, "narou", blank=policy, heading="drop")
            self.assertEqual(out.split("\n"), ["一通目。", "", "二通目。"], policy)
            self.assertTrue(export.verify([text], out, opts)["ok"], policy)
        # 文書のあいだでなければ、keep は見出しの跡に何も足さない
        out, _, _ = convert("彼は見た。\n# 題\n彼は歩いた。\n", "narou", blank="keep", heading="drop")
        self.assertEqual(out.split("\n"), [FW + "彼は見た。", FW + "彼は歩いた。"])

    def test_library_calls_normalize_bom_and_crlf(self):
        text = chr(0xFEFF) + GT + "本文\r\n彼は見た。\r\n"
        opts = export.Options(target="plain", indent="keep")
        out, _ = export.convert_text(text, opts)
        self.assertEqual(out, "本文\n彼は見た。")
        self.assertTrue(export.verify([text], out, opts)["ok"])
        self.assertFalse(export.verify([text], GT + "本文\n彼は見た。", opts)["ok"])
        clean = GT + "本文\n彼は見た。\n"
        for fn in (count_chars.count_raw, count_chars.count_narou, count_chars.count_body):
            self.assertEqual(fn(text), fn(clean), fn.__name__)
        self.assertEqual(count_chars.flow_rows(text, 20), count_chars.flow_rows(clean, 20))
        self.assertEqual(lint(text)[0]["chars"], count_chars.count_body(text))


class CodexReview12(unittest.TestCase):
    """Codex レビュー 12 の再現入力。"""

    def test_silent_lines_do_not_dilute_punctuation_density(self):
        """字数に入れた沈黙の行は、約物の頻度の検索対象にも入れる。分母だけ増えて警報が消えてはいけない。"""
        base = "彼は歩いた。\n" * 100 + "彼は黙った……。\n" * 10
        _, hits = lint(base)
        self.assertEqual(len(found(hits, "K02")), 1)
        _, hits = lint(base + "……\n" * 1000)
        k02 = found(hits, "K02")
        self.assertEqual(len(k02), 1)
        self.assertEqual(len(k02[0]["locations"]), 1010)

    def test_short_narration_still_gets_the_vocabulary_note(self):
        """全文が長くても、地の文が短ければ K00（回数だけの案内）を出す。診断が黙って消えない。"""
        _, hits = lint("寂しかった。\n")
        self.assertEqual(len(found(hits, "K00")), 1)
        _, hits = lint("寂しかった。\n" + "……\n" * 300)
        self.assertEqual(len(found(hits, "K00")), 1)

    def test_markdown_is_checked_even_when_the_body_is_empty(self):
        _, hits = lint(GT + "**！**\n")
        self.assertEqual(lines_of(hits, "N07"), [1])
        self.assertEqual(len(found(hits, "M01", "FAIL")), 1)


def nl_config():
    cfg = nl.Config("entertainment", "short", None)
    cfg.finalize()
    return cfg


if __name__ == "__main__":
    unittest.main()
