---
type: meta
created: 2026-05-21
tags: [meta, os, generated]
---

# 07_Generated — AI生成専用エリア

このフォルダは Personal OS（Intelligence Layer）が生成した出力の置き場です。POS仕様の「04 - GENERATED」に相当し、既存の PARA 構成に不足していた役割を補うために新設しました。

## なぜこのフォルダが要るか

`04_Resources/` は永続的に参照する知識ベース、`05_Tasks/done/` は完了タスクの記録です。どちらも「定期的に再生成される OS の出力（朝のブリーフィング・週次レビュー・健康診断）」の置き場には向きません。生成物と知識ベースを混ぜると、古い自動生成物が知識ベースを汚染します。そのため生成物専用エリアを分離します。

## ルール

- **手動編集禁止。** ここのノートは OS / Hermes が再生成する前提です。手で書き換えても次回生成で陳腐化・上書きされます。
- 残したい内容は `04_Resources/`（知識ベース）か該当プロジェクトノートへ **コピー** して移してください。
- 削除は安全です（必要なら再生成されます）。履歴として残したいものは `99_Archive/` へ。

## サブフォルダ（各ワークフローが生成時に自動作成）

| パス | 生成元ワークフロー |
| --- | --- |
| `briefings/YYYY-MM-DD-briefing.md` | Daily Morning Briefing |
| `reviews/YYYY-Www-review.md` | Weekly Review Generator |
| `health/YYYY-MM-DD-health.md` | Project Health Monitor |

ワークフローの定義は [[ワークフロー定義]]、OS全体の運用ルールは [[OS-運用マニュアル]] を参照。
