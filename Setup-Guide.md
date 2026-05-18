---
type: guide
created: 2026-05-17
tags: [setup, hermes, obsidian]
---

# 🛠️ Obsidian × Hermes セットアップガイド (2026年5月版)

このVaultを最大限活かすための、**Mac環境前提**の手順をまとめたものです。
`install.command` を一度実行すれば 90% は終わっていますが、残り 10% は Obsidian 内のクリック作業になります。

---

## 1. なぜこの構成なのか — Xから読み取った2026年5月時点のベストプラクティス

Nous Research が 2026-05-16 に出した **Hermes Agent v0.14.0「Foundation Release」** を起点に、
Obsidian コミュニティが収束しつつあるパターンは次の通り：

| レイヤ | 採用したもの | 理由 |
| --- | --- | --- |
| **モデルホスト** | **Ollama** (`localhost:11434`) | Hermes Agent も Obsidian プラグインも OpenAI互換エンドポイントを欲しがる。Ollama がデファクト |
| **モデル本体** | **`hermes3:8b`** (フォールバックで `nous-hermes2`) | Apple Silicon の MBP でも実用速度。Hermes 系は tool calling 対応 |
| **埋め込み** | `nomic-embed-text` | Copilot / Smart Composer が RAG で使う標準 |
| **Obsidian側 AIチャット** | **Copilot (Logan Yang)** ＋ **Smart Composer** | Copilot は CORS / `Relevant Notes` が安定、Smart Composer は vault-aware RAG が強力。**両方入れて使い分け**が現状ベスト |
| **Hermes自体の管理** | **Hermes Agent CLI v0.14+** (`hermes` コマンド) | Karpathy の LLM-Wiki スキルが内蔵されており、Vault 内へ知識ベースを自動構築できる |
| **PKM基盤** | Calendar / Periodic Notes / Templater / Dataview / QuickAdd | 「Power user 2026 essentials」として最頻出 |
| **独自プロジェクト連携** | **Shell commands** + Templater | Vault からスクリプトを叩く・コマンド結果をノートに貼る、の決定版 |

> 出典のXポストはこのファイル末尾の「参考リンク」セクションに。

---

## 2. install.command を実行したあとに残るクリック作業

`install.command` で **Obsidian / Ollama / Hermes Agent / モデル pull / CORS設定** はすべて済みます。残るのは Obsidian 内：

### 2-1. Community plugins を有効化

1. Obsidian 起動 → 左下歯車 → **Community plugins**
2. **Turn on community plugins** をクリック（Restricted mode を解除）
3. **Browse** から下記を **Install → Enable** の順で入れる
   - Templater
   - Dataview
   - Calendar
   - Periodic Notes
   - Copilot
   - Smart Composer
   - Obsidian Git
   - Shell commands
   - QuickAdd
   - Omnisearch
   - Obsidian Web Clipper

> Vault の `.obsidian/community-plugins.json` には既にこのリストが書かれているので、
> 各プラグインの **Install** さえすれば自動で **Enabled** になります。

### 2-2. Copilot 設定

Settings → **Copilot** タブ：

| 項目 | 値 |
| --- | --- |
| Default Model Provider | `Ollama` |
| Base URL | `http://localhost:11434` |
| Chat Model | `hermes3:8b` |
| Embedding Provider | `Ollama` |
| Embedding Model | `nomic-embed-text` |
| Default Mode | `Vault QA (RAG)` |

### 2-3. Smart Composer 設定

Settings → **Smart Composer** タブ：

1. **Chat Models** → Add custom model:
   - Provider: `Ollama`
   - Model ID: `hermes3:8b`
   - Base URL: `http://localhost:11434`
2. **Embedding Models** → Add custom model:
   - Provider: `Ollama`
   - Model ID: `nomic-embed-text`
3. **RAG** → Build vector index ボタンを押す（Vault の規模次第で数分）

### 2-4. Templater

Settings → **Templater**：

- Template folder location: `06_Templates`
- Trigger Templater on new file creation: **ON**
- Folder Templates:
   - `01_Daily` → `06_Templates/Daily.md`
   - `03_Projects` → `06_Templates/Project.md`

