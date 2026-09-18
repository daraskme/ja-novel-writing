#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""count_chars.py - 日本語小説原稿の字数を実測し、原稿用紙・指定グリッドのページ数に換算する。

LLM は字数を数えられないので、字数に関する判断（規定内か、目標に届いたか）は
このスクリプトの実測値だけで行う。数え方は投稿先・応募先ごとに逆向きになるため
（なろうは空白・改行・ルビを除外、ファンタジア型の公募は空白・改行も 1 字）、
単一の「字数」を持たず 3 モードを並べて出す。

標準ライブラリのみ。終了コード: 0 = 規定違反なし / 1 = --min/--max 等の規定違反 / 2 = 実行エラー。
使い方の正本は `python count_chars.py --help`。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2

# ---------------------------------------------------------------------------
# 内部記法の定義（export.py にも同じ定義がある。tests が両者の一致を確かめる）
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

# 「!?」「!!」を 1 マスに数える公募がある（ファンタジア大賞の公式 Q&A）。
PAIR_MARKS_RE = re.compile(r"[!?！？]{2}|.")
GRID_RE = re.compile(r"^\s*(\d{1,3})\s*[xX×]\s*(\d{1,3})\s*$")
HANGING = "、。"

MODES = ("narou", "raw", "body")
MAX_FILES_IN_SUMMARY = 30


class CountError(Exception):
    """実行エラー。メッセージをそのまま表示して終了コード 2 で終わる。"""


# ---------------------------------------------------------------------------
# 行の分類と記法の除去
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

    字下げの要否と「本文として数えるか」の両方がこの分類で決まる。
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


def strip_markup(text: str) -> str:
    """ルビ（記号とふりがな）と傍点の記号を取り除き、親文字・強調された語だけ残す。

    なろう（公式 API 文書）もファンタジア大賞（公式 Q&A）もルビを字数に含めないと
    明言しているので、全モードの既定でルビを除く。傍点を先に処理するのは、
    《《語》》 の内側の《語》をルビと取り違えないため。
    """
    text = EMPHASIS_RE.sub(r"\1", text)
    text = RUBY_RE.sub(r"\1", text)
    text = BARE_RUBY_RE.sub("", text)
    return text


def heading_title(line: str) -> str:
    """見出し行から # を外した題だけを返す。"""
    m = HEADING_RE.match(line)
    return m.group(1) if m else line


def _trim_eof(text: str) -> str:
    """ファイル末尾の改行は保存時の都合で付くものなので数えない。"""
    return text.rstrip("\n")


# ---------------------------------------------------------------------------
# 3 つの数え方
# ---------------------------------------------------------------------------
def count_raw(text: str, with_markup: bool = False) -> int:
    """raw: 空白・改行も 1 字。plain 形式で書き出したテキストの長さに相当する。

    投稿上限の安全判定と、空白・改行を数える公募（ファンタジア型）に使う。
    """
    body = _trim_eof(normalize_text(text))
    if with_markup:
        return len(body)
    lines = []
    for line in body.split("\n"):
        mark, rest = split_doc_prefix(line)      # 作中文書の印は投稿先に出ないので数えない。文面は数える
        lines.append(rest if mark == "document" else heading_title(rest))
    return len(strip_markup("\n".join(lines)))


def count_narou(text: str) -> int:
    """narou: 空白・改行・ルビを除外。なろうの作品文字数の定義に合わせた近似。

    見出し行は export がサイトの題欄へ回すので数えない。場面転換の記号は
    投稿すれば字として数えられるので残す。
    """
    lines = []
    for line in _trim_eof(normalize_text(text)).split("\n"):
        mark, rest = split_doc_prefix(line)
        if mark == "document" or not HEADING_RE.match(rest):
            lines.append(rest)
    body = strip_markup("\n".join(lines))
    return sum(1 for ch in body if not ch.isspace())


def count_body(text: str) -> int:
    """body: 本文だけ。空白・記法・見出し行・記号だけの行を除く。novel_lint の字数と同じ考え方。"""
    total = 0
    for line in normalize_text(text).split("\n"):
        kind = classify_line(line)
        if kind in ("blank", "heading", "scene", "symbol", "docblank"):
            continue
        line = split_doc_prefix(line)[1]
        if kind == "document" and is_symbol_only(line.strip()):
            continue      # 文書の中の飾り行（＊ や ―――― だけ）も本文には数えない
        total += sum(1 for ch in strip_markup(line) if not ch.isspace())
    return total


