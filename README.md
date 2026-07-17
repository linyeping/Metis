<div align="center">

<img src="backend/assets/Metis-26.7.17-Design.jpg" alt="Metis 26.7.17 · Design" width="100%" />

# Metis

**把模型接入代码、终端、浏览器与 Windows 桌面，让任务从一句话走到可验证的结果。**

<p>
  <img alt="Electron 41" src="https://img.shields.io/badge/Electron-41-47848F?style=flat-square&logo=electron&logoColor=white" />
  <img alt="React 19" src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=111111" />
  <img alt="TypeScript 6" src="https://img.shields.io/badge/TypeScript-6-3178C6?style=flat-square&logo=typescript&logoColor=white" />
  <img alt="Python 3.11" src="https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white" />
  <img alt="Flask SSE" src="https://img.shields.io/badge/Flask-SSE-2E8B72?style=flat-square&logo=flask&logoColor=white" />
</p>

<p>
  <a href="https://github.com/linyeping/Metis/releases/download/v26.7.17/Metis-Setup-26.7.17.exe"><img alt="下载 Metis 26.7.17" src="https://img.shields.io/badge/下载_Metis-26.7.17-357EC7?style=for-the-badge&logo=windows11&logoColor=white" /></a>
  <a href="https://github.com/linyeping/Metis/releases/tag/v26.7.17"><img alt="查看 Release Notes" src="https://img.shields.io/badge/查看-Release_Notes-2E8B72?style=for-the-badge&logo=github&logoColor=white" /></a>
</p>

<sub>Windows 10 / 11 · 64 位 · DeepSeek / OpenAI-compatible API</sub><br />
<sub><a href="README.en.md">English</a> · 由 <a href="https://github.com/linyeping">linyeping</a> 打造 · 产品方向与部分设计思路致谢 <a href="https://github.com/Serein0812">Serein</a></sub>

</div>

<br />

<h2 align="center">一个工作台，四种工作面</h2>

<p align="center">
  不同任务进入各自最合适的工作面，但共享同一套模型、项目、工具、权限和通知体系。
</p>

<p align="center">
  <img src="backend/assets/work-surfaces-real-26.7.17.png" alt="Metis Chat、Cowork、Code 与 Design 四种工作面" width="100%" />
</p>

<p align="center">
  <b>CHAT</b> 快速理解与轻量执行 &nbsp;·&nbsp;
  <b>COWORK</b> 计划驱动的多步骤交付 &nbsp;·&nbsp;
  <b>CODE</b> 围绕仓库的工程闭环 &nbsp;·&nbsp;
  <b>DESIGN</b> 对话驱动的可交付设计
</p>

<p align="center"><sub>模式之间的会话、工作区和草稿相互隔离；快速切换不会让旧请求覆盖当前记录。</sub></p>

<br />

<h2 align="center">Metis Design：从一句想法到可交付作品</h2>

<p align="center">
  在同一个工作区完成需求描述、素材整理、设计生成、实时预览、迭代评审与多格式交付。
</p>

<table align="center" width="100%">
  <tr>
    <td width="50%" valign="top">
      <b>项目入口与双栏 Studio</b><br /><br />
      创建或打开设计项目，在左侧和 Agent 协作，在右侧即时查看成品；项目、会话、页面、素材与设计系统持续保存在本机。
    </td>
    <td width="50%" valign="top">
      <b>真实渲染，不是静态草图</b><br /><br />
      生成网页、桌面与移动端原型、演示文稿、图片和交互式内容，并在隔离预览中直接检查最终效果。
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <b>完整导出链路</b><br /><br />
      支持按作品类型导出 HTML、PDF、PPTX、图片、Markdown 与项目包；预览、版本恢复、分享和导出状态集中管理。
    </td>
    <td width="50%" valign="top">
      <b>统一模型与桌面体验</b><br /><br />
      直接使用 Metis 当前模型配置与 API 凭据，并共享主题、语言、任务通知和桌面宠物状态；完成后可直接返回 Chat、Cowork 或 Code。
    </td>
  </tr>
</table>

<br />

<h2 align="center">不止回答，而是把工作做完</h2>

<p align="center">
  Metis 把模型推理、项目上下文、工具调用、权限控制与结果验证放进同一条本机工作流。
</p>

<div align="center">
  <img src="backend/assets/metis-capabilities-26.7.17.png" alt="Metis Code、Browser、Desktop、Design、Skills 与 Isolated VM 能力" width="100%" />
</div>

<table align="center" width="100%">
  <tr>
    <td width="50%" valign="top">
      <b>从目标到证据</b><br /><br />
      理解仓库与上下文 → 拆解计划 → 修改文件与运行命令 → 检查网页或 Windows 应用 → 汇总 diff、测试、截图、日志和产物。
    </td>
    <td width="50%" valign="top">
      <b>长任务不中断</b><br /><br />
      上下文压缩、checkpoint、后台 run、重连与恢复共同维护任务连续性，过程状态和最终产物都可回看。
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <b>本机优先</b><br /><br />
      文件系统、终端、浏览器和桌面工具在用户设备上运行；无需平台账号，也没有内置遥测。
    </td>
    <td width="50%" valign="top">
      <b>可验证交付</b><br /><br />
      工具调用、耗时、摘要和结果保持可见；完成状态由测试、diff、截图或日志证明，而不是只给一句“已完成”。
    </td>
  </tr>
