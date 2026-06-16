# -*- coding: utf-8 -*-
"""
Miro 全量 OpenAI tools schema（function.name = 实现函数名，便于对接）。
描述融合 C 原文纪律（Shell/Grep/Read/…）与 K 习惯（剪枝、地图、AST、终端快照）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

Prop = Dict[str, Any]
Entry = Tuple[str, str, Dict[str, Prop], List[str]]


def _obj(props: Dict[str, Prop], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
    }


def _string(desc: str) -> Prop:
    return {"type": "string", "description": desc}


def _integer(desc: str) -> Prop:
    return {"type": "integer", "description": desc}


def _boolean(desc: str) -> Prop:
    return {"type": "boolean", "description": desc}


def _array_of_string(desc: str) -> Prop:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


def _array_of_object(desc: str) -> Prop:
    return {"type": "array", "items": {"type": "object"}, "description": desc}


# (name, description, properties, required)
_TOOL_SPECS: List[Entry] = [
    (
        "execute_bash_command",
        """【Shell / 类 C Shell】在 shell 中执行命令，用于 git、包管理器、测试、构建、docker 等真终端操作。
严禁：不要用 Shell 读文件（cat/head/tail）、搜代码（grep/find）、改文件（sed/awk）、按名找文件（find）——请用 read_file、grep_search、glob_search、robust_replace_in_file、write_file。
长驻进程前先用 read_terminal_state 看是否已有同类进程。路径含空格必须加引号。
无依赖的多条命令应并行多次调用本工具；强依赖用 bash 的 && 串联（PowerShell 用 ; 或平台语法）。
K 侧：超时与输出截断防死锁；description 仅用于日志审计（5–10 词）。""",
        {
            "command": _string("要执行的命令（必填）"),
            "timeout": _integer("超时秒数，默认 60（对应 C 的 block_until_ms/1000 近似）"),
            "cwd": _string("工作目录，默认 '.'（C 名 working_directory 会在 registry 映射到此）"),
            "description": _string("5–10 词说明用途（可选，与 C Shell 一致）"),
        },
        ["command"],
    ),
    (
        "start_long_running_process",
        """启动后台常驻进程（dev server 等），登记 PID。与 C 要求一致：启动前应先 read_terminal_state 避免重复拉起。
