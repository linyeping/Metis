/**
 * chatStore — 核心对话状态管理。
 *
 * SSE 事件分发拆分到 sseParser.ts，
 * 纯工具函数拆分到 messageOps.ts。
 */
import { create } from 'zustand';
import {
  autoTitleSession,
  cancelChatRun,
  chatStream,
  compactConversation,
  getActiveSessionRun,
  getAwaySummary,
  getCompactStatus,
  getComposerDeepResearchEnabled,
  getPromptSuggestions,
  getSession,
  getSessionCheckpoints,
  parseUpload,
  rewindSession,
  runEventStream,
  startChatRun,
  undoTurn,
} from '../lib/api';
import { buildUserContent } from '../lib/chatUtils';
import type {
  ChatMemoryNotice,
  ChatMessage,
  ChatRunPayload,
  CompactStatusPayload,
  ChatRunRecoverySnapshot,
  ChatSubagentEvent,
  ChatTodoItem,
  ChatTodoNotice,
  ChatTokenUsage,
  ContextLedger,
  ParsedFile,
  RuntimeStatus,
  SessionCheckpoint,
} from '../lib/types';
import { useSessionStore } from './sessionStore';
import { useUiStore } from './uiStore';
import {
  attachmentReady,
  buildResumePrompt,
  buildUserDisplayContent,
  compactStatusFromError,
  formatError,
  isBackendBootingError,
  isRunApiUnavailable,
  messagesFromSession,
  normalizedSnapshotEvent,
  runtimeStatusFromError,
  shouldShowSingleUserHistoryNotice,
  singleUserHistoryNotice,
  TERMINAL_RUN_STATUSES,
  uploadDraft,
  uploadDraftPath,
} from './messageOps';
import {
  clearActiveRunController,
  getActiveRunController,
  hasActiveRunController,
  processedRunSeq,
  setActiveRunController,
} from './runManager';
import {
  persistBackgroundRunSnapshot,
  persistCompactHandoff,
  persistRecoverySnapshotFromState,
  readRecoverySnapshot,
  removeRecoverySnapshot,
} from './runRecovery';
import {
  _bindChatStore,
  _bindSessionStore,
  applyChatEvent,
  flushAssistantText,
  handleRunEvent,
  isActiveSession,
  updateAssistant,
} from './sseParser';

const PENDING_SEND_SESSION = '__pending_send_session__';

type RewindMode = 'conversation' | 'files' | 'both';

