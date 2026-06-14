"""思考过程解析（多层容错）。"""
import re
from typing import Tuple

from backend.tools.coding.foundation.core_mechanisms.colored_logger import colored_log


class ThoughtProcessParser:
    """思考过程解析（4 层容错）"""

    @staticmethod
    def extract_thoughts(content: str) -> Tuple[str, str]:
        if not content:
            return "", content

        try:
            match = re.search(
                r"<thought_process>(.*?)</thought_process>",
                content,
                re.DOTALL | re.IGNORECASE,
            )
            if match:
                thought = match.group(1).strip()
                actual = re.sub(
                    r"<thought_process>.*?</thought_process>",
                    "",
                    content,
                    flags=re.DOTALL | re.IGNORECASE,
                ).strip()
                return thought, actual

            match = re.search(r"<thought_process>(.*)", content, re.DOTALL | re.IGNORECASE)
            if match:
                colored_log.fallback("思考解析", "未闭合标签")
                return match.group(1).strip(), ""

            for pattern in [r"<thought>(.*?)</thought>", r"<thinking>(.*?)</thinking>"]:
                match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
                if match:
                    colored_log.fallback("思考解析", "标签变体")
                    thought = match.group(1).strip()
                    actual = re.sub(pattern, "", content, flags=re.DOTALL | re.IGNORECASE).strip()
                    return thought, actual

            colored_log.fallback("思考解析", "未检测到标签")
            return "", content

        except Exception as e:
            colored_log.error(f"思考解析异常: {e}")
            return "", content
