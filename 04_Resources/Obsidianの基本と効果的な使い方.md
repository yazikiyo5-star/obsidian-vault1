---
created: 2026-05-21
tags: [obsidian, guide, beginner, reference]
---

# Obsidian の基本と効果的な使い方

> このVault（Obsidian × Hermes）を使いこなすための初心者向けガイド。

---

## 1. Obsidian の基本概念

### ノート（Note）

Obsidian のノートは、拡張子が `.md` の**Markdownファイル**。Markdown とは、`# 見出し` や `- リスト` のように、テキストに簡単な記号を付けて構造を作る書式のこと。特別なソフトがなくてもテキストエディタで読み書きでき、将来 Obsidian をやめても**データが手元に残る**のが最大のメリット。

### Vault（ボルト）

Vault は「ノートを入れるフォルダ」のこと。Obsidian は PC 上の1つのフォルダを Vault として認識し、その中の `.md` ファイルをすべてノートとして扱う。クラウドに預けるのではなく、**自分の PC のローカルフォルダにデータがある**ので、プライバシーが守られる。

このVaultは以下のフォルダで整理されている（PARA に近い構成）:

| フォルダ | 役割 |
|---|---|
| `00_Meta/` | Vault 自体の運用ルールやメモ |
| `01_Daily/` | 日次ノート（毎日の記録） |
| `02_Inbox/` | とりあえず投げ込む場所。あとで分類する |
| `03_Projects/` | 進行中のプロジェクト |
| `04_Resources/` | 永続的に参照する知識ベース（このノートもここ） |
| `05_Clippings/` | Web Clipper で取り込んだ外部記事 |
| `05_Tasks/` | Hermes エージェントへのタスク指示書 |
| `06_Templates/` | テンプレート置き場 |
| `99_Archive/` | 完了・古くなったものの保管 |

### 内部リンク（Internal Link）

Obsidian 最大の武器。ノートの中で `[[ノート名]]` と書くと、別のノートへのリンクが作られる。

- `[[2026-05-21]]` → その日のデイリーノートにジャンプ
- `[[Obsidianの基本と効果的な使い方]]` → このノート自身へのリンク

リンクを貼ると、リンク先のノートには**バックリンク**（「どこからリンクされているか」）が自動で表示される。これにより、ノート同士が網のようにつながっていく。タグ検索だけでは見つけられない「関連」を発見できる仕組み。

### Properties / Frontmatter（プロパティ）

ノートの冒頭に `---` で囲んだ YAML ブロックを書くと、そのノートに**メタデータ**（追加情報）を付けられる。

```yaml
---
created: 2026-05-21
tags: [obsidian, guide]
---
```

- `tags:` — ノートの分類ラベル。`#obsidian` のようにノート本文中に書くこともできるが、frontmatter にまとめると管理しやすい
- `created:` — 作成日。あとで Dataview プラグインで一覧を作るときに便利
- 自由に項目を追加可能（`author:`, `status:`, `priority:` など）

このVaultのテンプレートでは、ノート新規作成時に frontmatter が自動挿入される。

---

## 2. このVaultで使われている主要プラグイン

Obsidian は「コアプラグイン」（公式機能）と「コミュニティプラグイン」（有志開発）の2層構造。このVaultで有効になっているコミュニティプラグインを解説する。

### Templater（テンプレーター）

**役割**: ノート作成時にテンプレートを自動適用する。Obsidian 標準のテンプレート機能よりはるかに高機能で、JavaScript を使った動的な値の挿入ができる。

**このVaultでの使われ方**:
- `06_Templates/Daily.md` — デイリーノート作成時に日付を自動入力し、前日・翌日へのリンクも自動生成
- `06_Templates/Task.md` — タスク名を聞いてファイル名を設定し、`05_Tasks/inbox/` に移動
- `<% tp.date.now("YYYY-MM-DD") %>` のような構文が Templater の記法

**注意**: テンプレートファイル（`06_Templates/` 内）の `<% ... %>` 構文は壊さないこと。展開済みのノートには展開後の値だけが入る。

### Dataview（データビュー）

**役割**: Vault 内のノートを**データベースのように検索・集計**して、ノート上に表やリストとして表示する。

**使用例**:
```dataview
TABLE created, tags FROM "04_Resources"
SORT created DESC
```
↑ このように書くと、`04_Resources/` フォルダ内のノートを作成日順に一覧表示できる。frontmatter の情報を読み取って表にしてくれる。

