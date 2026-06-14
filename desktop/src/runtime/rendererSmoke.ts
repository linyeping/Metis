import {
  createPermissionRule,
  deletePermissionRule,
  getComposerPermissionMode,
  getPermissions,
  getProviderModels,
  getProviderStatus,
  getProviderUsage,
  getSettings,
  getChatRun,
  getChatRuns,
  cancelChatRun,
  startChatRun,
  setComposerPermissionMode,
  verifyProviderConfig,
} from '../lib/api';
import type { ChatMessage, PermissionStatePayload } from '../lib/types';
import { findSafeLocalPreviewUrl, isPreviewableWebFilePath, localFilePreviewUrl } from '../lib/webPreview';
import { useChatStore } from '../store/chatStore';
import { useSessionStore } from '../store/sessionStore';
import { useSideChatStore } from '../store/sideChatStore';
import { useUiStore } from '../store/uiStore';

interface SmokeCheck {
  name: string;
  ok: boolean;
  detail?: string;
}

interface SmokeReport {
  ok: boolean;
  checks: SmokeCheck[];
  error?: string;
}

const SMOKE_TIMEOUT_MS = 12000;

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function asErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.stack || error.message;
  if (typeof error === 'string') return error;
  return String(error);
}

function lastAssistant() {
  const assistants = useChatStore.getState().messages.filter(message => message.role === 'assistant');
  return assistants.at(-1) ?? null;
}

function record(checks: SmokeCheck[], name: string, ok: boolean, detail?: string): void {
  checks.push({ name, ok, detail });
  if (!ok) {
    throw new Error(`${name}${detail ? `: ${detail}` : ''}`);
  }
}

async function waitForBoot(checks: SmokeCheck[]): Promise<number> {
  if (!window.metis) {
    record(checks, 'boot-ready', false, 'window.metis is missing');
    return 0;
  }

  const deadline = Date.now() + SMOKE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const state = await window.metis.bootState();
    if (state.status === 'ready' && state.port) {
      record(checks, 'boot-ready', true, `127.0.0.1:${state.port}`);
      return state.port;
    }
    if (state.status === 'error') {
      record(checks, 'boot-ready', false, state.error?.detail || state.error?.title || 'boot error');
      return 0;
    }
    await delay(80);
  }

  record(checks, 'boot-ready', false, 'timed out waiting for fake backend');
  return 0;
}

async function prepareStores(checks: SmokeCheck[]): Promise<void> {
  await useSessionStore.getState().load();
  const sessionState = useSessionStore.getState();
  record(checks, 'session-load', sessionState.activeSessionId === 'smoke-session', sessionState.activeSessionId || 'missing active session');
  record(
    checks,
    'workspace-load',
    sessionState.activeWorkspaceId === 'smoke-workspace',
    sessionState.activeWorkspaceId || 'missing active workspace',
  );
  useChatStore.getState().clearLocal();
  useChatStore.getState().clearMemoryNotice();
  useChatStore.getState().clearSubagents();
}

async function sendSmokeMessage(text: string): Promise<void> {
  useChatStore.getState().clearLocal();
  useChatStore.getState().clearMemoryNotice();
  await useChatStore.getState().send(text);
}

async function sendSmokeMessageAndCaptureStatus(
  text: string,
  predicate: (display: string) => boolean,
): Promise<string> {
  useChatStore.getState().clearLocal();
  useChatStore.getState().clearMemoryNotice();
  const capture = captureRuntimeStatus(predicate);
  const sendPromise = useChatStore.getState().send(text);
  try {
    const display = await capture.promise;
    await sendPromise;
    return display;
  } catch (error) {
    await sendPromise.catch(() => null);
    throw error;
  } finally {
    capture.cancel();
  }
}

async function waitForRuntimeStatus(predicate: (display: string) => boolean): Promise<string> {
  return captureRuntimeStatus(predicate).promise;
}

function captureRuntimeStatus(predicate: (display: string) => boolean): { promise: Promise<string>; cancel: () => void } {
  let timeoutId = 0;
  let unsubscribe: (() => void) | null = null;
  let settled = false;

  const cleanup = () => {
    if (timeoutId) {
      window.clearTimeout(timeoutId);
      timeoutId = 0;
    }
    unsubscribe?.();
    unsubscribe = null;
  };

  const promise = new Promise<string>((resolve, reject) => {
    const finish = (display?: string, error?: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) {
        reject(error);
        return;
      }
      resolve(display || '');
    };
    const inspect = () => {
      const status = useChatStore.getState().runtimeStatus;
      if (status && predicate(status.display)) {
        finish(status.display);
      }
    };
    unsubscribe = useChatStore.subscribe(inspect);
    inspect();
    timeoutId = window.setTimeout(() => {
      const chat = useChatStore.getState();
      const sessions = useSessionStore.getState();
      finish(
        undefined,
        new Error(
          `timed out waiting for runtime status (${JSON.stringify({
            activeSessionId: sessions.activeSessionId,
            messageCount: chat.messages.length,
            runSessionId: chat.runSessionId,
            runtimeStatus: chat.runtimeStatus?.display || null,
            streaming: chat.streaming,
          })})`,
        ),
      );
    }, SMOKE_TIMEOUT_MS);
  });

  return { promise, cancel: cleanup };
}

async function waitForRunTerminal(runId: string): Promise<string> {
  const deadline = Date.now() + SMOKE_TIMEOUT_MS;
  let status = '';
  while (Date.now() < deadline) {
    const run = await getChatRun(runId);
    status = run.status;
    if (status === 'done' || status === 'failed' || status === 'canceled') {
      return status;
    }
    await delay(80);
  }
  throw new Error(`timed out waiting for run ${runId} to finish (${status || 'unknown'})`);
}

async function cancelActiveRuns(sessionId: string): Promise<void> {
  const terminalStatuses = new Set(['done', 'failed', 'canceled']);
  const payload = await getChatRuns(sessionId).catch(() => ({ runs: [] }));
  const activeRuns = payload.runs.filter(run => run.runId && !terminalStatuses.has(run.status));
  await Promise.all(
    activeRuns.map(async run => {
      await cancelChatRun(run.runId).catch(() => null);
      await waitForRunTerminal(run.runId).catch(() => null);
    }),
  );
}

async function waitForCondition(predicate: () => boolean, detail: string): Promise<void> {
  const deadline = Date.now() + SMOKE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await delay(20);
  }
  throw new Error(`timed out waiting for ${detail}`);
}

async function waitForAppDialog(detail: string): Promise<HTMLElement> {
  await waitForCondition(() => Boolean(document.querySelector('.app-dialog')), detail);
  const dialog = document.querySelector<HTMLElement>('.app-dialog');
  if (!dialog) throw new Error(`missing app dialog for ${detail}`);
  return dialog;
}

function clickAppDialogConfirm(): void {
  const button = document.querySelector<HTMLButtonElement>('.app-dialog-confirm-button');
  if (!button) throw new Error('missing app dialog confirm button');
  button.click();
}

function clickAppDialogCancel(): void {
  const button = document.querySelector<HTMLButtonElement>('.app-dialog-cancel-button');
  if (!button) throw new Error('missing app dialog cancel button');
  button.click();
}

function selectAppDialogChoice(value: string): void {
  const input = document.querySelector<HTMLInputElement>(`.app-dialog-choices input[value="${value}"]`);
  if (!input) throw new Error(`missing app dialog choice ${value}`);
  input.click();
}

function hasTransition(element: Element | null): boolean {
  if (!(element instanceof HTMLElement)) return false;
  const style = window.getComputedStyle(element);
  const durations = style.transitionDuration.split(',').map(value => value.trim());
  return durations.some(value => value !== '0s' && value !== '0ms');
}

function setInputValue(input: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function setTextareaValue(input: HTMLTextAreaElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function setSelectValue(input: HTMLSelectElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value')?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

async function verifyCommandPalette(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setLanguage('zh');
  ui.setCommandOpen(false);
  await delay(20);

  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
  await waitForCondition(() => useUiStore.getState().commandOpen, 'command palette open');

  const palette = document.querySelector('.command-palette');
  const text = palette?.textContent || '';
  record(checks, 'command-palette-open', Boolean(palette), text || 'missing command palette');
  record(checks, 'command-palette-basic-command', text.includes('新建会话') && text.includes('切换模型'), text);
  record(
    checks,
    'new94-command-center-tabs-visible',
    Boolean(document.querySelector('.command-center-tabs')) && text.includes('Command Center'),
    text,
  );

  const systemTab = Array.from(document.querySelectorAll<HTMLButtonElement>('.command-center-tabs button')).find(button =>
    button.textContent?.includes('系统'),
  );
  systemTab?.click();
  await waitForCondition(() => Boolean(document.querySelector('.command-system-panel')), 'NEW-94 command center system tab');
  const systemText = document.querySelector('.command-center-body')?.textContent || '';
  record(
    checks,
    'new94-command-center-system-tab',
    systemText.includes('后端日志') && systemText.includes('生成诊断包'),
    systemText,
  );

  const providerTab = Array.from(document.querySelectorAll<HTMLButtonElement>('.command-center-tabs button')).find(button =>
    button.textContent?.includes('模型'),
  );
  providerTab?.click();
  await waitForCondition(() => (document.querySelector('.command-center-body')?.textContent || '').includes('Provider 健康'), 'NEW-94 command center provider tab');
  const providerText = document.querySelector('.command-center-body')?.textContent || '';
  record(
    checks,
    'new94-command-center-provider-tab',
    providerText.includes('Provider 健康') && providerText.includes('切换模型'),
    providerText,
  );

  const input = document.querySelector<HTMLInputElement>('.command-search input');
  input?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await waitForCondition(() => !useUiStore.getState().commandOpen, 'command palette close');
  record(checks, 'command-palette-escape-close', !useUiStore.getState().commandOpen, 'closed');
}

async function verifySessionSearch(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setSidebarOpen(true);
  ui.setActiveSection('chat');
  await delay(40);

  const sidebarInput = document.querySelector<HTMLInputElement>('.session-search input');
  record(checks, 'sidebar-search-input-present', Boolean(sidebarInput), 'session search input');
  if (!sidebarInput) return;

  setInputValue(sidebarInput, 'smoke-search');
  await waitForCondition(
    () => (document.querySelector('.session-search-results')?.textContent || '').includes('Smoke Search Hit'),
    'sidebar search result',
  );
  const sidebarText = document.querySelector('.session-search-results')?.textContent || '';
  record(checks, 'sidebar-search-hit-visible', sidebarText.includes('Smoke Search Hit') && sidebarText.includes('Smoke Workspace'), sidebarText);
  setInputValue(sidebarInput, '');

  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }));
  await waitForCondition(() => useUiStore.getState().commandOpen, 'command palette open for search');
  const commandInput = document.querySelector<HTMLInputElement>('.command-search input');
  record(checks, 'command-search-input-present', Boolean(commandInput), 'command search input');
  if (!commandInput) return;

  setInputValue(commandInput, 'smoke-search');
  await waitForCondition(
    () => (document.querySelector('.command-results')?.textContent || '').includes('Smoke Search Hit'),
    'command palette search result',
  );
  const commandText = document.querySelector('.command-results')?.textContent || '';
  record(checks, 'command-palette-fulltext-hit-visible', commandText.includes('全文搜索') && commandText.includes('Smoke Search Hit'), commandText);

  const hit = Array.from(document.querySelectorAll<HTMLButtonElement>('.command-item')).find(button =>
    button.textContent?.includes('Smoke Search Hit'),
  );
  record(checks, 'command-palette-fulltext-hit-clickable', Boolean(hit), commandText);
  hit?.click();
  await waitForCondition(() => useSessionStore.getState().activeSessionId === 'search-hit-session', 'search result session switch');
  record(checks, 'search-result-switches-session', useSessionStore.getState().activeSessionId === 'search-hit-session', useSessionStore.getState().activeSessionId || '');

  await useSessionStore.getState().selectSession('smoke-session');
  await useChatStore.getState().loadSession('smoke-session');
}

async function verifyMotionHooks(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setSidebarOpen(true);
  ui.setRightRailOpen(true);
  await delay(40);

  const sidebarButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.titlebar-actions button')).find(button => button.title === '左栏');
  record(checks, 'new90-left-sidebar-toggle-visible', Boolean(sidebarButton), document.querySelector('.titlebar-actions')?.innerHTML || '');
  sidebarButton?.click();
  await waitForCondition(() => useUiStore.getState().sidebarOpen === false, 'left sidebar collapsed from titlebar');
  record(
    checks,
    'new90-left-sidebar-toggle-collapses',
    document.querySelector('.secondary-panel')?.getAttribute('data-open') === 'false',
    document.querySelector('.secondary-panel')?.outerHTML.slice(0, 100),
  );
  sidebarButton?.click();
  await waitForCondition(() => useUiStore.getState().sidebarOpen === true, 'left sidebar expanded from titlebar');

  const activityRun = await startChatRun({
    assistant_id: 'assistant-sidebar-running-smoke',
    message: 'sidebar-running-smoke',
    session_id: 'search-hit-session',
  });
  await waitForCondition(
    () =>
      Array.from(document.querySelectorAll<HTMLElement>('.session-item[data-running="true"]')).some(item =>
        (item.textContent || '').includes('Smoke Search Hit'),
      ),
    'running session spinner visible',
  );
  const runningRow = Array.from(document.querySelectorAll<HTMLElement>('.session-item[data-running="true"]')).find(item =>
    (item.textContent || '').includes('Smoke Search Hit'),
  );
  record(
    checks,
    'new90-session-running-spinner-visible',
    Boolean(runningRow?.querySelector('.session-state-dot[data-status="running"], .session-run-spinner')),
    runningRow?.outerHTML || 'missing running session row',
  );
  record(
    checks,
    'new100-session-rename-button-visible',
    Boolean(runningRow?.querySelector('.rename-session')),
    runningRow?.outerHTML || 'missing running session row',
  );
  record(
    checks,
    'new100-session-naked-count-hidden',
    !runningRow?.querySelector('.session-meta'),
    runningRow?.outerHTML || 'missing running session row',
  );

  ui.setRightRailOpen(true);
  ui.setRightRailMode('activity');
  await waitForCondition(() => Boolean(document.querySelector('.run-activity-center')), 'NEW-91 run activity center visible');
  record(checks, 'new91-run-activity-center-visible', Boolean(document.querySelector('.run-activity-center')), document.querySelector('.activity-pane')?.textContent || '');
  // FABLEADV-28: 稳定性总览已移除；任务用状态色点 + 折叠详情 + 行点跳转。
  record(
    checks,
    'new92-activity-card-visual-language',
    Boolean(document.querySelector('.activity-section-head')),
    document.querySelector('.run-activity-center')?.outerHTML.slice(0, 240) || '',
  );
  await waitForCondition(
    () => Array.from(document.querySelectorAll<HTMLElement>('.run-activity-card')).some(card => (card.textContent || '').includes('Smoke Search Hit')),
    'NEW-91 run card visible',
  );
  const runCard = Array.from(document.querySelectorAll<HTMLElement>('.run-activity-card')).find(card => (card.textContent || '').includes('Smoke Search Hit'));
  record(checks, 'new91-run-card-visible', Boolean(runCard), runCard?.outerHTML || 'missing NEW-91 run card');

  const jumpButton = runCard?.querySelector<HTMLButtonElement>('.run-card-open');
  jumpButton?.click();
  await waitForCondition(() => useSessionStore.getState().activeSessionId === 'search-hit-session', 'NEW-91 run jump selects session');
  record(checks, 'new91-run-jump-selects-session', useSessionStore.getState().activeSessionId === 'search-hit-session', useSessionStore.getState().activeSessionId || '');

  ui.setRightRailMode('activity');
  await waitForCondition(() => Boolean(document.querySelector('.run-activity-card')), 'NEW-91 run card after jump');
  const detailCard = Array.from(document.querySelectorAll<HTMLElement>('.run-activity-card')).find(card => (card.textContent || '').includes('Smoke Search Hit'));
  const detailButton = Array.from(detailCard?.querySelectorAll<HTMLButtonElement>('.run-card-actions button') || []).find(button => button.textContent?.includes('详情'));
  detailButton?.click();
  await waitForCondition(
    () => useUiStore.getState().rightRailMode === 'activity' && (document.querySelector('.activity-inline-tool-output .tool-output-pane')?.textContent || '').includes(activityRun.runId),
    'NEW-91 run detail opens inline tool preview',
  );
  record(checks, 'new91-run-detail-opens-tool-preview', (document.querySelector('.tool-output-pane')?.textContent || '').includes(activityRun.runId), document.querySelector('.tool-output-pane')?.textContent || '');

  ui.setRightRailMode('activity');
  await waitForCondition(() => Boolean(document.querySelector('.run-activity-card')), 'NEW-91 run card before cancel');
  const cancelCard = Array.from(document.querySelectorAll<HTMLElement>('.run-activity-card')).find(card => (card.textContent || '').includes('Smoke Search Hit'));
  const cancelButton = Array.from(cancelCard?.querySelectorAll<HTMLButtonElement>('.run-card-actions button') || []).find(button => button.textContent?.includes('取消'));
  record(checks, 'new91-run-cancel-button-visible', Boolean(cancelButton), cancelCard?.outerHTML || 'missing cancel card');
  cancelButton?.click();
  await waitForCondition(() => {
    const activityText = document.querySelector('.run-activity-center')?.textContent || '';
    return activityText.includes('取消中') || activityText.includes('已取消');
  }, 'NEW-91 run cancel requested');
  const canceledRun = await getChatRun(activityRun.runId);
  record(checks, 'new91-run-cancel-requests-canceling', canceledRun.status === 'canceling' || canceledRun.status === 'canceled', canceledRun.status);
  const terminalStatus = canceledRun.status === 'canceled' ? canceledRun.status : await waitForRunTerminal(activityRun.runId);
  record(checks, 'new91-run-cancel-reaches-terminal-state', terminalStatus === 'canceled', terminalStatus);
  await useSessionStore.getState().selectSession('smoke-session');
  await useChatStore.getState().loadSession('smoke-session');

  const shell = document.querySelector('.shell-body');
  const sessionShell = document.querySelector('.session-list-shell');
  record(checks, 'layout-grid-transition', hasTransition(shell), shell ? window.getComputedStyle(shell).transitionDuration : 'missing');
  record(checks, 'workspace-collapse-transition', hasTransition(sessionShell), sessionShell ? window.getComputedStyle(sessionShell).transitionDuration : 'missing');

  ui.setSidebarOpen(false);
  ui.setRightRailOpen(false);
  await delay(40);

  const sidebar = document.querySelector('.secondary-panel');
  const rightRail = document.querySelector('.right-rail');
  record(checks, 'sidebar-stays-mounted-closed', sidebar?.getAttribute('data-open') === 'false', sidebar?.outerHTML.slice(0, 80));
  record(checks, 'right-rail-stays-mounted-closed', rightRail?.getAttribute('data-open') === 'false', rightRail?.outerHTML.slice(0, 80));

  ui.setSidebarOpen(true);
  ui.setRightRailOpen(true);
}

