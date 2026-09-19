> Grok 4.6（Vercel AI Gateway 経由、Perplexity の検索つき。2026-09-19）の調査結果。出典は Grok が報告したもので、こちらで実在を確かめたのは一部だけ（arXiv:2505.16234 LIFEBench、arXiv:2508.13805、arXiv:2511.01807、ACL Anthology の Zero-Shot Strategies for Length-Controllable Summarization、monokaki の記事）。「推測」と書かれた部分は Grok の推測で、事実ではない。

2025〜2026年の論文と実務知見を優先して調べます。判定役の不一致・位置バイアスの扱いと、創作評価の枠組みを並行で検索します。**要点。** 判定役の不一致と位置バイアスはノイズではなく構造。2025〜2026年の実務は、(1) 必ず両順で読む、(2) 両順が一致したときだけ勝敗、(3) 家族横断の複数判定＋人間の小標本校正、(4) 不一致は棄却せず信号として残す、が標準に近い。創作では汎用LLM判定の人間一致は天井が低い。

## 確認できた事実

**位置バイアスと集約。** Shi et al., *A Systematic Study of Position Bias in LLM-as-a-Judge*（IJCNLP 2025, https://aclanthology.org/2025.ijcnlp-long.18/）は15判定×22課題で、位置バイアスが偶然ではなく、品質差が小さいほど強いと報告。*The Coin Flip Judge?*（arXiv:2606.13685, 2026-04）は平均13.6%が提示順で逆転、判定間一致76%（κ=0.51）。*Reliability without Validity*（arXiv:2606.19544, 2026-06）は再テスト信頼性が高くても位置バイアスが残る「一貫性–バイアス逆説」を示す。推奨はAB/BAの両方、κなどの偶然補正指標、複数回試行。

実務側も一致する。両順を走らせ、**両順が同じ勝者のときだけ記録、不一致は引き分け／棄権**（FlowVerify 2026-05, https://www.flowverify.co/blog/llm-as-judge-systematic-bias-2026）。Arena-Hardは両順をBradley-Terryに入れ、不一致対を捨てない（Zylos, 2026-08, https://zylos.ai/en/research/2026-08-24-llm-as-judge-calibration-structured-evaluation/）。複数判定は単純平均より判定ごとの信頼度を推定するBT-σ（*Who can we trust? LLM-as-a-jury*, arXiv:2602.16610, 2026-02）。パネルは**提供者を混ぜる**（OpenAI＋Anthropic＋Google）。同系統3体は実質1票。不一致は人間へ回す（orq.ai, 2026-05, https://orq.ai/blog/llm-juries-in-practice）。人間校正は100〜300対が目安。創作の人間同士κはおおよそ0.65前後が天井。

**創作評価。** LitBench（Fein et al., EACL 2026, https://aclanthology.org/2026.eacl-long.362/）では最強の既製判定でも人間選好一致は73%。EQ-Bench Creative Writing v3（Paech, 2025〜）はルーブリック＋Elo、4000字切り詰め、AB/BA平均、Slop（定型句）は判定なしの機械指標（https://eqbench.com/creative_writing.html）。落とし穴は長さバイアス、自己選好（同系統の文体を好む、認識ではなく分布の近さ。IOV Studio 2026-06）、判定が「説明を削れ・余韻」側に寄ると直球告白が減点されうる**ルーブリック漏れ**、AI文同士を人間文より高く付ける傾向（arXiv:2608.23705, 2026-08）。

**字数。** LLMは生成中に正確に数えられない。外部で数え、足りなければ特定箇所を一度だけ足す／多めに書いて切る、が実務（GitHub Discussions 2025-10; PBJ Marketing 2025-09）。「approximately」は下限扱いになりやすい（GlitchyTales 2026-02）。

## 見つからなかったこと

日本語小説専用のLLM判定ベンチは見つからず。継ぎ足し周回が整合を壊す、という創作エージェント論文もなし。gpt-6-astraを判定役にした査読研究もなし。

## 推測（事実ではない）

駅前告白の割れはスキルの失敗というより、**指示遵守（感情の名指し・交際後の約束）**と**文芸的余韻**の軸が衝突している信号。gpt-6-astraの理由はそのまま後者を減点している。生成がSonnet、判定にもSonnetがいると自己選好が技能差に見える可能性がある。

## 打ち手（3つ）

1. **字数は下限ノルマにしない。** 指定があるときだけ「1回書いて外部計測→不足なら1箇所を1回だけ足す→再計測1回で止める」。届かなくても継がない。モデル固有の倍率はスキル本文に書かない。
2. **判定は総合1票に潰さない。** 両順一致のみ勝敗。割れた依頼は「指示遵守」と「余韻・文体」を別軸で点にする。3判定不一致は引き分け＋人間1本。
3. **告白系はジャンル契約を判定プロンプト先頭に固定。** 「感情を名指しし、交際の成立が言葉で残ること」を必須条件にし、余韻偏重の減点を止める。スキル本文の「削る・像で切る」は direct 契約では適用外、と判定側に書く。
