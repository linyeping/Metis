"""图像生成：密钥与图像 API 地址仅来自 ``web/config.py``。"""
import sys
from pathlib import Path

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


def _resolve_openai_key_and_url() -> tuple[str, str]:
    try:
        root = str(Path(__file__).resolve().parents[4])
        if root not in sys.path:
            sys.path.insert(0, root)
        from backend.web.config import get_openai_image_api_url, resolve_openai_api_key

        return resolve_openai_api_key(), get_openai_image_api_url()
    except Exception:
        return "", ""


@trace_execution
def generate_image(prompt: str, size: str = "1024x1024") -> str:
    key, img_url = _resolve_openai_key_and_url()
    if not key:
        return (
            "⚠️ 未配置图像生成密钥。请在 ``mine/miro/web/config.py`` 填写 ``OPENAI_API_KEY``（或与视觉共用的兼容密钥）。"
            "提示词已记录：\n" + prompt[:500]
        )
    try:
        import requests
        try:
            from backend.web.config import OPENAI_IMAGE_MODEL
        except Exception:
            OPENAI_IMAGE_MODEL = "dall-e-2"
        r = requests.post(
            img_url,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": OPENAI_IMAGE_MODEL,
                "prompt": prompt[:900],
                "n": 1,
                "size": size,
            },
            timeout=120,
        )
        if r.status_code != 200:
            return f"❌ API 错误 {r.status_code}: {r.text[:400]}"
        data = r.json()
        u = data["data"][0].get("url") or data["data"][0].get("b64_json", "")[:80]
        return f"✅ 已请求生成图像: {u}"
    except ImportError:
        return "❌ 需要 requests"
    except Exception as e:
        return f"❌ generate_image 失败: {str(e)}"
