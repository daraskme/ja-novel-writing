#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""init_project.py — 小説プロジェクトの雛形を作る。

長編（serial）は仕様のディレクトリ一式、短編（short）は notes.md 1 枚を作る。
雛形は assets/templates/ にあり、記入例はコメントに入っている（そのまま複製しても
見本作の内容が台帳やコンテキストパックに混ざらないようにするため）。

既存のファイルは上書きしない。novel.toml（short では notes.md）が既にあれば、
別のプロジェクトを壊さないように何もせず止まる。

終了コード: 0 = 成功 / 2 = 実行エラー
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"

PROFILES = ("bungei", "entertainment", "web")
LENGTHS = ("flash", "short", "long")
MEDIUMS = ("narou", "kakuyomu", "alphapolis", "pixiv", "print", "none")

# プロファイル別の初期値。web は各話末に開いた問いを置くのが様式、bungei は余韻で閉じてよい。
# ここは「最初の置き値」で、作品側の novel.toml を書き換えればそちらが勝つ。
PROFILE_DEFAULTS = {
    "bungei": {"chapter_hook": "off", "emotion_naming": "restrained", "chapter_chars": 6000},
    "entertainment": {"chapter_hook": "optional", "emotion_naming": "balanced", "chapter_chars": 5000},
    "web": {"chapter_hook": "required", "emotion_naming": "balanced", "chapter_chars": 4000},
}

# serial の雛形: (雛形ファイル, プロジェクト内の置き場所)
SERIAL_FILES = (
    ("novel.toml", "novel.toml"),
    ("STATUS.md", "STATUS.md"),
    ("logline.md", "plan/logline.md"),
    ("structure.md", "plan/structure.md"),
    ("decisions.md", "plan/decisions.md"),
    ("world.md", "bible/world.md"),
    ("style-sheet.md", "style/style-sheet.md"),
    ("glossary.toml", "style/glossary.toml"),
    ("foreshadowing.toml", "plot/foreshadowing.toml"),
    ("beat.toml", "plot/beats/ch001.toml"),
)
SERIAL_DIRS = (
    "plan", "bible/characters", "style", "plot/beats",
    "canon/summaries", "canon/state", "manuscript", "work", "reviews",
)
ENDINGS_LOG_HEADER = (
    "# 章頭・章末の型の記録。1 章 1 行で追記する（同じ型を続けないための記録）\n"
    "# 書式: ch001 | 頭: 作業の途中から入る | 末: 行為の途中で切る\n"
)


class InitError(Exception):
    """利用者に案内文を見せて終了コード 2 で止めるためのエラー。"""


def chapter_id(n: int) -> str:
    return f"ch{n:03d}"


