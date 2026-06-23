---
created: 2026-06-18
status: review-required
goal: ハンナ・アーレントの仕事・価値観を、哲学の予備知識ゼロでも読めるレポートにまとめる
priority: high
delegate: claude（deep-research）/ Kimi 併用可
source: 05_Tasks/ハンナアーレントについてのリサーチ.md（hermes3:8b で BLOCKED）
tags: [task, draft, research]
---

# 進行可能タスク：ハンナ・アーレント リサーチ

> ⚠️ これは **確認待ちドラフト**。内容OKなら `05_Tasks/inbox/` へ移すか、Cowork/Claude Code で下記コマンドを実行して進行する。

## なぜ止まっていたか
ローカル `hermes3:8b` は実Web調査ができず、対象の人物特定すらできずに質問返し（BLOCKED）になっていた。→ 実Web検索が可能なエンジン（Claude deep-research / Kimi）に委譲すれば解消する。

## やってほしいこと（確定Spec）
ハンナ・アーレント（1906–1975, 政治哲学者）について、一次情報・信頼できる二次情報をもとに調べ、**哲学の知識がない人でも読める日本語レポート**を作る。

含める観点:
1. 何をした人か（生涯・亡命・米国での活動を3〜4文で）
2. 主要著作と各1行要約（『全体主義の起源』『人間の条件』『エルサレムのアイヒマン』など）
3. 中心概念をかみ砕いて（「悪の凡庸さ」「活動 vs 労働 vs 仕事」「公共性」）
4. 現代へのつながり（なぜ今も読まれるか）
5. もっと知るための入口（入門書・講演・映画）

## 受け入れ条件
- [ ] 専門用語は必ず日常語の言い換えを併記
- [ ] 主張には出典URLを付ける（最低5ソース、うち学術・百科系を1つ以上）
- [ ] 「悪の凡庸さ」の通説と近年の批判（資料の再検討）の両論を併記
- [ ] レポートを `04_Resources/research/hannah-arendt/report.md` に保存（frontmatter付き）

## 触ってOKな範囲
- WebSearch / WebFetch
- `04_Resources/research/hannah-arendt/` のフォルダ・ファイル作成
- ❌ 既存ユーザノートの書き換えはしない

## 実行コマンド（どれか1つ）
**A. Cowork（このチャット）で私が直接:**
> 「draftのアーレント・リサーチを deep-research で実行して」

**B. Hermes 経由で Kimi に投げる（コピペ用・Kimi MCP不要）:**
```
hermes -z "次のSpecで日本語リサーチレポートを作成。出典URL必須・専門用語は言い換え併記。観点=生涯/主要著作/中心概念(悪の凡庸さ,活動と労働の区別,公共性)/現代的意義/入門ガイド。対象=Hannah Arendt。出力はMarkdownのみ。"
```
→ 出力を私（Claude）に貼れば検証して `report.md` に整える。

B