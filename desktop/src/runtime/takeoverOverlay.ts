// FABLEADV-21: 接管视觉指示驱动。
// 监听 chatStore + desk SSE：当当前 run 进入桌面接管，或后端视觉循环仍在运行/暂停时，
// 通知主进程亮起金色覆盖层；直到 run 结束且 desk 循环结束才熄灭。
// 主进程急停事件 → 这里调用 stop() 停止当前 run。
import { useChatStore } from '../store/chatStore';

interface MetisOverlayBridge {
  backendPort?: () => Promise<number | null>;
  overlaySetActive?: (active: boolean) => Promise<unknown> | void;
  onTakeoverStop?: (callback: () => void) => (() => void) | void;
}

function bridge(): MetisOverlayBridge | undefined {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (window as any).metis as MetisOverlayBridge | undefined;
}

let overlayActive = false;
let chatTakeoverActive = false;
let deskTakeoverActive = false;
let deskEventSource: EventSource | null = null;
let deskReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let initialized = false;

const DESK_STREAM_RECONNECT_MS = 5000;
const takeoverTools = new Set([
  'desktop_action',
  'desktop_expert',
  'desktop_vision_task',
  'desktop_win2_action',
  'desktop_win2_task',
  'desktop_window_action',
]);

function setOverlayActive(next: boolean): void {
  if (next === overlayActive) return;
  overlayActive = next;
  try {
    void bridge()?.overlaySetActive?.(next);
  } catch {
    /* 非 Electron 环境（如纯浏览器/测试）无 metis 桥，忽略 */
  }
}

function refreshOverlay(): void {
  setOverlayActive(chatTakeoverActive || deskTakeoverActive);
}

function isDesktopTool(toolName: string | undefined): boolean {
  return typeof toolName === 'string' && takeoverTools.has(toolName);
}

function activeDeskPayload(payload: Record<string, unknown>): boolean {
  const status = String(payload.vision_status || payload.visionStatus || '').toLowerCase();
  const goal = String(payload.vision_goal || payload.visionGoal || payload.goal || '').trim();
  const running = payload.vision_running === true || payload.visionRunning === true;
  if (running || status === 'running') return true;
  return status === 'paused' && Boolean(goal);
}

function updateDeskTakeover(payload: Record<string, unknown>): void {
  if (payload.event === 'vision_state' || payload.event === 'interrupt' || payload.event === 'hello') {
    deskTakeoverActive = activeDeskPayload(payload);
    refreshOverlay();
  }
}

function clearDeskReconnectTimer(): void {
  if (deskReconnectTimer) {
    clearTimeout(deskReconnectTimer);
    deskReconnectTimer = null;
  }
}

function scheduleDeskStreamReconnect(): void {
  if (deskReconnectTimer || !bridge()?.backendPort) return;
  deskReconnectTimer = setTimeout(() => {
    deskReconnectTimer = null;
    void connectDeskStream();
  }, DESK_STREAM_RECONNECT_MS);
}

async function currentBackendPort(): Promise<number | null> {
  const api = bridge();
  if (!api?.backendPort) return null;
  try {
    return await api.backendPort();
  } catch {
    return null;
  }
}

async function refreshDeskStatusFromHttp(): Promise<void> {
  const port = await currentBackendPort();
  if (!port) return;
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/status`, {
      cache: 'no-store',
    });
    if (!response.ok) return;
    const data = (await response.json()) as Record<string, unknown>;
    updateDeskTakeover({ event: 'vision_state', ...data });
  } catch {
    /* Keep the previous visible state until SSE reconnects or chat run ends. */
  }
}

async function connectDeskStream(): Promise<void> {
  if (deskEventSource || typeof EventSource === 'undefined') return;
  const port = await currentBackendPort();
  if (!port) {
    scheduleDeskStreamReconnect();
    return;
  }

  clearDeskReconnectTimer();
  const source = new EventSource(`http://127.0.0.1:${port}/api/desk/stream`);
  deskEventSource = source;
  source.onmessage = event => {
    try {
      updateDeskTakeover(JSON.parse(event.data) as Record<string, unknown>);
    } catch {
      /* ignore malformed keepalive or proxy noise */
    }
  };
  source.onerror = () => {
    if (deskEventSource === source) {
      deskEventSource = null;
    }
    try {
      source.close();
    } catch {
      /* ignore */
    }
    void refreshDeskStatusFromHttp();
    scheduleDeskStreamReconnect();
  };
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
      chatTakeoverActive = true;
      refreshOverlay();
      return;
    }
    if (!streaming) {
      chatTakeoverActive = false;
      refreshOverlay();
    }
  });

  void connectDeskStream();
}