async function verifyBusySessionGuard(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setActiveSection('chat');
  ui.setSidebarOpen(true);
  await useSessionStore.getState().selectSession('smoke-session');
  await useChatStore.getState().loadSession('smoke-session');
  useChatStore.getState().clearLocal();

  const busyRun = await startChatRun({
    assistant_id: 'assistant-busy-guard-smoke',
    message: 'sidebar-running-smoke',
    session_id: 'smoke-session',
  });
  await useChatStore.getState().loadSession('smoke-session');
  await waitForCondition(() => useChatStore.getState().streaming, 'NEW-93 busy run attached');
  await waitForCondition(
    () => Boolean(document.querySelector('.session-item[data-running="true"] .session-state-dot[data-status="running"]')),
    'NEW-93 running session state dot',
  );
  const stateDot = document.querySelector<HTMLElement>('.session-item[data-running="true"] .session-state-dot');
  record(checks, 'new93-session-state-dot-visible', stateDot?.getAttribute('data-status') === 'running', stateDot?.outerHTML || 'missing state dot');

  await useChatStore.getState().send('busy guard should not create a second run');
  await waitForCondition(
    () => (document.querySelector('.runtime-status')?.textContent || '').includes('当前会话正在运行'),
    'NEW-93 busy send guard status',
  );
  const busyStatus = useChatStore.getState().runtimeStatus;
  record(
    checks,
    'new93-busy-send-guard-visible',
    busyStatus?.severity === 'warning' && busyStatus.display.includes('当前会话正在运行'),
    JSON.stringify(busyStatus),
  );

  useChatStore.getState().stop();
  const terminalStatus = await waitForRunTerminal(busyRun.runId);
  record(checks, 'new93-busy-run-cancel-cleanup', terminalStatus === 'canceled', terminalStatus);
  await useChatStore.getState().loadSession('smoke-session');
  useChatStore.getState().clearLocal();
}

async function verifyLongThreadWindowing(checks: SmokeCheck[]): Promise<void> {
  const now = Date.now();
  const longMessages: ChatMessage[] = Array.from({ length: 140 }, (_, index) => ({
    id: `long-thread-${index}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    content: `Long thread smoke message ${index}`,
    createdAt: now + index,
    tools:
      index === 137
        ? [
            {
              id: 'long-thread-tool',
              callId: 'long-thread-tool',
              toolName: 'read_file',
              status: 'success',
              result: 'x'.repeat(4000),
              summary: 'Long tool output smoke',
            },
          ]
        : [],
  }));

  useUiStore.getState().setActiveSection('chat');
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: longMessages,
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });

  await waitForCondition(
    () => (document.querySelector('.thread-window')?.getAttribute('data-message-window') || '').endsWith('/140'),
    'long thread window render',
  );
  const threadWindow = document.querySelector<HTMLElement>('.thread-window');
  const initialRows = document.querySelectorAll('.message-row').length;
  record(
    checks,
    'long-thread-window-caps-mounted-rows',
    Boolean(threadWindow && initialRows > 0 && initialRows <= 90),
    `${threadWindow?.getAttribute('data-message-window') || 'missing'} rows=${initialRows}`,
  );

  const loader = document.querySelector<HTMLButtonElement>('.thread-history-loader');
  record(checks, 'long-thread-history-loader-visible', Boolean(loader), threadWindow?.outerHTML.slice(0, 160) || '');
  loader?.click();
  await waitForCondition(() => document.querySelectorAll('.message-row').length > initialRows, 'older messages load into window');
  const expandedRows = document.querySelectorAll('.message-row').length;
  record(
    checks,
    'long-thread-history-loader-expands-window',
    expandedRows > initialRows && expandedRows <= 140,
    `before=${initialRows} after=${expandedRows}`,
  );

  const longToolPre = document.querySelector<HTMLElement>('.tool-card pre');
  if (longToolPre) {
    const style = window.getComputedStyle(longToolPre);
    record(checks, 'long-tool-output-height-limited', style.maxHeight !== 'none', style.maxHeight);
  }
  const longToolCard = document.querySelector<HTMLElement>('.tool-card');
  if (longToolCard) {
    const rect = longToolCard.getBoundingClientRect();
    const maxWidth = window.getComputedStyle(longToolCard).maxWidth;
    record(
      checks,
      'new82-tool-card-compact-width',
      rect.width <= 1120 || maxWidth.includes('1104') || maxWidth.includes('chat-column-width'),
      `width=${Math.round(rect.width)} maxWidth=${maxWidth}`,
    );
  }
  const toolGroup = document.querySelector<HTMLElement>('.tool-activity-group');
  record(
    checks,
    'new94-tool-activity-group-visible',
    Boolean(toolGroup && toolGroup.textContent?.includes('工具活动')),
    toolGroup?.outerHTML.slice(0, 240) || 'missing tool activity group',
  );
}

async function verifyRunRecoveryDiagnostics(checks: SmokeCheck[]): Promise<void> {
  const sessionId = 'smoke-session';
  const key = `metis.chat.runRecovery.${sessionId}`;
  const now = Date.now();
  await useSessionStore.getState().selectSession(sessionId);
  useChatStore.getState().stop();
  await cancelActiveRuns(sessionId);
  await delay(120);
  localStorage.setItem(
    key,
    JSON.stringify({
      sessionId,
      assistantId: 'recovery-assistant-smoke',
      startedAt: now - 60000,
      updatedAt: now - 5000,
      phase: 'tool',
      display: '正在运行工具',
      severity: 'working',
      toolCount: 2,
      preview: 'Recovery smoke preview',
    }),
  );
  useUiStore.getState().setActiveSection('chat');
  useChatStore.getState().hydrateRecoverySnapshot(sessionId);
  await waitForCondition(() => Boolean(document.querySelector('.run-recovery-notice')), 'run recovery notice visible');
  const noticeText = document.querySelector('.run-recovery-notice')?.textContent || '';
  record(
    checks,
    'new60-run-recovery-notice-visible',
    noticeText.includes('检测到未完成') && noticeText.includes('Recovery smoke preview'),
    noticeText,
  );

  const failedButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.run-recovery-actions button')).find(button =>
    button.textContent?.includes('标记失败'),
  );
  record(checks, 'new60-run-recovery-mark-failed-button', Boolean(failedButton), noticeText);
  failedButton?.click();
  await waitForCondition(() => !document.querySelector('.run-recovery-notice'), 'run recovery mark failed clears notice');
  record(
    checks,
    'new60-run-recovery-mark-failed',
    useChatStore.getState().runtimeStatus?.display.includes('已标记上一次运行失败') === true && localStorage.getItem(key) === null,
    JSON.stringify(useChatStore.getState().runtimeStatus),
  );

  localStorage.setItem(
    key,
    JSON.stringify({
      sessionId,
      assistantId: 'recovery-assistant-smoke-2',
      startedAt: now - 30000,
      updatedAt: now - 2000,
      phase: 'streaming',
      display: '正在生成回复',
      severity: 'working',
      toolCount: 0,
      preview: 'Cleanup smoke preview',
    }),
  );
  useChatStore.getState().hydrateRecoverySnapshot(sessionId);
  await waitForCondition(() => Boolean(document.querySelector('.run-recovery-notice')), 'run recovery cleanup notice visible');
  const clearButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.run-recovery-actions button')).find(button =>
    button.textContent?.includes('清理状态'),
  );
  record(checks, 'new60-run-recovery-clear-button', Boolean(clearButton), document.querySelector('.run-recovery-notice')?.textContent || '');
  clearButton?.click();
  await waitForCondition(() => !document.querySelector('.run-recovery-notice') && localStorage.getItem(key) === null, 'run recovery cleanup clears storage');
  record(checks, 'new60-run-recovery-cleanup', localStorage.getItem(key) === null, localStorage.getItem(key) || 'cleared');
  useChatStore.getState().clearLocal();
}

async function verifyDevServerAutoPreview(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setCommandOpen(false);
  ui.setSettingsOpen(false);
  ui.setModelPickerOpen(false);
  ui.setRightRailOpen(true);
  ui.setRightRailMode('web');
  await waitForCondition(
    () => useUiStore.getState().rightRailMode === 'web' && Boolean(document.querySelector('.dev-server-panel')),
    'dev server panel visible',
  );
  const startButton = document.querySelector<HTMLButtonElement>('.dev-server-start-button');
  record(checks, 'new64-dev-server-panel-visible', Boolean(startButton), document.querySelector('.dev-server-panel')?.textContent || '');
  startButton?.click();
  await waitForCondition(() => useUiStore.getState().webPreviewUrl === 'http://127.0.0.1:5173/', 'dev server auto opens right rail url');
  const panelText = document.querySelector('.dev-server-panel')?.textContent || '';
  record(
    checks,
    'new64-dev-server-auto-opens-preview',
    panelText.includes('Vite') && panelText.includes('127.0.0.1:5173'),
    panelText,
  );
  const status = await window.metis.devServerStatus({ cwd: useSessionStore.getState().workspaces[0]?.path });
  record(checks, 'new64-dev-server-reuses-running-status', status.state === 'running' && status.url.includes('5173'), JSON.stringify(status));
}

async function verifyPreviewVisualAudit(checks: SmokeCheck[]): Promise<void> {
  useUiStore.getState().setRightRailOpen(true);
  useUiStore.getState().setRightRailMode('web');
  if (!useUiStore.getState().webPreviewUrl) {
    useUiStore.getState().setWebPreviewUrl('http://127.0.0.1:5173/');
  }
  await waitForCondition(() => Boolean(document.querySelector('.web-audit-button')), 'preview audit button visible');
  const auditButton = document.querySelector<HTMLButtonElement>('.web-audit-button');
  record(checks, 'new65-preview-audit-button-visible', Boolean(auditButton), document.querySelector('.dev-server-panel')?.textContent || '');
  auditButton?.click();
  await waitForCondition(() => Boolean(document.querySelector('.preview-audit-panel')), 'preview audit panel visible');
  const auditText = document.querySelector('.preview-audit-panel')?.textContent || '';
  record(
    checks,
    'new65-preview-audit-evidence-saved',
    auditText.includes('验收') && auditText.includes('preview-evidence'),
    auditText,
  );
}

async function verifyTrueSessionResume(checks: SmokeCheck[]): Promise<void> {
  const sessionId = 'smoke-session';
  const key = `metis.chat.runRecovery.${sessionId}`;
  const now = Date.now();
  useChatStore.getState().clearLocal();
  localStorage.setItem(
    key,
    JSON.stringify({
      sessionId,
      assistantId: 'recovery-resume-assistant-smoke',
      startedAt: now - 70000,
      updatedAt: now - 2000,
      phase: 'tool',
      display: '正在运行 UI 预览任务',
      severity: 'working',
      toolCount: 3,
      preview: 'Resume smoke assistant preview',
      canResume: true,
      checkpoint: '工具 read_file 已完成，dev server 待启动',
      lastUserPreview: '请继续完成网页预览任务',
      assistantPreview: '已经识别前端项目',
    }),
  );
  useUiStore.getState().setActiveSection('chat');
  useChatStore.getState().hydrateRecoverySnapshot(sessionId);
  await waitForCondition(() => Boolean(document.querySelector('.run-recovery-notice')), 'true resume notice visible');
  const noticeText = document.querySelector('.run-recovery-notice')?.textContent || '';
  record(
    checks,
    'new66-resume-checkpoint-visible',
    noticeText.includes('中断点') && noticeText.includes('dev server 待启动') && noticeText.includes('继续执行'),
    noticeText,
  );
  const resumeButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.run-recovery-actions button')).find(button =>
    button.textContent?.includes('继续执行'),
  );
  record(checks, 'new66-resume-button-visible', Boolean(resumeButton), noticeText);
  resumeButton?.click();
  await waitForCondition(
    () => useChatStore.getState().messages.some(message => message.role === 'user' && message.content.includes('继续上一次中断的任务')),
    'resume prompt sent',
  );
  await waitForCondition(() => !useChatStore.getState().streaming, 'resume stream finished');
  record(
    checks,
    'new66-resume-sends-normal-chat',
    useChatStore.getState().messages.some(message => message.role === 'assistant' && message.content.includes('Hello from fake backend')),
    useChatStore.getState().messages.map(message => message.content).join('\n'),
  );
}

async function verifyContextWindowQuota(checks: SmokeCheck[]): Promise<void> {
  useUiStore.getState().setSidebarOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.context-window-card')), 'context window card visible');
  const card = document.querySelector<HTMLElement>('.context-window-card');
  record(
    checks,
    'new67-context-window-meter-visible',
    Boolean(card?.textContent?.includes('Context window') && card.textContent.includes('1.0M')),
    card?.textContent || '',
  );
  useChatStore.setState({ usage: { promptTokens: 920000, completionTokens: 10000, totalTokens: 930000 } });
  await waitForCondition(
    () => document.querySelector('.context-window-card')?.getAttribute('data-level') === 'danger',
    'context window danger threshold',
  );
  record(
    checks,
    'new67-context-window-danger-threshold',
    document.querySelector('.context-window-card')?.getAttribute('data-level') === 'danger',
    document.querySelector('.context-window-card')?.textContent || '',
  );
}

async function verifyContextCompactionControl(checks: SmokeCheck[]): Promise<void> {
  useUiStore.getState().setActiveSection('chat');
  useUiStore.getState().setSidebarOpen(true);
  const now = Date.now();
  useChatStore.setState(state => ({
    messages:
      state.messages.length >= 6
        ? state.messages
        : Array.from({ length: 8 }, (_, index) => ({
            id: `new69-local-message-${index}`,
            role: index % 2 === 0 ? 'user' : 'assistant',
            content: `NEW-69 local compact smoke message ${index}`,
            createdAt: now + index,
          })),
  }));
  await waitForCondition(() => Boolean(document.querySelector('.context-window-card')), 'context compact card visible');
  await waitForCondition(
    () => document.querySelector<HTMLButtonElement>('.context-compact-button')?.disabled === false,
    'context compact button enabled',
  );
  const button = document.querySelector<HTMLButtonElement>('.context-compact-button');
  record(
    checks,
    'new69-context-compact-button-visible',
    Boolean(button && button.textContent?.includes('压缩上下文')),
    document.querySelector('.context-window-card')?.textContent || '',
  );
  const messagesBeforeInlineAnimation = useChatStore.getState().messages.length;
  useChatStore.setState({ compacting: true });
  await waitForCondition(
    () =>
      document.querySelector('.thread-shell')?.getAttribute('data-compacting') === 'true' &&
      Boolean(document.querySelector('.thread-window .inline-compaction-row .context-organizing-notice')),
    'context compacting visual',
  );
  const inlineCompactionRow = document.querySelector<HTMLElement>('.thread-window .inline-compaction-row');
  record(
    checks,
    'new70-context-compacting-visual-visible',
    document.querySelector('.thread-shell')?.getAttribute('data-compacting') === 'true' &&
      (inlineCompactionRow?.textContent || '').includes('正在整理上下文'),
    inlineCompactionRow?.textContent || '',
  );
  record(
    checks,
    'new71-inline-compaction-row-in-thread-window',
    Boolean(
      inlineCompactionRow &&
        inlineCompactionRow.closest('.thread-window') &&
        inlineCompactionRow.querySelector('.context-cube') &&
        inlineCompactionRow.querySelector('.context-gold-rail'),
    ),
    inlineCompactionRow?.outerHTML.slice(0, 360) || '',
  );
  const threadWindowStyle = getComputedStyle(document.querySelector<HTMLElement>('.thread-window') as HTMLElement);
  record(
    checks,
    'new77-context-compaction-window-does-not-animate',
    threadWindowStyle.animationName === 'none' && threadWindowStyle.transform === 'none',
    `${threadWindowStyle.animationName}; ${threadWindowStyle.transform}`,
  );
  record(
    checks,
    'new71-inline-compaction-does-not-change-message-count',
    useChatStore.getState().messages.length === messagesBeforeInlineAnimation,
    `${messagesBeforeInlineAnimation} -> ${useChatStore.getState().messages.length}`,
  );
  useChatStore.setState({ compacting: false });
  await waitForCondition(() => !document.querySelector('.inline-compaction-row'), 'inline compacting visual clears');
  button?.click();
  await waitForCondition(
    () => (document.querySelector('.context-compact-status')?.textContent || '').includes('已压缩'),
    'context compact status done',
  );
  const statusText = document.querySelector('.context-compact-status')?.textContent || '';
  record(
    checks,
    'new69-context-compact-status-visible',
    statusText.includes('8 -> 5') && statusText.includes('NEW-69 smoke'),
    statusText,
  );
  await waitForCondition(
    () => useChatStore.getState().messages.some(message => message.role === 'system' && message.content.includes('Context Summary')),
    'compacted system summary loaded',
  );
  record(
    checks,
    'new69-context-compact-session-reloaded',
    useChatStore.getState().messages.length === 5 &&
      useChatStore.getState().messages.some(message => message.content.includes('No real API provider was called')),
    useChatStore.getState().messages.map(message => `${message.role}: ${message.content.slice(0, 80)}`).join('\n'),
  );
  await waitForCondition(
    () => (document.querySelector('.context-summary-card')?.textContent || '').includes('上下文已整理'),
    'context summary card rendered',
  );
  const summaryCardText = document.querySelector('.context-summary-card')?.textContent || '';
  record(
    checks,
    'new70-context-summary-card-visible',
    summaryCardText.includes('上下文已整理') && summaryCardText.includes('No real API provider was called'),
    summaryCardText,
  );
  record(
    checks,
    'new71-inline-compaction-row-clears-after-summary',
    !document.querySelector('.inline-compaction-row') && summaryCardText.includes('上下文已整理'),
    document.querySelector('.thread-window')?.textContent || '',
  );
  const handoffRaw = localStorage.getItem('metis.chat.compactHandoff.smoke-session') || '';
  record(
    checks,
    'new69-context-compact-handoff-saved',
    handoffRaw.includes('NEW-69 smoke') && !/sk-[A-Za-z0-9_-]{12,}/.test(handoffRaw),
    handoffRaw,
  );
}

async function verifyProviderUsageAndModels(checks: SmokeCheck[]): Promise<void> {
  const catalog = await getProviderModels({
    backend: 'custom-openai',
    baseUrl: 'https://api.example.com',
    model: 'gpt-5.5',
    apiKey: 'fake-smoke-key',
  });
  record(
    checks,
    'new68-provider-model-catalog',
    catalog.ok &&
      catalog.apiBaseUrl === 'https://api.example.com/v1' &&
      catalog.models.some(model => model.id === 'gpt-5.5' && model.contextLimit === 1000000) &&
      catalog.models.some(model => model.id === 'gpt-image-2' && !model.chatCapable),
    JSON.stringify(catalog),
  );

  const usage = await getProviderUsage({
    backend: 'custom-openai',
    baseUrl: 'https://api.example.com',
    model: 'gpt-5.5',
    apiKey: 'fake-smoke-key',
  });
  record(
    checks,
    'new68-provider-usage-api',
    usage.status === 'ok' && usage.usageUrl === 'https://api.example.com/v1/usage' && usage.remaining > 400,
    JSON.stringify(usage),
  );

  const ui = useUiStore.getState();
  ui.setSettingsSection('usage');
  ui.setSettingsOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.provider-usage-card')), 'provider usage card visible');
  const usageButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.provider-usage-card button')).find(button =>
    button.textContent?.includes('刷新额度'),
  );
  record(checks, 'new68-provider-usage-button-visible', Boolean(usageButton), document.querySelector('.provider-usage-card')?.textContent || '');
  usageButton?.click();
  await waitForCondition(() => (document.querySelector('.provider-usage-card')?.textContent || '').includes('额度查询成功'), 'provider usage refreshed');
  record(
    checks,
    'new68-provider-usage-visible',
    (document.querySelector('.provider-usage-card')?.textContent || '').includes('累计 Tokens'),
    document.querySelector('.provider-usage-card')?.textContent || '',
  );

  ui.setSettingsSection('model');
  await waitForCondition(() => Boolean(document.querySelector('.provider-profile-panel')), 'provider profile panel visible');
  const profilePanelText = document.querySelector('.provider-profile-panel')?.textContent || '';
  record(
    checks,
    'new76-provider-profile-panel-visible',
    profilePanelText.includes('DeepSeek') &&
      profilePanelText.includes('deepseek-v4-flash') &&
      profilePanelText.includes('本地预设'),
    profilePanelText,
  );
  await waitForCondition(() => Boolean(document.querySelector('.provider-catalog-panel')), 'provider catalog panel visible');
  const providerTextInputs = Array.from(
    document.querySelectorAll<HTMLInputElement>('.settings-base-url-input, .settings-model-input, .settings-api-key-input'),
  );
  record(
    checks,
    'new72-provider-text-inputs-spellcheck-disabled',
    providerTextInputs.length === 3 && providerTextInputs.every(input => input.spellcheck === false),
    providerTextInputs.map(input => `${input.className}:${input.spellcheck}`).join(', '),
  );
  const catalogButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.provider-catalog-panel button')).find(button =>
    button.textContent?.includes('刷新模型目录'),
  );
  record(checks, 'new68-provider-model-button-visible', Boolean(catalogButton), document.querySelector('.provider-catalog-panel')?.textContent || '');
  catalogButton?.click();
  await waitForCondition(() => {
    const text = document.querySelector('.provider-catalog-panel')?.textContent || '';
    return text.includes('gpt-5.5') || text.includes('deepseek-v4-flash');
  }, 'provider model catalog visible');
  const modelPanelText = document.querySelector('.provider-catalog-panel')?.textContent || '';
  record(
    checks,
    'new68-provider-models-visible',
    modelPanelText.includes('非聊天模型') || modelPanelText.includes('本地预设模型') || modelPanelText.includes('deepseek-v4-pro'),
    modelPanelText,
  );
  const disclosure = document.querySelector<HTMLButtonElement>('.provider-model-disclosure');
  record(
    checks,
    'new72-provider-model-disclosure-visible',
    Boolean(disclosure) && disclosure?.getAttribute('aria-expanded') === 'true',
    document.querySelector('.provider-catalog-panel')?.textContent || '',
  );
  disclosure?.click();
  await waitForCondition(() => !document.querySelector('.provider-model-list'), 'provider model catalog collapsed');
  record(
    checks,
    'new72-provider-model-picker-collapses',
    disclosure?.getAttribute('aria-expanded') === 'false',
    document.querySelector('.provider-catalog-panel')?.innerHTML || '',
  );
  disclosure?.click();
  await waitForCondition(() => Boolean(document.querySelector('.provider-model-list')), 'provider model catalog expanded');
  const targetModel = Array.from(document.querySelectorAll<HTMLButtonElement>('.provider-model-list button')).find(button =>
    button.textContent?.includes('gpt-5.4') || button.textContent?.includes('deepseek-v4-pro'),
  );
  const targetModelId = targetModel?.textContent?.includes('gpt-5.4') ? 'gpt-5.4' : 'deepseek-v4-pro';
  targetModel?.click();
  await waitForCondition(() => document.querySelector<HTMLInputElement>('.settings-model-input')?.value === targetModelId, 'provider model selection applied');
  record(
    checks,
    'new72-provider-model-picker-selects-model',
    document.querySelector<HTMLInputElement>('.settings-model-input')?.value === targetModelId,
    document.querySelector<HTMLInputElement>('.settings-model-input')?.value || '',
  );
  ui.setSettingsSection('tools');
  await waitForCondition(() => Boolean(document.querySelector('.permission-panel')), 'permission panel visible');
  const settingsPanel = document.querySelector<HTMLElement>('.settings-panel');
  record(
    checks,
    'new72-settings-panel-no-horizontal-overflow',
    settingsPanel ? settingsPanel.scrollWidth <= settingsPanel.clientWidth + 1 : false,
    settingsPanel ? `${settingsPanel.scrollWidth}/${settingsPanel.clientWidth}` : 'missing',
  );
  if (settingsPanel) {
    settingsPanel.scrollTop = 0;
    const canScroll = settingsPanel.scrollHeight > settingsPanel.clientHeight + 1;
    settingsPanel.scrollTop = settingsPanel.scrollHeight;
    await delay(20);
    record(
      checks,
      'new73-permission-center-scrolls-vertically',
      !canScroll || settingsPanel.scrollTop > 0,
      `${settingsPanel.scrollTop}/${settingsPanel.scrollHeight}/${settingsPanel.clientHeight}`,
    );
  }
  ui.setSettingsOpen(false);
}

function permissionRowFor(tool: string): HTMLElement | null {
  return (
    Array.from(document.querySelectorAll<HTMLElement>('.permission-row')).find(row => row.textContent?.includes(tool)) ?? null
  );
}

async function waitForPermissionState(
  predicate: (state: PermissionStatePayload) => boolean,
  detail: string,
): Promise<PermissionStatePayload> {
  const deadline = Date.now() + SMOKE_TIMEOUT_MS;
  let lastState = await getPermissions();
  while (Date.now() < deadline) {
    lastState = await getPermissions();
    if (predicate(lastState)) return lastState;
    await delay(40);
  }
  throw new Error(`timed out waiting for ${detail}: ${JSON.stringify(lastState.rules)}`);
}

async function verifyComposerUploadAndRegressionFixes(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setActiveSection('chat');
  useChatStore.getState().clearLocal();
  await delay(40);

  const file = new File(['# Smoke upload\n\nattachment body'], 'smoke-upload.md', { type: 'text/markdown' });
  const uploadPromise = useChatStore.getState().addFiles([file]);
  await waitForCondition(
    () => Boolean(document.querySelector('.attachment-card[data-status="parsing"], .attachment-card[data-status="ready"]')),
    'attachment draft card visible',
  );
  record(
    checks,
    'new73-composer-upload-card-visible',
    Boolean(document.querySelector('.attachment-card')),
    document.querySelector('.attachment-row')?.textContent || '',
  );
  await uploadPromise;
  await waitForCondition(() => Boolean(document.querySelector('.attachment-card[data-status="ready"]')), 'attachment card ready');
  const attachmentText = document.querySelector('.attachment-row')?.textContent || '';
  record(
    checks,
    'new73-composer-upload-ready-card',
    attachmentText.includes('smoke-upload.md') && attachmentText.includes('.md'),
    attachmentText,
  );
  await useChatStore.getState().send('attachment smoke');
  const userMessage = useChatStore.getState().messages.find(message => message.role === 'user' && message.content.includes('attachment smoke'));
  record(
    checks,
    'new73-composer-upload-hidden-from-bubble',
    Boolean(
      userMessage &&
        !userMessage.content.includes('Fake parsed upload: smoke-upload.md') &&
        userMessage.attachments?.some(attachment => attachment.name === 'smoke-upload.md'),
    ),
    userMessage?.content || 'missing user message',
  );
  await waitForCondition(
    () =>
      Boolean(document.querySelector('.message-attachment-card')) &&
      !(document.querySelector('.message-row.user')?.textContent || '').includes('Fake parsed upload'),
    'attachment hidden from rendered user bubble',
  );
  record(
    checks,
    'new73-message-attachment-card-rendered',
    (document.querySelector('.message-row.user')?.textContent || '').includes('smoke-upload.md'),
    document.querySelector('.message-row.user')?.textContent || '',
  );
  useChatStore.getState().clearLocal();
}

async function verifyPermissionBulkManagement(checks: SmokeCheck[]): Promise<void> {
  const created = await Promise.all([
    createPermissionRule({ tool: 'smoke_bulk', action: 'ask', argsMatch: { path: 'src/*' }, source: 'smoke_bulk' }),
    createPermissionRule({ tool: 'smoke_bulk', action: 'allow', argsMatch: { path: 'src/*' }, source: 'smoke_bulk' }),
    createPermissionRule({ tool: 'smoke_export', action: 'deny', argsMatch: { path: 'secret/*' }, source: 'smoke_bulk' }),
  ]);
  const ui = useUiStore.getState();
  ui.setSettingsSection('tools');
  ui.setSettingsOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.permission-bulk-toolbar')), 'permission bulk toolbar');
  await waitForCondition(() => Boolean(permissionRowFor('smoke_bulk') && permissionRowFor('smoke_export')), 'permission smoke rules loaded');
  const permissionText = document.querySelector('.permission-panel')?.textContent || '';
  record(
    checks,
    'new61-permission-bulk-toolbar-visible',
    permissionText.includes('批量删除') && permissionText.includes('清理冲突'),
    permissionText,
  );
  record(
    checks,
    'new61-permission-import-export-visible',
    Boolean(document.querySelector('.permission-import-export') && document.querySelector('.permission-export-json')),
    document.querySelector('.permission-import-export')?.textContent || '',
  );

  const exportButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.permission-import-export button')).find(button =>
    button.textContent?.includes('导出规则'),
  );
  exportButton?.click();
  await waitForCondition(() => {
    const textarea = document.querySelector<HTMLTextAreaElement>('.permission-export-json');
    return Boolean(textarea?.value.includes('metis.permission.rules.v1') && textarea.value.includes('smoke_export'));
  }, 'permission export json');
  record(
    checks,
    'new61-permission-export-json',
    document.querySelector<HTMLTextAreaElement>('.permission-export-json')?.value.includes('smoke_export') === true,
    document.querySelector<HTMLTextAreaElement>('.permission-export-json')?.value || '',
  );

  const importTextarea = document.querySelector<HTMLTextAreaElement>('.permission-import-json');
  const importButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.permission-import-export button')).find(button =>
    button.textContent?.includes('导入规则'),
  );
  record(checks, 'new61-permission-import-controls-visible', Boolean(importTextarea && importButton), 'import controls');
  if (!importTextarea || !importButton) return;
  setTextareaValue(
    importTextarea,
    JSON.stringify({ rules: [{ tool: 'smoke_imported', action: 'ask', argsMatch: { path: 'app/*' } }] }),
  );
  importButton.click();
  const importDialog = await waitForAppDialog('permission import dialog');
  record(checks, 'new61-permission-import-themed-dialog', (importDialog.textContent || '').includes('导入权限规则'), importDialog.textContent || '');
  clickAppDialogConfirm();
  await waitForCondition(() => (document.querySelector('.permission-panel')?.textContent || '').includes('smoke_imported'), 'permission imported rule visible');
  const afterImport = await getPermissions();
  record(
    checks,
    'new61-permission-import-persists-rule',
    afterImport.rules.some(rule => rule.tool === 'smoke_imported'),
    JSON.stringify(afterImport.rules),
  );

  const cleanupButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.permission-bulk-toolbar button')).find(button =>
    button.textContent?.includes('清理冲突'),
  );
  record(checks, 'new61-permission-cleanup-button-visible', Boolean(cleanupButton), document.querySelector('.permission-bulk-toolbar')?.textContent || '');
  cleanupButton?.click();
  const cleanupDialog = await waitForAppDialog('permission conflict cleanup dialog');
  record(
    checks,
    'new61-permission-cleanup-themed-dialog',
    (cleanupDialog.textContent || '').includes('清理冲突权限规则'),
    cleanupDialog.textContent || '',
  );
  clickAppDialogConfirm();
  const afterCleanup = await waitForPermissionState(
    state => state.rules.filter(rule => rule.tool === 'smoke_bulk').length === 1,
    'permission conflict cleanup',
  );
  record(
    checks,
    'new61-permission-conflict-cleanup',
    afterCleanup.rules.filter(rule => rule.tool === 'smoke_bulk').length === 1,
    JSON.stringify(afterCleanup.rules),
  );

  for (const tool of ['smoke_export', 'smoke_imported']) {
    const row = permissionRowFor(tool);
    const checkbox = row?.querySelector<HTMLInputElement>('.permission-rule-checkbox');
    record(checks, `new61-permission-${tool}-checkbox-visible`, Boolean(checkbox), row?.textContent || '');
    checkbox?.click();
  }
  const bulkDeleteButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.permission-bulk-toolbar button')).find(button =>
    button.textContent?.includes('批量删除'),
  );
  record(checks, 'new61-permission-bulk-delete-button-visible', Boolean(bulkDeleteButton), document.querySelector('.permission-bulk-toolbar')?.textContent || '');
  bulkDeleteButton?.click();
  const bulkDeleteDialog = await waitForAppDialog('permission bulk delete dialog');
  record(
    checks,
    'new61-permission-bulk-delete-themed-dialog',
    (bulkDeleteDialog.textContent || '').includes('批量删除权限规则'),
    bulkDeleteDialog.textContent || '',
  );
  clickAppDialogConfirm();
  const afterBulkDelete = await waitForPermissionState(
    state => !state.rules.some(rule => rule.tool === 'smoke_export' || rule.tool === 'smoke_imported'),
    'permission bulk delete',
  );
  record(
    checks,
    'new61-permission-bulk-delete',
    !afterBulkDelete.rules.some(rule => rule.tool === 'smoke_export' || rule.tool === 'smoke_imported'),
    JSON.stringify(afterBulkDelete.rules),
  );
  for (const rule of afterBulkDelete.rules.filter(rule => rule.tool === 'smoke_bulk')) {
    await deletePermissionRule(rule.id);
  }
  for (const rule of created) {
    const state = await getPermissions();
    if (state.rules.some(item => item.id === rule.id)) await deletePermissionRule(rule.id);
  }
  ui.setSettingsOpen(false);
}

async function verifyRealUiPreviewAutomation(checks: SmokeCheck[]): Promise<void> {
  const viteUrl = findSafeLocalPreviewUrl('  \u001b[32m➜\u001b[0m  Local:   http://localhost:5173/');
  const hostPortUrl = findSafeLocalPreviewUrl('ready - started server on 0.0.0.0:3000');
  const externalUrl = findSafeLocalPreviewUrl('Production preview: https://example.com/app');
  const localHtmlUrl = localFilePreviewUrl('http://127.0.0.1:5000', 'qq-classroom-agent.html');
  record(checks, 'new62-vite-local-url-detected', viteUrl === 'http://localhost:5173/', viteUrl);
  record(checks, 'new62-host-port-url-normalized', hostPortUrl === 'http://127.0.0.1:3000', hostPortUrl);
  record(checks, 'new62-external-url-rejected', externalUrl === '', externalUrl);
  record(
    checks,
    'new74-local-html-preview-url-built',
    isPreviewableWebFilePath('qq-classroom-agent.html') &&
      localHtmlUrl === 'http://127.0.0.1:5000/file-preview?path=qq-classroom-agent.html',
    localHtmlUrl,
  );
  useUiStore.getState().setWebPreviewUrl(viteUrl);
  await waitForCondition(() => useUiStore.getState().rightRailMode === 'web' && useUiStore.getState().webPreviewUrl === viteUrl, 'NEW-62 right rail vite preview');
  record(checks, 'new62-right-rail-vite-preview-opens', useUiStore.getState().webPreviewUrl === viteUrl, useUiStore.getState().webPreviewUrl);
}

async function verifyReleaseDiagnosticsBundle(checks: SmokeCheck[]): Promise<void> {
  const diagnostics = await window.metis.diagnostics();
  record(
    checks,
    'new63-diagnostics-payload',
    diagnostics.backend.status === 'ready' && Boolean(diagnostics.backend.logPath) && diagnostics.app.fakeBackend === true,
    JSON.stringify(diagnostics),
  );
  record(
    checks,
    'new63-diagnostics-redacted',
    !/sk-[A-Za-z0-9_-]{12,}|api[_-]?key\s*[:=]\s*[^\s"'`,;]+/i.test(JSON.stringify(diagnostics)),
    JSON.stringify(diagnostics).slice(0, 500),
  );
  useUiStore.getState().setSettingsSection('about');
  useUiStore.getState().setSettingsOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.diagnostics-panel')), 'diagnostics panel visible');
  const panelText = document.querySelector('.diagnostics-panel')?.textContent || '';
  record(
    checks,
    'new63-diagnostics-panel-visible',
    panelText.includes('发布诊断') && panelText.includes('生成诊断包'),
    panelText,
  );
  const bundleButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.diagnostics-actions button')).find(button =>
    button.textContent?.includes('生成诊断包'),
  );
  record(checks, 'new63-diagnostics-bundle-button-visible', Boolean(bundleButton), panelText);
  bundleButton?.click();
  await waitForCondition(() => (document.querySelector('.diagnostics-panel')?.textContent || '').includes('诊断包已保存'), 'diagnostics bundle saved');
  const resultText = document.querySelector('.diagnostics-panel')?.textContent || '';
  record(checks, 'new63-diagnostics-bundle-generated', resultText.includes('诊断包已保存'), resultText);
  useUiStore.getState().setSettingsOpen(false);
}

