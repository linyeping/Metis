# Metis 对标 Claude Cowork — 规划文档

> 角色分工：本文档由 **Opus 4.8 规划 + 验收**，**Sonnet 4.6 施工**。
> 编写日期：2026-06-29。验收以**真机实测**为准（沿用项目铁律：评测必 repeat≥3 看事件流，不只看聚合分）。

---

## 0. 给施工者的前置纪律（先读，别跳过）

1. **不要重建已有机制。** 下面 §2 列了 Metis 已经实现的可靠性机制（含 file:line）。施工前先确认，不要照外部"最佳实践清单"重造轮子。
2. **三个伪命题，不要做：**
   - ❌ **cache-first / prefix-cache 改造**——已用两次 A/B 实测证明：缓存健康**不是** DeepSeek 的瓶颈。
   - ❌ **多代理协作层（Architect/Coder/Tester 并行写）**——已有只读并行扇出（[parallel_subagents.py](../backend/runtime/parallel_subagents.py)），且 Cowork 本身不是多代理产品。
   - ❌ **用 prompt/nudge 掰 DeepSeek 行为**——已实测 DeepSeek 对软提示免疫。
3. **Cowork 是什么（对齐认知）：** Anthropic 的桌面 agentic 产品——跑在桌面、连本地文件与应用、给目标后自主多步交付成品、关键决策保留给人。Metis **本来就是这个品类**，不是"缺一个 Cowork 模块"。差的是下面几条具体能力。
4. **每个任务的 DoD（完成定义）** = 代码 + 单测 + **真机实测**（在 Windows 桌面应用里真的跑通一次，留下证据）。文档/JSON 改动可豁免编译验证，代码改动不可。

---

## 1. 目标与不做的边界

**目标：** 把 Metis 从"能连 2 个服务的雏形"补齐到"对标 Cowork 的连接广度 + 自主任务可靠性"，同时保住 Metis 独有优势（Preview Browser、Computer Use、本地优先、无遥测）。

**本轮范围（按优先级）：**
- **P1 连接器扩展 + 安全 token 接线**（高，本文档主体，execute-ready）
- **P2 DeepSeek 可靠性调参**（高，eval 驱动，本文给方法不给死参数）
- **P3 表格(xlsx) 工具**（中）
- **P4 交付物收口**（中）

**明确不做：** 后台无人值守调度（Cowork 是 attended 模式，优先级低）；§0.2 的三个伪命题。

---

## 2. 现状基线：Metis 已有的，不要重建

