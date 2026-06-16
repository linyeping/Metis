/**
 * sseParser — SSE 事件分发与文本批处理。
 *
 * 从 chatStore.ts 拆分。依赖 useChatStore 做 setState，
 * 但只在函数运行时引用，不在模块初始化时。
 */
import { normalizeChatStreamEvent, type NormalizedChatEvent } from '../lib/agentEvents';
import { answerToolPermission } from '../lib/api';
import { findSafeLocalPreviewUrl } from '../lib/webPreview';
import type { ChatMessage, ChatMessagePart, ChatStreamEvent, ChatSubagentEvent, ChatTodoNotice, ChatToolEvent, RuntimeStatus } from '../lib/types';
import { useUiStore } from './uiStore';
import {
  formatError,
  formatStreamError,
  normalizedSnapshotEvent,
  permissionDetails,
  recoveryPreview,
  optionalRecoveryPreview,
  runtimeStatusFromError,
  summarizeToolValue,
  toolErrorHint,
  toolId,
  toolResultStatus,
  toolText,
} from './messageOps';

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

const pendingPermissionDialogs = new Set<string>();
const pendingAssistantText = new Map<string, { sessionId: string | null; text: string }>();
let textFlushFrame: number | ReturnType<typeof globalThis.setTimeout> | null = null;

// ---------------------------------------------------------------------------
// Lazy store accessor — breaks circular import
// ---------------------------------------------------------------------------

type LazyStore = {
  getState: () => {
    messages: ChatMessage[];
    runtimeStatus: any;
    runSessionId: string | null;
    subagents: ChatSubagentEvent[];
    todoNotice: ChatTodoNotice | null;
  };
  setState: (partial: Record<string, unknown>) => void;
};

let _storeRef: LazyStore | null = null;

export function _bindChatStore(store: LazyStore): void {
  _storeRef = store;
}

function chatStore(): LazyStore {
  if (!_storeRef) throw new Error('sseParser: chatStore not bound. Call _bindChatStore first.');
  return _storeRef;
}

// Separate accessor for session store (also lazy)
type LazySessionStore = { getState: () => { activeSessionId: string | null } };
let _sessionStoreRef: LazySessionStore | null = null;

export function _bindSessionStore(store: LazySessionStore): void {
  _sessionStoreRef = store;
}

function sessionStore(): LazySessionStore {
  if (!_sessionStoreRef) throw new Error('sseParser: sessionStore not bound.');
  return _sessionStoreRef;
}

// ---------------------------------------------------------------------------
// Core event application
// ---------------------------------------------------------------------------

export function isActiveSession(sessionId: string | null): boolean {
  return Boolean(sessionId && sessionStore().getState().activeSessionId === sessionId);
}

