---
created: 2026-06-18
status: review-required
goal: Ghost Printer プロジェクトの停滞を解消し、次の実機作業を着手可能な単位に分解する
priority: normal
delegate: claude（計画）→ ユーザ（実機作業）
source: 02_Inbox/Ghost Printer メモ.md（次アクション欄が空のまま）
tags: [task, draft, ghost-printer]
---

# 進行可能タスク：Ghost Printer 次手の分解

> ⚠️ 確認待ちドラフト。Inboxメモの「次のアクション」が空欄で停滞中。下記を着手単位に割った。

## 現状（メモより）
Ghost Printer = 日常データを SOUL フォーマットに蓄積する個人用ウェアラブル。Pre-MVP 完了。次は **Orange Pi 3B 実機セットアップ** と **COLMI R02 リング連携**。プロジェクト一式は `04_Resources/ghost-printer/`。

## 着手可能なネクストアクション（提案）
1. **Orange Pi 3B 初期セットアップ手順書を作る**（OS焼き込み→SSH→必要パッケージ）。delegate: claude が手順ドラフト → ユーザが実機実行
2. **COLMI R02 リングの連携可否を調査**（BLE仕様・既存OSSライブラリの有無）。delegate: claude（WebSearch）
3. **SOULフォーマットへのデータ書き込みI/Fを定義**（リング→Orange Pi→SOUL.md の流れ）。delegate: claude（設計ドラフト）

## 受け入れ条件
- [ ] 各アクションが「30分以内に着手できる」粒度になっている
- [ ] 成果物は `04_Resources/ghost-printer/` 配下に保存
- [ ] 実機が必要な手順はユーザ作業として明記

## 触ってOKな範囲
- WebSearch / WebFetch
- `04_Resources/ghost-printer/` のファイル作成
- ❌ `02_Inbox/Ghost Printer メモ.md` 本体は無断で書き換えない

## 実行コマンド
**Cowork で私に:**
> 「Ghost Printer draft の ②COLMI R02 連携調査を実行して」（まず軽い調査タスクから）
