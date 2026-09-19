#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""novel_lint.py -- 日本語小説の本文を機械検査する（標準ライブラリのみ）。

検査するもの:
  N 表記        三点リーダー・ダッシュの形、感嘆符疑問符の後の空白、「。」の位置、字下げの混在、半角記号、Markdown 装飾
  R/L/P/T       文末の連続、文長、段落、体言止めの統計
  S/D/K         比喩標識、会話、語彙の密度（辞書は data/lexicon.json）
  C 数え上げ    「『ありがとう』の四文字」のような字数・音数の言い間違いを実数と突き合わせる
  M メタ混入    本文が空（保存事故）、前置き・後書き・解説の混入
  X 漏出        創作用語・スキルの装置名が地の文に出ていないか
  G 作品固有    glossary.toml の禁止語彙・表記ゆれ（--project 指定時）
  O 冒頭と結び  ありがちな入り方・締め方の型（参考情報）

作中文書（チャット・メール・手紙・貼り紙）の範囲は、文面の各行の頭に全角 ＞ を付けて明示できる（任意。references/notation.md §3）。
明示した行の表記は N09（要確認）に回り、字下げの検査と文の統計から外れる。数え上げ（C）と Markdown 装飾（N07）は文面にも当たる。

考え方:
  - FAIL にするのは、表記の機械的な誤り（N）、数え上げの不一致（C）、本文が空（M01）だけ。統計と密度は INFO / WARN / 強WARN の警報。
  - 警報は検知器であって判決ではない。ヒットごとに fix か keep を選ぶ。目標の比率は無く、警報をゼロにすることも目標にしない。
  - どのルールも style/lint.json の overrides に {"ID": {"off": true, "reason": "…"}} と書けば止められる（作者の流儀）。
  - 総合スコアは出さない。警報線は data/thresholds.json が唯一の権威。作品ごとの上書きは style/lint.json。

終了コード: 0 = FAIL なし / 1 = FAIL あり / 2 = 実行エラー
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import statistics
import sys
import unicodedata
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

DATA_DIR = Path(__file__).resolve().parent / "data"
SEVERITY_ORDER = {"FAIL": 0, "STRONG": 1, "WARN": 2, "INFO": 3}
SEVERITY_LABEL = {"FAIL": "FAIL", "STRONG": "強WARN", "WARN": "WARN", "INFO": "INFO"}
GROUPS = "NRLPTSDKCMXGO"

# ---------------------------------------------------------------------------
# 前処理と計数規約
# ---------------------------------------------------------------------------
WS = re.compile(r"[\s　]")
JP = re.compile(r"[ぁ-んァ-ヶ一-龥]")
QUOTE = re.compile(r"「[^「」]*」")
QUOTE2 = re.compile(r"『[^『』]*』")   # 「」の外にある『』（『』を台詞に使う流儀、作中の題名など）
MARK = "\x00"
SENT_SPLIT = re.compile(r"(?<=[。！？!?])[」』）)]*")
TAIL = re.compile(r"[。！？!?…―—─\s　」』）)\x00]+$")
HEADING = re.compile(r"^\s*#{1,6}\s")
RUBY_EMPH = re.compile(r"《《([^《》\n]+)》》")
RUBY_BAR = re.compile(r"[｜|]([^｜|《》\n]{1,30})《[^《》\n]{1,40}》")
RUBY_KANJI = re.compile(r"([一-龥々〆ヶ]+)《[^《》\n]{1,40}》")
COUNTABLE = re.compile(r"[ぁ-ゖァ-ヺー一-龥々〆a-zA-Zａ-ｚＡ-Ｚ0-9０-９]")
KANJI = re.compile(r"[一-龥々〆]")
KANA_ONLY = re.compile(r"^[ぁ-ゖァ-ヺー]+$")
SMALL_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ")


def has_letters(s: str) -> bool:
    """文字か数字を 1 つでも含むか（記号と空白だけの行を本文から外すため）。英文や拡張漢字の行は本文。"""
    return any(unicodedata.category(c)[0] in ("L", "N") for c in s)


def nws(s: str) -> int:
    """空白を除いた字数。"""
    return len(WS.sub("", s))


def strip_notation(s: str) -> str:
    """ルビ・傍点の記法を外して親文字だけにする（字数と統計を記法で歪めないため）。"""
    s = RUBY_EMPH.sub(r"\1", s)
    s = RUBY_BAR.sub(r"\1", s)
    s = RUBY_KANJI.sub(r"\1", s)
    return s


def strip_quotes_inner(s: str) -> str:
    """入れ子の内側の「」を空にして、外側の「…」だけが 1 発話として拾えるようにする。"""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"(「[^「」]*)「[^「」]*」", r"\1", s)
    return s


def strip_quotes(s: str, repl: str = "") -> str:
    """台詞「…」を内側から繰り返し取り除く。"""
    prev = None
    while prev != s:
        prev = s
        s = QUOTE.sub(repl, s)
    return s


PAIRS = {"「": "」", "『": "』"}
NO_INDENT_OPENERS = ("「", "『", "（", "(", "【", "〈", "《", "［", "〔")   # export.py の OPEN_BRACKETS と同じ考え方。行頭の括弧は下げない


def brackets_balanced(s: str) -> bool:
    """「」『』の順序と種類の対応をスタックで確かめる。"""
    stack = []
    for c in s:
        if c in PAIRS:
            stack.append(PAIRS[c])
        elif c in PAIRS.values():
            if not stack or stack.pop() != c:
                return False
    return not stack


SPEECH_VERB = r"(?:と|って)[\s　]*(?:言|い[っわ]|答|応|叫|呟|囁|返|訊|聞|尋|問|頼|命|告|怒鳴|笑|続け|繰り返|書|記|つぶや|ささや|こぼ)"


def utterance_spans(body: str):
    """行の中の発話を (開始, 終了) の列で返す。外側の括弧を 1 発話とし、入れ子の中身は数えない。

    - 「…」は発話。
    - 『…』は、(a) 行頭から行末までを占めるとき、(b) 直後が「と言った」などの引用動詞のとき、だけ発話。
      『書名』という雑誌、『旅』を買った、のような書名・強調は地の文に残す。
    括弧の対応が壊れている行では、対応のとれた部分だけを拾う。"""
    spans, stack = [], []
    for i, c in enumerate(body):
        if c in PAIRS:
            stack.append((c, i))
        elif c in PAIRS.values() and stack and PAIRS[stack[-1][0]] == c:
            opener, start = stack.pop()
            if stack:
                continue                     # 入れ子の内側は、外側の発話に含まれる
            if opener == "「":
                spans.append((start, i + 1))
            else:
                rest = body[i + 1:]
                whole_line = start == 0 and rest.strip(" 　") == ""
                if whole_line or re.match(SPEECH_VERB, rest):
                    spans.append((start, i + 1))
    return spans


def strip_all_quotes(s: str, repl: str = "") -> str:
    """発話を repl に置き換える。"""
    lead = len(s) - len(s.lstrip(" \t　"))
    body = s[lead:]
    out, last = [], 0
    for a, b in utterance_spans(body):
        out.append(body[last:a])
        out.append(repl)
        last = b
    out.append(body[last:])
    return s[:lead] + "".join(out)


def ending_class(sent: str) -> str:
    """文末の粗い分類（形態素解析なし）。"""
    core = TAIL.sub("", sent)
    if not core:
        return "other"
    if re.search(r"(です|ます|でした|ました|ません|でしょう|ましょう)$", core):
        return "masu"
    # 表層の末尾パターンによる近似。「〜なんだ」「行くんだ」は説明のダ、「嫌いだ」「きれいだ」は断定のダ
    # 説明の「んだ」: なんだ／〜るんだ／〜くんだ／〜ったんだ／〜ないんだ、漢字語幹＋う段＋んだ（遊ぶんだ・望むんだ・待つんだ）
    if re.search(r"(?:な|[るく]|った|ない|たい|しい)んだ$", core) or re.search(r"[一-龥々][うぐすつぬぶむ]んだ$", core) or re.search(
            r"(嫌い|きらい|きれい|綺麗|みたい|くらい|ぐらい|せい|幸い|いっぱい|一杯|おしまい|違い|勢い|次第|匂い|思い|願い)だ$", core):
        return "da"
    if re.search(r"(あした|明日|うた|へた|下手)$", core):
        return "taigen"
    if re.search(r"(た|んだ|いだ)$", core):
        return "ta"
    if re.search(r"(だ|である)$", core):
        return "da"
    if re.search(r"(ない|ぬ|ず)$", core):
        return "nai"
    if re.search(r"い$", core):
        return "i"
    if re.search(r"[うくぐすつづぬぶむる]$", core):
        return "ru"
    if re.search(r"[一-龥々ァ-ヶー]$", core):
        return "taigen"
    return "other"


def cv(vals):
    if len(vals) < 2:
        return None
    m = statistics.mean(vals)
    return statistics.pstdev(vals) / m if m else None


def excerpt(s: str, n: int = 24) -> str:
    s = re.sub(r"[\r\n]+[\s　]*", " / ", s.replace(MARK, "").strip()).strip(" 　/")
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------------------
# 数の読み取り（C 群）
# ---------------------------------------------------------------------------
KANJI_DIGIT = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
NUM = r"(?:[0-9０-９]+|[一二三四五六七八九十百〇]+|ひと|ふた)"


def parse_number(s: str):
    """漢数字（〜999）、ひと/ふた、半角・全角の算用数字を整数にする。読めなければ None。"""
    s = s.strip()
    if s == "ひと":
        return 1
    if s == "ふた":
        return 2
    z = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if z.isdigit():
        return int(z)
    if not s or any(c not in "一二三四五六七八九十百〇" for c in s):
        return None
    if all(c in KANJI_DIGIT for c in s):  # 「一〇」式の位取り
        return int("".join(str(KANJI_DIGIT[c]) for c in s))
    total, cur = 0, 0
    for c in s:
        if c in KANJI_DIGIT:
            cur = KANJI_DIGIT[c]
        elif c == "十":
            total += (cur or 1) * 10
            cur = 0
        elif c == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def count_chars_of(x: str) -> int:
    """「X」の字数。約物・空白・記法を除き、文字（L）と数字（N）を 1 字として数える。"""
    t = unicodedata.normalize("NFC", strip_notation(x))
    return sum(1 for c in t if unicodedata.category(c)[0] in ("L", "N"))


def ruby_reading(x: str) -> str:
    """ルビがあれば読みに置き換える（拍数は読みで数える）。"""
    x = RUBY_EMPH.sub(r"\1", x)
    x = re.sub(r"[｜|][^｜|《》\n]{1,30}《([^《》\n]{1,40})》", r"\1", x)
    return re.sub(r"[一-龥々〆ヶ]+《([^《》\n]{1,40})》", r"\1", x)


def count_morae(x: str):
    """かなだけの語の拍数。拗音の小書きは前の字と 1 拍、促音・長音・撥音は 1 拍。漢字を含めば None。"""
    t = unicodedata.normalize("NFC", ruby_reading(x))
    core = "".join(c for c in t if unicodedata.category(c)[0] in ("L", "N"))
    if not core or not KANA_ONLY.match(core):
        return None      # 漢字・踊り字・英数字などが 1 字でもあれば「数えられない」とする（落として数えない）
    return sum(1 for c in core if c not in SMALL_KANA)


def morae_ambiguous(x: str) -> bool:
    """「あぁ」「えぇ」のように小書きの母音が長音として読まれうる表記は、拍数が一意に決まらない。"""
    core = "".join(c for c in ruby_reading(x) if unicodedata.category(c)[0] == "L")
    if core and all(c in SMALL_KANA or c in "っッー" for c in core):
        return True              # 「ゃ」「っ」だけを取り出して数える話は、数え方が一意に決まらない
    return bool(re.search(r"[ぁぃぅぇぉァィゥェォ]", core))


# ---------------------------------------------------------------------------
# 設定の読み込み
# ---------------------------------------------------------------------------
def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_toml(path: Path):
    if tomllib is None:
        raise RuntimeError("TOML を読むには Python 3.11 以上が必要（tomllib）。--project を外せば TOML 無しで検査できる")
    with open(path, "rb") as f:
        return tomllib.load(f)


