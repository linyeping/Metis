import { startTransition } from 'react';
import type { AppMode, SectionId } from './types';
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

const modeOrder: Record<AppMode, number> = {
  chat: 0,
  cowork: 1,
  code: 2,
};

const modeSnapshots = new Map<AppMode, ModeSnapshot>();
let navigationSeq = 0;

export function navigateAppMode(targetMode: AppMode, section: SectionId = 'chat'): void {
  const ui = useUiStore.getState();
  const currentMode = ui.appMode;
  if (currentMode === targetMode) {
    ui.setActiveSection(section);
    return;
  }

  const seq = ++navigationSeq;
  const direction = Math.sign(modeOrder[targetMode] - modeOrder[currentMode]);
  let target: { sessionId: string | null; workspaceId: string; draft: boolean } | null = null;

  captureModeSnapshot(currentMode);
  runModeViewTransition(direction, () => {
    const session = useSessionStore.getState();
    session.rememberModeState(currentMode);
    target = session.prepareModeSession(targetMode);
    restoreModeSnapshot(targetMode, target.sessionId);
    const latestUi = useUiStore.getState();
    latestUi.setAppMode(targetMode);
    latestUi.setActiveSection(section);
  });

  scheduleAfterFirstPaint(() => {
    if (seq !== navigationSeq || !target?.sessionId) return;
    void syncModeSession(seq, targetMode, target.sessionId);
  });
}

export function navigateToSession(sessionId: string, targetMode: AppMode, section: SectionId = 'chat'): void {
  const ui = useUiStore.getState();
  const currentMode = ui.appMode;
  const seq = ++navigationSeq;
  const direction = currentMode === targetMode ? 0 : Math.sign(modeOrder[targetMode] - modeOrder[currentMode]);
  let target: { sessionId: string | null; workspaceId: string; draft: boolean } | null = null;

  captureModeSnapshot(currentMode);
  runModeViewTransition(direction, () => {
    const session = useSessionStore.getState();
    session.rememberModeState(currentMode);
    target = session.prepareSessionSelection(targetMode, sessionId);
    restoreModeSnapshot(targetMode, target.sessionId);
    const latestUi = useUiStore.getState();
    latestUi.setAppMode(targetMode);
    latestUi.setActiveSection(section);
  });

  scheduleAfterFirstPaint(() => {
    if (seq !== navigationSeq || !target?.sessionId) return;
    void syncModeSession(seq, targetMode, target.sessionId);
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
      usage: chat.usage,
      contextLedger: chat.contextLedger,
      loadedSessionId: chat.loadedSessionId,
      runSessionId: chat.runSessionId,
      pendingSendSessionId: chat.pendingSendSessionId,
    },
  });
}

function restoreModeSnapshot(mode: AppMode, sessionId: string | null): void {
  const snapshot = modeSnapshots.get(mode);
  if (snapshot && snapshot.sessionId === sessionId) {
    useChatStore.setState({ ...snapshot.state, controller: null });
    return;
  }
  useChatStore.getState().clearLocal();
}

function runModeViewTransition(direction: number, update: () => void): void {
  const root = document.documentElement;
  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  root.dataset.modeTransitionDirection = direction > 0 ? 'forward' : direction < 0 ? 'back' : 'neutral';
  root.dataset.modeTransitionPhase = 'prepare';

  // Keep this path compositor-only. Native View Transition snapshots are good
  // for small SPA pages, but in Metis they capture a large assistant thread,
  // iframes, and workspace panels, which is exactly what made mode switches
  // feel heavy in packaged Electron builds.
  startTransition(update);
  if (reducedMotion) {
    delete root.dataset.modeTransitionDirection;
    delete root.dataset.modeTransitionPhase;
    return;
  }

  window.requestAnimationFrame(() => {
    root.dataset.modeTransitionPhase = 'running';
    window.setTimeout(() => {
      delete root.dataset.modeTransitionDirection;
      delete root.dataset.modeTransitionPhase;
    }, 180);
  });
}

function scheduleAfterFirstPaint(callback: () => void): void {
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      window.setTimeout(callback, 40);
    });
  });
}

async function syncModeSession(seq: number, mode: AppMode, sessionId: string): Promise<void> {
  const session = useSessionStore.getState();
  await session.selectSession(sessionId).catch(() => null);
  if (seq !== navigationSeq || useUiStore.getState().appMode !== mode) return;
  await useChatStore.getState().loadSession(sessionId, { force: true }).catch(() => null);
}
