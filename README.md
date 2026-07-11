<div align="center">

<img src="backend/assets/cover.png" alt="Metis · 墨提斯" width="100%" />

# Metis · 墨提斯

**把模型接入代码、终端、浏览器与 Windows 桌面，让任务从一句话走到可验证的结果。**

[![Release](https://img.shields.io/github/v/release/linyeping/Metis?display_name=tag&sort=semver&style=flat-square)](https://github.com/linyeping/Metis/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/linyeping/Metis/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/linyeping/Metis/actions/workflows/ci.yml)
![Windows](https://img.shields.io/badge/Windows-10%20%2F%2011-357EC7?style=flat-square&logo=windows11&logoColor=white)
![Local First](https://img.shields.io/badge/Execution-Local--first-2E8B72?style=flat-square)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial-C9A24B?style=flat-square)

**[下载 26.7.11](https://github.com/linyeping/Metis/releases/tag/v26.7.11) · [查看更新](desktop/release/RELEASE_NOTES_v26.7.11.md) · [English](README.en.md)**

由 [linyeping](https://github.com/linyeping) 打造 · 产品方向与部分设计思路致谢 [Serein](https://github.com/Serein0812)

</div>

---

## 从目标到结果

Metis 不是给聊天窗口再加几个按钮，而是一套运行在本机的 **AI 执行工作台**。它把模型推理、项目上下文、工具调用、权限控制和结果验证放进同一条桌面工作流：

```text
目标 -> 计划 -> 执行 -> 观察 -> 验证 -> 交付
             ^                 |
             +---- 继续迭代 ----+
```

你可以让它理解仓库、修改文件、运行命令、检查网页、操作 Windows 应用，并把 diff、测试、截图、日志和产物作为完成证据。整个过程保留会话、任务状态与审计记录，长任务中断后也能继续。

> **Local-first by design**：文件、命令与桌面操作在本机执行；Metis 无强制账号、无内置遥测，API Key 与 OAuth Token 不经过 Metis 中转服务。

---

## 获取 Metis

| | |
|---|---|
| 当前版本 | **26.7.11** |
| 系统 | Windows 10 / 11 64 位 |
| 安装包 | [下载 Metis-Setup-26.7.11.exe](https://github.com/linyeping/Metis/releases/download/v26.7.11/Metis-Setup-26.7.11.exe) |
| 模型 | DeepSeek 或任意 OpenAI-compatible API |

当前安装包尚未代码签名，Windows SmartScreen 可能显示提示。请从本仓库的 GitHub Release 下载并核对发布版本。

---

## 三种工作面

Metis 将不同强度的任务分到三个独立但连续的工作面，而不是把所有交互塞进一条聊天流。

| 工作面 | 适合处理 | 执行模型 |
|---|---|---|
| **Chat** | 问答、分析、检索、轻量文件处理 | 快速响应，按需调用工具 |
| **Cowork** | 多步骤交付、资料整理、跨工具协作 | 计划驱动，可拆分子任务并汇总证据 |
| **Code** | 仓库理解、代码修改、测试与构建 | 工作区感知，围绕 diff 与验证闭环 |

会话、工作区和草稿按模式隔离；快速切换不会让旧请求覆盖当前记录，也不会把另一个模式的工作区错误带入新任务。

## 可验证的执行能力

| 能力域 | Metis 如何工作 | 可检查的结果 |
|---|---|---|
| **Code & Terminal** | 搜索代码、结构化编辑、执行 Git/CLI、运行测试与构建 | diff、终端输出、测试结论、生成文件 |
| **Preview Browser** | 导航、点击、输入、DOM 观察，并收集 console/network 异常 | 页面状态、截图、DOM、控制台与网络证据 |
| **Computer Use** | 基于窗口观察执行鼠标键盘操作，按 `observe -> act -> verify` 循环推进 | 操作轨迹、窗口截图、步骤状态与结果 |
| **Store & Connectors** | 安装技能、工具和外部服务连接器，展示具体能力说明 | 可追溯来源、彩色品牌标识、连接状态 |
| **Long-running Work** | 上下文压缩、checkpoint、后台 run、心跳重连与恢复 | 可恢复会话、任务进度、压缩边界与产物 |

## 可信执行边界

- **本机优先**：文件系统、终端、浏览器和桌面工具在用户设备上运行。
- **权限分层**：读取、写入、删除、外部提交等动作按风险进入许可策略与审批流程。
- **过程可见**：工具卡展示状态、耗时、摘要和可展开结果，后台任务有独立活动视图。
- **凭据隔离**：API Key 与 OAuth Token 不写入日志、不进入模型上下文；OAuth Token 加密存储。
- **结果导向**：任务完成不只依赖模型声明，而是结合测试、diff、截图、日志或产物验证。

## 26.7.11 版本亮点

- 全面启用新的圆角花朵品牌标识，覆盖桌面端、托盘、Store 与 Windows 安装包。
- Store 使用彩色连接器 Logo，并为插件与工具提供具体、可搜索的中英文能力说明。
- 设置中新增关闭窗口行为：询问、最小化到托盘或直接退出；默认仍为最小化到托盘。
- 重构 Chat / Cowork / Code 导航链路，减少重复加载并防止快速切换时的会话竞态。

---

## 产品界面

<div align="center">
<img src="backend/assets/Feature%20Showcase.png" alt="Feature Showcase" width="100%" />
</div>

界面围绕持续工作设计：中心线程承载目标和结果，右侧工作台承载 Preview、Diff、Terminal、Files 与后台 Activity，设置中心统一管理模型、权限、运行时、连接器和桌面行为。

---

## 架构

<div align="center">
<img src="backend/assets/Architecture.png" alt="Architecture" width="100%" />
</div>

```text
Metis Desktop
├─ Electron main process
│  ├─ 窗口、菜单、OAuth、WebContentsView Preview
│  ├─ 后端生命周期管理
│  └─ Windows 打包入口
├─ React renderer
│  ├─ Chat / Tool Activity / Right Rail / Settings
│  ├─ Browser Activity / Preview Browser UI
│  └─ Zustand stores + assistant-ui message stream
└─ Python backend
   ├─ Flask + SSE API
   ├─ agent_loop / tool_registry / skills
   ├─ browser automation / desktop automation
   ├─ provider adapters
   └─ checkpoint / context budget / connectors
```

通信方式：

- Renderer 与后端通过 HTTP / SSE 通信。
- Electron main 负责本地预览、OAuth、打包后的后端启动和桌面 shell 能力。
- 后端工具最终调用本地文件系统、终端、浏览器、桌面自动化和模型 API。

---

## 运行环境

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11 64 位 |
| Node.js | 开发模式需要 Node.js；安装包模式不要求用户手动安装 |
| Python | 开发模式需要 Python；安装包会内置后端运行时 |
| 网络 | 需要联网调用模型 API |
| API key | DeepSeek 或任意 OpenAI-compatible endpoint |
| 桌面操控 | `/computer` 会控制鼠标键盘，执行敏感动作前应由用户确认 |

当前版本尚未代码签名，Windows SmartScreen 可能提示风险，确认后可继续运行。

---

## 开发运行

```powershell
python -m pip install -e backend/

cd desktop
npm ci
npm run dev
```

开发模式会启动：

- Vite renderer：默认 `http://127.0.0.1:5174`
- Electron desktop shell
- 本机 Python backend：由 Electron launcher 管理

---

## 常用命令

```powershell
# 前端类型检查
cd desktop
npm run typecheck

# 前端单测
npm run test

# Electron / 安全 / 契约测试
npm run test:contracts

# 后端测试
cd ..
python -m pytest backend/tests/ -q

# 生产 renderer 构建
cd desktop
npm run build
```

---

## 打包 Windows EXE

```powershell
cd desktop
npm run dist:win
```

`dist:win` 会执行：

1. `npm run build-backend`：用 PyInstaller 打包 Python 后端。
2. `npm run build`：构建 React/Vite renderer。
3. `electron-builder --win nsis`：生成 Windows NSIS 安装包。

产物位置：

```text
desktop/release/
```

如果只想验证前端是否能生产构建：

```powershell
cd desktop
npm run build
```

---

## 项目结构

```text
Miro/
├── backend/
│   ├── bridges/        # 事件契约、供应商/工具协议桥接
│   ├── runtime/        # agent loop、工具注册、技能、checkpoint、context budget
│   ├── tools/          # 代码、浏览器、桌面、检索等工具实现
│   ├── web/            # Flask API、SSE、Preview Browser bridge
│   └── assets/         # 封面、架构图、功能展示图
├── desktop/
│   ├── electron/       # Electron main/preload、OAuth、打包入口
│   ├── src/            # React UI、stores、runtime、i18n
│   └── scripts/        # 构建、契约测试、冒烟测试脚本
├── docs/               # 开发日志和设计文档
└── README.md / README.en.md
```

---

## 隐私与安全

- Metis 不要求平台账号，不内置遥测。
- API key 和 OAuth token 存在本机配置/加密存储中。
- 连接器 token 不进入模型上下文。
- 工具动作有审计记录，便于回看和排查。
- `/computer` 和 `/browser` 会区分读取信息与发送/提交数据；涉及外部副作用、敏感数据、删除、上传、授权等操作时应先确认。

---

## 许可证

**[PolyForm Noncommercial 1.0.0](LICENSE)** © 2026 linyeping

源码可见，**个人 / 非商用免费**（学习、研究、个人项目、非营利组织）。
**任何商业用途或商业二次开发，须事先获得作者书面授权（付费）**。

---

<div align="center">

**由 [linyeping](https://github.com/linyeping) 打造** · 产品方向与部分设计思路致谢：[Serein](https://github.com/Serein0812) · 智者不喧，巧者不竭。

</div>