interface ChatState {
  messages: ChatMessage[];
  composerText: string;
  attachments: ParsedFile[];
  streaming: boolean;
  error: string | null;
  runtimeStatus: RuntimeStatus | null;
  memoryNotice: ChatMemoryNotice | null;
  todoNotice: ChatTodoNotice | null;
  planTodos: ChatTodoItem[];
  recoveryNotice: ChatRunRecoverySnapshot | null;
  awaySummary: string | null;
  promptSuggestions: string[];
  compactStatus: CompactStatusPayload | null;
  compacting: boolean;
  subagents: ChatSubagentEvent[];
  controller: AbortController | null;
  runSessionId: string | null;
  pendingSendSessionId: string | null;
  usage: ChatTokenUsage | null;
  contextLedger: ContextLedger | null;
  setComposerText: (value: string) => void;
  addFiles: (files: FileList | File[]) => Promise<void>;
  removeAttachment: (path: string) => void;
  clearAttachments: () => void;
  clearMemoryNotice: () => void;
  clearTodoNotice: () => void;
  dismissAwaySummary: () => void;
  applyPromptSuggestion: (value: string) => void;
  dismissRecoveryNotice: () => void;
  refreshCompactStatus: () => Promise<void>;
  compactContext: (model?: string) => Promise<void>;
  hydrateRecoverySnapshot: (sessionId: string | null) => void;
  markRecoveryFailed: () => void;
  resumeInterruptedRun: () => Promise<void>;
  clearRecoverySnapshot: () => void;
  clearSubagents: () => void;
  loadSession: (sessionId: string | null, options?: { force?: boolean }) => Promise<void>;
  rewindLatest: () => Promise<void>;
  rewindToMessage: (messageId: string) => Promise<void>;
  undoLastTurn: () => Promise<void>;
  send: (overrideText?: string) => Promise<void>;
  stop: () => void;
  clearLocal: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  composerText: '',
  attachments: [],
  streaming: false,
  error: null,
  runtimeStatus: null,
  memoryNotice: null,
  todoNotice: null,
  planTodos: [],
  recoveryNotice: null,
  awaySummary: null,
  promptSuggestions: [],
  compactStatus: null,
  compacting: false,
  subagents: [],
  controller: null,
  runSessionId: null,
  pendingSendSessionId: null,
  usage: null,
  contextLedger: null,
  setComposerText: composerText => set({ composerText }),
  addFiles: async files => {
    const incoming = Array.from(files).slice(0, 5);
    set(state => {
      const byPath = new Map(state.attachments.map(item => [item.path, item]));
      for (const file of incoming) {
        const draft = uploadDraft(file);
        byPath.set(draft.path, draft);
      }
      return { attachments: Array.from(byPath.values()).slice(0, 5) };
    });

    await Promise.all(
      incoming.map(async file => {
        const draftPath = uploadDraftPath(file);
        try {
          const parsed = await parseUpload(file);
          replaceAttachmentDraft(draftPath, { ...parsed, status: 'ready', error: '' });
        } catch (error) {
          replaceAttachmentDraft(draftPath, {
            ...uploadDraft(file),
            status: 'error',
            error: formatError(error),
          });
        }
      }),
    );
  },
  removeAttachment: path =>
    set(state => ({ attachments: state.attachments.filter(attachment => attachment.path !== path) })),
  clearAttachments: () => set({ attachments: [] }),
  clearMemoryNotice: () => set({ memoryNotice: null }),
  clearTodoNotice: () => set({ todoNotice: null }),
  dismissAwaySummary: () => set({ awaySummary: null }),
  applyPromptSuggestion: value => set({ composerText: value, promptSuggestions: [] }),
  dismissRecoveryNotice: () => set({ recoveryNotice: null }),
  refreshCompactStatus: async () => {
    try {
      set({ compactStatus: await getCompactStatus() });
    } catch (error) {
      if (isBackendBootingError(error)) {
        set({ compactStatus: null });
        return;
      }
      set({ compactStatus: compactStatusFromError(error) });
    }
  },
  compactContext: async (model = '') => {
    if (get().streaming || get().compacting) return;
    const sessionId = useSessionStore.getState().activeSessionId;
    if (!sessionId) return;
    set({ compacting: true, compactStatus: { running: true, ok: false, beforeCount: 0, afterCount: 0, summaryPreview: '正在压缩上下文...', updatedAt: Date.now() / 1000, error: '' } });
    try {
      const result = await compactConversation();
      set({ compactStatus: result });
      if (!result.ok) return;
      persistCompactHandoff(sessionId, result, model);
      await useSessionStore.getState().load();
      await get().loadSession(sessionId);
    } catch (error) {
      set({ compactStatus: compactStatusFromError(error) });
    } finally {
      set({ compacting: false });
    }
  },
  rewindLatest: async () => rewindToCheckpoint({}),
  rewindToMessage: async messageId => rewindToCheckpoint({ messageId }),
  undoLastTurn: async () => {
    const ui = useUiStore.getState();
    const sessionId = useSessionStore.getState().activeSessionId;
    if (!sessionId) return;
    if (get().streaming || hasActiveRunController(sessionId)) {
      ui.pushToast({ title: '当前会话正在运行', description: '请等待本轮完成或停止后再撤回。', type: 'warning', sessionId });
      return;
    }
    try {
      const result = await undoTurn(sessionId);
      if (!result.ok) {
        ui.pushToast({ title: '无法撤回', description: result.error || '没有可撤回的回合。', type: 'warning', sessionId });
        return;
      }
      await useSessionStore.getState().load();
      await get().loadSession(sessionId, { force: true });
      // Drop the user message back into the composer so it can be edited + resent.
      const existing = get().composerText.trim();
      set({ composerText: existing ? existing : result.userText });
    } catch (error) {
      ui.pushToast({ title: '撤回失败', description: formatError(error), type: 'error', sessionId });
    }
  },
  hydrateRecoverySnapshot: sessionId => set({ recoveryNotice: readRecoverySnapshot(sessionId) }),
  markRecoveryFailed: () => {
    const notice = get().recoveryNotice;
    if (!notice) return;
    removeRecoverySnapshot(notice.sessionId);
    set(state => ({
      recoveryNotice: null,
      runtimeStatus: {
        phase: 'failed',
        message: '上一次运行没有正常结束。',
        display: '已标记上一次运行失败',
        severity: 'error',
        toolName: '',
        hint: '可以重新发送这条需求，或打开诊断日志排查中断原因。',
        recoverable: false,
      },
      messages: state.messages.map(message =>
        message.id === notice.assistantId
          ? {
              ...message,
              pending: false,
              error: message.error || '上一次运行没有正常结束，已手动标记失败。',
              content: message.content || '上一次运行没有正常结束，已手动标记失败。',
            }
          : message,
      ),
    }));
  },
  resumeInterruptedRun: async () => {
    const notice = get().recoveryNotice;
    if (!notice || get().streaming) return;
    removeRecoverySnapshot(notice.sessionId);
    set({ recoveryNotice: null });
    await get().send(buildResumePrompt(notice));
  },
  clearRecoverySnapshot: () => {
    const sessionId = get().recoveryNotice?.sessionId || useSessionStore.getState().activeSessionId;
    removeRecoverySnapshot(sessionId);
    set({ recoveryNotice: null });
  },
  clearSubagents: () => set({ subagents: [] }),
  loadSession: async (sessionId, options = {}) => {
    useUiStore.getState().clearExpandedToolCards();
    if (!sessionId) {
      set({
        attachments: [],
        composerText: '',
        messages: [],
        memoryNotice: null,
        todoNotice: null,
        awaySummary: null,
        promptSuggestions: [],
        planTodos: [],
        recoveryNotice: null,
        compactStatus: null,
        runtimeStatus: null,
        subagents: [],
        usage: null,
        contextLedger: null,
      });
      return;
    }

    /*
     * Session message ownership:
     * - While a run is active for this session, local optimistic messages plus
     *   SSE events are the source of truth. Passive reloads must not replace
     *   `messages`, because the backend may not have persisted the newest user
     *   turn yet.
     * - After the run finishes, send()/attach cleanup reloads session metadata
     *   and the next idle loadSession may fully align with backend history.
     * - Explicit maintenance flows may pass force:true to bypass this guard.
     */
    if (sessionReloadBlocked(get(), sessionId, Boolean(options.force))) {
      return;
    }

    const session = await getSession(sessionId);
    const activeRun = await getActiveSessionRun(sessionId).catch(() => ({ ok: false, run: null }));
    const activeRunInfo = activeRun.run && !TERMINAL_RUN_STATUSES.has(activeRun.run.status) ? activeRun.run : null;
    const nextMessages = messagesFromSession(session);
    const recoverySnapshot = readRecoverySnapshot(sessionId);
    if (activeRunInfo && !nextMessages.some(message => message.id === activeRunInfo.assistantId)) {
      nextMessages.push({
        id: activeRunInfo.assistantId || `assistant-${activeRunInfo.runId}`,
        role: 'assistant',
        content: '',
        createdAt: (activeRunInfo.createdAt || Date.now() / 1000) * 1000 + 1,
        pending: true,
        tools: [],
      });
    }
    if (!activeRunInfo && shouldShowSingleUserHistoryNotice(nextMessages)) {
      nextMessages.push(singleUserHistoryNotice(session, recoverySnapshot));
    }
    if (sessionReloadBlocked(get(), sessionId, Boolean(options.force))) {
      return;
    }
    set({
      attachments: [],
      composerText: '',
      messages: nextMessages,
      memoryNotice: null,
      todoNotice: null,
      awaySummary: null,
      recoveryNotice: activeRunInfo ? null : recoverySnapshot,
      promptSuggestions: [],
      usage: null,
      contextLedger: null,
      runtimeStatus: null,
      subagents: [],
      streaming: Boolean(activeRunInfo),
      runSessionId: activeRunInfo ? sessionId : null,
      controller: activeRunInfo ? getActiveRunController(sessionId)?.controller || null : null,
    });
    if (activeRunInfo) {
      attachRunStream(activeRunInfo, sessionId);
    } else if (nextMessages.length > 0) {
      void refreshSessionHints(sessionId, { includeAway: true });
    }
  },
  send: async overrideText => {
    const text = (overrideText ?? get().composerText).trim();
    const allAttachments = get().attachments;
    const attachments = allAttachments.filter(attachment => attachmentReady(attachment));
    if (allAttachments.some(attachment => attachment.status === 'parsing')) return;
    let sessionId = useSessionStore.getState().activeSessionId;
    if (!text && attachments.length === 0) return;
    if (text.toLowerCase() === '/rewind' && attachments.length === 0) {
      set({ composerText: '' });
      await get().rewindLatest();
      return;
    }
    if (!sessionId) {
      set({ pendingSendSessionId: PENDING_SEND_SESSION });
      sessionId = await useSessionStore.getState().newSession();
      if (!sessionId) {
        set({
          pendingSendSessionId: null,
          error: 'Metis 无法创建新会话，消息未发送。',
          runtimeStatus: {
            phase: 'session_create_failed',
            message: 'Metis 无法创建新会话。',
            display: '无法创建会话',
            severity: 'error',
            toolName: '',
            hint: '请重试，或检查本地后端是否已正常启动。',
            recoverable: true,
          },
        });
        return;
      }
      set({ pendingSendSessionId: sessionId });
    }
    if (hasActiveRunController(sessionId)) {
      set({
        pendingSendSessionId: null,
        runtimeStatus: {
          phase: 'session_busy',
          message: '当前会话已有任务正在运行。',
          display: '当前会话正在运行',
          severity: 'warning',
          toolName: '',
          hint: '请等待完成、点击停止，或切换到其他会话继续工作。',
          recoverable: true,
        },
      });
      return;
    }

    const now = Date.now();
    const assistantId = `assistant-${now}`;
    const controller = new AbortController();
    const userContent = buildUserContent(text, attachments);
    const userDisplayContent = buildUserDisplayContent(text, attachments);

    set(state => ({
      attachments: [],
      composerText: '',
      controller,
      error: null,
      pendingSendSessionId: null,
      runSessionId: sessionId,
      runtimeStatus: null,
      streaming: true,
      usage: null,
      contextLedger: null,
      subagents: [],
      messages: [
        ...state.messages,
        {
          id: `user-${now}`,
          role: 'user',
          content: userDisplayContent,
          createdAt: now,
          attachments,
        },
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          createdAt: now + 1,
          pending: true,
          tools: [],
        },
      ],
    }));
    persistRecoverySnapshot(sessionId, assistantId, now);
    setActiveRunController(sessionId, { assistantId, controller, runId: '' });

