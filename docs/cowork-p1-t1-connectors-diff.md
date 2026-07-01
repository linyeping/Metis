# P1-T1 施工清单（diff 级）— 扩充连接器目录

> 配套主文档：[cowork-gap-plan.md](./cowork-gap-plan.md) §3.3 T1。
> 角色：Opus 4.8 规划，Sonnet 4.6 施工。本清单可机械执行。

## ⚠️ 施工前必读（诚实警告）

1. **MCP server 的包名/二进制会随时间漂移。** 下面给的 `command`/`args` 是规划时的合理取值，**不保证 2026 年现存**。**每加一个连接器，先在终端实测它能启动（`npx <pkg> --help` 或拉起后 list_tools 成功），再写进 registry。启动不了的，换等价 server 或本轮跳过并在 PR 里注明。**
2. **安全红线（不可违反）：token/密码绝不进 `args`，只经 `token_env` → env 注入。** Postgres 连接串含密码，见下方 P6 的特殊处理。
3. T1 只动**两个文件**：`registry.py`（加数据）+ `test_connectors_mcp_auth.py`（加断言）。不碰 mcp_client.py（那是 T2）。

---

## 文件 1：`backend/runtime/connectors/registry.py`

在 `_CONNECTORS` 字典里，**`"gmail": ...` 条目之后、闭合 `}` 之前**，按下列顺序追加 6 条。结构严格沿用现有 `ConnectorDefinition`（字段：`service_id / display_name / scopes / token_env / mcp / notes`）。

### P1 — google_drive（复用 Google OAuth）
```python
    "google_drive": ConnectorDefinition(
        service_id="google_drive",
        display_name="Google Drive",
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        token_env="GOOGLE_OAUTH_ACCESS_TOKEN",
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-gdrive"],
            "example": {
                "mcpServers": {
                    "google_drive": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-gdrive"],
                        "token_env": "GOOGLE_OAUTH_ACCESS_TOKEN",
                    }
                }
            },
        },
        notes=[
            "Shares Google OAuth with Gmail/Calendar; request combined scopes in the OAuth flow.",
            "Read-only scope by default; widen only when a write task requires it.",
            "VERIFY the MCP package exists and starts before committing.",
        ],
    ),
```

### P2 — google_calendar（复用 Google OAuth）
```python
    "google_calendar": ConnectorDefinition(
        service_id="google_calendar",
        display_name="Google Calendar",
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        token_env="GOOGLE_OAUTH_ACCESS_TOKEN",
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-google-calendar"],
            "example": {
                "mcpServers": {
                    "google_calendar": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-google-calendar"],
                        "token_env": "GOOGLE_OAUTH_ACCESS_TOKEN",
                    }
                }
            },
        },
        notes=[
            "Shares Google OAuth with Gmail/Drive.",
            "Writing events is a side-effecting action; gate behind permission approval.",
            "VERIFY the MCP package exists and starts before committing.",
        ],
    ),
```

### P3 — slack
```python
    "slack": ConnectorDefinition(
        service_id="slack",
        display_name="Slack",
        scopes=["channels:read", "channels:history", "chat:write"],
        token_env="SLACK_BOT_TOKEN",
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-slack"],
            "example": {
                "mcpServers": {
                    "slack": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-slack"],
                        "token_env": "SLACK_BOT_TOKEN",
                    }
                }
            },
        },
        notes=[
            "Bot token (xoxb-) via env; some servers also need SLACK_TEAM_ID — pass via env too, never args.",
            "chat:write means posting messages — a send action; require explicit permission.",
            "VERIFY the MCP package exists and starts before committing.",
        ],
    ),
```

### P4 — notion
```python
    "notion": ConnectorDefinition(
        service_id="notion",
        display_name="Notion",
        scopes=["read", "update", "insert"],
        token_env="NOTION_API_KEY",
        mcp={
            "command": "npx",
            "args": ["-y", "@notionhq/notion-mcp-server"],
            "example": {
                "mcpServers": {
                    "notion": {
                        "command": "npx",
                        "args": ["-y", "@notionhq/notion-mcp-server"],
                        "token_env": "NOTION_API_KEY",
                    }
                }
            },
        },
        notes=[
            "Integration token from a Notion internal integration; pages must be shared with it.",
            "Some builds read the token from a header env (e.g. OPENAPI_MCP_HEADERS) — confirm the var name and map token_env accordingly.",
            "VERIFY the MCP package exists and starts before committing.",
        ],
    ),
```