def count_invisible(text: str) -> int:
    """ゼロ幅空白などの不可視文字。字数に紛れ込み、投稿先で化けることがあるので知らせる。"""
    return sum(
        1 for ch in text
        if ch not in "\n\t" and unicodedata.category(ch) in ("Cf", "Cc")
    )


# ---------------------------------------------------------------------------
# グリッド（字×行）への流し込み
# ---------------------------------------------------------------------------
def parse_grid(spec: str) -> tuple:
    m = GRID_RE.match(spec)
    if not m or int(m.group(1)) == 0 or int(m.group(2)) == 0:
        raise CountError(
            f"--grid の値 '{spec}' を読めない。'字数x行数' の形で書く（例: 42x34、40x16、20x20）。"
        )
    return int(m.group(1)), int(m.group(2))


def rows_for_cells(cells: list, width: int, hang: bool) -> int:
    """1 段落が width 字詰めで何行を占めるか。空行も 1 行。"""
    n_cells = len(cells)
    if n_cells == 0:
        return 1
    rows, i = 0, 0
    while i < n_cells:
        i += width
        rows += 1
        # ぶら下げ: 行がちょうど埋まった直後の句読点は行末にはみ出させ、次の行を作らない。
        if hang and i < n_cells and cells[i] in HANGING:
            i += 1
    return rows


def flow_rows(text: str, width: int, hang: bool = False, pair_marks: bool = False,
              assume_indent: bool = False) -> int:
    """原稿を width 字詰めに流し込んだ総行数。ルビ・傍点記号は除き、見出しは題だけを 1 行と数える。

    行頭・行末禁則による追い出しは再現しない簡易計算（ワープロの実測より少し短く出ることがある）。
    """
    body = _trim_eof(normalize_text(text))
    if not body:
        return 0
    rows = 0
    for line in body.split("\n"):
        kind = classify_line(line)
        line = split_doc_prefix(line)[1]
        plain = strip_markup(heading_title(line) if kind == "heading" else line)
        if kind in ("blank", "docblank"):
            plain = ""
        cells = PAIR_MARKS_RE.findall(plain) if pair_marks else list(plain)
        # export で字下げを付ける運用の原稿は、地の文の段落頭に 1 マス足して数える。
        if assume_indent and kind == "narration" and not line[:1].isspace():
            cells.insert(0, FW_SPACE)
        rows += rows_for_cells(cells, width, hang)
    return rows


def pages_for(rows: int, height: int) -> tuple:
    """(ページ数, 最終ページの行数)。"""
    if rows == 0:
        return 0, 0
    pages = math.ceil(rows / height)
    return pages, rows - (pages - 1) * height


# ---------------------------------------------------------------------------
# 入力
# ---------------------------------------------------------------------------
def normalize_newlines(text: str) -> str:
    """CRLF / CR を LF に揃える（Windows で保存した原稿でも改行を 1 字と数えるため）。"""
    return text.replace(CR + LF, LF).replace(CR, LF)


def normalize_text(text: str) -> str:
    """先頭の BOM を外し、改行を LF に揃える。ファイルを読む経路と、関数を直接呼ぶ経路で数が分かれないようにする。"""
    return normalize_newlines(text.lstrip(BOM))


def read_text(path: str) -> str:
    """UTF-8 で読み、BOM と CRLF を落とす。'-' は標準入力。"""
    if path == "-":
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
    else:
        p = Path(path)
        if not p.is_file():
            raise CountError(
                f"入力ファイルが無い: {path}\n"
                "  渡せるのはファイル、ディレクトリ（直下の .md / .txt）、ワイルドカード、'-'（標準入力）、--text。"
            )
        try:
            with open(p, "r", encoding="utf-8") as f:
                text = f.read()
        except UnicodeDecodeError as e:
            raise CountError(
                f"UTF-8 として読めない: {path}（{e.reason}）。原稿を UTF-8 で保存し直す。"
            )
    return normalize_newlines(text.lstrip(BOM))


def expand_inputs(paths: list) -> list:
    """ディレクトリとワイルドカードを展開する（PowerShell は * を展開しないため自前で行う）。"""
    result = []
    for raw in paths:
        if raw == "-":
            result.append(raw)
            continue
        p = Path(raw)
        if p.is_dir():
            found = sorted(
                str(c) for c in p.iterdir()
                if c.is_file() and c.suffix.lower() in (".md", ".txt")
            )
            if not found:
                raise CountError(f"ディレクトリ {raw} の直下に .md / .txt が無い。")
            result.extend(found)
        elif any(ch in raw for ch in "*?["):
            found = sorted(glob.glob(raw))
            if not found:
                raise CountError(f"パターン {raw} に一致するファイルが無い。")
            result.extend(found)
        else:
            result.append(raw)
    return result