export function applyChatEvent(
  event: ChatStreamEvent,
  assistantId: string,
  sessionId: string | null,
  persistSnapshot: (sessionId: string | null, assistantId: string, event: NormalizedChatEvent) => void,
  persistRecovery: (assistantId: string) => void,
): void {
  const normalized = normalizeChatStreamEvent(event);
  if (!isActiveSession(sessionId)) {
    persistSnapshot(sessionId, assistantId, normalized);
    return;
  }
  if (normalized.kind === 'text_delta' || normalized.kind === 'content_delta') {
    scheduleAssistantText(assistantId, normalized.text, sessionId, persistSnapshot);
  } else if (normalized.kind === 'content') {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    finalizeAssistantTextSegment(assistantId, normalized.text);
    persistRecovery(assistantId);
    maybeOpenLocalPreview(normalized.text);
  } else if (normalized.kind === 'thinking') {
    return;
  } else if (normalized.kind === 'runtime_status' && normalized.runtimeStatus) {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    setRuntimeStatus(normalized.runtimeStatus);
    persistRecovery(assistantId);
  } else if (normalized.kind === 'tool_call') {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    const now = Date.now();
    upsertTool(assistantId, {
      id: toolId(normalized, assistantId),
      callId: normalized.callId,
      toolName: normalized.toolName,
      args: normalized.args,
      status: 'running',
      startedAt: now,
      summary: summarizeToolValue(normalized.args),
    });
    setRuntimeStatus(toolRuntimeStatus(normalized, 'running'));
    persistRecovery(assistantId);
  } else if (normalized.kind === 'permission_request') {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    const now = Date.now();
    upsertTool(assistantId, {
      id: toolId(normalized, assistantId),
      callId: normalized.callId,
      requestId: normalized.requestId,
      toolName: normalized.toolName,
      args: normalized.args,
      status: 'waiting_approval',
      startedAt: now,
      summary: `等待确认 ${summarizeToolValue(normalized.args)}`,
    });
    setRuntimeStatus(toolRuntimeStatus(normalized, 'waiting_approval'));
    persistRecovery(assistantId);
    void requestToolPermission(normalized, assistantId);
  } else if (normalized.kind === 'tool_result') {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    const resultStatus = toolResultStatus(normalized.result);
    upsertTool(assistantId, {
      id: toolId(normalized, assistantId),
      callId: normalized.callId,
      toolName: normalized.toolName,
      result: normalized.result,
      status: resultStatus,
      finishedAt: Date.now(),
      summary: summarizeToolValue(normalized.result),
      errorHint: resultStatus === 'error' ? toolErrorHint(normalized.result) : '',
    });
    setRuntimeStatus(toolRuntimeStatus(normalized, resultStatus));
    persistRecovery(assistantId);
    maybeOpenLocalPreview(normalized.result);
  } else if (normalized.kind === 'error') {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    const message = formatStreamError(normalized);
    chatStore().setState({ runtimeStatus: runtimeStatusFromError(normalized.error) });
    updateAssistant(assistantId, current => ({ ...current, content: current.content || message, error: message }));
    useUiStore.getState().pushToast({
      title: normalized.error.title || '运行失败',
      description: normalized.error.message || message,
      action: normalized.error.hint,
      type: normalized.error.recoverable ? 'warning' : 'error',
      duration: normalized.error.recoverable ? 7000 : 0,
      sessionId,
    });
    persistRecovery(assistantId);
  } else if (normalized.kind === 'compact' && normalized.compactStatus) {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    chatStore().setState({ compactStatus: normalized.compactStatus });
  } else if (normalized.kind === 'done') {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    finalizeOpenTools(
      assistantId,
      chatStore().getState().runtimeStatus?.severity === 'error' ? 'error' : 'success',
    );
    maybeOpenLocalPreview(chatStore().getState().messages.find(message => message.id === assistantId)?.content || '');
    if (chatStore().getState().runtimeStatus?.severity !== 'error') {
      chatStore().setState({ runtimeStatus: null });
    }
    if (normalized.usage) {
      chatStore().setState({ usage: normalized.usage });
    }
    if (normalized.contextLedger) {
      chatStore().setState({ contextLedger: normalized.contextLedger });
    }
  } else if (normalized.kind === 'memory_nudge' && normalized.memory) {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    chatStore().setState({
      memoryNotice: {
        ...normalized.memory,
        createdAt: Date.now(),
      },
    });
  } else if (normalized.kind === 'todo_update' && normalized.todo) {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    const todoStatus = runtimeStatusFromTodo(normalized.todo);
    if (todoStatus) setRuntimeStatus(todoStatus);
    chatStore().setState({
      todoNotice: {
        ...normalized.todo,
        createdAt: Date.now(),
      },
      // 持久任务清单：供右侧 Plan 面板常驻显示，不随通知卡关闭而清空（FABLEADV-31）。
      planTodos: Array.isArray(normalized.todo.todos) ? normalized.todo.todos : [],
    });
  } else if (normalized.subagent) {
    flushAssistantText(assistantId, sessionId, persistSnapshot);
    upsertSubagent(normalized.subagent);
  }
}

export function handleRunEvent(
  runId: string,
  event: ChatStreamEvent,
  assistantId: string,
  sessionId: string | null,
  processedRunSeq: Map<string, number>,
  persistSnapshot: (sessionId: string | null, assistantId: string, event: NormalizedChatEvent) => void,
  persistRecovery: (assistantId: string) => void,
): void {
  const seq = Number(event.seq || 0);
  if (runId && seq > 0) {
    const previous = processedRunSeq.get(runId) || 0;
    if (seq <= previous) return;
    processedRunSeq.set(runId, seq);
  }
  applyChatEvent(event, assistantId, sessionId, persistSnapshot, persistRecovery);
}

// ---------------------------------------------------------------------------
// Assistant text batching
// ---------------------------------------------------------------------------

