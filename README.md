<div align="center">

<img src="backend/assets/cover.png" alt="Metis · 墨提斯" width="100%" />

# Metis · 墨提斯

**本地优先、隐私至上的桌面 AI 智能体 — 为 DeepSeek 深度调校**

> 智者不喧，巧者不竭。
> _The wise stay quiet; the skilled never run dry._

![Electron](https://img.shields.io/badge/Electron-40-47848F?logo=electron&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-6-3178C6?logo=typescript&logoColor=white)
![Python](https://img.shields.io/badge/Python-Flask%20%2B%20SSE-3776AB?logo=python&logoColor=white)
![i18n](https://img.shields.io/badge/i18n-中文%20%2F%20English-C9A24B)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial-C9A24B)

**Built by [linyeping](https://github.com/linyeping)**

</div>

---

## 这是什么

**Metis** 是一个完全跑在你自己电脑上的 AI 智能体桌面客户端：前端 Electron + React，后端是本地 Python 智能体。它能读写代码、操控终端、查数据库、自主浏览网页、甚至接管桌面——并把每一步透明地展示在右侧工作台里。

设计原则很硬核：

- 🏠 **本地优先** — 智能体后端在本机运行，会话与数据留在本地。
- 🔒 **隐私至上** — 无 OAuth、无遥测埋点、无强制账号；API key 仅以环境变量名引用，**绝不明文落盘**。
- 🐉 **DeepSeek 特化** — 针对 DeepSeek 的前缀缓存与上下文特性专门调校，同时通过「自定义 OpenAI 中转」兼容任意兼容端点。
- 🌏 **中英双语** — 全界面中文 / English 一键切换。
- 🧱 **稳定优先** — 真机验收、契约测试、崩溃自愈、健康心跳重连，对标桌面级工程质量。

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

## 运行需要什么环境

发给别人的安装包是**自包含**的，对方电脑**不用装 Python、不用装 Node**：

| 必需 | 说明 |
|---|---|
| **Windows 10/11 64 位** | 安装包是 win-x64 |
| **磁盘 ~450MB** | 后端经 PyInstaller 打成独立 exe，已内置 Python 运行时 |
| **联网** | 调用 DeepSeek / 中转的大模型 API |
| **一个 API key** | 首启向导里填自己的 DeepSeek 或中转 key（仅按环境变量名引用，不落明文） |
| **Chrome / Edge** | `/browser` 网页操控复用系统浏览器（Windows 自带 Edge 即可） |

> `/computer` 桌面操控会控制鼠标键盘，首次可能触发一次 UAC 提权；安装包若未做受信任签名，Windows 会提示「未知发布者」，点「仍要运行」即可。

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

**Built by [linyeping](https://github.com/linyeping)** · 智者不喧，巧者不竭。

</div>
