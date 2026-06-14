import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useChatStore } from '../../store/chatStore';
import { initTakeoverOverlay } from '../takeoverOverlay';

// 验证接管覆盖层的激活逻辑：desktop_* 工具运行中亮起，运行结束熄灭。
describe('takeoverOverlay 激活逻辑', () => {
  const overlaySetActive = vi.fn();

  beforeEach(() => {
    overlaySetActive.mockClear();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).metis = {
      overlaySetActive,
      onTakeoverStop: () => () => {},
    };
    initTakeoverOverlay();
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({ streaming: false, runtimeStatus: null } as any);
  });

  it('desktop_* 工具运行时亮起，结束时熄灭', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({
      streaming: true,
      runtimeStatus: { phase: 'tool_running', message: '', display: '', severity: 'working', toolName: 'desktop_action' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    expect(overlaySetActive).toHaveBeenCalledWith(true);

    overlaySetActive.mockClear();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({ streaming: false } as any);
    expect(overlaySetActive).toHaveBeenCalledWith(false);
  });

  it('非 desktop 工具不触发覆盖层', () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    useChatStore.setState({
      streaming: true,
      runtimeStatus: { phase: 'tool_running', message: '', display: '', severity: 'working', toolName: 'read_file' },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    expect(overlaySetActive).not.toHaveBeenCalledWith(true);
  });
});
