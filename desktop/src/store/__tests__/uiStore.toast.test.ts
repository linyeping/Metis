import { beforeEach, describe, expect, it } from 'vitest';
import { useUiStore } from '../uiStore';

describe('uiStore toast session cleanup', () => {
  beforeEach(() => {
    useUiStore.setState({ expandedToolCards: new Set(), toasts: [] });
  });

  it('deduplicates same session toasts and clears only the previous session', () => {
    useUiStore.getState().pushToast({
      title: '运行失败',
      description: 'context too large',
      type: 'error',
      duration: 0,
      sessionId: 'session-a',
    });
    useUiStore.getState().pushToast({
      title: '运行失败',
      description: 'context too large',
      type: 'error',
      duration: 0,
      sessionId: 'session-a',
    });
    useUiStore.getState().pushToast({
      title: '运行失败',
      description: 'context too large',
      type: 'error',
      duration: 0,
      sessionId: 'session-b',
    });
    useUiStore.getState().pushToast({
      title: '全局通知',
      description: 'keep me',
      type: 'info',
    });

    expect(useUiStore.getState().toasts).toHaveLength(3);
    useUiStore.getState().clearToastsForSession('session-a');

    expect(useUiStore.getState().toasts.map(toast => toast.sessionId || 'global')).toEqual(['session-b', 'global']);
  });

  it('persists expanded tool card state outside individual ToolCard mounts', () => {
    useUiStore.getState().setToolCardExpanded('tool-call-1', true);
    expect(useUiStore.getState().expandedToolCards.has('tool-call-1')).toBe(true);

    useUiStore.getState().setToolCardExpanded('tool-call-1', false);
    expect(useUiStore.getState().expandedToolCards.has('tool-call-1')).toBe(false);

    useUiStore.getState().setToolCardExpanded('tool-call-2', true);
    useUiStore.getState().clearExpandedToolCards();
    expect(useUiStore.getState().expandedToolCards.size).toBe(0);
  });
});
