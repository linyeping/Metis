import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type ChatStoreModule = typeof import('../../store/chatStore');
type OverlayModule = typeof import('../takeoverOverlay');

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  emit(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }
}

describe('takeoverOverlay 激活逻辑', () => {
  const overlaySetActive = vi.fn();
  let useChatStore: ChatStoreModule['useChatStore'];
  let initTakeoverOverlay: OverlayModule['initTakeoverOverlay'];
  let originalEventSource: typeof EventSource | undefined;

  beforeEach(async () => {
    vi.resetModules();
    overlaySetActive.mockClear();
    FakeEventSource.instances = [];
    originalEventSource = globalThis.EventSource;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (globalThis as any).EventSource;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).metis = {
      overlaySetActive,
      onTakeoverStop: () => () => {},
    };

    ({ useChatStore } = await import('../../store/chatStore'));
    ({ initTakeoverOverlay } = await import('../takeoverOverlay'));
    initTakeoverOverlay();
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({ streaming: false, runtimeStatus: null } as any);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).EventSource = originalEventSource;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (window as any).metis;
  });

  it('desktop 控制工具运行后保持亮起，直到当前 run 结束', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({
      streaming: true,
      runtimeStatus: { phase: 'tool_running', message: '', display: '', severity: 'working', toolName: 'desktop_action' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    expect(overlaySetActive).toHaveBeenCalledWith(true);

    overlaySetActive.mockClear();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({
      streaming: true,
      runtimeStatus: { phase: 'llm_request', message: '', display: '', severity: 'working', toolName: 'tool' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    expect(overlaySetActive).not.toHaveBeenCalledWith(false);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({ streaming: false } as any);
    expect(overlaySetActive).toHaveBeenCalledWith(false);
  });

  it('Win2 和桌面专家工具运行时也会亮起覆盖层', () => {
    for (const toolName of ['desktop_win2_task', 'desktop_win2_action', 'desktop_expert']) {
      overlaySetActive.mockClear();
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      useChatStore.setState({
        streaming: true,
        runtimeStatus: { phase: 'tool_running', message: '', display: '', severity: 'working', toolName },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
      } as any);
      expect(overlaySetActive).toHaveBeenCalledWith(true);

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      useChatStore.setState({ streaming: false } as any);
    }
  });

  it('非 desktop 控制工具不触发覆盖层', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({
      streaming: true,
      runtimeStatus: { phase: 'tool_running', message: '', display: '', severity: 'working', toolName: 'read_file' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    expect(overlaySetActive).not.toHaveBeenCalledWith(true);
  });

  it('desk SSE 视觉循环运行/结束会直接控制覆盖层', async () => {
    vi.resetModules();
    overlaySetActive.mockClear();
    FakeEventSource.instances = [];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (globalThis as any).EventSource = FakeEventSource;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).metis = {
      backendPort: vi.fn().mockResolvedValue(4173),
      overlaySetActive,
      onTakeoverStop: () => () => {},
    };

    ({ useChatStore } = await import('../../store/chatStore'));
    ({ initTakeoverOverlay } = await import('../takeoverOverlay'));
    initTakeoverOverlay();
    await Promise.resolve();
    await Promise.resolve();

    expect(FakeEventSource.instances[0]?.url).toBe('http://127.0.0.1:4173/api/desk/stream');

    FakeEventSource.instances[0].emit({
      event: 'vision_state',
      vision_running: true,
      vision_status: 'running',
      vision_goal: '打开 Chrome',
    });
    expect(overlaySetActive).toHaveBeenCalledWith(true);

    overlaySetActive.mockClear();
    FakeEventSource.instances[0].emit({
      event: 'vision_state',
      vision_running: false,
      vision_status: 'done',
      vision_goal: '打开 Chrome',
    });
    expect(overlaySetActive).toHaveBeenCalledWith(false);
  });
});
