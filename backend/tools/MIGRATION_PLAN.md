# Miro 工具迁移计划

> **当前真源**：`Tools/registry.py`（单一真源；`execute_tool` / `TOOLS_SCHEMA` / 参数别名均由此导出）  
> **更新时间**：2026-03-28  
> **状态**：主路径已完成；`legacy_tools_monolith.py` 若仍存在则仅作过渡 shim，新代码禁止依赖其 TOOLS_SCHEMA。

---

## 一、迁移目标

将所有工具注册从 `legacy_tools_monolith.py` 迁移到模块化的 `Tools/registry.py`，实现：

1. **单一真源**：所有工具定义、Schema、执行逻辑统一在 registry
2. **参数别名**：C 风格参数名（path/contents）与 Miro 实现（file_path/content）自动映射
3. **工具名别名**：支持 C 短名（Read/Write）与 Miro 长名（read_file/write_file）
4. **清晰边界**：legacy 仅保留 logger 等过渡功能，不再维护重复工具定义

---

## 二、迁移状态

### 2.1 已完成（✅）

| 项目 | 状态 | 说明 |
|------|------|------|
| **Registry 创建** | ✅ 完成 | `Tools/registry.py` 已创建，包含 49 个工具 |
| **Schema 定义** | ✅ 完成 | `Tools/schema_definitions.py` 包含所有工具的 OpenAI Schema |
| **参数别名** | ✅ 完成 | `normalize_tool_kwargs()` 实现 C↔Miro 参数映射 |
| **工具名别名** | ✅ 完成 | `TOOL_NAME_ALIASES` 支持 C 短名调用 |
| **execute_tool** | ✅ 完成 | 统一执行入口，包含 pre/post hook、异常处理 |
| **app.py 对接** | ✅ 完成 | `app.py` 已从 registry 导入 TOOLS_SCHEMA 和 execute_tool |
| **49 模块导入** | ✅ 完成 | 所有业务模块已导入到 AVAILABLE_TOOLS |

### 2.2 Legacy 当前状态

| 文件 | 状态 | 说明 |
|------|------|------|
| `legacy_tools_monolith.py` | ✅ **shim** | **仅转发** `Tools.registry`，无重复定义；**默认无警告**；`MIRO_AUDIT_LEGACY_IMPORT=1` 时首次加载打 INFO |
| `foundation/core_mechanisms/log_config.py` | ✅ 标准化 | 标准 `logging` 配置 |
| `foundation/core_mechanisms/colored_logger.py` | ✅ 唯一 | **全仓仅此一处** `ColoredLogger`；`app.py` 使用 `colored_log` |

---

## 三、Registry 架构

### 3.1 核心组件

```python
# Tools/registry.py

# 1. Schema 定义（OpenAI tools 格式）
TOOLS_SCHEMA: List[Dict[str, Any]] = build_tools_schema()

# 2. 工具名别名（C 短名 → Miro 长名）
TOOL_NAME_ALIASES: Dict[str, str] = {
    "Read": "read_file",
    "Shell": "execute_bash_command",
    "Grep": "grep_search",
    # ... 17 个 C 工具别名
}

# 3. 可用工具字典（函数名 → 可调用对象）
AVAILABLE_TOOLS: Dict[str, Callable[..., str]] = {
    "read_file": read_file,
    "write_file": write_file,
    # ... 49 个工具
}

# 4. 写类工具集合（用于 post-edit lint）
WRITE_LIKE_TOOLS: Set[str] = {
    "write_file",
    "robust_replace_in_file",
    # ... 9 个写类工具
}

# 5. 统一执行入口
def execute_tool(tool_name: str, **kwargs: Any) -> str:
    """
    - 工具名别名解析
    - 参数别名映射
    - pre_tool_hook
    - 执行工具
    - post_edit_lint（可选）
    - post_tool_hook
    - 异常捕获为字符串
    """
```

### 3.2 参数别名映射

`normalize_tool_kwargs()` 实现的映射规则：

