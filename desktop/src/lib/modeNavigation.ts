import type { AppMode, SectionId } from './types';
import { switchSession } from './api';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';
import { useUiStore } from '../store/uiStore';

let navigationSeq = 0;
let sessionSwitchQueue: Promise<void> = Promise.resolve();

function suppressModeLayoutMotion(targetMode: AppMode): void {
  if (typeof document === 'undefined') return;
  document.querySelector<HTMLIFrameElement>('.mode-backdrop iframe')?.contentWindow?.postMessage({
    type: 'metis-backdrop-state',
    active: false,
    scene: targetMode,
  }, '*');
  document.documentElement.classList.add('metis-mode-switching');
  const release = () => document.documentElement.classList.remove('metis-mode-switching');
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => requestAnimationFrame(release));
  } else {
    setTimeout(release, 0);
  }
}

export function navigateAppMode(targetMode: AppMode, section: SectionId = 'chat'): void {
  const ui = useUiStore.getState();
  if (ui.appMode === targetMode) {
    ui.setActiveSection(section);
    return;
  }

  navigationSeq += 1;
  suppressModeLayoutMotion(targetMode);
  useChatStore.getState().clearLocal();
  useSessionStore.getState().prepareFreshModeDraft(targetMode);
  ui.setRightRailOpen?.(false);
  ui.setAppMode(targetMode);
  ui.setActiveSection(section);
}

export function navigateToSession(sessionId: string, targetMode: AppMode, section: SectionId = 'chat'): void {
  const ui = useUiStore.getState();
  const session = useSessionStore.getState();
  if (ui.appMode === targetMode && session.activeSessionId === sessionId) {
    ui.setActiveSection(section);
    return;
  }

  const seq = ++navigationSeq;
  suppressModeLayoutMotion(targetMode);
  useChatStore.getState().clearLocal();
  const target = session.prepareSessionSelection(targetMode, sessionId);
  ui.setAppMode(targetMode);
  ui.setActiveSection(section);

  if (!target.sessionId) return;
  void syncSelectedSession(seq, targetMode, target.sessionId);
}

async function syncSelectedSession(seq: number, mode: AppMode, sessionId: string): Promise<void> {
  await queueSessionSwitch(sessionId);
  if (seq !== navigationSeq || useUiStore.getState().appMode !== mode) return;
  await useChatStore.getState().loadSession(sessionId).catch(() => null);
}

function queueSessionSwitch(sessionId: string): Promise<void> {
  const operation = sessionSwitchQueue.then(async () => {
    await switchSession(sessionId).catch(() => null);
  });
  sessionSwitchQueue = operation.catch(() => undefined);
  return operation;
}
