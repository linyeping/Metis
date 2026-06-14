"""智能内容剪枝（文档 07）。"""
import re
from typing import List, Tuple

from .log_config import logger


class ContentPruner:
    """
    智能内容剪枝 - 对应文档 07: 关键技术细节
    用于大文件的 Token 优化
    """

    def __init__(self, max_tokens: int = 5000):
        self.max_tokens = max_tokens

    def prune(self, content: str, explanation: str = "") -> Tuple[str, bool]:
        """
        基于 explanation 剪枝内容
        返回: (剪枝后的内容, 是否进行了剪枝)
        """
        # 简单的 Token 估算（1 token ≈ 4 字符）
        estimated_tokens = len(content) / 4

        if estimated_tokens <= self.max_tokens:
            return content, False

        # 提取关键词
        keywords = self._extract_keywords(explanation)

        # 计算每行的相关性分数
        lines = content.split('\n')
        scored_lines = self._score_lines(lines, keywords)

        # 选择最相关的行
        selected_indices = self._select_top_lines(scored_lines, self.max_tokens)

        # 重建内容
        pruned_content = self._rebuild_content(lines, selected_indices)

        logger.info(f"[PRUNING] 剪枝: {len(lines)} 行 → {len(selected_indices)} 行")
        return pruned_content, True

    def _extract_keywords(self, explanation: str) -> List[str]:
        """提取关键词"""
        stop_words = {'the', 'a', 'an', 'to', 'for', 'of', 'in', 'on', '的', '了', '和'}
        words = re.findall(r'\w+', explanation.lower())
        return [w for w in words if w not in stop_words and len(w) > 2]

    def _score_lines(self, lines: List[str], keywords: List[str]) -> List[Tuple[int, float, str]]:
        """计算每行的相关性分数"""
        scored = []
        for i, line in enumerate(lines):
            score = 0
            line_lower = line.lower()

            # 关键词匹配
            for keyword in keywords:
                if keyword in line_lower:
                    score += 10

            # 代码结构重要性
            if re.match(r'^\s*(class|def|function|interface|type)\s', line):
                score += 5

            if re.match(r'^\s*(import|from|#include)', line):
                score += 3

            scored.append((i, score, line))

        return scored

    def _select_top_lines(self, scored_lines: List[Tuple[int, float, str]], max_tokens: int) -> List[int]:
        """选择得分最高的行"""
        sorted_lines = sorted(scored_lines, key=lambda x: x[1], reverse=True)

        selected_indices = []
        current_tokens = 0

        for idx, score, line in sorted_lines:
            line_tokens = len(line) / 4
            if current_tokens + line_tokens > max_tokens:
                break
            selected_indices.append(idx)
            current_tokens += line_tokens

        return sorted(selected_indices)

    def _rebuild_content(self, all_lines: List[str], selected_indices: List[int]) -> str:
        """重建内容，保持结构"""
        result_lines = []
        last_idx = -1

        for idx in selected_indices:
            if idx > last_idx + 1:
                gap = idx - last_idx - 1
                result_lines.append(f"    # ... (省略 {gap} 行)")

            result_lines.append(all_lines[idx])
            last_idx = idx

        return '\n'.join(result_lines)