| 工具 | C 参数名 | Miro 参数名 | 映射逻辑 |
|------|----------|-------------|----------|
| read_file | path | file_path | `path` → `file_path` |
| read_file | offset, limit | start_line, end_line | `offset` → `start_line`, `end_line = offset + limit - 1` |
| write_file | path, contents | file_path, content | 双向映射 |
| robust_replace | old_string, new_string | search_text, replace_text | 双向映射 |
| glob_search | glob_pattern, target_directory | pattern, root | 双向映射 |
| grep_search | glob, head_limit | glob_pattern, max_results | 双向映射 |
| web_search | search_term | query | `search_term` → `query` |
| generate_image | description | prompt | `description` → `prompt` |
| execute_bash_command | working_directory, block_until_ms | cwd, timeout | `block_until_ms/1000` → `timeout` |
| edit_notebook | target_notebook | path | `target_notebook` → `path` |

---

## 四、验收标准

### 4.1 功能验收

- [x] **工具数量**：AVAILABLE_TOOLS 包含 49 个工具
- [x] **Schema 完整性**：TOOLS_SCHEMA 包含所有工具的 OpenAI 格式定义
- [x] **别名支持**：C 短名（Read/Write/Grep 等）可正常调用
- [x] **参数映射**：C 参数名（path/contents）自动转换为 Miro 参数名
- [x] **异常处理**：execute_tool 捕获所有异常并返回友好字符串
- [x] **Hook 集成**：pre/post hook 正常工作
- [x] **Post-edit lint**：写类工具执行后自动调用 read_lints（可配置）

### 4.2 代码质量

- [x] **类型注解**：所有公开函数有完整类型注解
- [x] **文档字符串**：关键函数有清晰的 docstring
- [x] **日志记录**：关键操作有 logger 记录
- [x] **错误信息**：异常信息清晰，便于调试

---

## 五、待迁移项（按优先级）

### 5.1 高优先级

- [x] **统一 Logger**：`ColoredLogger` 仅在 `colored_logger.py`；`app.py` 无重复类定义（验收：`verify_block6_legacy.py`）

### 5.2 中优先级

- [x] **legacy_tools_monolith**：已改为 **registry 静默 shim**（兼容旧 import；废弃提示见模块文档，不默认打 DeprecationWarning）
  - 业务代码：`grep`/AST 扫描应 **零** `from Tools.legacy_tools_monolith import ...`，新引用使用 `backend.tools.registry`
  - 验收：`py others/scripts/verify_block6_legacy.py`

### 5.3 低优先级

- [ ] **环境变量开关**：`USE_LEGACY_TOOLS=1` 回退机制（可选）
  - 用于紧急回退到旧实现
  - 仅在生产环境需要

---

## 六、迁移历史

### 2026-03-28：主路径完成

- ✅ 创建 `Tools/registry.py` 和 `Tools/schema_definitions.py`
- ✅ 实现 49 个工具的完整注册
- ✅ 实现 C↔Miro 参数别名映射
- ✅ 实现工具名别名（C 短名支持）
- ✅ `app.py` 改为从 registry 导入
- ✅ 所有测试通过（32 个测试）

### 2026-03-28：块6 legacy 收口

- ✅ `legacy_tools_monolith.py` 重建为 **仅转发 registry** 的静默 shim（可选 `MIRO_AUDIT_LEGACY_IMPORT` 审计）
- ✅ 全仓业务 `.py` 不直接 import legacy；`others/scripts/verify_block6_legacy.py` 持续验收
- ✅ `ColoredLogger` 单一定义于 `colored_logger.py`

### 之前：Legacy 阶段

- 所有工具定义在 `legacy_tools_monolith.py`
- Schema 与实现混在一起
- 无参数别名支持
- 无工具名别名支持

---

## 七、参考文档

- **技术规格**：`others/说明/MIRO_EVOLUTION_DOCKING_SPEC.md`
- **资料整合**：`others/说明/MIRO_KC_INTEGRATED_DOCKING.md`
- **完成报告**：`FINAL_COMPLETION_REPORT.md`
- **待办清单**：`TODO_ROADMAP.md`

---

## 八、联系与支持

如有问题，请参考：

1. **Registry 源码**：`Tools/registry.py`（约 300 行，包含完整注释）
2. **Schema 定义**：`Tools/schema_definitions.py`（约 600 行，包含所有工具描述）
3. **测试用例**：`tests/` 目录下的测试文件

---

*最后更新：2026-03-28*  
*状态：主路径已完成；legacy 为 **registry 转发 shim**（块6 完成）*
