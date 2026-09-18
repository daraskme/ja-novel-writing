# 評価ラウンド 2（2026-09-19）

> 本評価は、限定した依頼・モデル・設定による少数試行の探索的比較です。選好は掲載した作品対に対する判定であり、一般的な勝率や品質向上を示しません。生成・判定モデルの偏り、提示順序、長さの影響が残ります。過去の評価を参考に修正した既知の課題を含むため、独立した未知の課題での検証ではありません。lint は確認候補を示すもので、文学的品質や誤りの確定を表しません。

ラウンド 1（`../eval-round1/`）は 4 組・各 1 試行・同一モデルで、直球の告白場面だけスキルなしが選ばれた。敗因を受けて結びの文言を直したが、その後の再評価はしていなかった。ラウンド 2 は、Codex との相談（`../consult-1-remaining-issues.md`）で決めた条件で、`evals/run_blind_eval.py` を使って行った。ラウンド 1 の 3 勝 1 敗とは合算しない。

## 条件

- 生成も判定も Codex CLI 0.155.1（`codex exec -s read-only`、reasoning medium）。スキルはコミット `0561228` の `SKILL.md`・`references/`・`scripts/`・`assets/` の写し（ハッシュ `8dd360996c03`）。
- 同じ対を A/B を入れ替えて 2 回読ませ、2 回で選好が変わった対は「順序不安定」とした。判定役に渡したのは、依頼文・2 つの応答・依頼の型ごとの問いだけ。
- 実行は 3 本。記録は `r2-confession/`・`r2-others/`・`r2-ablation/`（`results.md`・`results.json`・`run.json`・`assign.json`・`gen/`・`judge/`。ログはローカルのパスを含むので置いていない）。
- ファイルの読み取りの隔離は保証していない（`evals/README.md`「残る限界」）。スキルなしの系統と判定役のログに、スキルや結果のファイル名は出ていなかった（`results.md` の「ログの点検」）。
- 1 回目の着手時は Codex CLI がログアウト状態で、18 件の生成がすべて失敗した。ログインし直して再実行した。
- 3 本とも、生成は検証レビュー 11（`../codex-review-11.md`）を反映する前の実行器で行った。`r2-confession` と `r2-ablation` は判定もそうである。応答のハッシュと判定の成否の記録（`*.meta.json`）は、保存してあったログの終了コードと依頼文から、あとで起こした（`run.json` の `migrated`）。本文と判定の中身には触れていない。起こす際に、どの判定も、終了コード 0・保存した依頼文がいまの応答から組み立てた依頼文と一致・最終行から選好を読める、の 3 点を満たすことを確かめた。`r2-others` の判定はレビュー 11 の反映後の実行器で行い、判定の本文のハッシュだけをあとで足した。
- つまりこれらは、旧実行器による探索的評価の移行記録であり、修正後の実行器の動作を検証した結果ではない。後付けのハッシュは、移行した時点の保存ファイルどうしの整合性を示すだけで、実行した当時の完全性を遡って保証しない。終了コードとログの点検は、手元に保管している原ログで確かめたが、原ログは公開していないので、公開している資料だけでは追認できない（ここに置いた記録に対して report を走らせ直すと、ログの点検は「確認不能」になる）。

## 結果

| 実行 | 依頼 | 試行 | 1 回目（A / B → 選好） | 2 回目（A / B → 選好） | 結果 |
|---|---|---|---|---|---|
| スキルあり（with）対なし（base） | 卒業式の告白 | 1 | base / with → with | with / base → with | **with** |
| 同上 | 卒業式の告白 | 2 | with / base → with | base / with → with | **with** |
| 同上 | 饒舌な一人称コメディ | 1 | with / base → base | base / with → with | **順序不安定** |
| 同上 | 駅前の告白 | 1 | with / base → with | base / with → with | **with** |
| 同上 | 能力バトル | 1 | base / with → base | with / base → with | **順序不安定** |
| 同上 | 読み聞かせの童話 | 1 | with / base → with | base / with → with | **with** |
| 同上 | Web 連載の第 12 話 | 1 | base / with → with | with / base → base | **順序不安定** |
| 現行（current）対 結びの文言だけを抜いた写し（ablated） | 卒業式の告白 | 1 | ablated / current → current | current / ablated → ablated | **順序不安定** |
| 同上 | 卒業式の告白 | 2 | current / ablated → ablated | ablated / current → ablated | **ablated** |

