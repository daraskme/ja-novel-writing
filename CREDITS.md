# 出典とライセンス上の注意

このスキルは、下記の公開資料を調査し、そこから得た考え方・手順・分類を参考にしたうえで、文面・構成・例文のすべてを新たに書き下ろしたものである。元資料の文章は転載していない。足りない部分（日本語の文体論、表記と投稿先の体裁、LLM 固有の失敗モード、長編の状態管理、機械検査、数え上げの検算など）は独自に設計した。lint の初期閾値は、青空文庫の公開作品 33 本の計量（本文は保存せず集計値のみ使用）で人間の分布と照合している。

## 参考にした資料

| 資料 | 作者 | ライセンス | このスキルが学んだ主な考え方 |
|---|---|---|---|
| japanese-tech-writing（gist） | k16shikano | Unlicense と表記 | LLM 口調の機能的な定義、翻訳調の判定、過剰修正を防ぐ安全弁 |
| cognitive-rhythm-writing（gist） | k16shikano | 表記なし | 「状況を更新する文か、文書を更新する文か」の判定、未回収の緊張の管理、装置名の漏出検査 |
| [fiction-prose-jp](https://github.com/MetamoL/fiction-prose-jp) | MetamoL | 独自（自分用の改変は可、再配布・転載・実質的部分の組み込みは禁止） | 短文と語り手の声、判断文の点検、書き出す前の状態宣言、点検完了の定義、測れるものは機械に任せる分業 |
| [japanese-creative-writing](https://github.com/tanaka-naoki/japanese-creative-writing) | tanaka-naoki | MIT | モード分け、怪談の作法 |
| [novel2hermes_jp](https://github.com/kgmkm/novel2hermes_jp) | kgmkm | MIT | 執筆前後の三段検証、人物の知識状態、世界制約リスト、感情強度の設計、レビュー指摘の必須要件 |
| [novel2agent-jp](https://github.com/kgmkm/novel2agent-jp) | kgmkm | MIT | proposed / confirmed の二段階確定、決定論的なコンテキストパック、却下案の記録、保存事故の検出 |
| [awesome-novel-agent](https://github.com/modoojunko/awesome-novel-agent) | modoojunko | GPL-3.0 | 場面種別の技法カードと絞り込み、AI 臭の層別整理と境界事例、短編の執筆契約、盲検読者、人物の層設計 |

各作者に感謝する。

## 再配布について

- 文面・構成・例文はすべて書き下ろしで、元資料の文章は含めていない（公開前に、元資料との文字 n-gram の重なりを機械的に確認し、句レベルで一致した数箇所も言い換えた）。学んだのは考え方・手順・分類であり、各資料の著作物そのものは再配布していない。
- このリポジトリには現時点でライセンスを付けていない。再利用の条件はリポジトリの所有者が決める。fork や再配布をする場合は、各資料のライセンス（特に fiction-prose-jp の再配布禁止条項と awesome-novel-agent の GPL-3.0）との関係を、それぞれの責任で確認すること。
- 投稿サイトの記法・字数の数え方・生成 AI 利用の申告ルール、公募の応募規定は変わる。`references/notation.md` の記載は 2026 年 9 月時点の確認内容であり、投稿・応募の前に必ず最新の公式情報を確認すること。
- 生成 AI で書いた本文は、投稿サイトによっては申告が必要で、公募によっては応募対象外になる。利用者自身が各規定に従うこと。