# ---------------------------------------------------------------------------
# 計測と出力
# ---------------------------------------------------------------------------
def measure(name: str, text: str, grids: list, args) -> dict:
    entry = {
        "path": name,
        "body": count_body(text),
        "narou": count_narou(text),
        "raw": count_raw(text, with_markup=args.with_markup),
        "grids": [],
        "notes": [],
    }
    for width, height in grids:
        rows = flow_rows(text, width, args.hang, args.pair_marks, args.assume_indent)
        pages, last = pages_for(rows, height)
        entry["grids"].append({
            "grid": f"{width}x{height}", "rows": rows, "pages": pages, "last_page_rows": last,
        })
    invisible = count_invisible(text)
    if invisible:
        entry["notes"].append(f"不可視の制御文字が {invisible} 個ある（字数に含まれる。投稿前に取り除く）")
    bare = len(BARE_RUBY_RE.findall(EMPHASIS_RE.sub("", RUBY_RE.sub("", text))))
    if bare:
        entry["notes"].append(f"｜を省略したルビらしき箇所が {bare} 件（ルビとして字数から除いた。原稿では｜を付ける）")
    return entry


def total_of(entries: list, grids: list) -> dict:
    total = {
        "files": len(entries),
        "body": sum(e["body"] for e in entries),
        "narou": sum(e["narou"] for e in entries),
        "raw": sum(e["raw"] for e in entries),
        "grids": [],
    }
    for i, (width, height) in enumerate(grids):
        rows = sum(e["grids"][i]["rows"] for e in entries)
        pages, last = pages_for(rows, height)
        total["grids"].append({
            "grid": f"{width}x{height}",
            "rows": rows,
            "pages": pages,                     # 全ファイルを続けて流した場合
            "last_page_rows": last,
            "pages_break_per_file": sum(e["grids"][i]["pages"] for e in entries),
        })
    return total


def run_checks(total: dict, args) -> list:
    """--min/--max（字数）と --min-pages/--max-pages（最初の --grid のページ数）を合計値と比べる。"""
    checks = []
    if args.min is not None or args.max is not None:
        value = total[args.mode]
        if args.min is not None:
            ok = value >= args.min
            checks.append({
                "what": f"{args.mode} 字数", "value": value, "bound": f"下限 {args.min:,}", "ok": ok,
                "detail": "" if ok else f"下限まであと {args.min - value:,} 字",
            })
        if args.max is not None:
            ok = value <= args.max
            checks.append({
                "what": f"{args.mode} 字数", "value": value, "bound": f"上限 {args.max:,}", "ok": ok,
                "detail": "" if ok else f"上限を {value - args.max:,} 字超過",
            })
    if args.min_pages is not None or args.max_pages is not None:
        g = total["grids"][args.first_user_grid]
        if args.min_pages is not None:
            ok = g["pages"] >= args.min_pages
            checks.append({
                "what": f"{g['grid']} ページ数", "value": g["pages"], "bound": f"下限 {args.min_pages}",
                "ok": ok, "detail": "" if ok else f"下限まであと {args.min_pages - g['pages']} ページ",
            })
        if args.max_pages is not None:
            ok = g["pages"] <= args.max_pages
            checks.append({
                "what": f"{g['grid']} ページ数", "value": g["pages"], "bound": f"上限 {args.max_pages}",
                "ok": ok, "detail": "" if ok else f"上限を {g['pages'] - args.max_pages} ページ超過",
            })
    return checks


def format_counts(entry: dict, mode: str) -> str:
    if mode == "all":
        return f"body {entry['body']:,} / narou {entry['narou']:,} / raw {entry['raw']:,}"
    return f"{mode} {entry[mode]:,}"


def format_grid(g: dict, is_genko: bool) -> str:
    unit = "枚" if is_genko else "ページ"
    label = "400字詰" if is_genko else g["grid"]
    if g["rows"] == 0:
        return f"{label}: 0 {unit}"
    return f"{label}: {g['rows']:,} 行 → {g['pages']:,} {unit}（最終{unit} {g['last_page_rows']} 行）"