    try {
      const deepResearch = await getComposerDeepResearchEnabled().catch(() => false);
      try {
        const run = await startChatRun({ message: userContent, session_id: sessionId, assistant_id: assistantId, deep_research: deepResearch });
        setActiveRunController(sessionId, { assistantId, controller, runId: run.runId });
        await runEventStream(run.runId, event => handleRunEvent(run.runId, event, assistantId, sessionId, processedRunSeq, persistBackgroundRunSnapshot, persistCurrentRecoverySnapshot), controller.signal);
      } catch (runError) {
        if (isRunApiUnavailable(runError)) {
          await chatStream({ message: userContent, session_id: sessionId, deep_research: deepResearch }, event => applyChatEvent(event, assistantId, sessionId, persistBackgroundRunSnapshot, persistCurrentRecoverySnapshot), controller.signal);
        } else {
          throw runError;
        }
      }
      flushAssistantText(assistantId, sessionId, persistBackgroundRunSnapshot);
      await autoTitleSession(sessionId).catch(() => null);
      await useSessionStore.getState().load();
      void refreshSessionHints(sessionId, { includeAway: false });
    } catch (error) {
      flushAssistantText(assistantId, sessionId, persistBackgroundRunSnapshot);
      if (!controller.signal.aborted) {
        const message = formatError(error);
        if (isActiveSession(sessionId)) {
          set({ error: message, runtimeStatus: runtimeStatusFromError({ message }) });
          updateAssistant(assistantId, current => ({ ...current, content: current.content || message, error: message }));
          useUiStore.getState().pushToast({
            title: '运行失败',
            description: message,
            type: 'error',
            duration: 0,
            sessionId,
          });
        } else {
          persistBackgroundRunSnapshot(sessionId, assistantId, normalizedSnapshotEvent({
            kind: 'error',
            error: { code: '', title: 'Agent runtime error', message, hint: '', recoverable: false },
          }));
        }
      }
    } finally {
      removeRecoverySnapshot(sessionId);
      clearActiveRunController(sessionId, assistantId);
      refreshActiveRunUiState();
      if (isActiveSession(sessionId)) {
        updateAssistant(assistantId, current => ({ ...current, pending: false }));
      }
    }
  },
  stop: () => {
    const sessionId = get().runSessionId || useSessionStore.getState().activeSessionId;
    const activeRun = getActiveRunController(sessionId);
    removeRecoverySnapshot(sessionId);
    if (activeRun?.runId) {
      void cancelChatRun(activeRun.runId).catch(() => null);
    }
    activeRun?.controller.abort();
    if (sessionId && activeRun) {
      clearActiveRunController(sessionId, activeRun.assistantId);
    }
    refreshActiveRunUiState();
    set({ runtimeStatus: null });
  },
  clearLocal: () =>
    set({
      messages: [],
      attachments: [],
      composerText: '',
      memoryNotice: null,
      todoNotice: null,
      recoveryNotice: null,
      awaySummary: null,
      promptSuggestions: [],
      compactStatus: null,
      usage: null,
      contextLedger: null,
      runtimeStatus: null,
      error: null,
      pendingSendSessionId: null,
    }),
}));

