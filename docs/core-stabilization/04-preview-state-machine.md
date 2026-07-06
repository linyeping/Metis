# Metis Core Stabilization 04 - Preview 状态机

> 编写日期：2026-07-06
> 目标：让 Electron 成为 native preview 的唯一权威；React 只提交 layout intent，backend tools 只提交 browser command。

---

## 0. 结论

Metis 的 Preview 能力很强：Electron 用 `WebContentsView` 承载真实浏览器，React right rail 控制布局，Python backend 通过 long-poll bridge 发 browser command。问题是状态散落在三层，每层都在推断 visible、bounds、loading、occluded。

本施工把 Preview 收敛为一个 Electron 权威状态机。React 不再判断“真实 preview 状态”，只表达布局意图；backend 不碰可见性，只发浏览器命令并接收 command result。

---

## 1. 现状基线

| 能力 | 当前文件 | 状态 |
|---|---|---|
| WebContentsView preview | `desktop/electron/main.cjs` | 已有 |
| bounds intent helper | `desktop/electron/preview-state.cjs` | 已有 |
| preview IPC | `desktop/electron/preload.cjs` | 已有 |
| right rail bounds sync | `desktop/src/components/rightrail/RightRail.tsx` | 已有 |
| backend command bridge | `backend/web/preview_bridge.py` | 已有 |
| Electron long-poll backend | `desktop/electron/main.cjs` | 已有 |
| preview evidence 保存 | `desktop/electron/main.cjs` | 已有 |

当前缺口：

- `visible/hidden/loading/ready/error/occluded` 没有统一状态枚举。
- React 会通过 `previewSetBounds({ visible: false })` 等方式影响状态，但不清楚是否为权威。
- backend bridge 返回 command result，但不会进入统一 preview activity/state。
- preview evidence 已有 schema，但没有挂 artifact registry。

---

## 2. 状态机

Preview state：

```text
hidden
  -> mounting
  -> loading
  -> ready
  -> occluded
  -> error
```

允许转移：

| From | To | 触发 |
|---|---|---|
| `hidden` | `mounting` | React 提交 visible layout intent |
| `mounting` | `loading` | Electron 创建 WebContentsView 并开始 load |
| `loading` | `ready` | did-finish-load 或已命中当前 URL |
| `loading` | `error` | did-fail-load / unsafe URL / view unavailable |
| `ready` | `loading` | load new URL / reload |
| `ready` | `occluded` | Electron 判定被遮挡或 app 失焦策略要求隐藏 |
| `occluded` | `ready` | 恢复 last visible bounds |
| `ready` | `hidden` | React 提交 hidden layout intent |
| `error` | `loading` | retry / load new URL |
| `error` | `hidden` | hide intent |

---

## 3. `metis.preview_state.v1`

Electron 向 renderer 广播：

```json
{
  "schema": "metis.preview_state.v1",
  "version": 1,
  "state": "ready",
  "tab_id": "preview-main",
  "url": "http://127.0.0.1:5173/",
  "title": "Metis App",
  "bounds": { "x": 800, "y": 80, "width": 900, "height": 700 },
  "visible": true,
  "loading": false,
  "occluded": false,
  "error": "",
  "last_command_id": "preview-xxx",
  "activity_seq": 42,
  "updated_at": "2026-07-06T09:30:00.000Z"
}
```

规则：

- `state` 是唯一 UI 状态源。
- `bounds` 是 Electron 实际应用或最后接受的 bounds。
- `activity_seq` 由 Electron 增加，用于 React 判断 activity 是否刷新。
- `last_command_id` 记录最近一次 backend browser command，不等于 run event id。

---

## 4. 三层职责

### 4.1 Electron

Electron 是权威：

- 创建/销毁 `WebContentsView`
- 应用 bounds
- 判断 hidden/occluded/loading/ready/error
- 执行 browser command
- 保存 activity 和 evidence
- 广播 `metis:preview-state`

建议把 `main.cjs` 中 preview 相关逻辑逐步移到：

- `desktop/electron/preview-controller.cjs`
- `desktop/electron/preview-state.cjs` 继续放纯函数

### 4.2 React

React 只做：

