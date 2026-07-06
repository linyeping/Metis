# Metis Core Stabilization 05 - 文件/报告 Artifact Registry

> 编写日期：2026-07-06
> 目标：建立后端持久 artifact registry，让文件变更、diff、报告、下载、preview evidence 和 workspace 文件进入同一个产物面板。

---

## 0. 结论

Metis 已经能生成很多“产物”：研究报告、文件改动、diff preview、preview evidence、docx/pdf/report、下载文件。但这些产物的来源分散，前端 `documentLibrary` 只是 `localStorage` cache，不应继续作为事实来源。

需要新增后端持久 registry：`metis.artifact.v1`。前端 document library 只做缓存和展示，不再负责定义产物事实。

---

## 1. 现状基线

| 产物类型 | 当前位置 | 状态 |
|---|---|---|
| research report | `backend/tools/coding/network_external/web/research_jobs.py` | 会写 markdown report |
| documentLibrary | `desktop/src/lib/documentLibrary.ts` | localStorage cache |
| tool transcript | `backend/web/app.py` + `desktop/src/store/messageOps.ts` | 有 tool record |
| diff preview | `backend/tools/coding/modify_refactor/modify_text/diff_preview.py` + right rail | 有展示，无统一 registry |
| file change audit/revert | `backend/web/workspace_routes.py` | 有 audit |
| preview evidence | `desktop/electron/main.cjs` | 有 `metis.preview_evidence.v1` |
| artifact tools | `backend/tools/artifacts` | 有多种产物工具 |

当前缺口：

- 没有统一 artifact id。
- 产物与 run/session/event 的关联不稳定。
- 前端 artifact 面板无法统一展示 report、diff、文件、evidence。
- Electron 生成的 preview evidence 不会进入 backend registry。

---

## 2. `metis.artifact.v1`

Artifact 记录：

```json
{
  "schema": "metis.artifact.v1",
  "version": 1,
  "artifact_id": "art_xxx",
  "run_id": "run_xxx",
  "session_id": "sess_xxx",
  "turn_id": "turn_xxx",
  "kind": "report",
  "title": "Research report",
  "path": "D:/workspace/.metis/research/reports/report.md",
  "url": "",
  "mime": "text/markdown",
  "created_at": "2026-07-06T09:30:00.000Z",
  "updated_at": "2026-07-06T09:30:00.000Z",
  "source_event_id": "evt_run_xxx_000017",
  "source_tool_call_id": "call_xxx",
  "metadata": {}
}
```

必填：

- `schema`
- `version`
- `artifact_id`
- `kind`
- `title`
- `created_at`

`path` 和 `url` 至少一个非空。敏感路径按 workspace/path safety 规则处理。

---

## 3. Artifact Kind

第一阶段支持：

| kind | 含义 |
|---|---|
| `file_change` | 已应用的文件变更记录 |
| `diff` | diff preview 或变更集合 |
| `report` | research/deep research/verification report |
| `document` | docx/pdf/xlsx/pptx/markdown 等成品文档 |
| `preview_evidence` | browser preview screenshot/health/evidence |
| `download` | 工具下载的文件 |
| `workspace_file` | 用户或 agent 需要展示的 workspace 文件 |

后续可扩：

- `image`
- `dataset`
- `log`
- `patch`
- `terminal_recording`

---

## 4. 存储设计

建议新增：

- `backend/runtime/artifact_registry.py`
- `backend/web/artifact_routes.py`

存储文件：

```text
.metis/artifacts/registry.jsonl
```

为什么用 JSONL：

- 和现有 audit 风格一致。
- append-only，低风险。
- 后续可做 compact/reindex。
- 不需要引入数据库。

写入规则：

- `artifact_id` 后端生成，格式 `art_<uuid>`。
- 同一 `source_event_id + path/url + kind` 幂等 upsert。
- 写入前校验 path 是否在 workspace、`.metis` 允许目录、temp evidence 目录或明确允许的 downloads。
- registry 不保存大内容，只保存引用和 metadata。

---

## 5. API

### 5.1 列表

`GET /artifacts?session_id=&run_id=&kind=&limit=`

返回：

```json
{
  "artifacts": []
}
```

### 5.2 获取单个

`GET /artifacts/:artifact_id`

返回 artifact 记录。

### 5.3 注册

`POST /artifacts`

只允许 loopback。请求体可由 backend 内部、Electron 或测试调用：