### P5 — filesystem（无 token，验证"无凭据连接器"路径）
```python
    "filesystem": ConnectorDefinition(
        service_id="filesystem",
        display_name="Local Filesystem",
        scopes=[],
        token_env="",
        mcp={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "<ALLOWED_DIR>"],
            "example": {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "<ALLOWED_DIR>"],
                    }
                }
            },
        },
        notes=[
            "No token. The directory arg is NOT a secret — it scopes access and must stay inside the workspace boundary.",
            "<ALLOWED_DIR> is a placeholder; the manager (T3) substitutes the actual allowed workspace path at connect time.",
            "Keep this scoped to the current workspace; do not expose the whole drive.",
        ],
    ),
```

### P6 — postgres（⚠️ 凭据特殊处理）
> **冲突点：** 参考实现 `@modelcontextprotocol/server-postgres` 把连接串当 **arg** 传——而连接串含密码，违反"token 不进 args"红线。
>
> **决策（按以下顺序）：**
> 1. 若存在**从 env 读连接串**的 server 变体（如读 `DATABASE_URL`），用它，`token_env="DATABASE_URL"`，args 里**不放**连接串。
> 2. 若只有 arg 式 server：**本轮跳过 postgres**，在 PR 注明原因，留到 T2/T3 引入"env→arg 安全包装"后再加。**不要为了凑数把密码塞进 args。**

```python
    # 仅当找到 env 式（DATABASE_URL）的 postgres MCP server 时才加入；否则本轮跳过。
    "postgres": ConnectorDefinition(
        service_id="postgres",
        display_name="PostgreSQL",
        scopes=[],
        token_env="DATABASE_URL",
        mcp={
            "command": "npx",
            "args": ["-y", "<ENV_BASED_POSTGRES_MCP_SERVER>"],
            "example": {
                "mcpServers": {
                    "postgres": {
                        "command": "npx",
                        "args": ["-y", "<ENV_BASED_POSTGRES_MCP_SERVER>"],
                        "token_env": "DATABASE_URL",
                    }
                }
            },
        },
        notes=[
            "Connection string contains the password -> MUST come via DATABASE_URL env, NEVER as an arg.",
            "If only an arg-based server exists, SKIP postgres this round (see P6 decision note).",
        ],
    ),
```

---

## 文件 2：`backend/tests/test_connectors_mcp_auth.py`

在文件末尾追加（保持现有两个测试不动）。覆盖：新条目存在、Google 三件套共用 token_env、filesystem 无 token、所有非空 token_env 全大写常量风格。

```python
def test_connector_catalog_expanded_set() -> None:
    services = {item["service_id"] for item in connector_catalog()}
    expected = {
        "github",
        "gmail",
        "google_drive",
        "google_calendar",
        "slack",
        "notion",
        "filesystem",
    }
    assert expected <= services
    # postgres 可能本轮被跳过（凭据进 args 的红线），不强制。


def test_google_connectors_share_oauth_token_env() -> None:
    for service in ("gmail", "google_drive", "google_calendar"):
        conn = get_connector(service)
        assert conn is not None
        assert conn.token_env == "GOOGLE_OAUTH_ACCESS_TOKEN"


def test_filesystem_connector_has_no_token() -> None:
    fs = get_connector("filesystem")
    assert fs is not None
    assert fs.token_env == ""
    assert fs.scopes == []


def test_no_connector_leaks_secret_in_args() -> None:
    # 红线：凭据绝不进 args。args 里不得出现 token_env 的值占位或明显密钥样式。
    for item in connector_catalog():
        args = item["mcp"].get("args", [])
        joined = " ".join(str(a) for a in args).lower()
        for marker in ("token", "secret", "password", "api_key", "apikey"):
            assert marker not in joined, f"{item['service_id']} args 疑似含凭据: {args}"
```

---

## 验证步骤（真机，逐条留证）

1. **每个 server 能起来**：对 P1–P5（及 P6 若保留）逐个 `npx -y <pkg> --help`（或拉起后能 list_tools）。起不来的从 registry 删除/换包，并在 PR 注明。
2. **单测通过**：`pytest backend/tests/test_connectors_mcp_auth.py -v`。
3. **catalog 自检**：Python 里 `from backend.runtime.connectors import connector_catalog; print({c['service_id'] for c in connector_catalog()})` —— 确认 ≥6 条且含 filesystem。
4. **红线自检**：上面 `test_no_connector_leaks_secret_in_args` 必须绿。

## DoD（Opus 验收逐条核）
- [ ] `_CONNECTORS` 新增 ≥5 条（含 filesystem 无 token），postgres 视红线决定加/跳
- [ ] 每个保留的连接器 **真机实测过 server 能启动**（PR 附证据：命令+输出截断）
- [ ] Google 三件套共用 `GOOGLE_OAUTH_ACCESS_TOKEN`
- [ ] 4 个新测试全绿；原有 2 个测试不回归
- [ ] 红线测试通过：任何连接器 args 不含凭据
- [ ] PR 描述列出：哪些 server 实测通过、哪些被换/跳及原因