</table>

<br />

<h2 align="center">覆盖完整任务生命周期</h2>

<table align="center" width="100%">
  <tr>
    <td width="33%" valign="top">
      <b>会话与后台任务</b><br /><br />
      会话恢复、归档、未读标记、运行队列、后台任务、checkpoint 与完成通知，让长任务离开当前页面后仍可继续。
    </td>
    <td width="34%" valign="top">
      <b>多标签工作区</b><br /><br />
      在同一任务中并排使用文件、Diff、终端、网页预览、计划和活动记录；标签可搜索、切换、关闭和恢复。
    </td>
    <td width="33%" valign="top">
      <b>项目级上下文</b><br /><br />
      目录、仓库、分支、会话、附件、设计系统和生成产物共同组成可回看的项目上下文。
    </td>
  </tr>
  <tr>
    <td width="33%" valign="top">
      <b>Skills / Connectors / MCP</b><br /><br />
      复用技能流程，连接 GitHub、Slack、Notion、Google Workspace、数据库与本机文件，并扩展外部 MCP 工具。
    </td>
    <td width="34%" valign="top">
      <b>安全执行环境</b><br /><br />
      权限门、Worktree、WSL 与 HCS VM 按任务选择执行边界；敏感操作保持可见、可确认、可审计。
    </td>
    <td width="33%" valign="top">
      <b>从检查到交付</b><br /><br />
      运行测试、构建和诊断，检查浏览器与 Windows 应用，最后汇总改动、日志、截图、链接和可下载产物。
    </td>
  </tr>
</table>

<br />

<h2 align="center">连接你已经使用的服务</h2>

<p align="center">
  <img src="desktop/src/assets/connectors/github.svg" alt="GitHub" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/slack.svg" alt="Slack" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/notion.svg" alt="Notion" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/google-calendar.svg" alt="Google Calendar" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/google-drive.svg" alt="Google Drive" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/gmail.svg" alt="Gmail" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/postgresql.svg" alt="PostgreSQL" width="42" height="42" />&nbsp;&nbsp;&nbsp;
  <img src="desktop/src/assets/connectors/filesystem.svg" alt="Local Filesystem" width="42" height="42" />
</p>

<p align="center"><sub>Store 与连接器中心提供具体的能力说明、来源、授权方式、可用工具和连接状态。</sub></p>

<br />

<details>
  <summary><b>26.7.17 版本亮点</b></summary>
  <br />
  <ul>
    <li>新增 Metis Design：项目入口、对话式 Studio、页面与素材管理、实时预览、设计系统、版本恢复和多格式导出。</li>
    <li>Design 直接使用 Metis 当前模型配置与 API 凭据，统一主题、语言、任务通知和桌面宠物状态。</li>
    <li>新增任务完成通知、后台运行、会话恢复/未读/归档、自定义宠物库，并改进托盘恢复和模式切换性能。</li>
    <li>完善多标签文件工作区、终端、网页预览、Diff、计划与活动记录，支持浏览器式快捷键和标签搜索。</li>
    <li>扩展 Skills、Connectors、外部 MCP、项目级权限、Worktree、WSL 与隔离 VM 工作流。</li>
    <li>完成 HCS direct runner 的持久化数据盘、guest handshake 和 boot verifier；大型工作区快照改为完整或失败。</li>
  </ul>
</details>

<br />

---

<h2 align="center">本机架构</h2>

<div align="center">
  <img src="backend/assets/metis-local-architecture-26.7.17.png" alt="Metis 本机架构：Chat、Cowork、Code、Design、Python Agent、安全执行与模型 API" width="100%" />
</div>

<table align="center" width="100%">
  <tr>
    <td width="33%" valign="top"><b>Electron Desktop</b><br /><sub>窗口、菜单、OAuth、WebContentsView Preview 与打包后的后端生命周期。</sub></td>
    <td width="34%" valign="top"><b>React Workbench</b><br /><sub>Chat / Cowork / Code / Design、工具活动、右侧工作台、设置中心与状态管理。</sub></td>
    <td width="33%" valign="top"><b>Python Agent</b><br /><sub>Flask + SSE、agent loop、工具注册、技能、浏览器与桌面自动化、checkpoint 和连接器。</sub></td>
  </tr>
</table>

> [!NOTE]
> Renderer 与本机后端通过 HTTP / SSE 通信。API Key 与 OAuth Token 不经过 Metis 中转服务，也不会写入日志或模型上下文。

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
├── open-design/         # 内置 Metis Design 编辑器与导出运行时源码
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
