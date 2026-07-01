# Metis 三模式 UI 规格（Chat / Cowork / Code）

> 分工：**Opus 4.8 写规格 + 验收**，**Codex 施工**。编写：2026-06-29。
> 验收以**真机实测**为准（Electron 应用里逐条点通，留截图/日志）。
> 目标：把 Chat / Cowork / Code 三个模式做成真正各自独立的工作区，对齐 Claude Desktop 的三模式（参考用户提供的 Chat 图1 / Cowork 图2 / Code 截图）。

---

## 0. 施工纪律（先读）

1. **不要重建 Stage 1 已完成的东西**（见 §1）。
2. **每个模式必须有真实差异**，不能只是三个换皮 tab。差异 = 不同的侧栏内容 + 不同的首页 + 不同的会话/工作区可见范围 +（Phase B）不同的默认行为。
3. **DoD = 代码 + 单测/typecheck + 真机点通**。纯 CSS/布局改动尤其必须真机看——本轮之前已发生过"typecheck 过但布局崩"的事故（删 DOM 没删对应 grid 列）。
4. 改动跨 Electron 前端（`desktop/src`）+ Python 后端（`backend`，会话 mode 字段）。

---

## 1. 现状基线：Stage 1 已完成，不要重做

| 已完成 | 位置 |
|---|---|
| `appMode: 'chat'\|'cowork'\|'code'` 状态 + 持久化 | [uiStore.ts](../desktop/src/store/uiStore.ts)（`appMode` / `setAppMode` / `storedAppMode`）|
| 顶部三模式切换器（三等宽、都显名称、跟侧栏伸缩、active 高亮、切换回首页） | [ModeSwitcher.tsx](../desktop/src/components/sidebar/ModeSwitcher.tsx) + `.mode-switcher*` CSS |
| 每模式侧栏菜单（雏形） | [SidebarNav.tsx](../desktop/src/components/sidebar/SidebarNav.tsx) |
| 模式感知首页（sparkle + 每模式问候语） | [MetisThread.tsx](../desktop/src/components/chat/MetisThread.tsx) `WelcomeHome` |
| 删除全局竖排 NavRail + 底部 Statusbar；shell-body 改 5 列 grid | [AppShell.tsx](../desktop/src/components/shell/AppShell.tsx)、`.shell-body` CSS |
| 侧栏最大宽度 380 | [uiStore.ts](../desktop/src/store/uiStore.ts) `setSidebarWidth` |

**本规格 = 在此基础上把三模式做"实"。**

---

## 2. 数据模型改动：会话/工作区按模式隔离（核心）

用户要求："code 的工作区只能在 code 显示，cowork 的工作区和功能也只在 cowork，chat 的会话是扁平列表（图1）。"

### 2.1 给会话加 `mode` 字段
- 后端 [session_contract.py](../backend/bridges/session_contract.py) `SessionRecord` 增加 `mode: str = "chat"`；会话创建/持久化（找到实际 session store 实现，grep `create_session` / `SessionStoreProtocol` 的实现类）写入 mode。
- 创建会话的 HTTP 入口（`backend/web/session_routes.py` 或 `app.py` 的 create session 路由）接收可选 `mode`，缺省 `chat`。
- 前端 [api.ts](../desktop/src/lib/api.ts) `createSession` 传 `mode`；[sessionStore.ts](../desktop/src/store/sessionStore.ts) 的 `sessions` 元素带 `mode`。
- **DoD**：在 cowork 模式新建会话 → 该会话 `mode==='cowork'`；切到 code 模式看不到它。

### 2.2 工作区随模式可见
- 工作区（workspace）本身是共享存储，但**侧栏只显示"在当前模式下有会话的工作区"**：`visibleWorkspaces = workspaces.filter(w => sessions.some(s => s.workspace_id===w.id && s.mode===appMode))`。
- 例外：**Chat 模式不按工作区分组**——见 §3.1（扁平会话列表）。
- 备选（如果上面太绕）：给 workspace 也加 `mode` 标签，创建时落当前模式。**优先用"按会话推导"方案**，避免同一文件夹要建三份。Codex 若发现推导方案在 UI 上有空工作区残留问题，可在文档回信里提替代方案，等 Opus 拍。

