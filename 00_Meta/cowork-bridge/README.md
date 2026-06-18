---
created: 2026-06-18
type: meta
tags: [bridge, cowork, hermes, protocol]
---

# Cowork ↔ Hermes ブリッジ

Claude Cowork と Hermes Agent が**同じファイルを並行参照**するための共有置き場。
Cowork の一時作業ファイルはサンドボックス内にありMacから見えないため、**共有したい作業状態は必ずこのフォルダに書き出す**。Hermes はここを読み取り専用で監視する。

## owner / lock 規約（衝突防止）
各ファイルの frontmatter に必ず付ける:

```yaml
---
owner: cowork        # cowork | hermes  ← 今の書き込み主体
status: working      # working | ready | done
updated: 2026-06-18 HH:mm
---
```

ルール:
- **`owner` 以外のエージェントは書き込まない**（読むのは自由）。
- Cowork が作業中は `owner: cowork, status: working`。完了で `status: ready` にして「Hermes が引き取ってよい」を示す。
- Hermes が引き取る時は `owner: hermes` に書き換えてから作業。
- `status: done` で双方とも書き込み終了。

## ファイル命名
`<YYYY-MM-DD>-<slug>.md`（例 `2026-06-18-arendt-research-state.md`）。

## Hermes 側の実態（config.yaml 全499行を確認済 2026-06-18）
`~/.hermes/config.yaml` に **Vault/Obsidian/監視ディレクトリの固定パスは存在しない**。
関係する設定は次の3つ:

1. **読み取り**: Hermes は `terminal` ツールセット（`cwd: .`）でファイルシステムに直接アクセスできる。
   → タスクがこのフォルダのパスを参照すれば、**追加設定なしで即読める**（オンデマンド読取）。
   ただし常時監視ではなく、起動 cwd が基準。Vault 内で起動するか絶対パス指定が必要。
2. **Skill 自動ロード**: `skills.external_dirs: []` が**空**。ここに
   `…/Vault/04_Resources/Skills` を足すと、保存した Skill を Hermes が自動認識する。
3. **常時監視**: `cron:` + `kanban:` がタスク自動処理エンジン。bridge を継続監視するなら
   このフォルダを見る cron ジョブを1つ追加する（既存 05_Tasks 監視と同じ仕組み）。

### 推奨する1行追加（要・あなたの承認 / 私は無断編集しない）
```yaml
skills:
  external_dirs:
    - /Users/haru/Documents/Claude/Projects/obsidian,hermes/Vault/04_Resources/Skills
```
- 即読取だけなら設定変更ゼロでOK（パスを渡せば読む）。
- 自動ロード／常時監視が要るなら上記＋cron。**config.yaml 編集はあなたの承認後に**。

## 使い方（Cowork 側）
> 「ブリッジに <タスク> の作業状態を書き出して」→ Cowork がこのフォルダに state ファイルを作成。
> Hermes はそれを読んで並行作業。完了したら `status: ready/done` で受け渡し。
