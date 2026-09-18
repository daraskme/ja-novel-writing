#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_lint.py — 長編（serial）プロジェクトの台帳を機械で点検する。

見るのは「台帳どうしの食い違い」と「台帳の遅れ」だけで、本文の出来は見ない
（本文は novel_lint.py の担当）。点数は出さない。出すのは場所と、直す方向の一句。

このファイルの前半（読み込み部）は build_context.py からも import される。
台帳の読み方を 2 か所に書くと必ずずれるので、読み込みはここを唯一の実装にしている。

終了コード: 0 = FAIL なし / 1 = FAIL あり / 2 = 実行エラー
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 古い Python 向けの案内用
    tomllib = None

# --------------------------------------------------------------------------
# 定数（仕様書 §4 のキーと列挙値）
# --------------------------------------------------------------------------
PROFILES = ("bungei", "entertainment", "web")
LENGTHS = ("flash", "short", "long")
MEDIUMS = ("narou", "kakuyomu", "alphapolis", "pixiv", "print", "none")
CHAPTER_HOOKS = ("required", "optional", "off")
EMOTION_NAMING = ("restrained", "balanced", "direct")
INDENTS = ("fullwidth", "none", "export")
F_STATUSES = ("planned", "planted", "reminded", "paid", "dropped")
F_OPEN = ("planned", "planted", "reminded")  # まだ回収も放棄もしていない
FACT_STATUSES = ("proposed", "confirmed")
DECISION_KINDS = ("change", "reject", "note")

RE_CHAR_ID = re.compile(r"^C\d+$")
RE_LOC_ID = re.compile(r"^L\d+$")
RE_FORE_ID = re.compile(r"^F\d+$")
RE_CHAPTER_FILE = re.compile(r"^ch(\d{3,})$")
RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
RE_UNDECIDED_NAME = re.compile(r"TBD|未定|（仮）|\(仮\)|仮称|\{\{")
# 見出しは「## D001 2026-09-02 reject」。後ろに「（ch005, C03）by user」を続ける書き方も受ける
RE_DECISION_HEAD = re.compile(
    r"^##\s+(D\d+)\s+(\S+)\s+(change|reject|note)\b[ \t]*(.*)$", re.M
)
RE_REF_ID = re.compile(r"ch\d{3,}|[CLF]\d{2,}")
RE_LOCATION_LINE = re.compile(r"^\s*[-*]\s*(L\d+)\s+([^:：\n]+?)\s*(?:[:：]\s*(.*))?$", re.M)
RE_ENDING_LINE = re.compile(r"^ch(\d{3,})\s*\|\s*頭[:：]\s*(.*?)\s*\|\s*末[:：]\s*(.*?)\s*$")

# これより短い本文は「保存事故かもしれない」と知らせる（空保存は実例の多い事故）
MIN_MANUSCRIPT_CHARS = 100
# STATUS.md は再開時に毎回読むので、長くなったら知らせる
STATUS_MAX_LINES = 40
# 要約・状態スナップショットが雛形のまま（実質空）かを見る下限
MIN_CANON_NOTE_CHARS = 20
# proposed が何章ぶん溜まったら WARN にするか（2 章ぶん遅れると回復コストが跳ねる）
PROPOSED_LAG_CHAPTERS = 2


# --------------------------------------------------------------------------
# データ構造
# --------------------------------------------------------------------------
@dataclass
class Finding:
    level: str  # FAIL / WARN / INFO
    code: str
    where: str
    message: str


@dataclass
class Character:
    id: str
    name: str
    reading: str
    path: Path
    text: str  # コメントを含む全文


@dataclass
class Decision:
    id: str
    date: str
    kind: str
    what: str
    why: str
    affects: list
    by: str = ""  # user / agent（書いてあれば）


@dataclass
class Project:
    root: Path
    novel: dict = field(default_factory=dict)
    beats: dict = field(default_factory=dict)  # 章番号 -> dict
    beat_paths: dict = field(default_factory=dict)  # 章番号 -> Path
    foreshadowing: list = field(default_factory=list)
    glossary: dict = field(default_factory=dict)
    characters: dict = field(default_factory=dict)  # ID -> Character
    locations: dict = field(default_factory=dict)  # ID -> 名称
    facts: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    endings: list = field(default_factory=list)  # (章番号, 頭, 末)
    manuscripts: dict = field(default_factory=dict)  # 章番号 -> Path
    manuscript_chars: dict = field(default_factory=dict)  # 章番号 -> 本文字数
    findings: list = field(default_factory=list)  # 読み込み時に見つけた問題
    broken_files: set = field(default_factory=set)  # 構文エラーで読めなかったファイル

    def rel(self, path: Path) -> str:
        """プロジェクトからの相対パスを / 区切りで返す（Windows でも表示を揃える）。"""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def add(self, level: str, code: str, where, message: str) -> None:
        where_s = self.rel(where) if isinstance(where, Path) else str(where)
        self.findings.append(Finding(level, code, where_s, message))

    @property
    def last_written(self) -> int:
        """本文が（空でなく）存在する最後の章。無ければ 0。"""
        written = [ch for ch, n in self.manuscript_chars.items() if n > 0]
        return max(written) if written else 0


# --------------------------------------------------------------------------
# 小さな道具
# --------------------------------------------------------------------------
def chapter_id(n: int) -> str:
    return f"ch{n:03d}"


def read_text(path: Path) -> str:
    # PowerShell の既定出力は BOM 付き UTF-8 になりやすいので utf-8-sig で読む
    return path.read_text(encoding="utf-8-sig")


