import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '../../lib/types';
import { _bindChatStore, _bindSessionStore, applyChatEvent, flushAssistantText } from '../sseParser';

describe('sseParser ordered message parts', () => {
  it('keeps narration before tools and corrects only the latest text segment', () => {
    const state: { messages: ChatMessage[]; runtimeStatus: unknown; runSessionId: string | null; subagents: []; todoNotice: null } = {
      messages: [
        {
          id: 'assistant-1',
          role: 'assistant',
          content: '',
          createdAt: Date.now(),
          pending: true,
        },
      ],
      runtimeStatus: null,
      runSessionId: 'session-1',
      subagents: [],
      todoNotice: null,
    };
    const store = {
      getState: () => state,
      setState: (partial: Record<string, unknown>) => Object.assign(state, partial),
    };
    _bindChatStore(store);
    _bindSessionStore({ getState: () => ({ activeSessionId: 'session-1' }) });
    const persistSnapshot = () => undefined;
    const persistRecovery = () => undefined;

    applyChatEvent({ type: 'content_delta', payload: { text: 'I will inspect the file first.' } }, 'assistant-1', 'session-1', persistSnapshot, persistRecovery);
    applyChatEvent(
      { type: 'tool_call', payload: { tool: 'read_file', args: { path: 'backend/web/app.py' }, call_id: 'call-1' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );
    applyChatEvent({ type: 'content_delta', payload: { text: 'Found the route.' } }, 'assistant-1', 'session-1', persistSnapshot, persistRecovery);
    applyChatEvent({ type: 'content', payload: { text: 'Found the route and patched it.' } }, 'assistant-1', 'session-1', persistSnapshot, persistRecovery);
    flushAssistantText('assistant-1', 'session-1', persistSnapshot);

    expect(state.messages[0].content).toBe('I will inspect the file first.Found the route and patched it.');
    expect(state.messages[0].parts).toEqual([
      { type: 'text', text: 'I will inspect the file first.' },
      { type: 'tool', toolId: 'assistant-1-call-1', callId: 'call-1' },
      { type: 'text', text: 'Found the route and patched it.' },
    ]);
  });
});
