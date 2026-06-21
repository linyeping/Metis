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
        details: { request_model: 'pro', served_model: 'pro' },
      },
    });

    expect(event.runtimeStatus?.turn).toBe(3);
    expect(event.runtimeStatus?.toolCalls).toBe(7);
    expect(event.runtimeStatus?.callId).toBe('call-1');
    expect(event.runtimeStatus?.details?.request_model).toBe('pro');
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

  it('normalizes done context ledger fields', () => {
    const event = normalizeChatStreamEvent({
      type: 'done',
      payload: {
        context_ledger: {
          system_tokens: 10,
          schema_tokens: 20,
          history_tokens: 30,
          estimated_total_tokens: 60,
          context_limit: 1000,
          context_ratio: 0.06,
          cache_hit_tokens: 40,
          cache_miss_tokens: 10,
          cache_hit_rate: 0.8,
          system_breakdown: { system_prompt: 4, skills: 5, memory: 1 },
          schema_breakdown: { mcp: 8, builtin: 12 },
        },
      },
    });

    expect(event.contextLedger?.estimatedTotalTokens).toBe(60);
    expect(event.contextLedger?.systemBreakdown?.skills).toBe(5);
    expect(event.contextLedger?.schemaBreakdown?.mcp).toBe(8);
    expect(event.contextLedger?.cacheHitRate).toBe(0.8);
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

  it('normalizes tool summary labels', () => {
    const event = normalizeChatStreamEvent({
      type: 'tool_result',
      payload: {
        tool: 'read_file',
        call_id: 'call-read',
        result: 'ok',
        summary: 'Read app.py',
      },
    });

    expect(event.summary).toBe('Read app.py');
    expect(event.callId).toBe('call-read');
  });

  it('normalizes permission explainer metadata', () => {
    const event = normalizeChatStreamEvent({
      type: 'permission_request',
      payload: {
        tool: 'write_file',
        call_id: 'call-write',
        request_id: 'permission-1',
        permission: {
          explainer: {
            explanation: 'write_file may modify files.',
            reasoning: 'I am asking because this writes outside workspace.',
            risk: 'May write outside workspace',
            riskLevel: 'HIGH',
            autoguard: {
              shouldBlock: true,
              reason: 'target is outside the active workspace',
            },
          },
        },
      },
    });

    expect(event.permission?.explainer?.riskLevel).toBe('HIGH');
    expect(event.permission?.explainer?.autoguard?.shouldBlock).toBe(true);
  });
});
