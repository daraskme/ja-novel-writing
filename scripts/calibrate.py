#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate.py -- 作者が認めた章から、その作品の lint 警報線を作る（標準ライブラリのみ）。

なぜ要るか:
  人間の書き手の分布は広い（一文段落率 3〜88%、会話比率 1〜55%）。万人向けの警報線は、
  必ずどこかの良い作品を誤検出する。その作品の声で書かれ、作者が良しとした章を物差しにする。

やること:
  承認済みの章（3 本以上）を novel_lint.py と同じ計数で測り、対応する指標の警報線を、章の平均とばらつき
  （下げすぎを防ぐ床つき）から作って style/lint.json の overrides に書く。算出方法はこのスクリプトが正本。
  契約（emotion_naming = direct）や手書きで止めてあるルールは較正しない。表記（N）と数え上げ（C）の FAIL は動かさない。
  手書きの上書き（reason が "calibrate:" で始まらないもの）は残す。3 本未満なら何もしない。

使い方:
  python calibrate.py --project . manuscript/ch001.md manuscript/ch002.md manuscript/ch003.md
  python calibrate.py --project . --dry-run manuscript/ch00[1-5].md

終了コード: 0 = 書いた（または --dry-run）/ 1 = 章が足りず何もしなかった / 2 = 実行エラー
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import novel_lint as nl  # noqa: E402

MIN_CHAPTERS = 3
MIN_CHARS = 1500     # これより短い章は統計が暴れるので物差しにしない
LOWER_STATS = {"L01": "sentence_len_cv", "L02": "sentence_len_mean", "P02": "paragraph_len_cv"}


def density_per_rule(doc: nl.Doc, cfg: nl.Config) -> dict:
    out = {}
    for rid, rule in cfg.thresholds["rules"].items():
        if rule.get("kind") != "density" or "lexicon" not in rule:
            continue
        unit = rule.get("unit", "narr")
        lines, base = (doc.narr_lines(), doc.narr_chars) if unit == "narr" else (doc.all_lines(), doc.total_chars)
        if base:
            out[rid] = len(nl.find_all(lines, cfg.rx(rule["lexicon"]))) * 1000 / base
    return out


def measure(path: Path, cfg: nl.Config):
    doc = nl.Doc(path.read_text(encoding="utf-8-sig"))
    stats = nl.collect_stats(doc, cfg)
    return doc, stats, density_per_rule(doc, cfg)


def mean_sd(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < MIN_CHAPTERS:
        return None, None
    return statistics.mean(vals), statistics.pstdev(vals)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="承認済みの章から作品別の lint 警報線を作り、style/lint.json に書く。",
                                 epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chapters", nargs="+", help="作者が良しとした章の本文ファイル（3 本以上）")
    ap.add_argument("--project", default=".", help="プロジェクトのルート")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず、作られる値だけ表示する")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    root = Path(args.project)
    if not root.is_dir():
        print(f"エラー: --project {root} はディレクトリではない", file=sys.stderr)
        return 2
    try:
        cfg = nl.Config(None, None, root if (root / "novel.toml").is_file() else None)
        project_cfg = nl.Config(None, None, root if (root / "novel.toml").is_file() else None)
        project_cfg.finalize()
        cfg.overrides = {}      # 物差しを作るときは既存の上書きを見ない（止めてあるルールの判定は project_cfg で）
        cfg.finalize()
    except Exception as exc:
        print(f"エラー: 設定を読めない: {exc}", file=sys.stderr)
        return 2
    rows, used = [], []
    names = []
    for pattern in args.chapters:      # Windows のシェルはワイルドカードを展開しないので、ここで展開する
        hits = sorted(glob.glob(pattern))
        names += hits if hits else [pattern]
    for name in names:
        p = Path(name)
        if not p.is_file():
            print(f"エラー: {p} が無い", file=sys.stderr)
            return 2
        doc, stats, dens = measure(p, cfg)
        if doc.total_chars < MIN_CHARS:
            print(f"除外: {p.as_posix()} は {doc.total_chars} 字（{MIN_CHARS} 字未満は物差しにしない）")
            continue
        rows.append((stats, dens))
        used.append(p.as_posix())
    if len(rows) < MIN_CHAPTERS:
        print(f"章が足りない: 使える章は {len(rows)} 本。{MIN_CHAPTERS} 本以上そろうまではプロファイルの既定値を使う（何も書いていない）")
        return 1
    reason = "calibrate: " + ", ".join(Path(u).name for u in used)
    new = {}
    for rid, key in LOWER_STATS.items():      # 下限側（これを下回ったら鳴らす）
        m, sd = mean_sd([s[key] for s, _ in rows])
        if m is not None:
            new[rid] = {"info": round(max(m - sd, m * 0.85), 3), "warn": round(max(m - 2 * sd, m * 0.7), 3), "reason": reason}
    m, sd = mean_sd([s["dialogue_ratio"] for s, _ in rows])
    if m is not None:
        new["D01"] = {"target": round(m, 3), "info": round(max(0.10, 2 * sd), 3), "reason": reason}
    for rid in sorted({r for _, d in rows for r in d}):   # 上限側（これを上回ったら鳴らす）
        if project_cfg.levels(rid).get("off"):
            continue      # 契約や手書きで止めてあるルールを、機械的な再計算で復活させない
        m, sd = mean_sd([d.get(rid, 0.0) for _, d in rows])
        if m is None:
            continue
        base = cfg.levels(rid)
        # 作者の平均が既定より低いとき、線を下げすぎない（既定の info の半分を床にする）
        floor = (base.get("info") or base.get("warn") or 1.0) * 0.5
        warn = max(m + 2 * sd, m * 1.3, floor * 1.5)
        info = max(m + sd, m * 1.15, floor)
        new[rid] = {"info": round(info, 2), "warn": round(warn, 2), "reason": reason}
    path = root / "style" / "lint.json"
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"エラー: {path.as_posix()} が壊れている（{exc.msg}）。直すか退避してからやり直す", file=sys.stderr)
            return 2
    old = data.get("overrides", {}) if isinstance(data, dict) else {}
    kept = {k: v for k, v in old.items() if isinstance(v, dict) and not str(v.get("reason", "")).startswith("calibrate:")}
    merged = {**new, **kept}     # 手書きの上書きが勝つ
    result = {"version": 1, "calibrated_from": used, "overrides": dict(sorted(merged.items()))}
    print(f"物差しにした章: {len(used)} 本")
    for rid, v in sorted(new.items()):
        name = cfg.rule(rid)["name"]
        shown = ", ".join(f"{k}={val}" for k, val in v.items() if k != "reason")
        print(f"  {rid} {name}: {shown}" + ("  ※手書きの上書きを優先" if rid in kept else ""))
    if args.dry_run:
        print("（--dry-run: 書き込んでいない）")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"書き込み: {path.as_posix()}。外したいルールは overrides に {{\"off\": true, \"reason\": \"…\"}} を手で書く")
    return 0


if __name__ == "__main__":
    sys.exit(main())