async function verifyProviderRegistry(checks: SmokeCheck[]): Promise<void> {
  const status = await getProviderStatus();
  const providerIds = status.providers.map(provider => provider.providerId);
  record(
    checks,
    'provider-registry-profiles-visible',
    providerIds.includes('deepseek') &&
      providerIds.includes('openai-compatible') &&
      providerIds.includes('custom-openai') &&
      providerIds.includes('kimi') &&
      providerIds.includes('zhipu-glm') &&
      providerIds.includes('bailian') &&
      providerIds.includes('anthropic'),
    providerIds.join(', '),
  );
  record(
    checks,
    'provider-active-deepseek',
    status.active?.providerId === 'deepseek' && status.active.chatUrl === 'https://api.deepseek.com/chat/completions',
    JSON.stringify(status.active),
  );

  const validation = await verifyProviderConfig({
    backend: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-v4-flash',
    apiKey: 'fake-smoke-key',
  });
  record(
    checks,
    'provider-local-verify-no-network',
    validation.ok && validation.message.includes('没有发起真实模型调用') && validation.chatUrl === 'https://api.deepseek.com/chat/completions',
    JSON.stringify(validation),
  );

  const repairedValidation = await verifyProviderConfig({
    backend: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    model: 'gpt-4o',
    apiKey: 'fake-smoke-key',
  });
  record(
    checks,
    'new76-deepseek-gpt-model-repaired',
    repairedValidation.ok &&
      repairedValidation.providerId === 'deepseek' &&
      repairedValidation.model === 'deepseek-v4-flash' &&
      repairedValidation.warnings.some(warning => warning.includes('gpt-4o') && warning.includes('deepseek-v4-flash')),
    JSON.stringify(repairedValidation),
  );

  const customProfile = status.providers.find(provider => provider.providerId === 'custom-openai');
  record(
    checks,
    'provider-custom-openai-empty-defaults',
    Boolean(customProfile && customProfile.baseUrl === '' && customProfile.defaultModel === ''),
    JSON.stringify(customProfile),
  );

  const customValidation = await verifyProviderConfig({
    backend: 'custom-openai',
    baseUrl: 'https://relay.example.com/v1',
    model: 'gpt-relay-smoke',
    apiKey: 'fake-relay-key',
  });
  record(
    checks,
    'provider-custom-openai-local-verify-no-network',
    customValidation.ok &&
      customValidation.providerId === 'custom-openai' &&
      customValidation.chatUrl === 'https://relay.example.com/v1/chat/completions',
    JSON.stringify(customValidation),
  );

  const presetCatalog = await getProviderModels({
    backend: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-v4-flash',
    apiKey: '',
  });
  record(
    checks,
    'new75-provider-preset-models-without-key',
    presetCatalog.status === 'preset' &&
      presetCatalog.models.some(model => model.id === 'deepseek-v4-flash' && model.contextLimit === 1000000),
    JSON.stringify(presetCatalog),
  );
}