- 计算 right rail host DOM bounds
- 调用 `previewSetLayoutIntent`
- 渲染 Electron 广播的 state
- 展示 command result、activity、error

React 不做：

- 不自行推断 preview 已 ready
- 不自行维护 loading 权威状态
- 不根据 tab/url 重建 native preview 事实

### 4.3 Backend

Backend 只做：

- 工具调用 `request_preview_command(kind, payload)`
- 等待 Electron result
- 把 command result 放进 tool result 或 agent event

Backend 不做：

- 不判断 preview 可见性
- 不设置 bounds
- 不缓存 preview UI 状态

---

## 5. IPC/API

### 5.1 Renderer -> Electron

替换或包裹现有 IPC：

```ts
previewSetLayoutIntent({
  visible: true,
  tabId: 'preview-main',
  bounds: { x, y, width, height },
  reason: 'right-rail-web-card'
})
```

现有 `previewSetBounds` 保留一轮，内部转成 layout intent。

### 5.2 Backend -> Electron

保留现有 long-poll：

- `GET /api/preview-browser/next`
- `POST /api/preview-browser/result`

command 增加字段：

```json
{
  "id": "preview-xxx",
  "kind": "load|command|observe|action|capture|activity",
  "payload": {},
  "created_at": 1783310000.0
}
```

Electron result 必须带：

```json
{
  "ok": true,
  "command_id": "preview-xxx",
  "preview_state": {},
  "browser_activity": {}
}
```

---

## 6. 后端施工

`backend/web/preview_bridge.py`：

- command id 继续由后端生成。
- result 校验 `command_id` 或兼容 `id`。
- timeout result 增加 `preview_bridge_connected`。
- 不引入 preview state cache。

工具层：

- browser tool result 中只引用 command result。
- 如果 command 生成 screenshot/evidence，注册 artifact 由 05 文档处理。

---

## 7. Electron 施工

### 7.1 PreviewController

新增 controller 管理：

```js
class PreviewController {
  getState() {}
  setLayoutIntent(intent) {}
  load(payload) {}
  command(command) {}
  observe(payload) {}
  capture() {}
  setOccluded(value) {}
  emitState(patch) {}
}
```

第一阶段可不使用 class，但要把状态集中到一个模块，避免 `main.cjs` 继续膨胀。

### 7.2 状态广播

任何状态变更都调用 `emitPreviewState`，并输出 `metis.preview_state.v1`。

触发点：

- view created
- load start
- load finish
- load fail
- bounds applied
- hidden intent
- occluded change
- command executed
- capture/evidence saved

---

## 8. 前端施工

`desktop/src/components/rightrail/RightRail.tsx`：

- 用 `previewState.state` 渲染 loading/error/ready。
- `previewSetBounds` 改名封装为 `previewSetLayoutIntent`。
- web card hidden 时只发 hidden intent，不再清空 Electron state。
- activity 面板按 `activity_seq` 刷新。

`desktop/src/store/uiStore.ts`：

- 不存权威 preview state，只存 UI intent 和 right rail preference。
- 如需跨组件显示 preview state，新增只读 `previewRuntimeState`，由 Electron event 更新。

---

## 9. 迁移顺序

1. Electron state payload 加 `schema/state/activity_seq`，兼容旧字段。
2. React 读取新 state，但仍保留旧字段 fallback。
3. `previewSetBounds` 内部改成 layout intent。
4. backend command result 增加 `preview_state`。
5. RightRail loading/error UI 切到 state machine。
6. 把 preview evidence 注册到 artifact registry。

---

## 10. 验收

- [ ] Electron 广播的每个 preview state 都有 `schema=metis.preview_state.v1`。
- [ ] React 打开/关闭 right rail 不会让 backend preview bridge 误判。
- [ ] occluded 后恢复使用 last visible bounds。
- [ ] load URL 成功后 state 为 `ready`，失败后 state 为 `error`。
- [ ] backend browser command result 带 `preview_state`。
- [ ] 视觉验收保存的 evidence 能出现在 artifact 面板。
- [ ] Electron smoke 覆盖 desktop/mobile bounds、load、reload、capture。

---

## 11. 不做

- 不在本阶段重写 browser tool。
- 不在本阶段支持多 native preview view。
- 不让 backend 直接控制 native bounds。
