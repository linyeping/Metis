import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { PendingFollowups } from '../PendingFollowups';
import { useChatStore } from '../../../store/chatStore';
import { useSessionStore } from '../../../store/sessionStore';
import { useUiStore } from '../../../store/uiStore';

describe('PendingFollowups', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    useUiStore.setState({ language: 'zh' });
    useSessionStore.setState({ activeSessionId: 'session-queue' });
    useChatStore.setState({
      streaming: true,
      followupsBySession: {
        'session-queue': Array.from({ length: 8 }, (_, index) => ({
          id: `followup-${index}`,
          message: `message ${index}`,
          behavior: index % 2 === 0 ? 'queue' as const : 'steer' as const,
          status: 'pending',
          createdAt: index,
          updatedAt: index,
          runId: 'run-1',
        })),
      },
      pausedFollowupSessions: {},
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    useSessionStore.setState({ activeSessionId: null });
    useChatStore.setState({ followupsBySession: {}, pausedFollowupSessions: {} });
  });

  it('keeps all queued rows in a five-row scroll viewport', () => {
    act(() => root.render(<PendingFollowups />));

    expect(container.querySelectorAll('.pending-followup-row')).toHaveLength(8);
    expect(container.querySelector('.pending-followups-list')?.getAttribute('data-visible-rows')).toBe('5');
    expect(container.textContent).toContain('8/10');
  });

  it('routes Edit message back to the composer draft action', async () => {
    const editFollowup = vi.fn(async () => undefined);
    useChatStore.setState({ editFollowup });
    act(() => root.render(<PendingFollowups />));

    const more = container.querySelector<HTMLButtonElement>('[aria-label="更多操作"]');
    expect(more).not.toBeNull();
    act(() => more!.click());
    const edit = container.querySelector<HTMLButtonElement>('[role="menuitem"]');
    expect(edit?.textContent).toContain('编辑消息');
    await act(async () => {
      edit!.click();
      await Promise.resolve();
    });

    expect(editFollowup).toHaveBeenCalledWith('followup-0');
  });
});