def strip_html_comments(text: str) -> str:
    """雛形の案内コメントを落とす。コメントは人と LLM への注記で、台帳の中身ではない。"""
    text = RE_HTML_COMMENT.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def body_char_count(text: str) -> int:
    """本文字数の概算（見出し行と空白を除く）。保存事故の検出用で、字数の正本は count_chars.py。"""
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return len(re.sub(r"\s", "", "".join(lines)))


def require_tomllib() -> None:
    if tomllib is None:
        ver = ".".join(map(str, sys.version_info[:3]))
        raise SystemExit(
            f"実行エラー: TOML を読むには Python 3.11 以上（標準の tomllib）が要る。いまは {ver}。\n"
            "  試せるもの: `py -3.11 <このスクリプト>` / `python3.11 <このスクリプト>`\n"
            "  どれも無ければ、このスクリプトを飛ばし、飛ばしたことを報告に 1 行書いて続ける。"
        )


def parse_character_heading(text: str, fallback_stem: str):
    """人物シートの 1 行目「# C01 名前（よみ）」から (ID, 名前, よみ) を取る。

    見出しが崩れていてもファイル名「C01-名前.md」から拾えるようにしておく。
    """
    m = re.search(r"^#\s*(C\d+)\s+(.+?)\s*$", strip_html_comments(text), re.M)
    if m:
        cid, rest = m.group(1), m.group(2)
        rm = re.match(r"^(.*?)\s*[（(]([^）)]*)[）)]", rest)
        if rm:
            return cid, rm.group(1).strip(), rm.group(2).strip()
        return cid, rest.strip(), ""
    fm = re.match(r"^(C\d+)(?:[-_ ](.+))?$", fallback_stem)
    if fm:
        return fm.group(1), (fm.group(2) or "").strip(), ""
    return "", fallback_stem, ""


# --------------------------------------------------------------------------
# 読み込み（build_context.py と共用）
# --------------------------------------------------------------------------
def _load_toml(project: Project, path: Path):
    """TOML を読む。構文エラーは Finding にして None を返し、他のファイルの検査を止めない。"""
    try:
        return tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as exc:
        project.add("FAIL", "E01", path, f"TOML の構文エラー: {exc}")
    except UnicodeDecodeError:
        project.add("FAIL", "E01", path, "UTF-8 で読めない。UTF-8 で保存し直す")
    project.broken_files.add(project.rel(path))
    return None


def _load_beats(project: Project) -> None:
    beats_dir = project.root / "plot" / "beats"
    if not beats_dir.is_dir():
        return
    for path in sorted(beats_dir.glob("*.toml")):
        m = RE_CHAPTER_FILE.match(path.stem)
        if not m:
            project.add("WARN", "W08", path, "章ビートのファイル名は ch001.toml の形にする（3 桁ゼロ埋め）")
            continue
        number = int(m.group(1))
        data = _load_toml(project, path)
        project.beat_paths[number] = path
        if data is not None:
            project.beats[number] = data


def _load_characters(project: Project) -> None:
    chars_dir = project.root / "bible" / "characters"
    if not chars_dir.is_dir():
        return
    for path in sorted(chars_dir.glob("*.md")):
        text = read_text(path)
        cid, name, reading = parse_character_heading(text, path.stem)
        if not cid:
            project.add("WARN", "W05", path, "人物 ID が読み取れない。1 行目を「# C01 名前（よみ）」にする")
            continue
        if cid in project.characters:
            other = project.rel(project.characters[cid].path)
            project.add("FAIL", "E06", path, f"人物 ID {cid} が重複している（もう一方: {other}）")
            continue
        project.characters[cid] = Character(cid, name, reading, path, text)


def _load_locations(project: Project) -> None:
    path = project.root / "bible" / "world.md"
    if not path.is_file():
        return
    body = strip_html_comments(read_text(path))
    for m in RE_LOCATION_LINE.finditer(body):
        project.locations[m.group(1)] = m.group(2).strip()