### 2-5. Daily Notes / Periodic Notes

Settings → **Daily notes**: (`install.command` で `.obsidian/daily-notes.json` 設定済み)
- Date format: `YYYY-MM-DD`
- New file location: `01_Daily`
- Template: `06_Templates/Daily`

---

## 3. Hermes Agent との連携 3パターン

### パターンA: Obsidian プラグインから Hermes モデルを呼ぶ（ライト）

`Copilot` / `Smart Composer` 経由で `hermes3:8b` を叩くだけ。最も低摩擦。
**用途**: ノート要約、Q&A、文章リライト、RAG検索

### パターンB: Hermes Agent CLI から Vault に書き込ませる（パワー）

ターミナルで `hermes` を起動し、設定でこの Vault のパスを指定。

```bash
hermes
# 起動後の初期セットアップで Obsidian vault path を聞かれたら:
#   /Users/haru/Documents/Claude/Projects/obsidian,hermes/Vault
# を入力
```

Hermes 内蔵の **LLM-Wiki スキル**（Karpathy 氏寄贈, X上で発表済）を使うと、
任意のトピックを指示するだけで Vault 内に複数の Markdown ノートとして
研究ノートを生成してくれる。

**用途**: 「OAuth 2.1 と PKCE について Wiki を作って」のような長期作業

### パターンC: Hermes Kanban Bridge で Vault がプロジェクト管理ボードになる（実験的）

X上で Shane (@ShaneRobinett) が公開している `hermes-kanban-bridge v1.5.0+` を入れると、
Vault の `03_Projects/` を Hermes Agent が自律的に動かすボードに変える。

**入れ方** (任意・上級者向け):
```bash
hermes plugin install hermes-kanban-bridge
hermes kanban init --vault "$HOME/Documents/Claude/Projects/obsidian,hermes/Vault"
```

---

## 4. 同期戦略 (重要)

複数デバイス利用や事故対策のため、**いずれか1つ**は選んで下さい。

| 選択肢 | 速度 | コスト | 強み | 弱み |
| --- | --- | --- | --- | --- |
| **Obsidian Git** (推奨) | ◯ | 無料 (GitHub 無料枠で十分) | 履歴が残る／衝突解決が明示的／CI とも連携できる | Git の学習コストが少しある |
| Obsidian Sync (公式) | ◎ | 有料 ($4-10/月) | E2E暗号化／設定ゼロ／モバイルもシームレス | 課金 |
| iCloud Drive | △ | 無料 | 一見ラク | **公式は非推奨** (キャッシュ更新でファイル消失事例あり) |

### 推奨セットアップ (Obsidian Git)

1. GitHub で private リポジトリを作る（例: `obsidian-vault`）
2. このVaultフォルダで:
   ```bash
   cd "/Users/haru/Documents/Claude/Projects/obsidian,hermes/Vault"
   git init && git branch -M main
   git remote add origin git@github.com:<your-account>/obsidian-vault.git
   ```
3. Obsidian Git プラグイン設定で：
   - Vault backup interval: `10 minutes`
   - Auto pull interval: `10 minutes`
   - Commit message: `vault: {{date}} - {{numFiles}} files`

`.gitignore` は次の節を参照。

---

## 5. 独自プロジェクト・開発物の自動実行（Shell commands プラグイン）

このVault から ターミナル経由で自前のスクリプトを叩く構成。
**「Vault がランチャー」**になり、ノート上でコマンドの結果が確認できます。

### 5-1. プラグイン: Shell commands

Settings → **Shell commands** → **New shell command** で、たとえば：

| Alias | Command | Output to |
| --- | --- | --- |
| Run Hermes brief on current note | `hermes run --skill summarize --input "{{file_path}}"` | Open in new note |
| Pull latest models | `ollama pull hermes3:8b && ollama pull nomic-embed-text` | Notification |
| Build vault stats | `find "{{vault_path}}" -name "*.md" \| wc -l` | Status bar |
| Run my project | `cd "{{vault_path}}/../my-project" && ./run.sh` | Open in modal |

### 5-2. QuickAdd で「定型操作1クリック化」