function scheduleAssistantText(
  messageId: string,
  delta: string,
  sessionId: string | null,
  persistSnapshot: (sessionId: string | null, assistantId: string, event: NormalizedChatEvent) => void,
): void {
  if (!delta) return;
  const pending = pendingAssistantText.get(messageId);
  pendingAssistantText.set(messageId, {
    sessionId: pending?.sessionId ?? sessionId,
    text: `${pending?.text ?? ''}${delta}`,
  });
  if (textFlushFrame !== null) return;

  const schedule =
    typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function'
      ? window.requestAnimationFrame
      : (callback: FrameRequestCallback) => globalThis.setTimeout(() => callback(Date.now()), 16);

  textFlushFrame = schedule(() => {
    textFlushFrame = null;
    flushAllAssistantText(persistSnapshot);
  });
}

export function flushAssistantText(
  messageId: string,
  sessionId?: string | null,
  persistSnapshot?: (sessionId: string | null, assistantId: string, event: NormalizedChatEvent) => void,
): void {
  const pending = pendingAssistantText.get(messageId);
  if (!pending?.text) return;
  pendingAssistantText.delete(messageId);
  const ownerSessionId = sessionId === undefined ? pending.sessionId : sessionId;
  if (!isActiveSession(ownerSessionId)) {
    persistSnapshot?.(ownerSessionId, messageId, normalizedSnapshotEvent({ kind: 'text_delta', text: pending.text }));
    return;
  }
  appendAssistantText(messageId, pending.text, ownerSessionId);
}

function flushAllAssistantText(
  persistSnapshot?: (sessionId: string | null, assistantId: string, event: NormalizedChatEvent) => void,
): void {
  for (const messageId of Array.from(pendingAssistantText.keys())) {
    flushAssistantText(messageId, undefined, persistSnapshot);
  }
}

// ---------------------------------------------------------------------------
// Assistant message updates
// ---------------------------------------------------------------------------

export function updateAssistant(messageId: string, updater: (message: ChatMessage) => ChatMessage): void {
  chatStore().setState({
    messages: chatStore().getState().messages.map(
      (message: ChatMessage) => (message.id === messageId ? updater(message) : message),
    ),
  });
}

function appendAssistantText(messageId: string, delta: string, sessionId: string | null): void {
  if (!delta) return;
  updateAssistant(messageId, message => {
    const parts = ensureParts(message);
    const last = parts.at(-1);
    const nextParts =
      last?.type === 'text'
        ? [...parts.slice(0, -1), { ...last, text: `${last.text}${delta}` }]
        : [...parts, { type: 'text' as const, text: delta }];
    return { ...message, content: textFromParts(nextParts), parts: nextParts };
  });
}

function finalizeAssistantTextSegment(messageId: string, text: string): void {
  if (!text) return;
  updateAssistant(messageId, message => {
    const parts = ensureParts(message);
    const last = parts.at(-1);
    const nextParts =
      last?.type === 'text'
        ? [...parts.slice(0, -1), { ...last, text }]
        : [...parts, { type: 'text' as const, text }];
    return { ...message, content: textFromParts(nextParts), parts: nextParts };
  });
}

// ---------------------------------------------------------------------------
// Tool and subagent upsert
// ---------------------------------------------------------------------------

function upsertTool(messageId: string, tool: ChatToolEvent): void {
  updateAssistant(messageId, message => {
    const tools = message.tools ?? [];
    const exactIndex = tools.findIndex(item => item.callId === tool.callId);
    const fallbackIndex = exactIndex === -1 ? findRunningToolByName(tools, tool) : -1;
    const index = exactIndex === -1 ? fallbackIndex : exactIndex;
    if (index === -1) {
      const nextTools = [...tools, tool];
      return { ...message, tools: nextTools, parts: appendToolPart(ensureParts(message), tool) };
    }
    const next = tools.slice();
    next[index] = mergeToolEvent(next[index], tool, exactIndex === -1);
    return { ...message, tools: next };
  });
}

function findRunningToolByName(tools: ChatToolEvent[], tool: ChatToolEvent): number {
  if (tool.status !== 'success' && tool.status !== 'error') return -1;
  for (let index = tools.length - 1; index >= 0; index -= 1) {
    const previous = tools[index];
    if (previous.toolName !== tool.toolName) continue;
    if (previous.status === 'running' || previous.status === 'waiting_approval') return index;
  }
  return -1;
}

