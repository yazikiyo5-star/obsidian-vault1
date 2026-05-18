<%*
const title = tp.date.now("YYYY-MM-DD");
await tp.file.rename(title);
await tp.file.move("/01_Daily/" + title);
-%>
---
date: <% tp.date.now("YYYY-MM-DD") %>
weekday: <% tp.date.now("dddd") %>
tags: [daily]
---

# <% tp.date.now("YYYY-MM-DD (ddd)") %>

## 🌅 きょうの3つ
- [ ] 
- [ ] 
- [ ] 

## 📝 ログ


## 💡 アイデア / 学び


## 🤖 Hermes に投げたもの
> Copilot / Smart Composer で `/summarize today` を実行すると、このノートを要約します。


## 🔗 リンク
- 前日: [[<% tp.date.now("YYYY-MM-DD", -1, tp.file.title, "YYYY-MM-DD") %>]]
- 翌日: [[<% tp.date.now("YYYY-MM-DD", 1, tp.file.title, "YYYY-MM-DD") %>]]
