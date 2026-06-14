"""上帝视角彩色终端追踪器（自 app.py 迁入，依赖标准 logger）。"""
import json
from typing import Any, Dict

from .log_config import logger


class ColoredLogger:
    """上帝视角彩色终端追踪器"""
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

    @classmethod
    def thought(cls, message: str):
        print(f"{cls.GREEN}{cls.BOLD}[THOUGHT]{cls.RESET} {cls.GREEN}{message}{cls.RESET}")
        logger.info(f"[THOUGHT] {message}")

    @classmethod
    def tool_use(cls, tool_name: str, args: Dict[str, Any]):
        args_str = json.dumps(args, ensure_ascii=False)[:100]
        print(f"{cls.BLUE}{cls.BOLD}[TOOL USE]{cls.RESET} {cls.BLUE}{tool_name}({args_str}...){cls.RESET}")
        logger.info(f"[TOOL USE] {tool_name}")

    @classmethod
    def fallback(cls, strategy: str, reason: str):
        print(f"{cls.YELLOW}{cls.BOLD}[FALLBACK]{cls.RESET} {cls.YELLOW}{strategy} - {reason}{cls.RESET}")
        logger.warning(f"[FALLBACK] {strategy} - {reason}")

    @classmethod
    def error(cls, message: str):
        print(f"{cls.RED}{cls.BOLD}[ERROR]{cls.RESET} {cls.RED}{message}{cls.RESET}")
        logger.error(f"[ERROR] {message}")

    @classmethod
    def info(cls, message: str):
        print(f"{cls.CYAN}[INFO]{cls.RESET} {message}")
        logger.info(f"[INFO] {message}")

    @classmethod
    def intent(cls, intent_type: str, scores: Dict[str, int]):
        print(f"{cls.MAGENTA}{cls.BOLD}[INTENT]{cls.RESET} {cls.MAGENTA}{intent_type}{cls.RESET} (得分: {scores})")
        logger.info(f"[INTENT] {intent_type}")

    @classmethod
    def success(cls, message: str):
        print(f"{cls.GREEN}{cls.BOLD}[SUCCESS]{cls.RESET} {cls.GREEN}{message}{cls.RESET}")
        logger.info(f"[SUCCESS] {message}")

    @classmethod
    def self_x(cls, phase: str, message: str):
        """自我五项闭环专用日志"""
        print(f"{cls.CYAN}{cls.BOLD}[SELF-{phase.upper()}]{cls.RESET} {cls.CYAN}{message}{cls.RESET}")
        logger.info(f"[SELF-{phase.upper()}] {message}")


colored_log = ColoredLogger()