// Bind store references for sseParser (breaks circular import)
_bindChatStore(useChatStore);
_bindSessionStore(useSessionStore);

async function rewindToCheckpoint(target: { messageId?: string; checkpointId?: string }): Promise<void> {
  const ui = useUiStore.getState();
  const sessionId = useSessionStore.getState().activeSessionId;
  if (!sessionId) {
    ui.pushToast({ title: '没有可回滚的会话', description: '先打开一个已有会话，再使用 rewind。', type: 'warning' });
    return;
  }
  if (useChatStore.getState().streaming || hasActiveRunController(sessionId)) {
    ui.pushToast({ title: '当前会话正在运行', description: '请等待本轮完成或停止后再回滚。', type: 'warning', sessionId });
    return;
  }

  let checkpoints: SessionCheckpoint[] = [];
  try {
    checkpoints = await getSessionCheckpoints(sessionId);
  } catch (error) {
    ui.pushToast({ title: '读取 checkpoint 失败', description: formatError(error), type: 'error', sessionId });
    return;
  }
  const checkpoint = findRewindCheckpoint(checkpoints, target);
  if (!checkpoint) {
    ui.pushToast({
      title: '没有可用 checkpoint',
      description: target.messageId ? '这条消息还没有可回滚的快照，通常需要等待运行完成后再试。' : '当前会话还没有自动快照。',
      type: 'warning',
      sessionId,
    });
    return;
  }

  const affectedFiles = affectedFilesFrom(checkpoints, checkpoint.checkpointId);
  const details = rewindDetails(checkpoint, affectedFiles);
  const choice = await ui.requestChoice({
    title: target.messageId ? '回到这条消息之前？' : '回滚最近一次 checkpoint？',
    message:
      affectedFiles.length > 0
        ? `将从 checkpoint 开始恢复状态。文件模式会还原 ${affectedFiles.length} 个文件，请确认。`
        : '将从 checkpoint 开始恢复状态。这个 checkpoint 没有记录到文件改动。',
    details,
    choices: [
      { value: 'both', label: '对话 + 文件', description: '截回对话，并还原 checkpoint 后触碰过的文件。' },
      { value: 'conversation', label: '只回滚对话', description: '只隐藏 checkpoint 之后的对话，不改工作区文件。' },
      { value: 'files', label: '只回滚文件', description: '只还原文件，不改变当前对话历史。' },
    ],
    defaultChoice: 'both',
    confirmLabel: '回滚',
    cancelLabel: '取消',
    tone: 'danger',
    icon: 'warning',
  });
  if (!choice.confirmed) return;

  const mode = isRewindMode(choice.choice) ? choice.choice : 'both';
  try {
    const result = await rewindSession(sessionId, {
      checkpointId: checkpoint.checkpointId,
      messageId: target.messageId,
      mode,
    });
    if (mode === 'files' || mode === 'both') {
      ui.refreshWorkspaceView();
    }
    await useSessionStore.getState().load();
    await useChatStore.getState().loadSession(sessionId, { force: true });
    const restoredCount = result.restored.length;
    ui.pushToast({
      title: '已回滚',
      description:
        mode === 'conversation'
          ? `对话已截回到 ${result.historyLength} 条。`
          : `已还原 ${restoredCount} 个文件，对话${mode === 'both' ? '也已截回' : '保持不变'}。`,
      type: 'success',
      sessionId,
    });
  } catch (error) {
    ui.pushToast({ title: '回滚失败', description: formatError(error), type: 'error', duration: 0, sessionId });
  }
}

