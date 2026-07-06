# Metis Core Stabilization 01 - Runs 主路径统一

> 编写日期：2026-07-06
> 目标：让 `/runs` 成为 desktop 的唯一主执行路径；`/chat` 只保留兼容和测试用途。

---

## 0. 结论

Metis 现在已经具备 `/runs` 的核心雏形：创建、订阅、恢复、取消都已存在。需要做的不是重建 runtime，而是把 desktop 的正常路径完全收敛到 `/runs`，把 `/chat` 降级为 legacy shim。

Chat、Cowork、Code 不应该是三套执行 API。它们都应该创建同一种 run，只通过 `surface_mode`、session mode、权限默认值、工具可见性和 UI 布局区分。

---

## 1. 现状基线

| 能力 | 当前文件 | 状态 |
|---|---|---|
| `POST /runs` 创建 run | `backend/web/app.py` | 已有 |
| `GET /runs/:id/events?after=seq` 订阅和恢复 | `backend/web/app.py` | 已有 |
| `POST/DELETE /runs/:id/cancel` 停止 | `backend/web/app.py` | 已有 |
| session active run 查询 | `backend/web/app.py` | 已有 |
| `/chat` 流式执行 | `backend/web/app.py` | 仍是并行主路径 |
| desktop 先走 `/runs`，失败 fallback 到 `/chat` | `desktop/src/store/chatStore.ts` | 需要删除 fallback |
| API 命名仍偏 chat | `desktop/src/lib/api.ts` | 需要改成 run 语义 |

当前最大风险不是 `/runs` 不可用，而是主路径分裂：同一个用户动作可能走 `/runs`，也可能走 `/chat`，导致事件协议、取消、恢复、权限和工具卡状态无法稳定。

---

## 2. 稳定协议

### 2.1 创建

`POST /runs`

请求体：

```json
{
  "message": "用户输入",
  "session_id": "sess_xxx",
  "assistant_id": "assistant-run-xxx",
  "surface_mode": "chat|cowork|code",
  "deep_research": false
}
```

返回：

```json
{
  "ok": true,
  "run_id": "run_xxx",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "assistant_id": "assistant-run-xxx",
  "surface_mode": "code",
  "status": "running",
  "created_at": 1783310000.0,
  "updated_at": 1783310000.0,
  "last_seq": 0
}
```

规则：

- 后端生成 `run_id` 和 `turn_id`。第一阶段可让 `turn_id == run_id`，但字段必须存在。
- `surface_mode` 只表达 UI/产品表面，不改变 run 协议。
- 同一 session 同时只能有一个 active run。已有 active run 时返回 `409` 和 active run payload。
- desktop 不再因为 `/runs` 不可用自动 fallback `/chat`。如果 `/runs` 不可用，直接显示后端版本/协议错误。

### 2.2 订阅和恢复

`GET /runs/:run_id/events?after=<seq>`

规则：

- `seq` 是 run 内严格单调递增整数，从 1 开始。
- `after=0` 返回该 run 当前缓存里的全部事件。
- 网络断线后，desktop 用最后处理过的 `seq` 续订。
- terminal event 后仍允许短时间 replay，便于 UI 恢复最终状态。

### 2.3 取消

`POST /runs/:run_id/cancel`

规则：

- 后端是取消状态权威。
- 返回后 run 进入 `canceling`，最终由事件流发 `run_canceled` 或 `run_failed`。
- 前端不要本地伪造终结状态，只能先显示“正在停止”，等待事件收口。

### 2.4 `/chat` 兼容边界

`/chat`、`/chat/sync`、`/chat/edit`、`/chat/regenerate` 保留，但标记为兼容 API：

- desktop 正常发送不调用 `/chat`。
- 测试或外部脚本仍可调用 `/chat`。
- `/chat` 内部尽量复用 `/runs` 的 serializer 和 agent stream，不再拥有独立事件形状。
- 后续可以加响应头：`X-Metis-Deprecated: /chat is legacy; use /runs`。

---

## 3. 后端施工

### 3.1 抽出 RunService

建议新增：

