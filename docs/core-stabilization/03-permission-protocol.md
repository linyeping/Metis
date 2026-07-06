# Metis Core Stabilization 03 - 权限协议

> 编写日期：2026-07-06
> 目标：把权限请求做成显式协议和后端权威状态机，前端只负责展示选择并提交 answer。

---

## 0. 结论

Metis 的权限“能力”已经比较成熟：有 mode、rules、path safety、audit、临时 writable root、full access grant。问题不在权限判断本身，而在生命周期没有成为显式协议。

本施工不重写权限策略。它只把现有能力收编成 `metis.permission_request.v1`，并让后端成为状态和超时的权威。

---

## 1. 现状基线

| 能力 | 当前文件 | 状态 |
|---|---|---|
| 权限策略 | `backend/runtime/permission_control.py` | 已有 |
| 权限解释和风险 | `backend/runtime/agent_services.py` | 已有 |
| pending lock/result/context | `backend/web/app.py` | 已有 |
| 提交权限答复 | `POST /permission` in `backend/web/app.py` | 已有 |
| 权限 rules/audit | `GET/POST /permissions` in `backend/web/app.py` | 已有 |
| 前端弹窗 | `desktop/src/store/sseParser.ts` | 已有 |
| 前端 API | `desktop/src/lib/api.ts` `answerToolPermission` | 已有 |

当前缺口：

- `permission_request` 只是 agent event 的一种，没有独立 schema。
- `displayed/answered/applied/expired/audited` 状态没有显式事件。
- 前端点击后会本地把工具卡改成 running/waiting，后端确认不是唯一状态源。
- 超时和过期语义不清晰。

---

## 2. `metis.permission_request.v1`

权限请求对象：

```json
{
  "schema": "metis.permission_request.v1",
  "version": 1,
  "request_id": "perm_xxx",
  "call_id": "call_xxx",
  "run_id": "run_xxx",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "tool_name": "write_file",
  "status": "requested",
  "created_at": "2026-07-06T09:30:00.000Z",
  "expires_at": "2026-07-06T09:35:00.000Z",
  "arguments_preview": {},
  "decision": {
    "source": "mode|rule|registry|path_safety",
    "reason": "Ask mode requires approval",
    "risk_level": "medium"
  },
  "path_safety": {},
  "choices": [],
  "audit_id": ""
}
```

字段规则：

- `request_id` 后端生成，稳定。
- `call_id` 关联工具生命周期。
- `status` 后端权威。
- `arguments_preview` 是脱敏后的参数，不要求和真实工具参数完全一致。
- `choices` 由后端给出，前端不要自己推导可选项。

---

## 3. 生命周期

标准状态机：

```text
requested
  -> displayed
  -> answered
  -> applied | rejected | expired
  -> audited
  -> tool_resumed | tool_denied
```

### 3.1 状态含义

| 状态 | 权威方 | 含义 |
|---|---|---|
| `requested` | backend | agent loop 请求权限，后端创建 pending request |
| `displayed` | backend | 前端确认已展示给用户 |
| `answered` | backend | 前端提交 answer，后端收到 |
| `applied` | backend | allow/grant/rule 已应用，工具可继续 |
| `rejected` | backend | deny 已应用，工具不可继续 |
| `expired` | backend | 超时，默认拒绝 |
| `audited` | backend | 审计已落盘 |
| `tool_resumed` | backend | agent loop 已收到 allow 并继续 |
| `tool_denied` | backend | agent loop 已收到 deny 并返回拒绝结果 |

### 3.2 agent event 对应

权限协议通过 agent event v2 对外广播：

- `permission_required`
- `permission_answered`
- `permission_applied`
- `permission_rejected`
- `permission_expired`
- `permission_audited`

工具生命周期仍走：

- `tool_running`
- `tool_failed` with `PERMISSION_DENIED`

---

## 4. API

### 4.1 获取权限请求

`GET /permissions/requests/:request_id`

返回当前 permission request。用于 UI 恢复、重复弹窗去重、调试。

### 4.2 标记已展示

`POST /permissions/requests/:request_id/displayed`

请求体：

```json
{
  "surface": "desktop",
  "displayed_at": "2026-07-06T09:30:05.000Z"
}
```