function mergeToolEvent(previous: ChatToolEvent, incoming: ChatToolEvent, preserveIdentity: boolean): ChatToolEvent {
  const merged = { ...previous, ...incoming };
  if (!preserveIdentity) return merged;
  return {
    ...merged,
    id: previous.id,
    callId: previous.callId,
    requestId: incoming.requestId || previous.requestId,
    startedAt: incoming.startedAt || previous.startedAt,
  };
}

function finalizeOpenTools(messageId: string, status: 'success' | 'error'): void {
  const now = Date.now();
  updateAssistant(messageId, message => {
    const tools = message.tools ?? [];
    let changed = false;
    const nextTools = tools.map(tool => {
      if (tool.status !== 'running' && tool.status !== 'waiting_approval') return tool;
      changed = true;
      const result =
        tool.result ??
        (status === 'error'
          ? '[Run ended before this tool returned a result]'
          : '[Run completed without a separate tool result event]');
      return {
        ...tool,
        result,
        status,
        finishedAt: tool.finishedAt || now,
        summary: tool.summary || summarizeToolValue(result),
        errorHint:
          status === 'error'
            ? tool.errorHint || 'This run ended before the tool returned a separate result.'
            : tool.errorHint,
      };
    });
    return changed ? { ...message, tools: nextTools } : message;
  });
}

function ensureParts(message: ChatMessage): ChatMessagePart[] {
  if (message.parts?.length) return message.parts.slice();
  return message.content ? [{ type: 'text', text: message.content }] : [];
}

function appendToolPart(parts: ChatMessagePart[], tool: ChatToolEvent): ChatMessagePart[] {
  if (parts.some(part => part.type === 'tool' && part.callId === tool.callId)) return parts;
  return [...parts, { type: 'tool', toolId: tool.id, callId: tool.callId }];
}

function textFromParts(parts: ChatMessagePart[]): string {
  return parts
    .map(part => (part.type === 'text' ? part.text : ''))
    .join('');
}

function upsertSubagent(subagent: ChatSubagentEvent): void {
  const state = chatStore().getState() as { subagents: ChatSubagentEvent[] };
  const now = Date.now();
  const index = state.subagents.findIndex(item => item.taskId === subagent.taskId);
  const nextSubagent = (previous?: ChatSubagentEvent): ChatSubagentEvent => ({
    ...previous,
    ...subagent,
    progress: clampProgress(subagent.progress),
    startedAt: previous?.startedAt || subagent.startedAt || now,
    updatedAt: now,
    finishedAt:
      subagent.status === 'done' || subagent.status === 'error'
        ? previous?.finishedAt || subagent.finishedAt || now
        : previous?.finishedAt,
  });
  if (index === -1) {
    chatStore().setState({ subagents: [...state.subagents, nextSubagent()] });
  } else {
    const next = state.subagents.slice();
    next[index] = nextSubagent(next[index]);
    chatStore().setState({ subagents: next });
  }
}

function clampProgress(value: number): number {
  return Math.min(Math.max(value, 0), 100);
}

function setRuntimeStatus(status: RuntimeStatus): void {
  const previous = chatStore().getState().runtimeStatus as RuntimeStatus | null;
  const now = Date.now();
  const nextStatus =
    status.phase === 'tool_running' && previous?.phase === 'tool_running' && previous.callId === status.callId
      ? { ...status, display: previous.display, message: previous.message, hint: previous.hint }
      : status;
  const resetStart = !previous || status.phase === 'starting' || previous.severity === 'error' || previous.severity === 'done';
  chatStore().setState({
    runtimeStatus: {
      ...nextStatus,
      startedAt: resetStart ? now : previous.startedAt || now,
      updatedAt: now,
    },
  });
}

