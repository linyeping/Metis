<div align="center">

<img src="backend/assets/cover.png" alt="Metis · 墨提斯" width="100%" />

# Metis · 墨提斯

**一个会读写代码、操控终端与网页的桌面 AI 智能体**

> 智者不喧，巧者不竭。

![Electron](https://img.shields.io/badge/Electron-40-47848F?logo=electron&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-6-3178C6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-Flask%20%2B%20SSE-3776AB?logo=python&logoColor=white)
![i18n](https://img.shields.io/badge/i18n-中文%20%2F%20English-C9A24B)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial-C9A24B)

**由 [linyeping](https://github.com/linyeping) 打造**

**中文 · [English](README.en.md)**

</div>

---

## 这是什么

**Metis** 是一个桌面 AI 智能体客户端：前端 Electron + React，后端是一个跑在本机的 Python 智能体进程。你给它一个目标，它会自己规划、调用工具、一步步去做——读写代码、跑终端命令、查数据库、浏览网页，必要时还能接管桌面。每一步动作都实时显示在右侧工作台里，你能看清它在做什么、改了什么。

模型走 API：默认适配 DeepSeek，也兼容任意 OpenAI 兼容端点（含自定义中转），在设置里填自己的 key 即可。

几个取舍：

- 🔒 **不收集你的东西** — Metis 本身无需账号、无强制登录、无遥测；第三方连接器走标准 OAuth，token 本地加密、永不离开本机、不走中转。
- 🌏 **中英双语** — 全界面中文 / English 一键切换。
- 🧱 **尽量稳** — 崩溃自愈、健康心跳重连、动作审计，争取不在长任务里掉链子。

还在持续打磨，欢迎试用、提 issue。

---

## 功能一览

<div align="center">
<img src="backend/assets/Feature%20Showcase.png" alt="Feature Showcase" width="100%" />
</div>

| 模块 | 说明 |
|---|---|
| 🤖 **智能体循环** | 计划 → 工具调用 → 观察 → 续写；支持**截断自动续写**、延迟工具激活、动作审计 |
| 🛠️ **工具箱** | 代码读写/搜索、**本地语义索引(RAG)**、终端、Git/Diff、文件预览 |
| 🌐 **浏览器操控** | `/browser`：读 DOM 自主浏览、填表、复用登录态 |
| 🖥️ **桌面操控** | `/computer`：截图 + 坐标操作任意原生软件，坐标系可配 |
| ⚡ **并行子智能体** | 只读分析任务扇出并行执行，加速大仓库理解 |
| 📋 **审计日志** | 每轮工具动作落 `.metis/audit/`，可回溯 |
| 🎚️ **权限模式** | 请求批准 / 替我审批 / 完全访问，按风险分级 |
| ⏱️ **定时任务** | 内置 cron，定时跑智能体工作流 |
| 🧩 **技能 & `/` 指令** | 输入 `/` 唤起命令面板：`/new`、`/compact`、`/rewind`、`/browser`、`/computer` 及自定义技能 |
| 🎨 **16 套主题** | 8 浅 + 8 深，金色为主的质感配色；白天/夜晚双模式各记各的主题 |
| 🔁 **自愈重连** | 后端崩溃自动重启、API 假死 8s 心跳探测、状态栏如实显示「正在重新连接」 |
| 📦 **一键打包** | PyInstaller 打包后端 + electron-builder 出 Windows 安装包，开箱即用 |

---

## 架构

<div align="center">
<img src="backend/assets/Architecture.png" alt="Architecture" width="100%" />
</div>

- **渲染层** `desktop/src/` — React 19 + Vite + Zustand 状态 + assistant-ui 消息流。
- **主进程** `desktop/electron/main.cjs` — 窗口管理、`WebContentsView` 原生网页预览、打包入口。
- **后端** `backend/` — Flask/SSE 服务、`agent_loop` 智能体循环、`tool_registry` 工具注册、供应商适配。
- 三者通过 **HTTP / SSE** 通信，工具最终对接 **DeepSeek / 任意兼容端点**。

---

## 运行环境

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11（64 位） |
| 磁盘空间 | 约 450 MB |
| 网络 | 联网以调用大模型 API |
| API key | DeepSeek 或任意 OpenAI 兼容端点的密钥，首次启动时在向导中填入 |
| 浏览器 | `/browser` 网页操控复用系统 Chrome / Edge |

安装包已内置运行时，无需额外配置开发环境。`/computer` 桌面操控涉及鼠标键盘控制，首次使用可能触发系统授权。当前版本尚未代码签名，Windows SmartScreen 可能提示风险，确认后可继续运行。

---

## 开发

```powershell
python -m pip install -e backend/   # 安装后端
cd desktop
npm ci
npm run dev                          # 开发模式，自动拉起后端
```

## 验证

```powershell
python -m pytest backend/tests/ -q   # 后端单测
cd desktop
npm run typecheck                     # 类型检查
npm run test                          # 渲染层单测 (vitest)
npm run test:contracts                # 契约/安全测试
```

## 打包（Windows）

```powershell
cd desktop
npm run build-backend                 # PyInstaller 打包后端
npm run dist:win                      # 出 NSIS 安装包 → desktop/release/
```

---

## 项目结构

```
Miro/
├── backend/          # Python 智能体：Flask/SSE、agent_loop、工具、供应商适配
│   ├── runtime/      #   智能体循环、工具注册、技能、审计、并行子智能体
│   ├── tools/        #   代码 / 浏览器 / 桌面 / 检索 等工具实现
│   └── assets/       #   品牌图（封面 / 架构 / 功能展示）
├── desktop/          # Electron 桌面端
│   ├── electron/     #   主进程、preload、安全/契约测试
│   ├── src/          #   React 渲染层（组件、store、运行时、i18n）
│   └── scripts/      #   打包与冒烟脚本
└── docs/             # 架构 / 开发 / 变更记录
```

## 许可证

**[PolyForm Noncommercial 1.0.0](LICENSE)** © 2026 linyeping

源码可见,**个人 / 非商用免费**(学习、研究、个人项目、非营利组织)。
**任何商业用途或商业二次开发,须事先获得作者书面授权(付费)** —— 联系见仓库主页。

---

<div align="center">

**由 [linyeping](https://github.com/linyeping) 打造** · 智者不喧，巧者不竭。

</div>
