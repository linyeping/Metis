import { type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react';
import { useMemo } from 'react';
import type { ChatMessage } from '../lib/types';
import { useChatStore } from '../store/chatStore';

export function useMetisRuntime() {
  const messages = useChatStore(state => state.messages);
  const streaming = useChatStore(state => state.streaming);
  const send = useChatStore(state => state.send);

  const threadMessages = useMemo(() => messages.map(toThreadMessage), [messages]);

  return useExternalStoreRuntime<ThreadMessage>({
    messages: threadMessages,
    isRunning: streaming,
    onNew: async message => {
      const text = Array.isArray(message.content)
        ? message.content
            .filter(part => part.type === 'text')
            .map(part => ('text' in part ? part.text : ''))
            .join('')
        : String(message.content || '');
      await send(text);
    },
  });
}

export function toThreadMessage(message: ChatMessage): ThreadMessage {
  const createdAt = new Date(message.createdAt || Date.now());

  if (message.role === 'user') {
    return {
      id: message.id,
      role: 'user',
      content: [{ type: 'text', text: message.content }],
      attachments: [],
      createdAt,
      metadata: { custom: { attachments: message.attachments ?? [] } },
    } as ThreadMessage;
  }

  if (message.role === 'system') {
    return {
      id: message.id,
      role: 'system',
      content: [{ type: 'text', text: message.content }],
      createdAt,
      metadata: { custom: {} },
    } as ThreadMessage;
  }

  // FABLEADV-21: 后端 call_id 可能为空（relay 不回传 tool_call id）。若直接透传空串，
  // ToolCard 的 cardId 会退化成 hash(args)，参数相同的多次桌面动作就撞同一个 id →
  // 点一张卡展开的是另一张（用户报告的「单步卡片点不开」）。这里按 index 兜底保证唯一稳定。
  const toolParts = (message.tools ?? []).map((tool, index) => ({
    type: 'tool-call' as const,
    toolCallId: tool.callId || `${message.id}#tool-${index}`,
    toolName: tool.toolName,
    args: tool.args ?? {},
    argsText: safeJson(tool.args ?? {}),
    metisStatus: tool.status,
    metisSummary: tool.summary,
    metisErrorHint: tool.errorHint,
    metisStartedAt: tool.startedAt,
    metisFinishedAt: tool.finishedAt,
    ...(tool.status === 'running' ? {} : { result: tool.result ?? '' }),
  }));

  const toolPartById = new Map(toolParts.map(part => [part.toolCallId, part]));
  const orderedParts: unknown[] = [];
  if (message.parts?.length) {
    const renderedToolIds = new Set<string>();
    for (const part of message.parts) {
      if (part.type === 'text') {
        if (part.text) orderedParts.push({ type: 'text' as const, text: part.text });
        continue;
      }
      const toolPart = toolPartById.get(part.callId);
      if (toolPart) {
        orderedParts.push(toolPart);
        renderedToolIds.add(part.callId);
      }
    }
    for (const toolPart of toolParts) {
      if (!renderedToolIds.has(toolPart.toolCallId)) orderedParts.push(toolPart);
    }
  } else {
    orderedParts.push(...toolParts);
    if (message.content) orderedParts.push({ type: 'text' as const, text: message.content });
  }

  return {
    id: message.id,
    role: 'assistant',
    content: orderedParts,
    createdAt,
    status: message.error
      ? { type: 'incomplete', reason: 'error', error: message.error }
      : message.pending
        ? { type: 'running' }
        : { type: 'complete', reason: 'stop' },
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {},
    },
  } as unknown as ThreadMessage;
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value ?? '');
  }
}
