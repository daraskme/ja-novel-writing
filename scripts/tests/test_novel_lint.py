# -*- coding: utf-8 -*-
"""novel_lint.py のテスト。サンプル文はすべて自作。"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import novel_lint as nl  # noqa: E402

NLCHR = chr(10)


def lint(text, profile="entertainment", only="", **kw):
    cfg = nl.Config(profile, "long", None)
    cfg.finalize()
    for k, v in kw.items():
        setattr(cfg, k, v)
    stats, hits = nl.lint_text(text, cfg, only=only)
    return stats, hits


def rules(hits, severity=None):
    return [h["rule"] for h in hits if severity is None or h["severity"] == severity]


class NumberParsing(unittest.TestCase):
    def test_numbers(self):
        cases = {"四": 4, "十": 10, "十二": 12, "二十": 20, "二十三": 23, "九十九": 99, "百": 100, "百五": 105,
                 "4": 4, "１２": 12, "ひと": 1, "ふた": 2, "一〇": 10}
        for s, n in cases.items():
            self.assertEqual(nl.parse_number(s), n, s)
        self.assertIsNone(nl.parse_number("数"))

    def test_morae(self):
        self.assertEqual(nl.count_morae("きょう"), 2)
        self.assertEqual(nl.count_morae("がっこう"), 4)
        self.assertEqual(nl.count_morae("しゃしん"), 3)
        self.assertEqual(nl.count_morae("コーヒー"), 4)
        self.assertIsNone(nl.count_morae("今日"))

    def test_count_chars_ignores_punctuation_and_ruby(self):
        self.assertEqual(nl.count_chars_of("ありがとう。"), 5)
        self.assertEqual(nl.count_chars_of("……うん"), 2)
        self.assertEqual(nl.count_chars_of("｜永遠《とわ》"), 2)


class Counting(unittest.TestCase):
    def fails(self, text):
        _, hits = lint(text, only="C")
        return [h for h in hits if h["rule"] == "C01" and h["severity"] == "FAIL"]

    def test_wrong_counts_fail(self):
        for text in ["　「ありがとう」の四文字が言えなかった。",
                     "　「すき」の三文字。",
                     "　「ごめんね」\n　たった三文字の言葉が出てこない。",
                     "　「ただいま」の5文字。",
                     "　「おかえりなさい」の十二文字。",
                     "　たった三文字の言葉。「すき」",
                     "　「愛」の六文字。",
                     "　「さよなら」、その五文字を飲み込んだ。",
                     "　『バカ』という三文字。"]:
            self.assertEqual(len(self.fails(text)), 1, text)

    def test_correct_counts_pass(self):
        for text in ["　「愛」の一文字を書いた。",
                     "　「さようなら」という五文字。",
                     "　たった二文字の言葉。「好き」",
                     "　「愛してる」のたった四文字。",
                     "　四文字の言葉、「さよなら」。",
                     "　「ごめん」という三文字が喉につかえた。",
                     "　「ただいま」の4文字。",
                     "　「バカ」の二文字。",
                     "　「うん」ふた文字の返事だった。",
                     "　「ありがとう。」の五文字。",
                     "　「……はい」の二文字。"]:
            self.assertEqual(self.fails(text), [], text)

    def test_no_false_fail_on_generic_mentions(self):
        text = "　三文字熟語は苦手だ。一文字も書けなかった。二文字目で手が止まった。原稿用紙は四百字詰めだ。二千字ほど書いた。"
        self.assertEqual(self.fails(text), [])

    def test_long_preceding_speech_is_not_failed(self):
        text = "「きのうの夜、駅前で誰と会っていたのか教えてくれないか」\n　たった三文字の返事を待った。"
        self.assertEqual(self.fails(text), [])

    def test_kana_reading_ambiguity_is_warn(self):
        _, hits = lint("　「愛してる」の五文字。", only="C")
        self.assertEqual([h["severity"] for h in hits if h["rule"] == "C01"], ["WARN"])

    def test_bare_word_is_warn_not_fail(self):
        _, hits = lint("　絶望という三文字が浮かぶ。希望の二文字を胸に刻んだ。最初の二文字を消した。", only="C")
        c02 = [h for h in hits if h["rule"] == "C02"]
        self.assertEqual(len(c02), 1)
        self.assertEqual(len(c02[0]["locations"]), 1)
        self.assertEqual(c02[0]["severity"], "WARN")

    def test_morae(self):
        _, hits = lint("　「しゃしん」は四音だ。「がっこう」は四音。「今日」は二音。", only="C")
        self.assertEqual(len([h for h in hits if h["rule"] == "C03" and h["severity"] == "FAIL"][0]["locations"]), 1)
        self.assertEqual(len([h for h in hits if h["rule"] == "C03" and h["severity"] == "WARN"][0]["locations"]), 1)

    def test_verse(self):
        ok = "　祖母は一句詠んだ。「ふるいけや　かわずとびこむ　みずのおと」"
        ng = "　祖母は一句詠んだ。「なつのよる　ほしがきれいだ　そらをみあげる」"
        self.assertNotIn("C04", rules(lint(ok, only="C")[1]))
        self.assertIn("C04", rules(lint(ng, only="C")[1]))

    def test_keywords_and_enumeration_are_info(self):
        _, hits = lint("　頭文字を繋ぐと名前になる。三つの理由がある。", only="C")
        self.assertIn("C05", rules(hits, "INFO"))
        self.assertIn("C06", rules(hits, "INFO"))


class Notation(unittest.TestCase):
    def test_ellipsis_dash_space_period(self):
        text = "　彼は…黙った。\n　そう―言いかけた。\n「本当？嘘でしょ」\n「帰るよ。」\n　だめだ・・・。"
        _, hits = lint(text, only="N")
        got = rules(hits, "FAIL")
        for r in ("N01", "N02", "N03", "N04"):
            self.assertIn(r, got)

    def test_correct_notation_passes(self):
        text = "　彼は……黙った。\n　そう――言いかけた。\n「本当？　嘘でしょ」\n「帰るよ」\n「待って！」\n　ラーーーメン、と彼は伸ばした。"
        _, hits = lint(text, only="N")
        self.assertEqual(rules(hits, "FAIL"), [])

    def test_indent_mix(self):
        text = "　一行目の地の文。\n二行目は下げ忘れ。\n　三行目の地の文。\n　四行目の地の文。"
        _, hits = lint(text, only="N")
        self.assertIn("N05", rules(hits, "FAIL"))

    def test_no_indent_uniform_is_not_fail(self):
        text = "一行目の地の文。\n二行目の地の文。\n三行目の地の文。"
        _, hits = lint(text, profile="web", only="N")
        self.assertEqual(rules(hits, "FAIL"), [])

    def test_markdown(self):
        _, hits = lint("　彼は**強く**言った。\n- 箇条書き", only="N")
        self.assertIn("N07", rules(hits))


class Rhythm(unittest.TestCase):
    def test_same_ending_run(self):
        text = "　" + "".join(f"彼は{w}を見た。" for w in "空海山川森町道橋")
        _, hits = lint(text, only="R")
        self.assertIn("R01", rules(hits, "STRONG"))

    def test_tag_sentence_excluded_and_dialogue_cuts_run(self):
        text = "　戸を開けた。\n　靴を脱いだ。\n「ただいま」\n　返事はなかった。\n　灯りをつけた。"
        stats, hits = lint(text, only="R")
        self.assertEqual(stats["same_ending_max_run"], 2)
        self.assertEqual(rules(hits), [])

    def test_stats_keys(self):
        stats, _ = lint("　雨の朝だった。傘を持たずに出た。\n「遅いよ」\n　と言われて、走った。")
        for k in ("chars", "narration_chars", "dialogue_ratio", "sentence_len_mean", "sentence_len_cv",
                  "ending_share", "paragraph_len_cv", "one_sentence_paragraph_rate", "kanji_ratio"):
            self.assertIn(k, stats)


class DensityAndMeta(unittest.TestCase):
    AI = ("　彼女はそっと扉を開けた。静かに息を吐いた。ゆっくりと歩いた。ふと空を見上げた。"
          "まるで夢のようだった。彼女は小さく頷いた。胸の奥が痛んだ。彼女は静かに微笑んだ。") * 9

    def test_density_rules_fire_on_ai_prose(self):
        _, hits = lint(self.AI, profile="bungei")
        got = rules(hits)
        for r in ("K10", "K14", "R01"):
            self.assertIn(r, got)

    def test_no_total_score(self):
        _, hits = lint(self.AI)
        blob = json.dumps(hits, ensure_ascii=False)
        self.assertNotRegex(blob, r"総合|スコア|点数")

    def test_short_text_reports_counts_only(self):
        _, hits = lint("　ふと空を見た。まるで嘘のようだった。")
        self.assertIn("K00", rules(hits, "INFO"))
        self.assertEqual([r for r in rules(hits, "WARN") if r != "M01"], [])

    def test_empty_body_fails(self):
        _, hits = lint("# 第一章\n\n")
        self.assertEqual(rules(hits, "FAIL"), ["M01"])

    def test_meta_preface(self):
        _, hits = lint("以下は掌編小説です。\n\n　男は橋を渡った。\n\nいかがでしたか。")
        self.assertIn("M02", rules(hits))

    def test_emotion_direct_contract_turns_off_k08(self):
        cfg = nl.Config("web", "long", None)
        cfg.finalize()
        cfg.emotion_naming = "direct"
        self.assertTrue(cfg.levels("K08").get("off"))

    def test_override(self):
        cfg = nl.Config("bungei", "long", None)
        cfg.finalize()
        cfg.overrides = {"R01": {"info": 9, "warn": 12, "strong": 20, "reason": "報告体"}, "K10": {"off": True, "reason": "童話調"}}
        self.assertEqual(cfg.levels("R01")["warn"], 12)
        self.assertTrue(cfg.levels("K10").get("off"))


class CodexReviewRegressions(unittest.TestCase):
    """外部レビューで見つかった誤検出・見逃しの再発防止。FAIL は必ず直されるので、誤 FAIL は本文を壊す。"""

    def c(self, text, only="C"):
        return lint(text, only=only)[1]

    def test_negated_count_is_not_failed(self):
        for text in ["　「ありがとう」は四文字ではなく五文字だ。", "　「ありがとう」って四文字だっけ？　と彼は指を折った。"]:
            self.assertEqual([h for h in self.c(text) if h["severity"] == "FAIL"], [], text)

    def test_sentence_end_then_unrelated_quote(self):
        text = "　「ありがとう」は四文字ではなく五文字だ。\n　「いすゞ」の三文字。"
        self.assertEqual([h for h in self.c(text) if h["severity"] == "FAIL"], [])

    def test_unicode_letters_are_counted(self):
        self.assertEqual(nl.count_chars_of("いすゞ"), 3)
        self.assertEqual(nl.count_chars_of("𠮷野家"), 3)
        self.assertEqual(nl.count_chars_of("バカヤロー！"), 5)

    def test_question_then_reply_is_not_failed(self):
        # 「どこ？」は記号込みなら三文字。待っているのは別の語（返事）でもある。どちらの理由でも FAIL にしない
        hits = self.c("「どこ？」\n　たった三文字の返事を待った。")
        self.assertNotIn("FAIL", [h["severity"] for h in hits if h["rule"] == "C01"])
        hits = self.c("「どこだ」\n　たった二文字の返事を待った。")
        self.assertEqual([h["severity"] for h in hits if h["rule"] == "C01"], ["WARN"])

    def test_crossline_archetype_still_fails(self):
        hits = self.c("「ありがとう」\n　たった四文字の言葉。けれど重かった。")
        self.assertEqual([h["severity"] for h in hits if h["rule"] == "C01"], ["FAIL"])
        hits = self.c("　たった四文字の言葉。\n「ありがとう」")
        self.assertEqual([h["severity"] for h in hits if h["rule"] == "C01"], ["FAIL"])

    def test_morae_ruby_reverse_and_ambiguous(self):
        self.assertEqual([h["severity"] for h in self.c("　「｜今日《きょう》」は三音。") if h["rule"] == "C03"], ["FAIL"])
        self.assertEqual([h for h in self.c("　「｜今日《きょう》」は二音。") if h["rule"] == "C03"], [])
        self.assertEqual([h["severity"] for h in self.c("　四音の言葉、「しゃしん」。") if h["rule"] == "C03"], ["FAIL"])
        self.assertEqual([h["severity"] for h in self.c("　「あぁ」は二拍だった。") if h["rule"] == "C03"], ["WARN"])

    def test_dates(self):
        hits = self.c("　二〇二六年二月三十日、月曜日。二〇二六年九月十九日（土曜）。二〇二六年九月十九日（月曜）。")
        c08 = [h for h in hits if h["rule"] == "C08"]
        self.assertEqual(len(c08), 1)
        self.assertEqual(len(c08[0]["locations"]), 2)

    def test_silent_dialogue_and_double_bracket_dialogue(self):
        stats, hits = lint("「……」\n『今日は帰りません』\n　彼は靴を履いた。")
        self.assertNotIn("M01", [h["rule"] for h in hits if h["severity"] == "FAIL"])
        self.assertEqual(stats["utterances"], 2)

    def test_unbalanced_bracket_is_reported(self):
        self.assertIn("N08", rules(self.c("「今日は\n帰りません」\n　彼は靴を履いた。", only="N")))

    def test_indent_shape_and_space_after_question(self):
        hits = self.c("  半角で字下げした行。\n　　全角二つの行。\n　普通の行。\n「本当？ 嘘でしょ」", only="N")
        self.assertIn("N05", rules(hits, "FAIL"))
        self.assertIn("N03", rules(hits, "FAIL"))

    def test_off_override_silences_any_rule(self):
        cfg = nl.Config("bungei", "long", None)
        cfg.finalize()
        cfg.overrides = {"N04": {"off": True, "reason": "作者の流儀"}}
        _, hits = nl.lint_text("「帰るよ。」\n　彼は言った。", cfg)
        self.assertNotIn("N04", rules(hits))

    def test_direct_contract_survives_calibration_override(self):
        cfg = nl.Config("web", "long", None)
        cfg.finalize()
        cfg.emotion_naming = "direct"
        cfg.overrides = {"K08": {"info": 3, "warn": 5, "reason": "calibrate: ch001"}}
        self.assertTrue(cfg.levels("K08").get("off"))
        cfg.overrides = {"K08": {"enable": True, "warn": 5, "reason": "作者の指定"}}
        self.assertFalse(cfg.levels("K08").get("off"))

    def test_density_guard_uses_rule_denominator(self):
        text = "　悲しい。寂しい。嬉しい。\n" + "「" + "あのね、それでね、きのうの話なんだけど。" * 45 + "」"
        _, hits = lint(text, profile="bungei")
        self.assertNotIn("K08", [h["rule"] for h in hits if h["severity"] in ("WARN", "STRONG")])

    def test_repeat_window_not_diluted_by_appending(self):
        head = "　彼はそっと戸を開け、そっと靴を脱ぎ、そっと廊下を進んだ。" + "柱時計が鳴るのを待って、階段の三段目を避けて上がった。" * 30
        tail = "\n　" + "翌朝は市場へ出かけ、荷を下ろし、帳面をつけ、昼には戻って飯を食った。" * 60
        for text in (head, head + tail):
            _, hits = lint(text, profile="bungei")
            self.assertTrue(any(h["rule"] == "K10" and "再出" in h["name"] for h in hits))

    def test_ending_class(self):
        self.assertEqual(nl.ending_class("そうなんだ。"), "da")
        self.assertEqual(nl.ending_class("嫌いだ。"), "da")
        self.assertEqual(nl.ending_class("本を読んだ。"), "ta")
        self.assertEqual(nl.ending_class("川を泳いだ。"), "ta")

    def test_flash_skips_paragraph_statistics(self):
        cfg = nl.Config("bungei", "flash", None)
        cfg.finalize()
        text = "\n".join("　" + "彼は歩いた。" * 3 for _ in range(12))
        _, hits = nl.lint_text(text, cfg)
        self.assertFalse({"P02", "P03", "D01"} & set(rules(hits)))


class CodexReviewRound2(unittest.TestCase):
    """検証レビュー 2 で再現した誤 FAIL・見逃しの再発防止。"""

    def sev(self, text, rule, only="C"):
        return [h["severity"] for h in lint(text, only=only)[1] if h["rule"] == rule]

    def test_negation_question_and_sentence_boundary_are_not_failed(self):
        for text in ["　「ありがとう」は四文字でない。", "　「ありがとう」は四文字？", "　「ありがとう」は四文字だろうか。",
                     "　「はい」。三文字の返事を待った。", "　「か」は二音で、子音と母音からなる。"]:
            self.assertNotIn("FAIL", self.sev(text, "C01") + self.sev(text, "C03"), text)

    def test_corrected_number_is_checked(self):
        self.assertEqual(self.sev("　「ありがとう」は四文字ではなく六文字だ。", "C01"), ["FAIL"])
        self.assertEqual(self.sev("　「ありがとう」は四文字ではなく五文字だ。", "C01"), [])

    def test_mora_context_and_unsupported_chars(self):
        self.assertNotIn("FAIL", self.sev("「どこ？」\n　三拍の返事を待った。", "C03"))
        self.assertNotIn("FAIL", self.sev("　返事は三拍だった。\n「いってらっしゃい」", "C03"))
        self.assertEqual(self.sev("　「いすゞ」は三拍。", "C03"), ["WARN"])       # 数えられない字は落とさず、要確認にする
        self.assertEqual(self.sev("　「𠮷の」は一拍。", "C03"), ["WARN"])
        self.assertIsNone(nl.count_morae("いすゞ"))

    def test_dates_without_weekday(self):
        hits = [h for h in lint("　二〇二六年二月二十九日。二〇二六年十三月一日。二〇二六年一月〇日。二〇二八年二月二十九日。二月二十九日。", only="C")[1] if h["rule"] == "C08"]
        self.assertEqual(len(hits[0]["locations"]), 3)

    def test_double_bracket_dialogue_and_titles(self):
        self.assertEqual(lint("『吾輩は猫である』を本棚へ戻した。\n　彼は帰った。")[0]["utterances"], 0)
        self.assertEqual(lint("　彼は『今日は帰りません』と言った。\n　雨だった。")[0]["utterances"], 1)
        self.assertEqual(lint("『「帰れ」と言われたんだ』\n　彼は笑った。")[0]["utterances"], 1)
        self.assertEqual(lint("「姉は『帰れ』と言った」\n　彼は笑った。")[0]["utterances"], 1)

    def test_bracket_order_and_kind(self):
        for text in ["」帰るよ「\n　彼は言った。", "「帰るよ』と姉が言い、弟が『待って」と答えた。"]:
            self.assertIn("N08", rules(lint(text, only="N")[1]), text)
        self.assertNotIn("N08", rules(lint("「姉は『帰れ』と言った」", only="N")[1]))

    def test_past_tense_nda_is_ta(self):
        for s, want in [("洗濯物をたたんだ。", "ta"), ("庭の草をつんだ。", "ta"), ("本を読んだ。", "ta"), ("そうなんだ。", "da"),
                        ("もう行くんだ。", "da"), ("知らなかったんだ。", "da"), ("寒くないんだ。", "da")]:
            self.assertEqual(nl.ending_class(s), want, s)

    def test_indent_mix_with_two_paragraphs(self):
        self.assertIn("N05", rules(lint("　彼は帰った。\n雨が降った。", only="N")[1], "FAIL"))

    def test_flash_reports_counts_not_density_warnings(self):
        cfg = nl.Config("bungei", "flash", None)
        cfg.finalize()
        _, hits = nl.lint_text("　" + "悲しかったが、電車に乗った。" * 45, cfg)
        self.assertNotIn("K08", [h["rule"] for h in hits if h["severity"] in ("WARN", "STRONG")])
        self.assertIn("K00", rules(hits))


class CodexReviewRound3(unittest.TestCase):
    """検証レビュー 3 の再現入力。FAIL は「同じ文の中の断定」と「直前の語を指す定型」だけに出す。"""

    def sev(self, text, rule, only="C"):
        return [h["severity"] for h in lint(text, only=only)[1] if h["rule"] == rule]

    def test_polite_negation_question_and_corrections_are_not_failed(self):
        for text in ["　「さようなら」は四文字ではありません。", "　「こんにちは」は四文字でしょうか。",
                     "　「こんにちは」は四文字ではなく、六文字でもない。", "　「おかえり」は三文字ではなく、五文字だろうか。",
                     "　「いや」。三文字の言葉を探した。", "　「よし」は三拍ではありません。", "　「ありがとう」は四文字かどうか、彼は指を折った。"]:
            self.assertNotIn("FAIL", self.sev(text, "C01") + self.sev(text, "C03"), text)

    def test_archetypes_still_fail(self):
        for text in ["　「ありがとう」の四文字が言えなかった。", "「ありがとう」\n　たった四文字の言葉。けれど重かった。",
                     "「ありがとう」\n　その四文字が、喉につかえた。", "　たった四文字の言葉。\n「ありがとう」",
                     "　「ありがとう」は四文字ではなく六文字だ。", "　「しゃしん」は四音だ。"]:
            self.assertIn("FAIL", self.sev(text, "C01") + self.sev(text, "C03"), text)

    def test_cross_sentence_without_pointer_is_warn(self):
        self.assertEqual(self.sev("　「いや」。三文字の言葉を探した。", "C01"), ["WARN"])

    def test_bracket_openers_are_not_indent_mix(self):
        for text in ["　校門には誰もいなかった。\n【立入禁止】の札が揺れていた。", "　彼は足を止めた。\n〈帰れ〉と書いてある。"]:
            self.assertNotIn("N05", rules(lint(text, only="N")[1], "FAIL"), text)

    def test_non_japanese_lines_are_body(self):
        stats, hits = lint("Hello, world!")
        self.assertNotIn("M01", rules(hits, "FAIL"))
        self.assertGreater(stats["chars"], 0)
        self.assertGreater(lint("𠮷。")[0]["chars"], 0)
        a = lint("　手紙には一行だけあった。")[0]["chars"]
        b = lint("　手紙には一行だけあった。\nMeet me at noon.")[0]["chars"]
        self.assertGreater(b, a)
        # 飾りの行（＊、――――）は字数に入れない。沈黙だけの行（……）は本文なので入れる（count_chars.py の body と同じ数え方。Codex レビュー 11）
        base = lint("　彼は帰った。")[0]["chars"]
        self.assertEqual(lint("＊\n――――\n　彼は帰った。")[0]["chars"], base)
        self.assertEqual(lint("……\n＊\n　彼は帰った。")[0]["chars"], base + 2)
        self.assertNotIn("M01", rules(lint("……")[1], "FAIL"))

    def test_double_bracket_cases(self):
        self.assertEqual(lint("『旅』という雑誌を買った。\n　雨だった。")[0]["utterances"], 0)
        self.assertEqual(lint("『おはよう』と言い、『おやすみ』と答えた。\n　雨だった。")[0]["utterances"], 2)
        self.assertEqual(lint("　母は『待って』と頼んだ。\n　雨だった。")[0]["utterances"], 1)
        self.assertEqual(lint("　彼は『「待て」と言われた』と答えた。\n　雨だった。")[0]["utterances"], 1)

    def test_explanatory_nda_with_kanji_stem(self):
        for s, want in [("今夜も遊ぶんだ。", "da"), ("君が望むんだ。", "da"), ("学んだ。", "ta"), ("列に並んだ。", "ta"), ("庭の草をつんだ。", "ta")]:
            self.assertEqual(nl.ending_class(s), want, s)


class DataFiles(unittest.TestCase):
    def test_lexicon_compiles(self):
        lex = json.loads((nl.DATA_DIR / "lexicon.json").read_text(encoding="utf-8"))
        for key, val in lex.items():
            if key.startswith("_") or key == "count_generic_nouns":
                continue
            for pat in (val if isinstance(val, list) else [val]):
                re.compile(pat)

    def test_thresholds_reference_existing_lexicon(self):
        th = json.loads((nl.DATA_DIR / "thresholds.json").read_text(encoding="utf-8"))
        lex = json.loads((nl.DATA_DIR / "lexicon.json").read_text(encoding="utf-8"))
        for rid, rule in th["rules"].items():
            self.assertIn("name", rule, rid)
            self.assertIn("profiles", rule, rid)
            if "lexicon" in rule:
                self.assertIn(rule["lexicon"], lex, rid)

    def test_purpose_youni_is_not_simile(self):
        rx = re.compile(json.loads((nl.DATA_DIR / "lexicon.json").read_text(encoding="utf-8"))["simile_any"])
        self.assertIsNone(rx.search("忘れないようにする"))
        self.assertIsNotNone(rx.search("眠るように倒れた"))


class Cli(unittest.TestCase):
    def test_exit_codes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            ok = Path(d) / "ok.md"
            ok.write_text("　男は橋を渡り、欄干に手を置いてから、川下の工場の煙を数えた。\n", encoding="utf-8")
            ng = Path(d) / "ng.md"
            ng.write_text("﻿　「ありがとう」の四文字。\r\n", encoding="utf-8")
            self.assertEqual(nl.main([str(ok), "--json"]), 0)
            self.assertEqual(nl.main([str(ng), "--json"]), 1)
            self.assertEqual(nl.main([str(Path(d) / "none.md")]), 2)
            self.assertEqual(nl.main([str(ok), "--only", "Z"]), 2)


class ShareAdvisory(unittest.TestCase):
    """C09: 割合・内訳・合計の言及は、数量の近くにあるときだけ位置を知らせる（検算はしない。INFO）。評価ラウンド 5 で「二十枚のうち五枚」を「半分」と書いた例から。"""

    def c09(self, text):
        return [h for h in lint(text, only="C")[1] if h["rule"] == "C09"]

    def test_share_near_a_quantity_is_pointed_out(self):
        hits = self.c09("　二十枚のうち、来年に回すのは五枚。半分は来年の担ぎ手の分だという。")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["severity"], "INFO")
        self.assertGreaterEqual(len(hits[0]["locations"]), 2)
        self.assertEqual(len(self.c09("　会費は三千円。合わせて九千円を、残り二人で割った。")), 1)
        self.assertEqual(len(self.c09("　二十枚の25％、つまり十枚だ。")), 1)
        self.assertEqual(len(self.c09("　十人のうち三人が残った。")), 1)

    def test_idioms_without_a_quantity_are_silent(self):
        for text in ["　話半分に聞いていた。", "　半分冗談のつもりだった。", "　うちの猫は窓辺にいる。", "　そのうち雨になる。",
                     "　うちの二人の子供が来た。", "　二人で話半分に聞いた。", "　三人は面白半分でついてきた。"]:
            self.assertEqual(self.c09(text), [], text)


class RangeAndNextStep(unittest.TestCase):
    """--range と「次にやること」。検査の周回を止めるための案内で、判定は変えない（評価ラウンド 3〜4 の作業記録から）。"""

    def run_cli(self, text, *args):
        import io, tempfile, contextlib
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.md"
            path.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = nl.main([str(path), *args])
        return code, buf.getvalue()

    def test_parse_range(self):
        self.assertEqual(nl.parse_range("1200-1500"), (1200, 1500))
        self.assertEqual(nl.parse_range("1200〜1500"), (1200, 1500))
        self.assertEqual(nl.parse_range("1500"), (1350, 1650, "approx"))
        # 「約 N 字」は目安。下限をわずかに割っただけ（3% まで）なら範囲内。範囲の指定には遊びを付けない
        self.assertEqual(nl.range_status(1346, nl.parse_range("1500")), "範囲内")
        self.assertEqual(nl.range_status(1651, nl.parse_range("1500")), "超過 1 字")      # 上限には遊びを付けない
        self.assertIn("下限は 1310 字まで可", nl.range_label(nl.parse_range("1500")))
        self.assertEqual(nl.range_status(1499, nl.parse_range("1500-1500")), "不足 1 字")
        self.assertEqual(nl.range_status(1300, nl.parse_range("1500")), "不足 50 字")
        self.assertEqual(nl.range_status(1196, nl.parse_range("1200-1500")), "不足 4 字")
        self.assertIsNone(nl.parse_range("1500-1200"))
        self.assertIsNone(nl.parse_range("abc"))

    def test_status_and_stop_signal(self):
        text = "　彼は駅まで歩いた。雨は上がっていた。\n「遅いよ」\n　彼女は笑った。\n"
        code, out = self.run_cli(text, "--length", "flash", "--range", "20-40", "--min-chars", "0")
        self.assertEqual(code, 0)
        self.assertIn("字数の指定 20〜40: 範囲内", out)
        self.assertIn("これ以上は合わせにいかない", out)
        self.assertIn("本文を変えていないなら、かけ直す必要は無い", out)
        self.assertIn("確認のかけ直しは、してよい", out)
        code, out = self.run_cli(text, "--length", "flash", "--range", "1200-1500", "--min-chars", "0")
        self.assertIn("不足 ", out)
        self.assertIn("まとめて書き足す", out)
        self.assertNotIn("かけ直す必要は無い", out)      # 字数を直すなら、測り直しが要る。矛盾した案内を並べない（Codex レビュー 15）
        self.assertNotIn("1.3 倍", out)
        code, out = self.run_cli(text, "--length", "flash", "--range", "10", "--min-chars", "0")
        self.assertIn("超過 ", out)

    def test_fail_changes_the_next_step_but_not_the_exit_code_rule(self):
        code, out = self.run_cli("　彼は笑った…\n", "--length", "flash", "--min-chars", "0")
        self.assertEqual(code, 1)
        self.assertIn("直したら 1 度かけ直して確かめる", out)
        self.assertNotIn("かけ直す必要は無い", out)

    def test_range_is_judged_per_file(self):
        """2 本のファイルを渡しても、字数の指定は 1 本ずつ見る（合算して「超過」と言わない。Codex レビュー 15）。"""
        import io, tempfile, contextlib
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in (1, 2):
                path = Path(tmp) / f"ch{i}.md"
                path.write_text("　彼は駅まで歩いた。雨は上がっていた。彼女は笑った。" + NLCHR, encoding="utf-8")
                paths.append(str(path))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                nl.main([*paths, "--length", "flash", "--range", "20-40", "--min-chars", "0"])
        out = buf.getvalue()
        self.assertNotIn("超過", out)
        self.assertEqual(out.count("字数は指定の範囲内"), 2)

    def test_long_form_without_range_has_no_next_step(self):
        code, out = self.run_cli("　彼は駅まで歩いた。\n", "--length", "long", "--min-chars", "0")
        self.assertNotIn("次にやること", out)

    def test_bad_range_is_a_usage_error_and_json_carries_the_note(self):
        code, _ = self.run_cli("　彼は歩いた。\n", "--range", "abc")
        self.assertEqual(code, 2)
        code, out = self.run_cli("　彼は歩いた。\n", "--length", "flash", "--range", "5-10", "--json", "--min-chars", "0")
        self.assertIn("次にやること", json.loads(out)["notes"][-1])


if __name__ == "__main__":
    unittest.main()
