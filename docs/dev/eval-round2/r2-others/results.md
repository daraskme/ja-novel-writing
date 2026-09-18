# 由来を伏せた読み比べの結果

> 本評価は、限定した依頼・モデル・設定による少数試行の探索的比較です。選好は掲載した作品対に対する判定であり、一般的な勝率や品質向上を示しません。生成・判定モデルの偏り、提示順序、長さの影響が残ります。過去の評価を参考に修正した既知の課題を含むため、独立した未知の課題での検証ではありません。lint は確認候補を示すもので、文学的品質や誤りの確定を表しません。

## 条件

- 作成: 2026-09-19T08:28:51 / スキルのコミット: 0561228（未コミットの変更: なし）
- 生成: `codex exec -s read-only --skip-git-repo-check -c model_reasoning_effort="medium" -C {cwd} -o {out} -`（codex-cli 0.155.1）
- 判定: `codex exec -s read-only --skip-git-repo-check -c model_reasoning_effort="medium" -C {cwd} -o {out} -`（codex-cli 0.155.1）。同じ対を A/B を入れ替えて 2 回読ませた。2 回で選好が変わった対は「順序不安定」
- 系統 `with`: このリポジトリのスキル、写しのハッシュ 8dd360996c03
- 系統 `base`: スキルなし（空のディレクトリ）
- 判定役に渡したのは、依頼文・2 つの応答・問いだけ（系統の名前、モデル名、版、過去の結果、lint の結果、期待出力は渡していない）
- 作業ディレクトリと依頼本文は分けているが、ファイルの読み取りの隔離は保証していない。他の系統、元のリポジトリ、結果・割り当て・ログ、利用環境の設定へアクセスできる可能性が残る。由来の情報を依頼文に直接含めない比較であり、厳密な盲検を保証するものではない
- ログの点検（スキルなしの系統と判定役のログに、スキルや結果のファイル名が出ていないか）: 15 本を点検して該当なし

## 対ごとの結果

| 依頼（ハッシュ） | 試行 | 1 回目（A / B → 選好） | 2 回目（A / B → 選好） | 結果 | 字数 with / base | 字数条件 with / base |
|---|---|---|---|---|---|---|
| flash-talkative-comedy（f316938e8a3c） | 1 | with / base → base | base / with → with | **順序不安定** | 1390 / 1464 | 指定なし / 指定なし |
| romance-direct-confession（c0b6c036f4d2） | 1 | with / base → with | base / with → with | **with** | 1218 / 808 | 指定なし / 指定なし |
| battle-spatial-causality（a7bd0c203885） | 1 | base / with → base | with / base → with | **順序不安定** | 1548 / 1411 | 指定なし / 指定なし |
| children-polite-repetition（27eb5aa73192） | 1 | with / base → with | base / with → with | **with** | 1473 / 1423 | 指定なし / 指定なし |
| web-episode-twelve-reminder（af41771f1f46） | 1 | base / with → with | with / base → base | **順序不安定** | 3162 / 2701 | 指定なし / 指定なし |

## 依頼ごとの内訳

試行数が依頼ごとに違うので、合計の勝敗や勝率は出さない。過去のラウンドの結果とも合算しない。

- flash-talkative-comedy: 順序不安定 1
- romance-direct-confession: with 1
- battle-spatial-causality: 順序不安定 1
- children-polite-repetition: with 1
- web-episode-twelve-reminder: 順序不安定 1

## lint の記録（判定のあとに結合。研究用）

診断の数（ルール単位）と該当箇所の数は別物。FAIL も WARN も確認候補で、確定した誤りではない（C 群を含む）。数が少ないことは、面白さや質の高さを意味しない。

| 依頼 | 試行 | 系統 | FAIL 診断 / 箇所 | 強WARN | WARN | INFO |
|---|---|---|---|---|---|---|
| flash-talkative-comedy | 1 | with | 0 | 0 | 1 / 4（T02） | 2 / 5（K00 L02） |
| flash-talkative-comedy | 1 | base | 1 / 1（N05） | 0 | 1 / 4（T02） | 4 / 10（K00 L02 R01 T01） |
| romance-direct-confession | 1 | with | 0 | 0 | 2 / 0（L01 R03） | 2 / 5（K00 R01） |
| romance-direct-confession | 1 | base | 0 | 0 | 1 / 0（R03） | 5 / 5（K00 L01 L02 N05 R01） |
| battle-spatial-causality | 1 | with | 0 | 0 | 0 | 4 / 3（K00 L01 L02 T01） |
| battle-spatial-causality | 1 | base | 0 | 0 | 1 / 9（T02） | 5 / 7（K00 L01 L02 P04 T01） |
| children-polite-repetition | 1 | with | 0 | 0 | 1 / 0（L01） | 3 / 3（K00 L02 O02） |
| children-polite-repetition | 1 | base | 0 | 0 | 1 / 0（L01） | 4 / 4（K00 L02 N05 P04） |
| web-episode-twelve-reminder | 1 | with | 0 | 0 | 0 | 5 / 4（K17 L01 L02 L04 R01） |
| web-episode-twelve-reminder | 1 | base | 0 | 0 | 1 / 1（T01） | 5 / 6（K10 L01 L02 L04 P02） |

## この結果から言えること・言えないこと

- 言える: この条件の、この作品対で、どちらが選ばれたか。入れ替えで選好が変わった対がいくつあったか。引用つきの理由（judge/ 以下）に出てきた具体的な長所と短所。
- 言えない: 勝率、一般的な品質の向上、特定の文言の修正が選好を変えたという因果、lint の数と面白さの関係。