def toml_escape(value: str) -> str:
    """TOML の基本文字列に入れるためのエスケープ（題名に引用符や \\ が入っても構文を壊さない）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_template(name: str, values: dict) -> str:
    """雛形を読み、{{NAME}} を置き換える。知らないプレースホルダは触らない。"""
    path = TEMPLATES_DIR / name
    if not path.is_file():
        available = ", ".join(sorted(p.name for p in TEMPLATES_DIR.glob("*"))) or "（空）"
        raise InitError(f"雛形 {path.as_posix()} が無い。ある雛形: {available}")
    text = path.read_text(encoding="utf-8")
    escape = name.endswith(".toml")

    def repl(m):
        key = m.group(1)
        if key not in values:
            return m.group(0)
        value = str(values[key])
        return toml_escape(value) if escape else value

    return re.sub(r"\{\{([A-Z_]+)\}\}", repl, text)


def write_new(path: Path, text: str, created: list, kept: list, root: Path) -> None:
    """無ければ作る。あれば温存する（利用者が書いた内容を消さない）。"""
    rel = path.relative_to(root).as_posix()
    if path.exists():
        kept.append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    created.append(rel)


def ensure_gitignore(root: Path, created: list, kept: list) -> None:
    """work/ は生成物なので版管理から外す。既存の .gitignore には 1 行足すだけにする。"""
    path = root / ".gitignore"
    if not path.exists():
        write_new(path, "work/\n", created, kept, root)
        return
    text = path.read_text(encoding="utf-8-sig")
    if not re.search(r"^work/?\s*$", text, re.M):
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(("" if text.endswith("\n") or not text else "\n") + "work/\n")
        created.append(".gitignore（work/ を追記）")
    else:
        kept.append(".gitignore")


def base_values(args) -> dict:
    defaults = PROFILE_DEFAULTS[args.profile]
    return {
        "TITLE": args.title,
        "PROFILE": args.profile,
        "LENGTH": args.length or ("short" if args.mode == "short" else "long"),
        "MEDIUM": args.medium,
        "POV": args.pov,
        "TENSE": args.tense,
        "CHAPTER_CHARS": args.chapter_chars or defaults["chapter_chars"],
        "CHAPTER_HOOK": defaults["chapter_hook"],
        "EMOTION_NAMING": defaults["emotion_naming"],
        "DATE": datetime.date.today().isoformat(),
        "CHAPTER": 1,
        "CHAPTER_ID": chapter_id(1),
        "CHAPTER_TITLE": "",
    }


def init_serial(root: Path, args) -> tuple:
    if (root / "novel.toml").exists():
        raise InitError(
            f"{(root / 'novel.toml').as_posix()} が既にある。既存のプロジェクトを壊さないよう、何もせずに止めた。\n"
            "  続きから作業するなら STATUS.md を読む。章ビートを足すなら --add-chapter N、人物を足すなら --add-character C02 --name 名前。\n"
            "  別の作品を始めるなら、別のディレクトリを指定する。"
        )
    created, kept = [], []
    values = base_values(args)
    for sub in SERIAL_DIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    for template, dest in SERIAL_FILES:
        write_new(root / dest, render_template(template, values), created, kept, root)
    write_new(root / "canon" / "facts.jsonl", "", created, kept, root)
    write_new(root / "canon" / "endings.log", ENDINGS_LOG_HEADER, created, kept, root)
    ensure_gitignore(root, created, kept)
    return created, kept


def init_short(root: Path, args) -> tuple:
    if (root / "notes.md").exists():
        raise InitError(f"{(root / 'notes.md').as_posix()} が既にある。何もせずに止めた。別のディレクトリを指定する。")
    created, kept = [], []
    values = base_values(args)
    values["CHAPTER_ID"] = "（短編）"
    (root / "reviews").mkdir(parents=True, exist_ok=True)
    write_new(root / "notes.md", render_template("short-notes.md", values), created, kept, root)
    write_new(root / "revision-log.md", render_template("revision-log.md", values), created, kept, root)
    return created, kept


def require_project(root: Path) -> None:
    if not (root / "novel.toml").is_file():
        raise InitError(
            f"{root.as_posix()} に novel.toml が無い。--add-chapter / --add-character は既存の serial プロジェクトに対して使う。\n"
            "  新規なら、まず引数なしでプロジェクトを作る: python init_project.py DIR --title 題名"
        )


def add_chapter(root: Path, number: int, title: str) -> str:
    require_project(root)
    if number < 1:
        raise InitError("--add-chapter には 1 以上の章番号を渡す。")
    dest = root / "plot" / "beats" / f"{chapter_id(number)}.toml"
    if dest.exists():
        raise InitError(f"{dest.as_posix()} は既にある。上書きしない。")
    values = {"CHAPTER": number, "CHAPTER_ID": chapter_id(number), "CHAPTER_TITLE": title}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_template("beat.toml", values), encoding="utf-8", newline="\n")
    return dest.relative_to(root).as_posix()


def add_character(root: Path, char_id: str, name: str) -> str:
    require_project(root)
    if not re.fullmatch(r"C\d{2,}", char_id):
        raise InitError(f"人物 ID は C01 の形にする（渡された値: {char_id}）。")
    if not name.strip():
        raise InitError("--name に名前を渡す。未定のまま作らない（仮の名前は本文と台帳に残って表記ゆれの元になる）。")
    chars_dir = root / "bible" / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(p.name for p in chars_dir.glob(f"{char_id}[-_.]*")) + \
        sorted(p.name for p in chars_dir.glob(f"{char_id}.md"))
    if existing:
        raise InitError(f"{char_id} は既にある（{existing[0]}）。別の ID を使う。")
    # ファイル名に使えない文字と空白を除く（Windows のパスで壊れないように）
    safe = re.sub(r'[\\/:*?"<>|\s　]+', "", name)
    dest = chars_dir / f"{char_id}-{safe}.md"
    dest.write_text(render_template("character.md", {"CHAR_ID": char_id, "CHAR_NAME": name.strip()}),
                    encoding="utf-8", newline="\n")
    return dest.relative_to(root).as_posix()


def next_steps_serial(root: Path) -> str:
    here = Path(__file__).resolve().parent.as_posix()
    return "\n".join([
        "次の一手:",
        "  1. plan/logline.md と novel.toml の [contract] を埋める（仮置きでよい）",
        f"  2. 人物を足す: python \"{here}/init_project.py\" \"{root.as_posix()}\" --add-character C01 --name 名前",
        "  3. style/style-sheet.md、bible/world.md（常時前提と場所）、plot/foreshadowing.toml を埋める",
        "  4. plot/beats/ch001.toml を書く（次章以降は --add-chapter N）",
        f"  5. python \"{here}/ledger_lint.py\" --project \"{root.as_posix()}\" → build_context.py --chapter 1 → パックだけを読んで書く",
        "  章を書いた後に canon/ へ足す要約と状態の雛形: assets/templates/summary.md, state.md",
    ])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="init_project.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "小説プロジェクトの雛形を作る。既存のファイルは上書きしない。\n"
            "  serial（既定）: novel.toml / STATUS.md / plan / bible / style / plot / canon / manuscript / work / reviews\n"
            "  short         : notes.md と revision-log.md だけ\n"
            "雛形の「例:」は見本作のもので、すべてコメントになっている。値を書くときはコメントの外に書く。"
        ),
        epilog=(
            "例:\n"
            "  python init_project.py my-novel --title 索道日誌 --profile entertainment --medium kakuyomu\n"
            "  python init_project.py my-novel --add-character C01 --name 鵜殿ちづる\n"
            "  python init_project.py my-novel --add-chapter 2 --chapter-title 木曜の写真\n"
            "  python init_project.py my-short --mode short --title 木曜の弁当箱\n\n"
            "プロファイル別の置き値（novel.toml を書き換えればそちらが勝つ）:\n"
            "  bungei        chapter_hook=off       emotion_naming=restrained  1 章 6000 字\n"
            "  entertainment chapter_hook=optional  emotion_naming=balanced    1 章 5000 字\n"
            "  web           chapter_hook=required  emotion_naming=balanced    1 章 4000 字\n\n"
            "終了コード: 0 = 成功 / 2 = 実行エラー（novel.toml が既にある、など）"
        ),
    )
    p.add_argument("dir", metavar="DIR", help="プロジェクトのディレクトリ（無ければ作る）")
    p.add_argument("--mode", choices=("serial", "short"), default="serial", help="serial = 長編・連載一式（既定）/ short = notes.md 1 枚")
    p.add_argument("--title", default="", help="作品の題（後から novel.toml で変えられる）")
    p.add_argument("--profile", choices=PROFILES, default="entertainment", help="文体プロファイル（既定 entertainment）")
    p.add_argument("--length", choices=LENGTHS, help="長さの区分（既定: serial は long、short は short）")
    p.add_argument("--medium", choices=MEDIUMS, default="none", help="投稿先・媒体（既定 none）")
    p.add_argument("--pov", default="三人称一元", help="人称と視点（既定: 三人称一元）")
    p.add_argument("--tense", default="過去形基調", help="時制（既定: 過去形基調）")
    p.add_argument("--chapter-chars", type=int, metavar="N", help="1 章の目標字数（既定: プロファイル別）")
    g = p.add_argument_group("既存の serial プロジェクトに足す")
    g.add_argument("--add-chapter", type=int, metavar="N", help="plot/beats/chNNN.toml を雛形から作る")
    g.add_argument("--chapter-title", default="", help="--add-chapter で作る章の仮題")
    g.add_argument("--add-character", metavar="ID", help="bible/characters/ID-名前.md を雛形から作る（例: C02）")
    g.add_argument("--name", default="", help="--add-character で作る人物の名前")
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = Path(args.dir).resolve()
    try:
        if args.add_chapter is not None or args.add_character:
            if args.add_chapter is not None:
                print(f"作成: {add_chapter(root, args.add_chapter, args.chapter_title)}")
            if args.add_character:
                print(f"作成: {add_character(root, args.add_character, args.name)}")
            return 0
        root.mkdir(parents=True, exist_ok=True)
        created, kept = (init_short if args.mode == "short" else init_serial)(root, args)
    except InitError as exc:
        print(f"実行エラー: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"実行エラー: ファイルを書けなかった（{exc}）。パスと書き込み権限を確かめる。", file=sys.stderr)
        return 2

    print(f"プロジェクトを作った: {root.as_posix()}（{args.mode}）")
    print(f"  作成 {len(created)} 件: " + ", ".join(created[:12]) + (" …" if len(created) > 12 else ""))
    if kept:
        print(f"  既にあったので温存 {len(kept)} 件: " + ", ".join(kept[:8]) + (" …" if len(kept) > 8 else ""))
    if args.mode == "short":
        print("次の一手: notes.md を埋める（確認は最大 1 往復）→ 2〜3 場面ごとに分けて書く → count_chars.py で実測 → novel_lint.py")
    else:
        print(next_steps_serial(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