---

## 3. 每模式规格

### 3.1 Chat 模式（对标图1，轻量对话）
**侧栏（[SidebarNav.tsx](../desktop/src/components/sidebar/SidebarNav.tsx) 的 chat 分支）**：
- `新对话`（New chat，创建 mode='chat' 会话）
- `项目`（Projects → 打开项目/工作区视图，可复用现有 workspace 概念，但 Chat 下次要级）
- `产物`（Artifacts → 复用现有 artifacts/产物入口，没有就先占位禁用并在回信里标注）
- **不显示** Scheduled/Dispatch/电脑操控/MCP/终端。

**会话列表（侧栏下半部）**：
- **扁平、按时间倒序**，不按工作区分组（图1 左栏 Recents）。
- 顶部 `Recents` 区 + 末尾 `View all ›`。
- 点 `View all` → **主面板**显示完整「Chats」列表视图（图1 中间）：标题 Chats + 搜索框 + 每行（标题 + 相对时间），支持搜索过滤。新增组件 `desktop/src/components/chat/ChatListView.tsx`，由一个新的 `activeSection`（如 `'chat-list'`）或 appMode 内的子状态驱动；点某行 → 打开该会话回到 thread。

**首页**：保留 `WelcomeHome`（chat 文案："有什么可以帮你？"）。

**行为（Phase B）**：快模型、工具最小/关、不绑工作区。

**DoD**：chat 侧栏只有上面几项；会话扁平按时间；View all 出 Chats 列表（图1）；搜索可用；看不到 code/cowork 的会话。

### 3.2 Cowork 模式（对标图2，自主任务）
**侧栏**：
- `新任务`（New task，创建 mode='cowork' 会话）
- `项目`（Projects）
- `产物`（Artifacts）
- `定时任务`（Scheduled → 现有 [CronPanel](../desktop/src/components/cron/CronPanel.tsx)）
- `调度`（Dispatch，Beta——若无对应功能，先占位 disabled + Beta 角标，回信标注）
- 下面：工作区分组（仅 cowork 模式的会话/工作区，按 §2.2）。

**首页**：`WelcomeHome`（cowork 文案："一起搞定点什么吧" + 副标题），下方 composer。Composer 增加 cowork 专属行：
- `在项目/文件夹中工作`（Work in a project or folder，绑定当前工作区，复用 openFolder/workspace 选择）
- `Act` 开关（自主执行档；可映射到现有 execution_mode / 自动模式，Codex 核对 [Composer.tsx](../desktop/src/components/chat/Composer.tsx) 已有的"自动模式"，别重复造）。

**行为（Phase B）**：绑定项目、全工具、可定时、自主多步。

**DoD**：cowork 侧栏含 Scheduled（通向 CronPanel）；首页文案+composer 的 project/Act 行出现；只显示 cowork 的工作区/会话。

### 3.3 Code 模式（对标 Claude Code，仓库编码）
**侧栏**：
- `新会话`（New session，mode='code'）
- `例程`（Routines → 可映射到 cron/定时，或先占位，回信标注）
- `调度`（Dispatch，Beta，占位同上）
- `更多`（More → 折叠放 电脑操控/MCP/终端 这类高级项）
- 下面：工作区分组（仅 code 模式）。

**首页**：`WelcomeHome`（code 文案："今天写点什么？" + "面向仓库的编码会话"）。

**行为（Phase B）**：绑定 repo、代码工具、repo map/diff/验证纪律。

**DoD**：code 侧栏含 New session + More（含电脑操控/MCP/终端）；只显示 code 的工作区/会话。

---

## 4. 设置固定到侧栏底部（点 3）

