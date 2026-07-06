import type { AgentEventKind, ChatStreamEvent } from './types';

type UnknownRecord = Record<string, unknown>;

const legacyKinds = new Set<AgentEventKind>([
  'text_delta',
  'content_delta',
  'content',
  'thinking',
  'tool_call',
  'tool_result',
  'permission_request',
  'error',
  'compact',
  'runtime_status',
  'todo_update',
  'memory_nudge',
  'subagent_start',
  'subagent_progress',
  'subagent_done',
  'done',
]);

export function adaptAgentEventForReducer(input: unknown): ChatStreamEvent {
  const event = recordValue(input);
  if (event.schema !== 'metis.agent_event.v2' && event.version !== 2) {
    return input as ChatStreamEvent;
  }
  return agentEventV2ToChatStreamEvent(event);
}

export function agentEventV2ToChatStreamEvent(event: UnknownRecord): ChatStreamEvent {
  const payload = recordValue(event.payload);
  const kind = stringValue(event.kind);
  const base = baseEvent(event, payload);

  if (kind === 'message_delta') {
    return { ...base, type: 'content_delta', payload: { text: stringValue(value(payload, 'text')) } };
  }
  if (kind === 'message_completed') {
    return { ...base, type: 'content', payload: { text: stringValue(value(payload, 'text')) } };
  }
  if (kind === 'thinking_delta') {
    return { ...base, type: 'thinking', payload: { text: stringValue(value(payload, 'text')) } };
  }
  if (kind === 'tool_requested' || kind === 'tool_running') {
    return {
      ...base,
      type: 'tool_call',
      payload: {
        tool: toolName(payload),
        args: value(payload, 'arguments', 'args') ?? {},
        call_id: stringValue(value(payload, 'call_id', 'callId')),
        summary: stringValue(value(payload, 'summary', 'arguments_preview', 'argumentsPreview')),
      },
    };
  }
  if (kind === 'permission_required') {
    return {
      ...base,
      type: 'permission_request',
      payload: {
        tool: toolName(payload),
        args: value(payload, 'arguments', 'args') ?? {},
        call_id: stringValue(value(payload, 'call_id', 'callId')),
        request_id: stringValue(value(payload, 'request_id', 'requestId')),
        permission: recordValue(value(payload, 'permission')),
      },
    };
  }
  if (kind === 'permission_answered') {
    return {
      ...base,
      type: 'runtime_status',
      payload: {
        phase: 'permission_answered',
        message: booleanValue(value(payload, 'approved')) ? 'Permission approved' : 'Permission denied',
        tool: toolName(payload),
        call_id: stringValue(value(payload, 'call_id', 'callId')),
        recoverable: true,
      },
    };
  }
  if (kind === 'permission_applied' || kind === 'permission_rejected' || kind === 'permission_expired' || kind === 'permission_audited') {
    return {
      ...base,
      type: 'runtime_status',
      payload: {
        phase: kind,
        message: permissionStatusMessage(kind),
        tool: toolName(payload),
        call_id: stringValue(value(payload, 'call_id', 'callId')),
        recoverable: kind !== 'permission_expired',
        details: payload,
      },
    };
  }
  if (kind === 'tool_succeeded' || kind === 'tool_failed' || kind === 'tool_canceled' || kind === 'tool_timed_out') {
    return {
      ...base,
      type: 'tool_result',
      payload: {
        tool: toolName(payload),
        result: terminalToolResult(kind, payload),
        call_id: stringValue(value(payload, 'call_id', 'callId')),
        summary: stringValue(value(payload, 'summary', 'result_preview', 'resultPreview')),
      },
    };
  }
  if (kind === 'run_completed') {
    return {
      ...base,
      type: 'done',
      payload,
    };
  }
  if (kind === 'run_failed') {
    return {
      ...base,
      type: 'error',
      payload: errorPayload(payload, 'RUN_FAILED', '运行失败'),
    };
  }
  if (kind === 'run_canceled') {
    return {
      ...base,
      type: 'error',
      payload: errorPayload(payload, 'RUN_CANCELLED', '运行已取消'),
    };
  }
  if (kind === 'runtime_status') {
    return { ...base, type: 'runtime_status', payload };
  }
  if (legacyKinds.has(kind as AgentEventKind)) {
    return { ...base, type: kind, payload } as ChatStreamEvent;
  }
  return {
    ...base,
    type: 'runtime_status',
    payload: {
      phase: 'protocol_event',
      message: kind || 'unknown event',
      details: payload,
      recoverable: true,
    },
  };
}

function permissionStatusMessage(kind: string): string {
  if (kind === 'permission_applied') return 'Permission applied';
  if (kind === 'permission_rejected') return 'Permission rejected';
  if (kind === 'permission_expired') return 'Permission expired';
  if (kind === 'permission_audited') return 'Permission audited';
  return 'Permission updated';
}

function baseEvent(event: UnknownRecord, payload: UnknownRecord): Omit<ChatStreamEvent, 'type'> {
  return {
    schema: 'metis.agent_event.v2',
    event_id: stringValue(event.event_id),
    timestamp: timestampSeconds(event.timestamp),
    payload,
    run_id: stringValue(event.run_id),
    runId: stringValue(event.run_id),
    turn_id: stringValue(event.turn_id),
    turnId: stringValue(event.turn_id),
    session_id: stringValue(event.session_id),
    sessionId: stringValue(event.session_id),
    message_id: stringValue(event.message_id),
    messageId: stringValue(event.message_id),
    seq: numberValue(event.seq),
  };
}

function errorPayload(payload: UnknownRecord, fallbackCode: string, fallbackTitle: string): UnknownRecord {
  const error = recordValue(value(payload, 'error'));
  const message =
    stringValue(value(error, 'message')) ||
    stringValue(value(payload, 'message', 'result_preview', 'resultPreview')) ||
    fallbackTitle;
  return {
    ...payload,
    code: stringValue(value(error, 'code')) || stringValue(value(payload, 'code')) || fallbackCode,
    title: stringValue(value(payload, 'title')) || fallbackTitle,
    message,
    hint: stringValue(value(payload, 'hint')),
    recoverable: booleanValue(value(error, 'recoverable'), booleanValue(value(payload, 'recoverable'))),
  };
}

function terminalToolResult(kind: string, payload: UnknownRecord): unknown {
  const rawResult = value(payload, 'result', 'result_preview', 'resultPreview');
  const error = recordValue(value(payload, 'error'));
  const errorMessage = stringValue(value(error, 'message'));
  const text = typeof rawResult === 'string' ? rawResult : '';
  if (kind === 'tool_succeeded') return rawResult ?? '';
  if (kind === 'tool_canceled') return text || `[Cancelled] ${errorMessage || 'Tool canceled'}`;
  if (kind === 'tool_timed_out') return text || `Error: ${errorMessage || 'Tool timed out'}`;
  if (/^(error\b|❌|错误|\[permission denied\])/i.test(text.trim())) return rawResult;
  return text ? `Error: ${text}` : `Error: ${errorMessage || 'Tool failed'}`;
}

function toolName(payload: UnknownRecord): string {
  return stringValue(value(payload, 'tool_name', 'toolName', 'tool', 'name')) || 'tool';
}

function value(record: UnknownRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined) return record[key];
  }
  return undefined;
}

function recordValue(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function timestampSeconds(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 10_000_000_000 ? value / 1000 : value;
  }
  if (typeof value === 'string' && value) {
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed / 1000;
  }
  return Date.now() / 1000;
}
