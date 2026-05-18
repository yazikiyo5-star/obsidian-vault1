# Welcome to your Obsidian × Hermes Vault

このVaultは **Obsidian + Nous Research Hermes Agent** 連携用に初期化されています。

## このVaultの構成

- `00_Meta/` — Vaultそのものに関するメモ（運用ルール、変更履歴など）
- `01_Daily/` — Daily Notes が日付ごとに自動作成される場所
- `02_Inbox/` — まず投げ込む置き場（あとで分類）
- `03_Projects/` — 進行中のプロジェクトノート
- `04_Resources/` — 知識ベース（永続的に参照するもの）
- `05_Clippings/` — Web Clipperなどから取り込んだ外部記事
- `06_Templates/` — Daily / Project / Meeting / Hermes-Brief テンプレート
- `99_Archive/` — 終わったもの・古くなったものの保管

## Hermes連携のはじめかた

1. ターミナルで `hermes` と打って Hermes Agent を起動
2. 同じターミナルか別タブで `OLLAMA_ORIGINS="app://obsidian.md*" ollama serve` を立てておく（Obsidian側プラグインから叩く用）
3. Obsidianで **Copilot** または **Smart Composer** を有効化（Settings → Community plugins）
4. プラグイン設定で
   - Provider: `Ollama`
   - Base URL: `http://localhost:11434`
   - Model: `hermes3:8b`（または pull 済みの Hermes 系モデル）
   - Embedding model: `nomic-embed-text`
5. `06_Templates/Hermes-Brief.md` を Templater で呼び出して試運転

詳細は **`Setup-Guide.md`**（Vault 直下）を参照。