class Config:
    def __init__(self, profile: str, length: str, project: Path | None):
        self.thresholds = load_json(DATA_DIR / "thresholds.json")
        self.lexicon = load_json(DATA_DIR / "lexicon.json")
        self.guards = self.thresholds["guards"]
        self.profile = profile
        self.length = length
        self.indent_policy = None       # fullwidth | none | export
        self.emotion_naming = None
        self.overrides = {}
        self.forbidden_words = []
        self.variants = []              # (variant, canonical)
        self.notes = []
        if project:
            self._load_project(project)

    def _load_project(self, root: Path):
        novel = root / "novel.toml"
        if novel.is_file():
            data = load_toml(novel)
            work = data.get("work", {})
            if not self.profile and work.get("profile") in ("bungei", "entertainment", "web"):
                self.profile = work["profile"]
            if not self.length and work.get("length") in ("flash", "short", "long"):
                self.length = work["length"]
            self.indent_policy = data.get("format", {}).get("indent")
            self.emotion_naming = data.get("contract", {}).get("emotion_naming")
        else:
            self.notes.append(f"{novel} が無い。プロファイルは --profile か既定値を使う")
        lint_json = root / "style" / "lint.json"
        if lint_json.is_file():
            data = load_json(lint_json)
            self.overrides = data.get("overrides", {}) if isinstance(data, dict) else {}
        glossary = root / "style" / "glossary.toml"
        if glossary.is_file():
            g = load_toml(glossary)
            self.forbidden_words = [w for w in g.get("constraints", {}).get("forbidden_words", []) if w]
            for term in g.get("terms", []):
                for v in term.get("variants", []) or []:
                    if v:
                        self.variants.append((v, term.get("canonical", "")))

    def finalize(self):
        self.profile = self.profile or "entertainment"
        self.length = self.length or "long"

    def levels(self, rule_id: str, variant: str | None = None) -> dict:
        """ルールの警報線。style/lint.json の overrides ＞ プロファイル ＞ default。"""
        rule = self.thresholds["rules"][rule_id]
        profiles = rule["profiles"]
        base = dict(profiles.get(variant or self.profile) or profiles.get("default") or {})
        if variant and variant not in profiles:
            base = dict(profiles.get(self.profile) or profiles.get("default") or {})
        ov = self.overrides.get(rule_id)
        if isinstance(ov, dict):
            if ov.get("off"):
                return {"off": True}
            base.update({k: v for k, v in ov.items() if k not in ("reason", "off")})
        if self.emotion_naming == "direct" and rule_id == "K08" and not (isinstance(ov, dict) and ov.get("enable") is True):
            return {"off": True}      # 感情直球を契約した作品では名指しを数えない。機械的な較正でこの契約を解除させない
        return base

    def is_off(self, rule_id: str) -> bool:
        ov = self.overrides.get(rule_id)
        return isinstance(ov, dict) and bool(ov.get("off"))

    def rule(self, rule_id: str) -> dict:
        return self.thresholds["rules"][rule_id]

    def rx(self, key: str):
        return re.compile(self.lexicon[key], re.M)


# ---------------------------------------------------------------------------
# 本文の分解
# ---------------------------------------------------------------------------
# 作中文書（チャット・メール・手紙・貼り紙）の範囲を原稿の中で明示する記号。行頭の全角 ＞ 1 字（export.py / count_chars.py と同じ定義）。
# ＞ だけの行は文書の中の空行。行頭の ＞ を本文として残すときは、その前に \ か ＼ を置く。
DOC_PREFIX = chr(0xFF1E)
DOC_ESCAPES = (chr(0x5C) + DOC_PREFIX, chr(0xFF3C) + DOC_PREFIX)
# 沈黙・絶句だけの行（…… や ！？）。文の統計には入れないが、本文の字数には入れる（export.py / count_chars.py の SILENT_PROSE_RE と同じ）
SILENT_LINE = re.compile(r"^[…‥！？!?。、―—\s]*[…‥！？!?][…‥！？!?。、―—\s]*$")


class Doc:
    """1 段落 1 行の本文を、地の文・台詞・作中文書・文に分解して持つ。行番号は元ファイルのもの。

    行頭の ＞ で明示された作中文書（kind="document"）は、字数には入れるが、地の文と台詞のどちらにも数えない。
    文長・文末・段落の統計には入れず、同一文末の連続もそこで切る（前後の地の文をつながない）。"""

    def __init__(self, text: str):
        source = text.lstrip(chr(0xFEFF)).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.explicit_doc = set()   # ＞ で明示された行の番号（＞ だけの行を含む）
        self.raw_lines = []         # 印（＞ かエスケープ）を 1 字だけ外した行。以後の検査はこちらを見る
        for i, raw in enumerate(source, 1):
            if raw.startswith(DOC_PREFIX):
                self.explicit_doc.add(i)
                raw = raw[1:]
            elif raw.startswith(DOC_ESCAPES):
                raw = raw[1:]
            self.raw_lines.append(raw)
        self.paras = []        # dict(line, raw, body, kind, narr, sents=[(text, cls, is_tag)], n_utt)
        self.dialogues = []    # (line, text)
        self.unbalanced = []   # (line, excerpt) 括弧が行内で閉じていない
        self.silent_lines = []  # (行番号, 本文) 沈黙だけの行（……）。段落にはしないが、字数と全文検索（約物の頻度）には入れる
        for i, raw in enumerate(self.raw_lines, 1):
            in_doc = i in self.explicit_doc
            if not raw.strip() or (HEADING.match(raw) and not in_doc):      # 文書の中の「# 件名」は文面
                continue
            if not has_letters(raw) and not re.search(r"[「『]", raw):
                # ＊ や …… だけの行（場面転換・飾り・沈黙）。無言の台詞「……」は本文として残す。
                # 字数の数え方は count_chars.py の body に合わせる: 沈黙（……、！？）と丸括弧の行は数え、飾り（＊、――――）は数えない
                if SILENT_LINE.match(raw.strip()) or "（" in raw:
                    self.silent_lines.append((i, strip_notation(raw.rstrip())))
                continue
            clean = strip_notation(raw.rstrip())
            body = clean.lstrip(" \t　")
            if not brackets_balanced(body):
                self.unbalanced.append((i, excerpt(body)))
            if in_doc:
                self.paras.append(dict(line=i, raw=raw, clean=clean, body=body, kind="document", narr="", sents=[], n_utt=0,
                                       indented=False, starts_quote=False, lead=""))
                continue
            for a, b in utterance_spans(body):
                self.dialogues.append((i, body[a + 1:b - 1]))
            marked = strip_all_quotes(body, MARK)
            n_utt = marked.count(MARK)
            narr = marked.replace(MARK, "")
            starts_quote = body.startswith(("「", "『"))
            if nws(narr) == 0 and n_utt:
                kind = "dialogue"
            elif starts_quote or n_utt:
                kind = "mixed"
            else:
                kind = "narration"
            sents = []
            if kind != "dialogue":
                for seg in SENT_SPLIT.split(marked):
                    plain = seg.replace(MARK, "")
                    if nws(plain) < 2:
                        continue
                    is_tag = bool(re.match(r"^[\s　]*\x00[、,]?[\s　]*(と|って)", seg))
                    sents.append((plain, ending_class(plain), is_tag))
            self.paras.append(dict(line=i, raw=raw, clean=clean, body=body, kind=kind, narr=narr,
                                   sents=sents, n_utt=max(n_utt, 1) if kind == "dialogue" else n_utt,
                                   indented=raw.startswith("　"), starts_quote=starts_quote,
                                   lead=re.match(r"[ \t　]*", raw).group(0)))
        self.total_chars = sum(nws(p["clean"]) for p in self.paras) + sum(nws(t) for _, t in self.silent_lines)
        self.narr_chars = sum(nws(p["narr"]) for p in self.paras)
        self.dlg_chars = sum(nws(d) for _, d in self.dialogues)

    def document_between(self, line_a: int, line_b: int) -> bool:
        """2 つの行のあいだに、＞ で明示された行があるか（＞ だけの行や、記号だけで段落にならなかった文面も含む）。
        明示された文書は、文末の連続や「文書を導入する段落」の持ち越しをそこで切る境界になる。"""
        return any(n in self.explicit_doc for n in range(line_a + 1, line_b))

    def narr_lines(self):
        """(行番号, 地の文) の列。密度系の検索対象。"""
        return [(p["line"], p["narr"]) for p in self.paras if nws(p["narr"])]

    def all_lines(self):
        """(行番号, 本文) の列。全文を分母にする検索の対象。字数に入れた行は、ここにも入れる（分母だけ増えて検出が減るのを防ぐ）。"""
        return sorted([(p["line"], p["clean"]) for p in self.paras] + self.silent_lines)


# ---------------------------------------------------------------------------
# ヒットの入れ物
# ---------------------------------------------------------------------------
class Report:
    def __init__(self, cfg: Config, only: str, skip: str):
        self.cfg = cfg
        self.hits = []
        self.only = only
        self.skip = skip

    flash_skip = frozenset()

    def enabled(self, rule_id: str) -> bool:
        if rule_id in self.flash_skip:
            return False
        g = rule_id[0]
        if self.only and g not in self.only:
            return False
        return g not in self.skip

    def add(self, rule_id, severity, name, message, locations=(), hint="", value=None, threshold=None):
        if not self.enabled(rule_id) or self.cfg.is_off(rule_id):
            return
        self.hits.append(dict(rule=rule_id, severity=severity, name=name, message=message, hint=hint,
                              value=value, threshold=threshold,
                              locations=[dict(line=l, excerpt=e) for l, e in locations]))

    def add_level(self, rule_id, value, levels, fmt, locations=(), lower=False, variant_note=""):
        """value を levels(info/warn/strong) と比べ、いちばん重い段で 1 件だけ記録する。"""
        if levels.get("off") or value is None:
            return
        rule = self.cfg.rule(rule_id)
        for key, sev in (("strong", "STRONG"), ("warn", "WARN"), ("info", "INFO")):
            th = levels.get(key)
            if th is None:
                continue
            if (value < th) if lower else (value >= th):
                self.add(rule_id, sev, rule["name"], fmt.format(v=value, th=th) + variant_note,
                         locations, rule.get("hint", ""), value, th)
                return


# ---------------------------------------------------------------------------
# N 表記
# ---------------------------------------------------------------------------
SPOKEN_AFTER = re.compile(r"(?:と|って)[^。「」『』]{0,14}?(?:言|い[っわ]|答|応|叫|呟|囁|返[しすっ]|訊|聞[いか]|尋|問|頼|命|告|怒鳴|笑|続け|繰り返|つぶや|ささや|こぼ|漏ら|切り出)")
NOUN_PARTICLE_AFTER = re.compile(r"(?:の|を|が|に|は|も|へ|や|で|という|とは|といった|とか|なる|だ|です|。|、)")


MENTION_CUE = re.compile(r"題名|表題|題[はがを]|タイトル|書名|誌名|札|看板|貼り紙|張り紙|掲示|案内板|標識|文字列|検索語|件名|表示|印刷|書かれ|書いてあ|記され|"
                         r"刻まれ|彫られ|とあった|こうあった|次の|ラベル|見出し|文面|署名|宛名|メニュー|献立|品書|名札|表札|画面")
SHORT_SUBJECT = re.compile(r"^[^、。]{1,10}(?:は|が|も)、?$")


def quote_roles(body: str, prev_text: str = "", next_text: str = "", carried_cue: bool = False):
    """行の中の引用を (開始, 終了, 役割) で返す。役割は "speech"（台詞）/ "other"（書名・固有名・看板・件名・入力文字列、分類できないもの）。
    入れ子の内側（台詞の中の『書名』など）は常に "other"。

    台詞と言えるのは、外側の引用で、次のどれかのときだけ。迷ったら "other"（N09 WARN）に倒す。
      (a)「」が文の頭（行頭か、直前が句点・閉じ括弧）にあり、行末で閉じる
      (b) 直後が「と言った」の類で、引用の前が無いか、短い主語（彼は、姉が）だけ
      (c)「」が文の頭にあり、直後に台詞の「」が続く
    引用の前や、前の段落に「題名」「札」「表示」「件名」などの語があれば、台詞としない。
    『』だけの行は、隣の段落を見ないと書名か台詞か決められないので "other"。"""
    spans, stack = [], []
    for i, c in enumerate(body):
        if c in PAIRS:
            stack.append((c, i))
        elif c in PAIRS.values() and stack and PAIRS[stack[-1][0]] == c:
            opener, start = stack.pop()
            spans.append((start, i + 1, opener, bool(stack)))
    roles, next_top_role = [], None
    for start, end, opener, nested in sorted(spans, key=lambda t: -t[0]):      # 後ろから決める（(c) が次の引用の役割を見るため）
        if nested:
            roles.append((start, end, "other"))
            continue
        rest, before = body[end:].lstrip(" 　"), body[:start].rstrip(" 　")
        at_sentence_head = before == "" or before[-1] in "。！？!?」』"
        cue = MENTION_CUE.search(before) is not None
        if before == "" and not cue:      # 行頭から始まる引用は、前後の段落が「題名」「札」「便箋にはこうあった」と説明していないかを見る
            # 前の段落が「母が叫んだ。」のように発話を導いているなら、そこに出る「看板」などは背景で、次の行は台詞
            prev_is_speech_lead = re.search(r"(?:言[っいうわ]|叫[んび]|答え|呟[いく]|囁[いく]|訊[いくね]|尋ね|怒鳴[っり]|告げ|声がし|声をかけ|声を上げ)(?:た|だ|て|で|ている|でいる|ていた|でいた)?[。、]?$",
                                            prev_text.rstrip()) is not None
            cue = (carried_cue or (not prev_is_speech_lead and (MENTION_CUE.search(prev_text) is not None or DOCUMENT_CUE.search(prev_text) is not None))
                   or (rest == "" and MENTION_CUE.search(next_text[:80]) is not None))
        speech = False
        if cue:
            speech = False
        elif SPOKEN_AFTER.match(rest) and (at_sentence_head or SHORT_SUBJECT.match(before)):
            speech = True
        elif opener == "「" and at_sentence_head:
            if rest == "":
                speech = True
            elif rest.startswith("「"):
                speech = next_top_role == "speech"
        role = "speech" if speech else "other"
        roles.append((start, end, role))
        next_top_role = role
    return sorted(roles)


def role_at(roles, pos: int) -> str:
    """pos の表記違反をどう扱うか: "narration"（引用の外）/ "speech" / "other"。入れ子は内側を優先。"""
    inner = [r for r in roles if r[0] < pos < r[1]]
    if not inner:
        return "narration"
    return min(inner, key=lambda r: r[1] - r[0])[2]