**初心者向けポイント**: 最初は読むだけでOK。慣れてきたら `LIST`, `TABLE`, `TASK` クエリを試してみよう。

### Periodic Notes（ペリオディック・ノーツ）

**役割**: デイリーノート（日次）・ウィークリーノート（週次）・マンスリーノート（月次）を一元管理するプラグイン。

**このVaultでの設定**:
- デイリーノートは `01_Daily/YYYY-MM-DD.md` 形式
- テンプレートは `06_Templates/Daily.md` が自動適用される
- **手動でファイル名を変えない**こと（プラグインが認識しなくなる）

### Calendar（カレンダー）

**役割**: サイドバーにカレンダーを表示し、日付をクリックするだけでその日のデイリーノートを開ける（なければ新規作成）。Periodic Notes と連携して動く。

### Copilot / Smart Composer（AI チャット）

**役割**: Vault 内のノートに対して AI に質問したり、文章の要約・翻訳・校正を依頼できるプラグイン。

**このVaultでの構成**:
- **Copilot**: チャット形式で AI と対話。ノートの内容を文脈として渡せる
- **Smart Composer**: ノートの文脈を理解した上で、inline でテキストを書いたり編集できる。セマンティック検索（意味的な検索）にも対応
- どちらも **Ollama（ローカルLLMサーバ）** を使うので、ノートの内容が外部に送信されない
- モデルは `hermes3:8b`、埋め込みモデルは `nomic-embed-text`

**使い方の例**:
1. サイドバーの Copilot アイコンを開く
2. 「このノートを要約して」「英語に翻訳して」などと入力
3. `copilot/copilot-custom-prompts/` にカスタムプロンプトが用意されている（Summarize, Simplify, Make shorter など）

### Obsidian Git（ギット連携）

**役割**: Vault を Git で**自動バックアップ**する。一定間隔で commit & push してくれるので、変更履歴が残り、誤って消しても復元できる。

**このVaultでの動作**: コミットメッセージは `vault backup: YYYY-MM-DD HH:mm:ss` 形式で自動生成されている。

**安心ポイント**: 基本的に裏で自動実行されるので、意識せずにバックアップが取られている。

### その他のプラグイン

| プラグイン | 一言説明 |
|---|---|
| **QuickAdd** | ワンアクションでノート作成やテンプレート適用ができるランチャー |
| **Omnisearch** | Vault 全体の高速全文検索。標準検索より賢い |
| **Obsidian Web Clipper** | ブラウザから Web ページを Vault にクリップ保存 |
| **Shell Commands** | Obsidian 内からターミナルコマンドを実行（上級者向け） |

---

## 3. 日々の運用で効く操作

### コマンドパレット（`Cmd + P`）

Obsidian のほぼすべての操作は、ここから実行できる。メニューを探す必要がない。

- `Cmd + P` を押して、やりたいことをキーワードで入力
- 例: `daily` と打つ → 「Periodic Notes: Open daily note」が出てくる
- 例: `template` と打つ → Templater のコマンド一覧が出る

**最重要ショートカット。これだけ覚えればほぼ困らない。**

### クイックスイッチャー（`Cmd + O`）

ノートを素早く開くための検索窓。

- `Cmd + O` でファイル名を部分一致で検索
- ノートが増えてきても、フォルダを辿る必要がなくなる
- 日次ノートも `2026-05` と打てば候補が出る

### 検索（`Cmd + Shift + F`）

Vault 全体のノート本文を検索する。

- ファイル名だけでなく、**ノートの中身**もヒットする
- `tag:#daily` でタグ検索、`path:04_Resources` でフォルダ限定検索も可能
- Omnisearch プラグインが入っているので、さらに賢い検索も利用できる

### デイリーノート（`Cmd + Shift + D` または Calendar クリック）

毎日の作業記録・メモ・思いつきを記録する場所。

- サイドバーの Calendar で日付をクリック → その日のノートが開く（なければ自動作成）
- テンプレートが自動適用され、「きょうの3つ」「ログ」「アイデア / 学び」のセクションが用意される
- 前日・翌日へのリンクも自動生成される

**おすすめの使い方**: 朝に「きょうの3つ」を埋め、日中はログに追記、夕方に振り返り。

### よく使うショートカットまとめ

