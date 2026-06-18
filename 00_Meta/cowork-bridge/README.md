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

## Hermes 側で必要な設定（要・あなたの確認）
Hermes がこのフォルダを読むには、`~/.hermes/config.yaml` の監視/コンテキスト対象に
このパスを含める必要がある:

```
/Users/haru/Documents/Claude/Projects/obsidian,hermes/Vault/00_Meta/cowork-bridge/
```

- 既に Vault 全体を読む設定なら**追加不要**（Hermes は 05_Tasks/inbox を監視している実績あり）。
- フォルダ単位監視なら上記パスを 1 行追加。**設定変更は無断で行わず、必ず確認してから**。

## 使い方（Cowork 側）
> 「ブリッジに <タスク> の作業状態を書き出して」→ Cowork がこのフォルダに state ファイルを作成。
> Hermes はそれを読んで並行作業。完了したら `status: ready/done` で受け渡し。
