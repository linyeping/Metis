# -*- coding: utf-8 -*-
"""全局监听 ESC → 写入暂停标志（需 pynput）。另开终端运行：

  cd <agent 根目录>
  python -m tools.desk_automation.hooks.esc_listener
"""

from __future__ import annotations

import sys


def main() -> None:
    from backend.runtime.pip_helper import ensure_import
    pynput = ensure_import("pynput", pip="pynput")
    keyboard = pynput.keyboard

    from ..config import set_paused

    def on_press(key: keyboard.Key | keyboard.KeyCode) -> None:
        try:
            if key == keyboard.Key.esc:
                set_paused(True)
                print("ESC → desk_automation 已暂停（POST /api/resume 或网页继续）", flush=True)
        except Exception as ex:  # noqa: BLE001
            print("error:", ex, flush=True)

    print("监听 ESC …  Ctrl+C 退出", flush=True)
    with keyboard.Listener(on_press=on_press) as lis:
        lis.join()


if __name__ == "__main__":
    main()
