---
name: browser
builtin: true
description: "网页任务用浏览器：读 DOM 打开/操作网页、点链接、填表单、提取内容、登录站点。比像素点击稳得多。"
when_to_use: "用户要在网页/网站上做事：打开某网址、搜索、点链接、填网页表单、抓取页面信息、登录 GitHub/Gmail 等。"
user-invocable: true
allowed-tools: [browse_web, browse_and_extract]
---
# 浏览器模式（Browser Use）

本次任务请**只用浏览器工具**完成，**不要**用 `desktop_*`（截图+坐标点击）去操作网页——像素点击网页最不可靠（坐标会偏、没有结构信息）。

## 用哪个工具
- `browse_web(task, url="", max_steps=15, use_login=False)`：自主完成一段网页操作（打开、导航、点击、填表、总结）。
- `browse_and_extract(url, what_to_extract, use_login=False)`：打开某页并抽取指定信息。

## 要点
- 需要登录态（GitHub / Gmail / 后台等）时传 `use_login=True`，会复用用户真实浏览器配置与 cookie。
- 多步任务交给一次 `browse_web` 调用（给清楚的 `task`），别把它拆成很多次小调用。
- 只有当 `browse_web` 实在搞不定（验证码、原生系统弹窗、非网页内容）时，才考虑切到 `/computer`。
