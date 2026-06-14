// FABLEADV-21: 接管视觉指示驱动。
// 监听 chatStore：当运行中且当前工具是 desktop_*（模型在操控真实键鼠）时，
// 通知主进程亮起金色覆盖层；空闲一段时间或运行结束则熄灭。
// 急停胶囊点击 → 主进程转发 metis:takeover-stop → 这里调用 stop() 停止当前 run。
import { useChatStore } from '../store/chatStore';

interface MetisOverlayBridge {
  overlaySetActive?: (active: boolean) => Promise<unknown> | void;
  onTakeoverStop?: (callback: () => void) => (() => void) | void;
}

function bridge(): MetisOverlayBridge | undefined {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (window as any).metis as MetisOverlayBridge | undefined;
}

const IDLE_HIDE_MS = 6000;
let overlayActive = false;
let idleTimer: ReturnType<typeof setTimeout> | null = null;
let initialized = false;

function setOverlayActive(next: boolean): void {
  if (next === overlayActive) return;
  overlayActive = next;
  try {
    void bridge()?.overlaySetActive?.(next);
  } catch {
    /* 非 Electron 环境（如纯浏览器/测试）无 metis 桥，忽略 */
  }
}

function clearIdle(): void {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}

function restartIdle(): void {
  clearIdle();
  idleTimer = setTimeout(() => setOverlayActive(false), IDLE_HIDE_MS);
}

function isDesktopTool(toolName: string | undefined): boolean {
  return typeof toolName === 'string' && toolName.startsWith('desktop_');
}

export function initTakeoverOverlay(): void {
  if (initialized) return;
  const api = bridge();
  if (!api) return; // 仅在 Electron 桌面端启用
  initialized = true;

  api.onTakeoverStop?.(() => {
    try {
      useChatStore.getState().stop();
    } catch {
      /* ignore */
    }
  });

  useChatStore.subscribe(state => {
    const streaming = state.streaming;
    const toolName = state.runtimeStatus?.toolName;
    if (streaming && isDesktopTool(toolName)) {
      setOverlayActive(true);
      restartIdle();
      return;
    }
    if (!streaming) {
      clearIdle();
      setOverlayActive(false);
    }
  });
}
