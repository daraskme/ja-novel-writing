# ja-novel-writing

日本語の小説を書く・直す・設計するための Claude Code スキル。掌編の一発執筆から、短編、長編・Web 連載の一貫性管理、既存原稿の推敲、文体模写、壁打ちまでを扱う。

## 何が違うか

- **書くときに NG 語リストを見せない。** 避けたい語を見せると、その語が出やすくなる。執筆時に読むのは作品の契約と「こう書く」という操作だけ。AI 臭のカタログは書いた後、機械検査が鳴った項目だけ読む。語彙の辞書はスクリプトだけが持つ。
- **機械で測れるものは機械で測る。** `scripts/novel_lint.py` が表記、文末・文長・段落の統計、語彙の密度、メタ混入を検査する。一部の指標は青空文庫 33 作品の計量と照合した。現代エンタメ・Web 向けや未較正の指標は暫定値で、各ルールの `calibrated` に示す。点数は出さない。ヒットは直す（fix）か残す（keep）を書き手が選ぶ。
- **数え上げを検算する。** 「『ありがとう』の四文字」のような字数・音数の誤りは LLM の指紋になる。lint が括弧内の実字数と本文の数を突き合わせ、合わなければ FAIL にする。
- **自分で書いたものを自分で採点しない。** 意図を知らされない読者役、編集役、校閲役を別コンテキストで走らせる。別系統のモデル（Codex CLI など）に見せる手順も同梱。
- **長編は状態をファイルに置く。** 計画（bible）と既出（canon）を分け、LLM が書き足した事実は proposed、作者が認めて confirmed。章ごとに必要な情報だけを束ねたコンテキストパックで書く。却下した案は決定ログに残し、再提案を防ぐ。
- **作品の声が上。** 文体シートとジャンル契約が汎用規則に勝つ。数値は目標ではなく目安と警報。

## 導入

Claude Code のスキル置き場に clone するか、clone した場所へリンクを張る。

```bash
git clone https://github.com/daraskme/ja-novel-writing ~/.claude/skills/ja-novel-writing
```

Windows で、編集をそのまま反映させたいならジャンクションが便利:

```powershell
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\ja-novel-writing" -Target "<このリポジトリを置いた場所>"
```

必要なのは Python 3.11 以上（標準ライブラリのみ）。Python が無くても執筆はできるが、機械検査は飛ばされる。

## 使い方

Claude Code で普通に頼めばよい。

- 「1500 字くらいの掌編を書いて。テーマは言えなかった一言」
- 「カクヨム用の異世界ファンタジー第 1 話、3000〜4000 字で」
- 「この下書き、AI っぽさを文章レベルで直して」（原稿を貼るかファイルを指定）
- 「長編を始めたい。プロジェクトを作って企画から」
- 「この見本の文体で短編を」

明示的に呼ぶなら `/ja-novel-writing`。

## 構成

```
SKILL.md            本体（不変の約束、モード判定、書くときの操作、読む条件表）
references/         必要なときだけ読む詳細（文体の核、場面カード、構成、人物、文体シート、
                    ジャンル、短編工程、長編運用、AI 臭カタログ、推敲、表記、レビュー、役割）
scripts/            機械検査・字数・エクスポート・プロジェクト管理（Python 標準ライブラリのみ）
assets/templates/   長編プロジェクトの雛形
evals/              スキルの評価用プロンプトと、由来を伏せた読み比べの実行器（evals/README.md）
```

## スクリプトを単体で使う

```bash
python scripts/novel_lint.py 原稿.md --profile web
```

```bash
python scripts/count_chars.py 原稿.md
```

```bash
python scripts/export.py 原稿.md --to kakuyomu --verify
```

```bash
python scripts/init_project.py my-novel
```

各スクリプトの `--help` に引数の説明がある。テストは次で走る。

```bash
python -m unittest discover -s scripts/tests
```

## 出典

`CREDITS.md` を参照。複数の公開スキルから考え方を学び、文面はすべて書き下ろした。再配布する場合の注意も同ファイルにある。
