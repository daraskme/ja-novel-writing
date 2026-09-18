#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export.py - 内部記法の原稿を投稿先の記法に変換する。本文は 1 字も直さない。

LLM に「記法だけ直して」と頼むと語句まで整えてしまう。変換をこのスクリプトに固定し、
さらに出力を内部記法へ逆変換して原稿と突き合わせることで、媒体展開のたびに本文が
静かに変質する事故を防ぐ。変換中に見つけた問題（素の《》、長すぎるルビなど）は
WARN で知らせるだけで、直すのは原稿側。

実装しているのは、各サイトの公式ヘルプで確認できた記法だけ（確認日 2026-09-19）。
未確認の記法は実装していない。一覧は `python export.py --help`。

標準ライブラリのみ。終了コード: 0 = 検証一致 / 1 = 検証不一致（FAIL） / 2 = 実行エラー。
"""
from __future__ import annotations

import argparse
import difflib
import glob
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2

# ---------------------------------------------------------------------------
# 内部記法の定義（count_chars.py にも同じ定義がある。tests が両者の一致を確かめる）
# ---------------------------------------------------------------------------
FW_SPACE = chr(0x3000)  # 全角空白（字下げの 1 マス）。目で見分けられないのでコードポイントで書く
BOM = chr(0xFEFF)
CR, LF = chr(13), chr(10)


def _span(first: int, last: int) -> str:
    """正規表現の文字クラスに入れる範囲。不可視・難読の文字を直書きしないための小道具。"""
    return f"{chr(first)}-{chr(last)}"


# CJK 統合漢字・拡張 A・互換漢字と、漢字扱いの記号。
KANJI = _span(0x3400, 0x4DBF) + _span(0x4E00, 0x9FFF) + _span(0xF900, 0xFAFF) + "々〆ヵヶ"
# 傍点 《《語》》。ルビの《》と記号が重なるので、必ずルビより先に処理する。
EMPHASIS_RE = re.compile(r"《《([^《》\n]+)》》")
# ルビ ｜親《るび》。内部記法は全角｜だが、半角 | で書かれた原稿も読めるようにしておく。
RUBY_RE = re.compile(r"[｜|]([^｜|《》\n]+)《([^《》\n]+)》")
# ｜を省略したルビ（漢字の直後に《かな》）。なろう・カクヨムがルビとして扱う形なので字数から外す。
BARE_RUBY_RE = re.compile(r"(?<=[" + KANJI + r"])《([ぁ-ゖァ-ヺー・]+)》")
# Markdown 見出し。章題であって本文ではない。
_HSPACE = "[ " + FW_SPACE + chr(0x09) + "]"  # 半角空白・全角空白・タブ
HEADING_RE = re.compile("^#{1,6}" + _HSPACE + "+(.*?)" + _HSPACE + "*#*" + _HSPACE + "*$")
# 場面転換行: ＊ だけの行（「＊　＊　＊」のように空白を挟んでもよい）。
SCENE_BREAK_RE = re.compile(r"^[＊\s]*＊[＊\s]*$")
# 行頭がこれらなら字下げしない（R/36 の裁定。なろう公式の明記は「 のみ）。
OPEN_BRACKETS = "「『（【〈《"
DIALOGUE_BRACKETS = "「『"
# 「……」「！？」だけの行は沈黙や絶句を表す地の文。◇ や ※ のような飾り行と区別する（罫線代わりのダッシュだけの行は飾り）。
SILENT_PROSE_RE = re.compile(r"^[…‥！？!?。、―—\s]*[…‥！？!?][…‥！？!?。、―—\s]*$")
# 作中文書（チャット・メール・手紙・貼り紙）の範囲を原稿の中で明示する記号。行頭の全角 ＞ 1 字。
# ＞ だけの行は文書の中の空行。行頭の ＞ を本文として残したいときは、その前に \ か ＼ を置く。
# 半角の > は Markdown の引用と見分けられないので、この記法には使わない。
DOC_PREFIX = chr(0xFF1E)
DOC_ESCAPES = (chr(0x5C) + DOC_PREFIX, chr(0xFF3C) + DOC_PREFIX)
DOC_KINDS = ("document", "docblank")
# ---------------------------------------------------------------------------

# 傍点とルビを 1 回の走査で拾う。傍点を選択肢の先頭に置くことで「傍点が先」の順序を固定し、
# 置換結果を二度と走査しないので、変換後の文字列が別の規則に再マッチする事故も起きない。
INLINE_RE = re.compile(
    r"《《(?P<emph>[^《》\n]+)》》"
    r"|(?P<bar>[｜|])(?P<parent>[^｜|《》\n]+)《(?P<ruby>[^《》\n]+)》"
)

TARGETS = ("narou", "kakuyomu", "alphapolis", "pixiv", "plain")
# 傍点の公式記法を確認できた出力先。それ以外は傍点を変換しない（未対応）。
NATIVE_EMPHASIS = ("kakuyomu", "pixiv")
# 傍点の記法を確認できていない投稿サイト。原稿の記法のまま残すと、サイト上で素の《《》》が見えるので WARN。
# plain は「記法を残す」のが仕様どおりの出力なので WARN にしない。
UNVERIFIED_EMPHASIS = ("narou", "alphapolis")
# novel.toml の [work] medium から出力先を決める。none と未知の値は --to を要求する。
MEDIUM_TO_TARGET = {
    "narou": "narou", "kakuyomu": "kakuyomu", "alphapolis": "alphapolis",
    "pixiv": "pixiv", "print": "plain",
}
# novel.toml の [format] indent から字下げの既定を決める。原稿に字下げが入っている作品と
# 字下げなしの流儀の作品は触らず、export で付けると決めた作品だけ付与する。
INDENT_FROM_PROJECT = {"export": "add", "fullwidth": "keep", "none": "keep"}

# 公式ヘルプで確認できた上限だけを持つ（2026-09-19）。値は参考で、超えても止めずに WARN。
RUBY_LIMITS = {"narou": (10, 10), "kakuyomu": (20, 50)}  # (親文字, ルビ)
BODY_LIMITS = {"narou": 70000, "alphapolis": 100000}      # 1 話の本文
LIMIT_WARN_RATIO = 0.95  # サイト内部の変換で字数が膨らむことがあるので手前で知らせる

# 出力先の記法を内部記法へ戻すための正規表現（検証専用。変換側とは別に書く）。
ALPHA_RUBY_BACK_RE = re.compile(r"#([^#\n]+?)__([^#\n]+?)__#")
PIXIV_RUBY_BACK_RE = re.compile(r"\[\[rb:\s*(.+?)\s*>\s*(.+?)\s*\]\]")
PIXIV_EMPH_BACK_RE = re.compile(r"\[\[emphasismark:(.+?)>(.)\]\]")
PIXIV_CHAPTER_BACK_RE = re.compile(r"^\[chapter:(.*)\]$")
PIXIV_NEWPAGE = "[newpage]"

# 原稿にこれが素で書かれていると、出力先でタグとして解釈されてしまう。
COLLISION_RES = {
    "alphapolis": [ALPHA_RUBY_BACK_RE],
    "pixiv": [re.compile(
        r"\[\[(?:rb|emphasismark|jumpuri):"
        r"|\[(?:newpage\]|chapter:|jump:|pixivimage:|uploadedimage:|b:|i:)"
    )],
}
# ルビの親文字・ふりがなに含まれると、出力先の記法が壊れる文字列。
RUBY_FORBIDDEN = {"alphapolis": ("#", "__"), "pixiv": (">", "]]")}

# なろうは漢字の直後の（かな）を自動でルビにする（公式ヘルプで確認）。
PAREN_RUBY_RE = re.compile(r"(?<=[" + KANJI + r"])[(（][ぁ-ゖァ-ヺー]+[)）]")

WARNING_TEXT = {
    "bouten-unsupported": "傍点 《《語》》 をこの出力先の記法に変換できない（未対応）。原稿の記法のまま残した。"
                          "サイトの入力補助で付け直すか、--bouten strip で記号だけ外す",
    "ruby-length": "ルビが出力先の上限を超えている（原稿側で分けるか短くする）",
    "ruby-forbidden-char": "ルビの親文字かふりがなに、出力先の記法を壊す文字がある（原稿側で直す）",
    "raw-kakko": "ルビ・傍点の記法になっていない素の《》がある。どの投稿先でもルビ記法と衝突するので原稿側で〈〉などに直す",
    "bare-ruby": "｜を省略したルビらしい箇所は変換していない（親文字の境界を決められない）。原稿側で｜を付ける",
    "half-bar": "半角 | で書かれたルビがある（内部記法は全角｜。出力先で効くかは未確認）",
    "paren-ruby": "漢字の直後の（かな）は、なろうでルビに化ける。補足のつもりなら原稿側で括弧の直前に｜を置く",
    "collision": "原稿に出力先のタグと同じ形の文字列がある。投稿するとタグとして解釈され、検証も一致しなくなる。原稿側で書き換える",
    "indent-odd": "行頭が全角空白 1 個ではない（半角空白・タブ・複数個）。そのまま出力した。原稿側で直す",
    "indent-bracket": "括弧始まりの行が字下げされている。そのまま出力した。原稿側で直す",
    "indent-mixed": "原稿の地の文に字下げあり・なしが混ざっている。原稿側で揃える（novel_lint の字下げ混在）",
    "title-markup": "見出しにルビ・傍点の記法がある。なろう・カクヨムの題欄ではルビが効かない",
    "size-limit": "出力が投稿先の 1 話上限に近いか超えている（値は 2026-09 確認の参考値。分割を検討し、公式で再確認する）",
}

AI_NOTICE = (
    "投稿前の確認: 生成 AI の利用申告（サイトの区分・タグ、公募の可否と申告の書き場所）は投稿先ごとに違い、"
    "短い周期で変わる。最新の公式ページで確かめ、どの区分に当たるかはユーザー自身が決める"
    "（references/notation.md）。"
)


class ExportError(Exception):
    """実行エラー。メッセージをそのまま表示して終了コード 2 で終わる。"""


@dataclass
class Options:
    target: str
    indent: str = "add"            # add / keep / remove
    blank: str = "keep"            # keep / none / dialogue / para
    heading: str = "drop"          # drop / text / keep / chapter（auto は解決済みの値を入れる）
    bouten: str = "keep"           # keep / strip（傍点が未対応の出力先だけで効く）
    plain_ruby: str = "keep"       # keep / paren / drop（plain だけで効く）
    scene_break: str = ""          # 空なら場面転換行をそのまま出す
    emphasis_mark: str = "﹅"       # pixiv の傍点に使う 1 字

    @property
    def strips_emphasis(self) -> bool:
        return self.target not in NATIVE_EMPHASIS and self.bouten == "strip"


@dataclass
class Report:
    """1 回の変換で分かったこと。stats は件数、warnings は原稿側で直すべき点。"""
    stats: dict = field(default_factory=lambda: {
        "ruby": 0, "emphasis": 0, "scene_breaks": 0, "headings": [],
        "indent_added": 0, "indent_removed": 0, "indent_already": 0, "indent_skipped": 0,
        "lines": 0, "document_lines": 0,
    })
    warnings: list = field(default_factory=list)

    def warn(self, code: str, line: int, detail: str = "") -> None:
        self.warnings.append({"code": code, "line": line, "detail": detail})


# ---------------------------------------------------------------------------
# 行の分類
# ---------------------------------------------------------------------------
def is_symbol_only(stripped: str) -> bool:
    """かな・漢字・英数字を 1 字も含まず、会話括弧も無い行か（◇ や ※ だけの飾り行）。"""
    if any(ch in "「『（" for ch in stripped):
        return False
    if SILENT_PROSE_RE.match(stripped):
        return False
    for ch in stripped:
        if ch.isspace():
            continue
        if unicodedata.category(ch)[0] not in ("P", "S"):
            return False
    return True


def split_doc_prefix(line: str) -> tuple:
    """(印, 本文) を返す。印は "document"（＞ を 1 個外した）/ "escaped"（エスケープを 1 字外した）/ ""。

    外すのは行頭の 1 字だけ。続く空白や 2 個目の ＞ は文面なので触らない。
    """
    if line.startswith(DOC_PREFIX):
        return "document", line[1:]
    if line.startswith(DOC_ESCAPES):
        return "escaped", line[1:]
    return "", line


def classify_line(line: str) -> str:
    """行を blank / heading / scene / symbol / dialogue / bracket / narration / document / docblank に分ける。

    字下げするのは narration だけ。会話・心内語の括弧始まり、場面転換行、空行、見出しは下げない。
    """
    mark, line = split_doc_prefix(line)
    if mark == "document":
        # 文書の中の「# 件名」や「＊」は文面であって、見出し・場面転換ではない。
        return "document" if line.strip() else "docblank"
    stripped = line.strip()
    if not stripped:
        return "blank"
    if HEADING_RE.match(line):
        return "heading"
    if SCENE_BREAK_RE.match(stripped):
        return "scene"
    if is_symbol_only(stripped):
        return "symbol"
    # 《《 始まりは傍点つきの語で始まる地の文であって、括弧始まりではない。
    if stripped[0] in OPEN_BRACKETS and not stripped.startswith("《《"):
        return "dialogue" if stripped[0] in DIALOGUE_BRACKETS else "bracket"
    return "narration"


# ---------------------------------------------------------------------------
# インライン記法（ルビ・傍点）の変換
# ---------------------------------------------------------------------------
def render_ruby(bar: str, parent: str, ruby: str, opts: Options) -> str:
    if opts.target == "alphapolis":
        return f"#{parent}__{ruby}__#"          # 公式ヘルプの例: #宇宙__そら__#
    if opts.target == "pixiv":
        return f"[[rb:{parent} > {ruby}]]"      # 公式ヘルプの例: [[rb:pixiv > ピクシブ]]
    if opts.target == "plain" and opts.plain_ruby == "paren":
        return f"{parent}（{ruby}）"
    if opts.target == "plain" and opts.plain_ruby == "drop":
        return parent
    # なろう・カクヨムは内部記法がそのまま公式記法。plain の既定もそのまま残す。
    return f"{bar}{parent}《{ruby}》"


def render_emphasis(word: str, opts: Options) -> str:
    if opts.target == "pixiv":
        return f"[[emphasismark:{word}>{opts.emphasis_mark}]]"
    if opts.strips_emphasis:
        return word
    # カクヨムは 《《語》》 が公式記法。未対応の出力先では原稿の記法のまま残す。
    return f"《《{word}》》"


def render_inline(line: str, line_no: int, opts: Options, report: Report) -> str:
    """1 行の中のルビ・傍点を出力先の記法にする。地の文の字は触らない。"""
    def replace(m):
        if m.group("emph") is not None:
            report.stats["emphasis"] += 1
            if opts.target in UNVERIFIED_EMPHASIS and not opts.strips_emphasis:
                report.warn("bouten-unsupported", line_no, m.group(0))
            return render_emphasis(m.group("emph"), opts)
        parent, ruby = m.group("parent"), m.group("ruby")
        report.stats["ruby"] += 1
        limit = RUBY_LIMITS.get(opts.target)
        if limit and (len(parent) > limit[0] or len(ruby) > limit[1]):
            report.warn("ruby-length", line_no,
                        f"親 {len(parent)} 字・ルビ {len(ruby)} 字（上限 親 {limit[0]}・ルビ {limit[1]}）")
        for bad in RUBY_FORBIDDEN.get(opts.target, ()):
            if bad in parent or bad in ruby:
                report.warn("ruby-forbidden-char", line_no, f"{m.group(0)} に {bad}")
        if m.group("bar") == "|":
            report.warn("half-bar", line_no, m.group(0))
        return render_ruby(m.group("bar"), parent, ruby, opts)

    return INLINE_RE.sub(replace, line)


# ---------------------------------------------------------------------------
# 原稿の点検（変換はしない。原稿側で直すべき点を WARN にするだけ）
# ---------------------------------------------------------------------------
def inspect_line(line: str, line_no: int, kind: str, opts: Options, report: Report) -> None:
    leftover = INLINE_RE.sub("", line)
    if BARE_RUBY_RE.search(leftover):
        # なろう・カクヨムは省略形もルビにするが、変換が要る出力先では素通しになる。
        if opts.target in ("alphapolis", "pixiv", "plain"):
            report.warn("bare-ruby", line_no)
        leftover = BARE_RUBY_RE.sub("", leftover)
    if "《" in leftover or "》" in leftover:
        report.warn("raw-kakko", line_no)
    if opts.target == "narou" and PAREN_RUBY_RE.search(leftover):
        report.warn("paren-ruby", line_no, PAREN_RUBY_RE.search(leftover).group(0))
    for pattern in COLLISION_RES.get(opts.target, ()):
        m = pattern.search(leftover)
        if m:
            report.warn("collision", line_no, m.group(0))
    if kind == "heading" and INLINE_RE.search(line):
        report.warn("title-markup", line_no)
    if kind == "narration" and line[:1].isspace():
        if not line.startswith(FW_SPACE) or line[1:2].isspace():
            report.warn("indent-odd", line_no)
    if kind in ("dialogue", "bracket") and line[:1].isspace():
        report.warn("indent-bracket", line_no)


# ---------------------------------------------------------------------------
# 変換本体
# ---------------------------------------------------------------------------
def apply_indent(line: str, opts: Options, report: Report) -> str:
    """地の文の段落頭に全角空白 1 個。既に空白で始まる行は二重に下げない。"""
    if opts.indent == "add":
        if line.startswith(FW_SPACE):
            report.stats["indent_already"] += 1
        elif line[:1].isspace():
            pass  # 半角空白・タブ始まり。indent-odd で知らせ済み。勝手に直さない。
        else:
            report.stats["indent_added"] += 1
            return FW_SPACE + line
    elif opts.indent == "remove" and line.startswith(FW_SPACE):
        report.stats["indent_removed"] += 1
        return line[1:]
    return line


def needs_gap(prev_kind: str, kind: str, policy: str) -> bool:
    """空行方針 none / dialogue / para で、2 つの行の間に空行を 1 つ入れるか。"""
    separators = ("scene", "symbol", "heading")
    if prev_kind in separators or kind in separators:
        return True  # 場面転換と見出しの前後はどの方針でも空ける
    if (prev_kind in DOC_KINDS) != (kind in DOC_KINDS):
        return True  # 作中文書のまとまりの外周は、どの方針でも空ける
    if prev_kind in DOC_KINDS:
        return False  # 文書の中の改行と空行は原稿のまま（空行は ＞ だけの行で書く）
    if policy == "para":
        return True
    if policy == "dialogue":
        return (prev_kind == "dialogue") != (kind == "dialogue")
    return False


def apply_blank_policy(items: list, policy: str) -> list:
    """items は (kind, text) の列。keep 以外は原稿の空行を捨てて方針どおりに入れ直す。

    kind が "dropped" の項目は、本文から外した見出しの跡（出力には出さない）。2 つの作中文書のあいだにあったなら、
    見出しが消えても別の文書のままにする。
    """
    if policy == "keep":
        out = []
        for i, item in enumerate(items):
            if item[0] != "dropped":
                out.append(item)
                continue
            following = next((kind for kind, _ in items[i + 1:] if kind != "dropped"), None)
            if out and out[-1][0] in DOC_KINDS and following in DOC_KINDS:
                out.append(("blank", ""))      # 原稿の空行には触らない方針でも、見出しを外して 2 つの文書がつながるのは防ぐ
        return out
    out, prev_kind, blank_seen = [], None, False
    for item in items:
        if item[0] in ("blank", "dropped"):
            blank_seen = True
            continue
        # 空行を挟んで並んだ 2 つの作中文書は別の文書。1 つにつなげない。
        split_docs = blank_seen and prev_kind in DOC_KINDS and item[0] in DOC_KINDS
        if prev_kind is not None and (split_docs or needs_gap(prev_kind, item[0], policy)):
            out.append(("blank", ""))
        out.append(item)
        prev_kind, blank_seen = item[0], False
    return out


def convert_text(text: str, opts: Options) -> tuple:
    """原稿 1 本を変換して (出力テキスト, Report) を返す。出力は末尾改行なし。"""
    text = normalize_text(text)
    report = Report()
    items = []
    indented = plain = 0
    for line_no, line in enumerate(text.split(LF), 1):
        kind = classify_line(line)
        line = split_doc_prefix(line)[1]  # 内部記法の印（＞ かエスケープ）を 1 字だけ外す。投稿先には出さない
        inspect_line(line, line_no, kind, opts, report)
        if kind == "blank":
            items.append((kind, line))
            continue
        if kind == "docblank":
            report.stats["document_lines"] += 1
            items.append((kind, ""))
            continue
        report.stats["lines"] += 1
        if kind == "document":
            # 文面は字下げも空行方針も当てず、ルビ・傍点だけを出力先の記法にする。
            report.stats["document_lines"] += 1
            items.append((kind, render_inline(line, line_no, opts, report)))
            continue
        if kind == "heading":
            title = HEADING_RE.match(line).group(1)
            report.stats["headings"].append(title)
            if opts.heading == "drop":
                items.append(("dropped", ""))  # 題はサイトの題欄に入れるもの。本文には出さない。跡だけ残す（文書の境界のため）
                continue
            if opts.heading == "text":
                line = render_inline(title, line_no, opts, report)
            elif opts.heading == "chapter":
                line = "[chapter:" + render_inline(title, line_no, opts, report) + "]"
            items.append((kind, line))
            continue
        if kind == "scene":
            report.stats["scene_breaks"] += 1
            items.append((kind, opts.scene_break or line))
            continue
        if kind == "symbol":
            items.append((kind, line))
            continue
        line = render_inline(line, line_no, opts, report)
        if kind == "narration":
            if line.startswith(FW_SPACE):
                indented += 1
            elif not line[:1].isspace():
                plain += 1
            line = apply_indent(line, opts, report)
        else:
            report.stats["indent_skipped"] += 1
        items.append((kind, line))

    if indented and plain:
        report.warn("indent-mixed", 0, f"字下げあり {indented} 行・なし {plain} 行")

    items = apply_blank_policy(items, opts.blank)
    lines = [t for _, t in items]
    # 見出しを外した跡や原稿末尾の空行を、出力の先頭・末尾に残さない。
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return LF.join(lines), report


def join_outputs(outputs: list, opts: Options) -> str:
    """--join: pixiv はファイル境界を改ページに、plain は空行 1 つにする。先頭には置かない。"""
    sep = LF + PIXIV_NEWPAGE + LF if opts.target == "pixiv" else LF + LF
    return sep.join(outputs)


# ---------------------------------------------------------------------------
# 検証: 出力を内部記法へ戻し、記法・字下げ・空行を正規化して原稿と突き合わせる
# ---------------------------------------------------------------------------
def project_source(text: str, opts: Options) -> list:
    """原稿側の期待値。オプションで意図的に落とすもの（見出し、傍点の記号など）だけを落とす。

    変換本体（render_*）とは別の素朴な置換で書いてある。同じコードで両側を作ると
    変換の誤りが検証をすり抜けるため。
    """
    out = []
    for line_no, line in enumerate(normalize_text(text).split(LF), 1):
        # 作中文書の印は原稿側でだけ外す。出力側では外さないので、印が出力に残れば不一致になる。
        in_document = line.startswith(DOC_PREFIX)
        if in_document or line.startswith(DOC_ESCAPES):
            line = line[1:]
        m = None if in_document else HEADING_RE.match(line)
        if m:
            if opts.heading == "drop":
                continue
            if opts.heading == "text":
                line = m.group(1)
        if opts.strips_emphasis:
            line = EMPHASIS_RE.sub(r"\1", line)
        if opts.target == "plain" and opts.plain_ruby == "paren":
            line = _sub_outside_emphasis(RUBY_RE, r"\1（\2）", line)
        elif opts.target == "plain" and opts.plain_ruby == "drop":
            line = _sub_outside_emphasis(RUBY_RE, r"\1", line)
        out.append((line_no, line, in_document))
    return out


def _sub_outside_emphasis(pattern, repl: str, line: str) -> str:
    """傍点の内側には手を付けずに置換する（傍点が先、の順序を検証側でも守る）。"""
    parts = EMPHASIS_RE.split(line)  # 偶数番目が傍点の外、奇数番目が傍点の中身
    for i in range(0, len(parts), 2):
        parts[i] = pattern.sub(repl, parts[i])
    rebuilt = []
    for i, part in enumerate(parts):
        rebuilt.append(part if i % 2 == 0 else "《《" + part + "》》")
    return "".join(rebuilt)


def reverse_output(text: str, opts: Options) -> list:
    """出力側の記法を内部記法へ戻す。"""
    out = []
    for line_no, line in enumerate(normalize_text(text).split(LF), 1):
        if opts.target == "alphapolis":
            line = ALPHA_RUBY_BACK_RE.sub(r"｜\1《\2》", line)
        elif opts.target == "pixiv":
            if line.strip() == PIXIV_NEWPAGE:
                continue
            m = PIXIV_CHAPTER_BACK_RE.match(line.strip())
            if m:
                line = "# " + m.group(1)
            line = PIXIV_EMPH_BACK_RE.sub(r"《《\1》》", line)
            line = PIXIV_RUBY_BACK_RE.sub(r"｜\1《\2》", line)
        out.append((line_no, line, False))
    return out


def canonical_lines(numbered: list, opts: Options) -> list:
    """(行番号, 行, 作中文書か) の列を、(行番号, 正規化した行, 字面のままの行, 作中文書か) にする。空行は落とす。

    正規化は、字下げ・場面転換の表記・見出しの段・｜の全半角を無視した形。字面のままの行は、｜の全半角だけを揃えた形で、
    作中文書の文面の突き合わせに使う（文面の「## 件名」や「＊ ＊」を見出し・場面転換として均すと、字の欠落を見逃す）。
    """
    custom_break = "".join(opts.scene_break.split())
    out = []
    for line_no, line, is_doc in numbered:
        if not line.strip():
            continue
        literal = RUBY_RE.sub(r"｜\1《\2》", line)
        s = line.lstrip(FW_SPACE)
        compact = "".join(s.split())
        if SCENE_BREAK_RE.match(s.strip()) or (custom_break and compact == custom_break):
            out.append((line_no, "＊", literal, is_doc))
            continue
        m = HEADING_RE.match(s)
        if m:
            s = "# " + m.group(1)
        out.append((line_no, RUBY_RE.sub(r"｜\1《\2》", s), literal, is_doc))
    return out


def _focus(a: str, b: str, width: int = 24) -> tuple:
    """2 つの行の最初に食い違う位置の前後だけを切り出す。"""
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    start = max(0, k - 8)
    return a[start:start + width], b[start:start + width]


def verify(source_texts: list, output_text: str, opts: Options) -> dict:
    """記法を正規化すれば出力が原稿と一致することを確かめる。"""
    expected = []
    for index, text in enumerate(source_texts):
        for line_no, line, literal, is_doc in canonical_lines(project_source(text, opts), opts):
            expected.append((index, line_no, literal if is_doc else line, is_doc))
    reversed_output = canonical_lines(reverse_output(output_text, opts), opts)
    # 出力のどの行が文面かは出力からは分からないので、原稿の同じ位置の行が文面なら字面のまま、そうでなければ正規化した形で比べる。
    # 行数がずれた場合は位置が合わなくなるが、そのときはどのみち不一致になる。
    actual = [(line_no, literal if i < len(expected) and expected[i][3] else line)
              for i, (line_no, line, literal, _) in enumerate(reversed_output)]

    a = [line for _, _, line, _ in expected]
    b = [line for _, line in actual]
    result = {"ok": a == b, "lines": len(a), "diffs": []}
    if result["ok"]:
        return result
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        src = a[i1] if i1 < i2 else ""
        dst = b[j1] if j1 < j2 else ""
        src_focus, dst_focus = _focus(src, dst)
        result["diffs"].append({
            "kind": {"replace": "変わった", "delete": "出力に無い", "insert": "出力に増えた"}[tag],
            "source_index": expected[i1][0] if i1 < len(expected) else None,
            "source_line": expected[i1][1] if i1 < len(expected) else None,
            "output_line": actual[j1][0] if j1 < len(actual) else None,
            "source": src_focus,
            "output": dst_focus,
            "count": max(i2 - i1, j2 - j1),
        })
    return result


# ---------------------------------------------------------------------------
# 入出力とプロジェクト設定
# ---------------------------------------------------------------------------
def normalize_newlines(text: str) -> str:
    return text.replace(CR + LF, LF).replace(CR, LF)


def normalize_text(text: str) -> str:
    """先頭の BOM を外し、改行を LF に揃える。ファイルを読む経路と、関数を直接呼ぶ経路で解釈が分かれないようにする。"""
    return normalize_newlines(text.lstrip(BOM))


def read_text(path: Path) -> str:
    if not path.is_file():
        raise ExportError(
            f"入力ファイルが無い: {path}\n"
            "  渡せるのは原稿ファイル（複数可）、ディレクトリ（直下の .md / .txt）、ワイルドカード。"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except UnicodeDecodeError as e:
        raise ExportError(f"UTF-8 として読めない: {path}（{e.reason}）。原稿を UTF-8 で保存し直す。")
    return normalize_newlines(text.lstrip(BOM))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=LF) as f:
        f.write(text + LF)


def expand_inputs(paths: list) -> list:
    """ディレクトリとワイルドカードを展開する（PowerShell は * を展開しないため自前で行う）。"""
    result = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found = sorted(c for c in p.iterdir() if c.is_file() and c.suffix.lower() in (".md", ".txt"))
            if not found:
                raise ExportError(f"ディレクトリ {raw} の直下に .md / .txt が無い。")
            result.extend(found)
        elif any(ch in raw for ch in "*?["):
            found = sorted(Path(f) for f in glob.glob(raw))
            if not found:
                raise ExportError(f"パターン {raw} に一致するファイルが無い。")
            result.extend(found)
        else:
            result.append(p)
    return result


def find_project(source: Path):
    """原稿の 1〜3 階層上に novel.toml があれば、そのディレクトリをプロジェクトとみなす。"""
    current = source.resolve().parent
    for _ in range(3):
        if (current / "novel.toml").is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def load_project_config(project: Path, explicit: bool) -> dict:
    """novel.toml から medium と indent を読む。読めないときは、明示指定なら止め、自動検出なら諦める。"""
    toml_path = project / "novel.toml"
    if not toml_path.is_file():
        if explicit:
            raise ExportError(
                f"{toml_path} が無い。--project にはプロジェクトのルート（novel.toml のある場所）を渡す。"
                "プロジェクトなしで使うなら --to で出力先を指定する。"
            )
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        if explicit:
            raise ExportError(
                "この Python には tomllib が無い（3.11 未満）ので novel.toml を読めない。"
                "--project を外し、--to と --indent を明示すれば同じ変換ができる。"
            )
        return {}
    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ExportError(
            f"{toml_path} を TOML として読めない: {e}\n"
            "  novel.toml を直すか、--to と --indent を明示して設定なしで変換する。"
        )
    return {
        "medium": str(data.get("work", {}).get("medium", "")),
        "indent": str(data.get("format", {}).get("indent", "")),
    }


def resolve_heading(target: str, heading: str) -> str:
    """見出しの既定: pixiv は章タグ、投稿サイトは題欄へ回すので本文から外す、plain は題の行として残す。"""
    if heading == "auto":
        return {"pixiv": "chapter", "plain": "text"}.get(target, "drop")
    if heading == "chapter" and target != "pixiv":
        raise ExportError("--heading chapter は pixiv 専用（[chapter:題] は pixiv のタグ）。他は drop / text / keep から選ぶ。")
    return heading


def resolve_output_paths(sources: list, args, target: str, project) -> list:
    """出力先のパスを決める。原稿を上書きする指定は拒む。"""
    single = args.join or len(sources) == 1
    name_for = (lambda src: f"{src.stem}.{target}.txt")
    if args.output:
        out = Path(args.output)
        looks_like_dir = out.is_dir() or args.output.endswith(("/", "\\"))
        if single and not looks_like_dir:
            paths = [out]
        else:
            if out.is_file():
                raise ExportError(
                    f"-o {args.output} は既存のファイル。複数の原稿を別々に書き出すときはディレクトリを渡す"
                    "（1 本にまとめるなら --join。pixiv と plain のみ）。"
                )
            paths = [out / (f"joined.{target}.txt" if args.join else name_for(sources[0]))] if single \
                else [out / name_for(src) for src in sources]
    else:
        base = (project / "work" / "export" / target) if project else None
        if single:
            root = base or (sources[0].parent / "export" / target)
            paths = [root / (f"joined.{target}.txt" if args.join else name_for(sources[0]))]
        else:
            paths = [(base or (src.parent / "export" / target)) / name_for(src) for src in sources]
    resolved_sources = {src.resolve() for src in sources}
    for p in paths:
        if p.resolve() in resolved_sources:
            raise ExportError(f"出力先 {p} が原稿と同じファイル。原稿は書き換えない。別のパスを -o で指定する。")
    return paths


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def describe_notation(opts: Options, stats: dict) -> str:
    ruby_how = {
        "narou": "そのまま", "kakuyomu": "そのまま",
        "alphapolis": "#親__るび__# へ", "pixiv": "[[rb:親 > るび]] へ",
        "plain": {"keep": "そのまま", "paren": "親（るび）へ", "drop": "ふりがなを落とした"}[opts.plain_ruby],
    }[opts.target]
    if opts.target == "kakuyomu":
        emph_how = "そのまま"
    elif opts.target == "pixiv":
        emph_how = "[[emphasismark:語>" + opts.emphasis_mark + "]] へ"
    elif opts.strips_emphasis:
        emph_how = "記号を外した"
    else:
        emph_how = "そのまま" if opts.target == "plain" else "未対応。原稿の記法のまま"
    parts = [f"ルビ {stats['ruby']} 件（{ruby_how}）", f"傍点 {stats['emphasis']} 件（{emph_how}）",
             f"場面転換 {stats['scene_breaks']} 行"]
    if stats.get("document_lines"):
        parts.append(f"作中文書 {stats['document_lines']} 行（行頭の ＞ を外した）")
    if stats["headings"]:
        how = {"drop": "本文から外した。サイトの題欄へ", "text": "題の行として残した",
               "keep": "そのまま", "chapter": "[chapter:題] へ"}[opts.heading]
        titles = "、".join(f"「{t}」" for t in stats["headings"][:3])
        more = " ほか" if len(stats["headings"]) > 3 else ""
        parts.append(f"見出し {len(stats['headings'])} 件（{how}: {titles}{more}）")
    return " / ".join(parts)


def describe_indent(opts: Options, stats: dict) -> str:
    if opts.indent == "add":
        return (f"add … 付与 {stats['indent_added']} 行、字下げ済み {stats['indent_already']} 行はそのまま、"
                f"括弧始まり {stats['indent_skipped']} 行は対象外")
    if opts.indent == "remove":
        return f"remove … 地の文 {stats['indent_removed']} 行から全角空白 1 個を外した"
    return "keep … 原稿のまま"


def print_warnings(warnings: list, out) -> None:
    grouped = {}
    for w in warnings:
        grouped.setdefault(w["code"], []).append(w)
    for code, items in grouped.items():
        lines = [str(w["line"]) for w in items if w["line"]][:5]
        where = f"{len(items)} 件"
        if lines:
            where += "。行 " + ", ".join(lines) + (" …" if len(items) > 5 else "")
        detail = next((w["detail"] for w in items if w["detail"]), "")
        tail = f" 例: {detail}" if detail else ""
        print(f"  WARN {WARNING_TEXT[code]}（{where}）{tail}", file=out)


def print_verify(result: dict, names: list, out) -> None:
    if result["ok"]:
        print(f"  検証: 一致（本文 {result['lines']} 行。記法・字下げ・空行を正規化すると原稿と同じ）", file=out)
        return
    print(f"  検証: FAIL 不一致 {len(result['diffs'])} 箇所。出力は原稿と同じ本文になっていない", file=out)
    for d in result["diffs"][:3]:
        src_name = names[d["source_index"]] if d["source_index"] is not None else "-"
        print(f"    {d['kind']}: 原稿 {src_name} {d['source_line']} 行目「{d['source']}」"
              f" / 出力 {d['output_line']} 行目「{d['output']}」", file=out)
    if len(result["diffs"]) > 3:
        print(f"    …ほか {len(result['diffs']) - 3} 箇所（全件は --json）", file=out)


# ---------------------------------------------------------------------------
# コマンドライン
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "内部記法の原稿を投稿先の記法に変換して別ファイルに書き出す。原稿は書き換えない。\n"
            "\n"
            "内部記法: ルビ ｜親《るび》 / 傍点 《《語》》 / 場面転換は ＊ だけの行 / 1 段落 1 行 / 見出しは # 題。\n"
            "\n"
            "このスクリプトが変えるのは記法・字下げ・空行・見出しの扱いだけで、本文の字は 1 字も直さない。\n"
            "誤字、約物、素の《》、長すぎるルビなどは WARN で知らせるので、原稿側を直して再実行する。\n"
            "変換のたびに出力を内部記法へ逆変換し、記法・字下げ・空行を正規化した本文が原稿と一致するかを検証する。\n"
            "一致しなければ書き出さず、終了コード 1 で終わる。"
        ),
        epilog=(
            "出力先ごとの変換（各サイトの公式ヘルプで 2026-09-19 に確認できた記法だけを実装）:\n"
            "  kakuyomu    ルビ・傍点とも内部記法がそのまま公式記法。上限の目安は親 20 字・ルビ 50 字。\n"
            "  narou       ルビはそのまま（親 10 字・ルビ 10 字まで）。傍点は未対応。\n"
            "              漢字の直後の（かな）がルビに化ける箇所を WARN。1 話 70,000 字の 95 パーセントで WARN。\n"
            "  alphapolis  ルビ → #親__るび__# 。傍点は未対応。1 話 100,000 字の 95 パーセントで WARN。\n"
            "  pixiv       ルビ → [[rb:親 > るび]] / 傍点 → [[emphasismark:語>﹅]] / 見出し → [chapter:題] /\n"
            "              --join のファイル境界 → [newpage]（先頭には置かない）。\n"
            "              Web 版エディタの「記法変換機能」を使うなら、変換せず kakuyomu 形式を貼ってもよい（アプリ版は不可）。\n"
            "  plain       記法をそのまま残したプレーンテキスト。--plain-ruby と --bouten で落とし方を選べる。\n"
            "\n"
            "未対応（確認できていないので実装していない）:\n"
            "  - なろう・アルファポリスの傍点（なろうの傍点タグの実体、アルファポリスの傍点記法は公式ヘルプに記載なし）。\n"
            "    原稿の 《《語》》 のまま残して WARN を出す。サイトの入力補助で付け直すか、--bouten strip で記号だけ外す。\n"
            "  - ハーメルン、ノベルアップ＋、青空文庫形式への出力。pixiv の挿絵・リンク・ジャンプ・太字・斜体のタグ。\n"
            "  - ｜を省略したルビ（漢字《かな》）の変換。親文字の境界を決められないので、原稿側で｜を付ける。\n"
            "  - カクヨム・pixiv の本文上限チェック（公式の値が未確認）。\n"
            "  - 算用数字と漢数字の変換、約物や空白の修正、公募の書式（字×行）への整形。本文は直さない。\n"
            "\n"
            "字下げ（--indent add）の規則:\n"
            "  全角空白 1 個を地の文の段落頭にだけ付ける。「『（【〈《 で始まる行、場面転換行、記号だけの行、空行、\n"
            "  見出しは下げない。既に全角空白で始まる行は二重にしない。《《 で始まる行は傍点つきの地の文なので下げる。\n"
            "  「……」や「！？」だけの行は沈黙を表す地の文として下げる（◇ ※ やダッシュだけの罫線は記号だけの行）。\n"
            "  サイトの自動字下げは原稿テキストに空白を書き込む編集機能で、表示時に下げてくれるわけではないので、\n"
            "  既定では出力に字下げを入れておく。\n"
            "\n"
            "例:\n"
            "  python export.py manuscript/ch001.md --to narou --verify\n"
            "      既定の出力先は <プロジェクト>/work/export/narou/ch001.narou.txt（プロジェクト外なら原稿の隣の export/narou/）。\n"
            "  python export.py manuscript/ch001.md\n"
            "      原稿の上の階層に novel.toml があれば、[work] medium と [format] indent を既定値に使う。\n"
            "  python export.py manuscript/ --to pixiv --join -o out/pixiv.txt\n"
            "      全章を 1 本にまとめ、章の境目に [newpage] を入れる。\n"
            "  python export.py draft.md --to plain --blank none --plain-ruby paren -o out/\n"
            "      空行を場面転換と見出しの前後だけにし、ルビを 親（るび） の形にする。\n"
            "  python export.py manuscript/ch001.md --to alphapolis --verify-file posted.txt\n"
            "      変換はせず、手元の posted.txt が原稿と同じ本文かだけを確かめる（出力を手で触った後の点検）。\n"
            "\n"
            "終了コード: 0 = 検証一致（WARN があっても 0） / 1 = 検証不一致 / 2 = 実行エラー"
        ),
    )
    parser.add_argument("sources", nargs="+", metavar="SRC",
                        help="原稿ファイル（複数可）。ディレクトリなら直下の .md / .txt、ワイルドカード可。")
    parser.add_argument("--to", choices=TARGETS, metavar="TARGET",
                        help="出力先: narou / kakuyomu / alphapolis / pixiv / plain。"
                             "省略時は novel.toml の [work] medium（print は plain）。")
    parser.add_argument("-o", "--output", metavar="PATH",
                        help="出力ファイル。原稿が複数ならディレクトリ。省略時は work/export/<出力先>/ 以下。")
    parser.add_argument("--project", metavar="DIR",
                        help="プロジェクトのルート（novel.toml の場所）。省略時は原稿の上 3 階層まで自動で探す。")
    parser.add_argument("--indent", choices=("add", "keep", "remove"),
                        help="字下げ。add = 地の文に全角空白 1 個を付与（既定）、keep = 原稿のまま、"
                             "remove = 地の文の行頭の全角空白 1 個を外す。"
                             "novel.toml の [format] indent が fullwidth / none なら既定は keep。")
    parser.add_argument("--blank", choices=("keep", "none", "dialogue", "para"), default="keep",
                        help="空行方針。keep = 原稿のまま（既定）、none = 場面転換と見出しの前後だけ（公募・縦書き向け）、"
                             "dialogue = それに加えて地の文と会話の境目、para = 全段落の間。keep 以外は原稿の空行を捨てて入れ直す。")
    parser.add_argument("--heading", choices=("auto", "drop", "text", "keep", "chapter"), default="auto",
                        help="見出し行（# 題）の扱い。auto = pixiv は [chapter:題]、plain は題の行、"
                             "投稿サイトは本文から外して題を表示（サイトの題欄に入れる）。")
    parser.add_argument("--bouten", choices=("keep", "strip"), default="keep",
                        help="傍点が未対応の出力先（narou / alphapolis / plain）での 《《語》》 の扱い。"
                             "keep = 記法のまま残す（narou / alphapolis では WARN）、strip = 記号だけ外す。")
    parser.add_argument("--plain-ruby", choices=("keep", "paren", "drop"), default="keep",
                        help="plain でのルビ。keep = ｜親《るび》のまま、paren = 親（るび）、drop = 親文字だけ。")
    parser.add_argument("--scene-break", metavar="STRING", default="",
                        help="場面転換行（＊ だけの行）をこの文字列の行に置き換える（例: \"　　　◇\"）。省略時はそのまま。")
    parser.add_argument("--emphasis-mark", metavar="CHAR", default="﹅",
                        help="pixiv の傍点に使う 1 字（既定 ﹅）。")
    parser.add_argument("--join", action="store_true",
                        help="複数の原稿を 1 本にまとめる（pixiv と plain のみ。他のサイトは 1 話ずつ投稿するので対象外）。")
    parser.add_argument("--verify", action="store_true",
                        help="書き出したファイルをディスクから読み直して、もう一度原稿と突き合わせる。"
                             "変換直後のメモリ上の検証は、このオプションが無くても常に行う。")
    parser.add_argument("--verify-file", metavar="FILE",
                        help="変換・書き出しをせず、既存の FILE が原稿と一致するかだけを検証する。"
                             "変換時と同じオプションを付ける。原稿が複数なら --join で作った 1 本とみなす。")
    parser.add_argument("--force", action="store_true",
                        help="検証が不一致でも書き出す（終了コードは 1 のまま。原因調査用）。")
    parser.add_argument("--stdout", action="store_true",
                        help="ファイルに書かず変換結果を標準出力へ出す（要約は標準エラーへ）。原稿 1 本か --join のとき。")
    parser.add_argument("--json", action="store_true", help="結果（件数・全 WARN・検証の差分）を JSON で出す。")
    return parser


def resolve_settings(args, sources: list) -> tuple:
    """プロジェクト設定とコマンドライン引数から (Options, project, メモ) を決める。引数が常に勝つ。"""
    notes = []
    explicit = bool(args.project)
    project = Path(args.project) if explicit else find_project(sources[0])
    config = load_project_config(project, explicit) if project else {}
    if project and not explicit:
        notes.append(f"プロジェクト: {project.as_posix()}（novel.toml を検出）")

    target = args.to or MEDIUM_TO_TARGET.get(config.get("medium", ""))
    if not target:
        medium = config.get("medium")
        reason = f"novel.toml の medium が '{medium}' で出力先を決められない。" if medium else "出力先が決まっていない。"
        raise ExportError(reason + " --to narou|kakuyomu|alphapolis|pixiv|plain で指定する。")
    if not args.to:
        notes.append(f"出力先: {target}（novel.toml の medium）")

    indent = args.indent or INDENT_FROM_PROJECT.get(config.get("indent", ""), "add")
    if not args.indent and config.get("indent") in INDENT_FROM_PROJECT:
        notes.append(f"字下げ: {indent}（novel.toml の indent = {config['indent']}）")

    if len(args.emphasis_mark) != 1:
        raise ExportError("--emphasis-mark は 1 字で指定する（例: ﹅ ・ ○）。")
    if args.join and target not in ("pixiv", "plain"):
        raise ExportError(
            f"--join は pixiv と plain だけで使える。{target} は 1 話ずつ投稿するので、原稿ごとに別ファイルへ書き出す。"
        )
    opts = Options(
        target=target, indent=indent, blank=args.blank,
        heading=resolve_heading(target, args.heading),
        bouten=args.bouten, plain_ruby=args.plain_ruby,
        scene_break=args.scene_break, emphasis_mark=args.emphasis_mark,
    )
    return opts, project, notes


def run(args) -> int:
    sources = expand_inputs(args.sources)
    texts = [read_text(src) for src in sources]
    names = [src.name for src in sources]
    opts, project, notes = resolve_settings(args, sources)
    info = sys.stderr if args.stdout else sys.stdout
    results = []

    # --- 既存ファイルの検証だけを行うモード ---
    if args.verify_file:
        existing = read_text(Path(args.verify_file))
        result = verify(texts, existing, opts)
        results.append({"sources": names, "output": Path(args.verify_file).as_posix(),
                        "written": False, "verify": result})
        if not args.json:
            print(f"{', '.join(names)} ⇔ {args.verify_file}（{opts.target}）", file=info)
            print_verify(result, names, info)
        return finish(args, opts, results, notes, info)

    if args.stdout and args.json:
        raise ExportError("--stdout と --json は同時に使えない（どちらも標準出力を使う）。")
    if args.stdout and len(sources) > 1 and not args.join:
        raise ExportError("--stdout は原稿 1 本か --join のときだけ使える。複数なら -o でディレクトリを指定する。")

    out_paths = [None] if args.stdout else resolve_output_paths(sources, args, opts.target, project)

    # --- 変換。--join なら全原稿で 1 ジョブ、そうでなければ原稿ごとに 1 ジョブ ---
    jobs = [(list(range(len(sources))), out_paths[0])] if (args.join or len(sources) == 1) \
        else [([i], out_paths[i]) for i in range(len(sources))]
    for indexes, out_path in jobs:
        outputs, stats_list, warnings = [], [], []
        for i in indexes:
            converted, report = convert_text(texts[i], opts)
            outputs.append(converted)
            stats_list.append(report.stats)
            for w in report.warnings:
                warnings.append(dict(w, source=names[i]))
        output_text = join_outputs(outputs, opts)
        stats = merge_stats(stats_list)

        limit = BODY_LIMITS.get(opts.target)
        if limit and len(output_text) >= limit * LIMIT_WARN_RATIO:
            warnings.append({"code": "size-limit", "line": 0, "source": names[indexes[0]],
                             "detail": f"出力 {len(output_text):,} 字 / 上限 {limit:,} 字"})

        job_names = [names[i] for i in indexes]
        result = verify([texts[i] for i in indexes], output_text, opts)
        written = False
        if result["ok"] or args.force:
            if args.stdout:
                # Windows の標準出力は LF を CRLF に直す。リダイレクト先のファイルを LF に保つ。
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(newline=LF)
                sys.stdout.write(output_text + LF)
            else:
                write_text(out_path, output_text)
                if args.verify:
                    # 書き出した実物を読み直す。エンコーディングや改行の事故をここで拾う。
                    result = verify([texts[i] for i in indexes], read_text(out_path), opts)
            written = True
        results.append({
            "sources": job_names, "output": out_path.as_posix() if out_path else "(stdout)",
            "written": written, "stats": stats, "warnings": warnings, "verify": result,
        })
        if not args.json:
            arrow = out_path.as_posix() if out_path else "(stdout)"
            print(f"{', '.join(job_names)} → {arrow}（{opts.target}）", file=info)
            print(f"  記法: {describe_notation(opts, stats)}", file=info)
            print(f"  字下げ: {describe_indent(opts, stats)} / 空行: {opts.blank}", file=info)
            print_verify(result, job_names, info)
            if not written:
                print("  検証が一致しないので書き出していない。原稿に出力先のタグと同じ形の文字列が無いかを確かめる"
                      "（原因調査のために書き出すなら --force）。", file=info)
            print_warnings(warnings, info)
    return finish(args, opts, results, notes, info)


def merge_stats(stats_list: list) -> dict:
    merged = dict(stats_list[0], headings=list(stats_list[0]["headings"]))
    for stats in stats_list[1:]:
        for key, value in stats.items():
            if key == "headings":
                merged["headings"].extend(value)
            else:
                merged[key] += value
    return merged


def finish(args, opts: Options, results: list, notes: list, info) -> int:
    failed = any(not r["verify"]["ok"] for r in results)
    if args.json:
        print(json.dumps({
            "target": opts.target, "options": asdict(opts), "notes": notes,
            "results": results, "fail": failed, "notice": AI_NOTICE,
        }, ensure_ascii=False, indent=2))
    else:
        for note in notes:
            print(note, file=info)
        print(AI_NOTICE, file=info)
    return EXIT_FAIL if failed else EXIT_OK


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ExportError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
