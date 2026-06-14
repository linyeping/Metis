"""全仓结构地图：用 tree-sitter 解析 AST 提取签名，生成紧凑 repo map。

优先使用 tree-sitter 方案（支持 Python/JS/TS 签名提取），
tree-sitter 不可用时回退到 ContextManager 的 ast 方案。
"""
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def generate_repo_map(workspace: str = ".", max_depth: int = 3) -> str:
    """
    生成智能代码地图，展示项目结构和源文件的类/函数签名。

    使用 tree-sitter 解析 AST（支持 .py/.js/.ts/.tsx/.jsx），
    提取顶级 class/function/method/interface/type 签名。
    结果按目录结构组织，带缓存。

    Args:
        workspace: 工作区根路径（相对或绝对）。
        max_depth: 目录遍历最大深度（兼容参数，tree-sitter 模式不受此限制）。
    """
    try:
        from backend.tools.coding.foundation.repo_map import generate_repo_map as _ts_generate
        return _ts_generate(workspace_root=workspace, max_tokens=4000)
    except Exception:
        # tree-sitter 不可用或出错，回退到旧方案
        from backend.tools.coding.foundation.core_mechanisms.context_manager import ContextManager
        return ContextManager(workspace=workspace).generate_repo_map(max_depth=max_depth)