function findRewindCheckpoint(
  checkpoints: SessionCheckpoint[],
  target: { messageId?: string; checkpointId?: string },
): SessionCheckpoint | null {
  if (target.checkpointId) {
    return checkpoints.find(item => item.checkpointId === target.checkpointId) || null;
  }
  if (target.messageId) {
    return checkpoints.find(item => item.userMessageId === target.messageId) || null;
  }
  return checkpoints[0] || null;
}

function affectedFilesFrom(checkpoints: SessionCheckpoint[], checkpointId: string): string[] {
  const chronological = [...checkpoints].reverse();
  const start = chronological.findIndex(item => item.checkpointId === checkpointId);
  const selected = start >= 0 ? chronological.slice(start) : chronological;
  const seen = new Set<string>();
  for (const checkpoint of selected) {
    for (const file of checkpoint.files) {
      if (file.relativePath) seen.add(file.relativePath);
    }
  }
  return Array.from(seen);
}

function rewindDetails(checkpoint: SessionCheckpoint, affectedFiles: string[]): string {
  const lines = [
    `Checkpoint: ${checkpoint.checkpointId}`,
    `创建时间: ${formatCheckpointTime(checkpoint.createdAt)}`,
    `状态: ${checkpoint.status || 'unknown'}`,
    '',
  ];
  if (affectedFiles.length === 0) {
    lines.push('文件: 没有记录到文件改动');
  } else {
    lines.push(`将还原文件 (${affectedFiles.length}):`);
    lines.push(...affectedFiles.slice(0, 12).map(path => `- ${path}`));
    if (affectedFiles.length > 12) lines.push(`- 其余 ${affectedFiles.length - 12} 个文件...`);
  }
  return lines.join('\n');
}

