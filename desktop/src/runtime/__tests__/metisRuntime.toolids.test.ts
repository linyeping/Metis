import { describe, expect, it } from 'vitest';

import type { ChatMessage } from '../../lib/types';
import { toThreadMessage } from '../metisRuntime';

// FABLEADV-21: 复现并锁定「单步卡片点不开」根因——
// 后端 call_id 可能为空，若透传空串则 ToolCard 的 cardId 退化为 hash(args)，
// 参数相同的多次桌面动作撞同一个 id。这里验证 metisRuntime 为每张卡兜底唯一 id。
function toolCallIds(message: ChatMessage): string[] {
  const tm = toThreadMessage(message);
  const content = (tm as { content?: unknown }).content;
  if (!Array.isArray(content)) return [];
  return content
    .filter((p): p is { type: string; toolCallId: string } =>
      Boolean(p) && typeof p === 'object' && (p as { type?: unknown }).type === 'tool-call',
    )
    .map(p => p.toolCallId);
}

describe('metisRuntime 工具卡 id 唯一性', () => {
  it('call_id 为空 + 参数相同的多张卡，仍获得唯一 toolCallId', () => {
    const message = {
      id: 'assistant-123',
      role: 'assistant',
      content: '',
      createdAt: 1,
      tools: [
        { callId: '', toolName: 'desktop_action', args: { action: 'click', x: 50, y: 50 }, status: 'success', result: 'Done' },
        { callId: '', toolName: 'desktop_action', args: { action: 'click', x: 50, y: 50 }, status: 'success', result: 'Done' },
        { callId: '', toolName: 'desktop_screenshot', args: {}, status: 'success', result: 'Saved' },
      ],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any as ChatMessage;

    const ids = toolCallIds(message);
    expect(ids).toHaveLength(3);
    expect(new Set(ids).size).toBe(3); // 全部唯一，无撞车
    expect(ids.every(id => id.length > 0)).toBe(true);
  });

  it('call_id 存在时按原值透传（不破坏正常情况）', () => {
    const message = {
      id: 'assistant-9',
      role: 'assistant',
      content: '',
      createdAt: 1,
      tools: [
        { callId: 'call_a', toolName: 'desktop_action', args: { action: 'click' }, status: 'success', result: 'Done' },
        { callId: 'call_b', toolName: 'desktop_action', args: { action: 'click' }, status: 'success', result: 'Done' },
      ],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any as ChatMessage;

    const ids = toolCallIds(message);
    expect(ids).toEqual(['call_a', 'call_b']);
  });
});