DOCUMENT_CUE = re.compile(r"チャット|メール|メッセージ|手紙|便箋|葉書|はがき|封筒|遺書|遺言|証文|契約書|答案|原稿|台本|脚本|新聞|雑誌|広告|ポスター|チラシ|書き置き|置き手紙|レシート|領収|伝票|名刺|切符|貼り紙|張り紙|掲示|案内板|看板|"
                          r"献立|メニュー|品書|ノート|日記|手帳|帳面|詩集|一節|"
                          r"注意書き|通知|画面|歌|詩|句|記事|新聞|書類|メモ|伝言|こうあった|とあった|書かれていた|書いてあった|次のよう")


def indent_candidates(doc: Doc):
    """字下げの有無を問える段落。括弧始まり、英数字始まり（英文の手紙・番号）、
    文として終わっていない行（拝啓・敬具、「件名：帰宅時間」のような項目、看板の文面）は数えない。"""
    return [p for p in doc.paras if p["kind"] != "document" and not p["body"].startswith(NO_INDENT_OPENERS)
            and not re.match(r"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９]", p["body"])
            and re.search(r"[。！？!?…―」』）)]$", p["body"].rstrip())
            and not re.match(r"[^。、]{1,8}[：:]", p["body"])]


def document_lines(doc: Doc) -> set:
    """字下げされた原稿の中で、字下げの無い行が 2 行以上まとまっている所、または前の段落が
    「チャットを開いた」「貼り紙があった」のように文書を導入している所は、作中文書（チャット・手紙・貼り紙・献立・歌詞）とみなす。
    1 行だけぽつんと字下げが無いのは、下げ忘れ。"""
    cands = indent_candidates(doc)
    if len(cands) < 2 or not any(p["indented"] for p in cands):
        return set()      # 字下げなしで統一された原稿には、文書のまとまりを見分ける手がかりが無い
    lines, run, prev, last_line = set(), [], None, 0
    for p in doc.paras + [None]:
        explicit = p is not None and p["kind"] == "document"      # ＞ で明示された文書は推定の対象外。字下げの無い行のまとまりもそこで切る
        # 明示された文書（段落にならなかった ＞ だけの行を含む）をまたいだら、まとまりを切り、導入の段落も持ち越さない。
        # 「メールを開いた。」の効力は、その直後の明示された文面で使い切っている
        crossed = p is not None and (explicit or doc.document_between(last_line, p["line"]))
        if crossed:
            if run and ((len(run) >= 2 and run_has_intro) or run_cue):
                lines.update(q["line"] for q in run)
            run, prev = [], None
        if p is not None:
            last_line = p["line"]
        loose = p is not None and not explicit and p["lead"] != "　" and not p["body"].startswith(NO_INDENT_OPENERS)
        if loose:
            if not run:
                run_has_intro = prev is not None      # 文書は、それを導入する地の文の段落のあとに来る。原稿の冒頭から続く字下げの乱れは下げ忘れ
                run_cue = run_has_intro and DOCUMENT_CUE.search(prev["clean"]) is not None
            run.append(p)
        else:
            if run and ((len(run) >= 2 and run_has_intro) or run_cue):
                lines.update(q["line"] for q in run)
            run = []
        if p is not None and not loose and not explicit:
            prev = p
    return lines


# 字下げの無い原稿では、作中文書を体裁から見分けられない。直前の行が「メッセージが届いた。」のように
# 文書を導入して終わっているときだけ、続く数行を「作中文書かもしれない行」として案内する（判定は変えない）。
DOC_INTRO = re.compile(
    r"(?:チャット|メール|メッセージ|ＬＩＮＥ|LINE|ライン|ＤＭ|DM|ショートメール|手紙|便箋|葉書|はがき|書き置き|置き手紙|メモ|伝言|通知|貼り紙|張り紙|掲示|画面|文面|返信|返事)"
    r"(?:(?!だが|たが|けれど|けど|ので|のに|ながら|つつ|まま)[^。、！？!?「」『』]){0,14}?"
    r"(?:開いた|開く|開けた|届いた|届く|届いていた|来た|きた|来ていた|きていた|入った|入っていた|読んだ|読む|読み返した|読み上げた|見た|見る|"
    r"表示された|表示されていた|出ていた|あった|書かれていた|書いてあった|残っていた|残されていた|だった|である)[。：:]?$")
DOC_GUESS_MAX_LINES = 3


def guessed_document_blocks(doc: Doc) -> list:
    """字下げの無い原稿で、作中文書かもしれない行のまとまりを [[行番号, ...], ...] で返す。案内（N10）専用。

    導入の行の直後（空行は 1 つまで挟んでよい）から、物理的に隣り合う行を最大 DOC_GUESS_MAX_LINES 行。
    空行・見出し・場面転換・＞ の行・字下げされた行・「 で始まる行で止める。どこで地の文に戻るかは決められないので、
    これを根拠に FAIL を下げたり統計から外したりはしない。"""
    if any(p["indented"] for p in doc.paras):
        return []      # 字下げのある原稿は document_lines() が体裁で見分ける
    by_line = {p["line"]: p for p in doc.paras}
    blocks = []
    for p in doc.paras:
        if p["kind"] == "document" or not DOC_INTRO.search(p["clean"].rstrip()):
            continue
        ln = p["line"] + 1
        if ln not in by_line and ln <= len(doc.raw_lines) and not doc.raw_lines[ln - 1].strip() and ln not in doc.explicit_doc:
            ln += 1      # 導入の行と文面のあいだの空行 1 つ
        block = []
        while len(block) < DOC_GUESS_MAX_LINES and ln in by_line:
            q = by_line[ln]
            if q["kind"] == "document" or q["lead"] or q["body"].startswith(("「", "『")):
                break
            block.append(ln)
            ln += 1
        if block:
            blocks.append(block)
    return blocks


def check_markdown(doc: Doc, rep: Report):
    """N07。段落にならなかった行（記号だけの文面「**！**」、「* * *」）も見るので、段落でなく行を走査する。
    本文の字数が 0 の原稿（記号だけ）でも走らせる。"""
    n07 = []
    for ln, raw in enumerate(doc.raw_lines, 1):
        if raw.strip() and not (HEADING.match(raw) and ln not in doc.explicit_doc) and re.search(
                r"\*\*[^*\n]+\*\*|__[^_\n]+__|^\s*(?:[-*+]|\d+\.)\s+\S|^\s*>\s|`[^`\n]+`", raw):
            n07.append((ln, excerpt(raw)))
    if n07:
        rep.add("N07", "WARN", "Markdown 装飾", f"{len(n07)} 箇所。本文に太字・箇条書き・引用記号・コード記法がある", n07,
                "小説の本文では使わない。強調は傍点 《《語》》 か、語順で")


def check_notation(doc: Doc, rep: Report):
    cfg = rep.cfg
    n01, n02, n03, n04, n06 = [], [], [], [], []
    soft = []      # 台詞と分類できない引用の中や、作中文書らしい行の表記違反。題名や文字列そのものかもしれないので WARN
    doc_lines = document_lines(doc)
    prev_text, carried_cue = "", False
    for idx, p in enumerate(doc.paras):
        ln, s = p["line"], p["clean"]
        lead = len(s) - len(s.lstrip(" \t　"))
        next_text = doc.paras[idx + 1]["clean"] if idx + 1 < len(doc.paras) else ""
        roles = [(a + lead, b + lead, r) for a, b, r in quote_roles(s[lead:], prev_text, next_text, carried_cue)]
        # 「便箋には二行あった。」のあとに引用の行が続くとき、2 行目以降にも手掛かりを引き継ぐ
        whole_line_quote = len(roles) >= 1 and roles[0][0] == lead and roles[-1][1] == len(s.rstrip())
        carried_cue = whole_line_quote and all(r[2] == "other" for r in roles) and (
            carried_cue or MENTION_CUE.search(prev_text) is not None or DOCUMENT_CUE.search(prev_text) is not None)
        prev_text = s
        in_document = ln in doc_lines or p["kind"] == "document"

        def in_label(pos):
            head = s[:pos]
            return any(head.count(o) > head.count(c) for o, c in (("【", "】"), ("〈", "〉"), ("［", "］")))

        def put(bucket, m, width_before=8, width_after=4):
            ex = excerpt(s[max(0, m.start() - width_before):m.end() + width_after])
            (soft if in_document or in_label(m.start()) or role_at(roles, m.start()) == "other" else bucket).append((ln, ex))

        for m in re.finditer(r"…+", s):
            if len(m.group(0)) % 2:
                put(n01, m)
        for m in re.finditer(r"・{3,}|\.{3,}|。{2,}|、{2,}", s):
            put(n01, m)
        for m in re.finditer(r"[―—─]+", s):
            if len(m.group(0)) % 2:
                a, b = s[m.start() - 1:m.start()], s[m.end():m.end() + 1]
                if len(m.group(0)) == 1 and re.match(r"[一-龥々ァ-ヶ]", a) and re.match(r"[一-龥々ァ-ヶ]", b):
                    soft.append((ln, "区間の表記なら可: " + excerpt(s[max(0, m.start() - 6):m.end() + 6])))
                else:
                    put(n02, m)
        for m in re.finditer(r"(?<![ァ-ヶーぁ-ん])ー{2,}|(?<![\-\w])--+(?![\-\w>])", s):
            put(n02, m)
        for m in re.finditer(r"[！？⁉‼](?![！？⁉‼!?」』）)】〉》　…―—]|$)", s):
            put(n03, m)
        for m in re.finditer(r"。[」』]", s):
            # 句点の直後の閉じ括弧が、台詞の終わりなら N04。書名『おやすみ。』や固有名は WARN
            role = next((r[2] for r in roles if r[1] == m.end()), "other")
            (n04 if role == "speech" and not in_document else soft).append((ln, excerpt(s[max(0, m.start() - 10):m.end()])))
        for m in re.finditer(r"(?<=[ぁ-んァ-ヶ一-龥])[!?,](?![\w/])|(?<=[ぁ-んァ-ヶ一-龥])\((?=[ぁ-んァ-ヶ一-龥])|[｡､｢｣]", s):
            n06.append((ln, excerpt(s[max(0, m.start() - 8):m.end() + 4])))
    if n01:
        rep.add("N01", "FAIL", "三点リーダーの形", f"{len(n01)} 箇所。「…」は 2 個 1 組（……）。「・・・」「...」は使わない", n01,
                "…… に直す。頻度が気になるなら K02 を見る")
    if n02:
        rep.add("N02", "FAIL", "ダッシュの形", f"{len(n02)} 箇所。「―」は 2 個 1 組（――）。長音符「ー」やハイフンで代用しない", n02,
                "―― に直す")
    if n03:
        rep.add("N03", "FAIL", "感嘆符・疑問符の後の空白", f"{len(n03)} 箇所。文が続くなら全角空白を 1 つ入れる（閉じ括弧の前と行末は不要）", n03,
                "「！」「？」の直後に全角空白")
    if n04:
        rep.add("N04", "FAIL", "閉じ括弧の直前の句点", f"{len(n04)} 箇所。台詞の終わりの「。」は書かない", n04, "「。」を削る")
    if n06:
        rep.add("N06", "WARN", "半角の記号", f"{len(n06)} 箇所。和文の中の ! ? , ( や半角カナ約物", n06, "全角に直す")
    if soft:
        rep.add("N09", "WARN", "引用の中の表記（要確認）", f"{len(soft)} 箇所。台詞と分類できない引用・台詞の中の引用にある表記",
                soft, "題名・固有名・入力文字列そのものなら、そのまま残す。台詞なら N01〜N04 の規則で直す")
    if doc.unbalanced:
        rep.add("N08", "WARN", "括弧の対応", f"{len(doc.unbalanced)} 行。「」『』が行の中で閉じていない（1 段落 1 行が前提。台詞の統計は不確か）",
                doc.unbalanced, "台詞の途中で改行していないか、閉じ忘れが無いか")
    # N10 字下げの無い原稿で、表記の FAIL が「文書を導入する行」の直後にあるとき、範囲の明示を案内する。FAIL はそのまま残す
    flagged = {loc["line"] for h in rep.hits if h["rule"] in ("N01", "N02", "N03", "N04") and h["severity"] == "FAIL"
               for loc in h["locations"]}      # 実際に出た FAIL だけ（overrides や --skip で止めたルールは根拠にしない）
    guessed = [b for b in guessed_document_blocks(doc) if flagged & set(b)]
    if guessed:
        by_line = {p["line"]: p for p in doc.paras}
        rep.add("N10", "INFO", "作中文書かもしれない行", f"{len(guessed)} 箇所。直前の行が文書（チャット・メール・手紙など）を導入していて、続く行に N01〜N04 がある",
                [(b[0], f"{excerpt(by_line[b[0]]['clean'], 16)}（ここから最大 {len(b)} 行。どこまでが文面かは推定していない）") for b in guessed],
                "文面なら、その行の頭に全角の ＞ を付けて範囲を明示する（以後その行の表記は N09 の確認扱いになり、統計からも外れる）。地の文なら N01〜N04 のとおり直す")
    odd_all = [p for p in doc.paras if p["lead"] not in ("", "　")]
    odd_lead = [(p["line"], excerpt(p["raw"])) for p in odd_all if p["line"] not in doc_lines]
    odd_doc = [(p["line"], excerpt(p["raw"])) for p in odd_all if p["line"] in doc_lines]
    if odd_doc:
        rep.add("N05", "WARN", "作中文書の中の字下げ", f"{len(odd_doc)} 行。手紙・メモ・詩の体裁として意図したものなら、このままでよい", odd_doc, "")
    if odd_lead:
        rep.add("N05", "FAIL", "字下げの形", f"{len(odd_lead)} 行。字下げは全角空白 1 個（半角空白・タブ・2 個以上は使わない）", odd_lead,
                "行頭の空白を全角 1 個に直す")
    # N05 字下げの混在。会話始まりの行は下げないのが標準なので、地の文始まりの段落だけを見る
    # 字下げの混在を見るのは、地の文の段落だけ。作中文書とみなした行のまとまりは、混在の根拠にしない
    narr_starts = [p for p in indent_candidates(doc) if p["line"] not in doc_lines]
    doc_block = [p for p in indent_candidates(doc) if p["line"] in doc_lines]
    if doc_block:
        rep.add("N05", "WARN", "字下げの無い行のまとまり", f"{len(doc_block)} 行。作中の手紙・チャット・貼り紙なら、このままでよい。地の文なら下げ忘れ",
                [(p["line"], excerpt(p["raw"])) for p in doc_block], "地の文なら行頭に全角空白を入れる")
    if len(narr_starts) >= 2 or (narr_starts and cfg.indent_policy):
        ind = [p for p in narr_starts if p["indented"]]
        rate = len(ind) / len(narr_starts)
        policy = cfg.indent_policy
        if 0 < rate < 1:
            minority = [p for p in narr_starts if p["indented"] != (rate >= 0.5)]
            rep.add("N05", "FAIL", "字下げの混在",
                    f"地の文段落の {rate:.0%} だけが全角 1 字下げ。どちらかに統一する",
                    [(p["line"], excerpt(p["raw"])) for p in minority], "原稿全体で統一（投稿時に付けるなら export.py）")
        elif rate == 0 and policy == "fullwidth":
            rep.add("N05", "FAIL", "字下げなし", "novel.toml は indent=fullwidth だが、字下げされた段落が無い", [],
                    "段落頭に全角空白を入れるか、[format] indent を export / none にする")
        elif rate == 1 and policy in ("none", "export"):
            rep.add("N05", "FAIL", "字下げあり", f"novel.toml は indent={policy} だが、原稿が字下げされている", [],
                    "原稿の字下げを外す（export.py が付ける）か、indent を fullwidth にする")
        elif rate == 0 and policy is None and cfg.profile != "web":
            rep.add("N05", "INFO", "字下げなしで統一", "段落頭の字下げが無い。投稿・応募時に export.py で付けるなら問題ない", [], "")
        quote_ind = [p for p in doc.paras if p["starts_quote"] and p["indented"]]
        if quote_ind and rate > 0:
            rep.add("N05", "WARN", "会話行の字下げ", f"{len(quote_ind)} 行。「 で始まる行は下げないのが標準",
                    [(p["line"], excerpt(p["raw"])) for p in quote_ind], "行頭の全角空白を外す")


