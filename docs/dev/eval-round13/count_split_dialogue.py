"""保存した生成本文から、台詞を地の文の一文で割る形を数える（評価ラウンド 13 の記録用）。

対象は docs/dev/ 以下の gen/ にある本文（base.md をスキルなし版、ほかをスキルあり版とする）。
数えるのは、同じ行（同じ段落）の中で、台詞の閉じ括弧のあとに括弧を含まない地の文が一文だけあり、
すぐ次の台詞が始まる形（「…」と彼女が言った。「…」／「…」母が言った。「…」）。
同じ話者かどうかは機械では決めないので、該当行をすべて列挙する。日本語の小説でも普通に使われる形で、
誤りを数えるものではない。

    python docs/dev/eval-round13/count_split_dialogue.py
"""
import collections
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PATTERN = re.compile(r"」[^「」『』\n。]{1,40}。「")


def main():
    os.chdir(ROOT)
    files = sorted(glob.glob("docs/dev/**/gen/**/*.md", recursive=True))
    total = collections.Counter()
    hits = collections.defaultdict(list)
    for path in files:
        side = "base" if os.path.basename(path) == "base.md" else "skill"
        total[side] += 1
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        found = [(i, line) for i, line in enumerate(lines, 1) if PATTERN.search(line)]
        if found:
            hits[side].append((path.replace("\\", "/"), found))
    print("本文の数:", dict(total))
    print("該当行を含む本文の数:", {k: len(v) for k, v in hits.items()})
    for side in ("base", "skill"):
        for path, found in hits[side]:
            print(side, path, "行:", ",".join(str(i) for i, _ in found))


if __name__ == "__main__":
    main()
