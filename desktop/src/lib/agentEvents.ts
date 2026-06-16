import type { AgentEventKind, ChatStreamEvent, ChatSubagentEvent, ChatTodoItem, ChatTokenUsage, CompactStatusPayload, ContextLedger, RuntimeStatus } from './types';

interface NormalizedError {
  code: string;
  title: string;
  message: string;
  hint: string;
  recoverable: boolean;
}

interface NormalizedMemory {
  message: string;
  memoryCount: number;
  skillCount: number;
  memoryPath: string;
  skillPath: string;
}

interface NormalizedTodo {
  todos: ChatTodoItem[];
  summary: string;
  activeCount: number;
  doneCount: number;
}

export interface NormalizedChatEvent {
  kind: AgentEventKind;
  text: string;
  toolName: string;
  args: unknown;
  result: unknown;
  callId: string;
  requestId: string;
  error: NormalizedError;
  usage: ChatTokenUsage | null;
  contextLedger: ContextLedger | null;
  runtimeStatus: RuntimeStatus | null;
  compactStatus: CompactStatusPayload | null;
  memory: NormalizedMemory | null;
  todo: NormalizedTodo | null;
  subagent: ChatSubagentEvent | null;
}

type UnknownRecord = Record<string, unknown>;

const knownKinds = new Set<AgentEventKind>([
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

export function normalizeChatStreamEvent(event: ChatStreamEvent): NormalizedChatEvent {
  const eventRecord = event as unknown as UnknownRecord;
  const payload = recordValue(event.payload);
  const kind = eventKind(event);
  const usagePayload = recordValue(value(payload, eventRecord, 'usage'));
  const ledgerPayload = recordValue(value(payload, eventRecord, 'context_ledger', 'contextLedger'));
  const memoryCount = numberValue(value(payload, eventRecord, 'memory_count', 'memoryCount'));
  const skillCount = numberValue(value(payload, eventRecord, 'skill_count', 'skillCount'));
  const memoryPath = stringValue(value(payload, eventRecord, 'memory_path', 'memoryPath'));
  const skillPath = stringValue(value(payload, eventRecord, 'skill_path', 'skillPath'));
  const todoItems = todoListValue(value(payload, eventRecord, 'todos'));
  const taskId = stringValue(value(payload, eventRecord, 'task_id', 'taskId', 'call_id', 'callId'));
  const subagentStatus = stringValue(value(payload, eventRecord, 'status'));
  const subagentProgress = clampProgress(numberValue(value(payload, eventRecord, 'progress')));
  const phase = stringValue(value(payload, eventRecord, 'phase'));
  const toolName = stringValue(value(payload, eventRecord, 'tool', 'toolName', 'tool_name', 'name')) || 'tool';
  const errorInfo = recordValue(value(payload, eventRecord, 'error_info', 'errorInfo'));
  const timestamp = numberValue(value(payload, eventRecord, 'timestamp')) || Date.now() / 1000;

  return {
    kind,
    text: stringValue(value(payload, eventRecord, 'text')),
    toolName,
    args: value(payload, eventRecord, 'args', 'arguments'),
    result: value(payload, eventRecord, 'result'),
    callId: stringValue(value(payload, eventRecord, 'call_id', 'callId')) || `call-${Date.now()}`,
    requestId: stringValue(value(payload, eventRecord, 'request_id', 'requestId')),
    error: {
      code: stringValue(value(errorInfo, payload, 'code')) || stringValue(value(payload, eventRecord, 'code')),
      title: stringValue(value(errorInfo, payload, 'title')) || stringValue(value(payload, eventRecord, 'title')),
      message:
        stringValue(value(errorInfo, payload, 'message', 'description')) ||
        stringValue(value(payload, eventRecord, 'message')),
      hint:
        stringValue(value(errorInfo, payload, 'hint', 'action')) ||
        stringValue(value(payload, eventRecord, 'hint')),
      recoverable: booleanValue(value(errorInfo, payload, 'recoverable', 'retry'), booleanValue(value(payload, eventRecord, 'recoverable'))),
    },
    usage:
      kind === 'done' && Object.keys(usagePayload).length > 0
        ? {
            promptTokens: numberValue(value(usagePayload, {}, 'prompt_tokens', 'promptTokens')),
            completionTokens: numberValue(value(usagePayload, {}, 'completion_tokens', 'completionTokens')),
            totalTokens: numberValue(value(usagePayload, {}, 'total_tokens', 'totalTokens')),
            promptCacheHitTokens: numberValue(value(usagePayload, {}, 'prompt_cache_hit_tokens', 'promptCacheHitTokens')),
            promptCacheMissTokens: numberValue(value(usagePayload, {}, 'prompt_cache_miss_tokens', 'promptCacheMissTokens')),
          }
        : null,
    contextLedger:
      kind === 'done' && Object.keys(ledgerPayload).length > 0
        ? contextLedgerValue(ledgerPayload)
        : null,
    runtimeStatus:
      kind === 'runtime_status'
        ? runtimeStatus({
            phase,
            message: stringValue(value(payload, eventRecord, 'message')),
            toolName,
            callId: stringValue(value(payload, eventRecord, 'call_id', 'callId')),
            turn: numberValue(value(payload, eventRecord, 'turn')),
            toolCalls: numberValue(value(payload, eventRecord, 'tool_calls', 'toolCalls')),
            timestamp,
            hint: stringValue(value(payload, eventRecord, 'hint')),
            recoverable: booleanValue(value(payload, eventRecord, 'recoverable'), true),
          })
        : null,
    compactStatus:
      kind === 'compact'
        ? {
            running: true,
            ok: false,
            beforeCount: numberValue(value(payload, eventRecord, 'before_count', 'beforeCount')),
            afterCount: numberValue(value(payload, eventRecord, 'after_count', 'afterCount')),
            summaryPreview: stringValue(value(payload, eventRecord, 'summary_preview', 'summaryPreview')),
            updatedAt: Date.now() / 1000,
            error: '',
          }
        : null,
    memory:
      kind === 'memory_nudge'
        ? {
            message:
              stringValue(value(payload, eventRecord, 'message')) ||
              `已沉淀 ${memoryCount} 条记忆${skillCount ? `，生成 ${skillCount} 个技能` : ''}`,
            memoryCount,
            skillCount,
            memoryPath,
            skillPath,
          }
        : null,
    todo:
      kind === 'todo_update'
        ? {
            todos: todoItems,
            summary: stringValue(value(payload, eventRecord, 'summary')),
            activeCount: todoItems.filter(item => isActiveTodoStatus(item.status)).length,
            doneCount: todoItems.filter(item => isDoneTodoStatus(item.status)).length,
          }
        : null,
    subagent:
      kind === 'subagent_start' || kind === 'subagent_progress' || kind === 'subagent_done'
        ? {
            taskId,
            name: stringValue(value(payload, eventRecord, 'name', 'tool')) || 'subagent',
            status: subagentStatus === 'error' ? 'error' : kind === 'subagent_done' ? 'done' : 'running',
            progress: subagentProgress || (kind === 'subagent_done' ? 100 : 0),
            summary: stringValue(value(payload, eventRecord, 'summary', 'message')),
            result: value(payload, eventRecord, 'result'),
          }
        : null,
  };
}

function eventKind(event: ChatStreamEvent): AgentEventKind {
  const candidate = stringValue(event.kind) || stringValue(event.type);
  return knownKinds.has(candidate as AgentEventKind) ? (candidate as AgentEventKind) : 'error';
}

function value(payload: UnknownRecord, event: UnknownRecord, ...keys: string[]): unknown {
  for (const key of keys) {
    if (payload[key] !== undefined) return payload[key];
  }
  for (const key of keys) {
    if (event[key] !== undefined) return event[key];
  }
  return undefined;
}

function recordValue(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : {};
}

function todoListValue(value: unknown): ChatTodoItem[] {
  if (!Array.isArray(value)) return [];
  return value.filter(item => item && typeof item === 'object') as ChatTodoItem[];
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function clampProgress(value: number): number {
  return Math.min(Math.max(value, 0), 100);
}

function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

function contextLedgerValue(payload: UnknownRecord): ContextLedger {
  const systemBreakdown = recordValue(value(payload, {}, 'system_breakdown', 'systemBreakdown'));
  const schemaBreakdown = recordValue(value(payload, {}, 'schema_breakdown', 'schemaBreakdown'));
  return {
    systemTokens: numberValue(value(payload, {}, 'system_tokens', 'systemTokens')),
    schemaTokens: numberValue(value(payload, {}, 'schema_tokens', 'schemaTokens')),
    historyTokens: numberValue(value(payload, {}, 'history_tokens', 'historyTokens')),
    estimatedTotalTokens: numberValue(value(payload, {}, 'estimated_total_tokens', 'estimatedTotalTokens')),
    contextLimit: numberValue(value(payload, {}, 'context_limit', 'contextLimit')),
    contextRatio: numberValue(value(payload, {}, 'context_ratio', 'contextRatio')),
    cacheHitTokens: numberValue(value(payload, {}, 'cache_hit_tokens', 'cacheHitTokens')),
    cacheMissTokens: numberValue(value(payload, {}, 'cache_miss_tokens', 'cacheMissTokens')),
    cacheHitRate: numberValue(value(payload, {}, 'cache_hit_rate', 'cacheHitRate')),
    promptTokens: numberValue(value(payload, {}, 'prompt_tokens', 'promptTokens')),
    completionTokens: numberValue(value(payload, {}, 'completion_tokens', 'completionTokens')),
    totalTokens: numberValue(value(payload, {}, 'total_tokens', 'totalTokens')),
    messageCount: numberValue(value(payload, {}, 'message_count', 'messageCount')),
    toolCount: numberValue(value(payload, {}, 'tool_count', 'toolCount')),
    systemBreakdown: {
      systemPrompt: numberValue(value(systemBreakdown, {}, 'system_prompt', 'systemPrompt')),
      skills: numberValue(value(systemBreakdown, {}, 'skills')),
      memory: numberValue(value(systemBreakdown, {}, 'memory')),
    },
    schemaBreakdown: {
      mcp: numberValue(value(schemaBreakdown, {}, 'mcp')),
      builtin: numberValue(value(schemaBreakdown, {}, 'builtin')),
    },
  };
}

function isDoneTodoStatus(status: unknown): boolean {
  const value = stringValue(status).toLowerCase();
  return value === 'done' || value === 'completed' || value === 'complete';
}

function isActiveTodoStatus(status: unknown): boolean {
  const value = stringValue(status).toLowerCase();
  return value === 'in_progress' || value === 'active' || value === 'doing';
}

function runtimeStatus(input: {
  phase: string;
  message: string;
  toolName: string;
  callId: string;
  turn: number;
  toolCalls: number;
  timestamp: number;
  hint: string;
  recoverable: boolean;
}): RuntimeStatus {
  const severity = runtimeSeverity(input.phase);
  return {
    phase: input.phase,
    message: input.message,
    display: runtimeDisplay(input.phase, input.message, input.toolName),
    severity,
    toolName: input.toolName,
    callId: input.callId,
    turn: input.turn,
    toolCalls: input.toolCalls,
    updatedAt: Math.round(input.timestamp * 1000),
    hint: input.hint,
    recoverable: input.recoverable,
  };
}

function runtimeSeverity(phase: string): RuntimeStatus['severity'] {
  if (phase === 'failed') return 'error';
  if (phase === 'retrying' || phase === 'sse_reconnecting') return 'warning';
  if (phase === 'completed') return 'done';
  if (phase === 'tool_running' || phase === 'streaming' || phase === 'llm_request' || phase === 'compact_started') return 'working';
  return 'info';
}

function runtimeDisplay(phase: string, message: string, toolName: string): string {
  if (phase === 'starting') return '准备运行...';
  if (phase === 'llm_request') return '连接模型中...';
  if (phase === 'streaming') return '接收回复中...';
  if (phase === 'tool_running') return `运行工具 ${toolName || 'tool'}...`;
  if (phase === 'tool_done') return `工具 ${toolName || 'tool'} 已完成`;
  if (phase === 'compact_started') return '正在压缩上下文...';
  if (phase === 'compact_done') return '上下文已压缩';
  if (phase === 'retrying') return '正在重试...';
  if (phase === 'sse_reconnecting') return message || '事件流断开，正在重连...';
  if (phase === 'completed') return '已完成';
  if (phase === 'failed') return message ? `已失败: ${message}` : '已失败';
  return message || phase || '运行中...';
}