async function verifyThreeZones(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();

  ui.setActiveSection('skills');
  await waitForCondition(() => (document.querySelector('[data-zone="skills"]')?.textContent || '').includes('Smoke Skill'), 'skills zone data');
  const skillsText = document.querySelector('[data-zone="skills"]')?.textContent || '';
  record(checks, 'skills-zone-live-data', skillsText.includes('Smoke Skill') && !skillsText.includes('商店 registry 后续接入'), skillsText);

  ui.setActiveSection('mcp');
  await waitForCondition(() => (document.querySelector('[data-zone="mcp"]')?.textContent || '').includes('smoke-mcp'), 'mcp zone data');
  const mcpText = document.querySelector('[data-zone="mcp"]')?.textContent || '';
  record(checks, 'mcp-zone-live-data', mcpText.includes('smoke-mcp') && mcpText.includes('read_resource'), mcpText);

  ui.setActiveSection('computer');
  await waitForCondition(
    () => (document.querySelector('[data-zone="computer"]')?.textContent || '').includes('Smoke desktop automation'),
    'computer zone data',
  );
  const computerText = document.querySelector('[data-zone="computer"]')?.textContent || '';
  record(
    checks,
    'computer-zone-live-data',
    computerText.includes('Smoke desktop automation') && computerText.includes('安全控制') && !computerText.includes('这里保留安全总开关'),
    computerText,
  );

  ui.setActiveSection('chat');
}

async function verifyComposerPermissionAccess(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  await setComposerPermissionMode('auto');
  ui.setSettingsOpen(false);
  ui.setActiveSection('chat');
  await waitForCondition(() => Boolean(document.querySelector('.composer-access-button')), 'composer permission button');
  const button = document.querySelector<HTMLButtonElement>('.composer-access-button');
  record(
    checks,
    'composer-access-button-visible',
    Boolean(button && button.textContent?.includes('替我审批')),
    button?.outerHTML || 'missing composer access button',
  );
  if (!button) return;

  const composer = document.querySelector<HTMLElement>('.composer');
  const textarea = document.querySelector<HTMLTextAreaElement>('.composer textarea');
  const toolbar = document.querySelector<HTMLElement>('.composer-toolbar');
  const sendButton = document.querySelector<HTMLButtonElement>('.send-button');
  const composerStyle = composer ? getComputedStyle(composer) : null;
  const composerRect = composer?.getBoundingClientRect();
  const textareaRect = textarea?.getBoundingClientRect();
  const sendRect = sendButton?.getBoundingClientRect();
  record(
    checks,
    'new80-composer-column-layout',
    composerStyle?.display === 'flex' && composerStyle.flexDirection === 'column' && Boolean(toolbar),
    composer?.outerHTML.slice(0, 240) || 'missing composer',
  );
  record(
    checks,
    'new80-composer-textarea-full-width',
    Boolean(composerRect && textareaRect && textareaRect.width >= composerRect.width - 56),
    composerRect && textareaRect ? `${textareaRect.width}/${composerRect.width}` : 'missing composer textarea',
  );
  record(
    checks,
    'new80-composer-circular-send-button',
    Boolean(sendRect && Math.abs(sendRect.width - sendRect.height) <= 2 && sendRect.width >= 34),
    sendRect ? `${sendRect.width}x${sendRect.height}` : 'missing send button',
  );
  record(
    checks,
    'new81-composer-default-height-compact',
    Boolean(composerRect && composerRect.height <= 122),
    composerRect ? `${composerRect.width}x${composerRect.height}` : 'missing composer',
  );
  record(
    checks,
    'new81-sidebar-folder-icon-in-search-row',
    Boolean(document.querySelector('.sidebar-search-row .sidebar-folder-button')) &&
      !(document.querySelector('.sidebar-search-row .sidebar-folder-button')?.textContent || '').trim(),
    document.querySelector('.sidebar-search-row')?.innerHTML || 'missing sidebar search row',
  );
  record(
    checks,
    'new81-sidebar-top-new-chat-removed',
    !Boolean(document.querySelector('.sidebar-actions')) &&
      !(document.querySelector('.sidebar-search-row')?.textContent || '').includes('新会话'),
    document.querySelector('.sidebar')?.textContent || '',
  );

  button.click();
  await waitForCondition(() => Boolean(document.querySelector('.composer-access-menu')), 'composer permission menu');
  const menuText = document.querySelector('.composer-access-menu')?.textContent || '';
  record(
    checks,
    'composer-access-menu-visible',
    menuText.includes('请求批准') && menuText.includes('替我审批') && menuText.includes('完全访问权限'),
    menuText,
  );

  const fullOption = Array.from(document.querySelectorAll<HTMLButtonElement>('.composer-access-option')).find(option =>
    option.textContent?.includes('完全访问权限'),
  );
  record(checks, 'composer-access-full-option-visible', Boolean(fullOption), menuText);
  fullOption?.click();
  await waitForCondition(() => (document.querySelector('.composer-access-button')?.textContent || '').includes('完全访问'), 'composer full access saved');
  const fullMode = await getComposerPermissionMode();
  const fullState = await getPermissions();
  record(
    checks,
    'composer-access-full-persists-rule',
    fullMode === 'full' &&
      fullState.rules.some(rule => rule.tool === '*' && rule.action === 'allow' && rule.source === 'composer_access'),
    JSON.stringify(fullState.rules),
  );

  document.querySelector<HTMLButtonElement>('.composer-access-button')?.click();
  await waitForCondition(() => Boolean(document.querySelector('.composer-access-menu')), 'composer menu reopen for auto');
  const autoOption = Array.from(document.querySelectorAll<HTMLButtonElement>('.composer-access-option')).find(option =>
    option.textContent?.includes('替我审批'),
  );
  record(checks, 'composer-access-auto-option-visible', Boolean(autoOption), document.querySelector('.composer-access-menu')?.textContent || '');
  autoOption?.click();
  await waitForCondition(() => (document.querySelector('.composer-access-button')?.textContent || '').includes('替我审批'), 'composer auto access saved');
  const autoMode = await getComposerPermissionMode();
  const autoState = await getPermissions();
  record(
    checks,
    'composer-access-auto-removes-owned-rule',
    autoMode === 'auto' && !autoState.rules.some(rule => rule.tool === '*' && rule.source === 'composer_access'),
    JSON.stringify(autoState.rules),
  );
}

async function verifyDeveloperWorkflowPolish(checks: SmokeCheck[]): Promise<void> {
  await verifyComposerPermissionAccess(checks);

  const settings = await getSettings();
  record(
    checks,
    'new52-5-proxy-settings-present',
    settings.proxyMode === 'custom' && settings.proxyHost === '127.0.0.1' && settings.proxyPort === '7890',
    JSON.stringify({
      proxyMode: settings.proxyMode,
      proxyHost: settings.proxyHost,
      proxyPort: settings.proxyPort,
      terminalShell: settings.terminalShell,
    }),
  );

  const appInfo = await window.metis.appInfo();
  record(checks, 'new52-5-fake-backend-diagnostic-visible', appInfo.fakeBackend === true, JSON.stringify(appInfo));

  const ui = useUiStore.getState();
  ui.setSettingsSection('network');
  ui.setSettingsOpen(true);
  await waitForCondition(
    () =>
      document.querySelector('.settings-dialog')?.getAttribute('data-active-section') === 'network' &&
      (document.querySelector('.settings-dialog')?.textContent || '').includes('代理设置'),
    'network settings panel',
  );
  const networkText = document.querySelector('.settings-dialog')?.textContent || '';
  record(checks, 'new52-5-network-settings-ui', networkText.includes('Clash') && networkText.includes('127.0.0.1'), networkText);
  ui.setSettingsSection('terminal');
  await waitForCondition(
    () =>
      document.querySelector('.settings-dialog')?.getAttribute('data-active-section') === 'terminal' &&
      (document.querySelector('.settings-dialog')?.textContent || '').includes('默认终端'),
    'terminal settings panel',
  );
  const terminalSettingsText = document.querySelector('.settings-dialog')?.textContent || '';
  record(checks, 'new52-5-terminal-settings-ui', terminalSettingsText.includes('PowerShell'), terminalSettingsText);
  const terminalSettingsSelect = document.querySelector<HTMLSelectElement>('.terminal-shell-select');
  const terminalSettingsOptions = Array.from(terminalSettingsSelect?.options ?? []).map(option => option.value);
  record(
    checks,
    'new84-terminal-settings-shell-options',
    ['powershell', 'cmd', 'bash', 'sh', 'shell'].every(option => terminalSettingsOptions.includes(option)),
    terminalSettingsOptions.join(','),
  );
  record(
    checks,
    'new54-terminal-settings-disclosure',
    Boolean(document.querySelector('.terminal-settings-disclosure') && document.querySelector('.terminal-shell-select')),
    terminalSettingsText,
  );
  ui.setSettingsOpen(false);

  ui.setFontFamily('microsoft-yahei');
  await delay(20);
  record(
    checks,
    'new52-5-font-setting-applies',
    getComputedStyle(document.documentElement).getPropertyValue('--font-sans').includes('Microsoft YaHei'),
    getComputedStyle(document.documentElement).getPropertyValue('--font-sans'),
  );
  ui.setFontFamily('official-sans');
  ui.setUiFontSize(16);
  ui.setCodeFontSize(14);
  await delay(20);
  const rootStyle = getComputedStyle(document.documentElement);
  record(
    checks,
    'new83-ui-font-size-applies',
    rootStyle.getPropertyValue('--ui-font-size').trim() === '16px',
    rootStyle.getPropertyValue('--ui-font-size'),
  );
  record(
    checks,
    'new83-code-font-size-applies',
    rootStyle.getPropertyValue('--code-font-size').trim() === '14px',
    rootStyle.getPropertyValue('--code-font-size'),
  );
  ui.setSettingsSection('appearance');
  ui.setSettingsOpen(true);
  await waitForCondition(
    () =>
      document.querySelector('.settings-dialog')?.getAttribute('data-active-section') === 'appearance' &&
      (document.querySelector('.settings-dialog')?.textContent || '').includes('UI 字号'),
    'appearance font size controls',
  );
  const appearanceText = document.querySelector('.settings-dialog')?.textContent || '';
  record(
    checks,
    'new83-appearance-font-size-controls-visible',
    appearanceText.includes('UI 字号') && appearanceText.includes('代码字号') && document.querySelectorAll('.settings-size-row').length >= 2,
    appearanceText,
  );
  ui.setSettingsOpen(false);
  ui.setUiFontSize(14);
  ui.setCodeFontSize(12);

  ui.setTerminalOpen(false);
  await delay(40);
  const navTerminalToggle = document.querySelector<HTMLButtonElement>('.nav-terminal-button');
  record(checks, 'terminal-nav-button-visible', Boolean(navTerminalToggle), document.querySelector('.nav-rail')?.textContent || '');
  navTerminalToggle?.click();
  await waitForCondition(
    () =>
      useUiStore.getState().terminalOpen &&
      document.querySelector('.right-rail .workspace-card[data-card="terminal"] .terminal-panel')?.getAttribute('data-open') === 'true',
    'terminal card open from nav rail',
  );
  record(
    checks,
    'terminal-nav-button-opens-terminal',
    navTerminalToggle?.getAttribute('data-active') === 'true' || useUiStore.getState().terminalOpen,
    document.querySelector('.terminal-panel')?.getAttribute('data-open') || '',
  );
  record(
    checks,
    'new99-terminal-card-opens-from-nav',
    Boolean(document.querySelector('.right-rail .workspace-card[data-card="terminal"] .terminal-panel[data-embedded="true"]')),
    document.querySelector('.right-rail')?.textContent || '',
  );
  const terminalPanel = document.querySelector<HTMLElement>('.terminal-panel');
  const rightRail = document.querySelector<HTMLElement>('.right-rail');
  const terminalRect = terminalPanel?.getBoundingClientRect();
  const rightRailRect = rightRail?.getBoundingClientRect();
  const terminalScoped =
    Boolean(terminalRect && rightRailRect) &&
    terminalRect!.left >= rightRailRect!.left - 1 &&
    terminalRect!.right <= rightRailRect!.right + 1;
  record(
    checks,
    'new99-terminal-card-scoped-to-workspace',
    terminalScoped,
    terminalRect && rightRailRect
      ? `terminal=${Math.round(terminalRect.left)}..${Math.round(terminalRect.right)} right=${Math.round(rightRailRect.left)}..${Math.round(rightRailRect.right)}`
      : 'missing terminal/right rail',
  );
  navTerminalToggle?.click();
  await waitForCondition(() => !useUiStore.getState().terminalOpen, 'terminal panel closes from nav rail');
  const terminalToggle = Array.from(document.querySelectorAll<HTMLButtonElement>('.statusbar .status-button')).find(button =>
    button.textContent?.includes('Terminal'),
  );
  record(checks, 'new100-statusbar-terminal-button-removed', !terminalToggle, document.querySelector('.statusbar')?.textContent || '');
  navTerminalToggle?.click();
  await waitForCondition(
    () =>
      useUiStore.getState().terminalOpen &&
      document.querySelector('.right-rail .workspace-card[data-card="terminal"] .terminal-panel')?.getAttribute('data-open') === 'true',
    'terminal card open',
  );
  record(
    checks,
    'new54-terminal-controls-visible',
    Boolean(
      document.querySelector('.terminal-tab-strip') &&
        document.querySelector('.terminal-control-group') &&
        document.querySelector('.terminal-resizer'),
    ),
    document.querySelector('.terminal-panel')?.textContent || '',
  );
  await waitForCondition(
    () => {
      const statusText = document.querySelector('.terminal-live-status')?.textContent || '';
      return statusText.includes('PTY') || statusText.includes('SHELL');
    },
    'live terminal ready',
  );
  record(
    checks,
    'new58-terminal-live-session-ready',
    Boolean(document.querySelector('.terminal-live-output') && document.querySelector('.terminal-live-status')),
    document.querySelector('.terminal-panel')?.textContent || '',
  );
  const terminalStartedText = document.querySelector('.terminal-live-output')?.textContent || '';
  record(
    checks,
    'new73-terminal-cwd-not-backend-dist',
    !/resources[\\/]backend-dist|backend-dist[\\/]metis-backend/i.test(terminalStartedText),
    terminalStartedText,
  );
  const liveInput = document.querySelector<HTMLInputElement>('.terminal-command-input');
  const liveSendButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.terminal-input-row button')).find(button =>
    button.textContent?.includes('发送'),
  );
  record(checks, 'new58-terminal-live-input-visible', Boolean(liveInput && liveSendButton), document.querySelector('.terminal-input-row')?.textContent || '');
  const terminalMenuTrigger = document.querySelector<HTMLButtonElement>('.terminal-menu-trigger');
  terminalMenuTrigger?.click();
  await waitForCondition(() => Boolean(document.querySelector('.terminal-menu')), 'terminal menu opens');
  const terminalMenuText = document.querySelector('.terminal-menu')?.textContent || '';
  record(
    checks,
    'new100-terminal-menu-no-default-shell',
    !terminalMenuText.includes('Default shell') &&
      !terminalMenuText.includes('New terminal') &&
      document.querySelectorAll('.terminal-shell-option').length === 0,
    terminalMenuText,
  );
  record(
    checks,
    'new100-terminal-menu-row-actions',
    Boolean(
      document.querySelector('.terminal-menu-check') &&
        document.querySelector('.terminal-menu-rename') &&
        document.querySelector('.terminal-menu-delete'),
    ),
    document.querySelector('.terminal-menu')?.innerHTML.slice(0, 260) || '',
  );
  terminalMenuTrigger?.click();
  if (!liveInput || !liveSendButton) return;
  const liveOutputMarker = 'metis-pty-output-ok';
  setInputValue(liveInput, "Write-Output ([char[]](109,101,116,105,115,45,112,116,121,45,111,117,116,112,117,116,45,111,107) -join '')");
  await waitForCondition(() => !liveSendButton.disabled, 'live terminal send button enabled');
  liveSendButton.click();
  await waitForCondition(() => (document.querySelector('.terminal-live-output')?.textContent || '').includes(liveOutputMarker), 'live terminal output');
  record(
    checks,
    'new58-terminal-live-output-streams',
    (document.querySelector('.terminal-live-output')?.textContent || '').includes(liveOutputMarker),
    document.querySelector('.terminal-live-output')?.textContent || '',
  );
  const ctrlCButton = document.querySelector<HTMLButtonElement>('.terminal-control-group button[title="发送 Ctrl+C"]');
  record(checks, 'new58-terminal-ctrl-c-visible', Boolean(ctrlCButton), document.querySelector('.terminal-control-group')?.textContent || '');
  ctrlCButton?.click();
  const terminalHeightBefore = useUiStore.getState().terminalHeight;
  const terminalResizer = document.querySelector<HTMLElement>('.terminal-resizer');
  record(checks, 'new54-terminal-resizer-visible', Boolean(terminalResizer), 'terminal resizer');
  terminalResizer?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientY: 500 }));
  record(
    checks,
    'new73-terminal-resize-disables-selection',
    document.body.classList.contains('resizing-terminal'),
    document.body.className,
  );
  window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientY: 440 }));
  window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientY: 440 }));
  await waitForCondition(() => useUiStore.getState().terminalHeight > terminalHeightBefore, 'terminal height resized');
  record(
    checks,
    'new54-terminal-resize-updates-height',
    useUiStore.getState().terminalHeight > terminalHeightBefore,
    `${terminalHeightBefore} -> ${useUiStore.getState().terminalHeight}`,
  );
  const terminalResult = await window.metis.terminalRun({
    command: 'Write-Output metis-terminal-smoke',
    cwd: useSessionStore.getState().workspaces.find(item => item.id === useSessionStore.getState().activeWorkspaceId)?.path,
    shell: 'powershell',
  });
  record(
    checks,
    'new52-5-terminal-run-powershell',
    terminalResult.ok && terminalResult.stdout.includes('metis-terminal-smoke'),
    JSON.stringify(terminalResult),
  );
  ui.setTerminalOpen(false);

  useChatStore.getState().clearLocal();
  await useChatStore.getState().send('local-preview smoke');
  await waitForCondition(
    () => useUiStore.getState().rightRailMode === 'web' && useUiStore.getState().webPreviewUrl === 'http://127.0.0.1:5174',
    'local preview auto opens right rail',
  );
  record(
    checks,
    'new52-5-local-preview-auto-opens',
    useUiStore.getState().webPreviewUrl === 'http://127.0.0.1:5174',
    useUiStore.getState().webPreviewUrl,
  );

  const assistantBubble = document.querySelector<HTMLElement>('.assistant-bubble');
  const markdownBody = document.querySelector<HTMLElement>('.markdown-body');
  const bubbleStyle = assistantBubble ? getComputedStyle(assistantBubble) : null;
  const markdownStyle = markdownBody ? getComputedStyle(markdownBody) : null;
  record(
    checks,
    'new52-5-markdown-width-guard',
    Boolean(bubbleStyle && markdownStyle && bubbleStyle.minWidth === '0px' && markdownStyle.overflowWrap === 'break-word'),
    `bubble min=${bubbleStyle?.minWidth || ''} markdown wrap=${markdownStyle?.overflowWrap || ''}`,
  );
}

