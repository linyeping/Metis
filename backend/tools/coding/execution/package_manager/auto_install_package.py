"""静默安装 Python 依赖（与 legacy install_package 一致）。"""
import subprocess
import sys

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def auto_install_package(package_name: str) -> str:
    """安装 Python 包：优先 core.engine.AutonomyEngine；否则 pip install。

    使用 ``sys.executable -m pip`` 确保安装到当前 Python 环境。
    """
    try:
        try:
            from backend.core.engine.autonomy_engine import AutonomyEngine
            success = AutonomyEngine.auto_install_package(package_name)
        except ImportError:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", package_name],
                capture_output=True,
                text=True,
                timeout=120,
            )
            success = result.returncode == 0

        if success:
            return f"✅ 包安装成功: {package_name}"
        return f"❌ 包安装失败: {package_name}"
    except Exception as e:
        return f"❌ 安装过程异常: {str(e)}"
