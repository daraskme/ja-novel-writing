# 評価ラウンド 2（計画。未実行）

ラウンド 1（`../eval-round1/`）は 4 組・各 1 試行・同一モデルで、直球の告白場面だけスキルなしが選ばれた。敗因を受けて結びの文言を直したが、その後の再評価はしていない。ラウンド 2 は、Codex との相談（`../consult-1-remaining-issues.md`）で決めた条件で、`evals/run_blind_eval.py` を使って行う。

## 状態（2026-09-19）

実行器・依頼・テストまで用意した。生成に着手した時点で Codex CLI がログアウト状態になっており（`401 Unauthorized`、`codex login status` は Not logged in）、18 件の生成がすべて失敗した。結果は何も得られていない。`codex login` のあと、下の手順をそのまま実行すれば走る（失敗した生成は記録に残り、同じ `--run` で再実行すると続きから走る）。

## 手順

結果の置き場所はリポジトリの外にする（ここでは `../blind-runs/`）。

1. スキルあり 対 なし。卒業式の告白を 2 試行（ラウンド 1 の「依頼 3」と同じ依頼文。evals.json の id 3 でも id 6 でもない）。

```bash
python -X utf8 evals/run_blind_eval.py generate --run ../blind-runs/r2-confession --arm with=skill --arm base=none --only graduation-confession --trials 2 --jobs 4
```

2. スキルあり 対 なし。ほかの 5 依頼を各 1 試行。

```bash
python -X utf8 evals/run_blind_eval.py generate --run ../blind-runs/r2-others --arm with=skill --arm base=none --only flash-talkative-comedy,romance-direct-confession,battle-spatial-causality,children-polite-repetition,web-episode-twelve-reminder --trials 1 --jobs 4
```

3. 結びの文言の有無だけを比べる。現行のスキル 対 その文言だけを抜いた写しで、卒業式の告白を 2 試行。スキルあり対なしの比較では、文言の修正の効果を分離できないためである。

```bash
python -X utf8 docs/dev/eval-round2/make_ablated_skill.py ../blind-runs/ablated-skill
```

```bash
python -X utf8 evals/run_blind_eval.py generate --run ../blind-runs/r2-ablation --arm current=skill --arm ablated=../blind-runs/ablated-skill --only graduation-confession --trials 2 --jobs 4
```

4. それぞれの `--run` に対して judge と report を実行する。

```bash
python -X utf8 evals/run_blind_eval.py judge --run ../blind-runs/r2-confession
```

```bash
python -X utf8 evals/run_blind_eval.py report --run ../blind-runs/r2-confession
```

5. `results.md`・`results.json`・`run.json`・`assign.json`・`gen/`・`judge/` をこのディレクトリへ写し、分かったことをこの README に書く。

## 結果の書き方

- 旧ラウンドの 3 勝 1 敗と合算しない。依頼ごとに試行数が違うので、合計の勝敗や勝率も書かない。差なし・順序不安定・判定不能・失敗を併記する。
- 言えるのは「この条件の、この作品対で、どちらが選ばれたか」まで。「結びの追記が改善を引き起こした」「告白の問題は解決した」「lint が減ったので面白くなった」とは書かない。
- 告白の対は、別のモデルか人間でも読み直す（生成と判定が同じモデルなら、そのモデルの好みが両方に効く）。
- `evals/README.md` の但し書きを付ける。
