/**
 * messageOps — 消息解析、错误格式化、Recovery 快照等纯函数。
 *
 * 从 chatStore.ts 拆分，无 Zustand store 依赖。
 */
import { contentToText } from '../lib/chatUtils';
import { compactBoundaryContent } from '../lib/compactBoundary';
import type { NormalizedChatEvent } from '../lib/agentEvents';
import type {
  ChatMessage,
  ChatRunRecoverySnapshot,
  ChatToolEvent,
  CompactStatusPayload,
  ParsedFile,
  RuntimeStatus,
  Session,
} from '../lib/types';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const RECOVERY_PREVIEW_LIMIT = 180;
export const COMPACT_HANDOFF_PREVIEW_LIMIT = 900;
export const TERMINAL_RUN_STATUSES = new Set(['done', 'failed', 'canceled']);

// ---------------------------------------------------------------------------
// Error formatting
// ---------------------------------------------------------------------------

export function formatError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return 'Metis could not complete the request.';
}

export function formatStreamError(event: NormalizedChatEvent): string {
  return [event.error.title, event.error.message, event.error.hint].filter(Boolean).join('\n\n') || 'Agent runtime error';
}

export function runtimeStatusFromError(error: { code?: string; title?: string; message?: string; hint?: string }): RuntimeStatus {
  const title = error.title || friendlyErrorTitle(error.code) || '运行失败';
  return {
    phase: 'failed',
    message: error.message || title,
    display: `已失败: ${title}`,
    severity: 'error',
    toolName: '',
    hint: error.hint || friendlyErrorHint(error.code),
    recoverable: false,
  };
}

export function friendlyErrorTitle(code?: string): string {
  if (code === 'LLM_AUTH_FAILED') return 'API Key 验证失败';
  if (code === 'LLM_TLS_ERROR') return 'TLS/证书连接失败';
  if (code === 'LLM_NETWORK_ERROR') return '网络连接失败';
  if (code === 'LLM_TIMEOUT') return '请求超时';
  if (code === 'LLM_RATE_LIMITED') return '请求过于频繁';
  return '';
}

export function friendlyErrorHint(code?: string): string {
  if (code === 'LLM_AUTH_FAILED') return '检查供应商、Base URL、模型名和 API Key 是否匹配。';
  if (code === 'LLM_TLS_ERROR') return '检查代理/VPN、系统时间和证书拦截。';
  if (code === 'LLM_NETWORK_ERROR') return '检查网络、代理/VPN、Base URL 和防火墙。';
  if (code === 'LLM_TIMEOUT') return '稍后重试，或检查当前网络和模型服务状态。';
  if (code === 'LLM_RATE_LIMITED') return '稍等片刻后再试，或降低请求频率。';
  return '';
}

// ---------------------------------------------------------------------------
// Tool value helpers
// ---------------------------------------------------------------------------

export function toolText(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? '');
  }
}

export function summarizeToolValue(value: unknown): string {
  const text = toolText(value).replace(/\s+/g, ' ').trim();
  if (!text) return 'No details';
  return text.length > 180 ? `${text.slice(0, 180)}...` : text;
}

export function toolResultStatus(result: unknown): ChatToolEvent['status'] {
  const text = toolText(result).trim();
  const head = text.slice(0, 240).toLowerCase();
  if (!text) return 'success';
  if (
    text.startsWith('❌') ||
    /^error\b/i.test(text) ||
    head.includes('traceback') ||
    head.includes('exception') ||
    head.includes('permission denied') ||
    head.includes('access denied')
  ) {
    return 'error';
  }
  return 'success';
}

export function toolErrorHint(result: unknown): string {
  const text = toolText(result).toLowerCase();
  if (text.includes('permission denied') || text.includes('access denied')) return '检查文件/目录权限，或换用允许访问的位置。';
  if (text.includes('not found') || text.includes('no such file')) return '检查路径是否存在，必要时先列目录确认。';
  if (text.includes('traceback') || text.includes('exception')) return '查看右栏完整输出，按异常栈定位失败原因。';
  return '查看右栏完整输出，修正参数后再试。';
}

