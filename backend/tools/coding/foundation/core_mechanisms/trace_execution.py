"""执行追踪装饰器（文档 13）。"""
import time
from functools import wraps
from typing import Callable

from .log_config import logger


def trace_execution(func: Callable) -> Callable:
    """
    执行追踪装饰器 - 记录每个工具调用的详细信息
    对应文档 13: 执行日志与内部追踪
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        tool_name = func.__name__

        logger.info(f"[TOOL_CALL] {tool_name}")
        logger.info(f"  参数: args={args[:2] if len(args) > 2 else args}, kwargs={kwargs}")

        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            logger.info(f"[TOOL_RESULT] {tool_name} 成功 (耗时 {duration:.3f}s)")
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"[TOOL_ERROR] {tool_name} 失败 (耗时 {duration:.3f}s): {str(e)}")
            raise

    return wrapper
