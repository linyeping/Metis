import type { NormalizedChatEvent } from '../lib/agentEvents';
import type { ChatMessage, ChatRunRecoverySnapshot, CompactHandoffSnapshot, CompactStatusPayload, RuntimeStatus } from '../lib/types';
import {
  compactHandoffPreview,
  numberOrNow,
  normalizeSeverity,
  optionalRecoveryPreview,
  recoveryPreview,
} from './messageOps';

const RECOVERY_STORAGE_PREFIX = 'metis.chat.runRecovery.';
const COMPACT_HANDOFF_STORAGE_PREFIX = 'metis.chat.compactHandoff.';

interface RecoveryState {
  messages: ChatMessage[];
  runtimeStatus: RuntimeStatus | null;
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

function recoveryKey(sessionId: string): string {
  return `${RECOVERY_STORAGE_PREFIX}${sessionId}`;
}

function compactHandoffKey(sessionId: string): string {
  return `${COMPACT_HANDOFF_STORAGE_PREFIX}${sessionId}`;
}

export function persistCompactHandoff(sessionId: string, status: CompactStatusPayload, model: string): void {
  const store = storage();
  if (!store || !sessionId) return;
  const snapshot: CompactHandoffSnapshot = {
    sessionId,
    createdAt: Date.now(),
    beforeCount: status.beforeCount,
    afterCount: status.afterCount,
    summaryPreview: compactHandoffPreview(status.summaryPreview),
    model,
  };
  try {
    store.setItem(compactHandoffKey(sessionId), JSON.stringify(snapshot));
  } catch {}
}

export function readRecoverySnapshot(sessionId: string | null): ChatRunRecoverySnapshot | null {
  const store = storage();
  if (!store || !sessionId) return null;
  try {
    const parsed = JSON.parse(store.getItem(recoveryKey(sessionId)) || 'null') as Partial<ChatRunRecoverySnapshot> | null;
    if (!parsed || parsed.sessionId !== sessionId || !parsed.assistantId) return null;
    return {
      sessionId,
      assistantId: String(parsed.assistantId),
      startedAt: numberOrNow(parsed.startedAt),
      updatedAt: numberOrNow(parsed.updatedAt),
      phase: String(parsed.phase || 'streaming'),
      display: String(parsed.display || '上一次运行没有正常结束'),
      severity: normalizeSeverity(parsed.severity),
      toolCount: Math.max(0, Math.floor(Number(parsed.toolCount || 0))),
      preview: recoveryPreview(parsed.preview),
      canResume: parsed.canResume === undefined ? true : Boolean(parsed.canResume),
      checkpoint: recoveryPreview(parsed.checkpoint || parsed.display || parsed.preview),
      lastUserPreview: optionalRecoveryPreview(parsed.lastUserPreview),
      assistantPreview: optionalRecoveryPreview(parsed.assistantPreview || parsed.preview),
    };
  } catch {
    removeRecoverySnapshot(sessionId);
    return null;
  }
}

export function removeRecoverySnapshot(sessionId: string | null): void {
  const store = storage();
  if (!store || !sessionId) return;
  try {
    store.removeItem(recoveryKey(sessionId));
  } catch {}
}

function writeRecoverySnapshot(snapshot: ChatRunRecoverySnapshot): void {
  const store = storage();
  if (!store || !snapshot.sessionId) return;
  try {
    store.setItem(recoveryKey(snapshot.sessionId), JSON.stringify(snapshot));
  } catch {}
}

export function persistRecoverySnapshotFromState(
  sessionId: string | null,
  assistantId: string,
  state: RecoveryState,
  startedAt = Date.now(),
): void {
  const store = storage();
  if (!store || !sessionId) return;
  const assistant = state.messages.find(message => message.id === assistantId);
  const lastUser = [...state.messages].reverse().find(message => message.role === 'user');
  const previous = readRecoverySnapshot(sessionId);
  const status = state.runtimeStatus;
  const snapshot: ChatRunRecoverySnapshot = {
    sessionId,
    assistantId,
    startedAt: previous?.startedAt || startedAt || assistant?.createdAt || Date.now(),
    updatedAt: Date.now(),
    phase: status?.phase || (assistant?.tools?.length ? 'tool' : 'streaming'),
    display: status?.display || '正在生成回复',
    severity: status?.severity || 'working',
    toolCount: assistant?.tools?.length || previous?.toolCount || 0,
    preview: recoveryPreview(assistant?.content || status?.message || previous?.preview || ''),
    canResume: true,
    checkpoint: recoveryPreview(status?.display || status?.message || previous?.checkpoint || '正在生成回复'),
    lastUserPreview: optionalRecoveryPreview(lastUser?.content || previous?.lastUserPreview || ''),
    assistantPreview: optionalRecoveryPreview(assistant?.content || previous?.assistantPreview || ''),
  };
  writeRecoverySnapshot(snapshot);
}

export function persistBackgroundRunSnapshot(
  sessionId: string | null,
  assistantId: string,
  normalized: NormalizedChatEvent,
): void {
  if (!sessionId) return;
  if (normalized.kind === 'done') {
    removeRecoverySnapshot(sessionId);
    return;
  }
  const previous = readRecoverySnapshot(sessionId);
  const runtimeStatus = normalized.runtimeStatus;
  const toolEvent =
    normalized.kind === 'tool_call' ||
    normalized.kind === 'permission_request' ||
    normalized.kind === 'tool_result';
  const errorMessage = normalized.kind === 'error'
    ? [normalized.error.title, normalized.error.message, normalized.error.hint].filter(Boolean).join('\n\n') || 'Agent runtime error'
    : '';
  const textPreview = normalized.kind === 'text_delta' || normalized.kind === 'content_delta' || normalized.kind === 'content' ? normalized.text : '';
  const assistantPreview = optionalRecoveryPreview(`${previous?.assistantPreview || ''}${textPreview}`);
  const snapshot: ChatRunRecoverySnapshot = {
    sessionId,
    assistantId,
    startedAt: previous?.startedAt || Date.now(),
    updatedAt: Date.now(),
    phase: runtimeStatus?.phase || (errorMessage ? 'failed' : toolEvent ? 'tool' : previous?.phase || 'streaming'),
    display: runtimeStatus?.display || errorMessage || previous?.display || '后台运行中',
    severity: runtimeStatus?.severity || (errorMessage ? 'error' : previous?.severity || 'working'),
    toolCount: Math.max(previous?.toolCount || 0, toolEvent ? (previous?.toolCount || 0) + 1 : 0),
    preview: recoveryPreview(textPreview || previous?.preview || '此会话仍在后台接收回复。'),
    canResume: true,
    checkpoint: runtimeStatus?.display || errorMessage || previous?.checkpoint || '后台运行中',
    lastUserPreview: previous?.lastUserPreview || '',
    assistantPreview,
  };
  writeRecoverySnapshot(snapshot);
}