async function verifyStandardStream(checks: SmokeCheck[]): Promise<void> {
  const display = await sendSmokeMessageAndCaptureStatus('standard smoke', value => value.includes('连接模型中'));
  const state = useChatStore.getState();
  const assistant = lastAssistant();
  const tool = assistant?.tools?.find(item => item.callId === 'standard-call-1');
  const notice = state.memoryNotice;

  record(checks, 'standard-runtime-status-visible', display.includes('连接模型中'), display);
  record(checks, 'standard-text-delta', Boolean(assistant?.content.includes('Hello from fake backend.')), assistant?.content);
  const assistantCopyButton = document.querySelector<HTMLButtonElement>('.assistant-copy-button');
  record(
    checks,
    'assistant-copy-button-visible',
    Boolean(assistantCopyButton && assistantCopyButton.textContent?.includes('复制')),
    document.querySelector('.message-row.assistant')?.textContent || '',
  );
  assistantCopyButton?.click();
  await waitForCondition(() => (document.querySelector('.assistant-copy-button')?.textContent || '').includes('已复制'), 'assistant copy button copied state');
  record(
    checks,
    'assistant-copy-button-copies-text',
    (document.querySelector('.assistant-copy-button')?.textContent || '').includes('已复制'),
    document.querySelector('.assistant-copy-button')?.textContent || '',
  );
  record(
    checks,
    'runtime-status-ignored',
    Boolean(assistant && !assistant.content.includes('Standard runtime check') && !assistant.error),
    assistant?.error || assistant?.content || 'missing assistant',
  );
  record(checks, 'standard-tool-call', tool?.toolName === 'read_file', tool?.toolName || 'missing tool');
  record(checks, 'standard-tool-result', tool?.status === 'success' && String(tool.result).includes('metis-desktop'), String(tool?.result));
  record(checks, 'standard-usage', state.usage?.totalTokens === 24, JSON.stringify(state.usage));
  record(
    checks,
    'standard-memory-nudge',
    notice?.message === 'Smoke memory nudge' && notice.memoryCount === 1 && notice.skillCount === 1,
    JSON.stringify(notice),
  );
  record(
    checks,
    'standard-memory-paths',
    Boolean(notice?.memoryPath.includes('METIS.md') && notice.skillPath.includes('SKILL.md')),
    JSON.stringify(notice),
  );
  await verifyLearningNoticeUi(checks);
}

async function verifyRightRailWorkbench(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setSidebarOpen(true);
  ui.setSidebarWidth(284);
  ui.setRightRailOpen(true);
  ui.setRightRailMode('files');
  await waitForCondition(() => (document.querySelector('.file-tree')?.textContent || '').includes('README.md'), 'right rail file tree');
  const sidebarWidthBefore = useUiStore.getState().sidebarWidth;
  const sidebarResizer = document.querySelector<HTMLElement>('.sidebar-resizer');
  record(checks, 'new99-sidebar-resizer-visible', Boolean(sidebarResizer), 'sidebar resizer');
  sidebarResizer?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 340 }));
  record(
    checks,
    'new99-sidebar-resize-disables-selection',
    document.body.classList.contains('resizing-sidebar'),
    document.body.className,
  );
  window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 390 }));
  window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: 390 }));
  await waitForCondition(() => useUiStore.getState().sidebarWidth > sidebarWidthBefore, 'NEW-99 sidebar width resized');
  record(
    checks,
    'new99-sidebar-resize-updates-width',
    useUiStore.getState().sidebarWidth > sidebarWidthBefore && useUiStore.getState().sidebarWidth >= 220,
    `${sidebarWidthBefore} -> ${useUiStore.getState().sidebarWidth}`,
  );
  const cardMenuButton = document.querySelector<HTMLElement>('.titlebar-cards-menu-button');
  const titlebar = document.querySelector<HTMLElement>('.titlebar');
  const cardMenuRect = cardMenuButton?.getBoundingClientRect();
  const titlebarRect = titlebar?.getBoundingClientRect();
  record(
    checks,
    'right-rail-card-menu-compact',
    Boolean(
      cardMenuRect &&
        titlebarRect &&
        cardMenuRect.width >= 42 &&
        cardMenuRect.width <= 52 &&
        cardMenuRect.height <= 32 &&
        cardMenuRect.top >= titlebarRect.top,
    ),
    cardMenuRect && titlebarRect
      ? `button=${Math.round(cardMenuRect.width)}x${Math.round(cardMenuRect.height)} titlebarTop=${Math.round(titlebarRect.top)}`
      : 'missing titlebar card menu button',
  );
  const cardMenuButtonElement = document.querySelector<HTMLButtonElement>('.titlebar-cards-menu-button');
  cardMenuButtonElement?.click();
  await waitForCondition(
    () => document.querySelector('.workspace-card-menu')?.getAttribute('data-open') === 'true',
    'NEW-99 workspace card menu opens',
  );
  const cardMenuText = document.querySelector('.workspace-card-menu')?.textContent || '';
  record(
    checks,
    'new99-workspace-card-menu-visible',
    ['Preview', 'Terminal', 'Files', 'Diff', 'Background tasks', 'Plan'].every(label => cardMenuText.includes(label)),
    cardMenuText,
  );

  const workspaceCardEntries = [
    { id: 'web', label: 'Preview' },
    { id: 'terminal', label: 'Terminal' },
    { id: 'files', label: 'Files' },
    { id: 'diff', label: 'Diff' },
    { id: 'activity', label: 'Background tasks' },
    { id: 'plan', label: 'Plan' },
  ] as const;
  const hideAllWorkspaceCards = async () => {
    for (const entry of workspaceCardEntries) {
      useUiStore.getState().setWorkspaceCardVisible(entry.id, false);
    }
    useUiStore.getState().setWorkspaceCardVisible('tool', false);
    await delay(20);
  };
  const ensureCardMenuOpen = async () => {
    if (document.querySelector('.workspace-card-menu')?.getAttribute('data-open') === 'true') return;
    cardMenuButtonElement?.click();
    await waitForCondition(
      () => document.querySelector('.workspace-card-menu')?.getAttribute('data-open') === 'true',
      'NEW-99 workspace card menu reopens',
    );
  };
  const clickCardMenuItem = async (label: string) => {
    await ensureCardMenuOpen();
    const item = Array.from(document.querySelectorAll<HTMLButtonElement>('.workspace-card-menu button')).find(button =>
      button.textContent?.includes(label),
    );
    item?.click();
    await delay(40);
    return Boolean(item);
  };

  await hideAllWorkspaceCards();
  const planClicked = await clickCardMenuItem('Plan');
  const planVisibility = useUiStore.getState().workspaceCardVisibility;
  record(
    checks,
    'new99-card-menu-plan-opens-without-files',
    planClicked && useUiStore.getState().rightRailOpen && planVisibility.plan && !planVisibility.files,
    JSON.stringify(planVisibility),
  );

  await hideAllWorkspaceCards();
  const terminalClicked = await clickCardMenuItem('Terminal');
  const terminalVisibility = useUiStore.getState().workspaceCardVisibility;
  record(
    checks,
    'new99-card-menu-terminal-opens-without-files',
    terminalClicked && useUiStore.getState().rightRailOpen && terminalVisibility.terminal && !terminalVisibility.files,
    JSON.stringify(terminalVisibility),
  );

  const failedCardOpens: string[] = [];
  for (const entry of workspaceCardEntries) {
    await hideAllWorkspaceCards();
    const clicked = await clickCardMenuItem(entry.label);
    const state = useUiStore.getState();
    if (!clicked || !state.rightRailOpen || !state.workspaceCardVisibility[entry.id]) {
      failedCardOpens.push(entry.id);
    }
  }
  record(
    checks,
    'new99-card-menu-opens-every-card-from-empty',
    failedCardOpens.length === 0,
    failedCardOpens.length ? failedCardOpens.join(',') : 'all cards opened',
  );

  await hideAllWorkspaceCards();
  for (const entry of ['web', 'files', 'diff', 'activity', 'plan'] as const) {
    useUiStore.getState().setWorkspaceCardVisible(entry, true);
  }
  useUiStore.getState().setWorkspaceCardVisible('tool', true);
  if (document.querySelector('.workspace-card-menu')?.getAttribute('data-open') === 'true') {
    cardMenuButtonElement?.click();
  }
  await delay(20);
  useUiStore.getState().setWorkspaceCardRowSplit('left', 50);
  useUiStore.getState().setWorkspaceCardRowSplit('middle', 50);
  useUiStore.getState().setWorkspaceCardRowSplit('right', 50);
  await delay(20);
  record(
    checks,
    'new100-empty-tool-output-card-hidden',
    !document.querySelector('.workspace-card[data-card="tool"]'),
    document.querySelector('.workspace-card-column[data-column="right"]')?.textContent || '',
  );
  record(
    checks,
    'new100-two-card-columns-default-half-split',
    useUiStore.getState().workspaceCardRowSplits.left === 50 &&
      useUiStore.getState().workspaceCardRowSplits.middle === 50 &&
      useUiStore.getState().workspaceCardRowSplits.right === 50,
    JSON.stringify(useUiStore.getState().workspaceCardRowSplits),
  );
  useUiStore.getState().setWorkspaceCardColumnWidths({ left: 40, middle: 30 });
  await delay(20);
  const columnWidthBefore = useUiStore.getState().workspaceCardColumnWidths;
  const columnResizer = document.querySelector<HTMLElement>('.workspace-column-resizer[data-boundary="left-middle"]');
  columnResizer?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 760 }));
  window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 725 }));
  window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: 725 }));
  await waitForCondition(
    () => useUiStore.getState().workspaceCardColumnWidths.left !== columnWidthBefore.left,
    'NEW-99 workspace column resize',
  );
  useUiStore.getState().setWorkspaceCardRowSplit('left', 58);
  const rowSplitBefore = useUiStore.getState().workspaceCardRowSplits.left;
  useUiStore.getState().setTerminalOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.workspace-card[data-card="terminal"]')), 'NEW-99 terminal card before row resize');
  const rowResizer = document.querySelector<HTMLElement>('.workspace-card-column[data-column="left"] .workspace-row-resizer');
  rowResizer?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientY: 360 }));
  window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientY: 394 }));
  window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientY: 394 }));
  await waitForCondition(() => useUiStore.getState().workspaceCardRowSplits.left !== rowSplitBefore, 'NEW-99 workspace row resize');
  record(
    checks,
    'new99-column-and-row-resize-updates-store',
    useUiStore.getState().workspaceCardColumnWidths.left !== columnWidthBefore.left &&
      useUiStore.getState().workspaceCardRowSplits.left !== rowSplitBefore,
    JSON.stringify({
      before: { columnWidthBefore, rowSplitBefore },
      after: {
        widths: useUiStore.getState().workspaceCardColumnWidths,
        rows: useUiStore.getState().workspaceCardRowSplits,
      },
    }),
  );
  const terminalClose = document.querySelector<HTMLButtonElement>('.workspace-card[data-card="terminal"] .workspace-card-header button');
  terminalClose?.click();
  await waitForCondition(
    () =>
      !useUiStore.getState().workspaceCardVisibility.terminal &&
      document.querySelector('.workspace-card-column[data-column="left"]')?.getAttribute('data-count') === '1',
    'NEW-99 closing terminal expands preview column',
  );
  record(
    checks,
    'new99-card-close-expands-column',
    document.querySelector('.workspace-card-column[data-column="left"]')?.getAttribute('data-count') === '1' &&
      Boolean(document.querySelector('.workspace-card[data-card="web"]')),
    document.querySelector('.workspace-card-column[data-column="left"]')?.outerHTML.slice(0, 160) || '',
  );
  useUiStore.getState().setWorkspaceCardVisible('web', false);
  await waitForCondition(
    () => !document.querySelector('.workspace-card-column[data-column="left"]'),
    'NEW-99 hidden column unmounts when all cards are closed',
  );
  record(
    checks,
    'new99-empty-column-unmounted',
    !document.querySelector('.workspace-card-column[data-column="left"]') &&
      !(document.querySelector('.right-rail')?.textContent || '').includes('从 Cards 菜单打开卡片'),
    document.querySelector('.right-rail')?.textContent || '',
  );
  useUiStore.getState().setWorkspaceCardVisible('web', true);
  await waitForCondition(() => Boolean(document.querySelector('.workspace-card-column[data-column="left"]')), 'NEW-99 restore preview column');
  const readmeButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.tree-file')).find(button =>
    button.textContent?.includes('README.md'),
  );
  record(checks, 'right-rail-file-tree-visible', Boolean(readmeButton), document.querySelector('.file-tree')?.textContent || '');
  readmeButton?.click();
  await waitForCondition(() => (document.querySelector('.preview-pane')?.textContent || '').includes('Fake workspace README'), 'right rail file preview');
  const fileText = document.querySelector('.preview-pane')?.textContent || '';
  record(checks, 'right-rail-file-preview-content', fileText.includes('README.md') && fileText.includes('Fake workspace README'), fileText);

  const toolOpenButton = document.querySelector<HTMLButtonElement>('.tool-card-open');
  record(checks, 'right-rail-tool-open-button-visible', Boolean(toolOpenButton), document.querySelector('.message-row.assistant')?.textContent || '');
  toolOpenButton?.click();
  await waitForCondition(() => (document.querySelector('.tool-output-pane')?.textContent || '').includes('metis-desktop'), 'right rail tool output');
  const toolText = document.querySelector('.tool-output-pane')?.textContent || '';
  record(checks, 'right-rail-tool-output-content', toolText.includes('read_file') && toolText.includes('字符') && toolText.includes('metis-desktop'), toolText);

  const assistantLink = document.querySelector<HTMLAnchorElement>('.message-row.assistant a[href="https://example.com"]');
  record(checks, 'assistant-link-visible', Boolean(assistantLink), document.querySelector('.message-row.assistant')?.textContent || '');
  assistantLink?.click();
  const linkDialog = await waitForAppDialog('assistant link confirmation dialog');
  const linkDialogText = linkDialog.textContent || '';
  record(
    checks,
    'assistant-link-themed-dialog',
    linkDialogText.includes('在右栏打开链接') && linkDialogText.includes('https://example.com') && linkDialogText.includes('打开链接'),
    linkDialogText,
  );
  clickAppDialogConfirm();
  await waitForCondition(
    () => useUiStore.getState().rightRailMode === 'web' && useUiStore.getState().webPreviewUrl === 'https://example.com',
    'assistant link opens right rail web preview',
  );
  record(
    checks,
    'assistant-link-opens-right-rail',
    useUiStore.getState().rightRailMode === 'web' && useUiStore.getState().webPreviewUrl === 'https://example.com',
    useUiStore.getState().webPreviewUrl,
  );

  ui.setRightRailMode('web');
  await waitForCondition(() => Boolean(document.querySelector('.web-url-input')), 'right rail web input');
  const webInput = document.querySelector<HTMLInputElement>('.web-url-input');
  const webMoreButton = document.querySelector<HTMLButtonElement>('.web-more-button');
  record(checks, 'right-rail-web-controls-visible', Boolean(webInput && webMoreButton), 'web controls');
  if (!webInput || !webMoreButton) return;
  setInputValue(webInput, 'https://example.com');
  webInput.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }));
  await waitForCondition(
    () => document.querySelector<HTMLElement>('.web-preview-host')?.dataset.previewUrl === 'https://example.com',
    'right rail preview host url',
  );
  record(
    checks,
    'right-rail-preview-host-opens-url',
    document.querySelector<HTMLElement>('.web-preview-host')?.dataset.previewUrl === 'https://example.com',
  );
  const rightRailWidthBefore = useUiStore.getState().rightRailWidth;
  const railResizer = document.querySelector<HTMLElement>('.rail-resizer');
  record(checks, 'new73-rail-resizer-visible', Boolean(railResizer), 'rail resizer');
  railResizer?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 820 }));
  record(
    checks,
    'new73-rail-resize-disables-selection',
    document.body.classList.contains('resizing-rail'),
    document.body.className,
  );
  window.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 780 }));
  window.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, clientX: 780 }));
  await waitForCondition(() => useUiStore.getState().rightRailWidth > rightRailWidthBefore, 'right rail width resized');
  record(
    checks,
    'new73-rail-resize-updates-width',
    useUiStore.getState().rightRailWidth > rightRailWidthBefore,
    `${rightRailWidthBefore} -> ${useUiStore.getState().rightRailWidth}`,
  );
  record(
    checks,
    'new73-preview-view-ipc-enabled',
    typeof window.metis.previewSetBounds === 'function' &&
      typeof window.metis.previewLoad === 'function' &&
      typeof window.metis.previewCommand === 'function',
    'preview IPC methods',
  );
  record(
    checks,
    'new107-preview-view-main-process-hosted',
    Boolean(document.querySelector('.web-preview-host')),
    document.querySelector('.web-preview-host')?.outerHTML || 'missing preview host',
  );

  setInputValue(webInput, 'https://example.org');
  webInput.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }));
  await waitForCondition(() => useUiStore.getState().webPreviewTabs.length >= 2, 'right rail multiple web tabs');
  record(
    checks,
    'right-rail-web-tabs-multiple',
    useUiStore.getState().webPreviewTabs.length >= 2 && useUiStore.getState().webPreviewUrl === 'https://example.org',
    JSON.stringify(useUiStore.getState().webPreviewTabs),
  );

  webMoreButton.click();
  await waitForCondition(() => Boolean(document.querySelector('.web-more-menu')), 'right rail web more menu');
  const zoomOutButton = document.querySelector<HTMLButtonElement>('.web-zoom-button[title="缩小页面"]');
  const zoomInButton = document.querySelector<HTMLButtonElement>('.web-zoom-button[title="放大页面"]');
  const zoomResetButton = document.querySelector<HTMLButtonElement>('.web-zoom-reset');
  const externalButton = document.querySelector<HTMLButtonElement>('.web-external-button[title="系统浏览器打开"]');
  record(
    checks,
    'right-rail-web-toolbar-visible',
    Boolean(document.querySelector('.web-back-button') && document.querySelector('.web-forward-button') && document.querySelector('.web-reload-button') && zoomInButton && zoomOutButton && zoomResetButton && externalButton),
    `${document.querySelector('.web-url-bar')?.textContent || 'missing omnibar'} / ${document.querySelector('.web-more-menu')?.textContent || 'missing menu'}`,
  );
  if (!zoomInButton || !zoomOutButton || !zoomResetButton) return;

  const activeTabId = useUiStore.getState().activeWebPreviewId;
  const activeZoom = () => useUiStore.getState().webPreviewTabs.find(tab => tab.id === activeTabId)?.zoom || 0;
  const layoutBeforeZoom = rightRailLayoutMetrics();
  record(checks, 'right-rail-web-zoom-initial', activeZoom() === 1, JSON.stringify(useUiStore.getState().webPreviewTabs));
  zoomInButton.click();
  await waitForCondition(() => activeZoom() > 1, 'right rail zoom in');
  record(checks, 'right-rail-web-zoom-in-updates-tab', activeZoom() === 1.1, String(activeZoom()));
  const layoutAfterZoom = rightRailLayoutMetrics();
  record(
    checks,
    'new77-web-zoom-layout-stable',
    Boolean(
      layoutBeforeZoom &&
        layoutAfterZoom &&
        layoutBeforeZoom.grid === layoutAfterZoom.grid &&
        Math.abs(layoutBeforeZoom.railWidth - layoutAfterZoom.railWidth) <= 1 &&
        Math.abs(layoutBeforeZoom.mainWidth - layoutAfterZoom.mainWidth) <= 1,
    ),
    JSON.stringify({ before: layoutBeforeZoom, after: layoutAfterZoom }),
  );
  zoomOutButton.click();
  await waitForCondition(() => activeZoom() === 1, 'right rail zoom out');
  record(checks, 'right-rail-web-zoom-out-updates-tab', activeZoom() === 1, String(activeZoom()));
  zoomInButton.click();
  await waitForCondition(() => activeZoom() > 1, 'right rail zoom before reset');
  zoomResetButton.click();
  await waitForCondition(() => activeZoom() === 1, 'right rail zoom reset');
  record(checks, 'right-rail-web-zoom-reset-updates-tab', activeZoom() === 1, String(activeZoom()));

  const activeCloseButton = Array.from(document.querySelectorAll<HTMLElement>('.web-preview-tab[data-active="true"] .web-tab-close')).at(0);
  record(checks, 'right-rail-web-tab-close-visible', Boolean(activeCloseButton), document.querySelector('.web-tab-strip')?.textContent || '');
  const beforeCloseTabs = useUiStore.getState().webPreviewTabs;
  const closeTargetId = useUiStore.getState().activeWebPreviewId;
  const closeTargetIndex = beforeCloseTabs.findIndex(tab => tab.id === closeTargetId);
  const expectedTabsAfterClose = beforeCloseTabs.filter(tab => tab.id !== closeTargetId);
  const expectedActiveAfterClose = expectedTabsAfterClose[Math.min(closeTargetIndex, expectedTabsAfterClose.length - 1)]?.url || '';
  activeCloseButton?.click();
  await waitForCondition(
    () =>
      useUiStore.getState().webPreviewTabs.length === beforeCloseTabs.length - 1 &&
      !useUiStore.getState().webPreviewTabs.some(tab => tab.id === closeTargetId),
    'right rail web tab closed',
  );
  record(
    checks,
    'right-rail-web-tab-close-removes-tab',
    useUiStore.getState().webPreviewTabs.length === beforeCloseTabs.length - 1 &&
      !useUiStore.getState().webPreviewTabs.some(tab => tab.id === closeTargetId) &&
      useUiStore.getState().webPreviewUrl === expectedActiveAfterClose,
    JSON.stringify(useUiStore.getState().webPreviewTabs),
  );
  ui.setSidebarWidth(284);
  ui.setRightRailWidth(780);
  ui.setWorkspaceCardColumnWidths({ left: 40, middle: 30 });
  ui.setWorkspaceCardRowSplit('left', 58);
  ui.setRightRailMode('files');
}