function formatCheckpointTime(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return 'unknown';
  return new Date(value * 1000).toLocaleString();
}

function isRewindMode(value: string): value is RewindMode {
  return value === 'conversation' || value === 'files' || value === 'both';
}

function replaceAttachmentDraft(draftPath: string, nextAttachment: ParsedFile): void {
  useChatStore.setState(state => {
    let replaced = false;
    const attachments: ParsedFile[] = [];
    for (const attachment of state.attachments) {
      if (attachment.path === draftPath) {
        if (!attachments.some(item => item.path === nextAttachment.path)) {
          attachments.push(nextAttachment);
        }
        replaced = true;
      } else if (attachment.path !== nextAttachment.path) {
        attachments.push(attachment);
      }
    }
    if (!replaced && !attachments.some(item => item.path === nextAttachment.path)) {
      attachments.push(nextAttachment);
    }
    return { attachments: attachments.slice(0, 5) };
  });
}

function attachRunStream(run: ChatRunPayload, sessionId: string): void {
  if (!run.runId || TERMINAL_RUN_STATUSES.has(run.status)) return;
  const existing = getActiveRunController(sessionId);
  if (existing?.runId === run.runId) {
    refreshActiveRunUiState();
    return;
  }
  const assistantId = run.assistantId || `assistant-${run.runId}`;
  const controller = new AbortController();
  setActiveRunController(sessionId, { assistantId, controller, runId: run.runId });
  persistRecoverySnapshot(sessionId, assistantId, (run.createdAt || Date.now() / 1000) * 1000);
  refreshActiveRunUiState();
  void (async () => {
    try {
      await runEventStream(run.runId, event => handleRunEvent(run.runId, event, assistantId, sessionId, processedRunSeq, persistBackgroundRunSnapshot, persistCurrentRecoverySnapshot), controller.signal, processedRunSeq.get(run.runId) || 0);
      flushAssistantText(assistantId, sessionId, persistBackgroundRunSnapshot);
      await autoTitleSession(sessionId).catch(() => null);
      await useSessionStore.getState().load();
      void refreshSessionHints(sessionId, { includeAway: false });
    } catch (error) {
      flushAssistantText(assistantId, sessionId, persistBackgroundRunSnapshot);
      if (!controller.signal.aborted) {
        persistBackgroundRunSnapshot(sessionId, assistantId, normalizedSnapshotEvent({
          kind: 'error',
          error: { code: '', title: 'Agent runtime error', message: formatError(error), hint: '', recoverable: false },
        }));
      }
    } finally {
      removeRecoverySnapshot(sessionId);
      clearActiveRunController(sessionId, assistantId);
      refreshActiveRunUiState();
      if (isActiveSession(sessionId)) {
        updateAssistant(assistantId, current => ({ ...current, pending: false }));
      }
    }
  })();
}

