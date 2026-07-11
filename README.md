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

<p>
  <a href="https://github.com/linyeping/Metis/releases/download/v26.7.11/Metis-Setup-26.7.11.exe"><img alt="下载 Metis 26.7.11" src="https://img.shields.io/badge/下载_Metis-26.7.11-357EC7?style=for-the-badge&logo=windows11&logoColor=white" /></a>
  <a href="https://github.com/linyeping/Metis/releases/tag/v26.7.11"><img alt="查看 Release Notes" src="https://img.shields.io/badge/查看-Release_Notes-2E8B72?style=for-the-badge&logo=github&logoColor=white" /></a>
</p>

<sub>Windows 10 / 11 · 64 位 · DeepSeek / OpenAI-compatible API</sub><br />
<sub><a href="README.en.md">English</a> · 由 <a href="https://github.com/linyeping">linyeping</a> 打造 · 产品方向与部分设计思路致谢 <a href="https://github.com/Serein0812">Serein</a></sub>

</div>

<br />

<h2 align="center">一个工作台，三种执行强度</h2>

<p align="center">
  不同任务进入各自最合适的工作面，但共享同一套会话、工具、权限和证据链。
</p>

<p align="center">
  <img src="backend/assets/work-surfaces.png" alt="Metis Chat、Cowork 与 Code 三种工作面" width="100%" />
</p>

<p align="center">
  <b>CHAT</b> 快速理解与轻量执行 &nbsp;·&nbsp;
  <b>COWORK</b> 计划驱动的多步骤交付 &nbsp;·&nbsp;
  <b>CODE</b> 围绕仓库的工程闭环
</p>

<p align="center"><sub>模式之间的会话、工作区和草稿相互隔离；快速切换不会让旧请求覆盖当前记录。</sub></p>

<br />

<h2 align="center">不止回答，而是把工作做完</h2>

<p align="center">
  Metis 把模型推理、项目上下文、工具调用、权限控制与结果验证放进同一条本机工作流。
</p>

<div align="center">
  <img src="backend/assets/Feature%20Showcase.png" alt="Metis 可执行代码、浏览器、桌面与技能任务" width="100%" />
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

<h2 align="center">本机架构</h2>

<div align="center">
  <img src="backend/assets/Architecture.png" alt="Metis 本机架构" width="100%" />
</div>

<table align="center" width="100%">
  <tr>
    <td width="33%" valign="top"><b>Electron Desktop</b><br /><sub>窗口、菜单、OAuth、WebContentsView Preview 与打包后的后端生命周期。</sub></td>
    <td width="34%" valign="top"><b>React Workbench</b><br /><sub>Chat / Cowork / Code、工具活动、右侧工作台、设置中心与状态管理。</sub></td>
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
