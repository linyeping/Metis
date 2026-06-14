"""枚举类型定义（文档 03 / 07）。"""
from enum import Enum


class OperationType(Enum):
    """AST 操作类型（文档 07）"""
    REPLACE_NODE = "replace_node"
    INSERT_NODE = "insert_node"
    DELETE_NODE = "delete_node"


class FallbackStrategy(Enum):
    """降级策略类型（文档 03）"""
    EXACT_MATCH = "exact_match"
    FUZZY_MATCH = "fuzzy_match"
    AST_EDIT = "ast_edit"
    FULL_REWRITE = "full_rewrite"