def print_summary(entries: list, total: dict, checks: list, grids: list, args) -> None:
    genko_index = 0 if args.show_genko else None
    shown = entries[:MAX_FILES_IN_SUMMARY]
    for e in shown:
        print(f"{e['path']}: {format_counts(e, args.mode)}")
        for i, g in enumerate(e["grids"]):
            print("  " + format_grid(g, i == genko_index))
        for note in e["notes"]:
            print(f"  注意: {note}")
    if len(entries) > len(shown):
        print(f"…ほか {len(entries) - len(shown)} 件（全件は --json）")
    if len(entries) > 1:
        print(f"合計（{total['files']} ファイル）: {format_counts(total, args.mode)}")
        for i, g in enumerate(total["grids"]):
            line = "  " + format_grid(g, i == genko_index)
            if g["pages_break_per_file"] != g["pages"]:
                unit = "枚" if i == genko_index else "ページ"
                line += f" / ファイルごとに改ページすると {g['pages_break_per_file']:,} {unit}"
            print(line)
    for c in checks:
        verdict = "OK" if c["ok"] else f"FAIL（{c['detail']}）"
        print(f"規定: {c['what']} {c['value']:,} / {c['bound']} → {verdict}")
    if grids:
        print("換算は禁則の追い出しを再現しない近似。公募は要項の指定グリッドと数え方を正とし、"
              "カクヨムの正確な字数はプレビュー画面で確かめる。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="count_chars.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "日本語小説原稿の字数を実測し、原稿用紙・指定グリッドのページ数に換算する。\n"
            "字数の数え方は投稿先・応募先で逆向きになるので、3 つのモードを並べて出す。\n"
            "\n"
            "  body   本文だけ。空白・改行・ルビと傍点の記法・見出し行・記号だけの行（＊ ◇ など。「……」だけの行は本文）を除く。\n"
            "         執筆の目標字数や novel_lint の統計と比べるときの数。\n"
            "  narou  空白・改行・ルビ（記号とふりがな）・傍点の記号・見出し行を除く。場面転換の記号は数える。\n"
            "         小説家になろうの作品文字数の定義（公式 API 文書）に合わせた近似。\n"
            "  raw    空白・改行も 1 字。ルビと傍点の記号、見出しの # だけ除く。plain 書き出しの長さに相当。\n"
            "         投稿上限の安全判定や、空白・改行を数える公募（ファンタジア大賞型）に使う。\n"
            "\n"
            "ルビは全モードで数えない（なろうもファンタジア大賞も原稿量に含めないと明言している）。\n"
            "ファイル末尾の改行は数えない。文字はコードポイント単位で数える。"
        ),
        epilog=(
            "例:\n"
            "  python count_chars.py manuscript/ch001.md\n"
            "      3 モードの字数と 400 字詰換算を 1 行ずつ出す。\n"
            "  python count_chars.py manuscript/ --mode narou\n"
            "      ディレクトリ直下の .md / .txt を全部数え、合計も出す。\n"
            "  python count_chars.py draft.md --mode body --max 4000\n"
            "      上限 4,000 字の規定と比べる。超えていれば終了コード 1。\n"
            "  python count_chars.py draft.md --grid 42x34 --min-pages 80 --max-pages 130\n"
            "      42 字×34 行に流したページ数を規定と比べる。\n"
            "  python count_chars.py draft.md --grid 40x16 --hang --pair-marks\n"
            "      行末句読点のぶら下げと「!?」1 字扱い（ファンタジア大賞の公式 Q&A の数え方）。\n"
            "  python count_chars.py --text \"ありがとう\"\n"
            "      語の字数を確かめる（本文に「〜の五文字」と書く前の検算）。括弧や句読点も 1 字と数えるので語だけを渡す。\n"
            "\n"
            "換算について:\n"
            "  400 字詰換算は「総字数÷400」ではなく、20 字×20 行に流し込んだ行数から出す（空行も 1 行）。\n"
            "  グリッドの既定値は持たない。賞ごとに 42x34、40x16、30x40、40x40 と違うので、必ず最新の要項から転記する。\n"
            "  行頭・行末禁則による追い出し、縦中横は再現しない。半角文字も 1 マスと数える。\n"
            "\n"
            "未対応: カクヨム・pixiv 固有の数え方（公式の定義が未確認。narou の値を近似として使い、サイトの表示で確かめる）。\n"
            "総合スコアや良し悪しの判定は出さない。出すのは実測値と、指定された規定との比較だけ。\n"
            "\n"
            "終了コード: 0 = 規定違反なし（規定を指定しなければ常に 0） / 1 = 規定違反あり / 2 = 実行エラー"
        ),
    )
    parser.add_argument(
        "paths", nargs="*", metavar="PATH",
        help="原稿ファイル（複数可）。ディレクトリなら直下の .md / .txt、ワイルドカード可、'-' で標準入力。",
    )
    parser.add_argument(
        "--text", metavar="STRING",
        help="ファイルの代わりに、渡した文字列そのものを数える（数え上げの検算用）。",
    )
    parser.add_argument(
        "--mode", choices=MODES + ("all",), default="all",
        help="出す数え方。既定 all は 3 モードを並べる。--min / --max を使うときは 1 つに絞る。",
    )
    parser.add_argument(
        "--grid", action="append", default=[], metavar="WxH",
        help="字数x行数のグリッドに流し込んだページ数を出す（例: 42x34）。複数指定可。",
    )
    parser.add_argument(
        "--no-genko", action="store_true",
        help="400 字詰（20x20）換算の行を出さない。",
    )
    parser.add_argument(
        "--hang", action="store_true",
        help="グリッド計算で行末の「、」「。」をぶら下げ、次の行に送らない。",
    )
    parser.add_argument(
        "--pair-marks", action="store_true",
        help="グリッド計算で「!?」「!!」「！？」など感嘆符・疑問符の 2 連を 1 マスと数える。",
    )
    parser.add_argument(
        "--assume-indent", action="store_true",
        help="字下げの無い地の文の段落頭に 1 マス足して流し込む（字下げを export 時に付ける運用の原稿用）。",
    )
    parser.add_argument(
        "--with-markup", action="store_true",
        help="raw モードでルビ・傍点の記号とふりがな、見出しの # も打ったとおりに数える"
             "（投稿欄に貼る文字列そのものの長さ。上限の安全側の見積もり）。",
    )
    parser.add_argument("--min", type=int, metavar="N", help="--mode の字数（合計）の下限。下回れば終了コード 1。")
    parser.add_argument("--max", type=int, metavar="N", help="--mode の字数（合計）の上限。超えれば終了コード 1。")
    parser.add_argument("--min-pages", type=int, metavar="N",
                        help="最初の --grid のページ数（全ファイルを続けて流した値）の下限。")
    parser.add_argument("--max-pages", type=int, metavar="N",
                        help="最初の --grid のページ数（全ファイルを続けて流した値）の上限。")
    parser.add_argument("--json", action="store_true", help="全ファイルの結果を JSON で出す。")
    return parser


