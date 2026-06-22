---
name: search
builtin: true
description: "免费联网搜索/深度研究：ddgs 搜索 + 抓取证据页 + 引用核实。不要直接 fetch google.com 搜索页（会被 403）。"
when_to_use: "需要查最新信息、对比多个来源、核实事实、找官网/文档链接时；已知具体 URL 用 web_fetch 直接读取，不要把搜索引擎结果页当成普通网页去抓。"
user-invocable: true
allowed-tools: [web_search, web_research, web_fetch]
---
# 搜索模式（Search Router）

`/search` 是路由技能：先判断这次查询该用便宜的单次搜索，还是要多来源核实的深度研究。

## 路由规则

1. 简单事实查询、只需要找到一个权威结果（比如官网链接、某个数字、某个名字）：
   - 用 `web_search(query, max_results)`。
   - 查询词支持搜索引擎原生高级语法：`site:域名` 限定站内、`"精确短语"` 强制完整匹配、
     `-排除词` 剔除噪音、`filetype:pdf` 等文件类型过滤（不同语法在不同底层引擎下生效程度可能不同）。

2. 需要对比多个来源、核实有争议的说法、或用户明确要求"引用网址/多方核实"：
   - 用 `web_research(question, max_results, max_pages)`。
   - 它会自动搜索 + 读取证据页 + 返回带 URL 的证据，不要只引用 `web_search` 的摘要片段当作最终证据。
   - 如果某个证据页状态是 `[PARTIAL EVIDENCE]` 或读取失败被标记为 blocked/rate-limited，**不要**假装没看到——明确告诉用户这条来源没法核实，而不是绕过去硬编一个结论。

3. 已经知道具体 URL：
   - 直接用 `web_fetch(url)`，不要先 `web_search` 再去抓同一个地址。

4. 绝对不要做的事：
   - 不要直接 `web_fetch("https://www.google.com/search?q=...")` 或任何搜索引擎结果页 URL——会被反爬拦截（403），这正是 `web_search` 存在的原因。
   - 不要编造看起来很具体但其实没有真正读到的细节（精确日期、版本代号、引用来源）。如果证据不足，明确说"无法核实"，而不是输出一份看起来权威但内容是编的报告。
   - 动态/需要登录/需要点击交互的页面，`web_fetch`/`web_search` 处理不了，改用 `/browser`。

## 要点
- `web_search` 便宜快，`web_research` 慢但带证据链，按用户实际需要的可信度选，不要每次都上 `web_research`。
- 深度研究开关（Composer 里的"深度研究"按钮）会让普通对话默认优先 `web_research`；`/search` 是显式触发，两者可以同时生效，也可以只用其中一个。