```json
{
  "kind": "preview_evidence",
  "title": "Preview evidence",
  "path": "C:/Users/.../preview-evidence/file.json",
  "mime": "application/json",
  "run_id": "run_xxx",
  "session_id": "sess_xxx",
  "source_event_id": "evt_xxx",
  "metadata": {}
}
```

### 5.4 重建索引

`POST /artifacts/reindex`

第一阶段只扫描已知目录：

- `.metis/research/reports`
- `.metis/artifacts`
- Electron preview evidence directory
- workspace file change audit

---

## 6. 后端施工

### 6.1 Registry 模块

核心函数：

```python
def register_artifact(
    *,
    kind: str,
    title: str,
    path: str = "",
    url: str = "",
    mime: str = "",
    run_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    source_event_id: str = "",
    source_tool_call_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]: ...

def list_artifacts(filters: ArtifactFilters) -> list[dict[str, Any]]: ...
def get_artifact(artifact_id: str) -> dict[str, Any] | None: ...
```

### 6.2 接入点

优先接这些低风险位置：

1. `research_jobs.py`：写 markdown report 后注册 `kind=report`。
2. `workspace_routes.py`：file change audit/revert 后注册 `kind=file_change` 或 `kind=diff`。
3. `backend/tools/artifacts`：docx/pdf/report 输出后注册 `kind=document`。
4. `desktop/electron/main.cjs`：`savePreviewEvidence` 成功后调用 backend `/artifacts` 注册 `kind=preview_evidence`。
5. agent event v2：工具终结事件 payload 带 `artifacts: ["art_xxx"]`，同时发 `artifact_created`。

### 6.3 run 上下文传递

工具层需要拿到当前 run/session/turn：

- 第一阶段可在 run service 执行工具前把 context 放进 config/runtime context。
- 若某些工具暂时拿不到 run context，也允许注册 sessionless artifact，但 metadata 必须标 `unscoped: true`。

---

## 7. 前端施工

### 7.1 documentLibrary 降级为 cache

`desktop/src/lib/documentLibrary.ts`：

- 保留 localStorage 作为离线 cache。
- 新增 `syncDocumentLibraryFromArtifacts()`。
- `upsertDocumentLibraryItem` 不再是事实来源，只缓存后端 artifact。

### 7.2 API

`desktop/src/lib/api.ts` 新增：

- `listArtifacts`
- `getArtifact`
- `registerArtifact`，仅 Electron/loopback 内部使用时暴露

### 7.3 UI

Artifacts 面板应该从 `/artifacts` 读取：

- report 打开 report viewer
- document 打开 file preview
- diff 打开 right rail diff
- preview_evidence 打开 evidence viewer
- workspace_file 打开 file preview

已有 disabled “会话文件/Artifacts” 导航项可以在接入后转为真实入口。

---

## 8. 与事件协议的关系

agent event v2 中添加：

```json
{
  "kind": "artifact_created",
  "payload": {
    "artifact": {
      "schema": "metis.artifact.v1",
      "artifact_id": "art_xxx",
      "kind": "report",
      "title": "Research report"
    }
  }
}
```

工具终结事件只引用 artifact id：

```json
{
  "kind": "tool_succeeded",
  "payload": {
    "call_id": "call_xxx",
    "artifacts": ["art_xxx"]
  }
}
```

这样工具卡和 artifact 面板不会各自维护一套来源。

---

## 9. 迁移顺序

1. 新增 registry 和 `/artifacts` list/get/register API。
2. research report 写入后注册 artifact。
3. 前端 documentLibrary 从 `/artifacts` 同步 report。
4. preview evidence 保存后注册 artifact。
5. document/diff/file_change 接入 registry。
6. agent event v2 发 `artifact_created`，tool terminal event 带 artifact ids。
7. sidebar Artifacts 入口转真实页面。

---

## 10. 验收

- [ ] 研究报告生成后 `/artifacts` 能查到 `kind=report`。
- [ ] preview evidence 保存后 `/artifacts` 能查到 `kind=preview_evidence`。
- [ ] documentLibrary 刷新后仍能显示已生成报告，不依赖旧 localStorage 写入。
- [ ] 工具卡中的 artifact id 能打开对应文件/报告/证据。
- [ ] registry 重启后仍可读。
- [ ] path safety 拦截 workspace 外未授权路径。
- [ ] reindex 能恢复已有 report/evidence 基本记录。

---

## 11. 不做

- 不把 artifact 内容存进 registry。
- 不引入数据库。
- 不在第一阶段做云同步。
- 不强制迁移所有历史 localStorage item。