async function verifyIndependentSideChat(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  const mainMessageCount = useChatStore.getState().messages.length;
  const side = useSideChatStore.getState();
  side.createSession('gpt-5.5');
  side.clearActive();
  ui.setRightRailOpen(true);
  ui.setRightRailMode('files');
  ui.setSideChatOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.side-chat-rail[data-open="true"] .side-chat-pane')), 'NEW-96 side chat rail');
  const input = document.querySelector<HTMLTextAreaElement>('.side-chat-rail[data-open="true"] .side-chat-input');
  const sendButton = document.querySelector<HTMLButtonElement>('.side-chat-rail[data-open="true"] .side-chat-send-button');
  record(
    checks,
    'new96-side-chat-controls-visible',
    Boolean(input && sendButton),
    document.querySelector('.side-chat-rail')?.textContent || 'missing side chat',
  );
  if (!input || !sendButton) return;
  setTextareaValue(input, 'new96 isolated smoke');
  sendButton.click();
  await waitForCondition(
    () => activeSideChatMessages().some(message => message.role === 'assistant' && message.content.includes('without agent context')),
    'NEW-96 side chat fake response',
  );
  const sideText = document.querySelector('.side-chat-messages')?.textContent || '';
  record(
    checks,
    'new96-side-chat-streams-response',
    sideText.includes('Side chat reply') && sideText.includes('without agent context'),
    sideText,
  );
  record(
    checks,
    'new96-side-chat-isolated',
    useChatStore.getState().messages.length === mainMessageCount,
    `${mainMessageCount} -> ${useChatStore.getState().messages.length}`,
  );

  ui.setSideChatOpen(false);
  await waitForCondition(() => Boolean(document.querySelector('.side-chat-rail[data-open="false"]')), 'NEW-98 side chat closes before titlebar toggle test');
  const titlebarChatButton = document.querySelector<HTMLButtonElement>('.titlebar-chat-toggle');
  record(checks, 'new98-titlebar-chat-toggle-visible', Boolean(titlebarChatButton), document.querySelector('.titlebar-actions')?.innerHTML || '');
  record(
    checks,
    'new98-titlebar-settings-button-removed',
    !document.querySelector('.titlebar-actions button[title="设置"]'),
    document.querySelector('.titlebar-actions')?.innerHTML || '',
  );
  record(
    checks,
    'new98-statusbar-chat-launcher-removed',
    !document.querySelector('.status-chat-launcher'),
    document.querySelector('.statusbar')?.textContent || '',
  );
  titlebarChatButton?.click();
  await waitForCondition(() => Boolean(document.querySelector('.side-chat-rail[data-open="true"] .side-chat-pane')), 'NEW-98 side chat rail opens');
  record(
    checks,
    'new98-side-chat-rail-opens-from-titlebar',
    Boolean(document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history')),
    document.querySelector('.side-chat-rail')?.textContent || 'missing side chat rail',
  );
  record(
    checks,
    'new98-side-chat-history-collapsed-by-default',
    document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history')?.getAttribute('data-open') === 'false',
    document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history')?.outerHTML.slice(0, 220) || '',
  );
  record(
    checks,
    'new98-side-chat-model-picker-removed',
    !document.querySelector('.side-chat-rail[data-open="true"] .side-chat-actions .side-chat-model-button'),
    document.querySelector('.side-chat-rail[data-open="true"] .side-chat-actions')?.textContent || '',
  );
  await waitForCondition(() => {
    const rail = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"]')?.getBoundingClientRect();
    return Boolean(rail && rail.width >= 160);
  }, 'NEW-98 side chat rail settled width');
  await waitForCondition(() => {
    const chatRect = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"]')?.getBoundingClientRect();
    const rightRect = document.querySelector<HTMLElement>('.right-rail[data-open="true"]')?.getBoundingClientRect();
    const mainRect = document.querySelector<HTMLElement>('.main-workspace-column')?.getBoundingClientRect();
    return Boolean(
      chatRect &&
        rightRect &&
        mainRect &&
        chatRect.width >= 160 &&
        chatRect.width <= 340 &&
        chatRect.left >= mainRect.right - 8 &&
        Math.abs(rightRect.left - chatRect.right) <= 8,
    );
  }, 'NEW-98 side chat rail and right rail settled bounds');
  const chatRect = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"]')?.getBoundingClientRect();
  const rightRect = document.querySelector<HTMLElement>('.right-rail[data-open="true"]')?.getBoundingClientRect();
  const mainRect = document.querySelector<HTMLElement>('.main-workspace-column')?.getBoundingClientRect();
  record(
    checks,
    'new98-side-chat-rail-coexists-with-right-rail',
    Boolean(
        chatRect &&
        rightRect &&
        mainRect &&
        chatRect.width >= 160 &&
        chatRect.width <= 340 &&
        chatRect.left >= mainRect.right - 8 &&
        Math.abs(rightRect.left - chatRect.right) <= 8,
    ),
    chatRect && rightRect && mainRect
      ? `main=${Math.round(mainRect.left)}..${Math.round(mainRect.right)} chat=${Math.round(chatRect.left)}..${Math.round(chatRect.right)} right=${Math.round(rightRect.left)}..${Math.round(rightRect.right)}`
      : 'missing layout rects',
  );
  ui.setRightRailOpen(false);
  await waitForCondition(() => {
    const shell = document.querySelector<HTMLElement>('.shell-body')?.getBoundingClientRect();
    const rail = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"]')?.getBoundingClientRect();
    return Boolean(shell && rail && Math.abs(rail.right - shell.right) <= 3);
  }, 'NEW-98 side chat docks right when right rail closes');
  const shellRect = document.querySelector<HTMLElement>('.shell-body')?.getBoundingClientRect();
  const dockedChatRect = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"]')?.getBoundingClientRect();
  record(
    checks,
    'new98-side-chat-docks-right-without-right-rail',
    Boolean(shellRect && dockedChatRect && Math.abs(dockedChatRect.right - shellRect.right) <= 3),
    shellRect && dockedChatRect ? `chatRight=${Math.round(dockedChatRect.right)} shellRight=${Math.round(shellRect.right)}` : 'missing docked rect',
  );
  ui.setRightRailOpen(true);
  await waitForCondition(() => Boolean(document.querySelector('.right-rail[data-open="true"]')), 'NEW-98 right rail restored');
  const historyToggle = document.querySelector<HTMLButtonElement>('.side-chat-rail[data-open="true"] .side-chat-history-toggle');
  record(checks, 'new98-side-chat-history-toggle-visible', Boolean(historyToggle), document.querySelector('.side-chat-history')?.textContent || '');
  historyToggle?.click();
  await waitForCondition(
    () => document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history')?.getAttribute('data-open') === 'true',
    'NEW-98 side chat history expands',
  );
  record(
    checks,
    'new98-side-chat-history-toggle-expands',
    Boolean(document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history-list')),
    document.querySelector('.side-chat-history')?.outerHTML.slice(0, 240) || '',
  );
  const activeBefore = useSideChatStore.getState().activeSessionId;
  const newButton = document.querySelector<HTMLButtonElement>('.side-chat-rail[data-open="true"] .side-chat-history-new');
  newButton?.click();
  await waitForCondition(
    () =>
      useSideChatStore.getState().activeSessionId !== activeBefore &&
      Boolean(document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history-row[data-active="true"]')),
    'NEW-97 side chat new history',
  );
  record(checks, 'new97-side-chat-history-create', useSideChatStore.getState().activeSessionId !== activeBefore);
  const activeHistoryRow = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"] .side-chat-history-row[data-active="true"]');
  const renameButton = activeHistoryRow?.querySelectorAll<HTMLButtonElement>('.side-chat-history-actions button').item(0);
  renameButton?.click();
  await waitForCondition(
    () => Boolean(document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history-row[data-active="true"] input')),
    'NEW-97 side chat rename input',
  );
  const renameInput = document.querySelector<HTMLInputElement>('.side-chat-rail[data-open="true"] .side-chat-history-row[data-active="true"] input');
  record(
    checks,
    'new97-side-chat-history-rename-input',
    Boolean(renameInput),
    document.querySelector('.side-chat-rail[data-open="true"] .side-chat-history-row[data-active="true"]')?.outerHTML || '',
  );
  if (renameInput) {
    setInputValue(renameInput, 'Dock Smoke Chat');
    renameInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    renameInput.blur();
    await waitForCondition(() => useSideChatStore.getState().sessions.some(session => session.title === 'Dock Smoke Chat'), 'NEW-97 side chat renamed');
    record(checks, 'new97-side-chat-history-rename', true);
  }
  const runtimeModelBefore = document.querySelector<HTMLButtonElement>('.statusbar .status-button')?.textContent || '';
  const modelButton = document.querySelector<HTMLButtonElement>('.side-chat-rail[data-open="true"] .side-chat-actions .side-chat-model-button');
  record(
    checks,
    'new97-side-chat-model-does-not-change-main-model',
    !modelButton && (document.querySelector<HTMLButtonElement>('.statusbar .status-button')?.textContent || '') === runtimeModelBefore,
    `${runtimeModelBefore} -> ${document.querySelector<HTMLButtonElement>('.statusbar .status-button')?.textContent || ''}; picker=${Boolean(modelButton)}`,
  );
  const deleteButton = document.querySelector<HTMLElement>('.side-chat-rail[data-open="true"] .side-chat-history-row[data-active="true"] .side-chat-history-actions button:last-child');
  const activeIdBeforeDelete = useSideChatStore.getState().activeSessionId;
  deleteButton?.click();
  await waitForCondition(() => !useSideChatStore.getState().sessions.some(session => session.id === activeIdBeforeDelete), 'NEW-97 side chat deleted');
  record(checks, 'new97-side-chat-history-delete', true);
  ui.setSideChatOpen(false);
}

function activeSideChatMessages() {
  const state = useSideChatStore.getState();
  return state.sessions.find(session => session.id === state.activeSessionId)?.messages || [];
}

function rightRailLayoutMetrics(): { grid: string; railWidth: number; mainWidth: number } | null {
  const shellBody = document.querySelector<HTMLElement>('.shell-body');
  const rail = document.querySelector<HTMLElement>('.right-rail');
  const main = document.querySelector<HTMLElement>('.main-panel');
  if (!shellBody || !rail || !main) return null;
  return {
    grid: getComputedStyle(shellBody).gridTemplateColumns,
    railWidth: Math.round(rail.getBoundingClientRect().width),
    mainWidth: Math.round(main.getBoundingClientRect().width),
  };
}

async function verifyFileChangeDiffWorkbench(checks: SmokeCheck[]): Promise<void> {
  const now = Date.now();
  useUiStore.getState().setActiveSection('chat');
  useUiStore.getState().setRightRailOpen(true);
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: [
      {
        id: 'diff-smoke-assistant',
        role: 'assistant',
        content: 'Synthetic file change smoke.',
        createdAt: now,
        tools: [
          {
            id: 'diff-smoke-tool',
            callId: 'diff-smoke-tool',
            toolName: 'write_file',
            args: {
              path: 'D:/Metis/Smoke/diff-smoke.md',
              before: '# Smoke\n\nold line\n',
              content: '# Smoke\n\nnew line\n',
            },
            result: 'Wrote D:/Metis/Smoke/diff-smoke.md',
            status: 'success',
            startedAt: now,
            finishedAt: now + 12,
            summary: 'Wrote diff smoke file',
          },
          {
            id: 'diff-smoke-tool-2',
            callId: 'diff-smoke-tool-2',
            toolName: 'edit_file',
            args: {
              path: 'D:/Metis/Smoke/settings.ts',
              before: 'export const theme = "old";\nexport const compact = false;\n',
              after: 'export const theme = "new";\nexport const compact = true;\n',
            },
            result: 'Edited D:/Metis/Smoke/settings.ts',
            status: 'success',
            startedAt: now + 13,
            finishedAt: now + 29,
            summary: 'Edited settings file',
          },
          {
            id: 'diff-smoke-tool-3',
            callId: 'diff-smoke-tool-3',
            toolName: 'delete_file',
            args: {
              path: 'D:/Metis/Smoke/obsolete.txt',
              before: 'obsolete line\nremove me\n',
            },
            result: 'Deleted D:/Metis/Smoke/obsolete.txt',
            status: 'success',
            startedAt: now + 30,
            finishedAt: now + 41,
            summary: 'Deleted obsolete file',
          },
        ],
      },
    ],
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });

  await waitForCondition(() => useUiStore.getState().rightRailMode === 'diff', 'right rail diff auto open');
  const diffText = document.querySelector('.diff-preview-pane')?.textContent || '';
  record(
    checks,
    'right-rail-diff-auto-opens-file-change',
    (diffText.includes('diff-smoke.md') || diffText.includes('settings.ts') || diffText.includes('obsolete.txt')) &&
      diffText.includes('+') &&
      diffText.includes('-'),
    diffText,
  );

  await waitForCondition(() => Boolean(document.querySelector('.file-change-review-card')), 'file change review card visible');
  const reviewCard = document.querySelector<HTMLElement>('.file-change-review-card');
  const reviewText = reviewCard?.textContent || '';
  record(
    checks,
    'new55-file-change-review-card-visible',
    reviewText.includes('已编辑 3 个文件') && reviewText.includes('+') && reviewText.includes('-') && reviewText.includes('settings.ts'),
    reviewText,
  );

  useUiStore.getState().setRightRailMode('files');
  const reviewButton = document.querySelector<HTMLButtonElement>('.file-change-review-button');
  record(checks, 'new55-file-change-review-button-visible', Boolean(reviewButton), reviewText);
  reviewButton?.click();
  await waitForCondition(() => useUiStore.getState().rightRailMode === 'diff', 'file change review button opens diff');
  record(
    checks,
    'new55-file-change-review-button-opens-diff',
    (document.querySelector('.diff-preview-pane')?.textContent || '').includes('diff-smoke.md'),
    document.querySelector('.diff-preview-pane')?.textContent || '',
  );
  const diffNavigatorText = document.querySelector('.diff-file-navigator')?.textContent || '';
  record(
    checks,
    'new59-diff-navigator-visible',
    diffNavigatorText.includes('3 个文件') && diffNavigatorText.includes('settings.ts') && diffNavigatorText.includes('obsolete.txt'),
    diffNavigatorText,
  );
  const obsoleteDiffButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.diff-file-row')).find(button =>
    button.textContent?.includes('obsolete.txt'),
  );
  record(checks, 'new59-diff-file-switch-button-visible', Boolean(obsoleteDiffButton), diffNavigatorText);
  obsoleteDiffButton?.click();
  await waitForCondition(() => (document.querySelector('.diff-preview-pane')?.textContent || '').includes('obsolete line'), 'diff navigator switches active file');
  record(
    checks,
    'new59-diff-file-switches-active-preview',
    (document.querySelector('.diff-preview-pane')?.textContent || '').includes('obsolete line') &&
      obsoleteDiffButton?.getAttribute('data-active') === 'true',
    document.querySelector('.diff-preview-pane')?.textContent || '',
  );

  const undoButton = document.querySelector<HTMLButtonElement>('.file-change-undo-button');
  record(checks, 'new55-file-change-undo-button-visible', Boolean(undoButton), reviewText);
  undoButton?.click();
  const revertDialog = await waitForAppDialog('file change revert confirmation dialog');
  record(
    checks,
    'new55-file-change-undo-themed-dialog',
    (revertDialog.textContent || '').includes('撤销文件变更') && (revertDialog.textContent || '').includes('D:/Metis/Smoke/settings.ts'),
    revertDialog.textContent || '',
  );
  clickAppDialogConfirm();
  await waitForCondition(
    () => (document.querySelector('.file-change-review-card')?.textContent || '').includes('已撤销'),
    'file change card reverted state',
  );
  record(
    checks,
    'new55-file-change-undo-marks-card-reverted',
    document.querySelector('.file-change-review-card')?.getAttribute('data-status') === 'reverted',
    document.querySelector('.file-change-review-card')?.textContent || '',
  );
  record(
    checks,
    'new56-file-change-revert-calls-backend',
    (document.querySelector('.file-change-review-card')?.textContent || '').includes('审计'),
    document.querySelector('.file-change-review-card')?.textContent || '',
  );
  record(
    checks,
    'new56-file-change-revert-success-count',
    (document.querySelector('.file-change-review-card')?.textContent || '').includes('已撤销 3 个文件'),
    document.querySelector('.file-change-review-card')?.textContent || '',
  );
  record(
    checks,
    'new59-diff-navigator-revert-statuses',
    (document.querySelector('.diff-file-navigator')?.textContent || '').includes('已撤销'),
    document.querySelector('.diff-file-navigator')?.textContent || '',
  );

  const diffButton = document.querySelector<HTMLButtonElement>('.tool-card-diff');
  record(checks, 'tool-card-diff-button-visible', Boolean(diffButton), document.querySelector('.tool-card')?.textContent || '');
  useUiStore.getState().setRightRailMode('files');
  diffButton?.click();
  await waitForCondition(() => useUiStore.getState().rightRailMode === 'diff', 'tool card diff button reopens diff');
  const reopenedText = document.querySelector('.diff-preview-pane')?.textContent || '';
  record(
    checks,
    'tool-card-diff-button-reopens-preview',
    reopenedText.includes('diff-smoke.md') && reopenedText.includes('new line'),
    reopenedText,
  );

  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: [
      {
        id: 'diff-conflict-assistant',
        role: 'assistant',
        content: 'Synthetic conflicting file change smoke.',
        createdAt: now + 100,
        tools: [
          {
            id: 'diff-conflict-tool',
            callId: 'diff-conflict-tool',
            toolName: 'edit_file',
            args: {
              path: 'D:/Metis/Smoke/settings.ts',
              before: 'export const theme = "previous";\n',
              after: 'export const theme = "agent-new";\n',
            },
            result: 'Edited D:/Metis/Smoke/settings.ts',
            status: 'success',
            startedAt: now + 100,
            finishedAt: now + 112,
            summary: 'Edited conflicting settings file',
          },
          {
            id: 'diff-blocked-tool',
            callId: 'diff-blocked-tool',
            toolName: 'write_file',
            args: {
              path: 'D:/Metis/Smoke/.env',
              before: '',
              content: 'SECRET=smoke',
            },
            result: 'Wrote D:/Metis/Smoke/.env',
            status: 'success',
            startedAt: now + 113,
            finishedAt: now + 120,
            summary: 'Wrote blocked env file',
          },
        ],
      },
    ],
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });
  await waitForCondition(
    () => (document.querySelector('.thread-window')?.textContent || '').includes('.env'),
    'conflicting file change review card visible',
  );
  const currentConflictCard = () => Array.from(document.querySelectorAll<HTMLElement>('.file-change-review-card')).find(card =>
    card.textContent?.includes('.env'),
  ) || null;
  const conflictReview = currentConflictCard()?.querySelector<HTMLButtonElement>('.file-change-review-button') || null;
  conflictReview?.click();
  await waitForCondition(() => (document.querySelector('.diff-file-navigator')?.textContent || '').includes('.env'), 'conflicting diff navigator visible');
  const conflictUndo = currentConflictCard()?.querySelector<HTMLButtonElement>('.file-change-undo-button') || null;
  conflictUndo?.click();
  const conflictDialog = await waitForAppDialog('file change conflict revert confirmation dialog');
  record(
    checks,
    'new59-conflict-revert-dialog-visible',
    (conflictDialog.textContent || '').includes('.env') && (conflictDialog.textContent || '').includes('settings.ts'),
    conflictDialog.textContent || '',
  );
  clickAppDialogConfirm();
  await waitForCondition(
    () => (currentConflictCard()?.textContent || '').includes('撤销未完成'),
    'file change card conflict state',
  );
  const conflictCardText = currentConflictCard()?.textContent || '';
  const conflictRailText = document.querySelector('.diff-preview-pane')?.textContent || '';
  record(
    checks,
    'new59-conflict-revert-card-detail',
    conflictCardText.includes('撤销失败') && conflictCardText.includes('冲突') && conflictCardText.includes('拦截'),
    conflictCardText,
  );
  record(
    checks,
    'new59-conflict-revert-rail-detail',
    (document.querySelector('.diff-file-navigator')?.textContent || '').includes('冲突') &&
      (document.querySelector('.diff-file-navigator')?.textContent || '').includes('已拦截') &&
      (conflictRailText.includes('file changed after agent edit') || conflictRailText.includes('secret-bearing filename')),
    conflictRailText,
  );
}