K：与 manage_long_running 模块统一，便于后续 list/stop。""",
        {
            "command": _string("完整命令行"),
            "name": _string("可选展示名称"),
            "cwd": _string("工作目录，默认 '.'"),
        },
        ["command"],
    ),
    (
        "stop_long_running_process",
        "终止由 start_long_running_process 登记的进程。",
        {"pid": _integer("进程 PID")},
        ["pid"],
    ),
    (
        "list_long_running_processes",
        "列出当前由本工具链登记的后台进程。",
        {},
        [],
    ),
    (
        "register_external_process",
        "登记外部已存在的 PID（仅记录；终止请用系统 kill/taskkill）。",
        {
            "pid": _integer("PID"),
            "name": _string("显示名"),
            "command": _string("命令摘要"),
        },
        ["pid", "name", "command"],
    ),
    (
        "write_file",
        """【Write / 覆盖写】写入或覆盖整个文件。优先编辑已有文件而非随意新建；不要主动写 README/*.md 除非用户要求。
已有文件必须先 read_file；新建文件不受此限制。成功后工具会返回 diff 摘要。
C：参数可用 path/contents 别名（registry 自动映射为 file_path/content）。""",
        {
            "file_path": _string("目标路径（可用 path 别名）"),
            "content": _string("完整文件内容（可用 contents 别名）"),
        },
        ["file_path", "content"],
    ),
    (
        "append_to_file",
        "在文件末尾追加内容；不存在则创建。适合日志或增量写入。",
        {
            "file_path": _string("文件路径"),
            "content": _string("追加文本"),
            "encoding": _string("编码，默认 utf-8"),
            "ensure_parent_dir": _boolean("是否自动创建父目录，默认 true"),
        },
        ["file_path", "content"],
    ),
    (
        "delete_file",
        "删除单个文件（目录请用 delete_directory）。C 名 path 可用。",
        {
            "path": _string("文件路径"),
            "missing_ok": _boolean("不存在时是否视为成功"),
        },
        ["path"],
    ),
    (
        "delete_directory",
        "递归删除目录；支持 missing_ok、ignore_errors 等安全选项。",
        {
            "path": _string("目录路径"),
            "recursive": _boolean("是否递归，默认 true"),
            "missing_ok": _boolean("不存在时忽略"),
        },
        ["path"],
    ),
    (
        "rename_file_update_refs",
        """移动或重命名文件并保守更新引用（import/路径字符串）。K+C：大重构时优于手工多处 StrReplace。
dry_run 可先预览。""",
        {
            "old_path": _string("原路径"),
            "new_path": _string("新路径"),
            "workspace_root": _string("工作区根，默认 '.'"),
            "update_imports": _boolean("是否尝试更新 import，默认 true"),
            "dry_run": _boolean("仅预览"),
        },
        ["old_path", "new_path"],
    ),
    (
        "list_directory",
        "列出目录树，可控深度。用于快速浏览结构（深层探索可配合 generate_repo_map）。",
        {
            "dir_path": _string("目录，默认 '.'"),
            "max_depth": _integer("最大深度，默认 2"),
        },
        [],
    ),
    (
        "read_file",
        """【Read / 精读】读取本地文本文件。返回 cat -n 风格行号：`=== path (lines 1-80 of 376) ===`，每行形如 `  12→code`。
C：可用 path 代替 file_path；offset/limit 是推荐分页参数（offset 为起始行，limit 为行数），也兼容 start_line/end_line。
大文件默认只返回前 1000 行，并提示下一次 offset/limit。编辑时行号和箭头只供引用，禁止复制进 search_text、replace_text 或 content。
图片/PDF/二进制：对 .png/.jpg/.pdf 等扩展名会直接返回说明（未接多模态），与 C「可读图片」差距已在实现层显式声明，勿用 Shell 绕路。
并行：无依赖的多文件请用 read_multiple_files 或并行多次 read_file。""",
        {
            "file_path": _string("路径（可用 path 别名）"),
            "start_line": _integer("起始行 1-based，可选"),
            "end_line": _integer("结束行 1-based，可选"),
            "offset": _integer("分页起始行 1-based；推荐用于续读大文件"),
            "limit": _integer("分页行数；与 offset 一起使用"),
            "skipPruning": _boolean("true=请求全文；默认 false 时超长文件返回前 1000 行"),
            "explanation": _string("保留兼容参数；当前 read_file 优先保持原始行号锚点"),
        },
        ["file_path"],
    ),
    (
        "read_file_chunk",
        "分块读取超大文件（字节块），避免一次性载入内存。",
        {
            "file_path": _string("路径"),
            "chunk_size": _integer("每块字节，默认 8192"),
            "max_chunks": _integer("最多块数，默认 10"),
        },
        ["file_path"],
    ),
    (
        "read_multiple_files",
        """【C 并行 Read】线程池并发读取多路径，顺序在输出中标注。参数与 read_file 剪枝选项一致。""",
        {
            "file_paths": _array_of_string("要读取的路径列表"),
            "skipPruning": _boolean("传给每个 read_file"),
            "explanation": _string("剪枝说明"),
            "max_workers": _integer("并发上限，默认 8"),
        },
        ["file_paths"],
    ),
    (
        "pdf_info",
        "读取 PDF 基本信息：页数、元数据。优先用于判断 PDF 是否可读、页数是否符合预期。",
        {
            "path": _string("PDF 文件路径"),
        },
        ["path"],
    ),
    (
        "pdf_extract_text",
        "从 PDF 提取文本用于快速理解内容。版式相关结论必须再用 pdf_render_pages 渲染检查。",
        {
            "path": _string("PDF 文件路径"),
            "max_pages": _integer("最多提取页数，默认 20"),
        },
        ["path"],
    ),
    (
        "pdf_render_pages",
        "将 PDF 页面渲染为 PNG，用于版式、图表、分页、字体和可读性验收。需要 Poppler/pdftoppm。",
        {
            "path": _string("PDF 文件路径"),
            "output_dir": _string("PNG 输出目录，默认 output/pdf"),
            "start_page": _integer("起始页，1-based，默认 1"),
            "end_page": _integer("结束页，0 表示到末尾"),
            "dpi": _integer("渲染 DPI，默认 150"),
        },
        ["path"],
    ),
    (
        "pdf_screenshot_page",
        "渲染 PDF 的单页为 PNG 截图，适合快速检查某一页版式。",
        {
            "path": _string("PDF 文件路径"),
            "page": _integer("页码，1-based，默认 1"),
            "output_path": _string("可选 PNG 输出路径"),
            "dpi": _integer("渲染 DPI，默认 150"),
        },
        ["path"],
    ),
    (
        "pdf_merge_split",
        "合并 PDF 或按页码选择输出新 PDF。pages 如 '1-3,5'；为空表示每个输入 PDF 全部页面。",
        {
            "input_paths": _array_of_string("输入 PDF 路径列表"),
            "output_path": _string("输出 PDF 路径"),
            "pages": _string("可选页码选择，如 1-3,5；会应用到每个输入 PDF"),
        },
        ["input_paths", "output_path"],
    ),
    (
        "pdf_create",
        "创建简单 PDF 文档。复杂排版优先生成 DOCX 后渲染/转换，或用专门脚本生成后再 pdf_render_pages 验收。",
        {
            "output_path": _string("输出 PDF 路径"),
            "title": _string("可选标题"),
            "body": _string("正文文本，可多行"),
            "lines": _array_of_string("可选正文行列表"),
        },
        ["output_path"],
    ),
    (
        "docx_create",
        "创建 Word/DOCX 文档，适合报告、作业、说明书草稿。完成后应 docx_render_pages 或 docx_inspect_layout 验收。",
        {
            "output_path": _string("输出 DOCX 路径"),
            "title": _string("可选标题"),
            "body": _string("正文文本，可多行"),
            "sections": _array_of_object("可选章节数组：heading/text/bullets/level"),
        },
        ["output_path"],
    ),
    (
        "docx_edit",
        "编辑现有 DOCX：简单查找替换、追加文本。会保留原结构，复杂修订后应重新渲染验收。",
        {
            "path": _string("输入 DOCX 路径"),
            "output_path": _string("输出 DOCX 路径；为空则原地保存"),
            "find": _string("要查找的文本"),
            "replace": _string("替换文本"),
            "append_text": _string("追加到文档末尾的文本"),
        },
        ["path"],
    ),
    (
        "docx_to_pdf",
        "用 LibreOffice/soffice 将 DOCX 转 PDF。适合交付前生成 PDF 或作为渲染 PNG 的中间步骤。",
        {
            "path": _string("输入 DOCX 路径"),
            "output_dir": _string("输出目录，默认 output/docx"),
        },
        ["path"],
    ),
    (
        "docx_render_pages",
        "将 DOCX 转 PDF 后渲染成 PNG，用于视觉 QA。需要 LibreOffice/soffice 和 Poppler/pdftoppm。",
        {
            "path": _string("输入 DOCX 路径"),
            "output_dir": _string("输出目录，默认 output/docx"),
            "dpi": _integer("渲染 DPI，默认 150"),
        },
        ["path"],
    ),
    (
        "docx_inspect_layout",
        "检查 DOCX 结构摘要：段落、表格、图片、标题；可选触发渲染验收。",
        {
            "path": _string("输入 DOCX 路径"),
            "render": _boolean("是否同时调用 docx_render_pages"),
            "output_dir": _string("渲染输出目录，默认 output/docx"),
        },
        ["path"],
    ),
    (
        "office_report_from_code_run",
        "后台执行 Python 代码/脚本或工作区命令，收集 stdout、stderr 和生成文件，并组装成 DOCX 报告。运行时会注入 METIS_REPORT_ARTIFACTS_DIR 供脚本保存图表/结果。适合实验报告、作业、数据分析、代码运行结果归档；不要为了这类任务默认接管 WPS/PyCharm。",
        {
            "output_path": _string("输出 DOCX 路径，例如 output/docx/experiment-report.docx"),
            "title": _string("报告标题"),
            "assignment": _string("作业/实验要求或背景说明"),
            "code": _string("可选：要写入并运行的 Python 代码"),
            "script_path": _string("可选：已有 Python 脚本路径；与 code/command 三选一"),
            "command": _string("可选：在工作区内执行的命令；与 code/script_path 三选一"),
            "working_dir": _string("可选运行目录，默认当前工作区"),
            "artifacts_dir": _string("可选产物目录，默认 output/report_artifacts/<报告名>"),
            "timeout": _integer("运行超时秒数，默认 120"),
            "render": _boolean("是否尝试 DOCX 渲染验收；需要 LibreOffice 和 Poppler"),
            "conclusion": _string("可选结论/分析文本，会写入报告结尾"),
            "language": _string("inline code 语言；v1 仅支持 python/py"),
        },
        ["output_path"],
    ),
    (
        "generate_repo_map",
        """【K 仓库地图】陌生中大型仓先鸟瞰模块/类/函数再定点 read_file。可与 task_dispatch explore 前配合。""",
        {
            "workspace": _string("工作区根，默认 '.'"),
            "max_depth": _integer("扫描深度，默认 3"),
        },
        [],
    ),
    (
        "read_terminal_state",
        """【C 终端快照】聚合读取 Cursor terminals 目录下快照（或 CURSOR_TERMINALS_DIR）。
长命令、dev server 前先读，避免重复启动。""",
        {
            "terminals_base": _string("可选：显式 terminals 目录"),
            "max_terminal_files": _integer("最多文件数，默认 15"),
            "tail_chars": _integer("每文件保留尾部字符上限，默认 12000"),
        },
        [],
    ),
    (
        "search_in_files",
        "基础跨文件文本搜索（无 rg 时的回退）。已知符号优先 grep_search。",
        {
            "pattern": _string("模式"),
            "file_pattern": _string("如 *.py"),
            "dir_path": _string("目录，默认 '.'"),
        },
        ["pattern"],
    ),
    (
        "grep_search",
        """【Grep / ripgrep】精确符号或正则搜索。禁止在 Shell 里跑 grep；应用本工具。
支持 output_mode: content | files_with_matches | count（对齐 C）；glob 与 glob_pattern 等价；head_limit 与 max_results 取有效上限。
增强功能：multiline（多行匹配）、context_lines（上下文）、type_filter（文件类型）。
无 rg 时降级为基础搜索并提示。""",
        {
            "pattern": _string("正则/模式"),
            "path": _string("搜索根路径或单文件，默认 '.'"),
            "glob_pattern": _string("rg --glob，如 *.py"),
            "glob": _string("C 别名，同 glob_pattern"),
            "max_results": _integer("content 模式最大行数，默认 50"),
            "head_limit": _integer("覆盖 max_results 的上限"),
            "case_sensitive": _boolean("默认 false=rg -i"),
            "output_mode": _string('content | files_with_matches | count，默认 content'),
            "multiline": _boolean("启用多行匹配模式（rg -U），默认 false"),
            "context_lines": _integer("显示匹配行前后各 N 行上下文（rg -C）"),
            "before_context": _integer("显示匹配行前 N 行（rg -B）"),
            "after_context": _integer("显示匹配行后 N 行（rg -A）"),
            "type_filter": _string("文件类型过滤（rg -t），如 'py', 'js', 'md'"),
        },
        ["pattern"],
    ),
    (
        "glob_search",
        """【Glob】按文件名模式发现路径；纯 `*.py` 会自动变为 `**/*.py` 以递归（C 习惯）。可并行多次调用。""",
        {
            "pattern": _string("glob 模式（可用 glob_pattern 别名映射）"),
            "root": _string("根目录（可用 target_directory 别名），默认 '.'"),
            "max_results": _integer("上限，默认 200"),
        },
        ["pattern"],
    ),
    (
        "semantic_search",
        """【SemanticSearch】按含义找代码。若工作区根存在 `.miro_semantic_index.json`（由 `python -m backend.tools.coding.read_search.search.semantic_search build` 生成），
则使用本地分块词频 + 余弦相似度检索；无索引时返回诚实降级与构建命令。参数对齐 C：`target_directories`、`num_results`。
glob→grep→read_file 仍为无索引时的推荐链。""",
        {
            "query": _string("自然语言查询"),
            "workspace_root": _string("工作区根（索引默认放此目录）"),
            "top_k": _integer("Top-K，默认 10"),
            "num_results": _integer("C 别名，同 top_k"),
            "target_directories": _array_of_string("路径前缀过滤；空数组表示全仓"),
            "hint_paths": _string("逗号分隔子目录提示，并入过滤"),
            "index_path": _string("自定义索引 JSON 路径，可选"),
        },
        ["query"],
    ),
    (
        "robust_replace_in_file",
        """【StrReplace + K 降级】编辑已有文件：精确片段替换，失败时 FallbackManager 走模糊与 AST 链。
C：old_string/new_string/path 会映射到 search_text/replace_text/file_path；replace_all=true 时整文件字面量全局替换（适合重命名字符串）。
必须先 read_file 或通过 grep_search 命中过目标文件。old_string 必须在文件中唯一（非 replace_all 时），否则扩大上下文再试。
不要把 read_file 左侧的行号/箭头（如 `12→`）复制进 search_text。成功后工具会返回 diff 摘要。""",
        {
            "file_path": _string("文件路径（可用 path）"),
            "search_text": _string("原片段（可用 old_string）"),
            "replace_text": _string("新片段（可用 new_string）"),
            "replace_all": _boolean("是否替换所有出现，默认 false"),
        },
        ["file_path", "search_text", "replace_text"],
    ),
    (
        "apply_patch",
        """【ApplyPatch】unified diff / 补丁式多 hunk 修改。适合批量结构化变更；建议先 read_file。
实现会调用 git apply 或 patch；勿与 notebook 混用。""",
        {
            "patch_text": _string("完整补丁文本"),
            "base_dir": _string("应用目录，默认 '.'"),
        },
        ["patch_text"],
    ),
    (
        "diff_preview",
        "Preview a proposed file edit as a unified diff without applying it.",
        {
            "file_path": _string("Target file path"),
            "new_content": _string("Full replacement content for a full-file preview"),
            "old_text": _string("Existing text to replace for a partial preview"),
            "new_text": _string("Replacement text used with old_text"),
            "context_lines": _integer("Number of diff context lines, default 3"),
        },
        ["file_path"],
    ),
    (
        "editCode",
        """【K AST 编辑】Python 结构级 replace/insert/delete，解决缩进与字符串匹配失败。
操作类型 replace_node | insert_node | delete_node；selector 如 函数名、Class.method。""",
        {
            "file_path": _string("仅限 .py"),
            "operation": _string("replace_node | insert_node | delete_node"),
            "selector": _string("AST 选择器"),
            "replacement": _string("新代码片段（insert/replace 需要）"),
        },
        ["file_path", "operation", "selector"],
    ),
    (
        "edit_notebook",
        """【EditNotebook】仅用于 .ipynb；禁止 StrReplace/ApplyPatch 直接改 notebook JSON。
对齐 C：is_new_cell=true 时在 cell_idx 插入新单元（可用 idx=len 表示追加）；old_string+new_string 在单元内唯一匹配时做块替换；否则可用 new_source 整单元替换。
cell_language 与 cell_type 二选一指定类型（python/markdown/...）。""",
        {
            "path": _string("ipynb 路径（C 别名 target_notebook）"),
            "cell_idx": _integer("单元索引；插入时 clamp 到 [0,len]"),
            "new_source": _string("整单元替换时的源码（与 old_string 二选一流程）"),
            "cell_type": _string("可选 code|markdown|raw"),
            "is_new_cell": _boolean("true=插入新 cell"),
            "old_string": _string("块替换：原片段（须在当前 cell 唯一）"),
            "new_string": _string("块替换：新片段；插入新 cell 时也可作初始内容"),
            "cell_language": _string("如 python、markdown、md（映射 cell_type）"),
        },
        ["path", "cell_idx"],
    ),
    (
        "rename_symbol",
        "单文件内符号重命名（词边界正则，可能误伤字符串——先只读与备份）。",
        {
            "file_path": _string("文件"),
            "old_name": _string("旧名"),
            "new_name": _string("新名"),
            "word_boundary": _boolean("是否词边界，默认 true"),
        },
        ["file_path", "old_name", "new_name"],
    ),
    (
        "extract_method",
        "从片段生成「新函数草稿 + 调用占位」文本，需人工核对参数（K 辅助重构）。",
        {
            "source_snippet": _string("选中代码"),
            "new_function_name": _string("新函数名"),
            "indent": _string("缩进，默认 4 空格"),
            "self_prefix": _boolean("是否生成 self 方法形，默认 true"),
        },
        ["source_snippet", "new_function_name"],
    ),
    (
        "auto_install_package",
        "静默尝试 pip 安装缺失依赖（K 自愈）；生产环境请谨慎开启。",
        {"package_name": _string("PyPI 包名")},
        ["package_name"],
    ),
    (
        "check_dev_environment",
        "检查本机 Python、Node.js、Git、Go、Rust、Java、CMake、GCC 等开发运行时，并根据项目文件提示缺失项。",
        {"workspace": _string("项目目录，默认当前工作区")},
        [],
    ),
    (
        "install_dev_runtime",
        "通过 winget 安装开发运行时。支持 Python、Node.js、Git、Go、Rust、Java、CMake、GCC/MinGW。仅在明确需要安装时调用。",
        {"runtime_name": _string("运行时名称，如 Python、Node.js、Git、Rust")},
        ["runtime_name"],
    ),
    (
        "setup_workspace",
        "检测项目类型，安装缺失运行时，并运行常见依赖安装命令（pip install / npm install / cargo check）。",
        {"workspace": _string("项目目录，默认当前工作区")},
        [],
    ),
    (
        "check_git_status",
        "工作区 git 状态快照（只读）。",
        {"cwd": _string("仓库目录，默认 '.'")},
        [],
    ),
    (
        "git_commit_pr",
        "git add -A → commit → 可选 push；PR 需在托管平台或 gh 完成。",
        {
            "message": _string("commit message"),
            "cwd": _string("仓库路径"),
            "remote": _string("远端名，默认 origin"),
            "branch": _string("可选分支"),
            "push": _boolean("是否 push，默认 true"),
        },
        ["message"],
    ),
    (
        "git_diff",
        "Show git diff for working tree or staged changes.",
        {
            "staged": _boolean("True to show staged changes"),
            "file_path": _string("Optional specific file path"),
            "cwd": _string("Git repository directory, default '.'"),
        },
        [],
    ),
    (
        "git_stage",
        "Stage specific files for commit.",
        {
            "files": _array_of_string("File paths to stage"),
            "cwd": _string("Git repository directory, default '.'"),
        },
        ["files"],
    ),
    (
        "git_create_branch",
        "Create and switch to a new git branch.",
        {
            "branch_name": _string("New branch name"),
            "cwd": _string("Git repository directory, default '.'"),
        },
        ["branch_name"],
    ),
    (
        "git_log",
        "Show recent git commit history.",
        {
            "count": _integer("Number of commits to show, default 5"),
            "oneline": _boolean("Use compact one-line format"),
            "cwd": _string("Git repository directory, default '.'"),
        },
        [],
    ),
    (
        "run_tests",
        "Auto-detect and run the project's test suite, or run a custom test command.",
        {
            "command": _string("Optional custom command, e.g. python -m pytest"),
            "cwd": _string("Working directory, default '.'"),
            "timeout": _integer("Timeout in seconds, default 120"),
        },
        [],
    ),
    (
        "read_lints",
        """【ReadLints】默认 CLI：ruff JSON → pyright/basedpyright JSON → mypy / flake8 / pylint。可选 LSP：`MIRO_READLINTS_MODE=lsp|auto`，`MIRO_LSP_COMMAND`（如 `pylsp` 或 `python -u path/to/lsp_stub_server.py`），`MIRO_LSP_DIAG_TIMEOUT_SEC`，`MIRO_LSP_FALLBACK_CLI`；LSP 走标准 Content-Length stdio，无诊断时可回退 CLI。与 IDE 内嵌 LSP 体验仍可能有差距。实质性编辑后应对改动文件调用。""",
        {
            "paths": _string("文件或目录；也可传 JSON 数组字符串"),
            "max_output": _integer("最大输出字符"),
        },
        [],
    ),
    (
        "analyze_complexity",
        "对 Python 文件做简易圈复杂度/体量评估。",
        {"file_path": _string("路径")},
        ["file_path"],
    ),
    (
        "undo_last_edit",
        "从备份恢复文件（若存在备份路径）。",
        {
            "file_path": _string("要恢复的文件"),
            "backup_path": _string("可选指定备份"),
        },
        ["file_path"],
    ),
    (
        "undo_edit",
        "Revert an uncommitted file edit using git checkout.",
        {
            "file_path": _string("File path to revert"),
            "cwd": _string("Git repository directory, default '.'"),
        },
        ["file_path"],
    ),
    (
        "verify_compilation",
        "py_compile 等编译级校验。",
        {"file_path": _string("路径")},
        ["file_path"],
    ),
    (
        "web_search",
        """【WebSearch】需要实时信息时使用；查询词应含年份与具体关键词（以系统注入当前日期为准）。""",
        {
            "query": _string("查询（可用 search_term 别名）"),
            "max_results": _integer("条数上限，默认 5"),
        },
        ["query"],
    ),
    (
        "web_fetch",
        """【WebFetch】首选网页读取工具：抓取 HTTPS 静态页面并默认提取干净 Markdown 正文；禁止内网/localhost（SSRF 防护以实现为准）。
适合标题、正文、新闻、文档页。页面明显是 SPA 空壳、需要点击/登录/JS 渲染时，再升级 browse_web。raw=true 才返回原始 HTML。""",
        {
            "url": _string("完整 https URL"),
            "limit": _integer("兼容旧参数；等价于 max_chars，默认 8000"),
            "max_chars": _integer("最大返回字符，默认 8000"),
            "raw": _boolean("true=返回原始 HTML；默认 false=正文提取 Markdown"),
        },
        ["url"],
    ),
    (
        "browse_web",
        "真实浏览器路径：仅当 web_fetch 内容残缺、页面需要 JS 渲染/点击/登录态时使用；可导航、点击、填写表单、提取信息。依赖缺失时会返回安装说明。",
        {
            "task": _string("浏览器任务目标"),
            "url": _string("可选起始 URL"),
            "max_steps": _integer("最大浏览步骤，默认 15"),
            "extract_content": _boolean("是否返回页面提取内容，默认 false"),
        },
        ["task"],
    ),
    (
        "browse_and_extract",
        "打开指定 URL 并提取指定信息；适合已知页面的数据提取。",
        {
            "url": _string("要访问的 URL"),
            "what_to_extract": _string("要提取的信息"),
        },
        ["url", "what_to_extract"],
    ),
    (
        "generate_image",
        "仅当用户明确要生成图片时使用；不用于数据图表。description 可用 prompt 别名。",
        {
            "prompt": _string("画面描述"),
            "size": _string("如 1024x1024"),
        },
        ["prompt"],
    ),
    (
        "ask_question",
        """【AskQuestion】需要用户选择题时使用。questions 为对象数组：id、prompt、options(>=2)、allow_multiple。
兼容旧版：questions 为字符串数组时自动补默认二元选项。返回 JSON，schema_version=2。""",
        {
            "title": _string("标题"),
            "questions": _array_of_object(
                "C 风格: [{id,prompt,options[],allow_multiple?}]；或字符串列表（兼容）"
            ),
            "blocking": _boolean("宿主是否应阻塞至用户作答"),
        },
        ["title", "questions"],
    ),
    (
        "populate_steering",
        "注入工作区/环境摘要到 steering 上下文（K）。",
        {"workspace": _string("根路径，默认 '.'")},
        [],
    ),
    (
        "todo_write",
        """【TodoWrite】多步任务（≥3 步）、规划型任务时使用；todos≥2 条；merge=true 合并更新。
每项需 id、content、status（pending/in_progress/completed/cancelled）。""",
        {
            "todos": _array_of_object("TODO 项对象列表"),
            "merge": _boolean("是否合并磁盘已有项"),
            "todo_storage_path": _string("可选 JSON 存储路径，默认 .agent_todos.json（映射实现参数 path）"),
        },
        ["todos"],
    ),
    (
        "read_project_memory",
        "Read the persistent MIRO.md project memory file.",
        {
            "scope": {
                "type": "string",
                "enum": ["project", "global"],
                "description": "Read project-level METIS.md or global METIS_HOME/METIS.md, with MIRO.md legacy fallback",
                "default": "project",
            },
        },
        [],
    ),
    (
        "read_workspace_memory",
        "Read the auto-maintained workspace memory (.metis/memory.json): inferred project type, "
        "key files, architecture notes, common commands, and learned patterns from prior sessions. "
        "Call this when resuming a long-running project to recover continuity.",
        {},
        [],
    ),
    (
        "update_project_memory",
        "Update MIRO.md to persist important project facts, conventions, or user corrections across sessions.",
        {
            "content": _string("Text to write to MIRO.md"),
            "mode": {
                "type": "string",
                "enum": ["append", "replace", "section"],
                "description": "append adds to end, replace overwrites all, section replaces one markdown section",
                "default": "append",
            },
            "section": _string("Section header to replace, e.g. ## Tech Stack"),
            "scope": {
                "type": "string",
                "enum": ["project", "global"],
                "description": "Update project-level METIS.md or global METIS_HOME/METIS.md",
                "default": "project",
            },
        },
        ["content"],
    ),
    (
        "switch_mode",
        """【SwitchMode】在 Plan / Act 等模式间切换；需产品确认 mode 枚举。附 note 说明原因。""",
        {
            "mode": _string("模式名，如 plan / act"),
            "note": _string("简短说明"),
        },
        ["mode"],
    ),
    (
        "write_open_files_context",
        "写入 `.miro_open_files.json`（paths + 可选 focus），供服务端 context 注入「打开文件」；merge 可与磁盘合并。",
        {
            "paths": _array_of_string("打开的文件路径列表，可空（merge 时保留磁盘 paths）"),
            "focus": _string("可选当前焦点路径；空字符串可清除 focus"),
            "merge": _boolean("是否与磁盘已有 paths 去重合并"),
            "open_files_storage_path": _string("可选输出路径，默认 .miro_open_files.json（映射 path）"),
        },
        [],
    ),
    (
        "delegate_explore",
        "子代理：广度探索占位（同进程）。复杂仓仍建议 generate_repo_map + grep。",
        {
            "goal": _string("目标描述"),
            "root": _string("根目录"),
            "max_depth": _integer("深度，默认 2"),
        },
        ["goal"],
    ),
    (
        "delegate_browser",
        "子代理：把网页导航、表单、提取等任务交给真实 browser-use 浏览器代理。",
        {"task": _string("任务"), "url": _string("可选 URL")},
        ["task"],
    ),
    (
        "delegate_shell",
        "子代理：复杂环境/脚本任务占位。",
        {"script_description": _string("要做什么"), "cwd": _string("工作目录")},
        ["script_description"],
    ),
    (
        "delegate_best_of_n",
        "子代理：多方案竞争；每个写入尝试必须先创建独立 git worktree，失败则拒绝在主工作区执行。",
        {
            "task": _string("任务"),
            "n": _integer("方案数，默认 3，最多 3"),
            "workspace_root": _string("源 git 工作区根，默认 ."),
        },
        ["task"],
    ),
    (
        "summon_context_gatherer",
        "K：召唤上下文——仓库地图 + 可选附加文件精读。",
        {
            "workspace": _string("根"),
            "extra_paths": _array_of_string("额外要 read 的路径"),
            "max_depth": _integer("地图深度"),
        },
        [],
    ),
    (
        "custom_agent_creator",
        "动态定义一次性子代理配置（占位返回说明）。",
        {
            "name": _string("名称"),
            "system_prompt": _string("系统提示"),
            "tools_allow": _string("允许工具模式，默认 *"),
        },
        ["name", "system_prompt"],
    ),
    (
        "task_dispatch",
        """【Task / C】统一子任务入口。prompt 必须自洽；subagent_type 选 explore|shell|browser|best_of_n|context_gatherer|custom。
默认 `MIRO_TASK_SUBPROCESS=1`：独立子进程执行，结果经 JSON 回传（见 `others/说明/TASK_SUBPROCESS_PROTOCOL.md`）；设 `MIRO_TASK_SUBPROCESS=0` 则同进程 delegate。
`resume` 非空时复用同 session id 文件（`.delegate_sessions/`）。""",
        {
            "prompt": _string("子任务说明"),
            "description": _string("3–5 词摘要"),
            "subagent_type": _string("explore | shell | browser | best_of_n | context_gatherer | custom"),
            "workspace_root": _string("工作区根，默认 ."),
            "model": _string("预留"),
            "readonly": _boolean("预留"),
            "resume": _string("session id，可空则新建"),
            "run_in_background": _boolean("预留"),
        },
        ["prompt"],
    ),
    (
        "run_parallel_tasks",
        """【Task 并行增强】一次性并行执行多个独立 Task。需要 `MIRO_TASK_SUBPROCESS=1`（默认）。
每个任务包含 prompt（必填）、subagent_type（可选，默认 explore）、resume（可选）。
适用场景：多目录分析、多独立探索、批量验证等。""",
        {
            "tasks": {
                "type": "array",
                "description": "任务列表，每个任务是包含 prompt/subagent_type/resume 的对象",
                "items": {
                    "type": "object",
                    "properties": {
                        "prompt": _string("任务描述"),
                        "subagent_type": _string("子代理类型，默认 explore"),
                        "resume": _string("可选：恢复会话 ID"),
                    },
                    "required": ["prompt"],
                },
            },
            "workspace_root": _string("工作区根，默认 ."),
            "timeout_sec": _integer("每个任务超时秒数，默认 180"),
            "max_workers": _integer("最大并行数，0 表示自动（默认 min(任务数, CPU核心数)）"),
        },
        ["tasks"],
    ),
    (
        "run_task_graph",
        """【Task DAG】按有向无环图拓扑执行多节点子任务；同层并行策略同 run_parallel_tasks。
nodes: [{id, prompt, subagent_type?, resume?}]；edges: [{from, to}]（from 先于 to）。含环则返回 ❌ 且不执行。
resume 非空：须已有 `<workspace>/.delegate_sessions/<resume>.json` 图级 checkpoint。需要 MIRO_TASK_SUBPROCESS=1。""",
        {
            "nodes": {
                "type": "array",
                "description": "节点列表，每项含 id、prompt，可选 subagent_type、resume",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": _string("节点唯一 id"),
                        "prompt": _string("子任务说明"),
                        "subagent_type": _string("explore|shell|…，默认 explore"),
                        "resume": _string("可选：子任务会话 id，须已有对应 .json"),
                    },
                    "required": ["id", "prompt"],
                },
            },
            "edges": {
                "type": "array",
                "description": "有向边 from -> to",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": _string("前置节点 id"),
                        "to": _string("后继节点 id"),
                    },
                    "required": ["from", "to"],
                },
            },
            "workspace_root": _string("工作区根，默认 ."),
            "timeout_sec": _integer("每节点超时秒数，默认 180"),
            "max_workers": _integer("并行上限，0 表示自动"),
            "resume": _string("图级 checkpoint 的 session id；空则新建"),
        },
        ["nodes", "edges"],
    ),
    (
        "manage_mcp_servers",
        "列出或管理 MCP 配置（JSON）。",
        {
            "action": _string("如 list"),
            "config_path": _string("配置路径"),
        },
        [],
    ),
    (
        "load_workflow_guidelines",
        "加载项目内工作流指南：.cursor/rules/**/*.md 与 AGENTS.md 等（K）。",
        {
            "workspace": _string("工作区根，默认 '.'"),
            "max_chars": _integer("合并后最大字符，默认 12000"),
        },
        [],
    ),
]


def build_tools_schema() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, desc, props, req in _TOOL_SPECS:
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc.strip() if desc else "",
                    "parameters": _obj(props, req),
                },
            }
        )
    return out