function refreshActiveRunUiState(): void {
  const activeSessionId = useSessionStore.getState().activeSessionId;
  const activeRun = getActiveRunController(activeSessionId);
  useChatStore.setState({
    streaming: Boolean(activeRun),
    controller: activeRun?.controller || null,
    runSessionId: activeRun ? activeSessionId : null,
  });
}

async function refreshSessionHints(sessionId: string, options: { includeAway: boolean }): Promise<void> {
  if (!sessionId) return;
  const activeAtStart = useSessionStore.getState().activeSessionId;
  if (activeAtStart !== sessionId) return;
  const [suggestionsResult, awayResult] = await Promise.all([
    getPromptSuggestions(sessionId).catch(() => ({ ok: false, suggestions: [] })),
    options.includeAway ? getAwaySummary(sessionId).catch(() => ({ ok: false, summary: '' })) : Promise.resolve({ ok: false, summary: '' }),
  ]);
  if (useSessionStore.getState().activeSessionId !== sessionId) return;
  useChatStore.setState({
    promptSuggestions: suggestionsResult.suggestions || [],
    awaySummary: options.includeAway && awayResult.ok ? awayResult.summary || null : useChatStore.getState().awaySummary,
  });
}

function sessionReloadBlocked(state: ChatState, sessionId: string, force: boolean): boolean {
  if (force) return false;
  if (state.pendingSendSessionId === PENDING_SEND_SESSION || state.pendingSendSessionId === sessionId) {
    return true;
  }
  return state.runSessionId === sessionId && (state.streaming || hasActiveRunController(sessionId));
}

function persistCurrentRecoverySnapshot(assistantId: string): void {
  persistRecoverySnapshot(useChatStore.getState().runSessionId || useSessionStore.getState().activeSessionId, assistantId);
}

function persistRecoverySnapshot(sessionId: string | null, assistantId: string, startedAt = Date.now()): void {
  persistRecoverySnapshotFromState(sessionId, assistantId, useChatStore.getState(), startedAt);
}
