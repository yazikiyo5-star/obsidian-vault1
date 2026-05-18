---
created: 2026-05-18
tags:
  - project
  - ghost-printer
  - hardware
  - AI
  - soul-format
aliases:
  - Ghost Printer
  - ゴーストプリンター
---

# Ghost-Printer プロジェクト概要

## コンセプト

日常生活から取得した多元的データを **SOUL フォーマット** に変換・蓄積し、AI が読み込みやすい形で整形するパーソナルデバイス。

- AI との対話を「毎回初対面」から「長年の付き合い」に変える
- データはデバイス上でローカル処理・保存（クラウド不要）
- ユーザが「誰に・何を・どこまで」見せるかを選択的に制御
- オフラインでも常時稼働

## ステータス (2026-05-18 時点)

| 領域 | 状態 |
|------|------|
| SOUL 抽出パイプライン (Track A) | ✅ 実装・検証済み |
| Permission Gateway / Capability Token (Track C) | ✅ 実装・検証済み |
| Soul Cortex（3モデル協調） | ✅ 実装・テストパス |
| CORTEX Manager（バイナリ永続化） | ✅ 実装・テストパス |
| Watch Point（観測生態系） | ✅ 実装・テストパス |
| SOUL バイナリフォーマット (Track A6) | ✅ 98 件テスト全通過 |
| 適応型 Watch Point + LLM 健康監視 (Track A7) | ✅ 60 件テスト全通過 |
| HW: Orange Pi 3B 移行 (Track B) | ✅ 仕様完了・実機セットアップ待ち |
| Soul Protocol L1-L5 (Track C6) | ✅ 仕様のみ (実装は MVP β 後) |
| **プロジェクト全体テスト** | **343 件全通過** |

## 開発環境

- **開発ツール**: Claude Code (Anthropic) で全コード・仕様を共同開発
- **ソースコード**: `ghost-printer-a1/` (Python)
- **実機 HW**: Orange Pi 3B (RK3566, 4GB) — 入手済み
- **ターゲット**: Armbian Trixie 26.2.0-trunk.843

## SOUL フォーマット — 4 層構造

```
soul.soul (バイナリ)
├── HEADER              固定長 128B
├── CORE IDENTITY       多次元ガウス分布（128dim）
├── EPISODIC MEMORY     3層 (recent → compressed → distilled)
├── SEMANTIC MAP        興味分布 (Dirichlet 512dim) + 人間関係グラフ
└── TEMPORAL PATTERNS   行動時系列 (HMM + サーカディアンリズム)
```

## ローカルモデルスタック

| 役割 | モデル | サイズ |
|------|--------|--------|
| 音声→テキスト | Whisper tiny | 39 MB |
| 意味抽出 / 重要度判定 | Bonsai 1.7B | 240 MB |
| 埋め込みベクトル生成 | MiniLM-L6 | 22 MB |

## ハードウェア構成

- **Core**: 小型 SoC (BLE 5.0 + I2S MEMS マイク) — 販売品
- **Shell**: OSS / 3D プリンタ対応 (Wearable / Desk / Car / Custom)
- **Soul Dock**: 充電 = 同期 = 認証の据え置きハブ

## ファイル構成

```
ghost-printer/
├── Ghost-Printer.md          ← このノート
├── ghost_printer_handoff.md   ← 初期引き継ぎドキュメント
├── ghost_printer.html         ← プロジェクト HTML
├── device_architecture_diagram.html
├── colmi_r02_setup_guide.md   ← COLMI R02 リングセットアップ
├── soul_wired.html
├── teaser/                    ← ティーザーサイト素材
│   ├── 00_README.md ... 04_mockup.html
│   └── assets/ (SVG)
└── ghost-printer-a1/          ← メインコードベース
    ├── main.py                ← エントリポイント
    ├── soul_binary.py         ← SOUL バイナリ実装
    ├── soul_cortex.py         ← 3モデル協調 Cortex
    ├── permission_gateway.py  ← 選択的自己開示
    ├── watchpoint.py          ← Watch Point 生態系
    ├── watchpoint_llm.py      ← LLM 健康監視
    ├── flash_cortex.py        ← SPI Flash 書込み
    ├── specs/                 ← 仕様書群
    ├── data/                  ← CORTEX.bin, soul.json 等
    ├── test_*.py              ← テスト (343件)
    ├── HANDOFF.md             ← 実機セットアップ引き継ぎ
    ├── ROADMAP.md             ← ロードマップ
    └── SETUP_OPI3B.md         ← OPi 3B セットアップ手順
```

## Claude Code との作業履歴

このプロジェクトは **Claude (Anthropic)** との協同で設計・実装されています。

- 設計議論 → 仕様書 (`specs/*.md`) → コード実装 → テストの全サイクルを Claude Code 上で実施
- Track A (SOUL 抽出) → Track B (HW) → Track C (Permission) → Track A6 (バイナリ) → Track A7 (Watch Point) → Track B2 (OPi 3B 移行) → Track C6 (Soul Protocol) の順で進行
- 全テスト 343 件は Pre-MVP 完了時点で全通過
- 引き継ぎ書 (`HANDOFF.md`) は Claude が作成

## 次のマイルストーン

1. microSD 到着後、OPi 3B で実機セットアップ (Phase 1-9)
2. 24 時間 systemd 常駐稼働の達成
3. COLMI R02 リング到着後の BLE プロトコル検証
4. A8 (Tier 2 = おかしな挙動の検知) の設計

## 関連リンク

- 元ソース: `/Users/haru/Documents/Claude/Projects/Ghost-print/`
- Obsidian Vault コピー: `04_Resources/ghost-printer/`