# ---------------------------------------------------------------------------
# R / L / P / T / D 統計
# ---------------------------------------------------------------------------
def collect_stats(doc: Doc, cfg: Config) -> dict:
    sents = []   # (line, text, cls, is_tag)
    tokens = []  # 文末クラス列。純台詞段落は "D" で run を切る
    last_line = 0
    for p in doc.paras:
        if doc.document_between(last_line, p["line"]):      # ＞ だけの行、記号だけの文面（＞＊）も境界
            tokens.append(("D", p["line"], ""))
        last_line = p["line"]
        if p["kind"] in ("dialogue", "document"):      # 作中文書でも同一文末の連続を切る
            tokens.append(("D", p["line"], ""))
            continue
        for text, cls, is_tag in p["sents"]:
            sents.append((p["line"], text, cls, is_tag))
            if not is_tag:
                tokens.append((cls, p["line"], text))
    body_sents = [s for s in sents if not s[3]]
    lens = [nws(s[1]) for s in body_sents]
    classes = [s[2] for s in body_sents]
    n = len(body_sents)
    narr_paras = [p for p in doc.paras if p["kind"] == "narration"]
    para_lens = [nws(p["clean"]) for p in narr_paras]
    one_sent = [p for p in narr_paras if len(p["sents"]) <= 1]
    # 同一文末の run
    runs, cur, start, count = [], None, None, 0
    for cls, line, text in tokens + [("D", 0, "")]:
        if cls == cur and cls != "D":
            count += 1
            continue
        if cur not in (None, "D") and count:
            runs.append((cur, count, start[0], start[1]))
        cur, start, count = cls, (line, text), 1
    in_long = sum(c for cls, c, _, _ in runs if c >= 4 and cls != "other")
    class_share = {c: round(classes.count(c) / n, 3) for c in sorted(set(classes))} if n else {}
    stats = {
        "chars": doc.total_chars,
        "narration_chars": doc.narr_chars,
        "dialogue_ratio": round(doc.dlg_chars / doc.total_chars, 3) if doc.total_chars else None,
        "sentences": n,
        "sentence_len_mean": round(statistics.mean(lens), 1) if lens else None,
        "sentence_len_median": statistics.median(lens) if lens else None,
        "sentence_len_cv": round(cv(lens), 3) if cv(lens) is not None else None,
        "sentence_len_max": max(lens) if lens else None,
        "sentences_le10": sum(1 for x in lens if x <= 10),
        "sentences_ge80": sum(1 for x in lens if x >= 80),
        "commas_per_sentence": round(statistics.mean([s[1].count("、") for s in body_sents]), 2) if n else None,
        "ending_share": class_share,
        "ta_rate": class_share.get("ta"),
        "keitai": (class_share.get("masu", 0) >= 0.5),
        "same_ending_max_run": max([c for cls, c, _, _ in runs if cls != "other"], default=0),
        "same_ending_run_share": round(in_long / n, 3) if n else None,
        "narration_paragraphs": len(narr_paras),
        "paragraph_len_mean": round(statistics.mean(para_lens), 1) if para_lens else None,
        "paragraph_len_cv": round(cv(para_lens), 3) if cv(para_lens) is not None else None,
        "one_sentence_paragraph_rate": round(len(one_sent) / len(narr_paras), 3) if narr_paras else None,
        "utterances": len(doc.dialogues),
        "kanji_ratio": round(len(KANJI.findall("".join(p["clean"] for p in doc.paras))) / doc.total_chars, 3) if doc.total_chars else None,
    }
    stats["_runs"] = runs
    stats["_body_sents"] = body_sents
    stats["_narr_paras"] = narr_paras
    return stats


def check_rhythm(doc: Doc, rep: Report, st: dict):
    cfg, g = rep.cfg, rep.cfg.guards
    n = st["sentences"]
    runs, body_sents, narr_paras = st["_runs"], st["_body_sents"], st["_narr_paras"]
    # R01 同一文末の連続（敬体の語りは線を緩める）
    variant = "keitai" if st["keitai"] else None
    lv = cfg.levels("R01", variant)
    if not lv.get("off"):
        worst = {}
        for cls, c, line, text in runs:
            if cls in ("other", "taigen"):
                continue
            for key, sev in (("strong", "STRONG"), ("warn", "WARN"), ("info", "INFO")):
                if lv.get(key) is not None and c >= lv[key]:
                    worst.setdefault(sev, []).append((line, f"{excerpt(text, 18)}（{cls} が {c} 連続）"))
                    break
        for sev in ("STRONG", "WARN", "INFO"):
            if sev in worst:
                rule = cfg.rule("R01")
                rep.add("R01", sev, rule["name"], f"{len(worst[sev])} 箇所" + ("（敬体の線で判定）" if variant else ""),
                        worst[sev], rule["hint"])
    if n >= g["run_share_min_sentences"]:
        rep.add_level("R02", st["same_ending_run_share"], cfg.levels("R02"), "{v:.0%}（警報線 {th:.0%}）")
        if not st["keitai"]:
            rep.add_level("R03", st["ta_rate"], cfg.levels("R03"), "{v:.0%}（警報線 {th:.0%}）")
    # L 文長
    if n >= g["cv_min_sentences"] and st["sentence_len_cv"] is not None:
        lv = dict(cfg.levels("L01"))
        if n < g["cv_full_sentences"] and not lv.get("off"):
            lv = {"info": lv.get("warn")}   # 標本が少ないうちは弱い線だけ
        rep.add_level("L01", st["sentence_len_cv"], lv, "CV {v:.2f}（警報線 {th:.2f} 未満）。平均 " + f"{st['sentence_len_mean']} 字、80 字以上 {st['sentences_ge80']} 文、10 字以下 {st['sentences_le10']} 文", lower=True)
        rep.add_level("L02", st["sentence_len_mean"], cfg.levels("L02"), "平均 {v} 字（警報線 {th} 字未満）", lower=True)
    lv = cfg.levels("L03")
    if not lv.get("off") and lv.get("info"):
        longs = [(ln, f"{excerpt(t, 18)}（{nws(t)} 字）") for ln, t, _, _ in body_sents if nws(t) > lv["info"]]
        if longs:
            rep.add("L03", "INFO", cfg.rule("L03")["name"], f"{len(longs)} 文が {lv['info']} 字超", longs, cfg.rule("L03")["hint"])
    lv = cfg.levels("L04")
    if not lv.get("off") and n >= 8:
        tol, locs, i = lv.get("info", 0.25), [], 0
        lens = [nws(s[1]) for s in body_sents]
        while i + 5 <= len(lens):
            w = lens[i:i + 5]
            m = statistics.mean(w)
            if m and all(abs(x - m) <= m * tol for x in w):
                locs.append((body_sents[i][0], f"{excerpt(body_sents[i][1], 16)}（{'/'.join(map(str, w))} 字）"))
                i += 5
            else:
                i += 1
        if locs:
            rep.add("L04", "INFO", cfg.rule("L04")["name"], f"{len(locs)} 箇所", locs, cfg.rule("L04")["hint"])
    # P 段落
    lv = cfg.levels("P01")
    if not lv.get("off"):
        for key, sev in (("warn", "WARN"), ("info", "INFO")):
            th = lv.get(key)
            if th:
                upper = lv.get("warn") if key == "info" and lv.get("warn") else None
                locs = [(p["line"], f"{excerpt(p['clean'], 16)}（{nws(p['clean'])} 字）") for p in narr_paras
                        if nws(p["clean"]) > th and (upper is None or nws(p["clean"]) <= upper)]
                if locs:
                    rep.add("P01", sev, cfg.rule("P01")["name"], f"{len(locs)} 段落が {th} 字超", locs, cfg.rule("P01")["hint"])
    if len(narr_paras) >= g["para_cv_min_paras"]:
        rep.add_level("P02", st["paragraph_len_cv"], cfg.levels("P02"), "CV {v:.2f}（警報線 {th:.2f} 未満）", lower=True)
        lv = cfg.levels("P03")
        rate = st["one_sentence_paragraph_rate"]
        if not lv.get("off") and rate is not None:
            if lv.get("info_over") is not None and rate > lv["info_over"]:
                rep.add("P03", "INFO", cfg.rule("P03")["name"], f"{rate:.0%}（目安の上側 {lv['info_over']:.0%} 超）", [], cfg.rule("P03")["hint"])
            elif lv.get("info_under") is not None and rate < lv["info_under"]:
                rep.add("P03", "INFO", cfg.rule("P03")["name"], f"{rate:.0%}（目安の下側 {lv['info_under']:.0%} 未満）", [], "Web 連載としては段落が長め。意図どおりなら keep")
    lv = cfg.levels("P04")
    if not lv.get("off") and lv.get("info"):
        short = cfg.rule("P04").get("short_chars", 25)
        locs, run, start = [], 0, None
        for p in doc.paras + [None]:
            ok = p is not None and p["kind"] == "narration" and len(p["sents"]) <= 1 and nws(p["clean"]) <= short
            if ok:
                run += 1
                start = start or p
            else:
                if run >= lv["info"]:
                    locs.append((start["line"], f"{excerpt(start['clean'], 16)}（{run} 連続）"))
                run, start = 0, None
        if locs:
            rep.add("P04", "INFO", cfg.rule("P04")["name"], f"{len(locs)} 箇所", locs, cfg.rule("P04")["hint"])
    lv = cfg.levels("P05")
    if not lv.get("off"):
        th = lv.get("warn") or lv.get("info")
        sev = "WARN" if lv.get("warn") else "INFO"
        if th and doc.dialogues:
            locs, acc, start = [], 0, None
            for p in doc.paras + [None]:
                if p is not None and p["kind"] == "narration":
                    acc += nws(p["clean"])
                    start = start or p
                else:
                    if acc > th:
                        locs.append((start["line"], f"{excerpt(start['clean'], 16)}（{acc} 字）"))
                    acc, start = 0, None
            if locs:
                rep.add("P05", sev, cfg.rule("P05")["name"], f"{len(locs)} 箇所が {th} 字超", locs, cfg.rule("P05")["hint"])
    # T 体言止め
    lv = cfg.levels("T01")
    if not lv.get("off"):
        by_sev = {}
        for cls, c, line, text in runs:
            if cls != "taigen":
                continue
            for key, sev in (("warn", "WARN"), ("info", "INFO")):
                if lv.get(key) is not None and c >= lv[key]:
                    by_sev.setdefault(sev, []).append((line, f"{excerpt(text, 18)}（{c} 連続）"))
                    break
        for sev, locs in by_sev.items():
            rep.add("T01", sev, cfg.rule("T01")["name"], f"{len(locs)} 箇所", locs, cfg.rule("T01")["hint"])
    if doc.narr_chars >= g["density_min_chars"]:
        taigen = [(ln, excerpt(t, 18)) for ln, t, cls, _ in body_sents if cls == "taigen"]
        if len(taigen) >= g["density_min_count"]:
            dens = len(taigen) * 1000 / doc.narr_chars
            rep.add_level("T02", round(dens, 1), cfg.levels("T02"), "{v}/地の文1000字（警報線 {th}）", taigen)