| 能力 | 代码位置 | 状态 |
|---|---|---|
| Tool-call repair（修畸形 tool call） | [agent_loop.py:76](../backend/runtime/agent_loop.py#L76), :1176, :1820 | ✅ 2 次重试 |
| 重试 + 可/不可重试分类 | [error_catalog.py](../backend/runtime/error_catalog.py), agent_loop.py:1139 | ✅ |
| Turn budget 动态阈值提示 | [agent_loop.py:621-658](../backend/runtime/agent_loop.py#L621) | ✅ |
| 反 churn（反复写待办不动手） | agent_loop.py:2101 `detect_todo_churn` | ✅ |
| 自动压缩 / 逐出 / checkpoint | agent_loop.py:667, :710, :803 | ✅ |
| Route-aware / capability mapping | [provider_profiles.py](../backend/bridges/provider_profiles.py), [model_capability.py](../backend/bridges/model_capability.py), [model_router.py](../backend/runtime/model_router.py) | ✅ |
| MCP 客户端（stdio/sse + 工具注册） | [mcp_client.py](../backend/runtime/mcp_client.py)（715 行）| ✅ |
| 验证纪律（强制 cite 验证结果） | [loop_discipline.py](../backend/runtime/loop_discipline.py) | ✅ |
| 只读并行子智能体扇出 | [parallel_subagents.py](../backend/runtime/parallel_subagents.py) | ✅ |
| 权限控制 / 审计 | permission_control.py, action_audit.py | ✅ |
| 产物工具（docx/pdf/report） | [backend/tools/artifacts](../backend/tools/artifacts) | ✅ |

**结论：可靠性"机制"基本齐全。Metis 的问题是机制的调参与连接广度，不是机制缺失。**

---

## 3. P1 — 连接器扩展 + 安全 token 接线（execute-ready）

### 3.1 现状与差距（已核实）

- 连接器目录只有 **GitHub + Gmail 两条**，且仅为 MCP 配置模板：[connectors/registry.py](../backend/runtime/connectors/registry.py)。
- MCP 的 `auth_token` **目前只从 config JSON 文件读**（[mcp_client.py:566](../backend/runtime/mcp_client.py#L566)、[:613-618](../backend/runtime/mcp_client.py#L613)），**没有接任何安全 token 存储**，也**没有 OAuth 获取流程**——`registry.py` 里 "device flow preferred" 只是注释，未实现。
- 全仓只有一个测试引用连接器（[test_connectors_mcp_auth.py](../backend/tests/test_connectors_mcp_auth.py)）。

差距 = ① 连接器数量太少；② 没有安全 token 存储；③ 没有获取 token 的 OAuth 流程；④ 没有给 UI/agent 用的管理面（list/connect/test/disconnect）。

### 3.2 设计原则（必须遵守）

- **Token 永远经 env 注入，绝不进命令行 args、绝不进 status 输出。** 复用现有约定（[mcp_client.py:_stdio_env_for_config](../backend/runtime/mcp_client.py#L613)），并扩展现有测试断言。
- **连接器定义是纯数据**，与"如何拿到 token"解耦。`ConnectorDefinition` 描述 service；token 来源由独立的 token store 提供。
- **复用 Electron `safeStorage` 做加密落盘**（桌面端已有加密 token 的先例，施工时先 grep 确认入口；后端不要自己造对称加密）。
- **本地优先：** 所有 token 落在用户本机，不上传，不进遥测，不进审计原文（审计里按现有 redaction 规则脱敏）。

### 3.3 任务清单（Sonnet 按序施工）

**T1. 扩充连接器目录（纯数据，最快见效）**
- 文件：[connectors/registry.py](../backend/runtime/connectors/registry.py)
- 在 `_CONNECTORS` 中新增定义（每条含 `service_id / display_name / scopes / token_env / mcp 模板 / notes`）：
  - `google_drive`、`google_calendar`（复用 Google OAuth，token_env 沿用/区分 scope）
  - `slack`（`SLACK_BOT_TOKEN` 或 OAuth）
  - `notion`（`NOTION_API_KEY`）
  - `filesystem`（本地目录 MCP，无 token，验证"无 token 连接器"路径）
  - `postgres`（连接串经 env）
- 每条都要有可用的 `mcp` 启动模板（command/args）。优先选社区成熟、可 `npx`/二进制启动的 MCP server。
- DoD：`connector_catalog()` 返回这些；扩展 [test_connectors_mcp_auth.py](../backend/tests/test_connectors_mcp_auth.py) 覆盖新条目的 `token_env` 与"无 token 连接器"。

**T2. 安全 token 存储接线 — ✅ 已完成（2026-06-29），但架构与原计划不同（见下）**

> **施工时的重大发现（推翻原计划假设）：** 桌面端**早已有**完整连接器子系统 [oauth.cjs](../desktop/electron/oauth.cjs)：safeStorage 加密、`userData/connectors/<service>.enc` 落盘、github device flow / gmail PKCE / 手动 token、status/disconnect IPC。且 [backend.cjs:3616](../desktop/electron/backend.cjs) 已确立"桌面 spawn 后端时解密密钥 → env 注入"的通道（`METIS_LLM_API_KEY`）。
>
> **关键约束：** `.enc` 是 OS 级 safeStorage（Windows=DPAPI）加密，**Python 后端解不了**。所以"后端读 .enc 文件"走不通——必须桌面端解密后经 env 注入。因此：
> - **写/删 token 是桌面端的活**（oauth.cjs），原计划的 Python `set_token/delete_token` **故意不做**（后端自加密=违反"不自造加密"；后端存明文=泄漏）。token_store 只做**只读查询层**。

实际落地：
- 新增 [token_store.py](../backend/runtime/connectors/token_store.py)：`get_token / is_connected / list_connected`（env 之上的只读视图，按 registry 的 token_env 取值）。无 set/delete（理由见上）。
- 改 [mcp_client.py](../backend/runtime/mcp_client.py)：`MCPServerConfig` 加 `service_id` 字段；`_stdio_env_for_config` 解析顺序 = **token_store(按 service_id) → config auth_token**（旧 config 向后兼容）；config loader 解析 `service_id`。
- **补上真实缺口**：桌面端原来**只注入 LLM key、不注入连接器 token**。新增 [oauth.cjs `decryptStoredConnectorTokens`](../desktop/electron/oauth.cjs) + [backend.cjs `injectConnectorTokens`](../desktop/electron/backend.cjs)，spawn 时把连接器裸 token 解密注入 backend env（gmail JSON blob 跳过、无 token 连接器跳过）。CONNECTORS 补齐 slack/notion/postgres（手动 token）。
- DoD 达成：Python 18 测试绿（含"token 来自 store 正确注入 env、不入 args/status"）；桌面 `decryptStoredConnectorTokens` 用伪 safeStorage 单测通过（github/slack 注入、gmail 跳过、未存的不出现）。
- **真机残留**：端到端（Electron 启动→设置里授权→重启后端→后端 token_store 读到）**未真机走通**，需真跑 app + 真 token；逻辑各段已分别验证。

**T3. 连接器管理面（给 UI/agent 调用）— ✅ 已完成（2026-06-29）**

> 落地：新增 [manager.py](../backend/runtime/connectors/manager.py)，暴露 `list_connectors / connect / test / disconnect / build_config`。
> - **token 不落盘**（沿用 T2 边界）：`connect(service_id, token="", allowed_dir="")` 的 token 仅本会话内存用，缺省从 token_store(env) 取；写/删仍是桌面端的活。
> - filesystem 的 `<ALLOWED_DIR>` 占位在 connect 时替换为传入目录；无 token 连接器/无目录会在 spawn 前被优雅拒绝。
> - 复用进程级全局 MCPManager（连接器工具与 config-file MCP 工具同处一个 registry）；disconnect 用 `remove_tools_by_source("mcp:<service>")` 注销。
> - **附带修复一个既有跨平台 bug**：[mcp_client.py `_connect_stdio`](../backend/runtime/mcp_client.py) 原来 `subprocess.Popen(["npx", ...])` 不带 shell，在 **Windows 上找不到 `npx.cmd`**（`WinError 2`）——这会让所有 npx 型 MCP server（含既有 gmail 连接器）在 Windows 起不来。改用 `shutil.which(command, path=env PATH)` 解析，POSIX/找不到时回退原值。
> - DoD 达成：10 个 manager 单测 + 真机 e2e（filesystem `connect→注册14工具→test→disconnect→工具清零`，受 `METIS_E2E_MCP=1` 开关保护，已真机 1 passed）。连接器全套 28 passed + 1 skipped；全量后端 739 passed（6 个 `test_isolated_runtime` 失败为**预先存在**的 VM-pack 资产缺失，已 stash 对照确认与本改动无关）。

原始 T3 设计（保留作参考）：
- 新增 `backend/runtime/connectors/manager.py`（或在 `__init__` 暴露）：
  - `list_connectors()` → catalog + 每条的 connected 状态（来自 token_store）。
  - `connect(service_id, token)` → 存 token、构造 `MCPServerConfig`、调用 [register_mcp_tools](../backend/runtime/mcp_client.py#L573) 注册工具、返回成功注册的工具名。
  - `test(service_id)` → 用最轻量的只读调用验证连通（如 list_tools / 一个只读 RPC），返回 ok/err。
  - `disconnect(service_id)` → 删 token + `disconnect_one` + 注销工具。
- DoD：在 Python 层能 `connect → test → 工具出现在 registry → disconnect → 工具消失` 走一遍（写成 e2e 测试 + 真机各跑一次）。
- **⚠️ postgres eager-connect 风险（T1 真机验收发现，2026-06-29）：** 当前 `mcp-postgres` 在**启动时就硬连数据库**，库不可达时连 MCP `initialize` 握手都不完成（实测报 `Connection attempt failed` / `Client has already been connected`）。后果：
  - `connect()` 对 postgres 不能假设"进程起来 = 成功"——必须把"DB 可达"纳入 `connect`/`test` 的判定，并把 server 启动期的 stderr 作为失败原因回传，而不是静默挂起。
  - `test()` 设计要容忍"server 进程在、但上游不通"这种半连接态。
  - 二选一收尾：① 给 postgres 的 `test()` 专门处理 eager-connect 失败；② 换一个 **lazy-connect**（initialize 不碰 DB）的 postgres MCP server。slack/notion/filesystem 无此问题（均 lazy，initialize 握手已真机通过）。

**T3-UI. 连接器管理面接 HTTP + ConnectorsTab 按钮 — ✅ 已完成（2026-06-29）**

> 落地：
> - **后端 HTTP 路由**（[app.py](../backend/web/app.py)，仿 `/mcp/*` 风格）：`GET /connectors`、`POST /connectors/connect|test|disconnect`。token 从进程 env(token_store) 取，**明文不经渲染进程/HTTP**；filesystem 的 allowed_dir 缺省取活动 workspace 根。操作结果一律 200+`ok` 标志（"没 token"是预期状态非 HTTP 错误），仅缺 `service_id` 返回 400。
> - **前端 api**（[api.ts](../desktop/src/lib/api.ts)）：`listBackendConnectors / connectConnector / testConnector / disconnectConnector`。
> - **UI**（[ConnectorsTab.tsx](../desktop/src/components/settings/tabs/ConnectorsTab.tsx)）：合并后端 active/toolsCount 状态，加**激活 / 测试连通 / 停用**按钮（与既有 token 授权/删除分开）；无 token 连接器（filesystem）隐藏 token 输入，只显示激活组。filesystem 已补进 [oauth.cjs](../desktop/electron/oauth.cjs) CONNECTORS 以便出卡片。
> - **验证**：后端 Flask test-client e2e PASS（GET→connect filesystem 注册14工具→test→`GET active:true`→disconnect 清零→slack 无 token 200+ok:false）；前端 `tsc --noEmit` 0 错误；契约 80/80；Python 连接器 28 passed+1 skipped。
> - **残留（诚实标注）**：以上每一层都已程序化验证，但**真机 Electron 应用里手点按钮**那一下我没做（需启动 GUI+后端引导）。结构上 typecheck + 已接线的 handler + 后端 e2e 已覆盖该路径；最后的 GUI 点击建议在真机由你点一次确认（filesystem 最省事，无需 token/重启）。
> - 新授权的 token 需**重启后端**才会注入 env（token 在 spawn 时注入）——UI 已用提示文案说明。

**T4. OAuth 获取流程（device flow / PKCE loopback）— ⚠️ 大部分已存在（T2 施工时发现）**

> **现状更正：** 这块**不在 Python 后端、而在桌面端**，且**已经实现**：[oauth.cjs](../desktop/electron/oauth.cjs) 有 GitHub device flow（`authorizeGitHubDeviceFlow`）、Gmail PKCE loopback（`authorizeGmailPkce` + `waitForLoopbackCode`）、手动 token、safeStorage 落盘。所以原计划的 `backend/runtime/connectors/oauth.py` **不必新建**。
>
> **T4 真正剩下的：**
> 1. **Google Drive/Calendar 的"凭据文件路径"模型**怎么支持（见 [registry.py](../backend/runtime/connectors/registry.py) 模块注释）——这两个 MCP server 不吃单一 bearer token，需要 oauth.cjs 决定如何产出/缓存它们要的凭据文件。
> 2. slack/notion/postgres 目前**只有手动 token**（无 OAuth）——如需 OAuth 要各自加 provider 分支。
> 3. 真机各跑通一次 github/gmail 授权（DoD 不变：真机录屏/日志为证）。
>
**T4 收尾（2026-06-29）：**

**(A) Google Drive/Calendar 凭据文件路径模型 — ✅ 已完成（代码 + hermetic 测试）**
- [registry.py](../backend/runtime/connectors/registry.py)：`ConnectorDefinition` 加 `auth_kind`（`bearer_token` / `credentials_file` / `none`）+ `credentials_envs`。重新加入 `google_drive`（`GDRIVE_OAUTH_PATH` + `GDRIVE_CREDENTIALS_PATH`）、`google_calendar`（`GOOGLE_OAUTH_CREDENTIALS`），标记为 `credentials_file`；filesystem 标 `none`。
- [token_store.py](../backend/runtime/connectors/token_store.py)：`credentials_ready()` = 所有 credentials_envs 都设了且文件存在；`is_connected` 按 auth_kind 分流。
- [manager.py](../backend/runtime/connectors/manager.py)：`build_config` 把 credentials_envs 的路径转发进 server 的 `env`；`connect()` 对 credentials_file 做就绪门控，未就绪返回一次性 auth 引导（不挂起）。
- 验证：connector 套件 **33 passed + 1 skipped**；HTTP `/connectors` 返回 8 条，`POST connect google_drive` 未配置时 200+ok:false + 正确引导。
- **诚实边界**：这两个 server 的**一次性交互授权是 server 自己驱动的**（`npx -y <server> auth` 开系统浏览器、把 token 缓存到 GDRIVE_CREDENTIALS_PATH/自身缓存），**后端无法 headless 代跑**；connect 只在该缓存已存在后才成功。UI 的文件选择器 + 一次性 auth 触发是**剩余的 UI 工作**（gdrive/calendar 暂未进 oauth.cjs 的卡片列表，仅后端建模 + `/connectors` 可见）。

**(B) github/gmail 真机 OAuth — ⚠️ 用户驱动，我无法代跑**
- 接线**已就绪且已读核**：[oauth.cjs](../desktop/electron/oauth.cjs) `authorizeGitHubDeviceFlow`（需 `METIS_GITHUB_CLIENT_ID`）、`authorizeGmailPkce`（需 `METIS_GOOGLE_CLIENT_ID`）、safeStorage 落盘、spawn 注入（T2）。
- **为什么我不能"跑一次"**：① 需要你自己的 OAuth client ID（我不能凭空造）；② 必须你在浏览器里亲自点"同意"——这是项目安全红线（OAuth 授权属"需显式许可"，Agent 绝不代点/代填凭据）。
- **真机 runbook（你来执行）：**
  1. GitHub：建一个 OAuth App（Device Flow），把 client id 设到环境变量 `METIS_GITHUB_CLIENT_ID`，重启 app。
  2. 设置 → 连接器 → GitHub → 点「OAuth 连接」→ 浏览器里输入 user code 授权。
  3. 重启后端（token 在 spawn 时注入 env）→ 回连接器点「激活」→ 应显示运行中 + 工具数 →「测试连通」绿。
  4. Gmail 同理：`METIS_GOOGLE_CLIENT_ID` + 测试模式加测试用户。
- **安全红线（项目级、不可违反）：** OAuth/SSO 授权必须由用户在系统浏览器亲自完成，Metis 只发起流程/接收回调/存 token，**绝不代填凭据、绝不替用户点同意**。

### 3.4 P1 验收清单（Opus 验收时逐条核）
- [x] catalog ≥ 6 个连接器，含 1 个无 token 的（filesystem）— **T1 完成，实测 6 条**
- [x] token 一律 env 注入，args/status 无明文（自动化断言通过）— **红线测试 `test_no_connector_leaks_secret_in_args` 绿**
- [x] token 落盘走桌面端加密，后端无自造加密 — **T2 完成（safeStorage 落盘 + spawn env 注入；后端只读）**
- [x] `connect→test→disconnect` 在真机走通一次（留证据）— **T3 完成：filesystem 真机 e2e PASS（注册14工具→test→清零）。注：在 Python 层真机走通；Electron 应用内 UI 串联待 T3-UI/真机**
- [~] 至少 1 个连接器（建议 GitHub）跑通 OAuth 真机授权（T4）— **接线就绪+已核；OAuth 授权用户驱动（需 client id + 浏览器点同意），见 T4(B) runbook，待你真机走一次**
- [x] 不回归：旧 config-file MCP 仍能加载 — **T2 测试 `test_explicit_auth_token_used_when_no_service_id` 覆盖**

#### T1 真机验收记录（2026-06-29，Opus 复核）
- 新增连接器：slack / notion / filesystem / postgres（+ 既有 github / gmail = 6）。
- 单测：`pytest backend/tests/test_connectors_mcp_auth.py` → **6 passed**。
- **真机 MCP `initialize` 握手实测**（非仅 `npm view bin`）：
  - ✅ filesystem（`secure-filesystem-server` 0.2.0）
  - ✅ slack（`Slack MCP Server` 1.0.0）
  - ✅ notion（`Notion API` 1.0.0）
  - ⚠️ postgres：**未通过**，eager-connect，详见 T3 风险条。
- google_drive / google_calendar：本轮跳过（OAuth-凭据文件路径模型，不吃 token_env），原因见 [registry.py](../backend/runtime/connectors/registry.py) 模块级注释，留待 T4。

---

## 4. P2 — DeepSeek 可靠性调参（eval 驱动，给方法不给死参）

**判断：** "DeepSeek 效果还不行"的真因不是缺机制（机制见 §2），而是已有阈值在 DeepSeek 上**未必触发在对的时机**。这要 eval 实测调，不能拍脑袋。

**要调的参数（都已存在，找到并做成可 A/B 的开关）：**
- Turn budget 警告阈值：[agent_loop.py:621](../backend/runtime/agent_loop.py#L621) `_turn_budget_warn_threshold`
- 反 churn 触发条件：`detect_todo_churn`（agent_loop.py:2101）
- 自动压缩比例：[agent_loop.py:658](../backend/runtime/agent_loop.py#L658) `_auto_compact_ratio`
- tool-call repair 次数：[agent_loop.py:76](../backend/runtime/agent_loop.py#L76) `METIS_TOOL_CALL_REPAIR_ATTEMPTS`

**方法（铁律）：**
1. 在 [backend/evals](../backend/evals) 里挑/补 3~5 个 DeepSeek 真实任务（代码重构、bug 修复、多文件理解）。
2. **每个配置 repeat≥3，看事件流（churn 次数、turn 触顶率、压缩触发时机），不只看聚合分。**
3. 一次只动一个阈值，A/B 对照。负向结果也要记录（沿用 deepseek 实验的记录习惯）。
4. 产出：把"在 DeepSeek 上验证有效"的阈值设为该 family 的默认（家族键化，已有基础设施）。

**DoD：** 至少一个阈值改动有 repeat≥3 的 A/B 证据支撑，落成 family 默认 + 一条实验记录。

---

## 5. P3 — 表格(xlsx) 工具（中）

**差距：** artifacts 有 docx/pdf/report，**无一等的表格工具**（全仓 xlsx 命中只在沙箱 rootfs，不是 agent 工具）。知识工作大量是 Excel/CSV，Cowork 主打"文档+数据"。

**任务：** 在 [backend/tools/artifacts](../backend/tools/artifacts) 新增 `xlsx_tools.py`：读（含多 sheet/公式值）、写、改单元格/区域、CSV↔XLSX。用 `openpyxl`（沙箱镜像已含，见 §基线 grep）。注册进工具表，纳入权限分级（写操作需许可）。
**DoD：** 真机让 DeepSeek 完成一个"读 xlsx → 算 → 写回新表"的任务并验证输出正确。

---

## 6. P4 — 交付物收口（中）

**差距：** 有产物生成，但没有 Cowork 那种"这是给你的成品"的显式出口。
**任务：** 约定一个会话级 deliverables 收口（一个 outputs 目录 + 在 UI 明确标注"本次产出"），让自主任务结束时把成品集中呈现，而不是散落在工作区。
**DoD：** 一个多步任务结束后，UI 能一处看到所有产物。轻量，最后做。

---

## 7. 施工顺序建议

1. **P1-T1**（连接器目录，最快见效，纯数据）
2. **P1-T2 + T3**（token 存储 + 管理面，让连接器真能用）
3. **P2**（DeepSeek 调参，和 P1 并行也行，因为是 eval 工作）
4. **P1-T4**（OAuth，硬，独立提交）
5. **P3 → P4**

每完成一个 P*-T*，提交一次（小步），并在本文件对应 DoD 打勾，Opus 验收。