export function permissionDetails(event: NormalizedChatEvent): string {
  return [`工具: ${event.toolName}`, `请求: ${event.requestId}`, '参数:', toolText(event.args)].join('\n');
}

export function toolId(event: NormalizedChatEvent, messageId: string): string {
  return `${messageId}-${event.callId}`;
}

// ---------------------------------------------------------------------------
// Session message parsing
// ---------------------------------------------------------------------------

export function messagesFromSession(session: Session): ChatMessage[] {
  const boundaryIndex = compactBoundaryIndex(session);
  const messages: ChatMessage[] = [];
  // FABLEADV-16: transcript-only tool records (metis_kind=tool) are aggregated
  // and attached to the next assistant message's tools[] so tool cards rebuild
  // after reload/compaction instead of vanishing.
  let pendingTools: ChatToolEvent[] = [];
  const flushPendingTools = (index: number) => {
    if (pendingTools.length === 0) return;
    messages.push({
      id: `${session.id}-${index}-tools`,
      role: 'assistant',
      content: '',
      createdAt: (session.createdAt + index / 1000) * 1000,
      tools: pendingTools,
    });
    pendingTools = [];
  };
  session.history.forEach((message, index) => {
    if (index === boundaryIndex) {
      flushPendingTools(index);
      messages.push(compactBoundaryMessage(session, boundaryIndex));
    }
    if (message.metis_kind === 'tool' && message.metis_tool) {
      pendingTools.push(toolRecordToEvent(message, index));
      return;
    }
    if (message.role === 'user' || message.role === 'assistant' || message.role === 'tool' || message.role === 'system') {
      const role = message.role === 'tool' ? 'assistant' : message.role;
      const content = contentToText(message.content);
      const parsedUserContent = message.role === 'user' ? parseUserAttachmentContent(content, index) : null;
      if (role === 'assistant' && pendingTools.length > 0) {
        // Attach accumulated tool cards to this assistant turn.
        messages.push({
          id: message.id || `${session.id}-${index}-${role}`,
          role,
          content: parsedUserContent?.content ?? content,
          createdAt: (session.createdAt + index / 1000) * 1000,
          tools: pendingTools,
        });
        pendingTools = [];
        return;
      }
      flushPendingTools(index);
      messages.push({
        id: message.id || `${session.id}-${index}-${role}`,
        role,
        content: message.role === 'tool' ? `Tool result${message.name ? ` (${message.name})` : ''}\n\n${content}` : parsedUserContent?.content ?? content,
        createdAt: (session.createdAt + index / 1000) * 1000,
        attachments: parsedUserContent?.attachments.length ? parsedUserContent.attachments : undefined,
      });
    }
  });
  flushPendingTools(session.history.length);
  if (boundaryIndex >= session.history.length && boundaryIndex >= 0) {
    messages.push(compactBoundaryMessage(session, boundaryIndex));
  }
  return messages;
}

function toolRecordToEvent(message: Session['history'][number], index: number): ChatToolEvent {
  const tool = message.metis_tool || {};
  const status = tool.status === 'error' || tool.status === 'running' || tool.status === 'waiting_approval'
    ? tool.status
    : 'success';
  return {
    id: message.id || `tool-${index}`,
    callId: tool.call_id || `tool-${index}`,
    toolName: tool.name || 'tool',
    args: tool.arguments,
    result: tool.result,
    status,
  };
}

function compactBoundaryIndex(session: Session): number {
  const state = session.compactState;
  if (!state?.summary) return -1;
  if (state.boundaryMessageId) {
    const byId = session.history.findIndex(message => message.id === state.boundaryMessageId);
    if (byId >= 0) return byId;
  }
  const rawIndex = Number.isFinite(state.boundaryIndex) ? Math.floor(state.boundaryIndex) : 0;
  return Math.max(0, Math.min(rawIndex, session.history.length));
}