生成の失敗、判定の失敗、判定不能は無かった。

## 分かったこと

- **卒業式の告白（ラウンド 1 で負けた依頼）では、2 試行とも、提示順を入れ替えてもスキルあり版が選ばれた。** 判定役が挙げた理由は、告白が相手の選択や過去の出来事の意味まで変える構成（「だから、帰れなかった」「でも今、言う」）と、次の約束へ進む結び。スキルなし版は 2 試行とも指定の上限 1500 字を少し超え（1518 字、1515 字。判定役も指摘）、涙の描写の重なりを弱点に挙げられた。
- **結びの文言の効果は確認できなかった。** 文言の有無だけを変えた比較では、1 対が順序不安定、1 対は文言を抜いた写しが選ばれた。選ばれた理由は、誤解が会話でほどける筋の作りであって、結びの温度ではなかった。したがって「卒業式の告白でスキルあり版が選ばれるようになったのは、結びの文言を足したからだ」とは言えない。文言が害になっているとも言えない（2 対では少なすぎる）。文言は残すが、効果は未確認として扱う。スキルを削るときの候補になる。
- **提示順の影響が大きい。** 9 対のうち 4 対で、A/B を入れ替えると選好が変わった。その 4 対はどれも、判定役が 2 回とも同じ位置（コメディ・連載・文言比較は B、バトルは A）を選んでいる。1 回だけ読ませる比較（ラウンド 1 のやり方）は、この揺れを結果として記録してしまう。ラウンド 1 の 3 勝 1 敗も、その前提で読むこと。
- コメディ・能力バトル・連載の 3 依頼では、この条件ではスキルの有無による安定した選好は出なかった。駅前の告白と童話では、提示順によらずスキルあり版が選ばれた（各 1 試行）。
- lint の記録（各 `results.md` の末尾）では、FAIL はスキルなし版のコメディの N05（字下げの混在）1 件だけ。スキルなし版には、字下げなしの INFO（N05、2 件）と、能力バトルの体言止めの WARN（T02、9 箇所）が出ている。これは確認候補の数であって、選好の理由ではない。

## 言えること・言えないこと

- 言える: この条件の卒業式の告白 2 対で、スキルあり版が選ばれた。駅前の告白と童話の各 1 対でも同じ。9 対のうち 4 対は、提示順の入れ替えで選好が変わった。
- 言えない: 勝率、一般的な品質の向上、「告白の問題は解決した」、「結びの追記が改善を引き起こした」、「lint が減ったので面白くなった」。
- 生成と判定が同じモデルなので、そのモデルの好みが両方に効いている。告白の 4 対（`r2-confession/gen/`、`r2-ablation/gen/`）は、別のモデルか人間でも読み直すのがよい。未実施。

## 同じ手順で走らせ直す

結果の置き場所はリポジトリの外にする（ここでは `../blind-runs/`）。

```bash
python -X utf8 evals/run_blind_eval.py generate --run ../blind-runs/r2-confession --arm with=skill --arm base=none --only graduation-confession --trials 2 --jobs 4
```

```bash
python -X utf8 evals/run_blind_eval.py generate --run ../blind-runs/r2-others --arm with=skill --arm base=none --only flash-talkative-comedy,romance-direct-confession,battle-spatial-causality,children-polite-repetition,web-episode-twelve-reminder --trials 1 --jobs 4
```

```bash
python -X utf8 docs/dev/eval-round2/make_ablated_skill.py ../blind-runs/ablated-skill
```

```bash
python -X utf8 evals/run_blind_eval.py generate --run ../blind-runs/r2-ablation --arm current=skill --arm ablated=../blind-runs/ablated-skill --only graduation-confession --trials 2 --jobs 4
```

それぞれの `--run` に対して judge と report を実行する。

```bash
python -X utf8 evals/run_blind_eval.py judge --run ../blind-runs/r2-confession
```

```bash
python -X utf8 evals/run_blind_eval.py report --run ../blind-runs/r2-confession
```
