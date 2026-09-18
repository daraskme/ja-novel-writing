# -*- coding: utf-8 -*-
"""結びの文言（評価ラウンド 1 の敗因を受けて足したもの）だけを抜いたスキルの写しを作る。ほかの差分は無い。

    python -X utf8 docs/dev/eval-round2/make_ablated_skill.py ../blind-runs/ablated-skill

抜く文が見つからなければ止まる（文言を変えたら CUTS も直す）。写しはリポジトリの外に作ること。
"""
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
CUTS = {
    "SKILL.md": ["ただし、依頼や契約が約束した感情（胸が熱くなる、泣ける、笑える、怖い）は最後の一行まで保つ。総括を避けようとして、約束と違う温度の軽い落ちや照れ隠しへ逃げない。"],
    "references/prose-core.md": ["- 結びを含む読後感が、依頼とジャンル契約の約束を満たすか確かめる。笑い・照れ・静かな収束も、その感情を深めるなら使える。"
                                 "別の感情へ着地して余韻を弱めていないか、最後の一、二文あり／なしで読み比べる。\n"],
}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    dst = Path(sys.argv[1]).resolve()
    if dst == SRC or SRC in dst.parents:
        print("写しはリポジトリの外に作る。")
        return 2
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for part in ("SKILL.md", "references", "scripts", "assets"):
        if (SRC / part).is_dir():
            shutil.copytree(SRC / part, dst / part, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        else:
            shutil.copy2(SRC / part, dst / part)
    for rel, cuts in CUTS.items():
        path = dst / rel
        text = path.read_text(encoding="utf-8")
        for cut in cuts:
            if text.count(cut) != 1:
                print(f"{rel}: 抜く文が見つからない（{cut[:24]}…）。CUTS を現行の文言に合わせる。")
                return 1
            text = text.replace(cut, "")
        path.write_text(text, encoding="utf-8", newline="\n")
    print(f"作成: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