function compactBoundaryMessage(session: Session, boundaryIndex: number): ChatMessage {
  const summary = session.compactState?.summary || '';
  return {
    id: `${session.id}-compact-boundary-${session.compactState?.compactCount || 1}-${boundaryIndex}`,
    role: 'system',
    content: compactBoundaryContent(summary),
    createdAt: (session.createdAt + Math.max(0, boundaryIndex) / 1000) * 1000 - 0.5,
  };
}

export function shouldShowSingleUserHistoryNotice(messages: ChatMessage[]): boolean {
  if (messages.length !== 1) return false;
  return messages[0].role === 'user';
}

export function singleUserHistoryNotice(session: Session, recovery: ChatRunRecoverySnapshot | null): ChatMessage {
  const preview = (recovery?.assistantPreview || recovery?.preview || '').trim();
  return {
    id: `${session.id}-single-user-history-notice`,
    role: 'system',
    content: preview
      ? `上一次回复没有完整保存到会话历史。恢复预览：${preview}`
      : '这个会话目前只保存了你的消息，没有保存到 AI 回复。可以重新发送这条需求，或打开后台任务/诊断日志确认是否中断。',
    createdAt: (session.updatedAt || session.createdAt || Date.now() / 1000) * 1000 + 2,
  };
}

export function buildUserDisplayContent(text: string, attachments: ParsedFile[]): string {
  if (text) return text;
  return attachments.length > 0 ? '请分析附件。' : '';
}

function parseUserAttachmentContent(content: string, index: number): { content: string; attachments: ParsedFile[] } {
  const attachmentPattern = /\n{2,}\[Attachment: ([^\]\n]+)\]\n([\s\S]*?)(?=\n{2,}\[Attachment: [^\]\n]+\]\n|$)/g;
  const attachments: ParsedFile[] = [];
  let firstAttachmentIndex = -1;
  let match: RegExpExecArray | null;

  while ((match = attachmentPattern.exec(content)) !== null) {
    if (firstAttachmentIndex === -1) firstAttachmentIndex = match.index;
    const name = match[1].trim() || 'attachment';
    const text = match[2] || '';
    attachments.push({
      path: `history-attachment-${index}-${attachments.length}-${name}`,
      name,
      extension: attachmentExtension(name),
      size: text.length,
      kind: 'document',
      mime: 'text/plain',
      text: '',
      status: 'ready',
      truncated: /\[\.\.\.truncated,/i.test(text),
    });
  }

  if (attachments.length === 0) {
    return { content, attachments: [] };
  }

  const visibleContent = content.slice(0, firstAttachmentIndex).trim();
  return {
    content: visibleContent && visibleContent !== 'Please use the attached files.' ? visibleContent : '请分析附件。',
    attachments,
  };
}

function attachmentExtension(name: string): string {
  const lastDot = name.lastIndexOf('.');
  return lastDot > -1 ? name.slice(lastDot).toLowerCase() : '';
}

// ---------------------------------------------------------------------------
// Upload helpers
// ---------------------------------------------------------------------------

export function uploadDraftPath(file: File): string {
  const nativePath = window.metis?.getPathForFile(file) || '';
  return nativePath || `${file.name || 'attachment'}-${file.size}-${file.lastModified || 0}`;
}

export function uploadDraft(file: File): ParsedFile {
  const name = file.name || 'attachment';
  const extension = name.includes('.') ? `.${name.split('.').pop() || ''}`.toLowerCase() : '';
  return {
    path: uploadDraftPath(file),
    name,
    extension,
    size: file.size,
    kind: file.type.startsWith('image/') ? 'image' : 'document',
    mime: file.type || 'application/octet-stream',
    text: '',
    status: 'parsing',
  };
}

export function attachmentReady(attachment: ParsedFile): boolean {
  return !attachment.status || attachment.status === 'ready';
}