async function verifyLocalHtmlAutoPreview(checks: SmokeCheck[]): Promise<void> {
  const now = Date.now();
  useUiStore.getState().setActiveSection('chat');
  useUiStore.getState().setRightRailOpen(true);
  useUiStore.getState().setRightRailMode('files');
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: [
      {
        id: 'html-preview-assistant',
        role: 'assistant',
        content: 'Synthetic local HTML preview smoke.',
        createdAt: now,
        tools: [
          {
            id: 'html-preview-tool',
            callId: 'html-preview-tool',
            toolName: 'write_file',
            args: {
              path: 'ui-smoke.html',
              content: '<!doctype html><html><body><h1>Metis UI smoke</h1></body></html>',
            },
            result: 'Wrote ui-smoke.html',
            status: 'success',
            startedAt: now,
            finishedAt: now + 10,
            summary: 'Wrote local HTML preview file',
          },
        ],
      },
    ],
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });

  await waitForCondition(
    () => useUiStore.getState().rightRailMode === 'web' && useUiStore.getState().webPreviewUrl.includes('/file-preview?path=ui-smoke.html'),
    'local HTML write opens web preview',
  );
  record(
    checks,
    'new74-local-html-write-auto-opens-web-preview',
    useUiStore.getState().rightRailMode === 'web' && useUiStore.getState().webPreviewUrl.includes('/file-preview?path=ui-smoke.html'),
    useUiStore.getState().webPreviewUrl,
  );
}

async function verifySubagentParallelUi(checks: SmokeCheck[]): Promise<void> {
  await useSessionStore.getState().selectSession('smoke-session');
  await useChatStore.getState().loadSession('smoke-session');
  useChatStore.getState().stop();
  await cancelActiveRuns('smoke-session');
  await delay(80);
  useChatStore.getState().clearLocal();
  useChatStore.getState().clearSubagents();
  useChatStore.getState().clearMemoryNotice();
  useUiStore.getState().setActiveSection('chat');
  const subagentRun = await startChatRun({
    assistant_id: 'assistant-subagent-smoke',
    message: 'subagent smoke',
    session_id: 'smoke-session',
  });
  await useChatStore.getState().loadSession('smoke-session');

  await waitForCondition(
    () => {
      const subagents = useChatStore.getState().subagents;
      return subagents.length === 3 && subagents.some(item => item.status === 'running' && item.progress > 5);
    },
    'subagent intermediate progress',
  );
  const midSubagents = useChatStore.getState().subagents;
  record(
    checks,
    'subagent-progress-events-consumed',
    midSubagents.length === 3 && midSubagents.some(item => item.progress > 5 && item.progress < 100),
    JSON.stringify(midSubagents),
  );

  await waitForRunTerminal(subagentRun.runId);
  await waitForCondition(
    () => {
      const subagents = useChatStore.getState().subagents;
      return subagents.length === 3 && subagents.every(item => item.status === 'done' && item.progress === 100);
    },
    'subagent done state',
  );
  await waitForCondition(() => (document.querySelector('.subagent-group')?.textContent || '').includes('3/3 完成'), 'subagent panel summary');
  const subagentText = document.querySelector('.subagent-group')?.textContent || '';
  record(
    checks,
    'subagent-panel-summary',
    subagentText.includes('并行子代理') && subagentText.includes('3/3 完成') && subagentText.includes('100%'),
    subagentText,
  );
  record(checks, 'new82-subagent-strip-is-compact', document.querySelectorAll('.subagent-group .subagent-card').length === 0, subagentText);

  const activityButton = document.querySelector<HTMLButtonElement>('.subagent-open-activity-button');
  record(checks, 'new82-subagent-activity-button-visible', Boolean(activityButton), subagentText);
  activityButton?.click();
  await waitForCondition(() => (document.querySelector('.subagent-activity-panel')?.textContent || '').includes('3/3 完成'), 'subagent activity rail');
  const activityText = document.querySelector('.subagent-activity-panel')?.textContent || '';
  record(checks, 'subagent-card-count', document.querySelectorAll('.subagent-activity-panel .subagent-card').length === 3, activityText);

  const openButton = document.querySelector<HTMLButtonElement>('.subagent-open-button');
  record(checks, 'subagent-open-button-visible', Boolean(openButton), activityText);
  openButton?.click();
  await waitForCondition(() => (document.querySelector('.subagent-result')?.textContent || '').includes('Found chatStore'), 'subagent result expanded');
  const resultText = document.querySelector('.subagent-result')?.textContent || '';
  record(checks, 'subagent-result-expands', resultText.includes('Found chatStore'), resultText);

  const rightRailButton = document.querySelector<HTMLButtonElement>('.subagent-right-rail-button');
  record(checks, 'subagent-right-rail-button-visible', Boolean(rightRailButton), subagentText);
  rightRailButton?.click();
  await waitForCondition(
    () => (document.querySelector('.tool-output-pane')?.textContent || '').includes('Found chatStore'),
    'subagent result in right rail',
  );
  const toolText = document.querySelector('.tool-output-pane')?.textContent || '';
  record(checks, 'subagent-result-opens-right-rail', toolText.includes('Subagent') && toolText.includes('Found chatStore'), toolText);

  const dismissButton = document.querySelector<HTMLButtonElement>('.subagent-dismiss-button');
  record(checks, 'new82-subagent-dismiss-visible', Boolean(dismissButton), subagentText);
  dismissButton?.click();
  await waitForCondition(() => !document.querySelector('.subagent-group'), 'subagent strip dismissed');
  record(checks, 'new82-subagent-strip-dismisses', !document.querySelector('.subagent-group'), 'dismissed');
}

async function verifyCronAutomation(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setActiveSection('cron');
  await waitForCondition(() => Boolean(document.querySelector('.cron-panel')), 'cron panel');

  const nameInput = document.querySelector<HTMLInputElement>('.cron-name-input');
  const scheduleSelect = document.querySelector<HTMLSelectElement>('.cron-schedule-select');
  const promptInput = document.querySelector<HTMLTextAreaElement>('.cron-prompt-input');
  const submitButton = document.querySelector<HTMLButtonElement>('.cron-submit-button');
  record(checks, 'cron-form-controls-visible', Boolean(nameInput && scheduleSelect && promptInput && submitButton), 'cron form controls');
  if (!nameInput || !scheduleSelect || !promptInput || !submitButton) return;

  setInputValue(nameInput, 'Smoke Cron Task');
  setSelectValue(scheduleSelect, 'every 30 minutes');
  setTextareaValue(promptInput, 'Run smoke cron');
  await waitForCondition(() => !submitButton.disabled, 'cron create enabled');
  submitButton.click();
  await waitForCondition(() => (document.querySelector('.cron-list')?.textContent || '').includes('Smoke Cron Task'), 'cron task created');
  const createdText = document.querySelector('.cron-list')?.textContent || '';
  record(checks, 'cron-create-adds-row', createdText.includes('Smoke Cron Task') && createdText.includes('每 30 分钟'), createdText);

  const editButton = document.querySelector<HTMLButtonElement>('.cron-edit-button');
  record(checks, 'cron-edit-button-visible', Boolean(editButton), createdText);
  editButton?.click();
  await waitForCondition(() => document.querySelector<HTMLInputElement>('.cron-name-input')?.value === 'Smoke Cron Task', 'cron edit form populated');
  const editNameInput = document.querySelector<HTMLInputElement>('.cron-name-input');
  const editPromptInput = document.querySelector<HTMLTextAreaElement>('.cron-prompt-input');
  const editSubmitButton = document.querySelector<HTMLButtonElement>('.cron-submit-button');
  if (!editNameInput || !editPromptInput || !editSubmitButton) return;
  setInputValue(editNameInput, 'Smoke Cron Task Edited');
  setTextareaValue(editPromptInput, 'Run edited smoke cron');
  await waitForCondition(() => !editSubmitButton.disabled, 'cron edit enabled');
  editSubmitButton.click();
  await waitForCondition(() => (document.querySelector('.cron-list')?.textContent || '').includes('Smoke Cron Task Edited'), 'cron task edited');
  const editedText = document.querySelector('.cron-list')?.textContent || '';
  record(checks, 'cron-edit-updates-row', editedText.includes('Smoke Cron Task Edited') && editedText.includes('Run edited smoke cron'), editedText);

  const toggleButton = document.querySelector<HTMLButtonElement>('.cron-toggle-button');
  record(checks, 'cron-toggle-button-visible', Boolean(toggleButton), editedText);
  toggleButton?.click();
  await waitForCondition(() => (document.querySelector('.cron-list')?.textContent || '').includes('停用'), 'cron task toggled');
  record(checks, 'cron-toggle-disables-row', (document.querySelector('.cron-list')?.textContent || '').includes('停用'));

  const runButton = document.querySelector<HTMLButtonElement>('.cron-run-button');
  record(checks, 'cron-run-button-visible', Boolean(runButton), document.querySelector('.cron-list')?.textContent || '');
  runButton?.click();
  await waitForCondition(
    () => useUiStore.getState().activeSection === 'chat' && useSessionStore.getState().activeSessionId === 'cron-result-session',
    'cron run result session',
  );
  await waitForCondition(
    () => (lastAssistant()?.content || '').includes('Cron smoke result saved by the fake backend.'),
    'cron run chat result loaded',
  );
  record(
    checks,
    'cron-run-opens-result-session',
    useSessionStore.getState().activeSessionId === 'cron-result-session',
    useSessionStore.getState().activeSessionId || '',
  );

  ui.setActiveSection('cron');
  await waitForCondition(() => (document.querySelector('.cron-list')?.textContent || '').includes('Smoke Cron Task Edited'), 'cron task before delete');
  const deleteButton = document.querySelector<HTMLButtonElement>('.cron-delete-button');
  record(checks, 'cron-delete-button-visible', Boolean(deleteButton), document.querySelector('.cron-list')?.textContent || '');

  deleteButton?.click();
  const cronDialog = await waitForAppDialog('cron delete confirmation dialog');
  const cronDialogText = cronDialog.textContent || '';
  record(
    checks,
    'cron-delete-themed-dialog',
    cronDialogText.includes('删除定时任务') && cronDialogText.includes('Smoke Cron Task Edited') && cronDialogText.includes('删除'),
    cronDialogText,
  );
  clickAppDialogConfirm();
  await waitForCondition(() => (document.querySelector('.cron-list')?.textContent || '').includes('暂无定时任务'), 'cron task deleted');
  const deletedText = document.querySelector('.cron-list')?.textContent || '';
  record(checks, 'cron-delete-removes-row', deletedText.includes('暂无定时任务') && !deletedText.includes('Smoke Cron Task Edited'), deletedText);

  await useSessionStore.getState().selectSession('smoke-session');
  await useChatStore.getState().loadSession('smoke-session');
  ui.setActiveSection('chat');
}

async function verifySkillDelete(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setActiveSection('skills');
  await waitForCondition(() => (document.querySelector('[data-zone="skills"]')?.textContent || '').includes('Smoke Skill'), 'skill before delete');

  const skillList = () => document.querySelector<HTMLElement>('[data-zone="skills"] .skill-list-live');
  const skillRows = () => Array.from(skillList()?.querySelectorAll<HTMLElement>('.skill-row') || []);
  const activeDeleteButton = () =>
    Array.from(skillList()?.querySelectorAll<HTMLButtonElement>('.skill-delete-button') || []).find(button => !button.disabled) || null;
  let deleteButton = activeDeleteButton();
  record(checks, 'skill-delete-button-visible', Boolean(deleteButton), document.querySelector('[data-zone="skills"]')?.textContent || '');

  let sawSkillDialog = false;
  while (deleteButton) {
    const beforeCount = skillRows().length;
    const targetRow = deleteButton.closest<HTMLElement>('.skill-row');
    const targetRowText = targetRow?.textContent || '';
    const targetRowPath = targetRow?.querySelector('span')?.textContent?.trim() || targetRowText;
    deleteButton.click();
    const skillDialog = await waitForAppDialog('skill delete confirmation dialog');
    const skillDialogText = skillDialog.textContent || '';
    if (!sawSkillDialog) {
      record(
        checks,
        'skill-delete-themed-dialog',
        skillDialogText.includes('删除本地技能') && skillDialogText.includes('SKILL.md') && skillDialogText.includes('删除'),
        skillDialogText,
      );
      sawSkillDialog = true;
    }
    clickAppDialogConfirm();
    await waitForCondition(
      () => {
        const rows = skillRows();
        const targetGone = targetRowPath ? rows.every(row => !(row.textContent || '').includes(targetRowPath)) : rows.length < beforeCount;
        return rows.length < beforeCount || targetGone;
      },
      'one skill row deleted',
    );
    await waitForCondition(
      () => skillRows().length === 0 || Boolean(activeDeleteButton()),
      'skill delete controls settled',
    );
    await waitForCondition(
      () =>
        Array.from(skillList()?.querySelectorAll<HTMLButtonElement>('.skill-delete-button') || []).every(
          button => !button.disabled && !button.textContent?.includes('删除中'),
        ),
      'skill delete buttons idle',
    );
    await delay(80);
    deleteButton = activeDeleteButton();
  }

  await waitForCondition(
    () => {
      const text = skillList()?.textContent || '';
      return !text.includes('Smoke Skill') && text.includes('暂无本地技能');
    },
    'skill deleted empty state',
  );
  const skillsText = skillList()?.textContent || '';
  record(checks, 'skill-delete-removes-row', !skillsText.includes('Smoke Skill') && skillsText.includes('暂无本地技能'), skillsText);
  ui.setActiveSection('chat');
}

async function verifySkillManagement(checks: SmokeCheck[]): Promise<void> {
  const ui = useUiStore.getState();
  ui.setActiveSection('skills');
  await waitForCondition(() => (document.querySelector('[data-zone="skills"]')?.textContent || '').includes('Smoke Skill'), 'skill management list');

  const detailButton = document.querySelector<HTMLButtonElement>('.skill-detail-button');
  record(checks, 'skill-detail-button-visible', Boolean(detailButton), document.querySelector('[data-zone="skills"]')?.textContent || '');
  detailButton?.click();
  await waitForCondition(() => (document.querySelector<HTMLTextAreaElement>('.skill-editor')?.value || '').includes('# Smoke Skill'), 'skill detail editor');
  const editor = document.querySelector<HTMLTextAreaElement>('.skill-editor');
  record(checks, 'skill-detail-loads-content', Boolean(editor?.value.includes('## Workflow')), editor?.value || '');
  if (!editor) return;

  const edited = `${editor.value.trim()}\n\n- NEW-31 smoke edit saved.\n`;
  setTextareaValue(editor, edited);
  const saveButton = document.querySelector<HTMLButtonElement>('.skill-save-button');
  record(checks, 'skill-save-button-enabled', Boolean(saveButton && !saveButton.disabled), saveButton?.outerHTML || 'missing save');
  saveButton?.click();
  await waitForCondition(
    () => (document.querySelector<HTMLTextAreaElement>('.skill-editor')?.value || '').includes('NEW-31 smoke edit saved'),
    'skill save result',
  );
  record(checks, 'skill-edit-save-persists', true, document.querySelector<HTMLTextAreaElement>('.skill-editor')?.value || '');

  const toggleButton = document.querySelector<HTMLButtonElement>('.skill-toggle-button');
  record(checks, 'skill-toggle-button-visible', Boolean(toggleButton), toggleButton?.outerHTML || 'missing toggle');
  toggleButton?.click();
  await waitForCondition(() => (document.querySelector('.skill-detail-panel')?.textContent || '').includes('停用'), 'skill toggled disabled');
  record(checks, 'skill-toggle-updates-status', (document.querySelector('.skill-detail-panel')?.textContent || '').includes('停用'));

  const openButton = document.querySelector<HTMLButtonElement>('.skill-open-folder-button');
  record(checks, 'skill-open-folder-button-visible', Boolean(openButton), openButton?.outerHTML || 'missing open folder');
  openButton?.click();
  await delay(40);

  const importInput = document.querySelector<HTMLInputElement>('.skill-import-input');
  const importButton = document.querySelector<HTMLButtonElement>('.skill-import-button');
  record(checks, 'skill-import-controls-visible', Boolean(importInput && importButton), 'import controls');
  if (!importInput || !importButton) return;
  setInputValue(importInput, 'D:\\Metis\\ImportedSmokeSkill');
  await waitForCondition(() => !importButton.disabled, 'skill import button enabled');
  importButton.click();
  await waitForCondition(() => (document.querySelector('[data-zone="skills"]')?.textContent || '').includes('Imported Smoke Skill'), 'imported skill visible');
  const skillsText = document.querySelector('[data-zone="skills"]')?.textContent || '';
  record(checks, 'skill-import-adds-row', skillsText.includes('Imported Smoke Skill'), skillsText);
}