Settings → **QuickAdd** → New Macro：

- Macro名: `Hermes Daily Summary`
- Action 1: `Run Shell command` → `hermes run --skill summarize-folder --input "{{vault_path}}/01_Daily/$(date +%Y-%m-%d).md"`
- Action 2: `Capture` → Create new note in `00_Meta/Summaries/` with the output

これを **Cmd+Shift+S** あたりにバインドすれば、その日のDaily Noteを毎晩自動要約する流れが作れます。

---

## 6. .gitignore（Git同期する場合）

Vault 直下に `.gitignore` を置いて以下を除外してください（既にこのVaultに同梱）:

```
.obsidian/workspace.json
.obsidian/workspace-mobile.json
.trash/
.DS_Store
*.swp
.obsidian/plugins/*/data.json
```

---

## 7. Web 情報のクリッピング

**Obsidian Web Clipper**（公式の拡張機能, 2025年公開）が現状ベスト：

1. [Chrome拡張ストア](https://chrome.google.com/webstore/detail/obsidian-web-clipper/) または Firefox からインストール
2. 拡張機能の設定で **Vault**: `Vault` を選び、**Default folder**: `05_Clippings`
3. 任意のWebページで拡張アイコンをクリック → Markdown化されて Vault に保存

---

## 8. 動作確認チェックリスト

- [ ] `/Applications/Obsidian.app` がある
- [ ] Obsidian 起動 → このVaultが開いている
- [ ] `curl -s http://localhost:11434/api/tags` がモデルを返す
- [ ] `ollama list` に `hermes3:8b` と `nomic-embed-text` がある
- [ ] `hermes --version` が `0.14.x` 以上
- [ ] Copilot Chat で「こんにちは」と打ってHermesが日本語で返す
- [ ] Daily Notes アイコンを押すと `01_Daily/2026-MM-DD.md` がテンプレ適用済で開く

---

## 9. トラブルシュート

| 症状 | 対処 |
| --- | --- |
| Copilot が CORS エラー | `OLLAMA_ORIGINS` が反映されていない。`launchctl setenv OLLAMA_ORIGINS "app://obsidian.md*"` を一度実行し、Ollama を再起動 |
| `ollama: command not found` | GUI 版 Ollama を起動して、メニューバーアイコンから "Install CLI" を実行 |
| Hermes が `404 model not found` | `ollama pull hermes3:8b` を実行。Hermes Agent 側の設定で provider を Ollama にし base_url を `http://localhost:11434/v1` (OpenAI互換) に |
| Smart Composer の vector index が止まる | Vault が大きい場合は `04_Resources/` 配下のサブセットだけインデックス対象に絞る |
| Templater がテンプレを差し込まない | Settings → Templater → Folder Templates を再設定 |

---

## 10. 参考リンク (X / 公式)

- [Nous Research — Foundation Release (v0.14, 2026-05-16)](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.5.16)
- [Hermes Agent docs — Installation](https://hermes-agent.nousresearch.com/docs/getting-started/installation)
- [Hermes Agent docs — Providers (Ollama含む)](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [Teknium on X: LLM-Wiki スキル内蔵で Obsidian 連携が公式化](https://x.com/Teknium/status/2041370915012071577)
- [Julian Goldie SEO on X: Hermes + Obsidian セットアップ手順 (X要約)](https://x.com/JulianGoldieSEO/status/2047699587788361844)
- [Shane on X: Hermes Kanban Bridge v1.5.0 (Vault がプロジェクト管理ボード化)](https://x.com/ShaneRobinett/status/2047692184518787185)
- [Obsidian Copilot (Logan Yang) — Local LLM](https://github.com/logancyang/obsidian-copilot/blob/master/local_copilot.md)
- [Smart Composer (glowingjade)](https://github.com/glowingjade/obsidian-smart-composer)
- [Top Obsidian Plugins in 2026 (Obsibrain)](https://www.obsibrain.com/blog/top-obsidian-plugins-in-2026-the-essential-list-for-power-users)
- [Sébastien Dubois — Must-have Obsidian plugins 2026](https://www.dsebastien.net/the-must-have-obsidian-plugins-for-2026/)