function toolRuntimeStatus(
  event: NormalizedChatEvent,
  status: ChatToolEvent['status'],
): RuntimeStatus {
  const summary = summarizeToolValue(status === 'running' || status === 'waiting_approval' ? event.args : event.result);
  const toolName = event.toolName || 'tool';
  const phase = status === 'error' ? 'tool_error' : status === 'success' ? 'tool_done' : status === 'waiting_approval' ? 'tool_waiting' : 'tool_running';
  const display =
    status === 'error'
      ? `工具失败 · ${toolName}`
      : status === 'success'
        ? `已完成 · ${toolName}`
        : status === 'waiting_approval'
          ? `等待确认 · ${toolName}`
          : `正在运行 · ${toolName}`;
  return {
    phase,
    message: summary,
    display: summary && status === 'running' ? `${display} · ${summary}` : display,
    severity: status === 'error' ? 'error' : status === 'success' ? 'working' : status === 'waiting_approval' ? 'warning' : 'working',
    toolName,
    callId: event.callId,
    hint: status === 'error' ? toolErrorHint(event.result) : '',
    recoverable: status !== 'error',
  };
}

function runtimeStatusFromTodo(todo: NonNullable<NormalizedChatEvent['todo']>): RuntimeStatus | null {
  if (!todo.todos.length) return null;
  const total = todo.todos.length;
  const current = todo.todos.find(item => {
    const status = String(item.status || '').toLowerCase();
    return status === 'in_progress' || status === 'active' || status === 'doing';
  });
  const currentText = String(current?.content || current?.task || current?.title || todo.summary || '').trim();
  return {
    phase: 'todo_progress',
    message: todo.summary,
    display: `[${todo.doneCount}/${total}] ${currentText || '推进任务清单'}`,
    severity: todo.doneCount >= total ? 'done' : 'working',
    toolName: 'todo_write',
    hint: '',
    recoverable: true,
  };
}

// ---------------------------------------------------------------------------
// Permission dialog
// ---------------------------------------------------------------------------

async function requestToolPermission(event: NormalizedChatEvent, assistantId: string): Promise<void> {
  if (!event.requestId || pendingPermissionDialogs.has(event.requestId)) return;
  pendingPermissionDialogs.add(event.requestId);

  try {
    const decision = await useUiStore.getState().requestChoice({
      title: '允许工具执行？',
      message: `Metis 想要运行工具 ${event.toolName}。请选择本次如何处理。`,
      details: permissionDetails(event),
      confirmLabel: '确认选择',
      cancelLabel: '仅本次拒绝',
      tone: 'danger',
      icon: 'warning',
      defaultChoice: 'once',
      choices: [
        {
          value: 'once',
          label: '仅本次允许',
          description: '允许这一次工具调用，不保存规则。',
        },
        {
          value: 'always_allow',
          label: '本工作区总是允许',
          description: '保存 allow 规则，下次同工具自动放行。',
        },
        {
          value: 'always_deny',
          label: '本工作区总是拒绝',
          description: '保存 deny 规则，下次同工具直接拒绝。',
        },
      ],
    });
    const approved = decision.confirmed && decision.choice !== 'always_deny';
    const remember = decision.confirmed
      ? decision.choice === 'always_allow'
        ? 'allow'
        : decision.choice === 'always_deny'
          ? 'deny'
          : ''
      : '';

    upsertTool(assistantId, {
      id: toolId(event, assistantId),
      callId: event.callId,
      requestId: event.requestId,
      toolName: event.toolName,
      args: event.args,
      status: approved ? 'running' : 'waiting_approval',
      summary: approved
        ? remember === 'allow'
          ? '已保存允许规则，等待工具结果'
          : '已允许，等待工具结果'
        : remember === 'deny'
          ? '已保存拒绝规则，等待后端确认'
          : '已拒绝，等待后端确认',
    });

    await answerToolPermission(event.requestId, approved, {
      remember,
      tool: event.toolName,
      args: event.args,
      callId: event.callId,
    });
  } catch (error) {
    upsertTool(assistantId, {
      id: toolId(event, assistantId),
      callId: event.callId,
      requestId: event.requestId,
      toolName: event.toolName,
      args: event.args,
      result: `Permission response failed: ${formatError(error)}`,
      status: 'error',
      finishedAt: Date.now(),
      summary: '权限响应失败',
      errorHint: '后端没有收到权限决定，请重试本次消息。',
    });
  } finally {
    pendingPermissionDialogs.delete(event.requestId);
  }
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

function maybeOpenLocalPreview(value: unknown): void {
  const url = findSafeLocalPreviewUrl(toolText(value));
  if (!url) return;
  useUiStore.getState().setWebPreviewUrl(url);
}
