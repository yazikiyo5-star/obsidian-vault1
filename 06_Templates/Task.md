<%*
const title = await tp.system.prompt("タスク名を入力 (例: ハンナアーレントを調べる)");
if (title) {
  await tp.file.rename(title);
  await tp.file.move("/05_Tasks/inbox/" + title);
}
const goal = await tp.system.prompt("一言ゴール (空でもOK)", title);
-%>
---
goal: <% goal %>
priority: normal
delegate: claude
created: <% tp.date.now("YYYY-MM-DD HH:mm") %>
tags: [task]
---

# <% tp.file.title %>

## やってほしいこと
<% tp.file.cursor() %>


## 受け入れ条件
- [ ] 
- [ ] 

## 触ってOKな範囲
- WebSearch / WebFetch
- `04_Resources/` 配下のフォルダ作成・ファイル作成

## blocked基準
- 法的判断・お金関係の決定は必ずblocked
- 個人情報を含む処理はblocked