def check_dialogue(doc: Doc, rep: Report, st: dict):
    cfg, g = rep.cfg, rep.cfg.guards
    if doc.total_chars >= g["density_min_chars"] and st["dialogue_ratio"] is not None:
        lv = cfg.levels("D01")
        if not lv.get("off"):
            tconf = cfg.thresholds["dialogue_ratio_target"]
            target = lv.get("target", tconf.get(cfg.profile, 0.45))
            tol = lv.get("info", tconf["tolerance"])
            if abs(st["dialogue_ratio"] - target) > tol:
                rep.add("D01", "INFO", cfg.rule("D01")["name"],
                        f"会話比率 {st['dialogue_ratio']:.0%}（目標 {target:.0%} ±{tol:.0%}）", [], cfg.rule("D01")["hint"])
    lv = cfg.levels("D02")
    if not lv.get("off"):
        by_sev, run, start = {}, 0, None
        for p in doc.paras + [None]:
            if p is not None and p["kind"] == "dialogue":
                run += p["n_utt"]
                start = start or p
                continue
            for key, sev in (("strong", "STRONG"), ("warn", "WARN"), ("info", "INFO")):
                if lv.get(key) is not None and run >= lv[key]:
                    by_sev.setdefault(sev, []).append((start["line"], f"{excerpt(start['clean'], 16)}（{run} 発話）"))
                    break
            run, start = 0, None
        for sev, locs in by_sev.items():
            rep.add("D02", sev, cfg.rule("D02")["name"], f"{len(locs)} 箇所", locs, cfg.rule("D02")["hint"])
    lv = cfg.levels("D03")
    if not lv.get("off") and lv.get("info"):
        win = cfg.rule("D03").get("window", 300)
        pos, marks = 0, []
        for p in doc.paras:
            for _ in range(p["n_utt"] if p["kind"] in ("dialogue", "document") else len(QUOTE.findall(p["body"]))):
                marks.append((pos, p["line"]))
            pos += nws(p["clean"])
        locs, i, last_end = [], 0, -1
        for j in range(len(marks)):
            while marks[j][0] - marks[i][0] > win:
                i += 1
            if j - i + 1 >= lv["info"] and marks[i][0] > last_end:
                locs.append((marks[i][1], f"{win} 字の窓に {j - i + 1} 発話"))
                last_end = marks[j][0]
        if locs:
            rep.add("D03", "INFO", cfg.rule("D03")["name"], f"{len(locs)} 箇所", locs, cfg.rule("D03")["hint"])
    if len(doc.dialogues) >= g["dialogue_rate_min_utterances"]:
        lead = [(ln, excerpt("「" + d, 16)) for ln, d in doc.dialogues if re.match(r"[\s　]*(…|・・|\.\.)", d)]
        rep.add_level("K03", round(len(lead) / len(doc.dialogues), 3), cfg.levels("K03"), "{v:.0%}（警報線 {th:.0%}）", lead)


# ---------------------------------------------------------------------------
# S / K 密度
# ---------------------------------------------------------------------------
def find_all(lines, rx):
    out = []
    for ln, s in lines:
        for m in rx.finditer(s):
            out.append((ln, m.group(0), excerpt(s[max(0, m.start() - 8):m.end() + 8], 28)))
    return out


def repeated_in_window(lines, rx, limit: int, window):
    """同じ表現が window 字の範囲に limit 回以上あれば知らせる。window が無ければ全文で limit 回を超えたとき。
    原稿の後ろに文章を足しても、既にある集中が消えないように、位置で見る。"""
    pos, offset = {}, 0
    for ln, s in lines:
        for m in rx.finditer(s):
            pos.setdefault(m.group(0), []).append((offset + m.start(), ln))
        offset += len(s)
    out = []
    for word, hits in pos.items():
        if window:
            for i in range(len(hits) - limit + 1):
                if hits[i + limit - 1][0] - hits[i][0] <= window:
                    out.append((hits[i][1], f"「{word}」が {window} 字の中に {limit} 回以上（全体で {len(hits)} 回）"))
                    break
        elif len(hits) > limit:
            out.append((hits[0][1], f"「{word}」×{len(hits)}"))
    return out


def check_density(doc: Doc, rep: Report):
    cfg, g = rep.cfg, rep.cfg.guards
    narr, everything = doc.narr_lines(), doc.all_lines()
    # 短い断片と掌編では密度が暴れるので警報にしない。どの群の表現があるかだけ知らせる（K00）。
    # 全文が長くても、そのルールの分母（地の文）が短ければ同じ扱いにする。分母の違う字数で切り替えると、診断が黙って消える
    short_all = doc.total_chars < g["density_min_chars"] or cfg.length == "flash"
    found_any = []
    for rid, rule in cfg.thresholds["rules"].items():
        if rule.get("kind") == "density" and "lexicon" in rule and not cfg.levels(rid).get("off"):
            unit = rule.get("unit", "narr")
            if short_all or (doc.narr_chars if unit == "narr" else doc.total_chars) < g["density_min_chars"]:
                hits = find_all(narr if unit == "narr" else everything, cfg.rx(rule["lexicon"]))
                if hits:
                    found_any.append((hits[0][0], f"{rid} {rule['name']} ×{len(hits)}: " + "、".join(sorted({w for _, w, _ in hits})[:5])))
    if found_any:
        rep.add("K00", "INFO", "語彙の癖（掌編・短い断片では密度を警報にしない）",
                f"全文 {doc.total_chars} 字・地の文 {doc.narr_chars} 字。該当した群と回数だけ示す", found_any,
                "一つずつ fix / keep を選ぶ。同義語に置き換えず、文の機能を果たし直すか削る")
    if short_all:
        return
    summary = {}
    for rid, rule in cfg.thresholds["rules"].items():
        if rule.get("kind") != "density" or "lexicon" not in rule:
            continue
        lv = cfg.levels(rid)
        if lv.get("off"):
            continue
        unit = rule.get("unit", "narr")
        lines, base = (narr, doc.narr_chars) if unit == "narr" else (everything, doc.total_chars)
        if base < g["density_min_chars"]:
            continue      # 全文が長くても、このルールの分母（地の文）が短ければ密度にしない（上の K00 で回数だけ知らせてある）
        found = find_all(lines, cfg.rx(rule["lexicon"]))
        dens = round(len(found) * 1000 / base, 2)
        summary[rid] = (len(found), dens)
        if len(found) >= rule.get("min_count", g["density_min_count"]):
            label = "地の文" if unit == "narr" else "全文"
            rep.add_level(rid, dens, lv, "{v}/" + label + f"1000字（警報線 {{th}}）、{len(found)} 回",
                          [(ln, ex) for ln, _, ex in found])
        # 同じ表現の使い回し（K09: 同一表現、K10: 同一語が近い範囲に固まる）
        if rule.get("repeat_warn") and found:
            rep_locs = repeated_in_window(lines, cfg.rx(rule["lexicon"]), rule["repeat_warn"], rule.get("repeat_window"))
            if rep_locs:
                rep.add(rid, "WARN", rule["name"] + "（同じ表現の再出）", f"{len(rep_locs)} 種", rep_locs, rule.get("hint", ""))
    # K01/K02 約物の頻度（形の誤りは N01/N02 が見る）
    dash = find_all(everything, re.compile(r"[―—─]{2,}"))
    ell = find_all(everything, re.compile(r"…{2,}|・{3,}"))
    for rid, found in (("K01", dash), ("K02", ell)):
        if len(found) >= g["density_min_count"]:
            rep.add_level(rid, round(len(found) * 1000 / doc.total_chars, 2), cfg.levels(rid),
                          "{v}/全文1000字（警報線 {th}）、" + f"{len(found)} 回", [(ln, ex) for ln, _, ex in found])
    # K08 + K09 は一つの予算。片方だけ下げると、もう片方へ滑る
    if "K08" in summary or "K09" in summary:
        a, b = summary.get("K08", (0, 0.0)), summary.get("K09", (0, 0.0))
        if a[0] + b[0] >= g["density_min_count"]:
            rep.add("K08", "INFO", "感情の名指し＋身体反応の合算",
                    f"名指し {a[1]}/1000字 ＋ 身体反応 {b[1]}/1000字 ＝ {round(a[1] + b[1], 2)}", [],
                    "二つは同じ予算。名指しを身体反応に置き換えても直ったことにならない")
    # S03 同じ喩えの再使用
    lv = cfg.levels("S03")
    if not lv.get("off"):
        vehicles = {}
        for ln, s in everything:
            for m in cfg.rx("simile_vehicle").finditer(s):
                vehicles.setdefault(m.group(1), []).append(ln)
        locs = [(lns[0], f"「{v}のよう」×{len(lns)}") for v, lns in vehicles.items() if len(lns) >= lv.get("warn", 2)]
        if locs:
            rep.add("S03", "WARN", cfg.rule("S03")["name"], f"{len(locs)} 種", locs, cfg.rule("S03")["hint"])
    # K05 / K06 / K17
    sents = [(p["line"], t) for p in doc.paras for t, _, tag in p["sents"] if not tag]
    if len(sents) >= g["run_share_min_sentences"]:
        conj = re.compile(cfg.lexicon["para_conj"])
        heads = [(ln, excerpt(t, 16)) for ln, t in sents if conj.match(t)]
        rep.add_level("K05", round(len(heads) / len(sents), 3), cfg.levels("K05"), "{v:.0%}（警報線 {th:.0%}）", heads)
        nparas = [p for p in doc.paras if p["kind"] == "narration"]
        if len(nparas) >= g["para_cv_min_paras"]:
            ph = [(p["line"], excerpt(p["clean"], 16)) for p in nparas if conj.match(p["body"])]
            rep.add_level("K06", round(len(ph) / len(nparas), 3), cfg.levels("K06"), "{v:.0%}（警報線 {th:.0%}）", ph)
    lv = cfg.levels("K17")
    if not lv.get("off") and lv.get("info"):
        many = [(ln, f"{excerpt(t, 16)}（読点 {t.count('、')}）") for ln, t in sents if t.count("、") >= lv["info"]]
        if many:
            rep.add("K17", "INFO", cfg.rule("K17")["name"], f"{len(many)} 文", many, cfg.rule("K17")["hint"])


# ---------------------------------------------------------------------------
# C 数え上げの検算
# ---------------------------------------------------------------------------
OPEN_Q, CLOSE_Q = "「『“\"", "」』”\""
CONNECT = (r"(?:[\s　]|[、。，．]|――|……|の|という|っていう|って|なんていう|とかいう|たった|わずか|ほんの|その|この|あの|そんな|こんな|"
           r"たかが|ただの?|だけの?|は|も|が|で|、)")
UNIT_CHAR = r"[ 　]*(?:文字|もじ|字)(?!目|だけ|ずつ|まで|ぶん|以上|以内|以下|ほど|程度|くらい|ぐらい|前後|熟語|詰|分|数|面|幕|体|形|引|画|句|義|通|余)"
UNIT_MORA = r"[ 　]*(?:音|拍|はく|ぱく|モーラ)(?!だけ|ずつ|まで|ぶん|以上|以内|ほど|くらい|ぐらい|楽|色|程|階|符|声|量|質|響|速|読|節|感|目|子)"
RX_FWD_CHAR = re.compile(rf"[{OPEN_Q}]([^{OPEN_Q}{CLOSE_Q}\n]{{1,40}})[{CLOSE_Q}]({CONNECT}{{0,5}})({NUM}){UNIT_CHAR}")
RX_REV_CHAR = re.compile(rf"({NUM}){UNIT_CHAR}(?:の|だけの|きりの|ぽっちの)?(?:言葉|単語|名前|返事|答え|返信|返答|メッセージ|挨拶|台詞|セリフ|一言|ひとこと|メール|呟き|つぶやき)?"
                         rf"(?:は|が|を|で|だ|だった|。|、|――|……|[\s　])*[\n\s　]*[{OPEN_Q}]([^{OPEN_Q}{CLOSE_Q}\n]{{1,40}})[{CLOSE_Q}]")
RX_REV_MORA = re.compile(rf"({NUM}){UNIT_MORA}(?:の|だけの)?(?:言葉|単語|名前|返事|答え|一言|ひとこと)?"
                         rf"(?:は|が|を|で|だ|だった|。|、|――|……|[\s　])*[\n\s　]*[{OPEN_Q}]([^{OPEN_Q}{CLOSE_Q}\n]{{1,40}})[{CLOSE_Q}]")
