# Metis Core Stabilization 02 - Agent Event v2 与工具生命周期

> 编写日期：2026-07-06
> 目标：定义 `metis.agent_event.v2`，让工具卡状态由后端完整生命周期驱动，前端 reducer 不再猜。

---

## 0. 结论

现有 `metis.agent_event.v1` 已经把事件包成 `schema/kind/event_id/timestamp/payload`，这是正确方向。但 v1 仍把大量 legacy 字段放在顶层，前端也会用 `call-${Date.now()}`、按 tool name 匹配 running tool 等方式补洞。

v2 的核心不是换名字，而是建立这些硬约束：

- 每个事件都有稳定 envelope。
- `run_id/session_id/turn_id/message_id/seq/event_id` 都由后端生成或确认。
- 工具生命周期由后端显式发送。
- 前端只按 `call_id` 更新工具卡。
- snake/camel/旧字段兼容只存在一个 adapter 文件里，不能进入 reducer。

---

## 1. 现状基线

| 能力 | 当前文件 | 状态 |
|---|---|---|
| v1 schema | `backend/bridges/event_contract.py` | `metis.agent_event.v1` |
| v1 serializer | `backend/bridges/event_serializer.py` | canonical payload + legacy 顶层字段混合 |
| tool call/result event | `backend/runtime/agent_loop.py` | 有 `ToolCallEvent`、`ToolResultEvent` |
| permission event | `backend/runtime/agent_loop.py` | 有 `PermissionRequestEvent` |
| 前端 normalize | `desktop/src/lib/agentEvents.ts` | 接收多种 legacy 形状 |
| 工具 reducer | `desktop/src/store/sseParser.ts` | 缺 call_id 时会按 tool name 合并 |
| 工具 transcript 重建 | `desktop/src/store/messageOps.ts` | 已有，方向正确 |

现状最危险的点：

- `desktop/src/lib/agentEvents.ts` 会生成 `call-${Date.now()}`。
- `desktop/src/store/sseParser.ts` 会 `findRunningToolByName`，导致同名并发工具可能合并错。
- `done/error/cancel` 会关闭未终结工具，这可以保留为异常兜底，但不能作为正常生命周期。

---

## 2. `metis.agent_event.v2` Envelope

所有 v2 事件强制带：

```json
{
  "schema": "metis.agent_event.v2",
  "version": 2,
  "run_id": "run_xxx",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "message_id": "assistant-run-xxx",
  "seq": 12,
  "event_id": "evt_run_xxx_000012",
  "timestamp": "2026-07-06T09:30:00.000Z",
  "kind": "tool_running",
  "payload": {}
}
```

字段规则：

- `seq`：run 内单调递增，后端分配。
- `event_id`：后端分配，建议由 `run_id + seq` 派生，便于幂等。
- `timestamp`：ISO 8601 UTC 字符串。v1 可继续输出 float timestamp，v2 固定字符串。
- `message_id`：对应本次 assistant message，不由前端临时生成后反向污染后端。
- `payload`：唯一承载业务字段的位置。v2 不再在顶层重复 `tool/toolName/callId`。

---

## 3. Event Kind

第一阶段 v2 支持这些 kind：

| kind | 用途 |
|---|---|
| `message_delta` | assistant 文本增量 |
| `message_completed` | assistant 文本收口 |
| `thinking_delta` | 可见思考/状态增量 |
| `tool_requested` | 模型请求工具，工具卡创建 |
| `permission_required` | 工具需要权限，关联 `request_id` |
| `permission_answered` | 用户或规则已答复 |
| `tool_running` | 工具开始执行 |
| `tool_succeeded` | 工具成功 |
| `tool_failed` | 工具失败 |
| `tool_canceled` | 工具取消 |
| `tool_timed_out` | 工具超时 |
| `artifact_created` | 产物创建 |
| `runtime_status` | 非工具类运行状态 |
| `subrun_planned` | Cowork subrun 已进入计划 |
| `subrun_running` | Cowork subrun 正在执行 |
| `subrun_waiting_permission` | Cowork subrun 等待权限答复 |
| `subrun_succeeded` | Cowork subrun 成功结束 |
| `subrun_failed` | Cowork subrun 失败结束 |
| `subrun_canceled` | Cowork subrun 被取消 |
| `subrun_promoted` | Cowork subrun diff 已应用到主 workspace |
| `run_completed` | run 正常结束 |
| `run_failed` | run 异常结束 |
| `run_canceled` | run 被取消 |