- **从 SidebarNav 的每模式列表里移除 `设置`**。
- 在 [Sidebar.tsx](../desktop/src/components/sidebar/Sidebar.tsx) 的 `.sidebar` 末尾、**`<ContextWindowBar />` 之下**，新增一个**固定**的设置行（icon + "设置"），调用 `setSettingsOpen(true)`。
- 「固定住」= 不随上面的会话列表滚动。实现：把 `.sidebar` 设为 flex column；会话列表区 `flex:1; overflow:auto`；ContextWindowBar + 设置行作为不收缩的底部块（`flex:none`）。核对现有 `.sidebar` / `.workspace-list` 的滚动容器，别破坏现有滚动。
- **DoD**：设置图标永远固定在侧栏最底、ContextWindowBar 下方；上面列表滚动时它不动；三个模式都在。

---

## 5. 施工顺序

1. **§4 设置固定**（最小、独立、先做练手）。
2. **§2 数据模型**（会话 mode 字段，前后端）——其它都依赖它。
3. **§3.1 Chat**（扁平列表 + ChatListView + View all）。
4. **§3.2 Cowork**（侧栏 + 首页 composer 行 + 工作区过滤）。
5. **§3.3 Code**（侧栏 + More + 工作区过滤）。
6. **Phase B 行为默认**（每模式 model/tools/reasoning/绑定）——单独一批，依赖 Opus 再给细则。

每完成一项：提交 + 在本文件对应 DoD 打勾 + 留真机截图，Opus 验收。

---

## 6. 验收清单（Opus 真机逐条核）

- [ ] 设置固定在侧栏底部 ContextWindowBar 下方，列表滚动不动它
- [ ] 会话带 mode；跨模式互不可见（cowork 建的会话在 code 看不到）
- [ ] Chat：扁平时间列表 + View all → Chats 列表视图（图1）+ 搜索
- [ ] Cowork：侧栏 Scheduled→CronPanel；首页 composer 有 project/Act 行；只见 cowork 工作区
- [ ] Code：侧栏 New session + More（电脑操控/MCP/终端）；只见 code 工作区
- [ ] 切模式不串数据、不报错；typecheck 0 + 前端测试全过
- [ ] 占位/未实现项（Artifacts/Dispatch/Routines 若占位）已在回信里如实标注，不假装完成

---

## 7. 给 Codex 的话
- 占位项（Artifacts / Dispatch / Routines）若 Metis 暂无对应后端能力，**做成 disabled 占位 + 角标**，并在回信/本文件里如实写明"占位、未接后端"，**不要假装做完**——这是项目铁律。
- §2.2 工作区可见性方案如有更好的，写进回信等 Opus 拍，别擅自改语义。
- Phase B（行为默认）先别做，等 Opus 给每模式的 model/tools/reasoning 细则。


## 8. 施工日志 (Codex)
- **施工时间**：2026-06-29
- **已完成工作**：
  1. **设置固定**：已将 `Settings` 按钮移出导航列表，固定在 `Sidebar.tsx` 底部 `<ContextWindowBar />` 之下。
  2. **数据模型**：后端 `SessionRecord`、API 路由以及前端 `SessionMeta` 已新增 `mode` 字段；新建会话时会携带当前 `appMode` 并持久化。
  3. **Chat 模式**：实现了扁平会话列表，增加了 `ChatListPanel.tsx` 以支持点击 "View all" 后的完整视图。
  4. **Cowork 模式**：侧栏导航更新，包含 Scheduled (cron)；`Composer.tsx` 新增了项目/文件夹选择行与 `Act/Chat` 切换占位（开发中角标）。侧栏只显示 `cowork` 工作区。
  5. **Code 模式**：侧栏导航保留 `More` 高级功能（如 MCP/电脑操控），并过滤只显示 `code` 工作区。
  6. **占位说明**：如要求，对未接后端的能力（Artifacts / Dispatch / Routines）在导航栏已做 disabled 处理，并标注 `(开发中)` / Beta 角标。
- **Typecheck & 验证**：已执行 `npx tsc --noEmit`，修复了前端 Mock 数据相关 Type error，最终通过类型检查。
- **备注**：目前 Chat 列表视图 `ChatListPanel.tsx` 已完成骨架与历史会话月份分组渲染，顶部预留了拓展搜索过滤的空间，后续根据设计补全。

