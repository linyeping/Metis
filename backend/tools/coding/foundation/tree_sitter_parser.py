"""tree-sitter AST 解析适配层 —— 从源文件提取顶级签名（class/function/method/interface/type）。

对不支持的语言或解析失败时返回空列表，绝不抛异常到调用方。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 延迟加载 tree-sitter（可能未安装）
# ---------------------------------------------------------------------------
_TREE_SITTER_AVAILABLE: Optional[bool] = None


def _check_tree_sitter() -> bool:
    global _TREE_SITTER_AVAILABLE
    if _TREE_SITTER_AVAILABLE is None:
        try:
            import tree_sitter  # noqa: F401
            _TREE_SITTER_AVAILABLE = True
        except ImportError:
            _TREE_SITTER_AVAILABLE = False
            logger.info("tree-sitter not installed; repo map will use fallback AST parser")
    return _TREE_SITTER_AVAILABLE


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class SignatureNode:
    """一个顶级签名节点。"""

    kind: str  # "class", "function", "method", "interface", "type"
    name: str
    signature: str  # 例如 "def foo(self, x: int) -> str"
    children: List[SignatureNode] = field(default_factory=list)
    line: int = 0


# ---------------------------------------------------------------------------
# 语言注册表
# ---------------------------------------------------------------------------
_EXT_TO_LANG: Dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}

_LANGUAGE_CACHE: Dict[str, object] = {}


def _get_language(lang_name: str):
    """按名称获取 tree-sitter Language 对象（带缓存）。"""
    if lang_name in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[lang_name]

    from tree_sitter import Language  # noqa: E402

    lang_obj = None
    try:
        if lang_name == "python":
            import tree_sitter_python as tsp
            lang_obj = Language(tsp.language())
        elif lang_name == "javascript":
            import tree_sitter_javascript as tsjs
            lang_obj = Language(tsjs.language())
        elif lang_name == "typescript":
            import tree_sitter_typescript as tsts
            lang_obj = Language(tsts.language_typescript())
        elif lang_name == "tsx":
            import tree_sitter_typescript as tsts
            lang_obj = Language(tsts.language_tsx())
    except Exception as exc:
        logger.debug("Failed to load tree-sitter language %s: %s", lang_name, exc)
        return None

    if lang_obj is not None:
        _LANGUAGE_CACHE[lang_name] = lang_obj
    return lang_obj


# ---------------------------------------------------------------------------
# 解析单文件
# ---------------------------------------------------------------------------
def _node_text(node, source_bytes: bytes) -> str:
    """从 AST 节点提取源文本。"""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _extract_python_signatures(root, source_bytes: bytes) -> List[SignatureNode]:
    """Python：提取顶级 class/function 及方法签名。"""
    results: List[SignatureNode] = []
    for child in root.children:
        if child.type == "class_definition":
            cls_name = ""
            bases = ""
            for sub in child.children:
                if sub.type == "name":
                    cls_name = _node_text(sub, source_bytes)
                elif sub.type == "identifier":
                    cls_name = _node_text(sub, source_bytes)
                elif sub.type == "argument_list":
                    bases = _node_text(sub, source_bytes)

            cls_sig = f"class {cls_name}{bases}" if bases else f"class {cls_name}"
            cls_node = SignatureNode(
                kind="class", name=cls_name, signature=cls_sig, line=child.start_point[0] + 1
            )

            # 提取方法
            body = None
            for sub in child.children:
                if sub.type == "block":
                    body = sub
                    break
            if body:
                for stmt in body.children:
                    if stmt.type == "function_definition":
                        fn_name, fn_sig = _python_func_sig(stmt, source_bytes)
                        cls_node.children.append(
                            SignatureNode(
                                kind="method",
                                name=fn_name,
                                signature=fn_sig,
                                line=stmt.start_point[0] + 1,
                            )
                        )
            results.append(cls_node)

        elif child.type == "function_definition":
            fn_name, fn_sig = _python_func_sig(child, source_bytes)
            results.append(
                SignatureNode(
                    kind="function",
                    name=fn_name,
                    signature=fn_sig,
                    line=child.start_point[0] + 1,
                )
            )
        elif child.type == "decorated_definition":
            # unwrap decorator to get actual def/class
            for sub in child.children:
                if sub.type == "function_definition":
                    fn_name, fn_sig = _python_func_sig(sub, source_bytes)
                    results.append(
                        SignatureNode(
                            kind="function",
                            name=fn_name,
                            signature=fn_sig,
                            line=sub.start_point[0] + 1,
                        )
                    )
                elif sub.type == "class_definition":
                    # Recurse — treat decorated class same as top-level
                    inner = _extract_python_signatures_node(sub, source_bytes)
                    results.extend(inner)
    return results


def _extract_python_signatures_node(class_node, source_bytes: bytes) -> List[SignatureNode]:
    """Extract from a single class_definition node (for decorated classes)."""
    cls_name = ""
    bases = ""
    for sub in class_node.children:
        if sub.type in ("name", "identifier"):
            cls_name = _node_text(sub, source_bytes)
        elif sub.type == "argument_list":
            bases = _node_text(sub, source_bytes)

    cls_sig = f"class {cls_name}{bases}" if bases else f"class {cls_name}"
    cls_node_obj = SignatureNode(
        kind="class", name=cls_name, signature=cls_sig, line=class_node.start_point[0] + 1
    )

    body = None
    for sub in class_node.children:
        if sub.type == "block":
            body = sub
            break
    if body:
        for stmt in body.children:
            if stmt.type == "function_definition":
                fn_name, fn_sig = _python_func_sig(stmt, source_bytes)
                cls_node_obj.children.append(
                    SignatureNode(
                        kind="method", name=fn_name, signature=fn_sig, line=stmt.start_point[0] + 1
                    )
                )
    return [cls_node_obj]


def _python_func_sig(node, source_bytes: bytes) -> Tuple[str, str]:
    """提取 Python 函数签名：def name(params) -> ret。"""
    name = ""
    params = ""
    ret = ""
    for child in node.children:
        if child.type in ("name", "identifier"):
            name = _node_text(child, source_bytes)
        elif child.type == "parameters":
            params = _node_text(child, source_bytes)
        elif child.type == "type":
            ret = _node_text(child, source_bytes)
    sig = f"def {name}{params}"
    if ret:
        sig += f" -> {ret}"
    return name, sig


def _extract_js_ts_signatures(root, source_bytes: bytes, lang: str) -> List[SignatureNode]:
    """JavaScript / TypeScript：提取 class/function/interface/type。"""
    results: List[SignatureNode] = []
    for child in root.children:
        _extract_js_ts_node(child, source_bytes, results, lang, top_level=True)
    return results


def _extract_js_ts_node(
    node, source_bytes: bytes, results: List[SignatureNode], lang: str, top_level: bool = False
):
    """递归提取 JS/TS 节点签名。"""
    t = node.type

    # export default / export ...
    if t in ("export_statement", "export_default_declaration"):
        for child in node.children:
            _extract_js_ts_node(child, source_bytes, results, lang, top_level=True)
        return

    if t == "class_declaration":
        cls_name = ""
        for sub in node.children:
            if sub.type in ("type_identifier", "identifier"):
                cls_name = _node_text(sub, source_bytes)
                break
        cls_obj = SignatureNode(
            kind="class", name=cls_name, signature=f"class {cls_name}",
            line=node.start_point[0] + 1,
        )
        # Extract methods from class body
        for sub in node.children:
            if sub.type == "class_body":
                for member in sub.children:
                    if member.type in ("method_definition", "public_field_definition"):
                        m_name = ""
                        for c in member.children:
                            if c.type in ("property_identifier", "identifier"):
                                m_name = _node_text(c, source_bytes)
                                break
                        if m_name:
                            cls_obj.children.append(
                                SignatureNode(
                                    kind="method", name=m_name, signature=m_name + "()",
                                    line=member.start_point[0] + 1,
                                )
                            )
        results.append(cls_obj)

    elif t in ("function_declaration", "generator_function_declaration"):
        fn_name = ""
        for sub in node.children:
            if sub.type == "identifier":
                fn_name = _node_text(sub, source_bytes)
                break
        if fn_name:
            results.append(
                SignatureNode(
                    kind="function", name=fn_name, signature=f"function {fn_name}()",
                    line=node.start_point[0] + 1,
                )
            )

    elif t == "lexical_declaration" and top_level:
        # const Foo = () => {} or const Foo = function() {}
        for sub in node.children:
            if sub.type == "variable_declarator":
                var_name = ""
                has_func = False
                for c in sub.children:
                    if c.type == "identifier":
                        var_name = _node_text(c, source_bytes)
                    elif c.type in ("arrow_function", "function_expression", "function"):
                        has_func = True
                if var_name and has_func:
                    results.append(
                        SignatureNode(
                            kind="function", name=var_name, signature=f"const {var_name} = ()",
                            line=node.start_point[0] + 1,
                        )
                    )

    elif t == "interface_declaration" and lang in ("typescript", "tsx"):
        iface_name = ""
        for sub in node.children:
            if sub.type == "type_identifier":
                iface_name = _node_text(sub, source_bytes)
                break
        if iface_name:
            results.append(
                SignatureNode(
                    kind="interface", name=iface_name, signature=f"interface {iface_name}",
                    line=node.start_point[0] + 1,
                )
            )

    elif t == "type_alias_declaration" and lang in ("typescript", "tsx"):
        type_name = ""
        for sub in node.children:
            if sub.type == "type_identifier":
                type_name = _node_text(sub, source_bytes)
                break
        if type_name:
            results.append(
                SignatureNode(
                    kind="type", name=type_name, signature=f"type {type_name}",
                    line=node.start_point[0] + 1,
                )
            )


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def detect_language(file_path: str) -> Optional[str]:
    """通过扩展名检测语言，不支持则返回 None。"""
    _, ext = os.path.splitext(file_path)
    return _EXT_TO_LANG.get(ext.lower())


def parse_file_signatures(file_path: str, language: Optional[str] = None) -> List[SignatureNode]:
    """解析单个文件，返回顶级签名节点列表。

    - tree-sitter 不可用时返回空列表
    - 解析失败时返回空列表（不抛异常）
    """
    if not _check_tree_sitter():
        return []

    if language is None:
        language = detect_language(file_path)
    if language is None:
        return []

    lang_obj = _get_language(language)
    if lang_obj is None:
        return []

    try:
        with open(file_path, "rb") as f:
            source_bytes = f.read()
    except (OSError, IOError):
        return []

    # 跳过超大文件（> 512KB）
    if len(source_bytes) > 512 * 1024:
        return []

    try:
        from tree_sitter import Parser
        parser = Parser(lang_obj)
        tree = parser.parse(source_bytes)
    except Exception as exc:
        logger.debug("tree-sitter parse failed for %s: %s", file_path, exc)
        return []

    root = tree.root_node
    if language == "python":
        return _extract_python_signatures(root, source_bytes)
    elif language in ("javascript", "typescript", "tsx"):
        return _extract_js_ts_signatures(root, source_bytes, language)
    return []
