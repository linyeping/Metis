---
name: browser
builtin: true
description: "网页任务用浏览器：读 DOM 打开/操作网页、点链接、填表单、提取内容、登录站点。比像素点击稳得多。"
when_to_use: "用户要在网页/网站上做事：打开某网址、操作本地预览、测试 localhost、点链接、填网页表单、抓取页面信息、登录 GitHub/Gmail 等。"
user-invocable: true
allowed-tools: [preview_browser_status, preview_browser_navigate, preview_browser_observe, preview_browser_action, preview_browser_screenshot, preview_browser_verify, browse_web, browse_and_extract]
---
# 浏览器模式（Browser Router）

本次任务请**只用浏览器工具**完成，**不要**用 `desktop_*`（截图+坐标点击）去操作网页。

`/browser` 是路由技能：先判断任务应该进 Metis 右栏 Preview Browser，还是走外部 browser-use。

## 路由规则

1. 右栏 Preview Browser 优先：
   - 用户提到 `localhost`、`127.0.0.1`、`0.0.0.0`、本地 dev server、file preview、右栏预览、Preview 卡片、网页预览、UI 验收、截图验收、点一下当前预览页。
   - 使用 `preview_browser_*`，不要再开外部浏览器，也不要走 `/computer`。

2. 已知外部 URL，只是读取或抽取内容：
   - 使用 `browse_and_extract(url, what_to_extract, use_login=False)`。
   - 需要用户真实登录态时才设置 `use_login=True`。

3. 外部动态网站需要多步浏览：
   - 使用 `browse_web(task, url="", max_steps=15, use_login=False)`。
   - 需要 GitHub/Gmail/后台等真实登录态时，只有在用户明确要求登录态或账号上下文时才设置 `use_login=True`。

4. 只有浏览器工具确实处理不了时才切到 `/computer`：
   - CAPTCHA、原生系统弹窗、浏览器外安装器、非网页软件窗口。

## Preview Browser 动作循环

对本地预览和右栏网页，按这个顺序做：

1. `preview_browser_navigate(url)`：目标 URL 不在当前 Preview 时导航。
   - `url` 可以是空值/`current`/`当前页面`，表示复用右栏当前页。
   - 本地地址可写成 `localhost`、`localhost:5173` 或完整 URL；Preview 会优先识别当前 dev server，并在端口未监听时自动尝试 `5173/5174/3000/4200/8000/8080`。
2. `preview_browser_observe(max_elements=80)`：读取 URL、标题、可见文本、可交互元素、`element_id`。
   - 如果用户问“为什么白屏/为什么打不开/页面报错”，重点查看返回的 `diagnostics`、`dom_summary`、`page_health`。
   - `diagnostics` 会包含 console warning/error、JS exception/unhandled rejection、failed network request、page load failure。
3. `preview_browser_action(action, element_id=...)`：优先用 `element_id` 点击、输入、滚动或按键。
4. `preview_browser_verify(...)` 或 `preview_browser_screenshot()`：每轮动作后验证页面状态；截图结果也会带 URL、标题、viewport、page_health、screenshot_health 和诊断摘要。
   - 简单验收继续用 `text_contains`、`url_contains`、`title_contains`。
   - UI 验收优先用 `preview_browser_verify(assertion="确认登录按钮可见并可点击")` 这种一句话验收。
   - 需要精确控制时用结构化字段：`button_text`、`require_button_clickable`、`input_label`、`require_input_editable`、`visible_text`、`not_visible_text`、`require_no_blank`、`require_no_console_errors`、`require_screenshot_not_blank`。
   - 检查白屏/纯白/纯黑截图时，设置 `require_no_blank=true` 和 `require_screenshot_not_blank=true`。

## 要点
- Preview 页面布局变化后重新 `observe`，旧 `element_id` 可能失效。
- Preview 的高风险动作会在 Electron 执行层拦截并要求用户确认：登录/OAuth、submit、upload、send、purchase、delete、payment、password/file input。
- browser-use 多步任务尽量交给一次 `browse_web` 调用，给清楚 `task`，不要拆成很多次小调用。
- 网页任务不要用 `desktop_*` 像素点击；网页有 DOM、元素、URL 和文本，比坐标可靠。
