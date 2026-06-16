import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '../../lib/types';
import { _bindChatStore, _bindSessionStore, applyChatEvent, flushAssistantText } from '../sseParser';

function bindParserState() {
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
  return state;
}

describe('sseParser ordered message parts', () => {
  it('keeps narration before tools and corrects only the latest text segment', () => {
    const state = bindParserState();
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

  it('merges a desktop_expert result without call_id into the running desktop_expert card', () => {
    const state = bindParserState();
    const persistSnapshot = () => undefined;
    const persistRecovery = () => undefined;

    applyChatEvent(
      { type: 'tool_call', payload: { tool: 'desktop_expert', args: { goal: 'click the button' }, call_id: 'desktop-call-1' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );
    applyChatEvent(
      { type: 'tool_result', payload: { tool: 'desktop_expert', result: '[Expert: desktop_expert]\nDone' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );

    expect(state.messages[0].tools).toHaveLength(1);
    expect(state.messages[0].tools?.[0]).toMatchObject({
      callId: 'desktop-call-1',
      toolName: 'desktop_expert',
      status: 'success',
      result: '[Expert: desktop_expert]\nDone',
    });
    expect(state.messages[0].parts).toEqual([
      { type: 'tool', toolId: 'assistant-1-desktop-call-1', callId: 'desktop-call-1' },
    ]);
  });

  it('finalizes stale running tools when the assistant turn is done', () => {
    const state = bindParserState();
    const persistSnapshot = () => undefined;
    const persistRecovery = () => undefined;

    applyChatEvent(
      { type: 'tool_call', payload: { tool: 'desktop_expert', args: { goal: 'open notepad' }, call_id: 'desktop-call-2' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );
    applyChatEvent({ type: 'done', payload: {} }, 'assistant-1', 'session-1', persistSnapshot, persistRecovery);

    expect(state.messages[0].tools).toHaveLength(1);
    expect(state.messages[0].tools?.[0].callId).toBe('desktop-call-2');
    expect(state.messages[0].tools?.[0].status).toBe('success');
    expect(state.messages[0].tools?.[0].finishedAt).toEqual(expect.any(Number));
    expect(state.messages[0].tools?.[0].result).toBe('[Run completed without a separate tool result event]');
  });

  it('finalizes running tools when the runtime reports cancellation', () => {
    const state = bindParserState();
    const persistSnapshot = () => undefined;
    const persistRecovery = () => undefined;

    applyChatEvent(
      { type: 'tool_call', payload: { tool: 'desktop_expert', args: { goal: 'open notepad' }, call_id: 'desktop-call-3' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );
    applyChatEvent(
      { type: 'runtime_status', payload: { phase: 'canceled', message: 'User canceled this run' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );

    expect(state.messages[0].tools).toHaveLength(1);
    expect(state.messages[0].tools?.[0]).toMatchObject({
      callId: 'desktop-call-3',
      status: 'error',
      result: '[Run canceled before this tool returned a result]\nUser canceled this run',
      errorHint: '任务已取消，工具活动已停止。',
    });
  });

  it('finalizes running tools when an error event arrives before a tool result', () => {
    const state = bindParserState();
    const persistSnapshot = () => undefined;
    const persistRecovery = () => undefined;

    applyChatEvent(
      { type: 'tool_call', payload: { tool: 'desktop_expert', args: { goal: 'open notepad' }, call_id: 'desktop-call-4' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );
    applyChatEvent(
      { type: 'error', payload: { title: '运行失败', message: 'Desktop task timed out', hint: 'Try a smaller goal.' } },
      'assistant-1',
      'session-1',
      persistSnapshot,
      persistRecovery,
    );

    expect(state.messages[0].tools).toHaveLength(1);
    expect(state.messages[0].tools?.[0].status).toBe('error');
    expect(state.messages[0].tools?.[0].result).toContain('[Run failed before this tool returned a result]');
    expect(state.messages[0].tools?.[0].result).toContain('Desktop task timed out');
    expect(state.messages[0].tools?.[0].finishedAt).toEqual(expect.any(Number));
  });
});