def validate_args(args) -> None:
    if not args.paths and args.text is None:
        raise CountError(
            "数える対象が無い。原稿ファイルかディレクトリを渡すか、--text \"文字列\" を使う（詳しくは --help）。"
        )
    if (args.min is not None or args.max is not None) and args.mode == "all":
        raise CountError(
            "--min / --max は、どの数え方と比べるかを --mode narou|raw|body で指定して使う"
            "（数え方は応募先の規定に合わせる。空白・改行を数える規定なら raw）。"
        )
    if (args.min_pages is not None or args.max_pages is not None) and not args.grid:
        raise CountError(
            "--min-pages / --max-pages は --grid と一緒に使う"
            "（例: --grid 42x34 --min-pages 80。400 字詰なら --grid 20x20）。"
        )


def run(args) -> int:
    validate_args(args)
    user_grids = [parse_grid(g) for g in args.grid]
    # 400 字詰換算は原稿ファイルのときだけ出す（--text の検算では雑音になる）。
    args.show_genko = not args.no_genko and bool(args.paths)
    grids = ([(20, 20)] if args.show_genko else []) + user_grids
    args.first_user_grid = 1 if args.show_genko else 0

    entries = []
    if args.text is not None:
        entries.append(measure("(--text)", normalize_newlines(args.text), grids, args))
    for path in expand_inputs(args.paths):
        name = "(stdin)" if path == "-" else Path(path).as_posix()
        entries.append(measure(name, read_text(path), grids, args))

    total = total_of(entries, grids)
    checks = run_checks(total, args)
    failed = any(not c["ok"] for c in checks)

    if args.json:
        print(json.dumps(
            {"mode": args.mode, "files": entries, "total": total, "checks": checks, "fail": failed},
            ensure_ascii=False, indent=2,
        ))
    else:
        print_summary(entries, total, checks, grids, args)
    return EXIT_FAIL if failed else EXIT_OK


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except CountError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
