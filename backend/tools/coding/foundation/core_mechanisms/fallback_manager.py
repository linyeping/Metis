"""多层降级策略管理器（文档 03）。AST 编辑见 `Tools.modify_refactor.modify_ast.edit_code_ast`。"""
import ast
import difflib
import os
import re
from typing import Any, Dict, Optional

from .enums import FallbackStrategy
from .log_config import logger


class FallbackManager:
    """
    多层降级策略管理器 - 对应文档 03: 异常处理与降级策略
    核心机制：永不放弃，总能找到解决方案
    """

    def __init__(self):
        self.execution_log = []

    def execute_with_fallback(self,
                            file_path: str,
                            old_str: str,
                            new_str: str) -> Dict[str, Any]:
        """
        文件编辑的完整降级链
        策略链: 精确匹配 → 模糊匹配 → AST 编辑 → 完全重写
        """
        strategies = [
            (FallbackStrategy.EXACT_MATCH, self._strategy_exact_match),
            (FallbackStrategy.FUZZY_MATCH, self._strategy_fuzzy_match),
            (FallbackStrategy.AST_EDIT, self._strategy_ast_edit),
        ]

        for i, (strategy_type, strategy_func) in enumerate(strategies):
            logger.info(f"[FALLBACK] 尝试策略 {i+1}/{len(strategies)}: {strategy_type.value}")

            try:
                result = strategy_func(file_path, old_str, new_str)
                if result["success"]:
                    logger.info(f"[FALLBACK] ✅ 策略 {strategy_type.value} 成功")
                    result["strategy_used"] = strategy_type.value
                    result["attempts"] = i + 1
                    return result
                else:
                    logger.warning(f"[FALLBACK] ❌ 策略 {strategy_type.value} 失败: {result.get('error')}")
            except Exception as e:
                logger.error(f"[FALLBACK] ❌ 策略 {strategy_type.value} 异常: {str(e)}")
                continue

        # 所有策略失败
        return {
            "success": False,
            "error": "所有降级策略均失败",
            "attempts": len(strategies),
            "suggestion": "请检查文件内容或手动编辑"
        }

    def _strategy_exact_match(self, file_path: str, old_str: str, new_str: str) -> Dict[str, Any]:
        """策略 1: 精确匹配"""
        if not os.path.exists(file_path):
            return {"success": False, "error": f"文件不存在: {file_path}"}

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if old_str not in content:
            return {"success": False, "error": "未找到精确匹配"}

        count = content.count(old_str)
        if count > 1:
            return {"success": False, "error": f"找到 {count} 个匹配，不唯一"}

        new_content = content.replace(old_str, new_str, 1)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return {
            "success": True,
            "method": "exact_match",
            "message": "精确匹配替换成功"
        }

    def _strategy_fuzzy_match(self, file_path: str, old_str: str, new_str: str,
                             threshold: float = 0.80) -> Dict[str, Any]:
        """策略 2: 模糊匹配（Aider 算法）"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        lines_content = content.splitlines()
        lines_search = old_str.splitlines()

        if not lines_search:
            return {"success": False, "error": "搜索内容为空"}

        best_ratio = 0
        best_idx = -1
        window_size = len(lines_search)

        # 滑动窗口查找最佳匹配
        for i in range(len(lines_content) - window_size + 1):
            window = "\n".join(lines_content[i:i+window_size])
            ratio = difflib.SequenceMatcher(None, old_str, window).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i

        if best_ratio < threshold:
            return {
                "success": False,
                "error": f"最高相似度 {best_ratio*100:.1f}% 低于阈值 {threshold*100}%",
                "best_ratio": best_ratio
            }

        # 执行替换
        prefix = "\n".join(lines_content[:best_idx])
        suffix = "\n".join(lines_content[best_idx+window_size:])
        new_content = f"{prefix}\n{new_str}\n{suffix}".strip('\n')

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return {
            "success": True,
            "method": "fuzzy_match",
            "similarity": best_ratio,
            "message": f"模糊匹配成功 (相似度 {best_ratio*100:.1f}%)"
        }

    def _strategy_ast_edit(self, file_path: str, old_str: str, new_str: str) -> Dict[str, Any]:
        """策略 3: AST 编辑（智能推断节点名称）"""
        if not file_path.endswith('.py'):
            return {"success": False, "error": "AST 编辑仅支持 Python 文件"}

        try:
            from backend.tools.coding.modify_refactor.modify_ast.edit_code_ast import ASTCodeEditor

            # 尝试从 old_str 中提取函数/类名
            selector = self._extract_selector_from_code(old_str)
            if not selector:
                return {"success": False, "error": "无法从代码中提取函数/类名"}

            editor = ASTCodeEditor(file_path)
            success = editor.replace_node(selector, new_str)

            if success:
                editor.save()
                return {
                    "success": True,
                    "method": "ast_edit",
                    "selector": selector,
                    "message": f"AST 编辑成功 (节点: {selector})"
                }
            else:
                return {"success": False, "error": f"未找到节点: {selector}"}

        except Exception as e:
            return {"success": False, "error": f"AST 编辑异常: {str(e)}"}

    def _extract_selector_from_code(self, code: str) -> Optional[str]:
        """从代码中提取函数/类名"""
        try:
            tree = ast.parse(code)
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    return node.name
                elif isinstance(node, ast.ClassDef):
                    return node.name
        except:
            pass

        # 正则提取
        match = re.search(r'def\s+(\w+)\s*\(', code)
        if match:
            return match.group(1)

        match = re.search(r'class\s+(\w+)\s*[:\(]', code)
        if match:
            return match.group(1)

        return None


fallback_manager = FallbackManager()