后端把状态从 `requested` 推进到 `displayed`。如果已经是更后状态，幂等返回当前状态。

### 4.3 提交答复

新 API：

`POST /permissions/requests/:request_id/answer`

请求体：

```json
{
  "approved": true,
  "choice": "once|always_allow|always_deny|temporary_root|writable_root|selected_root|full_access",
  "remember": "",
  "grant": "temporary_root",
  "root_path": "D:/workspace/out"
}
```

第一阶段兼容：

- 保留现有 `POST /permission`
- `answerToolPermission` 先切到新 API
- 旧 API 内部调用新 permission service

---

## 5. 后端施工

### 5.1 新增 PermissionRequestStore

建议文件：

- `backend/web/permission_requests.py`

职责：

```python
class PermissionRequestStore:
    def create(self, context: PermissionContext) -> PermissionRequest: ...
    def mark_displayed(self, request_id: str) -> PermissionRequest: ...
    def answer(self, request_id: str, answer: PermissionAnswer) -> PermissionRequest: ...
    def expire_due(self, now: float) -> list[PermissionRequest]: ...
    def get(self, request_id: str) -> PermissionRequest | None: ...
```

第一阶段可继续存内存；audit 继续落 `.metis/audit/tool-permissions.jsonl`。

### 5.2 收编现有全局 dict

当前 `app.py` 里的这些结构：

- `_permission_locks`
- `_permission_results`
- `_permission_contexts`
- `_permission_ephemeral_writable_roots`

不要求一次删掉，但新增 store 后应由 store 管理 request 状态。agent loop 等待仍可暂时用 lock/event。

### 5.3 choices 后端生成

把 `sseParser.ts` 里根据 path safety 推导 choices 的逻辑搬到后端：

- 普通工具：`once / always_allow / always_deny`
- 可授权目录：`temporary_root / writable_root / pick_root`
- 可 full access：`full_access`

前端只渲染后端给出的 choices。

### 5.4 超时

创建 request 时设置 `expires_at`。超时后：

- 状态变 `expired`
- 审计 action = deny，source = timeout
- agent loop 收到 deny
- 发送 `permission_expired` 和 `tool_failed`

---

## 6. 前端施工

### 6.1 API

`desktop/src/lib/api.ts`：

- 新增 `getPermissionRequest`
- 新增 `markPermissionDisplayed`
- 新增 `answerPermissionRequest`
- `answerToolPermission` 作为旧名委托新 API 一轮

### 6.2 UI

`desktop/src/store/sseParser.ts`：

- 收到 `permission_required` 后，读取 `payload.permission.choices`。
- 弹窗展示前调用 displayed。
- 用户选择后调用 answer。
- 本地只显示 `answer_submitting` 或 `pending_backend_confirmation`。
- 工具卡最终状态以 `permission_answered/tool_running/tool_failed` 事件为准。

### 6.3 去重

用 `request_id` 去重，而不是 `call_id` 或 tool name。已有 `pendingPermissionDialogs` 可保留，但 key 必须是 `request_id`。

---

## 7. 迁移顺序

1. 后端新增 permission schema 和 store，旧 `/permission` 不改行为。
2. agent event v2 的 `permission_required` 携带完整 permission object。
3. 前端弹窗改为使用后端 choices，但仍提交旧 `/permission`。
4. 新增 `/permissions/requests/:id/displayed|answer`。
5. 前端提交切到新 API。
6. 旧 `/permission` 变成 compatibility wrapper。
7. 加超时逻辑和测试。

---

## 8. 验收

- [ ] 权限请求有独立 `metis.permission_request.v1` 对象。
- [ ] 前端 choices 完全来自后端。
- [ ] 前端 displayed/answer 都有后端记录。
- [ ] 超时请求自动拒绝，并有 audit。
- [ ] 用户 deny 后工具卡最终为 `tool_failed` with `PERMISSION_DENIED`。
- [ ] 用户 allow 后必须先看到 `permission_answered/applied`，再看到 `tool_running`。
- [ ] `/permissions` 面板仍能看到 rules、writable roots、audit。

---

## 9. 不做

- 不改变现有权限 mode 语义。
- 不删除现有 rules/audit 文件格式。
- 不把所有权限状态第一阶段持久化到数据库。
