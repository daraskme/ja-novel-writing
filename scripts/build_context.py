#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_context.py -- 1 章を書くのに要る情報だけを、決まった手順で 1 枚に束ねる（標準ライブラリのみ）。

なぜ要るか:
  執筆役に全資料を渡すと、声が平均化し、設定の説明が本文に漏れ、古い記述を拾う。
  章番号から毎回同じ束を機械的に作れば、読み漏らしが無く、レビュー役にも同じ前提を渡せる。

パックの中身（上ほど優先。予算を超えたら 10→7 の順に落とす。0〜6 は落とさない。落としたものは末尾に書く）:
   0 執筆条件（novel.toml のジャンル契約・人称・時制・目標字数、bible/world.md の「常時前提」）
   1 章ビート全文
   2 伏線（この章で張る / 触れる / 回収する、維持中は 1 行ずつ）
   3 登場人物シート（登場 ID のみ。「変化の記録」は書く章より前の項目だけ。補足行ごと落とす）と呼称表
     （glossary.toml の [[address]] に from_ch があれば、書く章より前に始まった最新の呼び方だけ）
   4 文体シート
   5 世界制約（禁止語彙・存在しない概念・その人物が知らないこと）と用語
   6 前章末の状態スナップショット
   7 前章本文の末尾（接続用）
   8 関連する既出事実（proposed は【未確定】つき。supersedes で訂正された旧事実は載せない）
   9 決定ログ（この章・登場人物・関連伏線に関わるもの＋直近）
  10 過去章の要約（古い章ほど短く）

使い方:
  python build_context.py --project . --chapter 3            work/ch003.pack.md を作る
  python build_context.py --project . --chapter 3 --check    パックが原典より古くないか確かめる（古ければ終了コード 1）

予算は見出しや注記を含む完成形で測る。落とせない 0〜6 だけで予算を超える場合と、文体シート・前章末の状態が無い場合は、欠けたパックを作らずに終了コード 2 で止まる
（承知のうえで進めるなら --allow-missing）。

終了コード: 0 = 成功（--check では最新）/ 1 = --check で古い・無い / 2 = 実行エラー
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger_lint import (chapter_id, foreshadowing_by_id, last_touch_chapter,  # noqa: E402
                         load_project, read_text, strip_html_comments)

HASH_PLACEHOLDER = "<!-- sources-sha256: " + "0" * 64 + " -->\n"
HASH_LINE = re.compile(r"<!-- sources-sha256: ([0-9a-f]{64}) -->")
WS = re.compile(r"[\s　]")


class PackError(Exception):
    """パックを作れない（作るべきでない）状況。終了コード 2。"""


def size(text: str) -> int:
    """日本語は 1 字 ≒ 1 トークンと粗く見積もる。"""
    return len(WS.sub("", text))


def clean_md(text: str) -> str:
    """雛形のコメント（例や書き方の注記）はパックに載せない。"""
    text = strip_html_comments(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def toml_block(path: Path) -> str:
    """TOML はコメント行と行末コメントを落として載せる（値だけが要る）。"""
    rows = []
    for line in read_text(path).splitlines():
        if line.strip().startswith("#"):
            continue
        rows.append(_strip_toml_comment(line).rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(rows)).strip()


def _strip_toml_comment(line: str) -> str:
    """文字列の外にある # 以降を落とす。雛形の「例:」が執筆役の文脈に混ざるのを防ぐ。"""
    quote, i = None, 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return line[:i]
        i += 1
    return line


def filled_items(text: str) -> int:
    """雛形のコメントを除いた本文のうち、実際に値が書かれた行の数。
    「- 人称:」のように見出し語だけの行、見出し（#）、空行は数えない。「- 人称: 一人称」は 1 と数える。"""
    n, in_table = 0, False
    for line in clean_md(text).splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            in_table = False
            continue
        if t.startswith("|"):
            # 表は、見出し行と区切り行（|---|）を除いたデータ行の、空でないセルを値として数える
            cells = [c.strip() for c in t.strip("|").split("|")]
            is_rule = all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells)
            if in_table and not is_rule:
                n += 1 if any(c for c in cells) else 0
            in_table = True
            continue
        in_table = False
        t = t.lstrip("-*・ 　").strip()
        if not t:
            continue                  # 記号だけの空の箇条書き
        if ":" in t or "：" in t:
            value = t.replace("：", ":").split(":", 1)[1].strip()
            n += 1 if value else 0
        else:
            n += 1
    return n


