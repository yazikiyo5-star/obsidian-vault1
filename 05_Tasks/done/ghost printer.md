---
goal: ghost printerというプロジェクトファイルがこのMAC上にあります。それをオブシディアン上コピーしてください。Cloudeと作業しているのでそれも忘れずに
priority: normal
delegate: claude
created: 2026-05-18 18:39
tags:
  - task
---

# Untitled

## やってほしいこと



## 受け入れ条件
- [ ] 
- [ ] 

## 触ってOKな範囲
- WebSearch / WebFetch
- `04_Resources/` 配下のフォルダ作成・ファイル作成

## blocked基準
- 法的判断・お金関係の決定は必ずblocked
- 個人情報を含む処理はblocked

---
## 🤖 Hermes Response (2026-05-18 18:43 via claude)

STATUS: DONE

## 完了報告

### 変更内容

`/Users/haru/Documents/Claude/Projects/Ghost-print/` にあった Ghost-Printer プロジェクトを Vault にコピーしました。

### 変更ファイル一覧

| 操作 | パス | 説明 |
|------|------|------|
| 新規作成 | `04_Resources/ghost-printer/` | プロジェクト一式 (87ファイル, 1.5MB) |
| 新規作成 | `04_Resources/ghost-printer/Ghost-Printer.md` | プロジェクト概要ノート (YAML frontmatter付き) |

### 含まれるもの

- **ソースコード**: `ghost-printer-a1/` 配下の Python コード全体 (main.py, soul_binary.py, watchpoint.py 等)
- **仕様書**: `specs/` 配下 (a6, a7, b1, b2, b4, c1, c6)
- **ドキュメント**: HANDOFF.md, ROADMAP.md, SETUP_OPI3B.md, PROGRESS_REPORT.md
- **データ**: CORTEX.bin, soul.json 等
- **テスト**: test_*.py (343件分)
- **ティーザー素材**: `teaser/` 配下
- **Claude との作業履歴**: 概要ノートに「Claude Code との協同開発」のセクションを記載

### 影響範囲

`04_Resources/ghost-printer/` のみ。既存ファイルへの変更なし。`__pycache__` と `.pytest_cache` はコピーから除外済み。

ghost printerの情報を簡単に教えてください。

---
## 🤖 Hermes Response (2026-05-18 19:08 via claude)

STATUS: DONE

## Ghost Printer 概要

**Ghost Printer** は、日常のデータを独自の「SOUL フォーマット」に変換・蓄積し、AI が読み込みやすい形で保持するパーソナルデバイスプロジェクトです。

### 一言で言うと
> AIとの対話を「毎回初対面」から「長年の付き合い」に変えるウェアラブルデバイス

### 核心的なアイデア
- ユーザの音声・行動データをデバイス上で**ローカル処理**（クラウド不要）
- **SOUL フォーマット**（バイナリ形式）に変換して蓄積：アイデンティティ、エピソード記憶、興味分布、行動パターンの4層構造
- 誰に・何を・どこまで見せるかを**ユーザが制御**（Permission Gateway）

### 技術スタック
- **ハードウェア**: Orange Pi 3B (RK3566, 4GB) — 小型SoC + BLE 5.0 + MEMSマイク
- **ローカルモデル**: Whisper tiny（音声認識）+ Bonsai 1.7B（意味抽出）+ MiniLM-L6（埋め込み）
- **コード**: Python、テスト343件全通過
- **開発**: Claude Code との協同開発

### 現在の状態
Pre-MVP 完了。ソフトウェア側は主要機能が実装・テスト済み。次は Orange Pi 3B 実機セットアップと COLMI R02 リング（BLE）連携が控えている段階です。