RX_DATE = re.compile(rf"(?:({NUM})年)?({NUM})月({NUM})日(?:[、,（(\s　]*([月火水木金土日])曜)?")
RX_FWD_MORA = re.compile(rf"[{OPEN_Q}]([^{OPEN_Q}{CLOSE_Q}\n]{{1,40}})[{CLOSE_Q}]({CONNECT}{{0,5}})({NUM}){UNIT_MORA}")
RX_BARE = re.compile(rf"([一-龥々]{{1,8}}|[ァ-ヴー]{{2,12}})(?:の|という)({NUM}){UNIT_CHAR}")
RX_ANY_COUNT = re.compile(rf"({NUM}){UNIT_CHAR}")


NEGATION = re.compile(r"(?:では|じゃ|でも|で)(?:は|も)?(?:な[いくか]|あり?ません|ございません|あるまい)|と(?:ちゃう|違う)|とは(?:言|申|限ら|思)|ちゃう")
# 数そのものが伝聞・思い込み・仮定・疑問・虚偽の中にある標識。これがあれば断定しない（WARN に回す）
HEDGE = re.compile(r"と(?:聞|思|信じ|考え|言[いっわ]|申|噂|勘違|思い込|教わ|習|書いてあ)|という|との|って|ですって|だって|嘘|間違|誤|勘違|つもり|はず|"
                   r"なら|ならば|れば|たら|としても|だろう|でしょう|らしい|そうだ|かもしれ|かどうか|か(?:と|を|は)|[？?]|か[。\s　]*$|かな|かしら|っけ")
INTENSIFIER = re.compile(r"(?:たった|わずか|ほんの|その|この|あの|たかが|ただの)[\s　]*$")
NOUN_AFTER = r"(?:の|だけの|きりの|ぽっちの)?(?:言葉|単語|名前|挨拶|台詞|セリフ|一言|ひとこと|響き|メッセージ|呟き|つぶやき)"
# 断定と認める形は二つだけ。ほかはすべて要確認（WARN）に回す。
#   同格形: 「X」の／という N 文字 …… 数は X の呼び名。あとは文末・「〜の言葉」・格助詞で続いてよい（訂正や数え直しの語が無いこと）
#   主題形: 「X」は／が／も N 文字 …… コピュラで文が終わるときだけ。「四文字を超える」「三拍に満たない」「四文字にあらず」のように
#           助詞で述語が続く形は、等値の主張とは限らない（比較・否定・訂正は無数にある）ので断定にしない
# 長い形を先に置く（「だ」が先だと「だった」の「った」が残り、続きの述語と誤認する）
COPULA = r"(?:なのだった|でございます|であった|でござる|だった|でした|である|なのだ|です|どす|じゃ|だ|や)"
ASSERTIVE_TAIL = re.compile(r"^(?:" + NOUN_AFTER + r")?(?:|" + COPULA + r"|(?:が|を|に|は|も|で(?!は|も))[^。\n]*)$")
TOPIC_TAIL = re.compile(r"^" + COPULA + r"?$")
# 同格形でも、数を直している・数え直している・別の数を並べている文は、数え間違いの主張ではない
RECOUNT = re.compile(rf"訂正|直[しすさ]|改め|数え|否定|撤回|疑|主張|存在|信じ|違|誤|嘘|却下|認めな|先頭|末尾|最初|最後|一部|半分|途中|残[しりっ]|ところで|時点で|わけ(?:では|じゃ)な|{NUM}[ 　]*(?:文字|もじ|字|音|拍|はく|モーラ)")
# 同格形のあとに続いてよい述語。語を口にする・書く・消す・聞く・受け取る、といった「その語を扱う」動作に限る。
# 「四文字を否定した」「四文字の言葉は存在しない」のように、数の主張そのものを扱う述語は無数にあるので、ここに無い述語は断定にしない
WORD_HANDLING = (r"(?:言|い[えわっ]|口に|口を|呟|つぶや|囁|ささや|叫|告げ|伝え|返[しすっ]|繰り返|発音|発し|唱え|歌|詠|書|記|綴|打[ちってた]|入力|刻|彫|消|読|眺|見つめ|見た|見て|聞[いこか]|"
                 r"飲み込|呑み込|のみこ|噛みしめ|かみしめ|喉|胸|口の中|出てこな|出なか|出な[いく]|浮か|並[びんべ]|響|届|送|残[しすっ]|込め|重[いかく]|軽[いかく]|"
                 r"温か|あたたか|短[いかく]|長[いかく]|だけ|しか|すら|さえ|で(?:十分|足り|済)|に(?:すぎ|過ぎ)|が(?:すべて|全て))")
APPOS_TAIL = re.compile(r"^(?:" + NOUN_AFTER + r")?(?:|" + COPULA + r"|(?:が|を|に|は|も|で(?!は|も)|、)[^。\n]{0,30})$")
# 直後の文が訂正・数え直しなら、前の数は書き手が意図して置いた誤り。
# 別の語の話（次の文が新しい引用で始まる「「がっこう」は四音。」）は訂正ではないので、見るのは次の引用が始まる手前まで
CORRECTION_AHEAD = re.compile(rf"^[^「『]*?(?:^(?:いや|いいや|いいえ|違う|ちがう|否|正しくは|正確には|本当は|実際は|実は|もちろん|という|との|って|そう)|数え|間違|誤り|正しくな|本当は|勘違|思い込|演じ|ふりを|知らない|{NUM}[ 　]*(?:文字|もじ|字|音|拍|はく|モーラ))")

LINK_WORDS = re.compile(r"たった|わずか|ほんの|その|この|あの|たかが|ただの?|だけの?|という|っていう|って|なんていう|とかいう|――|……|[\s　、。，．]")
# 引用より前に置いてよいもの: 無し、強調語、短い主語（「美咲は」「翔太が」）。「弟によれば、」「弟の説では、」は断定にしない
PREFIX_OK = re.compile(r"^(?:|(?:たった|わずか|ほんの|その|この|あの|たかが|ただの)|[^、。のば]{1,8}(?<![でにと])(?:は|が|も)、?)$")


def sentence_prefix(text: str, pos: int) -> str:
    """pos を含む文の、pos より前の部分（行頭の字下げと開き括弧は除く）。"""
    start = max(text.rfind(c, 0, pos) for c in "。！？!?\n") + 1
    return text[start:pos].strip(" \t　「『")


def corrected_claim(after: str):
    """「四文字ではなく六文字だ」の、訂正後の (数, 単位の種類, その後ろ)。訂正が無ければ None。"""
    m = re.match(rf"(?:では|じゃ)なく、?[\s　]*({NUM})[ 　]*(文字|もじ|字|音|拍|はく|ぱく|モーラ)", after)
    if not m:
        return None
    return parse_number(m.group(1)), ("char" if m.group(2) in ("文字", "もじ", "字") else "mora"), after[m.end():]


def claim_context(between: str, after: str, x: str, inside_speech: bool = False, prefix: str = "") -> str:
    """数の主張がどれだけ確かかを返す: "assert"（断定）/ "unsure"（要確認）/ "skip"（その数を否定している）。

    FAIL は「読んで確かめてから直す」運用だが、誤 FAIL が多いと確認の手間が増え、FAIL への信頼も落ちる。
    文脈の判定は完全にはならないので、迷う形は WARN 側へ倒す。断定として扱うのは、次をすべて満たすときだけ。
      - 引用より前が、無し・強調語・短い主語のどれか（「弟によれば、」のような出どころの標識が無い）
      - 数のあとが断定の形（文末、だ／だった、格助詞で続く、〜の言葉）で、読点以降に話が続かず、
        伝聞・思い込み・仮定・疑問・虚偽の標識が無い
      - 人物の台詞の中の言及ではない（人物は数え間違えてよい）
      - 語と数が同じ文にあるか、文をまたいでも「たった四文字の言葉。」のように直前の語を指すと読める
    それ以外で数が合わないものは、要確認（WARN）に回す。解析できない残りは、常に WARN 側へ倒す。"""
    parts = re.split(r"[。\n]", after, maxsplit=1)
    clause = parts[0]
    following = parts[1].lstrip(" \t　\n")[:60] if len(parts) > 1 else ""
    if NEGATION.match(clause[:14]):
        return "skip"
    if len(parts) == 1 and len(after) >= 150:
        return "unsure"          # 文を最後まで読み切れていない。読めていない部分に訂正や否定があるかもしれない
    if CORRECTION_AHEAD.search(following):
        return "unsure"          # 「四文字。いや、五文字だった。」次の文で書き手自身が直している
    if re.search(r"だけ(?:が|を|は)?残|が残っ|を残し", following):
        return "unsure"          # 「三文字を消した。紙には「み」だけが残った。」語の一部を扱う話
    if re.search(r"(子音|母音|音節|音素|ローマ字|アルファベット|画数)", between + clause):
        return "unsure"
    if re.search(r"[「『]", clause):
        return "unsure"          # 「三文字を消し、「お」だけ残した」語の一部を扱っているのかもしれない
    if inside_speech or not PREFIX_OK.match(prefix):
        return "unsure"
    if HEDGE.search(clause) or HEDGE.search(prefix):
        return "unsure"
    link = LINK_WORDS.sub("", between)            # 引用と数をつなぐ語から、強調語・同格の標識・約物を除いた残り
    if link in ("", "の"):                          # 同格形
        if not APPOS_TAIL.match(clause) or RECOUNT.search(clause):
            return "unsure"
        rest = re.sub(r"^(?:" + NOUN_AFTER + r")?" + COPULA + r"?", "", clause)
        rest = re.sub(r"[書聞読語話歌]き?手|読者|筆者|作者|記者", "", rest)      # 名詞を動詞と取り違えない
        if rest and not re.search(WORD_HANDLING, rest):
            return "unsure"
    elif link in ("は", "が", "も"):                # 主題形
        if not TOPIC_TAIL.match(clause):
            return "unsure"
    else:
        return "unsure"
    if not re.search(r"[。！？!?\n]", between):
        return "assert"
    if x.rstrip().endswith(("？", "?")) or re.match(r"[^。\n]{0,8}(返事|答え|返答|返信)", after):
        return "unsure"
    pointing = INTENSIFIER.search(between) is not None
    nominal = re.match(NOUN_AFTER + r"(?:[。]|$|だ|で|な(?:の|ん)|に(?:すぎ|過ぎ)|が(?!し)|は|も)", clause) is not None
    searching = re.match(NOUN_AFTER + r"を", clause) is not None      # 「三文字の言葉を探した」は別の語の話
    return "assert" if (pointing or nominal) and not searching else "unsure"


def accepted_char_counts(x: str) -> set:
    """「X」を何文字と数えても正しいと言える数の集合。
    記号や空白を含む語は、文字だけ・記号込み・空白込みのどれで数えるかが書き手によって違う。"""
    t = unicodedata.normalize("NFC", strip_notation(x))
    letters = sum(1 for c in t if unicodedata.category(c)[0] in ("L", "N"))
    nonspace = sum(1 for c in t if not c.isspace())
    k = unicodedata.normalize("NFKC", t)
    return {letters, nonspace, len(t), sum(1 for c in k if unicodedata.category(c)[0] in ("L", "N"))}


def in_open_quote(text: str, pos: int) -> bool:
    """pos が、同じ行の中で開いたままの括弧（「 でも 『 でも）の内側にあるか（＝人物の台詞や引用の中の言及か）。"""
    line_start = text.rfind("\n", 0, pos) + 1
    stack = []
    for c in text[line_start:pos]:
        if c in PAIRS:
            stack.append(PAIRS[c])
        elif stack and c == stack[-1]:
            stack.pop()
    return bool(stack)