def world_premise(path: Path) -> str:
    """bible/world.md の「## 常時前提」の節だけを取り出す（雛形が約束している、パックに入る唯一の節）。"""
    if not path.is_file():
        return ""
    m = re.search(r"^##\s*常時前提\s*\n(.*?)(?=^##\s|\Z)", strip_html_comments(read_text(path)), re.S | re.M)
    return m.group(1).strip() if m else ""


CHANGE_ROW = re.compile(r"^\s*[-*]\s*ch(\d{1,4})\s*[〜~～\-－―]?")


def character_as_of(text: str, n: int) -> str:
    """人物シートの「## 変化の記録」から、書く章より先の行を落とす。
    第 3 章を書くときに第 9 章の変化が見えると、先取りした描写が混ざる。当該章で起きる変化は章ビートが渡す。"""
    out, in_changes, skipping, base_indent = [], False, False, 0
    for line in text.splitlines():
        if line.startswith("## "):
            in_changes, skipping = "変化" in line, False
        if in_changes and not line.startswith("## "):
            if line.lstrip().startswith("|"):
                raise PackError("人物シートの「変化の記録」に表がある。章ごとの絞り込みができないので、"
                                "「- ch005〜: 内容」の箇条書き（補足は字下げした行）に直す")
            indent = len(line) - len(line.lstrip(" \t　"))
            m = CHANGE_ROW.match(line)
            if m and (not skipping or indent <= base_indent):
                skipping, base_indent = int(m.group(1)) >= n, indent
            elif skipping and line.strip() and indent <= base_indent:
                skipping = False      # 章番号の無い同じ深さの行は、次の項目として扱う
            if skipping:
                continue              # 落とす項目の補足行・子項目（より深い字下げ、空行）も一緒に落とす
        out.append(line)
    return "\n".join(out)


def name_of(project, cid: str) -> str:
    ch = project.characters.get(cid)
    return f"{ch.name}（{cid}）" if ch else cid


def source_files(project, n: int) -> list:
    """鮮度の判定に使う原典。後続章の更新で古いと誤判定しないよう、N 章以前のものだけを見る。"""
    root = project.root
    files = [root / "novel.toml", root / "style" / "style-sheet.md", root / "style" / "glossary.toml",
             root / "plot" / "foreshadowing.toml", root / "plan" / "decisions.md", root / "canon" / "facts.jsonl",
             root / "bible" / "world.md"]
    files += sorted((root / "bible" / "characters").glob("*.md")) if (root / "bible" / "characters").is_dir() else []
    for ch in range(1, n + 1):
        files += [root / "plot" / "beats" / f"{chapter_id(ch)}.toml"]
        if ch < n:
            files += [root / "manuscript" / f"{chapter_id(ch)}.md", root / "canon" / "summaries" / f"{chapter_id(ch)}.md",
                      root / "canon" / "state" / f"{chapter_id(ch)}.md"]
    return [f for f in files if f.is_file()]