async function verifyLearningNoticeUi(checks: SmokeCheck[]): Promise<void> {
  await waitForCondition(() => Boolean(document.querySelector('.learning-notice')), 'learning notice');
  const noticeText = document.querySelector('.learning-notice')?.textContent || '';
  record(
    checks,
    'learning-notice-visible',
    noticeText.includes('Smoke memory nudge') && noticeText.includes('记忆 +1') && noticeText.includes('技能 +1'),
    noticeText,
  );

  const memoryButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.learning-actions button')).find(button =>
    button.textContent?.includes('查看记忆'),
  );
  record(checks, 'learning-notice-memory-button', Boolean(memoryButton), noticeText);
  memoryButton?.click();
  await waitForCondition(
    () =>
      useUiStore.getState().settingsOpen &&
      document.querySelector('.settings-dialog')?.getAttribute('data-active-section') === 'conversation',
    'memory settings panel',
  );
  await waitForCondition(
    () =>
      Array.from(document.querySelectorAll<HTMLTextAreaElement>('.settings-dialog textarea'))
        .map(textarea => textarea.value)
        .join('\n')
        .includes('Smoke memory'),
    'memory textareas loaded',
  );
  const settingsText = document.querySelector('.settings-dialog')?.textContent || '';
  const memoryValues = Array.from(document.querySelectorAll<HTMLTextAreaElement>('.settings-dialog textarea'))
    .map(textarea => textarea.value)
    .join('\n');
  record(
    checks,
    'learning-memory-panel-opens',
    memoryValues.includes('Smoke memory') && settingsText.includes('METIS.md'),
    `${settingsText}\n${memoryValues}`,
  );
  useUiStore.getState().setSettingsOpen(false);
  await delay(40);

  const skillsButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.learning-actions button')).find(button =>
    button.textContent?.includes('查看技能'),
  );
  record(checks, 'learning-notice-skills-button', Boolean(skillsButton), noticeText);
  skillsButton?.click();
  await waitForCondition(() => useUiStore.getState().activeSection === 'skills', 'skills section switch from notice');
  await waitForCondition(() => (document.querySelector('[data-zone="skills"]')?.textContent || '').includes('Smoke Skill'), 'skills from notice');
  const skillsText = document.querySelector('[data-zone="skills"]')?.textContent || '';
  record(checks, 'learning-skills-panel-opens', skillsText.includes('Smoke Skill') && skillsText.includes('自学习'), skillsText);
  useUiStore.getState().setActiveSection('chat');
}

async function verifyLegacyStream(checks: SmokeCheck[]): Promise<void> {
  const display = await sendSmokeMessageAndCaptureStatus('legacy smoke', value => value.includes('连接模型中'));
  const assistant = lastAssistant();
  const tool = assistant?.tools?.find(item => item.callId === 'legacy-call-1');

  record(checks, 'legacy-runtime-status-visible', display.includes('连接模型中'), display);
  record(checks, 'legacy-text-delta', Boolean(assistant?.content.includes('Legacy stream OK.')), assistant?.content);
  record(
    checks,
    'legacy-runtime-status-ignored',
    Boolean(assistant && !assistant.content.includes('Legacy runtime check') && !assistant.error),
    assistant?.error || assistant?.content || 'missing assistant',
  );
  record(checks, 'legacy-tool-result', tool?.toolName === 'list_directory' && tool.status === 'success', JSON.stringify(tool));
}

async function verifyStreamError(checks: SmokeCheck[]): Promise<void> {
  const display = await sendSmokeMessageAndCaptureStatus('error smoke', value => value.includes('已失败'));
  const assistant = lastAssistant();
  const runtimeStatus = useChatStore.getState().runtimeStatus;
  record(checks, 'error-runtime-status-visible', display.includes('已失败'), display);
  record(checks, 'error-runtime-status-final', runtimeStatus?.severity === 'error', JSON.stringify(runtimeStatus));
  record(
    checks,
    'stream-error',
    Boolean(assistant?.error?.includes('Smoke stream error') && assistant.content.includes('Smoke stream error')),
    assistant?.error || assistant?.content || 'missing assistant error',
  );
  record(
    checks,
    'error-runtime-status-ignored',
    Boolean(assistant && !assistant.content.includes('Smoke runtime failed') && !assistant.error?.includes('Smoke runtime failed')),
    assistant?.error || assistant?.content || 'missing assistant',
  );
}

async function verifyToolErrorStream(checks: SmokeCheck[]): Promise<void> {
  const display = await sendSmokeMessageAndCaptureStatus('tool-error smoke', value => value.includes('运行工具'));
  const assistant = lastAssistant();
  const tool = assistant?.tools?.find(item => item.callId === 'error-call-1');
  const summary = tool?.summary || '';
  const errorHint = tool?.errorHint || '';

  record(checks, 'tool-error-runtime-status-visible', display.includes('运行工具'), display);
  record(checks, 'tool-error-call', tool?.toolName === 'read_file', tool?.toolName || 'missing tool');
  record(checks, 'tool-error-status', tool?.status === 'error', JSON.stringify(tool));
  record(checks, 'tool-error-summary', summary.includes('Error executing read_file'), summary || 'missing summary');
  record(checks, 'tool-error-hint', errorHint.length > 0, errorHint || 'missing error hint');
  record(
    checks,
    'tool-error-output-not-in-content',
    Boolean(assistant && !assistant.content.includes('Error executing read_file')),
    assistant?.content || 'missing assistant',
  );
}

async function verifyToolPermissionApprovals(checks: SmokeCheck[]): Promise<void> {
  useChatStore.getState().clearLocal();
  useChatStore.getState().clearMemoryNotice();
  const allowPromise = useChatStore.getState().send('permission-allow smoke');
  const allowDialog = await waitForAppDialog('tool permission allow dialog');
  const allowDialogText = allowDialog.textContent || '';
  const allowWaitingTool = lastAssistant()?.tools?.find(item => item.callId === 'permission-allow-call-1');
  record(
    checks,
    'permission-allow-themed-dialog',
    allowDialogText.includes('允许工具执行') && allowDialogText.includes('write_file'),
    allowDialogText,
  );
  record(
    checks,
    'permission-allow-waiting-state',
    allowWaitingTool?.status === 'waiting_approval' && allowWaitingTool.requestId === 'perm-allow-1',
    JSON.stringify(allowWaitingTool),
  );
  clickAppDialogConfirm();
  await allowPromise;
  const allowTool = lastAssistant()?.tools?.find(item => item.callId === 'permission-allow-call-1');
  record(
    checks,
    'permission-allow-tool-success',
    allowTool?.status === 'success' && String(allowTool.result).includes('Fake permission approved'),
    JSON.stringify(allowTool),
  );
  record(
    checks,
    'permission-allow-content',
    Boolean(lastAssistant()?.content.includes('Permission approved smoke complete.')),
    lastAssistant()?.content || '',
  );

  useChatStore.getState().clearLocal();
  const denyPromise = useChatStore.getState().send('permission-deny smoke');
  const denyDialog = await waitForAppDialog('tool permission deny dialog');
  const denyDialogText = denyDialog.textContent || '';
  const denyWaitingTool = lastAssistant()?.tools?.find(item => item.callId === 'permission-deny-call-1');
  record(
    checks,
    'permission-deny-themed-dialog',
    denyDialogText.includes('允许工具执行') && denyDialogText.includes('delete_file'),
    denyDialogText,
  );
  record(
    checks,
    'permission-deny-waiting-state',
    denyWaitingTool?.status === 'waiting_approval' && denyWaitingTool.requestId === 'perm-deny-1',
    JSON.stringify(denyWaitingTool),
  );
  clickAppDialogCancel();
  await denyPromise;
  const denyTool = lastAssistant()?.tools?.find(item => item.callId === 'permission-deny-call-1');
  record(
    checks,
    'permission-deny-tool-error',
    denyTool?.status === 'error' && String(denyTool.result).includes('Permission denied'),
    JSON.stringify(denyTool),
  );
  record(
    checks,
    'permission-deny-content',
    Boolean(lastAssistant()?.content.includes('Permission denied smoke complete.')),
    lastAssistant()?.content || '',
  );

  useChatStore.getState().clearLocal();
  const rememberAllowPromise = useChatStore.getState().send('permission-remember-allow smoke');
  const rememberAllowDialog = await waitForAppDialog('tool permission remember allow dialog');
  selectAppDialogChoice('always_allow');
  record(
    checks,
    'permission-remember-allow-choice',
    (rememberAllowDialog.textContent || '').includes('本工作区总是允许'),
    rememberAllowDialog.textContent || '',
  );
  clickAppDialogConfirm();
  await rememberAllowPromise;
  const allowState = await getPermissions();
  const allowRule = allowState.rules.find(rule => rule.tool === 'write_file' && rule.action === 'allow');
  record(checks, 'permission-remember-allow-rule', Boolean(allowRule), JSON.stringify(allowState.rules));
  record(
    checks,
    'permission-remember-allow-audit',
    allowState.audit.some(entry => entry.requestId === 'perm-remember-allow-1' && entry.remember === 'allow'),
    JSON.stringify(allowState.audit.slice(0, 4)),
  );

  useChatStore.getState().clearLocal();
  const rememberDenyPromise = useChatStore.getState().send('permission-remember-deny smoke');
  const rememberDenyDialog = await waitForAppDialog('tool permission remember deny dialog');
  selectAppDialogChoice('always_deny');
  record(
    checks,
    'permission-remember-deny-choice',
    (rememberDenyDialog.textContent || '').includes('本工作区总是拒绝'),
    rememberDenyDialog.textContent || '',
  );
  clickAppDialogConfirm();
  await rememberDenyPromise;
  const denyState = await getPermissions();
  const denyRule = denyState.rules.find(rule => rule.tool === 'delete_file' && rule.action === 'deny');
  const rememberDenyTool = lastAssistant()?.tools?.find(item => item.callId === 'permission-remember-deny-call-1');
  record(checks, 'permission-remember-deny-rule', Boolean(denyRule), JSON.stringify(denyState.rules));
  record(
    checks,
    'permission-remember-deny-tool-error',
    rememberDenyTool?.status === 'error' && String(rememberDenyTool.result).includes('Permission denied'),
    JSON.stringify(rememberDenyTool),
  );
  record(
    checks,
    'permission-remember-deny-audit',
    denyState.audit.some(entry => entry.requestId === 'perm-remember-deny-1' && entry.remember === 'deny'),
    JSON.stringify(denyState.audit.slice(0, 4)),
  );

  if (allowRule) await deletePermissionRule(allowRule.id);
  const afterAllowDelete = await getPermissions();
  record(
    checks,
    'permission-delete-allow-rule',
    !afterAllowDelete.rules.some(rule => rule.id === allowRule?.id),
    JSON.stringify(afterAllowDelete.rules),
  );
  if (denyRule) await deletePermissionRule(denyRule.id);
  const afterDenyDelete = await getPermissions();
  record(
    checks,
    'permission-delete-deny-rule',
    !afterDenyDelete.rules.some(rule => rule.id === denyRule?.id),
    JSON.stringify(afterDenyDelete.rules),
  );

  const ui = useUiStore.getState();
  ui.setSettingsSection('tools');
  ui.setSettingsOpen(true);
  await waitForCondition(
    () =>
      document.querySelector('.settings-dialog')?.getAttribute('data-active-section') === 'tools' &&
      (document.querySelector('.permission-panel')?.textContent || '').includes('权限中心'),
    'NEW-53 permission center panel',
  );
  const permissionPanelText = document.querySelector('.permission-panel')?.textContent || '';
  record(
    checks,
    'new53-permission-center-visible',
    permissionPanelText.includes('权限中心') && permissionPanelText.includes('手动添加规则') && permissionPanelText.includes('审计日志'),
    permissionPanelText,
  );
  const templateText = document.querySelector('.permission-policy-templates')?.textContent || '';
  record(
    checks,
    'new57-permission-policy-templates-visible',
    templateText.includes('写入前确认') && templateText.includes('拒绝环境密钥'),
    templateText,
  );
  const envTemplate = Array.from(document.querySelectorAll<HTMLButtonElement>('.permission-template-button')).find(button =>
    button.textContent?.includes('拒绝环境密钥'),
  );
  record(checks, 'new57-permission-template-button-visible', Boolean(envTemplate), templateText);
  envTemplate?.click();
  await waitForCondition(
    () => (document.querySelector('.permission-panel')?.textContent || '').includes('path=*.env'),
    'permission template creates env deny rule',
  );
  const templateState = await getPermissions();
  const templateRule = templateState.rules.find(
    rule => rule.tool === '*' && rule.action === 'deny' && rule.source === 'policy_template' && rule.argsMatch.path === '*.env',
  );
  record(checks, 'new57-permission-template-persists-scoped-rule', Boolean(templateRule), JSON.stringify(templateState.rules));
  if (templateRule) await deletePermissionRule(templateRule.id);

  const searchInput = document.querySelector<HTMLInputElement>('.permission-search-input');
  record(checks, 'new53-permission-search-visible', Boolean(searchInput), permissionPanelText);
  if (!searchInput) return;
  setInputValue(searchInput, 'delete_file');
  await waitForCondition(() => (document.querySelector('.permission-panel')?.textContent || '').includes('delete_file'), 'permission search filters audit');
  record(
    checks,
    'new53-permission-search-filters',
    (document.querySelector('.permission-panel')?.textContent || '').includes('delete_file'),
    document.querySelector('.permission-panel')?.textContent || '',
  );
  setInputValue(searchInput, '');

  const askFilterButton = Array.from(document.querySelectorAll<HTMLButtonElement>('.permission-filter button')).find(button =>
    button.textContent?.includes('每次询问'),
  );
  record(checks, 'new53-permission-action-filter-visible', Boolean(askFilterButton), document.querySelector('.permission-filter')?.textContent || '');
  askFilterButton?.click();
  await delay(20);

  const toolInput = document.querySelector<HTMLInputElement>('.permission-new-tool-input');
  const actionSelect = document.querySelector<HTMLSelectElement>('.permission-new-action-select');
  const argKeyInput = document.querySelector<HTMLInputElement>('.permission-new-arg-key-input');
  const argPatternInput = document.querySelector<HTMLInputElement>('.permission-new-arg-pattern-input');
  const createButton = document.querySelector<HTMLButtonElement>('.permission-create-button');
  record(checks, 'new53-permission-create-controls-visible', Boolean(toolInput && actionSelect && createButton), 'manual rule controls');
  record(
    checks,
    'new57-permission-path-scope-controls-visible',
    Boolean(argKeyInput && argPatternInput),
    document.querySelector('.permission-create-rule')?.textContent || '',
  );
  if (!toolInput || !actionSelect || !argKeyInput || !argPatternInput || !createButton) return;
  setInputValue(toolInput, 'grep_search');
  actionSelect.value = 'ask';
  actionSelect.dispatchEvent(new Event('change', { bubbles: true }));
  setInputValue(argKeyInput, 'path');
  setInputValue(argPatternInput, 'src/*');
  createButton.click();
  await waitForCondition(() => (document.querySelector('.permission-panel')?.textContent || '').includes('grep_search'), 'manual permission rule created');
  const manualState = await getPermissions();
  const manualRule = manualState.rules.find(rule => rule.tool === 'grep_search' && rule.action === 'ask' && rule.argsMatch.path === 'src/*');
  record(checks, 'new53-permission-manual-rule-created', Boolean(manualRule), JSON.stringify(manualState.rules));
  record(
    checks,
    'new57-permission-manual-scoped-rule-created',
    Boolean(manualRule?.argsMatch.path === 'src/*'),
    JSON.stringify(manualState.rules),
  );

  const manualRow = Array.from(document.querySelectorAll<HTMLElement>('.permission-row')).find(row => row.textContent?.includes('grep_search'));
  const manualDeleteButton = manualRow?.querySelector<HTMLButtonElement>('button');
  record(checks, 'new53-permission-manual-delete-visible', Boolean(manualDeleteButton), manualRow?.textContent || '');
  manualDeleteButton?.click();
  const manualDeleteDialog = await waitForAppDialog('manual permission rule delete dialog');
  record(
    checks,
    'new53-permission-manual-delete-themed-dialog',
    (manualDeleteDialog.textContent || '').includes('删除权限规则') && (manualDeleteDialog.textContent || '').includes('grep_search'),
    manualDeleteDialog.textContent || '',
  );
  clickAppDialogConfirm();
  await waitForCondition(() => !(document.querySelector('.permission-panel')?.textContent || '').includes('grep_search'), 'manual permission rule deleted');
  const afterManualDelete = await getPermissions();
  record(
    checks,
    'new53-permission-manual-rule-deleted',
    !afterManualDelete.rules.some(rule => rule.id === manualRule?.id),
    JSON.stringify(afterManualDelete.rules),
  );
  ui.setSettingsOpen(false);
}

async function report(payload: SmokeReport): Promise<void> {
  if (window.metis?.reportSmokeResult) {
    await window.metis.reportSmokeResult(payload);
    return;
  }
  console.info('METIS_SMOKE_RESULT:', payload);
}

export async function runRendererSmoke(): Promise<void> {
  const checks: SmokeCheck[] = [];

  try {
    await waitForBoot(checks);
    await prepareStores(checks);
    await verifyComposerUploadAndRegressionFixes(checks);
    await verifyCommandPalette(checks);
    await verifySessionSearch(checks);
    await verifyMotionHooks(checks);
    await verifyBusySessionGuard(checks);
    await verifyLongThreadWindowing(checks);
    await verifyRunRecoveryDiagnostics(checks);
    await verifyDevServerAutoPreview(checks);
    await verifyPreviewVisualAudit(checks);
    await verifyTrueSessionResume(checks);
    await verifyContextWindowQuota(checks);
    await verifyContextCompactionControl(checks);
    await verifyProviderUsageAndModels(checks);
    await verifyPermissionBulkManagement(checks);
    await verifyRealUiPreviewAutomation(checks);
    await verifyReleaseDiagnosticsBundle(checks);
    await verifyProviderRegistry(checks);
    await verifyDeveloperWorkflowPolish(checks);
    await verifyThreeZones(checks);
    await verifyStandardStream(checks);
    await verifyRightRailWorkbench(checks);
    await verifyIndependentSideChat(checks);
    await verifyFileChangeDiffWorkbench(checks);
    await verifyLocalHtmlAutoPreview(checks);
    await verifySubagentParallelUi(checks);
    await verifyCronAutomation(checks);
    await verifySkillManagement(checks);
    await verifySkillDelete(checks);
    await verifyLegacyStream(checks);
    await verifyStreamError(checks);
    await verifyToolErrorStream(checks);
    await verifyToolPermissionApprovals(checks);
    await report({ ok: true, checks });
  } catch (error) {
    checks.push({ name: 'renderer-smoke-exception', ok: false, detail: asErrorMessage(error) });
    await report({ ok: false, checks, error: asErrorMessage(error) });
  }
}
