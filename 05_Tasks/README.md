---
type: meta
tags: [tasks, hermes, workflow]
---

# 🤖 Hermes Autonomous Task Queue

Hermes Agent が常駐してこのフォルダを監視し、勝手に仕事を進めるための置き場です。

## フォルダの役割

| フォルダ | 役割 | あなたの操作 | Hermesの操作 |
| --- | --- | --- | --- |
| **`inbox/`** | やってほしいことを置く場所 | ✏️ 新規 `*.md` を書いて置く | 👁️ 1分ごとに監視、見つけたら `active/` へ移動 |
| **`active/`** | Hermesが現在着手中 | 触らない（覗くのは可） | ✍️ 作業中。途中経過もここに書く |
| **`done/`** | 完了 | 確認＆レビュー → archive へ移動 | ✅ 完了したら移動 |
| **`blocked/`** | あなたの判断が要る | 📥 ノート開いて回答 → `inbox/` に戻す or 直接 `active/` に書き戻す | 🚨 macOS通知発火 |

## inbox に投げるノートの書き方（最小）

````markdown
---
goal: 1行で何をしてほしいか
priority: high | normal | low  
deadline: 2026-05-20  # 任意
delegate: claude-sonnet | hermes3 | auto  # 任意。autoならHermesが判断
---

# やってほしいこと
詳しい背景・受け入れ条件・参考リンクをここに自由に書く

## 受け入れ条件
- [ ] X が満たされる
- [ ] Y が成果物として `04_Resources/...` に保存される

## 触ってOKな範囲
- 04_Resources/ 配下のファイル作成
- WebSearch / WebFetch
- ❌ 03_Projects/secret は触らない

## 質問していい範囲
- 5分以上止まったらblocked化
- 法的判断・お金関係の決定は必ず blocked
````

## 中身のサンプル

ファイル名: `inbox/2026-05-18-summarize-x-thread.md`

````markdown
---
goal: X(Twitter)の特定スレッドを要約してVaultに保存
priority: normal
delegate: auto
---

URL: https://x.com/Teknium/status/...
保存先: 04_Resources/X-research/

要約フォーマットは Hermes-Brief.md テンプレに従う。
````

これだけ。あとは Hermes が active へ移し、Web Fetch → 要約 → 保存 → done に移動 → Git push、まで自動。

## blocked になったら

macOS通知センターに「**Hermesが質問中**」とバナーが出ます。クリックでVaultの該当ノートが開く。
ノート内の `## 質問` セクションに答えを書いて、ファイルを **inbox/ に移動** すれば再開。
