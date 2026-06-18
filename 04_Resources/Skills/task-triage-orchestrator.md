---
created: 2026-06-18
skill: Task-Triage-Orchestrator
version: 0.1.0
tags: [skill, orchestrator, tasks, hermes, kimi]
delegate: claude
---

# Skill: Task-Triage-Orchestrator v0.1.0

個人用タスクの取得→トリアージ→ネクストアクション生成→「進行可能MDファイル化」→（確認後）進行、までを回す Orchestrator スキル。Claude が指揮・検証、重い処理は Kimi/Hermes に委譲する想定。

## 入力 Spec
- **intake元**: `02_Inbox/*.md`, `05_Tasks/inbox/*.md`, `05_Tasks/*.md`, またはチャットで口頭。
- **各タスクに必要な判定軸**: `分類(note/reference/task)` / `priority(high|normal|low)` / `delegate(claude|kimi|hermes3|auto)` / `次アクション` / `blocked基準該当か`。

## 使用プロンプト（トリアージ）
```
次のノート群を読み、各々について JSON で
{file, 種別, priority, delegate, 次アクション, blocked理由(あれば)} を返せ。
判定ルール: 中身が空/重複/無題 → cleanup候補。
実Web調査や生成が要る → delegate=kimi。設計・計画 → delegate=claude。
法的/金銭/個人情報 → blocked。
```

## 出力形式
1. **トリアージ表**（チャット）: file / 分類 / priority / delegate / 次アクション。
2. **進行可能MDファイル**: `05_Tasks/draft/_review-<slug>.md`。frontmatter(`status: review-required`, `goal`, `priority`, `delegate`, `source`) + 確定Spec + 受け入れ条件 + 触ってOK範囲 + 実行コマンド。
3. ユーザ確認後、`05_Tasks/inbox/` へ移動して進行（Hermes監視 or Cowork/Claude Code 実行）。

## 検証ルール（ユーザ指摘から蓄積）
- [ ] 新規ノートに YAML frontmatter（`created:`, `tags:`）必須。
- [ ] ユーザの既存ノートを無断で書き換えない。新規は範囲を最小に。
- [ ] `.command` / `.obsidian/` / `.smtcmp_*` には触らない。
- [ ] Vault の git はサンドボックスから commit しない。
- [ ] 出典が要るタスクは URL 必須・両論併記。
- [ ] 「進行可能MD」は status: review-required で作り、勝手に実行しない（ユーザ確認が先）。
- [ ] ローカル hermes3:8b は実Web調査・特定が弱い → 調査系は Kimi/Claude へ委譲（Arendt例で確認済）。

## 再利用コマンド（Hermes / Cowork 用）
**Cowork（このチャット）:**
> 「Task-Triage-Orchestrator を実行。intake=02_Inbox と 05_Tasks/inbox。」

**Hermes ヘッドレス（Kimi委譲・コピペ用）:**
```
hermes -z "Vaultの 02_Inbox と 05_Tasks/inbox の*.mdを読み、各タスクをトリアージ(分類/priority/delegate/次アクション)してJSONで返せ。空/重複はcleanup候補。"
```

## 変更履歴
- v0.1.0 (2026-06-18): 初版。Inbox(4件)+05_Tasks をトリアージし、Arendt/Ghost Printer の進行可能MDを生成して確立。
