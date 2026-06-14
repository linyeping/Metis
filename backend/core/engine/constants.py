"""运行时配置（HTTP 服务与主循环共用）。

DeepSeek 密钥与 URL 仅自 ``web/config.py`` 注入（导入失败时密钥为空，请修复路径）。
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from backend.web.config import (
        DEEPSEEK_API_URL,
        DEEPSEEK_CHAT_MODEL,
        resolve_deepseek_api_key,
    )

    DEEPSEEK_API_KEY = resolve_deepseek_api_key()
    API_URL = DEEPSEEK_API_URL
except Exception:  # noqa: BLE001 — 允许在无 web 包路径时降级（不在此写具体 API 地址）
    DEEPSEEK_API_KEY = ""
    API_URL = ""
    DEEPSEEK_CHAT_MODEL = "deepseek-chat"

REQUEST_TIMEOUT = 600
MAX_LOOPS = 64
MAX_CONTEXT_TOKENS = 180000
CURRENT_WORKSPACE = "."
INTENT_ALIGNMENT_THRESHOLD = 0.9
