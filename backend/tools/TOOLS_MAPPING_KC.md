# C 17 工具 ↔ Miro Registry 映射（可追溯）

> **真源**：`Tools/registry.py` 中 `TOOL_NAME_ALIASES`、`AVAILABLE_TOOLS` 与 `normalize_tool_kwargs`。  
> **详细表**：`others/说明/MIRO_EVOLUTION_DOCKING_SPEC.md` 第五章。  
> **工具数量**：52 个（含 `run_parallel_tasks`、`run_task_graph` / P6 DAG）

| C 工具 | Registry `function.name`（主名） | 备注 |
|--------|----------------------------------|------|
| Shell | `execute_bash_command`（别名 `Shell`） | `working_directory`→`cwd`，`block_until_ms`→`timeout` |
| Glob | `glob_search`（`Glob`） | `glob_pattern`→`pattern`，`target_directory`→`root` |
| Grep | `grep_search`（`Grep`） | `glob`→`glob_pattern`，`head_limit` 参与 `max_results` |
| Read | `read_file`（`Read`） | `path`→`file_path`，`offset`/`limit`→行范围 |
| Delete | `delete_file`（`Delete`） | `path` |
| StrReplace | `robust_replace_in_file`（`StrReplace`） | `old_string`/`new_string`→`search_text`/`replace_text`，`replace_all` |
| Write | `write_file`（`Write`） | `path`/`contents`→`file_path`/`content` |
| EditNotebook | `edit_notebook`（`EditNotebook`） | `target_notebook`→`path` |
| TodoWrite | `todo_write`（`TodoWrite`） | |
| ReadLints | `read_lints`（`ReadLints`） | `paths` 数组→逗号分隔字符串 |
| SemanticSearch | `semantic_search`（`SemanticSearch`） | `target_directories`/`num_results` |
| WebSearch | `web_search`（`WebSearch`） | `search_term`→`query` |
| WebFetch | `web_fetch`（`WebFetch`） | |
| GenerateImage | `generate_image`（`GenerateImage`） | `description`→`prompt` |
| AskQuestion | `ask_question`（`AskQuestion`） | `questions` JSON 字符串→对象 |
| Task | `task_dispatch`（`Task`） | 子进程协议见 `others/说明/TASK_SUBPROCESS_PROTOCOL.md` |
| （Task 并行）| `run_parallel_tasks` | 并行执行多独立任务 |
| （Task DAG）| `run_task_graph` | P6：拓扑序 + 层内并行 + 图级 resume |
| SwitchMode | `switch_mode`（`SwitchMode`） | |
| （增强）ApplyPatch | `apply_patch`（`ApplyPatch`） | C 原文未单列，Miro 保留 |
| （Miro）打开文件上下文 | `write_open_files_context` | 无 C 同名；写 `.miro_open_files.json` 供 system 注入 |

其余 registry 工具与模块主名见规格书第四章全表；本表覆盖 C 习惯入口与常用别名。

**兼容入口**：旧版 `Tools.legacy_tools_monolith` 已迁移到 `backend.tools.registry`；**新代码请** `from backend.tools.registry import ...`。排查旧 shim 使用：设 **`MIRO_AUDIT_LEGACY_IMPORT=1`**。
