"""上下文管理（文档 04）。"""
import ast
import hashlib
import os

from .log_config import logger


class ContextManager:
    """
    智能上下文管理 - 对应文档 04: 状态管理与上下文
    """

    def __init__(self, workspace: str = "."):
        self.workspace = workspace
        self.file_cache = {}
        self.context_window = 8000

    def get_file_hash(self, file_path: str) -> str:
        """计算文件哈希，用于增量更新检测"""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()
        except:
            return ""

    def generate_repo_map(self, max_depth: int = 3) -> str:
        """
        生成智能代码地图
        对应文档 04 的上下文构建
        """
        logger.info(f"🗺️ 生成代码地图 (深度={max_depth})")
        repo_map = f"=== 📦 项目结构地图 ({os.path.abspath(self.workspace)}) ===\n"

        for root, dirs, files in os.walk(self.workspace):
            depth = root.replace(self.workspace, '').count(os.sep)
            if depth > max_depth:
                continue

            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                      ['__pycache__', 'node_modules', 'venv', 'env', 'dist', 'build', '.git']]

            indent = "  " * depth
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.workspace)

                if file.endswith('.py'):
                    repo_map += f"{indent}🐍 {rel_path}:\n"
                    repo_map += self._extract_python_structure(file_path, depth + 1)
                elif file.endswith(('.js', '.ts', '.jsx', '.tsx')):
                    repo_map += f"{indent}📜 {rel_path} (JS/TS)\n"
                elif file.endswith(('.json', '.yaml', '.yml')):
                    repo_map += f"{indent}⚙️ {rel_path} (配置)\n"
                else:
                    repo_map += f"{indent}📄 {rel_path}\n"

        return repo_map + "\n" + "="*50 + "\n"

    def _extract_python_structure(self, file_path: str, depth: int) -> str:
        """提取 Python 文件结构（AST 解析）"""
        indent = "  " * depth
        structure = ""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())

            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    structure += f"{indent}class {node.name}:\n"
                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef):
                            args = ', '.join([a.arg for a in sub.args.args])
                            structure += f"{indent}  def {sub.name}({args})\n"
                elif isinstance(node, ast.FunctionDef):
                    args = ', '.join([a.arg for a in node.args.args])
                    structure += f"{indent}def {node.name}({args})\n"
        except Exception as e:
            structure += f"{indent}(解析失败: {str(e)[:50]})\n"

        return structure


context_manager = ContextManager()
