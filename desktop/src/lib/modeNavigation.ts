import type { AppMode, SectionId } from './types';
import { switchSession } from './api';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';
import { useUiStore } from '../store/uiStore';

type ChatStoreState = ReturnType<typeof useChatStore.getState>;
type ChatVisualSnapshot = Pick<
  ChatStoreState,
  | 'messages'
  | 'composerText'
  | 'attachments'
  | 'streaming'
  | 'error'
  | 'runtimeStatus'
  | 'memoryNotice'
  | 'todoNotice'
  | 'awaySummary'
  | 'promptSuggestions'
  | 'compactStatus'
  | 'compacting'
  | 'subagents'
  | 'coworkPlan'
  | 'usage'
  | 'contextLedger'
  | 'loadedSessionId'
  | 'runSessionId'
  | 'pendingSendSessionId'
>;

interface ModeSnapshot {
  sessionId: string | null;
  state: ChatVisualSnapshot;
}

const modeSnapshots = new Map<AppMode, ModeSnapshot>();
let navigationSeq = 0;
let sessionSwitchQueue: Promise<void> = Promise.resolve();

export function navigateAppMode(targetMode: AppMode, section: SectionId = 'chat'): void {
  const ui = useUiStore.getState();
  const currentMode = ui.appMode;
  if (currentMode === targetMode) {
    ui.setActiveSection(section);
    return;
  }

  const seq = ++navigationSeq;
  let target: { sessionId: string | null; workspaceId: string; draft: boolean } | null = null;
  let restoredSnapshot = false;

  captureModeSnapshot(currentMode);
  applyModeChange(() => {
    const session = useSessionStore.getState();
    session.rememberModeState(currentMode);
    target = session.prepareModeSession(targetMode);
    restoredSnapshot = restoreModeSnapshot(targetMode, target.sessionId);
    const latestUi = useUiStore.getState();
    latestUi.setAppMode(targetMode);
    latestUi.setActiveSection(section);
  });

  scheduleAfterFirstPaint(() => {
    if (seq !== navigationSeq || !target?.sessionId) return;
    void syncModeSession(seq, targetMode, target.sessionId, restoredSnapshot);
  });
}

export function navigateToSession(sessionId: string, targetMode: AppMode, section: SectionId = 'chat'): void {
  const ui = useUiStore.getState();
  const currentMode = ui.appMode;
  if (currentMode === targetMode && useSessionStore.getState().activeSessionId === sessionId) {
    ui.setActiveSection(section);
    return;
  }
  const seq = ++navigationSeq;
  let target: { sessionId: string | null; workspaceId: string; draft: boolean } | null = null;
  let restoredSnapshot = false;

  captureModeSnapshot(currentMode);
  applyModeChange(() => {
    const session = useSessionStore.getState();
    session.rememberModeState(currentMode);
    target = session.prepareSessionSelection(targetMode, sessionId);
    restoredSnapshot = restoreModeSnapshot(targetMode, target.sessionId);
    const latestUi = useUiStore.getState();
    latestUi.setAppMode(targetMode);
    latestUi.setActiveSection(section);
  });

  scheduleAfterFirstPaint(() => {
    if (seq !== navigationSeq || !target?.sessionId) return;
    void syncModeSession(seq, targetMode, target.sessionId, restoredSnapshot);
  });
}

function captureModeSnapshot(mode: AppMode): void {
  const sessionId = useSessionStore.getState().activeSessionId;
  const chat = useChatStore.getState();
  modeSnapshots.set(mode, {
    sessionId,
    state: {
      messages: chat.messages,
      composerText: chat.composerText,
      attachments: chat.attachments,
      streaming: chat.streaming,
      error: chat.error,
      runtimeStatus: chat.runtimeStatus,
      memoryNotice: chat.memoryNotice,
      todoNotice: chat.todoNotice,
      awaySummary: chat.awaySummary,
      promptSuggestions: chat.promptSuggestions,
      compactStatus: chat.compactStatus,
      compacting: chat.compacting,
      subagents: chat.subagents,
      coworkPlan: chat.coworkPlan,
      usage: chat.usage,
      contextLedger: chat.contextLedger,
      loadedSessionId: chat.loadedSessionId,
      runSessionId: chat.runSessionId,
      pendingSendSessionId: chat.pendingSendSessionId,
    },
  });
}

function restoreModeSnapshot(mode: AppMode, sessionId: string | null): boolean {
  const snapshot = modeSnapshots.get(mode);
  if (snapshot && snapshot.sessionId === sessionId) {
    useChatStore.setState({ ...snapshot.state, controller: null });
    return true;
  }
  useChatStore.getState().clearLocal();
  return false;
}

function applyModeChange(update: () => void): void {
  // Zustand is an external store, so deferring this through startTransition
  // does not make the update interruptible. Apply it immediately so the
  // segmented control and mode shell respond in the click frame.
  update();
}

function scheduleAfterFirstPaint(callback: () => void): void {
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      window.setTimeout(callback, 40);
    });
  });
}

async function syncModeSession(seq: number, mode: AppMode, sessionId: string, restoredSnapshot: boolean): Promise<void> {
  // The renderer already prepared the target session from its local session
  // index. Only sync the backend pointer here; selectSession() also refreshes
  // sessions/workspaces and used to fan this into duplicate message loads.
  await queueSessionSwitch(sessionId);
  if (seq !== navigationSeq || useUiStore.getState().appMode !== mode) return;
  if (restoredSnapshot) {
    await useChatStore.getState().loadSession(sessionId, { force: true }).catch(() => null);
  }
}

function queueSessionSwitch(sessionId: string): Promise<void> {
  const operation = sessionSwitchQueue.then(async () => {
    await switchSession(sessionId).catch(() => null);
  });
  sessionSwitchQueue = operation.catch(() => undefined);
  return operation;
}
