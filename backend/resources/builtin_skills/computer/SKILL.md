---
name: computer
builtin: true
description: "电脑任务用 computer use：截图+点击操作原生软件、系统设置、安装器、跨软件流程。网页请改用 /browser。"
when_to_use: "用户要操作非浏览器的桌面程序或系统：文件资源管理器、Office、系统设置、安装程序、把内容从一个软件拖到另一个软件。"
user-invocable: true
allowed-tools: [desktop_win2_status, desktop_win2_observe, desktop_win2_action, desktop_win2_task, desktop_screenshot, desktop_action, desktop_vision_task, desktop_inventory, desktop_window_list, desktop_window_capture, desktop_window_action]
---
# 电脑模式（Computer Use）

本次任务请**用桌面工具**完成。这类工具能控任何窗口，适合**原生软件和系统**，以及跨软件流程。

## 用哪个工具
- 多步 GUI 流程（打开软件、搜索、导航、填表）**优先** `desktop_win2_task`——它使用窗口级 observe -> plan -> act -> verify 循环，比全屏截图坐标更稳。
- 需要手动拆步时，用 `desktop_win2_status` 找窗口，用 `desktop_win2_observe` 抓目标窗口，用 `desktop_win2_action` 做窗口相对动作；每次动作后重新 observe 验证。
- 只有当 Window2 无法解析/捕获目标窗口、游戏/画布 UI 或需要全屏视觉时，才回退 `desktop_vision_task`。
- 单步操作用 `desktop_action`（click / type / key / scroll）；坐标直接读你刚收到的那张截图的像素，系统会自动映射到物理屏幕。
- `desktop_screenshot` 看当前画面；`desktop_window_list` / `desktop_window_capture` / `desktop_window_action` 针对具体窗口。

## 要点
- **如果任务是在网页/网站里**，请改用 `/browser`（读 DOM）——用像素点击网页最不可靠。
- 操作前先截图确认当前画面，别凭记忆点。