旧的 `tool_call`、`tool_result` 保留在 v1 adapter，不进入 v2 reducer。
旧的 `subagent_start/progress/done` 只作为 legacy adapter 兼容，不再是 Cowork subrun 主协议。

---

### 3.1 Cowork Subrun Payload

`subrun_*` 事件的 `payload` 必须包含：

```json
{
  "schema": "metis.cowork_subrun_event.v1",
  "version": 1,
  "subrun_id": "subrun_xxx",
  "title": "Inspect implementation",
  "objective": "Map the current implementation.",
  "inputs": ["Parent goal", "Current workspace"],
  "expected_artifacts": ["diff summary", "validation evidence"],
  "acceptance_criteria": ["Changed files or evidence are attached."],
  "execution_profile": "local_worktree|local_vm",
  "dependencies": ["subrun_previous"],
  "status": "planned|running|waiting_permission|succeeded|failed|canceled|promoted",
  "stage": "agent_running",
  "progress": 40,
  "evidence": {
    "schema": "metis.cowork_subrun_evidence.v1",
    "success_evidence": true,
    "missing_success_evidence": false,
    "counts": {
      "diff": 1,
      "artifacts": 0,
      "stdout_test": 1,
      "failure_reasons": 0
    }
  }
}
```

规则：

- `subrun_id` 由后端生成。
- `dependencies` 只能引用同一 plan 中更早的 `subrun_id`。
- terminal event 必须有 `evidence`。成功 subrun 至少要有 `diff`、真实 artifact、stdout/test evidence 之一。
- 失败或取消 subrun 必须有 `failure_reasons`。如果 subrun 没有 diff、artifact、stdout/test，也没有失败原因，后端强制转成 `subrun_failed`，原因码为 `SUBRUN_MISSING_EVIDENCE`。
- coordinator 自动生成的 subrun report 不算成功 evidence；它只是记录 evidence 的持久报告。
- `subrun_promoted` 是用户 review/promote 后的状态，不由普通执行完成自动产生。

---

## 4. 工具生命周期协议

标准生命周期：

```text
tool_requested
  -> permission_required?
  -> permission_answered?
  -> tool_running
  -> tool_succeeded | tool_failed | tool_canceled | tool_timed_out
```

### 4.1 `tool_requested`

```json
{
  "call_id": "call_xxx",
  "tool_name": "write_file",
  "display_name": "Write file",
  "arguments": {},
  "arguments_preview": "path=...",
  "risk": {
    "level": "low|medium|high",
    "requires_permission": true
  }
}
```

规则：

- `call_id` 必须稳定，后端生成或透传 provider tool_call id。
- 前端收到后创建工具卡，状态为 `requested`。
- 不允许前端自己生成 call id。

### 4.2 `permission_required`

```json
{
  "call_id": "call_xxx",
  "request_id": "perm_xxx",
  "tool_name": "write_file",
  "permission": {
    "schema": "metis.permission_request.v1",
    "status": "requested"
  }
}
```

规则：

- 工具卡状态变为 `waiting_approval`。
- 权限详情走 03 文档定义的 permission schema。

### 4.3 `permission_answered`

```json
{
  "call_id": "call_xxx",
  "request_id": "perm_xxx",
  "approved": true,
  "grant": "temporary_root",
  "source": "user|rule|timeout"
}
```

规则：

- 这是后端确认已收到答复的事件，不是前端点击后本地乐观状态。
- 可以允许前端点击后显示 pending，但最终状态以该事件为准。

### 4.4 `tool_running`

```json
{
  "call_id": "call_xxx",
  "tool_name": "write_file",
  "started_at": "2026-07-06T09:30:02.000Z"
}
```

### 4.5 终结事件

成功：

```json
{
  "call_id": "call_xxx",
  "tool_name": "write_file",
  "status": "success",
  "result_preview": "Wrote file ...",
  "artifacts": ["art_xxx"],
  "completed_at": "2026-07-06T09:30:04.000Z"
}
```

