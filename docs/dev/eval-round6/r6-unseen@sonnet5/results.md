# 由来を伏せた読み比べの結果

> 本評価は、限定した依頼・モデル・設定による少数試行の探索的比較です。選好は掲載した作品対に対する判定であり、一般的な勝率や品質向上を示しません。生成・判定モデルの偏り、提示順序、長さの影響が残ります。過去の評価を参考に修正した既知の課題を含むため、独立した未知の課題での検証ではありません。lint は確認候補を示すもので、文学的品質や誤りの確定を表しません。

## 条件

- 作成: 2026-09-19T13:39:37 / スキルのコミット: d7409ec（未コミットの変更: あり）
- 生成: `python -X utf8 evals/gateway.py claude-code --model anthropic/claude-sonnet-5 --timeout 1500 --max-budget-usd 2`（Python 3.13.15）
- 判定: `python -X utf8 evals/gateway.py chat --model anthropic/claude-sonnet-5 --timeout 600`（Python 3.13.15）。同じ対を A/B を入れ替えて 2 回読ませた。2 回で選好が変わった対は「順序不安定」
- 系統 `with`: このリポジトリのスキル、写しのハッシュ 54eaed1a33db
- 系統 `base`: スキルなし（空のディレクトリ）
- 判定役に渡したのは、依頼文・2 つの応答・問いだけ（系統の名前、モデル名、版、過去の結果、lint の結果、期待出力は渡していない）
- 作業ディレクトリと依頼本文は分けているが、ファイルの読み取りの隔離は保証していない。他の系統、元のリポジトリ、結果・割り当て・ログ、利用環境の設定へアクセスできる可能性が残る。由来の情報を依頼文に直接含めない比較であり、厳密な盲検を保証するものではない
- ログの点検（スキルなしの系統と判定役のログに、スキルや結果のファイル名が出ていないか）: 15 本を点検して該当なし

## 対ごとの結果

| 依頼（ハッシュ） | 試行 | 1 回目（A / B → 選好） | 2 回目（A / B → 選好） | 結果 | 字数 with / base | 字数条件 with / base |
|---|---|---|---|---|---|---|
| kaidan-next-room（0ec54e262f8a） | 1 | base / with → with | with / base → with | **with** | 1036 / 1320 | 範囲内 / 範囲内 |
| library-bookmark-mystery（eb64d29b6626） | 1 | with / base → with | base / with → base | **順序不安定** | 1421 / 1137 | 指定なし / 指定なし |
| sf-last-report（a4b4c18b9e7b） | 1 | base / with → base | with / base → with | **順序不安定** | 1176 / 1237 | 指定なし / 指定なし |
| jidai-pawned-sword（623101c52656） | 1 | with / base → with | base / with → base | **順序不安定** | 1239 / 1270 | 指定なし / 指定なし |
| bungei-unknown-key（4ab4568eff43） | 1 | with / base → with | base / with → base | **順序不安定** | 1348 / 1381 | 指定なし / 指定なし |

## 依頼ごとの内訳

試行数が依頼ごとに違うので、合計の勝敗や勝率は出さない。過去のラウンドの結果とも合算しない。

- kaidan-next-room: with 1
- library-bookmark-mystery: 順序不安定 1
- sf-last-report: 順序不安定 1
- jidai-pawned-sword: 順序不安定 1
- bungei-unknown-key: 順序不安定 1

## lint の記録（判定のあとに結合。研究用）

診断の数（ルール単位）と該当箇所の数は別物。FAIL も WARN も確認候補で、確定した誤りではない（C 群を含む）。数が少ないことは、面白さや質の高さを意味しない。

| 依頼 | 試行 | 系統 | FAIL 診断 / 箇所 | 強WARN | WARN | INFO |
|---|---|---|---|---|---|---|
| kaidan-next-room | 1 | with | 0 | 0 | 1 / 1（M02） | 3 / 6（C07 K00 L01） |
| kaidan-next-room | 1 | base | 0 | 0 | 0 | 4 / 11（K00 N05 P04 R01） |
| library-bookmark-mystery | 1 | with | 0 | 0 | 1 / 1（M02） | 3 / 4（C07 K00 L01） |
| library-bookmark-mystery | 1 | base | 1 / 13（N05） | 0 | 0 | 3 / 8（C05 K00 N05） |
| sf-last-report | 1 | with | 0 | 0 | 1 / 1（M02） | 4 / 5（C07 K00 L01 R01） |
| sf-last-report | 1 | base | 0 | 0 | 0 | 4 / 9（K00 N05 O02 R01） |
| jidai-pawned-sword | 1 | with | 0 | 0 | 1 / 1（M02） | 4 / 5（C07 K00 L01 R01） |
| jidai-pawned-sword | 1 | base | 0 | 0 | 0 | 4 / 9（C09 K00 L01 N05） |
| bungei-unknown-key | 1 | with | 0 | 0 | 1 / 1（M02） | 3 / 4（C07 K00 L01） |
| bungei-unknown-key | 1 | base | 0 | 0 | 1 / 1（R01） | 2 / 6（K00 L01） |

## この結果から言えること・言えないこと

- 言える: この条件の、この作品対で、どちらが選ばれたか。入れ替えで選好が変わった対がいくつあったか。引用つきの理由（judge/ 以下）に出てきた具体的な長所と短所。
- 言えない: 勝率、一般的な品質の向上、特定の文言の修正が選好を変えたという因果、lint の数と面白さの関係。