| キー | 動作 |
|---|---|
| `Cmd + P` | コマンドパレット |
| `Cmd + O` | クイックスイッチャー |
| `Cmd + Shift + F` | Vault 全体検索 |
| `Cmd + N` | 新規ノート作成 |
| `Cmd + E` | 編集モード / プレビューモード切替 |
| `Cmd + Click` | リンク先を新しいタブで開く |
| `[[` | 内部リンクの入力を開始 |

※ Mac の場合。Windows では `Cmd` を `Ctrl` に読み替え。

---

## 4. 初心者がつまずきやすい点と対処

### 「フォルダ分けに悩んで手が止まる」

**対処**: 迷ったら `02_Inbox/` に放り込む。このVaultでは Inbox → 適切なフォルダへの整理、という流れが想定されている。最初から完璧に分類する必要はない。

### 「リンクの貼り方がわからない」

**対処**: ノート本文で `[[` を打つと候補が出る。まずは関連しそうなノートの名前を選ぶだけ。リンクは後から消せるし、貼りすぎても壊れない。

### 「テンプレートが動かない / 変な文字が出る」

**対処**: `<% tp.* %>` はTemplater の構文で、テンプレート適用時に展開される。ノートを新規作成するときに Templater 経由で作成すれば正しく動く。手でコピペすると構文がそのまま残ってしまう。

**正しい手順**: `Cmd + P` → `Templater: Create new note from template` → テンプレートを選択

### 「Properties / Frontmatter を壊してしまう」

**対処**: ノート冒頭の `---` で囲まれたブロックは YAML 形式。インデントや記号がずれると壊れる。Obsidian の Properties ビュー（ノート上部の UI）から編集すれば構文エラーを避けられる。

### 「グラフビューが複雑すぎて使えない」

**対処**: 全体グラフより**ローカルグラフ**（今開いているノートの周辺だけ表示）の方が実用的。右サイドバーの「ローカルグラフ」で確認できる。全体グラフは「眺めて楽しむもの」くらいの気持ちで。

### 「プラグインが多すぎて混乱する」

**対処**: まずはこの3つだけ意識すれば十分:
1. **Calendar + Periodic Notes** → デイリーノートを開く
2. **コマンドパレット**（`Cmd + P`） → 何でもここから
3. **クイックスイッチャー**（`Cmd + O`） → ノートを開く

他のプラグインは裏で動いているので、最初は存在を忘れていい。

### 「AI（Copilot / Smart Composer）が応答しない」

**対処**: Ollama が起動しているか確認する。ターミナルで以下を実行:
```bash
curl -s http://localhost:11434/api/tags
```
モデル一覧が返ってくれば Ollama は稼働中。返ってこなければ `ollama serve` で起動する。

### 「Git がよくわからない、怖い」

**対処**: Obsidian Git プラグインが自動でバックアップしてくれているので、基本的に何もしなくて良い。万が一ファイルを消してしまった場合は、コマンドパレットから `Obsidian Git: Open History` で履歴を確認できる。

---

## 5. このVaultの運用フロー（まとめ）

```
朝: Calendar でデイリーノートを開く → 「きょうの3つ」を書く
　↓
日中: 思いついたことは 02_Inbox/ に投げ込む or デイリーノートに追記
　↓
　　 調べ物の成果 → 04_Resources/ に保存
　　 タスク指示 → 05_Tasks/ にタスクノートを作成
　↓
夕方: デイリーノートを振り返り、Inbox を整理
　↓
裏側: Obsidian Git が自動で commit & push（バックアップ）
```

---

## 参考リンク

- [Obsidian 公式ヘルプ - Quick Switcher](https://help.obsidian.md/plugins/quick-switcher)
- [Obsidian Getting Started: Complete Beginner Guide (2026)](https://productivitystack.io/guides/obsidian-getting-started/)
- [The Ultimate Beginner's Guide to Obsidian](https://www.dsebastien.net/the-ultimate-beginners-guide-to-obsidian/)
- [Obsidian + Periodic Notes + Templater セットアップ](https://kevinquinn.fun/blog/get-started-with-obsidian-periodic-notes-and-templater/)
- [Obsidian Git - GitHub](https://github.com/Vinzent03/obsidian-git)
- [Smart Composer - GitHub](https://github.com/glowingjade/obsidian-smart-composer)
- [Copilot for Obsidian - ローカルLLM設定](https://github.com/logancyang/obsidian-copilot/blob/master/local_copilot.md)