失败：

```json
{
  "call_id": "call_xxx",
  "tool_name": "write_file",
  "status": "error",
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "User denied execution",
    "recoverable": true
  },
  "completed_at": "2026-07-06T09:30:04.000Z"
}
```

---

## 5. 后端施工

### 5.1 增加 v2 contract

建议文件：

- `backend/bridges/event_contract_v2.py`
- `backend/bridges/event_serializer_v2.py`

不要直接删 v1。v1 继续服务 legacy `/chat` 和旧测试。

### 5.2 在 run 层包装 envelope

不要让 agent loop 自己知道 `seq`。agent loop 只产 runtime event；run service 负责：

- 分配 `seq`
- 分配 `event_id`
- 注入 `run_id/session_id/turn_id/message_id`
- 调用 v2 serializer
- 写入 run event buffer

### 5.3 统一 call_id

在 provider/tool adapter 边界保证：

- provider 有 tool call id：直接使用或标准化。
- provider 没有 tool call id：后端生成 `call_<uuid>`。
- 同一个工具从 requested 到 terminal event 一直使用同一个 `call_id`。
- `request_id` 只用于权限请求，不替代 `call_id`。

### 5.4 工具 transcript

现有 transcript-only tool record 是对的，保留并扩展：

```json
{
  "metis_kind": "tool",
  "metis_tool": {
    "call_id": "call_xxx",
    "name": "write_file",
    "arguments": {},
    "status": "success",
    "result": "...",
    "source_event_id": "evt_run_xxx_000017",
    "artifacts": ["art_xxx"]
  }
}
```

---

## 6. 前端施工

### 6.1 新增唯一 adapter

建议新增：

- `desktop/src/lib/agentEventV2.ts`
- `desktop/src/lib/legacyAgentEventAdapter.ts`

规则：

- v2 事件直接通过 `agentEventV2.ts` 校验。
- v1/legacy/snake/camel 只在 `legacyAgentEventAdapter.ts` 转成 v2-like internal event。
- reducer 只接受 canonical internal event。

### 6.2 reducer 改造

`desktop/src/store/sseParser.ts`：

- 删除正常路径里的 `findRunningToolByName`。
- `upsertTool` 只按 `callId` 更新。
- 若 terminal tool event 缺 `call_id`，adapter 标为 protocol error，不合并到已有卡片。
- `done/error/cancel` 可以把仍 open 的工具标为 `error/canceled`，但要记录 `finalized_by_run_terminal: true`。

### 6.3 UI 状态枚举

内部工具状态建议：

```ts
type ToolLifecycleStatus =
  | 'requested'
  | 'waiting_approval'
  | 'permission_answered'
  | 'running'
  | 'success'
  | 'error'
  | 'canceled'
  | 'timed_out';
```

---

## 7. 迁移顺序

1. 后端新增 v2 serializer，route 仍默认发 v1。
2. `/runs` 支持 `?schema=v2` 或 request header `Accept: application/vnd.metis.agent-event.v2+json`。
3. 前端新增 v2 adapter 和 reducer tests。
4. desktop `/runs` 订阅切到 v2。
5. 删除前端 `call-${Date.now()}` 正常路径。
6. 删除按 tool name 合并的正常路径，仅 legacy adapter 可保留测试覆盖。
7. `/chat` 继续发 v1，或通过 legacy adapter 兼容。

---

## 8. 验收

- [ ] 所有 `/runs` 事件都有 v2 必填 envelope 字段。
- [ ] 并发两个同名工具不会合并错。
- [ ] 缺 `call_id` 的工具 terminal event 不会污染已有工具卡。
- [ ] 权限工具卡按 `call_id` 从 requested 走到 terminal。
- [ ] `done/error/cancel` 只作为异常兜底关闭 open tools。
- [ ] 前端 reducer tests 覆盖同名并发、缺 call_id、断线 replay。
- [ ] 后端 serializer tests 覆盖 envelope、seq、event_id、tool lifecycle。

---

## 9. 不做

- 不在本阶段删除 v1。
- 不在本阶段改 provider 调用策略。
- 不在本阶段把所有历史 transcript 重写成 v2。