def check_counting(doc: Doc, rep: Report):
    cfg = rep.cfg
    raw_text = "\n".join(doc.raw_lines)                       # 拍数はルビの読みで数えるので記法を外さない
    text = "\n".join(strip_notation(l) for l in doc.raw_lines)
    line_of = lambda t, pos: t.count("\n", 0, pos) + 1
    seen_nums = set()
    fails, warns, m_fail, m_warn = [], [], [], []

    def judge(kind, x, n, ln, between, after, inside_speech=False, prefix="", depth=0, ctx_override=None):
        """kind: "char" / "mora"。x=括弧の中、n=本文が言う数。"""
        ctx = ctx_override or claim_context(between, after, x, inside_speech, prefix)
        if ctx == "skip":
            fixed = corrected_claim(after)      # 否定のあとに言い直した数は、その単位で、同じ基準で判定し直す
            if fixed and depth == 0:
                judge(fixed[1], x, fixed[0], ln, between, fixed[2], inside_speech, prefix, 1)
            return
        if n is None:
            return
        if kind == "char":
            ok = accepted_char_counts(x)
            actual = count_chars_of(x)
            if actual == 0 or n in ok:
                return
            msg = f"「{excerpt(x, 14)}」は {actual} 文字（本文は「{n} 文字」）"
            if len(ok) > 1:
                # 記号や空白を含む語。どの数え方でも合わないが、直し先を機械が決められないので断定しない
                warns.append((ln, msg + f" ※記号・空白を含む。数え方は {sorted(ok)} のどれでも合わない"))
            elif ctx == "unsure" or actual > 14:
                warns.append((ln, msg + " ※指している語や、誰の主張かを確かめる"))
            elif KANJI.search(x) and actual < n <= actual + 3 * len(KANJI.findall(x)):
                # 漢字 1 字の読みは 1〜4 字ほど。かなで数えた可能性がある範囲だけ断定を避ける
                warns.append((ln, msg + " ※かな読みで数えた可能性。どちらで数えるか決めて本文を合わせる"))
            else:
                fails.append((ln, msg))
        else:
            morae = count_morae(x)
            if morae is None:
                m_warn.append((ln, f"「{excerpt(x, 14)}」の {n} 音: かなだけではないので機械では数えられない。読みを書き出して数える"))
            elif morae != n:
                msg = f"「{excerpt(x, 14)}」は {morae} 音（本文は「{n} 音」）"
                if ctx == "unsure":
                    m_warn.append((ln, msg + " ※指している語や、誰の主張かを確かめる"))
                elif morae_ambiguous(x):
                    m_warn.append((ln, msg + " ※小書きの母音を延ばして読むなら変わる"))
                else:
                    m_fail.append((ln, msg))

    noun_before_quote = re.compile(r"(言葉|単語|名前|返事|答え|返信|返答|メッセージ|挨拶|台詞|セリフ|一言|ひとこと|メール|呟き|つぶやき)")
    for kind, fwd, rev, src in (("char", RX_FWD_CHAR, RX_REV_CHAR, text), ("mora", RX_FWD_MORA, RX_REV_MORA, raw_text)):
        used = set()
        for m in fwd.finditer(src):
            used.add(m.start(3))
            if kind == "char":
                seen_nums.add(m.start(3))
            judge(kind, m.group(1), parse_number(m.group(3)), line_of(src, m.start(3)), m.group(2), src[m.end():m.end() + 160],
                  in_open_quote(src, m.start()), sentence_prefix(src, m.start()))
        for m in rev.finditer(src):
            if m.start(1) in used:
                continue
            gap = m.group(0)[:m.group(0).rfind(m.group(2))]
            # 逆順（三文字の言葉。「X」）は、数が言葉・返事などを指していると読めるときだけ対応づける
            if re.search(r"[。！？!?\n]", gap) and not noun_before_quote.search(gap):
                continue
            if kind == "char":
                seen_nums.add(m.start(1))
            unit = re.match(rf"{NUM}[ 　]*(?:文字|もじ|字|音|拍|はく|ぱく|モーラ)", src[m.start(1):])
            gap_tail = gap[unit.end():] if unit else ""
            prefix = sentence_prefix(src, m.start(1))
            tail_parts = re.split(r"[。\n]", src[m.end():m.end() + 160], maxsplit=1)
            clause_after = tail_parts[0]
            following = tail_parts[1].lstrip(" \t　\n")[:60] if len(tail_parts) > 1 else ""
            # 逆順で断定と読めるのは「（たった）四文字の言葉、「X」」の形だけ。数と括弧の間に言葉・名前などの名詞があり、
            # 前後に伝聞・仮定・疑問の標識が無く、括弧のあとが文末か格助詞で続く
            shape_ok = re.fullmatch(NOUN_AFTER + r"(?:は|が|だ|だった)?[、。…―\s　]*[「『“\"]?", gap_tail) is not None
            if NEGATION.match(gap_tail[:14]):
                continue
            sure = (shape_ok and PREFIX_OK.match(prefix) and not in_open_quote(src, m.start(1))
                    and not HEDGE.search(prefix + gap_tail + clause_after) and ASSERTIVE_TAIL.match(clause_after)
                    and not RECOUNT.search(clause_after) and not CORRECTION_AHEAD.search(following)
                    and len(clause_after) <= 30
                    and (clause_after == "" or re.search(WORD_HANDLING, clause_after) or re.fullmatch(COPULA, clause_after)))
            judge(kind, m.group(2), parse_number(m.group(1)), line_of(src, m.start(1)), gap_tail, "",
                  ctx_override="assert" if sure else "unsure")
    if fails:
        rep.add("C01", "FAIL", "字数の言い間違い", f"{len(fails)} 箇所。括弧の中の実字数と本文の数が合わない", fails,
                "数を実数に直すか、「その短い言葉」のように数を言わない形へ")
    if warns:
        rep.add("C01", "WARN", "字数の言及（要確認）", f"{len(warns)} 箇所", warns, "指している語と数え方を確かめる")
    if m_fail:
        rep.add("C03", "FAIL", "音数の言い間違い", f"{len(m_fail)} 箇所", m_fail, "拗音の小書きは前の字と 1 拍、促音・長音・撥音は 1 拍")
    if m_warn:
        rep.add("C03", "WARN", "音数の言及（要確認）", f"{len(m_warn)} 箇所", m_warn, "")
    # C02 括弧なし
    generic = set(cfg.lexicon.get("count_generic_nouns", []))
    bare = []
    for m in RX_BARE.finditer(text):
        if m.start(2) in seen_nums:
            continue
        word, n = m.group(1), parse_number(m.group(2))
        seen_nums.add(m.start(2))
        if word in generic or n is None or len(word) == n or claim_context("", text[m.end():m.end() + 30], word) == "skip":
            continue
        bare.append((line_of(text, m.start(2)), f"「{word}」は {len(word)} 文字（本文は「{n} 文字」）※語の切れ目が違えば無視"))
    if bare:
        rep.add("C02", "WARN", "字数の言及（括弧なし・要確認）", f"{len(bare)} 箇所", bare, "対象の語を括弧に入れると機械で検算できる")
    # C04 定型詩
    verse_kw = cfg.rx("verse_keywords")
    lines = raw_text.split("\n")
    verse = []
    for i, line in enumerate(lines):
        if not verse_kw.search("\n".join(lines[max(0, i - 1):i + 2])):
            continue
        for m in re.finditer(rf"[{OPEN_Q}]([^{OPEN_Q}{CLOSE_Q}\n]{{5,80}})[{CLOSE_Q}]", line):
            segs = [s for s in re.split(r"[\s　／/]+", m.group(1).strip()) if s]
            want = {3: [5, 7, 5], 5: [5, 7, 5, 7, 7]}.get(len(segs))
            shown = excerpt(strip_notation(m.group(1)), 16)
            if want:
                got = [count_morae(s) for s in segs]
                if None in got:
                    verse.append((i + 1, f"「{shown}」: かなだけではないので拍数は要目視"))
                elif got != want:
                    verse.append((i + 1, f"「{shown}」は {'-'.join(map(str, got))}（定型は {'-'.join(map(str, want))}）"))
            elif len(segs) == 1:
                total = count_morae(segs[0])
                if total is None:
                    verse.append((i + 1, f"「{shown}」: 句の拍数は要目視（読みを書き出して数える）"))
                elif total not in (17, 31) and 12 <= total <= 36:
                    verse.append((i + 1, f"「{shown}」は全 {total} 音（俳句 17・短歌 31）"))
    if verse:
        rep.add("C04", "WARN", "定型詩の音数", f"{len(verse)} 箇所", verse, "字余り・字足らずが意図なら keep")
    # C08 存在しない日付、年月日と曜日の食い違い
    dates = []
    for m in RX_DATE.finditer(text):
        y, mo, d = (parse_number(v) if v else None for v in (m.group(1), m.group(2), m.group(3)))
        if mo is None or d is None:
            continue
        ln = line_of(text, m.start())
        if not (1 <= mo <= 12) or d < 1 or d > 31:
            dates.append((ln, f"{mo} 月 {d} 日は暦に無い"))
            continue
        if y and 1600 <= y <= 2400:
            try:
                weekday = "月火水木金土日"[datetime.date(y, mo, d).weekday()]
            except ValueError:
                dates.append((ln, f"{y} 年 {mo} 月 {d} 日は暦に無い"))
                continue
            if m.group(4) and weekday != m.group(4):
                dates.append((ln, f"{y} 年 {mo} 月 {d} 日は{weekday}曜（本文は{m.group(4)}曜）"))
        elif d > (29 if mo == 2 else 30 if mo in (4, 6, 9, 11) else 31):
            dates.append((ln, f"{mo} 月 {d} 日は暦に無い"))      # 年が無い 2 月 29 日は断定しない
    if dates:
        rep.add("C08", "WARN", "日付と曜日", f"{len(dates)} 箇所", dates, "架空の暦でなければ直す。年齢・経過日数は Python で別に確かめる")
    # C05 / C06 / C07 機械では確かめきれない言及の位置（案内であって、検算済みを意味しない）
    kw = [(line_of(text, m.start()), excerpt(text[max(0, m.start() - 6):m.end() + 10], 28)) for m in cfg.rx("count_keywords").finditer(text)]
    if kw:
        rep.add("C05", "INFO", "文字遊び・順番・画数の言及", f"{len(kw)} 箇所。Python で検算する", kw, "確かめられないなら書かない")
    en = [(line_of(text, m.start()), excerpt(text[m.start():m.end() + 12], 28)) for m in cfg.rx("count_enumeration").finditer(text)]
    if en:
        rep.add("C06", "INFO", "個数の宣言", f"{len(en)} 箇所。実際に挙げた数と合っているか数える", en, "")
    # C09 割合と内訳。「二十枚のうち五枚」を「半分」と書くような食い違いは機械では確かめきれないので、数量の近くにある割合・内訳の語の位置だけ知らせる
    quantity = re.compile(rf"{NUM}[ 　]*(?:枚|個|人|名|着|本|冊|台|件|回|組|匹|頭|羽|軒|通|席|票|杯|点|円|万|日|年|か月|ヶ月|ヵ月|時間|週間|歳|才|キロ|メートル|グラム|パーセント|％)")
    share = re.compile(rf"(?<![話白び談])半分(?!冗談|本気)|半数|半額|(?:枚|個|人|名|着|本|冊|台|件|回|組|匹|頭|羽)のうち|{NUM}分の{NUM}|{NUM}割(?!り)|{NUM}倍|"
                       rf"{NUM}[ 　]*(?:%|％|パーセント)|(?:その)?うち[、 　]*{NUM}[ 　]*(?:枚|個|人|名|着|本|冊|台|件|回|組|匹|頭|羽)|残り(?:は|の|が)?[ 　]*{NUM}|合わせて[ 　]*{NUM}|合計[ 　]*{NUM}|計[ 　]*{NUM}")
    amounts = [m.start() for m in quantity.finditer(text)]
    shares = [(line_of(text, m.start()), excerpt(text[max(0, m.start() - 14):m.end() + 10], 30)) for m in share.finditer(text)
              if any(abs(m.start() - q) <= 60 for q in amounts)]
    if shares:
        rep.add("C09", "INFO", "割合・内訳・合計の言及", f"{len(shares)} 箇所。全体の数と突き合わせて計算する", shares,
                "二十枚のうち五枚は四分の一で、半分ではない。分配・交渉・日程の条件は、場面の途中で数が入れ替わっていないかも見る")
    loose = re.compile(rf"({NUM})[ 　]*(?:文字|もじ|字(?![幕体形面引義通余数])|音(?![楽色程階符声量質響速読節感])|拍(?![手車子])|はく(?![しり])|モーラ)(?!目|熟語|詰)")
    mora_seen = {m.start(3) for m in RX_FWD_MORA.finditer(text)} | {m.start(1) for m in RX_REV_MORA.finditer(text)}
    handled = seen_nums | mora_seen
    rest = [(line_of(text, m.start(1)), excerpt(text[max(0, m.start() - 12):m.end() + 4], 28))
            for m in loose.finditer(text) if m.start(1) not in handled]
    if rest:
        rep.add("C07", "INFO", "その他の字数・音数の言及", f"{len(rest)} 箇所。対象の語を確かめて数える（機械は対象を特定できていない）", rest, "")


# ---------------------------------------------------------------------------
# M / X / G / O
# ---------------------------------------------------------------------------
def check_meta(doc: Doc, rep: Report, min_chars: int):
    if doc.total_chars == 0:
        rep.add("M01", "FAIL", "本文が空", "見出しと空行しか無い。保存の失敗を疑う", [], "原稿を書き直して保存し直す")
        return
    if doc.total_chars < min_chars:
        rep.add("M01", "WARN", "本文が極端に短い", f"{doc.total_chars} 字（目安 {min_chars} 字未満）。保存が途中で切れていないか", [], "")
    nonempty = [(i, l) for i, l in enumerate(doc.raw_lines, 1) if l.strip()]
    edge = nonempty[:5] + nonempty[-5:] if len(nonempty) > 10 else nonempty
    pats = [re.compile(p, re.I) for p in rep.cfg.lexicon["meta"]]
    locs = []
    for ln, l in edge:
        s = l.strip()
        if s.startswith(("「", "『")):
            continue
        if any(p.search(s) for p in pats):
            locs.append((ln, excerpt(s, 28)))
    if locs:
        rep.add("M02", "WARN", "本文以外の混入", f"{len(locs)} 行。前置き・後書き・解説・字数の注記など", locs,
                "原稿ファイルには本文だけを置く。前提は会話の側で告げる")


