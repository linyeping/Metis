"""AST 级编辑：ASTCodeEditor + editCode（与 legacy 行为一致）。"""
import ast
import os
from typing import Optional

import astor

from backend.tools.coding.foundation.core_mechanisms.log_config import logger
from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_read
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


class ASTCodeEditor:
    """
    AST 级别的代码编辑器 - 对应文档 07: 关键技术细节
    解决字符串匹配因空格/缩进导致的失败问题
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        with open(file_path, 'r', encoding='utf-8') as f:
            self.source = f.read()
        try:
            self.tree = ast.parse(self.source)
        except SyntaxError as e:
            raise ValueError(f"AST 解析失败: {e}")

    def find_node(self, selector: str) -> Optional[ast.AST]:
        """
        查找 AST 节点
        selector 格式:
        - "functionName" - 模块级函数
        - "ClassName" - 类
        - "ClassName.methodName" - 类方法
        """
        if '.' in selector:
            class_name, method_name = selector.split('.', 1)
            for node in ast.walk(self.tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef) and sub.name == method_name:
                            return sub
        else:
            for node in self.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == selector:
                    return node

        return None

    def replace_node(self, selector: str, new_code: str) -> bool:
        """替换节点"""
        target_node = self.find_node(selector)
        if not target_node:
            logger.error(f"[AST] 未找到节点: {selector}")
            return False

        try:
            new_tree = ast.parse(new_code)
            new_node = new_tree.body[0]
        except SyntaxError as e:
            logger.error(f"[AST] 新代码语法错误: {e}")
            return False

        if '.' in selector:
            class_name, method_name = selector.split('.', 1)
            for node in self.tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for i, sub in enumerate(node.body):
                        if isinstance(sub, ast.FunctionDef) and sub.name == method_name:
                            node.body[i] = new_node
                            break
        else:
            for i, node in enumerate(self.tree.body):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == selector:
                    self.tree.body[i] = new_node
                    break

        return True

    def insert_node(self, selector: str, new_code: str) -> bool:
        """插入节点"""
        try:
            new_tree = ast.parse(new_code)
            new_node = new_tree.body[0]
        except SyntaxError as e:
            logger.error(f"[AST] 新代码语法错误: {e}")
            return False

        if selector == "start":
            insert_pos = 0
            for i, node in enumerate(self.tree.body):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    insert_pos = i
                    break
            self.tree.body.insert(insert_pos, new_node)
        elif '.' in selector:
            class_name = selector.split('.')[0]
            for node in self.tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    node.body.append(new_node)
                    break
        else:
            for i, node in enumerate(self.tree.body):
                if isinstance(node, ast.FunctionDef) and node.name == selector:
                    self.tree.body.insert(i + 1, new_node)
                    break

        return True

    def delete_node(self, selector: str) -> bool:
        """删除节点"""
        if '.' in selector:
            class_name, method_name = selector.split('.', 1)
            for node in self.tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    node.body = [
                        sub for sub in node.body
                        if not (isinstance(sub, ast.FunctionDef) and sub.name == method_name)
                    ]
                    return True
        else:
            self.tree.body = [
                node for node in self.tree.body
                if not (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == selector)
            ]
            return True

        return False

    def save(self) -> None:
        """保存修改后的代码"""
        try:
            new_source = astor.to_source(self.tree)
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(new_source)
            logger.info(f"[AST] 成功保存: {self.file_path}")
        except Exception as e:
            logger.error(f"[AST] 保存失败: {e}")
            raise


@trace_execution
def editCode(
    file_path: str,
    operation: str,
    selector: str,
    replacement: str = "",
) -> str:
    """
    AST 级别的代码编辑 - 对应文档 07: 关键技术细节
    """
    if not file_path.endswith('.py'):
        return "❌ editCode 仅支持 Python 文件"

    try:
        safe_fp = safe_path_for_read(file_path)
    except PathSecurityError as e:
        return str(e)
    file_path = str(safe_fp)

    if not os.path.exists(file_path):
        return f"❌ 文件不存在: {file_path}"

    try:
        editor = ASTCodeEditor(file_path)

        if operation == "replace_node":
            if not replacement:
                return "❌ replace_node 需要提供 replacement 参数"
            success = editor.replace_node(selector, replacement)
        elif operation == "insert_node":
            if not replacement:
                return "❌ insert_node 需要提供 replacement 参数"
            success = editor.insert_node(selector, replacement)
        elif operation == "delete_node":
            success = editor.delete_node(selector)
        else:
            return f"❌ 未知的操作类型: {operation}\n支持: replace_node, insert_node, delete_node"

        if success:
            editor.save()
            return f"✅ AST 编辑成功\n操作: {operation}\n节点: {selector}\n请立即验证！"
        else:
            return f"❌ AST 编辑失败: 未找到节点 '{selector}'"

    except ValueError as e:
        return f"❌ AST 解析失败: {str(e)}\n文件可能有语法错误"
    except Exception as e:
        return f"❌ AST 编辑异常: {str(e)}"