def sources_hash(project, n: int) -> str:
    h = hashlib.sha256()
    for f in source_files(project, n):
        h.update(f.relative_to(project.root).as_posix().encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()


def build(project, n: int, budget: int, tail_chars: int, allow_missing: bool = False) -> tuple:
    root = project.root
    beat = project.beats.get(n)
    if beat is None:
        have = ", ".join(chapter_id(c) for c in sorted(project.beats)) or "なし"
        raise PackError(f"plot/beats/{chapter_id(n)}.toml が無い。先に章ビートを書く（ある章ビート: {have}）")
    cast = list(dict.fromkeys(list(beat.get("characters", []) or []) +
                              [c for s in beat.get("scenes", []) or [] for c in (s.get("characters") or [])]))
    blocks = []   # (優先度, 見出し, 本文, 落とせるか)
    missing = []  # 落とせないのに存在しない資料

    # 0 執筆条件。ジャンル契約は汎用規則より上にあるので、パックだけを読む執筆役にも必ず渡す。落とさない
    work, contract = project.novel.get("work", {}) or {}, project.novel.get("contract", {}) or {}
    rows = []
    for label, val in (("プロファイル", work.get("profile")), ("人称と視点", work.get("pov")), ("時制", work.get("tense")),
                       ("この章の目標字数", work.get("chapter_target_chars")), ("読者に約束するもの", contract.get("promise")),
                       ("様式として残すもの", "、".join(contract.get("keep") or [])), ("章末に開いた問いを置くか", contract.get("chapter_hook")),
                       ("感情の名指し", contract.get("emotion_naming"))):
        if val not in (None, "", []):
            rows.append(f"- {label}: {val}")
    premise = world_premise(root / "bible" / "world.md")
    if premise:
        rows.append("- 世界の常時前提:\n" + "\n".join("  " + line for line in premise.splitlines()))
    blocks.append((0, "0 執筆条件（ジャンル契約と常時前提。汎用の書き方より、ここが優先）", "\n".join(rows) or "（novel.toml の [contract] が未記入）", False))

    blocks.append((1, f"1 章ビート {chapter_id(n)}", "```toml\n" + toml_block(project.beat_paths[n]) + "\n```", False))

    # 2 伏線。この章で扱うものは全文、ほかの未回収は 1 行。落とさない
    by_id = foreshadowing_by_id(project)
    rows = []
    for label, key in (("この章で張る", "plant"), ("この章で触れ直す", "touch"), ("この章で回収する", "payoff")):
        for fid in beat.get(key, []) or []:
            item = by_id.get(fid)
            if item:
                rows.append(f"- 【{label}】{fid}: {item.get('content', '')}"
                            + (f"（備考: {item['note']}）" if item.get("note") else "")
                            + (f" / 張った章: {item['planted_ch']}" if item.get("planted_ch") else ""))
            else:
                rows.append(f"- 【{label}】{fid}: 伏線台帳に無い ID（ledger_lint.py で確かめる）")
    handled = set((beat.get("plant") or []) + (beat.get("touch") or []) + (beat.get("payoff") or []))
    for item in project.foreshadowing:
        fid = item.get("id")
        if fid in handled or item.get("status") not in ("planted", "reminded"):
            continue
        last = last_touch_chapter(project, item, n)
        rows.append(f"- 【維持中】{fid}: {item.get('content', '')}（最後に触れた章 {last or '?'}、回収予定 {item.get('payoff_plan_ch') or '未定'}）"
                    " ※この章では触れなくてよい。矛盾だけ避ける")
    blocks.append((2, "2 伏線", "\n".join(rows) if rows else "（この章で扱う伏線なし）", False))

    # 3 登場人物と呼称
    rows = []
    for cid in cast:
        ch = project.characters.get(cid)
        rows.append(character_as_of(clean_md(ch.text), n) if ch else f"# {cid}\n（bible/characters/ にシートが無い。ledger_lint.py で確かめる）")
    addr, latest = [], {}
    for a in project.glossary.get("address", []) or []:
        start = int(a.get("from_ch", 0) or 0)
        if start >= n and start != 0:
            continue                  # 書く章から（またはそれ以降に）始まる呼び方は、まだ使われていない
        key = (a.get("from"), a.get("to"))
        if key not in latest or start >= int(latest[key].get("from_ch", 0) or 0):
            latest[key] = a
    for a in latest.values():
        if a.get("from") in cast + ["地の文"] and (a.get("to") in cast):
            addr.append(f"- {name_of(project, a['from']) if a['from'] != '地の文' else '地の文'} → {name_of(project, a['to'])}: "
                        f"「{a.get('call', '')}」" + (f"（{a['note']}）" if a.get("note") else ""))
    body = "\n\n".join(rows) + ("\n\n### 呼称表（この章の登場人物ぶん）\n" + "\n".join(addr) if addr else "")
    blocks.append((3, "3 登場人物（この章に出る人だけ）", body or "（章ビートの characters が空）", False))

    # 4 文体シート。無い・雛形のままなら、声の基準なしで書かせずに止める
    p = root / "style" / "style-sheet.md"
    sheet = clean_md(read_text(p)) if p.is_file() else ""
    if filled_items(sheet) < 3:      # 最小の 3 点（人称・距離・基調文末）に値があれば足りる。字数では測らない
        missing.append("style/style-sheet.md（文体シートが無いか、雛形のまま。人称・距離・基調文末の 3 点だけでも値を書く）")
    else:
        blocks.append((4, "4 文体シート", sheet, False))

    # 5 世界制約と用語
    cons = project.glossary.get("constraints", {}) or {}
    rows = []
    if cons.get("forbidden_words"):
        rows.append("- 本文に出さない語: " + "、".join(cons["forbidden_words"]))
    if cons.get("absent_concepts"):
        rows.append("- この世界に無い概念: " + "、".join(cons["absent_concepts"]))
    for u in cons.get("unknown_to", []) or []:
        if u.get("character") in cast:
            rows.append(f"- {name_of(project, u['character'])} が知らないこと: " + "、".join(u.get("items", [])))
    for t in project.glossary.get("terms", []) or []:
        rows.append(f"- 用語「{t.get('canonical', '')}」" + (f"（{t['reading']}）" if t.get("reading") else "")
                    + (f": {t['note']}" if t.get("note") else ""))
    if rows:
        blocks.append((5, "5 世界制約と用語", "\n".join(rows), False))

    # 6 前章末の状態 / 7 前章本文の末尾
    if n > 1:
        p = root / "canon" / "state" / f"{chapter_id(n - 1)}.md"
        if p.is_file() and filled_items(read_text(p)) >= 1:
            blocks.append((6, f"6 前章末の状態（{chapter_id(n - 1)}）", clean_md(read_text(p)), False))
        else:
            # 見出しや未記入の雛形だけの状態ファイルは「無い」のと同じ。第 2 章以降は開始状態なしで書かせない
            missing.append(f"canon/state/{chapter_id(n - 1)}.md（前章末の状態が無いか、未記入。所在・持ち物・怪我・各人が知ったことを書く）")
        p = root / "manuscript" / f"{chapter_id(n - 1)}.md"
        if p.is_file():
            text = read_text(p).rstrip()
            tail = text[-tail_chars:]
            tail = tail[tail.find("\n") + 1:] if len(text) > tail_chars and "\n" in tail else tail   # 段落の途中から始めない
            blocks.append((7, f"7 前章本文の末尾（{chapter_id(n - 1)}、接続用）", tail, True))

    # 8 既出事実。登場人物に関わるものと、直近 2 章のもの
    rows = []
    eligible = [f for f in project.facts if f.get("ch", 0) < n]
    superseded = {f["supersedes"] for f in eligible if f.get("supersedes")}   # 訂正された旧事実は載せない
    for f in eligible:
        if f.get("id") in superseded:
            continue
        related = bool(set(f.get("chars", []) or []) & set(cast)) or f.get("ch", 0) >= n - 2
        if related or f.get("status") == "proposed":
            mark = "【未確定】" if f.get("status") == "proposed" else ""
            fid = f"{f['id']} " if f.get("id") else ""      # 後から矛盾を追えるよう ID を残す
            rows.append(f"- {mark}{fid}（{chapter_id(f.get('ch', 0))}）: {f.get('fact', '')}")
    if rows:
        blocks.append((8, "8 既出の事実（本文に出たこと。【未確定】は作者未承認）", "\n".join(rows), True))

    # 9 決定ログ
    keys = set(cast) | handled | {chapter_id(n)}
    picked = [d for d in project.decisions if set(d.affects) & keys]
    recent = [d for d in project.decisions[-5:] if d not in picked]
    rows = [f"- {d.id} {d.date} [{d.kind}] {d.what}" + (f" — {d.why}" if d.why else "") for d in picked + recent]
    if rows:
        blocks.append((9, "9 決定ログ（reject は再提案しない）", "\n".join(rows), True))

    # 10 過去章の要約。直近 3 章は全文、古い章は最初の 1 文だけ
    rows = []
    for ch in range(1, n):
        p = root / "canon" / "summaries" / f"{chapter_id(ch)}.md"
        if not p.is_file():
            continue
        text = clean_md(re.sub(r"^#.*$", "", read_text(p), flags=re.M))
        if ch < n - 3:
            text = re.split(r"(?<=。)", text.replace("\n", ""))[0]
        rows.append(f"### {chapter_id(ch)}\n{text}")
    if rows:
        blocks.append((10, "10 これまでの要約", "\n\n".join(rows), True))

    # 予算。下から落とす。0〜3 は落とさない。落とせない分だけで予算を超えるなら、欠けたパックで書かせずに止める
    if missing and not allow_missing:
        raise PackError("必須の資料が足りない: " + " / ".join(missing)
                        + "。用意してから作り直す（承知のうえで進めるなら --allow-missing。パックに欠落を明記する）")
    if missing:
        blocks.append((0.5, "欠けている必須資料（--allow-missing で続行）", "\n".join(f"- {m}" for m in missing), False))
    title = project.novel.get("work", {}).get("title", "")
    head = (f"# コンテキストパック {chapter_id(n)}" + (f"『{title}』" if title else "") + "\n\n"
            "これは機械が束ねた執筆用の資料。この 1 枚だけを読んで書く。ここに無い設定を足さない。"
            "足した事実は canon/facts.jsonl に proposed で記録する。\n")

    def render(bs, dropped):
        out = [head] + [f"## {h}\n\n{body}\n" for _, h, body, _ in sorted(bs, key=lambda b: b[0])]
        out.append("## このパックから落としたもの\n\n" + ("\n".join(f"- {d}（予算 {budget} 字を超えたため）" for d in dropped) if dropped else "なし") + "\n")
        return "\n".join(out) + "\n" + HASH_PLACEHOLDER

    # 予算は見出しや注記、末尾のハッシュ行を含む完成形で測る。0〜6 は落とさない。それだけで超えるなら、欠けたパックで書かせずに止める
    required = [b for b in blocks if not b[3]]
    if size(render(required, [b[1] for b in blocks if b[3]])) > budget:
        sizes = "、".join(f"{b[1].split('（')[0]} {size(b[2])} 字" for b in required)
        raise PackError(f"落とせない部分だけで予算 {budget} 字を超える（{sizes}）。--budget を上げるか、"
                        "人物シート・文体シート・章ビートを刈り込む。契約・伏線・文体・制約・開始状態を欠いたパックでは書かない")
    dropped = []
    for prio in (10, 9, 8, 7):
        if size(render(blocks, dropped)) <= budget:
            break
        for i, b in enumerate(blocks):
            if b[0] == prio and b[3]:
                dropped.append(b[1])
                blocks.pop(i)
                break
    text = render(blocks, dropped).replace(HASH_PLACEHOLDER, f"<!-- sources-sha256: {sources_hash(project, n)} -->\n")
    return text, dropped, size(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="章を書くためのコンテキストパック work/chNNN.pack.md を作る。", epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=".", help="プロジェクトのルート（novel.toml のある場所）")
    ap.add_argument("--chapter", type=int, required=True, help="書く章の番号")
    ap.add_argument("--budget", type=int, default=60000, help="パックの上限（空白を除いた字数。既定 60000）")
    ap.add_argument("--tail-chars", type=int, default=1500, help="前章本文の末尾を何字載せるか（既定 1500）")
    ap.add_argument("--check", action="store_true", help="既存のパックが原典より古くないかだけ確かめる")
    ap.add_argument("--allow-missing", action="store_true", help="文体シートや前章末の状態が無くても作る（欠落をパックに明記する）")
    ap.add_argument("--json", action="store_true", help="結果を JSON で出す")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = Path(args.project)
    if not (root / "novel.toml").is_file():
        print(f"エラー: {root} に novel.toml が無い。init_project.py で作るか、--project を確かめる", file=sys.stderr)
        return 2
    try:
        project = load_project(root)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"エラー: プロジェクトを読めない: {exc}（ledger_lint.py で原因を確かめる）", file=sys.stderr)
        return 2
    out_path = root / "work" / f"{chapter_id(args.chapter)}.pack.md"
    if args.check:
        if not out_path.is_file():
            print(f"パックが無い: {out_path.as_posix()}。--check を外して作る")
            return 1
        m = HASH_LINE.search(read_text(out_path))
        fresh = bool(m) and m.group(1) == sources_hash(project, args.chapter)
        print("最新" if fresh else "古い: 原典が更新されている。作り直す")
        return 0 if fresh else 1
    if project.broken_files:
        print("エラー: 構文エラーで読めないファイルがある: " + ", ".join(sorted(project.broken_files))
              + "。ledger_lint.py で直してから作る", file=sys.stderr)
        return 2
    try:
        text, dropped, total = build(project, args.chapter, args.budget, args.tail_chars, args.allow_missing)
    except PackError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8", newline="\n")
    if args.json:
        print(json.dumps({"path": out_path.as_posix(), "chars": total, "dropped": dropped}, ensure_ascii=False))
    else:
        print(f"作成: {out_path.as_posix()}（約 {total} 字）" + (f"。落としたもの: {', '.join(dropped)}" if dropped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
