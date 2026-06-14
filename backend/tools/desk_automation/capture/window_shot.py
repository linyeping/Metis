# -*- coding: utf-8 -*-
"""按窗口标题截图（Windows；需 pyautogui + mss + pillow）。"""

from __future__ import annotations

import subprocess
import sys

from ..policy import allow_capture_or_input


def _find_window_rect(title_substring: str) -> dict[str, int] | None:
    """PowerShell 查窗口位置（无 pywin32 依赖）。"""
    if sys.platform != "win32":
        return None
    ps = (
        f"Add-Type -AssemblyName System.Windows.Forms; "
        f"Get-Process | Where-Object {{$_.MainWindowTitle -like '*{title_substring}*'}} "
        f"| Select-Object -First 1 Id, MainWindowTitle "
        f"| ForEach-Object {{ "
        f'  $src = @"'
        f"\nusing System; using System.Runtime.InteropServices;"
        f"\npublic class WR {{ [DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr h, out RECT r); "
        f"[StructLayout(LayoutKind.Sequential)] public struct RECT {{ public int L,T,R,B; }} }}"
        f'\n"@;'
        f"  Add-Type -TypeDefinition $src -ErrorAction SilentlyContinue; "
        f"  $h = $_.MainWindowHandle; $r = New-Object WR+RECT; "
        f"  [void][WR]::GetWindowRect($h, [ref]$r); "
        f"  @{{left=$r.L; top=$r.T; width=$r.R-$r.L; height=$r.B-$r.T}} | ConvertTo-Json "
        f"}}"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        import json

        return json.loads(r.stdout)
    except Exception:
        return None


def grab_window_png(title_substring: str) -> bytes | None:
    allow_capture_or_input()
    rect = _find_window_rect(title_substring)
    if not rect:
        return None
    import io

    from backend.runtime.pip_helper import ensure_packages
    ensure_packages({"mss": "mss", "PIL": "pillow"})
    from mss import mss
    from PIL import Image
    mon = {"left": rect["left"], "top": rect["top"], "width": rect["width"], "height": rect["height"]}
    with mss() as sct:
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