// ---------------------------------------------------------------------------
// Recovery snapshot helpers
// ---------------------------------------------------------------------------

export function numberOrNow(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : Date.now();
}

export function normalizeSeverity(value: unknown): ChatRunRecoverySnapshot['severity'] {
  return value === 'info' || value === 'working' || value === 'warning' || value === 'error' || value === 'done' ? value : 'warning';
}

export function recoveryPreview(value: unknown): string {
  const text = typeof value === 'string' ? value : String(value ?? '');
  const redacted = text
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, 'sk-***')
    .replace(/(api[_-]?key\s*[:=]\s*)[^\s"'`,;]+/gi, '$1***')
    .replace(/\s+/g, ' ')
    .trim();
  return (redacted || '上一次回复仍在生成中').slice(0, RECOVERY_PREVIEW_LIMIT);
}

export function optionalRecoveryPreview(value: unknown): string {
  const text = typeof value === 'string' ? value : String(value ?? '');
  if (!text.trim()) return '';
  return recoveryPreview(text);
}

export function buildResumePrompt(notice: ChatRunRecoverySnapshot): string {
  const lines = [
    '继续上一次中断的任务。',
    '',
    `中断点: ${notice.checkpoint || notice.display || notice.phase || '未知'}`,
    `上次阶段: ${notice.phase || 'streaming'}`,
    `已观察到的工具调用数: ${notice.toolCount}`,
  ];
  if (notice.lastUserPreview) {
    lines.push(`用户原始需求摘要: ${notice.lastUserPreview}`);
  }
  if (notice.assistantPreview || notice.preview) {
    lines.push(`中断前输出摘要: ${notice.assistantPreview || notice.preview}`);
  }
  lines.push('', '请先用一句话确认你理解的中断点，然后在不重复已完成工作的前提下继续执行。');
  return lines.join('\n').slice(0, 1200);
}

export function compactHandoffPreview(value: unknown): string {
  const text = typeof value === 'string' ? value : String(value ?? '');
  return text
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, 'sk-***')
    .replace(/(api[_-]?key\s*[:=]\s*)[^\s"'`,;]+/gi, '$1***')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, COMPACT_HANDOFF_PREVIEW_LIMIT);
}

export function compactStatusFromError(error: unknown): CompactStatusPayload {
  return {
    running: false,
    ok: false,
    beforeCount: 0,
    afterCount: 0,
    summaryPreview: '',
    updatedAt: Date.now() / 1000,
    error: formatError(error),
  };
}

export function isBackendBootingError(error: unknown): boolean {
  return formatError(error).includes('Metis backend is not ready yet.');
}

export function isRunApiUnavailable(error: unknown): boolean {
  const message = formatError(error).toLowerCase();
  return message.includes('http 404') || message.includes('run not found') || message.includes('route not found') || message.includes('/runs');
}

// ---------------------------------------------------------------------------
// normalizedSnapshotEvent — 用于后台 run 快照持久化
// ---------------------------------------------------------------------------

export function normalizedSnapshotEvent(event: Partial<NormalizedChatEvent> & { kind: NormalizedChatEvent['kind'] }): NormalizedChatEvent {
  return {
    kind: event.kind,
    text: event.text || '',
    toolName: event.toolName || 'tool',
    args: event.args,
    result: event.result,
    callId: event.callId || `snapshot-${Date.now()}`,
    requestId: event.requestId || '',
    error: {
      code: event.error?.code || '',
      title: event.error?.title || '',
      message: event.error?.message || '',
      hint: event.error?.hint || '',
      recoverable: event.error?.recoverable ?? false,
    },
    usage: event.usage || null,
    contextLedger: event.contextLedger || null,
    runtimeStatus: event.runtimeStatus || null,
    compactStatus: event.compactStatus || null,
    memory: event.memory || null,
    todo: event.todo || null,
    subagent: event.subagent || null,
  };
}
