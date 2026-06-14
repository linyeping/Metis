import { describe, expect, it } from 'vitest';
import { normalizeChatStreamEvent } from '../agentEvents';

describe('normalizeChatStreamEvent', () => {
  it('normalizes content delta events', () => {
    const event = normalizeChatStreamEvent({
      type: 'content_delta',
      payload: { text: 'live text' },
    });

    expect(event.kind).toBe('content_delta');
    expect(event.text).toBe('live text');
  });

  it('keeps runtime turn, tool count, and call id fields', () => {
    const event = normalizeChatStreamEvent({
      type: 'runtime_status',
      payload: {
        phase: 'tool_running',
        message: 'Running tool read_file',
        tool: 'read_file',
        call_id: 'call-1',
        turn: 3,
        tool_calls: 7,
      },
    });

    expect(event.runtimeStatus?.turn).toBe(3);
    expect(event.runtimeStatus?.toolCalls).toBe(7);
    expect(event.runtimeStatus?.callId).toBe('call-1');
  });

  it('normalizes done usage cache fields', () => {
    const event = normalizeChatStreamEvent({
      type: 'done',
      payload: {
        usage: {
          prompt_tokens: 100,
          completion_tokens: 5,
          total_tokens: 105,
          prompt_cache_hit_tokens: 80,
          prompt_cache_miss_tokens: 20,
        },
      },
    });

    expect(event.usage).toEqual({
      promptTokens: 100,
      completionTokens: 5,
      totalTokens: 105,
      promptCacheHitTokens: 80,
      promptCacheMissTokens: 20,
    });
  });

  it('normalizes todo update events', () => {
    const event = normalizeChatStreamEvent({
      type: 'todo_update',
      payload: {
        summary: '[任务清单] 1.▶️ investigate',
        todos: [
          { id: '1', content: 'investigate', status: 'in_progress' },
          { id: '2', content: 'verify', status: 'done' },
        ],
      },
    });

    expect(event.todo?.summary).toContain('任务清单');
    expect(event.todo?.activeCount).toBe(1);
    expect(event.todo?.doneCount).toBe(1);
  });
});
