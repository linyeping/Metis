<div align="center">

<img src="backend/assets/cover.png" alt="Metis · 墨提斯" width="100%" />

# Metis · 墨提斯

**把模型接入代码、终端、浏览器与 Windows 桌面，让任务从一句话走到可验证的结果。**

<p>
  <img alt="Electron 40" src="https://img.shields.io/badge/Electron-40-47848F?style=flat-square&logo=electron&logoColor=white" />
  <img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=111111" />
  <img alt="TypeScript 6" src="https://img.shields.io/badge/TypeScript-6-3178C6?style=flat-square&logo=typescript&logoColor=white" />
  <img alt="Python 3.11" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img alt="Flask SSE" src="https://img.shields.io/badge/Flask-SSE-2E8B72?style=flat-square&logo=flask&logoColor=white" />
</p>

**[下载 26.7.11](https://github.com/linyeping/Metis/releases/tag/v26.7.11) · [查看更新](desktop/release/RELEASE_NOTES_v26.7.11.md) · [English](README.en.md)**

由 [linyeping](https://github.com/linyeping) 打造 · 产品方向与部分设计思路致谢 [Serein](https://github.com/Serein0812)

</div>

<br />

<h2 align="center">从目标到结果</h2>

<p align="center">
  Metis 不是给聊天窗口再加几个按钮，而是一套运行在本机的 <b>AI 执行工作台</b>。<br />
  它把模型推理、项目上下文、工具调用、权限控制和结果验证放进同一条桌面工作流。
</p>

<p align="center">
  <kbd>目标</kbd> &nbsp;→&nbsp; <kbd>计划</kbd> &nbsp;→&nbsp; <kbd>执行</kbd> &nbsp;→&nbsp; <kbd>观察</kbd> &nbsp;→&nbsp; <kbd>验证</kbd> &nbsp;→&nbsp; <kbd>交付</kbd>
</p>

<p align="center">
  <sub>理解仓库、修改文件、运行命令、检查网页、操作 Windows 应用，并以 diff、测试、截图、日志和产物作为完成证据。</sub>
</p>

<p align="center">
  <b>Local-first by design</b><br />
  <sub>文件、命令与桌面操作在本机执行；无强制账号、无内置遥测，API Key 与 OAuth Token 不经过 Metis 中转服务。</sub>
</p>

<br />

<h2 align="center">获取 Metis</h2>

<p align="center">
  <a href="https://github.com/linyeping/Metis/releases/download/v26.7.11/Metis-Setup-26.7.11.exe"><img alt="Download Metis 26.7.11" src="https://img.shields.io/badge/下载_Metis-26.7.11-357EC7?style=for-the-badge&logo=windows11&logoColor=white" /></a>
  <a href="https://github.com/linyeping/Metis/releases/tag/v26.7.11"><img alt="Release notes" src="https://img.shields.io/badge/查看-Release_Notes-2E8B72?style=for-the-badge&logo=github&logoColor=white" /></a>
</p>

<p align="center">
  <b>Windows 10 / 11 · 64 位 · DeepSeek / OpenAI-compatible API</b><br />
  <sub>安装包尚未代码签名，Windows SmartScreen 可能显示提示。请仅从本仓库的 GitHub Release 下载。</sub>
</p>

<br />

<h2 align="center">三种工作面</h2>

<p align="center">不同强度的任务拥有独立但连续的工作面，不必全部挤进一条聊天流。</p>

<table width="100%">
  <tr>
    <td width="33%" align="center" valign="top">
      <img alt="Chat" src="https://img.shields.io/badge/CHAT-Ask_%26_Explore-4F7CAC?style=for-the-badge" /><br /><br />
      <b>快速理解与轻量执行</b><br />
      <sub>问答、分析、检索与文件处理<br />按需激活工具，保持响应直接</sub>
    </td>
    <td width="34%" align="center" valign="top">
      <img alt="Cowork" src="https://img.shields.io/badge/COWORK-Plan_%26_Deliver-2E8B72?style=for-the-badge" /><br /><br />
      <b>计划驱动的多步骤交付</b><br />
      <sub>拆分子任务、跨工具协作<br />汇总过程证据与最终产物</sub>
    </td>
    <td width="33%" align="center" valign="top">
      <img alt="Code" src="https://img.shields.io/badge/CODE-Build_%26_Verify-8A6BBE?style=for-the-badge" /><br /><br />
      <b>围绕仓库的工程闭环</b><br />
      <sub>理解代码、修改文件、运行测试<br />以 diff 与验证结果完成交付</sub>
    </td>
  </tr>
</table>

<p align="center"><sub>会话、工作区和草稿按模式隔离；快速切换不会让旧请求覆盖当前记录，也不会串入其他模式的工作区。</sub></p>

<br />

<h2 align="center">可验证的执行能力</h2>

<table width="100%">
  <tr>
    <td width="33%" align="center" valign="top"><b>Code & Terminal</b><br /><br /><sub>代码搜索、结构化编辑、Git / CLI、测试与构建<br /><br /><b>证据：</b>diff、终端输出、测试结论、生成文件</sub></td>
    <td width="34%" align="center" valign="top"><b>Preview Browser</b><br /><br /><sub>导航、点击、输入、DOM 观察、console / network 诊断<br /><br /><b>证据：</b>页面状态、截图、DOM 与错误记录</sub></td>
    <td width="33%" align="center" valign="top"><b>Computer Use</b><br /><br /><sub>基于窗口观察执行鼠标键盘操作<br /><br /><b>证据：</b>操作轨迹、窗口截图、步骤状态</sub></td>
  </tr>
  <tr>
    <td width="33%" align="center" valign="top"><b>Store & Connectors</b><br /><br /><sub>技能、工具与外部服务连接器<br /><br /><b>证据：</b>可追溯来源、能力说明、连接状态</sub></td>
    <td width="34%" align="center" valign="top"><b>Long-running Work</b><br /><br /><sub>上下文压缩、checkpoint、后台 run、重连与恢复<br /><br /><b>证据：</b>会话进度、恢复边界、任务产物</sub></td>
    <td width="33%" align="center" valign="top"><b>Safety & Evidence</b><br /><br /><sub>风险分层、权限审批、凭据隔离与活动审计<br /><br /><b>原则：</b>结果由测试、diff、截图或日志验证</sub></td>
  </tr>
</table>

<br />

<h2 align="center">可信执行边界</h2>

<table width="100%">
  <tr>
    <td width="50%" align="center" valign="top"><b>本机优先</b><br /><sub>文件系统、终端、浏览器和桌面工具在用户设备上运行</sub></td>
    <td width="50%" align="center" valign="top"><b>权限分层</b><br /><sub>读取、写入、删除与外部提交按风险进入审批流程</sub></td>
  </tr>
  <tr>
    <td width="50%" align="center" valign="top"><b>过程可见</b><br /><sub>工具状态、耗时、摘要、结果与后台任务均可回看</sub></td>
    <td width="50%" align="center" valign="top"><b>凭据隔离</b><br /><sub>API Key 与 OAuth Token 不进入日志和模型上下文</sub></td>
  </tr>
</table>

<br />

<details>
  <summary><b>26.7.11 版本亮点</b></summary>
  <br />
  <ul>
    <li>全面启用新的圆角花朵品牌标识，覆盖桌面端、托盘、Store 与 Windows 安装包。</li>
    <li>Store 使用彩色连接器 Logo，并提供具体、可搜索的中英文能力说明。</li>
    <li>新增关闭窗口行为：询问、最小化到托盘或直接退出。</li>
    <li>重构 Chat / Cowork / Code 导航链路，减少重复加载与快速切换竞态。</li>
  </ul>
</details>

<br />

---

<h2 align="center">产品界面</h2>

<div align="center">
<img src="backend/assets/Feature%20Showcase.png" alt="Feature Showcase" width="100%" />
</div>

<p align="center">
  中心线程承载目标和结果，右侧工作台承载 Preview、Diff、Terminal、Files 与后台 Activity。<br />
  <sub>设置中心统一管理模型、权限、运行时、连接器和桌面行为。</sub>
</p>

---

<h2 align="center">架构</h2>

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
