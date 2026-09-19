# 由来を伏せた読み比べの結果

> 本評価は、限定した依頼・モデル・設定による少数試行の探索的比較です。選好は掲載した作品対に対する判定であり、一般的な勝率や品質向上を示しません。生成・判定モデルの偏り、提示順序、長さの影響が残ります。過去の評価を参考に修正した既知の課題を含むため、独立した未知の課題での検証ではありません。lint は確認候補を示すもので、文学的品質や誤りの確定を表しません。

## 条件

- 作成: 2026-09-19T16:26:02 / スキルのコミット: b0f89b4（未コミットの変更: あり）
- 生成: `python -X utf8 evals/gateway.py claude-code --model anthropic/claude-sonnet-5 --timeout 1500 --max-budget-usd 2`（Python 3.13.15）
- 判定: `codex exec -s read-only --skip-git-repo-check -c model_reasoning_effort="medium" -C {cwd} -o {out} -`（codex-cli 0.155.1）。同じ対を A/B を入れ替えて 2 回読ませた。2 回で選好が変わった対は「順序不安定」
- 系統 `new`: このリポジトリのスキル、写しのハッシュ 6df8c5228741
- 系統 `prev`: 別の版のスキル（prev-skill）、写しのハッシュ 3e3513220194
- 判定役に渡したのは、依頼文・2 つの応答・問いだけ（系統の名前、モデル名、版、過去の結果、lint の結果、期待出力は渡していない）
- 作業ディレクトリと依頼本文は分けているが、ファイルの読み取りの隔離は保証していない。他の系統、元のリポジトリ、結果・割り当て・ログ、利用環境の設定へアクセスできる可能性が残る。由来の情報を依頼文に直接含めない比較であり、厳密な盲検を保証するものではない
- ログの点検（スキルなしの系統と判定役のログに、スキルや結果のファイル名が出ていないか）: 12 本を点検して該当なし

## 対ごとの結果

| 依頼（ハッシュ） | 試行 | 1 回目（A / B → 選好） | 2 回目（A / B → 選好） | 結果 | 字数 new / prev | 字数条件 new / prev |
|---|---|---|---|---|---|---|
| romance-rooftop-mutual（492301c4fab0） | 1 | prev / new → prev | new / prev → prev | **prev** | 1086 / 1091 | 範囲内 / 範囲内 |
| romance-festival-childhood-friend（d9379a240ab8） | 1 | prev / new → prev | new / prev → prev | **prev** | 1267 / 1310 | 指定なし / 指定なし |
| romance-restrained-reunion（350120861171） | 1 | prev / new → new | new / prev → new | **new** | 943 / 1033 | 指定なし / 指定なし |
| romance-unrequited-farewell（b91872a754ab） | 1 | new / prev → new | prev / new → new | **new** | 1201 / 1237 | 指定なし / 指定なし |
| office-kitchenette-comedy（f6a3f2c02f27） | 1 | new / prev → prev | prev / new → prev | **prev** | 947 / 838 | 範囲内 / 範囲内 |
| fantasy-inn-dinner（5bbca1863e63） | 1 | new / prev → new | prev / new → new | **new** | 1067 / 1195 | 指定なし / 指定なし |

## 依頼ごとの内訳

試行数が依頼ごとに違うので、合計の勝敗や勝率は出さない。過去のラウンドの結果とも合算しない。

- romance-rooftop-mutual: prev 1
- romance-festival-childhood-friend: prev 1
- romance-restrained-reunion: new 1
- romance-unrequited-farewell: new 1
- office-kitchenette-comedy: prev 1
- fantasy-inn-dinner: new 1

## lint の記録（判定のあとに結合。研究用）

診断の数（ルール単位）と該当箇所の数は別物。FAIL も WARN も確認候補で、確定した誤りではない（C 群を含む）。数が少ないことは、面白さや質の高さを意味しない。

| 依頼 | 試行 | 系統 | FAIL 診断 / 箇所 | 強WARN | WARN | INFO |
|---|---|---|---|---|---|---|
| romance-rooftop-mutual | 1 | new | 0 | 0 | 1 / 1（M02） | 3 / 7（C07 K00 L01） |
| romance-rooftop-mutual | 1 | prev | 0 | 0 | 1 / 1（M02） | 2 / 3（C07 K00） |
| romance-festival-childhood-friend | 1 | new | 0 | 0 | 1 / 1（M02） | 4 / 7（C07 D03 K00 O02） |
| romance-festival-childhood-friend | 1 | prev | 0 | 0 | 1 / 1（M02） | 3 / 3（C07 D03 K00） |
| romance-restrained-reunion | 1 | new | 0 | 0 | 1 / 1（M02） | 4 / 5（C07 K00 L01 R01） |
| romance-restrained-reunion | 1 | prev | 0 | 0 | 1 / 1（M02） | 2 / 6（C07 K00） |
| romance-unrequited-farewell | 1 | new | 0 | 0 | 1 / 1（M02） | 5 / 8（C07 K00 L01 R01 T01） |
| romance-unrequited-farewell | 1 | prev | 0 | 0 | 3 / 2（L01 M02 N06） | 3 / 4（C07 K00 O02） |
| office-kitchenette-comedy | 1 | new | 0 | 0 | 1 / 1（M02） | 3 / 7（C07 K00 R01） |
| office-kitchenette-comedy | 1 | prev | 0 | 0 | 1 / 1（M02） | 3 / 3（C07 K00 R01） |
| fantasy-inn-dinner | 1 | new | 0 | 0 | 1 / 1（M02） | 5 / 7（C07 C09 K00 L01 O02） |
| fantasy-inn-dinner | 1 | prev | 0 | 0 | 2 / 1（L01 M02） | 3 / 3（C07 C09 K00） |

## この結果から言えること・言えないこと

- 言える: この条件の、この作品対で、どちらが選ばれたか。入れ替えで選好が変わった対がいくつあったか。引用つきの理由（judge/ 以下）に出てきた具体的な長所と短所。
- 言えない: 勝率、一般的な品質の向上、特定の文言の修正が選好を変えたという因果、lint の数と面白さの関係。