def _load_facts(project: Project) -> None:
    path = project.root / "canon" / "facts.jsonl"
    if not path.is_file():
        return
    for lineno, line in enumerate(read_text(path).splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            project.add("FAIL", "E01", f"{project.rel(path)}:{lineno}", f"JSON として読めない行: {exc.msg}")
            continue
        if not isinstance(row, dict):
            project.add("FAIL", "E02", f"{project.rel(path)}:{lineno}", "1 行は 1 つの JSON オブジェクトにする")
            continue
        row["_line"] = lineno
        project.facts.append(row)


def parse_decisions(text: str) -> list:
    """決定ログ（Markdown）を項目に分ける。見出し「## D001 2026-09-02 reject」が区切り。

    本文は 2 通りの書き方を受ける（どちらで書かれても、パックと検査から漏らさないため）:
      箇条書き: 「- 内容: …」「- 理由: …」「- 影響: F001, C04」
      地の文  : 見出しに「（ch005, C03）by user」、本文 1〜2 行、続けて「理由: …」
    """
    body = strip_html_comments(text)
    heads = list(RE_DECISION_HEAD.finditer(body))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        chunk = body[m.end():end]
        tail = m.group(4)

        def pick(label: str) -> str:
            fm = re.search(rf"^\s*(?:[-*]\s*)?{label}\s*[:：]\s*(.*)$", chunk, re.M)
            return fm.group(1).strip() if fm else ""

        what = pick("内容")
        if not what:
            # ラベルなしの地の文を内容とみなす（理由・影響の行は除く）
            free = [ln.strip().lstrip("-* ").strip() for ln in chunk.splitlines()]
            free = [ln for ln in free if ln and not re.match(r"^(理由|影響)\s*[:：]", ln)]
            what = " ".join(free)
        affects = RE_REF_ID.findall(tail) + RE_REF_ID.findall(pick("影響"))
        by = re.search(r"\bby\s+([A-Za-z]+)", tail)
        out.append(Decision(m.group(1), m.group(2), m.group(3), what, pick("理由"),
                            list(dict.fromkeys(affects)), by.group(1) if by else ""))
    return out


def _load_decisions(project: Project) -> None:
    path = project.root / "plan" / "decisions.md"
    if path.is_file():
        project.decisions = parse_decisions(read_text(path))


def _load_endings(project: Project) -> None:
    path = project.root / "canon" / "endings.log"
    if not path.is_file():
        return
    for lineno, line in enumerate(read_text(path).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = RE_ENDING_LINE.match(line)
        if m:
            project.endings.append((int(m.group(1)), m.group(2), m.group(3)))
        else:
            project.add("INFO", "I05", f"{project.rel(path)}:{lineno}",
                        "形式外の行。「ch001 | 頭: 型 | 末: 型」で書く")


def _load_manuscripts(project: Project) -> None:
    ms_dir = project.root / "manuscript"
    if not ms_dir.is_dir():
        return
    for path in sorted(ms_dir.glob("ch*.md")):
        m = RE_CHAPTER_FILE.match(path.stem)
        if not m:
            continue
        number = int(m.group(1))
        project.manuscripts[number] = path
        project.manuscript_chars[number] = body_char_count(read_text(path))


def load_project(root) -> Project:
    """プロジェクト一式を読む。読めないファイルがあっても、読めた分で先へ進む。"""
    require_tomllib()
    root = Path(root).resolve()
    novel_path = root / "novel.toml"
    if not novel_path.is_file():
        raise SystemExit(
            f"実行エラー: {novel_path.as_posix()} が無い。\n"
            "  --project には novel.toml のあるディレクトリを渡す。"
            "新規なら init_project.py で作る。"
        )
    project = Project(root=root)
    project.novel = _load_toml(project, novel_path) or {}
    for name, attr in (("plot/foreshadowing.toml", "foreshadowing"), ("style/glossary.toml", "glossary")):
        path = root / name
        if not path.is_file():
            continue
        data = _load_toml(project, path)
        if data is None:
            continue
        if attr == "foreshadowing":
            items = data.get("items", [])
            project.foreshadowing = items if isinstance(items, list) else []
            if not isinstance(items, list):
                project.add("FAIL", "E03", path, "items は [[items]] の配列で書く")
        else:
            project.glossary = data
    _load_beats(project)
    _load_characters(project)
    _load_locations(project)
    _load_facts(project)
    _load_decisions(project)
    _load_endings(project)
    _load_manuscripts(project)
    return project


def foreshadowing_by_id(project: Project) -> dict:
    return {it.get("id"): it for it in project.foreshadowing if isinstance(it, dict) and it.get("id")}


def last_touch_chapter(project: Project, item: dict, before: int) -> int:
    """その伏線に最後に触れた章（before より前）。

    台帳には「張った章」しか無いので、触れ直しは章ビートの plant / touch から逆算する。
    本文のある章だけを数える（ビートに書いただけでは読者はまだ触れていない）。
    """
    last = item.get("planted_ch") if isinstance(item.get("planted_ch"), int) else 0
    fid = item.get("id")
    for ch, beat in project.beats.items():
        if ch >= before or project.manuscript_chars.get(ch, 0) <= 0:
            continue
        touched = list(beat.get("plant") or []) + list(beat.get("touch") or [])
        if fid in touched:
            last = max(last, ch)
    return last


# --------------------------------------------------------------------------
# 検査
# --------------------------------------------------------------------------
def _ids_hint(existing) -> str:
    """参照切れのとき「何が使えるか」まで言う。"""
    ids = sorted(existing)
    if not ids:
        return "まだ 1 件も登録が無い"
    shown = ", ".join(ids[:12]) + (" …" if len(ids) > 12 else "")
    return f"ある ID: {shown}"


def _check_enum(project, where, key, value, allowed) -> None:
    if value is not None and value not in allowed:
        project.add("FAIL", "E03", where, f"{key} = {value!r} は使えない。使える値: {' | '.join(allowed)}")


def check_novel(project: Project) -> None:
    """novel.toml: lint とレビュー役がここを読んで契約を切り替えるので、列挙値の誤りは FAIL。"""
    where = "novel.toml"
    if where in project.broken_files:
        return
    work = project.novel.get("work")
    if not isinstance(work, dict):
        project.add("FAIL", "E02", where, "[work] が無い。title / profile / length を書く")
        return
    for key in ("title", "profile", "length"):
        if key not in work:
            project.add("FAIL", "E02", where, f"[work] に {key} が無い")
    if isinstance(work.get("title"), str) and not work["title"].strip():
        project.add("WARN", "W07", where, "title が未記入")
    _check_enum(project, where, "work.profile", work.get("profile"), PROFILES)
    _check_enum(project, where, "work.length", work.get("length"), LENGTHS)
    _check_enum(project, where, "work.medium", work.get("medium"), MEDIUMS)
    target = work.get("chapter_target_chars")
    if target is not None and (not isinstance(target, int) or isinstance(target, bool) or target <= 0):
        project.add("FAIL", "E03", where, "work.chapter_target_chars は正の整数にする")
    contract = project.novel.get("contract", {})
    if isinstance(contract, dict):
        _check_enum(project, where, "contract.chapter_hook", contract.get("chapter_hook"), CHAPTER_HOOKS)
        _check_enum(project, where, "contract.emotion_naming", contract.get("emotion_naming"), EMOTION_NAMING)
        if "keep" in contract and not isinstance(contract["keep"], list):
            project.add("FAIL", "E03", where, "contract.keep は文字列の配列にする")
        if not str(contract.get("promise", "")).strip():
            project.add("WARN", "W07", where, "contract.promise（読者に約束する快楽）が未記入")
    fmt = project.novel.get("format", {})
    if isinstance(fmt, dict):
        _check_enum(project, where, "format.indent", fmt.get("indent"), INDENTS)


def _check_char_refs(project, where, key, values) -> None:
    """ID の形（C01）をした値だけを照合する。「観光客」のような名無しの端役は自由記述として通す。"""
    for value in values:
        if isinstance(value, str) and RE_CHAR_ID.match(value) and value not in project.characters:
            project.add("FAIL", "E04", where,
                        f"{key} の {value} は bible/characters/ に無い。{_ids_hint(project.characters)}")


def _check_loc_refs(project, where, key, values) -> None:
    for value in values:
        if isinstance(value, str) and RE_LOC_ID.match(value) and value not in project.locations:
            project.add("FAIL", "E04", where,
                        f"{key} の {value} は bible/world.md の「場所」に無い。{_ids_hint(project.locations)}")


def _as_list(project, where, key, value) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        project.add("FAIL", "E03", where, f"{key} は配列にする（例: [\"C01\"]）")
        return []
    return value


def _blank(value) -> bool:
    return value is None or (isinstance(value, (str, list)) and len(value) == 0) or \
        (isinstance(value, str) and not value.strip())


def check_beats(project: Project, next_ch: int) -> None:
    fore = foreshadowing_by_id(project)
    seen_numbers = {}
    for ch in sorted(project.beats):
        beat, path = project.beats[ch], project.beat_paths[ch]
        where = project.rel(path)

        # 必須キー。参照の起点になるキーが無いと、パックも他の検査も組めない
        for key in ("chapter", "title", "pov", "characters", "intent"):
            if key not in beat:
                project.add("FAIL", "E02", where, f"必須キー {key} が無い")
        number = beat.get("chapter")
        if number is not None:
            if not isinstance(number, int) or isinstance(number, bool):
                project.add("FAIL", "E03", where, "chapter は整数にする")
            else:
                if number != ch:
                    project.add("FAIL", "E05", where, f"chapter = {number} がファイル名（{chapter_id(ch)}）と合わない")
                if number in seen_numbers:
                    project.add("FAIL", "E05", where, f"chapter = {number} が {seen_numbers[number]} と重複している")
                seen_numbers.setdefault(number, where)

        intensity = beat.get("intensity")
        if intensity is not None and (not isinstance(intensity, int) or isinstance(intensity, bool)
                                      or not 0 <= intensity <= 100):
            project.add("FAIL", "E03", where, "intensity は 0〜100 の整数にする")

        # ID 参照
        cast = _as_list(project, where, "characters", beat.get("characters"))
        _check_char_refs(project, where, "characters", cast)
        if isinstance(beat.get("pov"), str):
            _check_char_refs(project, where, "pov", [beat["pov"]])
        _check_loc_refs(project, where, "locations", _as_list(project, where, "locations", beat.get("locations")))
        for key in ("plant", "touch", "payoff"):
            for fid in _as_list(project, where, key, beat.get(key)):
                if isinstance(fid, str) and fid not in fore:
                    project.add("FAIL", "E04", where,
                                f"{key} の {fid} は plot/foreshadowing.toml に無い。{_ids_hint(fore)}")

        scenes = beat.get("scenes", [])
        if not isinstance(scenes, list):
            project.add("FAIL", "E03", where, "scenes は [[scenes]] の配列で書く")
            scenes = []
        for i, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                continue
            s_cast = _as_list(project, where, f"scenes[{i}].characters", scene.get("characters"))
            _check_char_refs(project, where, f"scenes[{i}].characters", s_cast)
            if isinstance(scene.get("pov"), str):
                _check_char_refs(project, where, f"scenes[{i}].pov", [scene["pov"]])
            if isinstance(scene.get("location"), str):
                _check_loc_refs(project, where, f"scenes[{i}].location", [scene["location"]])
            # 場面にだけ出る人物は、章の characters に足し忘れていることが多い
            extra = [c for c in s_cast if isinstance(c, str) and RE_CHAR_ID.match(c) and c not in cast]
            if extra:
                project.add("WARN", "W07", where,
                            f"scenes[{i}] の {', '.join(extra)} が章の characters に無い（足し忘れでなければそのままでよい）")

        # 未記入は、これから書く章だけ知らせる。書き終えた章の空欄を蒸し返しても直す意味が薄い
        if ch >= next_ch:
            blanks = [k for k in ("title", "pov", "characters", "intent", "reader_should", "ending_type")
                      if _blank(beat.get(k))]
            if "intensity" not in beat:
                blanks.append("intensity")
            if not scenes:
                blanks.append("scenes")
            for i, scene in enumerate(scenes, 1):
                if isinstance(scene, dict):
                    blanks += [f"scenes[{i}].{k}" for k in ("goal", "conflict", "change", "anchor", "knowledge")
                               if _blank(scene.get(k))]
            if blanks:
                shown = ", ".join(blanks[:8]) + (f" ほか {len(blanks) - 8}" if len(blanks) > 8 else "")
                project.add("WARN", "W07", where, f"未記入: {shown}")

    # 章の欠番。ビートは 1 から連番のはず
    numbers = sorted(project.beat_paths)
    if numbers:
        missing = [n for n in range(1, numbers[-1] + 1) if n not in project.beat_paths]
        if missing:
            project.add("WARN", "W08", "plot/beats/",
                        "章ビートの欠番: " + ", ".join(chapter_id(n) for n in missing[:10]))
    for ch in sorted(project.manuscripts):
        if ch not in project.beat_paths:
            project.add("WARN", "W08", project.rel(project.manuscripts[ch]), "本文はあるが章ビートが無い")


def check_intensity_flat(project: Project) -> None:
    """3 章続けて強度がほぼ同じなら知らせる。全章が同じ熱量になるのは LLM 長編の癖。"""
    chapters = [ch for ch in sorted(project.beats) if isinstance(project.beats[ch].get("intensity"), int)]
    for a, b, c in zip(chapters, chapters[1:], chapters[2:]):
        if c - a != 2:
            continue
        values = [project.beats[x]["intensity"] for x in (a, b, c)]
        if max(values) - min(values) <= 5 and any(values):
            project.add("INFO", "I04", "plot/beats/",
                        f"{chapter_id(a)}–{chapter_id(c)} の intensity が {values} でほぼ平坦。谷か山を 1 つ作れないか")


def check_foreshadowing(project: Project, next_ch: int) -> None:
    where = "plot/foreshadowing.toml"
    seen = set()
    last_written = project.last_written
    next_beat = project.beats.get(next_ch, {})
    in_next_beat = set()
    for key in ("plant", "touch", "payoff"):
        in_next_beat.update(x for x in (next_beat.get(key) or []) if isinstance(x, str))

    for idx, item in enumerate(project.foreshadowing, 1):
        if not isinstance(item, dict):
            continue
        fid = item.get("id")
        label = fid or f"items[{idx}]"
        for key in ("id", "content", "status"):
            if _blank(item.get(key)):
                project.add("FAIL", "E02", where, f"{label}: 必須キー {key} が無い（または空）")
        if isinstance(fid, str):
            if not RE_FORE_ID.match(fid):
                project.add("FAIL", "E03", where, f"{fid}: 伏線 ID は F001 の形にする")
            if fid in seen:
                project.add("FAIL", "E06", where, f"{fid} が重複している")
            seen.add(fid)
        status = item.get("status")
        _check_enum(project, where, f"{label}.status", status, F_STATUSES)
        for key in ("planted_ch", "payoff_plan_ch", "payoff_ch", "max_gap"):
            value = item.get(key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                project.add("FAIL", "E03", where, f"{label}.{key} は 0 以上の整数にする")
        if status not in F_STATUSES:
            continue

        planted = item.get("planted_ch") if isinstance(item.get("planted_ch"), int) else 0
        plan = item.get("payoff_plan_ch") if isinstance(item.get("payoff_plan_ch"), int) else 0
        paid = item.get("payoff_ch") if isinstance(item.get("payoff_ch"), int) else 0
        gap = item.get("max_gap") if isinstance(item.get("max_gap"), int) else 0

        # 状態と章番号の食い違い。台帳の更新漏れはここに出る
        if status in ("planted", "reminded", "paid") and not planted:
            project.add("WARN", "W03", where, f"{label}: status = {status} なのに planted_ch が無い")
        if status == "planned" and planted and planted <= last_written:
            project.add("WARN", "W03", where,
                        f"{label}: {chapter_id(planted)} は本文があるのに status が planned のまま。張ったなら planted にする")
        if status == "paid" and not paid:
            project.add("WARN", "W03", where, f"{label}: status = paid なのに payoff_ch が無い")
        if status == "dropped" and _blank(item.get("note")):
            project.add("WARN", "W03", where, f"{label}: dropped にした理由を note に残す（回収しないと決めたこと自体が情報）")
        if planted and plan and planted > plan:
            project.add("WARN", "W03", where, f"{label}: planted_ch（{planted}）が payoff_plan_ch（{plan}）より後になっている")

        if status not in F_OPEN:
            continue
        # 期限。回収予定の章をもう書き終えているのに未回収
        if not plan:
            project.add("WARN", "W01", where, f"{label}: payoff_plan_ch が無い。回収予定の無い伏線は放置されやすい")
        elif plan < next_ch:
            project.add("WARN", "W01", where,
                        f"{label}: 回収予定の {chapter_id(plan)} を過ぎて未回収。回収するか、予定を動かすか、dropped にする")
        elif plan == next_ch and next_beat and fid not in (next_beat.get("payoff") or []):
            project.add("WARN", "W01", where,
                        f"{label}: {chapter_id(next_ch)} が回収予定なのに、その章ビートの payoff に入っていない")
        # 放置。読者が忘れる前に触れ直す
        if status != "planned" and gap and fid not in in_next_beat:
            last = last_touch_chapter(project, item, next_ch)
            if last and next_ch - last > gap:
                project.add("WARN", "W02", where,
                            f"{label}: 最後に触れたのは {chapter_id(last)}。max_gap {gap} を超えた。"
                            f"{chapter_id(next_ch)} で触れ直すか max_gap を見直す")

    # 書き終えた章のビートと台帳の突き合わせ
    fore = foreshadowing_by_id(project)
    for ch in sorted(project.beats):
        if project.manuscript_chars.get(ch, 0) <= 0:
            continue
        beat = project.beats[ch]
        for fid in beat.get("plant") or []:
            if fid in fore and fore[fid].get("status") == "planned":
                project.add("WARN", "W03", where, f"{fid}: {chapter_id(ch)} で張ったはずだが status が planned のまま")
        for fid in beat.get("payoff") or []:
            if fid in fore and fore[fid].get("status") in F_OPEN:
                project.add("WARN", "W03", where,
                            f"{fid}: {chapter_id(ch)} で回収したはずだが status が {fore[fid].get('status')} のまま")


def check_glossary(project: Project) -> None:
    where = "style/glossary.toml"
    g = project.glossary
    if not g:
        return
    for i, term in enumerate(_as_list(project, where, "terms", g.get("terms")), 1):
        if not isinstance(term, dict):
            continue
        if _blank(term.get("canonical")):
            project.add("FAIL", "E02", where, f"terms[{i}]: canonical（本文で使う表記）が無い")
        variants = term.get("variants", [])
        if not isinstance(variants, list):
            project.add("FAIL", "E03", where, f"terms[{i}].variants は配列にする")
        elif term.get("canonical") in variants:
            project.add("WARN", "W07", where, f"terms[{i}]: variants に canonical と同じ表記が入っている")
    for i, row in enumerate(_as_list(project, where, "address", g.get("address")), 1):
        if not isinstance(row, dict):
            continue
        for key in ("from", "to", "call"):
            if _blank(row.get(key)):
                project.add("FAIL", "E02", where, f"address[{i}]: {key} が無い")
        _check_char_refs(project, where, f"address[{i}]", [row.get("from"), row.get("to")])
    cons = g.get("constraints", {})
    if not isinstance(cons, dict):
        project.add("FAIL", "E03", where, "constraints は [constraints] の表で書く")
        return
    for key in ("forbidden_words", "absent_concepts"):
        _as_list(project, where, f"constraints.{key}", cons.get(key))
    for i, row in enumerate(_as_list(project, where, "constraints.unknown_to", cons.get("unknown_to")), 1):
        if not isinstance(row, dict):
            continue
        if _blank(row.get("character")):
            project.add("FAIL", "E02", where, f"constraints.unknown_to[{i}]: character が無い")
        else:
            _check_char_refs(project, where, f"constraints.unknown_to[{i}].character", [row["character"]])
        _as_list(project, where, f"constraints.unknown_to[{i}].items", row.get("items"))


def check_facts(project: Project) -> None:
    where = "canon/facts.jsonl"
    seen, all_ids = {}, {row.get("id") for row in project.facts}
    proposed = []
    for row in project.facts:
        at = f"{where}:{row['_line']}"
        for key in ("id", "ch", "fact", "status"):
            if _blank(row.get(key)):
                project.add("FAIL", "E02", at, f"必須キー {key} が無い")
        if "ch" in row and (not isinstance(row["ch"], int) or isinstance(row["ch"], bool)):
            project.add("FAIL", "E03", at, "ch は整数にする")
        _check_enum(project, at, "status", row.get("status"), FACT_STATUSES)
        fid = row.get("id")
        if fid in seen:
            project.add("FAIL", "E06", at, f"id {fid} が {seen[fid]} 行目と重複。訂正は上書きでなく supersedes つきの新しい行で")
        elif fid:
            seen[fid] = row["_line"]
        _check_char_refs(project, at, "chars", _as_list(project, at, "chars", row.get("chars")))
        sup = row.get("supersedes")
        if sup and sup not in all_ids:
            project.add("WARN", "W11", at, f"supersedes の {sup} が見つからない")
        if row.get("status") == "proposed":
            proposed.append(row)

    if not proposed:
        return
    # proposed は LLM が書き足しただけの事実。残っていること自体は正常だが、溜めると次章が思い込みの上に建つ
    chapters = sorted({r["ch"] for r in proposed if isinstance(r.get("ch"), int)})
    span = ", ".join(chapter_id(c) for c in chapters[:6]) + (" …" if len(chapters) > 6 else "")
    project.add("INFO", "I01", where, f"proposed が {len(proposed)} 件残っている（{span}）。ユーザーの承認を得たものだけ confirmed にする")
    stale = [r for r in proposed if isinstance(r.get("ch"), int) and r["ch"] <= project.last_written - PROPOSED_LAG_CHAPTERS]
    if stale:
        project.add("WARN", "W06", where,
                    f"{PROPOSED_LAG_CHAPTERS} 章以上前の proposed が {len(stale)} 件ある。要約と一緒にユーザーへ示して確定を頼む")


def check_canon_files(project: Project) -> None:
    """本文のある章に、要約と章末状態が揃っているか。欠けると次章のパックが前提を失う。"""
    for ch, path in sorted(project.manuscripts.items()):
        count = project.manuscript_chars.get(ch, 0)
        if count == 0:
            project.add("WARN", "W04", path, "本文が空。保存事故かもしれない（原稿を保存し直す）")
            continue
        if count < MIN_MANUSCRIPT_CHARS:
            project.add("WARN", "W04", path, f"本文が {count} 字しかない。保存事故でないか確かめる")
        for sub, label in (("summaries", "章要約"), ("state", "章末の状態スナップショット")):
            note = project.root / "canon" / sub / f"{chapter_id(ch)}.md"
            if not note.is_file():
                project.add("WARN", "W04", f"canon/{sub}/{chapter_id(ch)}.md", f"{label}が無い。canon を更新してから次章へ進む")
            elif body_char_count(strip_html_comments(read_text(note))) < MIN_CANON_NOTE_CHARS:
                project.add("WARN", "W04", note, f"{label}が雛形のまま（中身が無い）")
    written = sorted(ch for ch, n in project.manuscript_chars.items() if n > 0)
    if written:
        missing = [n for n in range(1, written[-1] + 1) if n not in project.manuscripts]
        if missing:
            project.add("WARN", "W08", "manuscript/", "本文の欠番: " + ", ".join(chapter_id(n) for n in missing[:10]))


def check_characters(project: Project) -> None:
    for cid, ch in sorted(project.characters.items()):
        # 名前が仮のまま本文に入ると、後からの置換で表記ゆれと呼称ゆれが残る
        if not ch.name or RE_UNDECIDED_NAME.search(ch.name) or RE_UNDECIDED_NAME.search(ch.path.stem):
            project.add("WARN", "W05", ch.path, f"{cid} の名前が未定のまま。プロットと本文に入る前に決める")
    # 似た名前の検出。LLM は頭の字・音が同じ名前を並べがちで、読者が取り違える
    by_head = {}
    for cid, ch in project.characters.items():
        if not ch.name or RE_UNDECIDED_NAME.search(ch.name):
            continue
        parts = re.split(r"[\s　]+", ch.name.strip())
        keys = {("名前の頭の字", parts[0][0])}
        if len(parts) > 1 and parts[-1]:
            keys.add(("下の名前の頭の字", parts[-1][0]))
        if ch.reading:
            keys.add(("読みの頭の音", ch.reading.strip()[0]))
        for key in keys:
            by_head.setdefault(key, []).append(f"{cid} {ch.name}")
    for (kind, head), members in sorted(by_head.items()):
        if len(members) >= 2:
            project.add("INFO", "I02", "bible/characters/",
                        f"{kind}「{head}」が同じ: {' / '.join(sorted(members))}（家族など意図したものならそのままでよい）")


def check_decisions(project: Project) -> None:
    where = "plan/decisions.md"
    path = project.root / where
    if not path.is_file():
        return
    body = strip_html_comments(read_text(path))
    heads = re.findall(r"^##\s+(D\S*.*)$", body, re.M)
    if len(heads) != len(project.decisions):
        project.add("WARN", "W11", where,
                    "見出しの形が崩れた項目がある。「## D001 2026-09-02 reject」（種別は change | reject | note）で書く")
    seen = set()
    fore = foreshadowing_by_id(project)
    for d in project.decisions:
        if d.id in seen:
            project.add("WARN", "W11", where, f"{d.id} が重複している。決定ログは追記専用で、番号は使い回さない")
        seen.add(d.id)
        # 影響先の書き間違いでは執筆を止めない（WARN 止まり）。ただしパックに載らなくなるので知らせる
        for ref in d.affects:
            known = (ref in project.characters or ref in project.locations or ref in fore
                     or (ref.startswith("ch") and int(ref[2:]) in project.beat_paths))
            if not known:
                project.add("WARN", "W11", where, f"{d.id}: 影響の {ref} が見つからない（このままだとパックに載らない）")


def check_lint_json(project: Project) -> None:
    """style/lint.json: 手書きの上書き（overrides）には理由を求める。理由の無い上書きは後で誰も外せない。"""
    path = project.root / "style" / "lint.json"
    if not path.is_file():
        return
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        project.add("FAIL", "E01", path, f"JSON の構文エラー: {exc.msg}（{exc.lineno} 行目）")
        return
    overrides = data.get("overrides", {}) if isinstance(data, dict) else {}
    if isinstance(overrides, dict):
        for rule, value in overrides.items():
            if not isinstance(value, dict) or _blank(value.get("reason")):
                project.add("WARN", "W09", path, f"overrides.{rule} に reason が無い。なぜ破るのかを 1 句書く")


def check_endings(project: Project, next_ch: int) -> None:
    """章頭・章末の型が 3 章続けて同じなら知らせる（型の一致は文字列で見るだけの粗い検査）。"""
    rows = sorted(project.endings)
    for label, idx in (("章頭", 1), ("章末", 2)):
        last3 = [r[idx] for r in rows[-3:]]
        if len(last3) == 3 and last3[0] and len(set(last3)) == 1:
            project.add("WARN", "W10", "canon/endings.log", f"直近 3 章の{label}の型がすべて「{last3[0]}」")
    beat = project.beats.get(next_ch)
    if beat and len(rows) >= 2:
        planned = str(beat.get("ending_type", "")).strip()
        if planned and all(r[2] == planned for r in rows[-2:]):
            project.add("WARN", "W10", project.rel(project.beat_paths[next_ch]),
                        f"ending_type「{planned}」は直近 2 章の章末と同じ型。別の切り方を検討する")
    logged = {r[0] for r in rows}
    unlogged = [ch for ch, n in sorted(project.manuscript_chars.items()) if n > 0 and ch not in logged]
    if unlogged and rows:
        project.add("INFO", "I05", "canon/endings.log",
                    "型の記録が無い章: " + ", ".join(chapter_id(c) for c in unlogged[:8]))


def check_status(project: Project) -> None:
    path = project.root / "STATUS.md"
    if not path.is_file():
        project.add("WARN", "W04", "STATUS.md", "STATUS.md が無い。再開点が分からなくなる")
        return
    lines = strip_html_comments(read_text(path)).splitlines()
    if len(lines) > STATUS_MAX_LINES:
        project.add("INFO", "I03", "STATUS.md", f"{len(lines)} 行ある。{STATUS_MAX_LINES} 行以内に刈り込む（履歴は decisions.md へ）")


def run_checks(project: Project, next_ch: int) -> list:
    check_novel(project)
    check_beats(project, next_ch)
    check_intensity_flat(project)
    check_foreshadowing(project, next_ch)
    check_glossary(project)
    check_facts(project)
    check_canon_files(project)
    check_characters(project)
    check_decisions(project)
    check_lint_json(project)
    check_endings(project, next_ch)
    check_status(project)
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    return sorted(project.findings, key=lambda f: (order.get(f.level, 9), f.code, f.where))


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------
def render_text(project: Project, findings: list, next_ch: int, limit: int) -> str:
    proposed = sum(1 for r in project.facts if r.get("status") == "proposed")
    open_f = sum(1 for it in project.foreshadowing if isinstance(it, dict) and it.get("status") in F_OPEN)
    written = sum(1 for n in project.manuscript_chars.values() if n > 0)
    lines = [
        f"台帳検査: {project.root.name}  次に書く章 {chapter_id(next_ch)} / 本文のある章 {written}"
        f" / 章ビート {len(project.beat_paths)} / 人物 {len(project.characters)}"
        f" / 伏線 {len(project.foreshadowing)}（未回収 {open_f}）/ 既出事実 {len(project.facts)}（proposed {proposed}）"
    ]
    for level, cap in (("FAIL", limit), ("WARN", limit), ("INFO", max(3, limit // 3))):
        rows = [f for f in findings if f.level == level]
        if not rows:
            continue
        lines.append(f"{level} {len(rows)}")
        for f in rows[:cap]:
            lines.append(f"  {f.code} {f.where}: {f.message}")
        if len(rows) > cap:
            lines.append(f"  … ほか {len(rows) - cap} 件（全件は --json）")
    counts = {lv: sum(1 for f in findings if f.level == lv) for lv in ("FAIL", "WARN", "INFO")}
    if not findings:
        lines.append("問題は見つからなかった。")
    lines.append(f"結果: FAIL {counts['FAIL']} / WARN {counts['WARN']} / INFO {counts['INFO']}"
                 "（FAIL は直してから build_context.py へ。WARN は直すか、理由があって残すかを決める）")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ledger_lint.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "長編プロジェクトの台帳（novel.toml・章ビート・伏線台帳・用語表・既出事実・要約・状態）の整合を検査する。\n"
            "章を書く前（build_context.py の前）と、canon を更新した後に実行する。本文の出来は見ない。"
        ),
        epilog=(
            "検査する内容:\n"
            "  FAIL  E01 構文（TOML / JSON / JSONL） E02 必須キー  E03 型・列挙値\n"
            "        E04 ID 参照切れ（人物 C01 / 場所 L01 / 伏線 F001） E05 章番号の不一致・重複  E06 ID 重複\n"
            "  WARN  W01 伏線の期限（payoff_plan_ch を過ぎて未回収） W02 伏線の放置（max_gap 超過）\n"
            "        W03 伏線の状態と章の食い違い  W04 要約・章末状態・本文の欠落（空保存を含む）\n"
            "        W05 人物名が TBD / 未定 /（仮） W06 古い proposed の滞留  W07 未記入\n"
            "        W08 章の欠番  W09 lint.json の上書きに理由なし  W10 章頭・章末の型の連続  W11 決定ログ等の参照\n"
            "  INFO  I01 proposed の残数  I02 似た名前  I03 STATUS.md が長い  I04 強度が平坦  I05 endings.log\n\n"
            "例:\n"
            "  python ledger_lint.py --project my-novel\n"
            "  python ledger_lint.py --project my-novel --chapter 12      # ch012 をこれから書く前提で期限を見る\n"
            "  python ledger_lint.py --project my-novel --json > work/ledger.json\n\n"
            "終了コード: 0 = FAIL なし / 1 = FAIL あり / 2 = 実行エラー。点数は出さない。"
        ),
    )
    p.add_argument("--project", "-p", default=".", metavar="DIR", help="novel.toml のあるディレクトリ（既定: カレント）")
    p.add_argument("--chapter", "-c", type=int, metavar="N",
                   help="これから書く章の番号。伏線の期限と未記入の判定に使う（既定: 本文のある最後の章 + 1）")
    p.add_argument("--json", action="store_true", help="全件を JSON で出す（既定は要約）")
    p.add_argument("--limit", type=int, default=12, metavar="N", help="要約で FAIL / WARN それぞれ何件まで見せるか（既定 12）")
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        project = load_project(args.project)
    except SystemExit as exc:  # 実行エラーは案内文つきで 2
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise
    next_ch = args.chapter if args.chapter and args.chapter > 0 else project.last_written + 1
    findings = run_checks(project, next_ch)
    if args.json:
        payload = {
            "project": project.root.as_posix(),
            "next_chapter": next_ch,
            "last_written": project.last_written,
            "counts": {lv: sum(1 for f in findings if f.level == lv) for lv in ("FAIL", "WARN", "INFO")},
            "findings": [asdict(f) for f in findings],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(project, findings, next_ch, max(1, args.limit)))
    return 1 if any(f.level == "FAIL" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
