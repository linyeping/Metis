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
        "metis_rootfs_asset_status",
        "检查 Metis rootfs 导入资产：查找 rootfs.tar/rootfs.vhdx，计算 SHA256，可选用 openssl 验 detached signature，并对照 metis-vm-pack.json 中登记的 checksum。只读诊断。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "rootfs_path": _string("可选明确 rootfs.tar/rootfs.tar.gz/rootfs.tar.zst/rootfs.vhdx 路径"),
            "expected_sha256": _string("可选期望 SHA256；为空则读取 bundle manifest 中登记值"),
            "signature_path": _string("可选 detached signature 路径；需要同时提供 public_key_path"),
            "public_key_path": _string("可选 OpenSSL public key 路径；与 signature_path 一起用于 openssl dgst -sha256 -verify"),
        },
        [],
    ),
    (
        "metis_rootfs_source_status",
        "解析 Metis rootfs 来源但不下载：支持本地 manifest、manifest_url、直接 asset_url，返回选中的资产 URL、SHA256、签名 URL 和是否满足下载前校验要求。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "manifest_url": _string("可选 rootfs source manifest URL，支持 http(s) 或 file://"),
            "manifest_path": _string("可选本地 rootfs source manifest JSON 路径"),
            "asset_url": _string("可选直接 rootfs 资产 URL/路径；提供时优先于 manifest 中资产"),
            "expected_sha256": _string("可选期望 SHA256；直接 asset_url 下载时必须提供，manifest 中也可覆盖"),
        },
        [],
    ),
    (
        "metis_rootfs_asset_download",
        "下载或复制 Metis rootfs 资产到 VM Pack，并可自动注册进 metis-vm-pack.json。默认 dry_run=true 只返回计划；dry_run=false 时必须有 SHA256，下载后会校验 checksum，支持 file/http/https/本地路径。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "manifest_url": _string("可选 rootfs source manifest URL，支持 http(s) 或 file://"),
            "manifest_path": _string("可选本地 rootfs source manifest JSON 路径"),
            "asset_url": _string("可选直接 rootfs 资产 URL/路径；提供时优先于 manifest 中资产"),
            "expected_sha256": _string("期望 SHA256；manifest 没提供时必填，提供时会覆盖 manifest 首选资产"),
            "signature_url": _string("可选 detached signature URL/路径；下载后与 public_key_path 一起校验"),
            "public_key_path": _string("可选 OpenSSL public key 路径；与 signature_url 一起用于签名校验"),
            "output_path": _string("可选下载目标路径；为空则放入 bundle 并规范命名为 rootfs.tar/rootfs.vhdx 等"),
            "dry_run": _boolean("默认 true，只返回计划；false 才下载/复制文件"),
            "force": _boolean("目标已存在时是否覆盖；默认 false"),
            "register": _boolean("下载成功后是否注册到 metis-vm-pack.json；默认 true"),
        },
        [],
    ),
    (
        "metis_rootfs_builder_status",
        "检查 Metis rootfs builder 状态但不写文件：检测 Docker/WSL、builder 脚本是否已生成、当前会选择的构建 backend。v1 中 Docker 是实际可执行构建后端，WSL 先生成 debootstrap 脚本。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "backend": _string("auto | docker | wsl | script_only，默认 auto"),
            "base_image": _string("Docker rootfs 基础镜像，默认 ubuntu:22.04"),
            "wsl_distro": _string("可选 WSL distro；用于检测 WSL 脚本执行环境"),
        },
        [],
    ),
    (
        "metis_rootfs_build",
        "生成并可执行 Metis rootfs 构建。默认 dry_run=true 只返回计划；dry_run=false 会写 builder 脚本。backend=docker 可实际 docker build/create/export 产出 rootfs.tar 并注册 SHA256；backend=wsl/script_only 只生成可审计脚本。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "backend": _string("auto | docker | wsl | script_only，默认 auto"),
            "base_image": _string("Docker rootfs 基础镜像，默认 ubuntu:22.04"),
            "profile": _string("standard 或 minimal。standard 安装 Python/Node/Git/rg/PDF/DOCX 工具；minimal 只复制 metisd 和目录结构"),
            "output_path": _string("可选 rootfs.tar 输出路径；为空则写入 bundle/rootfs.tar"),
            "image_tag": _string("可选 Docker 构建镜像 tag；为空自动生成 metis/rootfs:<timestamp>"),
            "wsl_distro": _string("可选 WSL distro；v1 仅用于计划/脚本"),
            "dry_run": _boolean("默认 true，只返回计划；false 才写脚本并尝试构建"),
            "allow_network": _boolean("是否允许构建联网。profile=standard 需要 true；profile=minimal 可离线使用本地已有 base image"),
            "register": _boolean("构建成功后是否调用 metis_rootfs_asset_register 写入 manifest；默认 true"),
            "force": _boolean("rootfs.tar 已存在时是否覆盖；默认 false"),
            "keep_image": _boolean("Docker 构建后是否保留中间 image 用作缓存；默认 true"),
            "timeout": _integer("Docker build/export 超时秒数，默认 1800"),
        },
        [],
    ),
    (
        "metis_rootfs_image_builder_status",
        "检查 Metis rootfs.vhdx image builder 状态但不写文件：检测 WSL import 能力、rootfs.tar 来源、rootfs.vhdx 目标、builder 脚本是否已生成。第一版可执行路径是 rootfs.tar -> WSL2 临时 distro/ext4.vhdx -> rootfs.vhdx。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "backend": _string("auto | wsl_import | script_only，默认 auto"),
            "rootfs_tar_path": _string("可选 rootfs.tar 来源路径；为空使用 bundle/rootfs.tar"),
            "output_path": _string("可选 rootfs.vhdx 输出路径；为空使用 bundle/rootfs.vhdx"),
            "temp_distro_name": _string("可选临时 WSL distro 名，默认 MetisRootfsImageBuilder"),
            "install_dir": _string("可选 WSL import 临时目录；为空使用 .metis/rootfs-image/wsl/<distro>"),
        },
        [],
    ),
    (
        "metis_rootfs_image_build",
        "生成 Metis 自己的 rootfs.vhdx。默认 dry_run=true 只返回计划；dry_run=false 会写 image builder 脚本，并通过 WSL2 import 把 rootfs.tar 变成 ext4.vhdx，再复制/登记为 bundle/rootfs.vhdx。rootfs.tar 缺失时可先调用 metis_rootfs_build 自动生成，镜像内会包含 Python/Node/Git/rg/PDF-DOCX 文档工具、metisd、artifact/diagnostics 目录和权限策略。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "backend": _string("auto | wsl_import | script_only，默认 auto"),
            "rootfs_tar_path": _string("可选 rootfs.tar 来源路径；为空使用 bundle/rootfs.tar"),
            "output_path": _string("可选 rootfs.vhdx 输出路径；为空使用 bundle/rootfs.vhdx"),
            "temp_distro_name": _string("可选临时 WSL distro 名，默认 MetisRootfsImageBuilder"),
            "install_dir": _string("可选 WSL import 临时目录；为空使用 .metis/rootfs-image/wsl/<distro>"),
            "build_rootfs_tar": _boolean("rootfs.tar 缺失时是否先调用 metis_rootfs_build 生成；默认 true"),
            "rootfs_backend": _string("生成 rootfs.tar 的 backend：auto | docker | wsl | script_only，默认 auto"),
            "base_image": _string("Docker rootfs 基础镜像，默认 ubuntu:22.04"),
            "profile": _string("minimal | standard | office。standard 预装 Python/Node/Git/rg/PDF/DOCX 工具；office 额外打 LibreOffice/ImageMagick"),
            "image_tag": _string("可选 Docker 构建镜像 tag；为空自动生成 metis/rootfs:<timestamp>"),
            "dry_run": _boolean("默认 true，只返回计划；false 才写脚本并尝试生成 rootfs.vhdx"),
            "allow_network": _boolean("是否允许 rootfs.tar 构建联网。profile=standard/office 通常需要 true"),
            "register": _boolean("构建成功后是否调用 metis_rootfs_asset_register 登记 rootfs.vhdx；默认 true"),
            "force": _boolean("rootfs.vhdx 或临时 distro 已存在时是否覆盖/清理；默认 false"),
            "cleanup": _boolean("构建完成后是否 unregister 临时 WSL distro；默认 true"),
            "timeout": _integer("WSL import / rootfs build 超时秒数，默认 1800"),
        },
        [],
    ),
    (
        "metis_rootfs_asset_register",
        "把 Metis 自有 rootfs.tar/rootfs.vhdx 登记进 VM Pack manifest：可复制到 bundle、计算 SHA256、校验 expected_sha256/签名，并写入 metis-vm-pack.json。执行 WSL import 前应先使用此工具登记资产。",
        {
            "rootfs_path": _string("rootfs.tar/rootfs.tar.gz/rootfs.tar.zst/rootfs.vhdx 路径"),
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM Pack bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "expected_sha256": _string("可选期望 SHA256；提供时必须匹配"),
            "signature_path": _string("可选 detached signature 路径；需要同时提供 public_key_path"),
            "public_key_path": _string("可选 OpenSSL public key 路径；与 signature_path 一起用于 openssl dgst -sha256 -verify"),
            "source_url": _string("可选来源 URL，写入 manifest 便于审计"),
            "copy": _boolean("是否复制到 bundle 并重命名为 rootfs.*；默认 true"),
            "force": _boolean("目标 rootfs 已存在时是否覆盖；默认 false"),
        },
        ["rootfs_path"],
    ),
    (
        "metis_runtime_bundle_prepare",
        "准备 Metis 自有 runtime bundle：围绕已构建/下载的 rootfs.tar/rootfs.vhdx 写入 metis-runtime-bundle.json、metis-runtime-latest.json、.origin 溯源文件、安装/搬盘/smoke PowerShell 脚本，并同步 metis-vm-pack.json 的 runtime_bundle 块。第一版以 WSL import 为可运行后端，不使用 Claude VM 资产。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis runtime bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "rootfs_path": _string("可选 Metis-owned rootfs.tar/rootfs.tar.gz/rootfs.tar.zst/rootfs.vhdx；提供时会先登记进 bundle"),
            "version": _string("runtime bundle 版本，例如 0.1.0 或 26.6.17；默认 0.1.0-local"),
            "channel": _string("发布通道，例如 local/stable/beta；默认 local"),
            "expected_sha256": _string("可选 rootfs 期望 SHA256；提供时必须匹配"),
            "source_url": _string("可选 rootfs 来源 URL/说明，写入 provenance"),
            "signature_path": _string("可选 detached signature 路径；需要同时提供 public_key_path"),
            "public_key_path": _string("可选 OpenSSL public key 路径；与 signature_path 一起验证签名"),
            "copy_rootfs": _boolean("rootfs_path 提供时是否复制到 bundle 并规范命名；默认 true"),
            "force": _boolean("目标文件/脚本已存在时是否覆盖；默认 false"),
            "dry_run": _boolean("是否只返回写入计划；默认 false"),
        },
        [],
    ),
    (
        "metis_runtime_bundle_package",
        "把已准备好的 Metis runtime bundle 打包成 release 资产：生成 metis-runtime-<version>-<channel>-full.zip、.sha256、metis-runtime-release-*.json 和 latest release manifest。用于后续上传 GitHub release 或配置 runtime update 源。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis runtime bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "output_dir": _string("可选输出目录，默认 .metis/runtime-pack/releases"),
            "version": _string("可选 package 版本；为空则读取 metis-runtime-bundle.json"),
            "channel": _string("可选发布通道；为空则读取 bundle manifest，默认 local"),
            "include_rootfs": _boolean("是否把 rootfs.tar/rootfs.vhdx 一起打进 zip；默认 true"),
            "force": _boolean("package 已存在时是否覆盖；默认 false"),
            "dry_run": _boolean("是否只返回打包计划；默认 false"),
        },
        [],
    ),
    (
        "metis_runtime_bundle_package_v2",
        "把 Metis direct-VM runtime assets 打成可发布包 v2：校验 vmlinuz、initrd、rootfs.vhdx、metis-bin.vhdx、manifest，生成 rootfs.vhdx.zst、SHA256SUMS.txt、runtime-bundle-v2-manifest.json、verify/install PowerShell 脚本和 release zip。用于给用户下载/解压/校验，避免用户自己安装 Docker/WSL 来构建 runtime 文件。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "output_dir": _string("可选输出目录，默认 .metis/runtime-pack/releases"),
            "version": _string("可选 package 版本；为空读取 metis-vm-pack.json version，默认 0.1.0-local"),
            "channel": _string("可选发布通道，默认 direct"),
            "package_name": _string("可选自定义 zip 文件名；为空生成 metis-runtime-bundle-v2-<version>-<channel>.zip"),
            "include_sessiondata": _boolean("是否把 sessiondata.vhdx 也打进发布包；默认 false，通常用户本地生成 session disk"),
            "force": _boolean("目标 package/rootfs.vhdx.zst 已存在时是否覆盖；默认 false"),
            "dry_run": _boolean("默认 false；true 时只返回打包计划和缺失资产/压缩器状态"),
        },
        [],
    ),
    (
        "metis_vm_direct_assets_prepare",
        "准备 Claude-style 直启 VM 所需的 Metis 自有资产层：rootfs.vhdx、vmlinuz、initrd、metis-bin.vhdx、sessiondata.vhdx，并生成 create-direct-vm-assets.ps1、host/hcs-runner.ps1、host/hcs-runner-plan.json 和 metis-direct-vm-assets.json。该工具只做资产与 HCS/Hyper-V runner 合同准备，不会假装完整 VM runner 已完成。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "rootfs_vhdx_path": _string("可选 Metis-owned rootfs.vhdx 来源路径"),
            "kernel_path": _string("可选 Metis-owned vmlinuz 来源路径"),
            "initrd_path": _string("可选 Metis-owned initrd 来源路径"),
            "metis_bin_path": _string("可选 Metis-owned metis-bin.vhdx 来源路径"),
            "sessiondata_path": _string("可选 sessiondata.vhdx 来源路径"),
            "version": _string("direct VM asset manifest 版本，默认 0.1.0-local"),
            "copy_assets": _boolean("是否复制传入资产到 bundle 并写 .origin；默认 true"),
            "create_vhdx_scripts": _boolean("是否生成创建 sessiondata/metis-bin VHDX 的 PowerShell 脚本；默认 true"),
            "create_vhdx": _boolean("是否立即执行 VHDX 创建脚本；默认 false，需要 Hyper-V PowerShell 模块"),
            "sessiondata_size_gb": _integer("sessiondata.vhdx 动态盘大小 GB，默认 8"),
            "metis_bin_size_mb": _integer("metis-bin.vhdx 动态盘大小 MB，默认 256"),
            "force": _boolean("目标资产/脚本存在时是否覆盖；默认 false"),
            "dry_run": _boolean("是否只返回计划；默认 false"),
        },
        [],
    ),
    (
        "metis_vm_direct_runner_prepare",
        "准备 Metis direct VM runner v1：写入 host/hcs-runner.ps1、host/hcs-runner-plan.json、guest/metisd.py、guest/PROTOCOL.md、host/artifact-sync.ps1、host/lifecycle-schema.json 和 metis-direct-runner.json。该工具实现 host/guest JSONL 协议、artifact sync、diagnostics 和生命周期合同；HCS ComputeSystem 直启仍保持 gated，不会假装已可生产运行。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "version": _string("direct runner manifest 版本，默认 0.1.0-local"),
            "transport": _string("jsonl-stdio 或 hcs-vsock-jsonl。默认 jsonl-stdio，用于本地协议 smoke；HCS 仍 gated"),
            "force": _boolean("是否重写 scaffold 文件；默认 false"),
            "dry_run": _boolean("是否只返回计划；默认 false"),
        },
        [],
    ),
    (
        "metis_vm_direct_runner_smoke",
        "本地验收 Metis direct VM guest 协议：通过 stdio 启动 guest/metisd.py，执行 runtime.hello、session.mount、process.run、artifact.list、artifact.collect、diagnostics.export、runtime.shutdown，验证 host/guest JSONL、artifact 同步和生命周期日志。不会启动 HCS/Hyper-V VM。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "command": _string("可选 smoke 命令；为空时自动生成一个写入 artifact 的 Python smoke 脚本"),
            "timeout": _integer("smoke 命令超时秒数，默认 30"),
            "prepare_if_missing": _boolean("guest daemon 或 manifest 缺失时是否自动执行 runner prepare；默认 true"),
        },
        [],
    ),
    (
        "metis_vm_hcs_starter_prepare",
        "准备真实 HCS ComputeSystem starter：根据 rootfs.vhdx、vmlinuz、initrd、sessiondata.vhdx、metis-bin.vhdx 生成 host/hcs-compute-system.json、host/HcsApiBridge.cs、host/hcs-starter.ps1、host/hcs-start-plan.json 和 metis-hcs-starter.json。该 starter 使用 Windows HCS API 创建/启动/终止 ComputeSystem；prepare 不会启动 VM。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "version": _string("HCS starter manifest 版本，默认 0.1.0-local"),
            "memory_mb": _integer("VM 内存 MB，默认 2048，最小 512"),
            "processor_count": _integer("vCPU 数，默认 2，最小 1"),
            "kernel_cmdline": _string("可选 LinuxKernelDirect KernelCmdLine；为空使用 Metis 默认 root=/dev/sda rw init=/usr/local/bin/metisd"),
            "force": _boolean("是否重写 scaffold/runner/starter 文件；默认 false"),
            "dry_run": _boolean("是否只返回计划；默认 false"),
        },
        [],
    ),
    (
        "metis_vm_hcs_starter_start",
        "计划或执行真实 HCS ComputeSystem start。默认 dry_run=true 只返回命令和状态；dry_run=false 且 enable_experimental_hcs=true 时，会通过 host/hcs-starter.ps1 调用 HcsCreateComputeSystem/HcsStartComputeSystem，并默认 hold 后 terminate。需要 Metis-owned direct VM assets 和 Windows vmcompute。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "compute_system_id": _string("可选 HCS compute system id；为空自动生成 metis-hcs-*"),
            "compute_document_path": _string("可选自定义 HCS ComputeSystem JSON；为空使用 bundle/host/hcs-compute-system.json"),
            "timeout": _integer("HCS create/start/terminate 每步超时秒数，默认 120"),
            "hold_seconds": _integer("启动后保持秒数，默认 3；keep_running=false 时随后 terminate"),
            "keep_running": _boolean("是否启动后保持运行不 terminate；默认 false"),
            "enable_experimental_hcs": _boolean("真实执行 HCS start 的显式门禁；dry_run=false 时必须 true"),
            "prepare_if_missing": _boolean("缺少 starter 文件时是否自动 prepare；默认 true"),
            "dry_run": _boolean("默认 true，只返回 start 计划，不调用 HCS"),
        },
        [],
    ),
    (
        "metis_vm_guest_handshake_prepare",
        "准备 Metis Guest Handshake Verifier：生成 host/guest-handshake.ps1、host/guest-handshake-plan.json 和 metis-guest-handshake.json。该层定义 HCS direct runner_ready 的硬门槛：只有 HCS/vsock JSONL 收到 booted guest 里的 metisd runtime.hello，HCS 直启 VM 才能标记为 ready；stdio 只验证 host-side guest protocol。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "version": _string("guest handshake manifest 版本，默认 0.1.0-local"),
            "transport": _string("hcs-vsock-jsonl 或 jsonl-stdio。默认 hcs-vsock-jsonl；jsonl-stdio 只能验证协议，不能提升 HCS direct runner_ready"),
            "timeout": _integer("等待 runtime.hello 的超时秒数，默认 30"),
            "force": _boolean("是否重写 scaffold/runner/starter/handshake 文件；默认 false"),
            "dry_run": _boolean("是否只返回计划；默认 false"),
        },
        [],
    ),
    (
        "metis_vm_guest_handshake_verify",
        "计划或执行 guest handshake verifier。默认 dry_run=true 只返回等待 runtime.hello 的计划；transport=jsonl-stdio 且 dry_run=false 会启动 guest/metisd.py 做 host-only 协议 smoke 并写 receipt，但不会提升 HCS direct runner_ready；transport=hcs-vsock-jsonl 未来会在 HCS start 后等待 guest metisd 回 runtime.hello，当前缺少 HCS/vsock bridge 时会明确返回 transport unavailable。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "transport": _string("hcs-vsock-jsonl 或 jsonl-stdio，默认 hcs-vsock-jsonl"),
            "compute_system_id": _string("可选 HCS compute system id；为空使用 metis-handshake"),
            "timeout": _integer("等待 runtime.hello 的超时秒数，默认 30"),
            "enable_experimental_hcs": _boolean("真实 HCS guest handshake 的显式门禁；dry_run=false 且 hcs-vsock-jsonl 时必须 true"),
            "prepare_if_missing": _boolean("缺少 handshake manifest 时是否自动 prepare；默认 true"),
            "dry_run": _boolean("默认 true，只返回 handshake 计划，不连接 VM"),
        },
        [],
    ),
    (
        "metis_vm_rootfs_boot_verifier_prepare",
        "准备 Metis rootfs boot verifier：生成 host/boot-cmdline-matrix.json、host/rootfs-inspect.ps1、host/rootfs-boot-verifier.ps1 和 metis-rootfs-boot-verifier.json。它会为 root=/dev/sda、/dev/sda1、/dev/vda 等候选生成多套 kernel cmdline，用来定位 HCS 直启失败到底是 root device、init 路径、rootfs 布局还是 guest daemon 缺失。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "version": _string("boot verifier manifest 版本，默认 0.1.0-local"),
            "root_device_candidates": _array_of_string("可选 root device 候选，例如 ['/dev/sda','/dev/sda1','/dev/vda1']"),
            "init_candidates": _array_of_string("可选 init 候选，例如 ['/usr/local/bin/metisd','/sbin/init']"),
            "force": _boolean("是否重写 starter/verifier 文件；默认 false"),
            "dry_run": _boolean("是否只返回计划；默认 false"),
        },
        [],
    ),
    (
        "metis_vm_rootfs_boot_verify",
        "计划或执行 rootfs boot verifier。默认 dry_run=true，只生成每个 cmdline 候选的 HCS start 计划和 compute document 路径；dry_run=false 且 enable_experimental_hcs=true 时，会逐个候选调用 HCS starter。当前只能验证 HCS create/start/terminate 证据，runner_ready 仍等未来 guest metisd handshake 成功后才提升。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 Metis VM bundle 目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "candidate_ids": _array_of_string("可选只验证指定候选 id；为空验证矩阵全部候选"),
            "timeout": _integer("HCS 每步超时秒数，默认 120"),
            "hold_seconds": _integer("启动后保持秒数，默认 3；随后 terminate"),
            "stop_after_first_success": _boolean("是否首个 HCS start 成功后停止；默认 true"),
            "enable_experimental_hcs": _boolean("真实执行 HCS boot 尝试的显式门禁；dry_run=false 时必须 true"),
            "prepare_if_missing": _boolean("缺少 verifier 文件时是否自动 prepare；默认 true"),
            "dry_run": _boolean("默认 true，只返回计划，不启动 VM"),
        },
        [],
    ),
    (
        "metis_vm_bundle_status",
        "检测 Metis VM Runtime Pack / Claude-style VM bundle 形态：rootfs.vhdx、vmlinuz、initrd、HCS/Hyper-V 主机能力、镜像体积和缺失项。只读诊断；不会启动 VM，也不会使用第三方 VM 资产。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "bundle_path": _string("可选 VM bundle 目录，例如 E:/ClaudeCode/cache/vm_bundles/claudevm.bundle；用于诊断参考，不代表可运行"),
        },
        [],
    ),
    (
        "metis_vm_pack_scaffold",
        "创建 Metis 自有 VM Runtime Pack 蓝图目录：metis-vm-pack.json、guest protocol、metisd stub、sandbox-helper 设计、host runner 设计。不会复制 Claude 资产，也不会启动 VM；用于后续接入 Metis 自己的 rootfs/kernel/initrd/guest agent。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "output_path": _string("可选输出目录；默认 .metis/runtime-pack/metisvm.bundle。必须在工作区内，除非权限允许工作区外路径"),
            "force": _boolean("目标目录非空时是否覆盖 scaffold 文件；默认 false"),
        },
        [],
    ),
    (
        "metis_vm_pack_adopt_reference",
        "从 Claude-style reference VM bundle 生成 Metis VM Pack 适配计划。默认只写 metis-vm-pack.json 与 reference-adoption-plan.json，记录 rootfs.vhdx/vmlinuz/initrd/sessiondata/smol-bin、.origin 指纹、体积和缺失项；不会复制资产。copy_assets=true 时才复制参考资产，仍会标记为 reference-only，避免误判为 Metis 自有资产。",
        {
            "reference_bundle_path": _string("参考 bundle 目录，例如 E:/ClaudeCode/cache/vm_bundles/claudevm.bundle"),
            "root": _string("可选工作区根目录，默认当前工作区"),
            "output_path": _string("Metis bundle 输出目录，默认 .metis/runtime-pack/metisvm.bundle"),
            "copy_assets": _boolean("是否复制参考 bundle 中的 vhdx/kernel/initrd 等大文件；默认 false"),
            "hash_assets": _boolean("是否计算参考资产 SHA256；大文件会很慢，默认 false"),
            "force": _boolean("目标文件存在时是否覆盖 scaffold/资产；默认 false"),
        },
        ["reference_bundle_path"],
    ),
    (
        "metis_wsl_runtime_status",
        "检测 Metis 托管 WSL import runtime：WSL import 能力、MetisRuntime distro 是否已安装、默认安装目录、rootfs.tar/rootfs.vhdx 候选资产。只读诊断，不会导入或启动 distro。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "distro_name": _string("可选 Metis 托管 distro 名，默认 MetisRuntime"),
            "install_dir": _string("可选安装目录，默认 %LOCALAPPDATA%/Metis/runtime/wsl/<distro>"),
            "rootfs_path": _string("可选 rootfs.tar/rootfs.tar.gz/rootfs.tar.zst/rootfs.vhdx 路径"),
        },
        [],
    ),
    (
        "metis_wsl_runtime_import",
        "把 Metis 自有 rootfs.tar/rootfs.vhdx 导入为托管 WSL distro。默认 dry_run=true 只返回计划命令；dry_run=false 才执行 wsl --import。不会使用 Claude 镜像。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "rootfs_path": _string("可选 rootfs.tar/rootfs.tar.gz/rootfs.tar.zst/rootfs.vhdx 路径；为空则扫描 Metis VM Pack"),
            "distro_name": _string("可选 Metis 托管 distro 名，默认 MetisRuntime"),
            "install_dir": _string("可选安装目录，默认 %LOCALAPPDATA%/Metis/runtime/wsl/<distro>"),
            "version": _integer("WSL 版本，默认 2"),
            "dry_run": _boolean("默认 true，只返回导入计划；false 才执行导入"),
            "allow_existing": _boolean("distro 已存在时是否继续生成/执行导入计划；默认 false"),
        },
        [],
    ),
    (
        "metis_sandbox_status",
        "检测 Metis Sandbox Runtime 可用性：VM Runtime Pack、WSL2、可用 distro、Docker daemon、Docker 镜像，以及当前会选择的默认 backend。只读诊断工具。",
        {
            "root": _string("可选工作区根目录，默认当前工作区"),
            "docker_image": _string("可选 Docker 镜像名，默认 METIS_DOCKER_RUNTIME_IMAGE 或 python:3.12-slim"),
            "wsl_distro": _string("可选指定 WSL distro；为空使用第一个可用 distro"),
            "vm_bundle_path": _string("可选 VM bundle 目录；可运行的 Metis VM Pack 会通过 guest protocol 执行命令"),
            "metis_wsl_distro": _string("可选 Metis 托管 WSL distro 名，默认 MetisRuntime"),
        },
        [],
    ),
    (
        "metis_runtime_create",
        "创建一个 Metis Runtime session。backend=auto 时优先 Metis 托管 WSL runtime、可运行 VM Pack、用户 WSL2、Docker，regular 模式最后回退 local-copy；strict_sandbox=true 时没有强沙箱就失败，不回退本机。源项目快照复制到 .metis/runtime/<session>/workspace，在隔离副本/沙箱里跑代码、生成图表和产物，避免直接污染用户项目。",
        {
            "task": _string("本次运行时任务说明，便于诊断和活动展示"),
            "root": _string("源项目根目录，默认当前工作区；跨盘/工作区外路径需要权限"),
            "mode": _string("copy 或 mount；默认 copy。mount 会直接在源项目运行，必须显式 allow_project_write"),
            "backend": _string("local | auto | vm | metis_wsl | wsl | docker。默认 local；auto 会优先 Metis 托管 WSL runtime，再选择可运行 VM Pack、用户 WSL2、Docker、local"),
            "docker_image": _string("Docker backend 使用的镜像，默认 METIS_DOCKER_RUNTIME_IMAGE 或 python:3.12-slim"),
            "wsl_distro": _string("WSL backend 指定 distro；为空使用第一个可用 distro"),
            "metis_wsl_distro": _string("Metis 托管 WSL runtime distro 名，默认 MetisRuntime"),
            "metis_wsl_install_dir": _string("Metis 托管 WSL runtime 安装目录，默认 %LOCALAPPDATA%/Metis/runtime/wsl/<distro>"),
            "vm_bundle_path": _string("VM Runtime Pack bundle 目录；可运行时 backend=vm 会通过 guest JSONL protocol 执行并收集 artifacts"),
            "include_patterns": _array_of_string("可选 glob 白名单，如 ['src/**','tests/**']"),
            "exclude_patterns": _array_of_string("可选 glob 黑名单"),
            "max_files": _integer("最多复制文件数，默认 2000"),
            "max_bytes": _integer("最多复制字节数，默认 80MB"),
            "allow_network": _boolean("是否允许运行时命令联网；默认 false，必须有明确授权才设 true"),
            "allow_cross_drive": _boolean("是否允许源根目录跨盘；默认 false"),
            "allow_project_write": _boolean("是否允许 mount 模式直接写源项目；默认 false"),
            "allow_desktop_write": _boolean("是否允许写桌面等非项目位置；v1 默认 false"),
            "strict_sandbox": _boolean("是否禁止 local-copy fallback；true 时必须使用 MetisRuntime/WSL/Docker/VM 强沙箱，否则创建失败"),
        },
        [],
    ),
    (
        "metis_runtime_run",
        "在 metis_runtime_create 创建的隔离工作区中后台执行命令。会注入 METIS_RUNTIME_ARTIFACTS_DIR 供脚本保存图表/报告/结果；失败时自动生成诊断包。默认阻止明显联网命令。",
        {
            "session_id": _string("运行时 session id"),
            "command": _string("要执行的命令，例如 python script.py、pytest -q、npm test"),
            "cwd": _string("相对隔离 workspace 的工作目录；禁止跳出运行时 workspace"),
            "timeout": _integer("超时秒数，默认 120"),
            "allow_network": _boolean("本次命令是否允许联网；默认 false，必须有明确授权才设 true"),
            "env": {"type": "object", "description": "附加环境变量，键和值都会转成字符串"},
        },
        ["session_id", "command"],
    ),
    (
        "metis_runtime_collect_artifacts",
        "从隔离 workspace 收集生成物到 .metis/artifacts/<session>/collected，适合把脚本输出的图片、PDF、DOCX、CSV、日志归档给用户查看。",
        {
            "session_id": _string("运行时 session id"),
            "patterns": _array_of_string("可选 glob 列表，默认收集常见图片、文档、表格和文本产物"),
            "max_files": _integer("最多收集文件数，默认 200"),
            "max_bytes_per_file": _integer("单文件最大字节数，默认 20MB"),
        },
        ["session_id"],
    ),
    (
        "metis_runtime_export_patch",
        "把隔离 workspace 中相对源项目的代码/文本改动导出为 patch 文件，默认写入 .metis/artifacts/<session>/<session>.patch。用于完成后把改动以 diff/patch 形式交回主项目，而不是直接写源项目。",
        {
            "session_id": _string("运行时 session id"),
            "output_path": _string("可选 patch 输出路径；为空则写入 session artifacts 目录"),
        },
        ["session_id"],
    ),
    (
        "metis_runtime_export_diagnostics",
        "导出运行时诊断包 zip：manifest、命令日志、stdout/stderr、artifact 列表和 patch 摘要。用于失败复盘或给用户/开发者定位问题。",
        {
            "session_id": _string("运行时 session id"),
        },
        ["session_id"],
    ),
    (
        "metis_runtime_status",
        "查看一个运行时 session 的状态，或列出当前工作区最近的运行时 session。",
        {
            "session_id": _string("可选运行时 session id；为空则列出当前 root 下最近 session"),
            "root": _string("列出 session 时使用的工作区根，默认当前工作区"),
        },
        [],
    ),
    (
        "metis_runtime_job",
        "Claude-style Runtime Job：一键创建隔离运行时、执行命令、收集 artifacts、导出 patch/diagnostics，并返回 verifier 证据链。用于代码运行、测试、构建、图表/报告生成；用户不需要显式说“沙箱”，这类任务默认应优先使用它而不是直接接管桌面。",
        {
            "task": _string("任务说明，例如 运行实验报告脚本并生成图表"),
            "command": _string("在隔离 workspace 中执行的 shell 命令，例如 python report.py 或 npm test"),
            "root": _string("源项目根目录，默认当前工作区"),
            "cwd": _string("相对隔离 workspace 的工作目录；禁止跳出运行时 workspace"),
            "backend": _string("auto | metis_wsl | wsl | docker | local；默认 auto，会优先 MetisRuntime"),
            "mode": _string("copy 或 mount；默认 copy，避免污染源项目"),
            "timeout": _integer("超时秒数，默认 120"),
            "allow_network": _boolean("是否允许联网；默认 false，必须用户明确授权才设 true"),
            "collect_artifacts": _boolean("是否额外扫描隔离 workspace 收集产物，默认 false；优先让脚本写入 METIS_RUNTIME_ARTIFACTS_DIR"),
            "artifact_patterns": _array_of_string("可选 artifact glob，如 ['*.png','*.md','*.docx']"),
            "export_patch": _boolean("是否导出隔离 workspace 中的改动 patch，默认 true"),
            "export_diagnostics": _string("always | on_failure | never；默认 on_failure"),
            "require_artifacts": _boolean("是否要求至少产生一个非空 artifact 才算验收通过"),
            "expected_stdout_contains": _string("可选 stdout 期望文本"),
            "strict_sandbox": _boolean("是否禁止 local-copy fallback；true 时必须使用 MetisRuntime/WSL/Docker/VM 强沙箱，否则 job 失败"),
            "max_files": _integer("最多复制文件数，默认 2000"),
            "max_bytes": _integer("最多复制字节数，默认 80MB"),
        },
        ["task", "command"],
    ),
    (
        "metis_runtime_job_status",
        "查看 Claude-style Runtime Job 的结果；为空时列出当前工作区最近 job。用于桌面端展示后台运行、artifacts、diagnostics 和 verifier 状态。",
        {
            "job_id": _string("可选 job id；为空则列出最近 runtime jobs"),
            "root": _string("工作区根，默认当前工作区"),
        },
        [],
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
            "region": _string("可选 ddgs 区域，如 us-en / cn-zh"),
            "timelimit": _string("可选时间过滤，如 d/w/m/y"),
        },
        ["query"],
    ),
    (
        "web_research",
        """【WebResearch】免费深度网页研究：先用 ddgs 搜索，再读取少量 HTTPS 证据页并返回可引用证据。
适合“查清楚/对比多个来源/需要证据链”的问题；普通实时事实先用 web_search，已知 URL 先用 web_fetch。不会处理登录、点击或 JS 动态页面，遇到这类页面再升级 browse_web。""",
        {
            "question": _string("研究问题，应具体到实体、年份、版本或判断标准"),
            "max_results": _integer("搜索结果上限，默认 5，最大 10"),
            "max_pages": _integer("读取证据页数量，默认 3，最大 5"),
            "max_chars_per_page": _integer("每个证据页返回字符数，默认 3000"),
            "reason": _string("可选；从普通搜索自升级到深度研究时写明简短原因，供本地诊断审计"),
        },
        ["question"],
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