def check_leak_and_glossary(doc: Doc, rep: Report):
    cfg = rep.cfg
    found = find_all(doc.narr_lines(), cfg.rx("leak"))
    if found:
        rep.add("X01", "INFO", "創作用語の漏出（地の文）", f"{len(found)} 箇所。物語の外の語彙が語りに出ていないか",
                [(ln, ex) for ln, _, ex in found], "作中で実際にその語を使う人物・題材なら keep")
    for word in cfg.forbidden_words:
        locs = []
        for p in doc.paras:
            if word in p["clean"]:
                where = "作中文書" if p["kind"] == "document" else "地の文" if word in p["narr"] else "台詞"
                locs.append((p["line"], f"{where}: {excerpt(p['clean'][max(0, p['clean'].find(word) - 8):], 22)}"))
        if locs:
            rep.add("G01", "WARN", f"世界制約の禁止語彙「{word}」", f"{len(locs)} 箇所", locs, "この世界・時代に無い語。言い換えでなく、その世界にある物で書く")
    for variant, canonical in cfg.variants:
        locs = [(p["line"], excerpt(p["clean"][max(0, p["clean"].find(variant) - 8):], 22)) for p in doc.paras if variant in p["clean"]]
        if locs:
            rep.add("G02", "WARN", f"表記ゆれ「{variant}」", f"{len(locs)} 箇所。正は「{canonical}」", locs, "glossary の note に台詞での例外があれば keep")


def check_edges(doc: Doc, rep: Report):
    cfg = rep.cfg
    if not doc.paras:
        return
    head, acc = "", 0
    for p in doc.paras:
        head += p["clean"] + "\n"
        acc += nws(p["clean"])
        if acc >= 200:
            break
    for pat in cfg.lexicon["opening"]:
        m = re.search(pat, head, re.M)
        if m:
            rep.add("O01", "INFO", "ありがちな入り方", f"冒頭 200 字に「{excerpt(m.group(0), 14)}」", [(doc.paras[0]["line"], excerpt(doc.paras[0]["clean"]))],
                    "天気・目覚め・回想の前置きから入っていないか。出来事の途中から入れないか")
            break
    last = doc.paras[-1]
    tail = (doc.paras[-2]["clean"] + "\n" if len(doc.paras) > 1 else "") + last["clean"]
    hit = []
    for pat in cfg.lexicon["ending"]:
        m = re.search(pat, tail, re.M)
        if m:
            hit.append(excerpt(m.group(0), 10))
    if hit:
        rep.add("O02", "INFO", "ありがちな締め方", "結びに " + "、".join(f"「{h}」" for h in hit[:4]), [(last["line"], excerpt(last["clean"]))],
                "希望の総括・語りの予告・きれいな着地になっていないか。最後の 1〜2 文を削って成立するか試す")


# ---------------------------------------------------------------------------
# 実行と出力
# ---------------------------------------------------------------------------
def lint_text(text: str, cfg: Config, only: str = "", skip: str = "", min_chars: int | None = None):
    doc = Doc(text)
    rep = Report(cfg, only, skip)
    stats = collect_stats(doc, cfg)
    check_meta(doc, rep, min_chars if min_chars is not None else cfg.guards["body_min_chars"])
    check_markdown(doc, rep)
    if doc.total_chars:
        check_notation(doc, rep)
        check_counting(doc, rep)
        # 掌編は標本が小さく統計が暴れるので、run と表記と数え上げを中心に見る（段落・会話比率の統計は止める）
        if cfg.length == "flash":
            rep.flash_skip = {"P02", "P03", "P05", "D01", "L04", "K06"}
        check_rhythm(doc, rep, stats)
        check_dialogue(doc, rep, stats)
        check_density(doc, rep)
        check_leak_and_glossary(doc, rep)
        check_edges(doc, rep)
    public = {k: v for k, v in stats.items() if not k.startswith("_")}
    rep.hits.sort(key=lambda h: (SEVERITY_ORDER[h["severity"]], h["rule"]))
    return public, rep.hits


def parse_range(spec: str):
    """--range の値を (下限, 上限) にする。"1200-1500" は範囲、"1500" は約 1500 字（±1 割）。読めなければ None。"""
    m = re.fullmatch(r"\s*(\d+)\s*(?:[-〜~]\s*(\d+))?\s*", spec or "")
    if not m:
        return None
    if m.group(2):
        low, high = int(m.group(1)), int(m.group(2))
        return (low, high) if low <= high else None
    n = int(m.group(1))
    return round(n * 0.9), round(n * 1.1), "approx"


def range_floor(bounds) -> int:
    """「約 N 字」は目安なので、下限を 3% まで割っても範囲内として扱う（下限ぴったりへ合わせにいく周回を止めるため）。上限と、範囲の指定には遊びを付けない。"""
    return bounds[0] - round(bounds[0] * 0.03) if len(bounds) > 2 else bounds[0]


def range_label(bounds) -> str:
    return f"約 {round((bounds[0] + bounds[1]) / 2)} 字（{bounds[0]}〜{bounds[1]}。下限は {range_floor(bounds)} 字まで可）" if len(bounds) > 2 else f"{bounds[0]}〜{bounds[1]}"


def range_status(chars: int, bounds) -> str:
    if chars < range_floor(bounds):
        return f"不足 {bounds[0] - chars} 字"
    if chars > bounds[1]:
        return f"超過 {chars - bounds[1]} 字"
    return "範囲内"


def next_step(results: list, cfg: Config, bounds) -> str:
    """検査の結果を読んだ直後に、次に何をするか（何をしないか）を 1 行で言う。周回を止めるための案内で、判定ではない。"""
    parts, length_off = [], False
    fails = sum(1 for r in results for h in r["hits"] if h["severity"] == "FAIL")
    for r in results if bounds else []:      # 字数の指定は、ファイルごとに見る
        name = f"{r['path']}: " if len(results) > 1 else ""
        status = range_status(r["stats"]["chars"], bounds)
        if status == "範囲内":
            parts.append(f"{name}字数は指定の範囲内。これ以上は合わせにいかない")
        else:
            length_off = True
            parts.append(f"{name}字数は指定の外（{status}）。lint は数を報告するだけで、どう扱うかは指定の種類で決まる"
                         "（目安ならこのまま出してよい。守る条件なら、継ぎ足しの周回はせず、構成から 1 回書き直す。references/short-pipeline.md 3 節）")
    if fails:
        parts.append("FAIL の箇所を読んで誤りと確かめたものを直す。直したら 1 度かけ直して確かめる")
    elif cfg.length in ("flash", "short") and not length_off:
        parts.append("FAIL は無い。WARN・INFO は読んで fix / keep を決める（INFO の詳細は --all）。本文を変えていないなら、かけ直す必要は無い。"
                     "数値を警報線の内側へ入れるためだけの書き直しはしない。誤りを直したときの確認のかけ直しは、してよい")
    return "次にやること: " + "。".join(parts) if parts else ""


def render(path: str, stats: dict, hits: list, cfg: Config, max_locs: int, show_all: bool, bounds=None) -> str:
    out = [f"== {path}  [{cfg.profile} / {cfg.length}]  {stats['chars']} 字（地の文 {stats['narration_chars']} 字、会話比率 "
           f"{stats['dialogue_ratio'] if stats['dialogue_ratio'] is not None else '-'}）"
           + (f"  字数の指定 {range_label(bounds)}: {range_status(stats['chars'], bounds)}" if bounds else "")]
    shown = [h for h in hits if h["severity"] != "INFO" or show_all]
    for h in shown:
        out.append(f"[{SEVERITY_LABEL[h['severity']]}] {h['rule']} {h['name']}: {h['message']}")
        for loc in h["locations"][:max_locs]:
            out.append(f"    L{loc['line']}: {loc['excerpt']}")
        if len(h["locations"]) > max_locs:
            out.append(f"    … ほか {len(h['locations']) - max_locs} 箇所（全件は --json）")
        if h["hint"]:
            out.append(f"    → {h['hint']}")
    infos = [h for h in hits if h["severity"] == "INFO"]
    if infos and not show_all:
        out.append("[INFO] " + "、".join(f"{h['rule']} {h['name']}" for h in infos) + "（詳細は --all）")
    if not hits:
        out.append("該当なし")
    return "\n".join(out)


def render_stats(path: str, stats: dict) -> str:
    rows = [f"== {path}"]
    for k, v in stats.items():
        rows.append(f"  {k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v}")
    return "\n".join(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="日本語小説の本文を機械検査する。FAIL は表記と数え上げの誤りだけ。ほかは警報で、fix か keep を書き手が選ぶ。総合スコアは出さない。",
        epilog="例: python novel_lint.py ch001.md --profile web / python novel_lint.py 見本.txt --stats --json / "
               "python novel_lint.py manuscript/ch003.md --project . --only NC")
    ap.add_argument("files", nargs="+", help="本文ファイル（UTF-8。1 段落 1 行。# 見出しと記号だけの行は本文に数えない）。- で標準入力")
    ap.add_argument("--profile", choices=["bungei", "entertainment", "web"], help="既定: novel.toml の値、無ければ entertainment")
    ap.add_argument("--length", choices=["flash", "short", "long"], help="既定: novel.toml の値、無ければ long")
    ap.add_argument("--project", help="長編プロジェクトのルート。novel.toml / style/lint.json / style/glossary.toml を読む")
    ap.add_argument("--stats", action="store_true", help="統計だけを出す（文体模写の計量、calibrate.py 用）")
    ap.add_argument("--json", action="store_true", help="全件を JSON で出す")
    ap.add_argument("--all", action="store_true", help="INFO の詳細も表示する")
    ap.add_argument("--only", default="", help=f"検査するルール群の頭文字（例: NC）。群: {GROUPS}")
    ap.add_argument("--skip", default="", help="外すルール群の頭文字（例: KO）")
    ap.add_argument("--max-locs", type=int, default=4, help="1 ルールあたりの表示箇所数（既定 4）")
    ap.add_argument("--min-chars", type=int, help="これ未満なら「極端に短い」と警告（既定 100）")
    ap.add_argument("--emotion-naming", choices=["restrained", "balanced", "direct"],
                    help="感情の名指しの契約。プロジェクト（novel.toml）が無い掌編でも渡せる。direct なら、感情の名指しの密度（K08）を警報にしない")
    ap.add_argument("--range", dest="char_range", help="依頼された字数。1200-1500 か、約 N 字なら 1500（±1 割。目安なので、下限だけは 3% まで割っても範囲内とする）。ファイルごとに、1 行目の字数（count_chars.py の body と同じ数え方）と比べて知らせる")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    bounds = parse_range(args.char_range) if args.char_range else None
    if args.char_range and bounds is None:
        print(f"エラー: --range {args.char_range} を読めない。1200-1500 か 1500 の形で書く", file=sys.stderr)
        return 2
    bad = [c for c in (args.only + args.skip).upper() if c not in GROUPS]
    if bad:
        print(f"エラー: ルール群 {bad} は無い。使えるのは {' '.join(GROUPS)}", file=sys.stderr)
        return 2
    try:
        project = Path(args.project) if args.project else None
        if project and not project.is_dir():
            print(f"エラー: --project {project} はディレクトリではない", file=sys.stderr)
            return 2
        cfg = Config(args.profile, args.length, project)
        if args.emotion_naming:
            cfg.emotion_naming = args.emotion_naming
        cfg.finalize()
    except Exception as exc:  # 設定の読み込み失敗は実行エラー
        print(f"エラー: 設定を読めない: {exc}", file=sys.stderr)
        return 2
    results, any_fail = [], False
    for name in args.files:
        path = Path(name)
        if name == "-":      # 掌編などファイルに保存していない本文は標準入力から
            try:
                text = sys.stdin.buffer.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                print("エラー: 標準入力を UTF-8 で読めない", file=sys.stderr)
                return 2
            path = Path("(stdin)")
        elif not path.is_file():
            print(f"エラー: {path} が無い。本文ファイルのパスを確かめる（標準入力なら -）", file=sys.stderr)
            return 2
        else:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                print(f"エラー: {path} を UTF-8 で読めない。UTF-8 で保存し直す", file=sys.stderr)
                return 2
        stats, hits = lint_text(text, cfg, args.only.upper(), args.skip.upper(), args.min_chars)
        if args.stats:
            hits = []
        any_fail = any_fail or any(h["severity"] == "FAIL" for h in hits)
        results.append(dict(path=str(path), profile=cfg.profile, length=cfg.length, stats=stats, hits=hits))
    if args.json:
        contract = ("lint は確認する箇所を示す検知器で、修正命令ではない。FAIL も、該当行と前後の本文を読んで誤りと確かめたものだけ直す。"
                    "誤検出や意図した表現なら本文を変えず、理由を残して keep。完了の条件は FAIL が 0 になることではなく、指摘をすべて確認・処置すること。"
                    "C05〜C07 は検算対象の案内で、検算済みを意味しない。")
        step = next_step(results, cfg, bounds)
        print(json.dumps({"files": results, "notes": cfg.notes + [contract] + ([step] if step else [])}, ensure_ascii=False, indent=1))
    else:
        for r in results:
            print(render_stats(r["path"], r["stats"]) if args.stats else render(r["path"], r["stats"], r["hits"], cfg, args.max_locs, args.all, bounds))
        for note in cfg.notes:
            print(f"注: {note}")
        if not args.stats:
            n = {s: sum(1 for r in results for h in r["hits"] if h["severity"] == s) for s in SEVERITY_ORDER}
            print(f"-- FAIL {n['FAIL']} / 強WARN {n['STRONG']} / WARN {n['WARN']} / INFO {n['INFO']}。"
                  "FAIL は、その箇所を読んで誤りと確かめてから直す（機械は文脈を読めない。誤検出なら本文を変えず理由を残す）。"
                  "警報は一件ずつ fix か keep を選ぶ（比率の目標は無い。同義語への置き換えはしない）。"
                  "C05〜C07・C09 は案内で、検算済みを意味しない")
            step = next_step(results, cfg, bounds)
            if step:
                print(step)
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