- `backend/web/run_service.py`
- `backend/web/run_routes.py`，或继续留在 `app.py` 但内部调用 service

核心职责：

```python
class RunService:
    def create_run(self, request: RunCreateRequest) -> RunRecord: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def list_runs(self, session_id: str = "") -> list[RunRecord]: ...
    def append_event(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]: ...
    def stream_events(self, run_id: str, after_seq: int) -> Response: ...
    def cancel_run(self, run_id: str) -> RunRecord: ...
```

第一阶段可以继续用现有 `_runs` in-memory store。不要在同一批里强行做持久化 run store，否则风险过大。

### 3.2 统一 send 逻辑

把 `/runs` 和 `/chat` 当前重复的会话准备逻辑收敛到一个内部函数：

```python
def prepare_turn_request(data: Mapping[str, Any]) -> PreparedTurn:
    ...
```

它负责：

- `_prepare_chat_session`
- workspace root
- checkpoint
- user message append
- smart title
- compact/model context
- `_config_for_session_mode`

这样 `/runs` 是主入口，`/chat` 只是调用同一套准备逻辑并用 legacy SSE 包装输出。

### 3.3 surface_mode 落库

run record 增加：

- `surface_mode`
- `turn_id`
- `last_seq`
- `schema_version`

session mode 仍属于 session；`surface_mode` 是这次 run 的 UI 表面。默认值：

- request body 显式值优先
- session mode 次之
- fallback `chat`

### 3.4 active run 行为

保留当前“单 session 单 active run”约束。后续如果要支持后台多 run，应单独设计 queue，不在本施工文档范围内。

---

## 4. 前端施工

### 4.1 API 命名收敛

在 `desktop/src/lib/api.ts` 中新增 run 语义函数：

- `createRun`
- `getRun`
- `getActiveRun`
- `cancelRun`
- `streamRunEvents`

保留旧函数名一轮兼容：

- `startChatRun` 调 `createRun`
- `runEventStream` 调 `streamRunEvents`

下一批再删旧名，避免一次改动触碰太多组件。

### 4.2 删除 desktop fallback

`desktop/src/store/chatStore.ts` 中删除：

```ts
if (isRunApiUnavailable(runError)) {
  await chatStream(...)
}
```

改为：

- `/runs` 创建失败：进入失败状态，显示明确错误。
- `409 active run`：尝试恢复 active run 或提示当前 session 已有运行。
- 网络断开：用 `streamRunEvents(runId, afterSeq)` 恢复。

### 4.3 surface_mode 传参

创建 run 时带上当前 `appMode`：

```ts
createRun({
  message,
  session_id: sessionId,
  assistant_id: assistantId,
  surface_mode: useUiStore.getState().appMode,
  deep_research: deepResearch,
});
```

---

## 5. 迁移顺序

1. 给 `/runs` payload 补齐 `turn_id`、`surface_mode`、`last_seq`，不改前端行为。
2. 加后端 tests，覆盖 create、stream after、cancel、active run conflict。
3. 前端 API 新增 run 语义函数，旧函数委托新函数。
4. 删除 `chatStore.ts` 的 `/chat` fallback。
5. `/chat` 保留，但加 deprecation header，并补测试确保 legacy 仍可用。
6. 文档和 smoke 更新：desktop 主路径只允许 `/runs`。

---

## 6. 验收

- [ ] desktop 发送 Chat/Cowork/Code 消息时只调用 `POST /runs`。
- [ ] 断网或刷新后用 `GET /runs/:id/events?after=seq` 恢复，不重复工具卡。
- [ ] 点击停止调用 `POST /runs/:id/cancel`，最终事件收口为 canceled/failed/completed。
- [ ] `/chat` 仍可被测试脚本调用，但 desktop 不再 fallback。
- [ ] 后端 route tests 覆盖 active run conflict、cancel、after replay。
- [ ] 前端 store tests 覆盖 `/runs` 创建失败不会静默 fallback。

---

## 7. 不做

- 不在本阶段实现 run 持久化重启恢复。
- 不在本阶段实现多 active run 队列。
- 不在本阶段重写 session/history 存储。
