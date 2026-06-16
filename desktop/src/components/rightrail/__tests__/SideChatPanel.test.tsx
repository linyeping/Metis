import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { SideChatPanel } from '../SideChatPanel';
import { useSideChatStore, type SideChatSession } from '../../../store/sideChatStore';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const testSession: SideChatSession = {
  id: 'side-layout-test',
  title: '布局测试',
  model: '',
  createdAt: 1,
  updatedAt: 2,
  messages: [
    {
      id: 'user-1',
      role: 'user',
      content: '你是什么模型',
      createdAt: 1,
    },
    {
      id: 'assistant-1',
      role: 'assistant',
      content: `我是一个由 OpenAI 提供的 AI 助手。

- 日常聊天版
- 写作/翻译助手版
- 编程辅助版`,
      createdAt: 2,
    },
  ],
};

function resetSideChatStore(session: SideChatSession = testSession) {
  useSideChatStore.setState({
    activeSessionId: session.id,
    composerText: '',
    controller: null,
    error: null,
    sessions: [session],
    streaming: false,
  });
}

describe('SideChatPanel markdown rendering', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    localStorage.clear();
    resetSideChatStore();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    localStorage.clear();
    resetSideChatStore({
      ...testSession,
      id: 'side-layout-reset',
      messages: [],
    });
  });

  it('wraps assistant markdown in the side-chat markdown body', () => {
    act(() => root.render(<SideChatPanel />));

    const assistantBubble = container.querySelector(
      ".side-chat-message[data-role='assistant'] .side-chat-bubble",
    ) as HTMLElement | null;
    expect(assistantBubble).not.toBeNull();

    const markdown = assistantBubble!.querySelector('.markdown-body.side-chat-markdown') as HTMLElement | null;
    expect(markdown).not.toBeNull();
    expect(assistantBubble!.firstElementChild).toBe(markdown);
    expect(markdown!.querySelectorAll('li')).toHaveLength(3);
  });
});
