import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const uiState = {
    appMode: 'chat' as 'chat' | 'cowork' | 'code',
    activeSection: 'chat',
    setAppMode(mode: 'chat' | 'cowork' | 'code') {
      uiState.appMode = mode;
    },
    setActiveSection(section: string) {
      uiState.activeSection = section;
    },
  };
  const sessionState = {
    activeSessionId: 'chat-1' as string | null,
    rememberModeState: vi.fn(),
    prepareModeSession: vi.fn((mode: 'chat' | 'cowork' | 'code') => {
      const sessionId = mode === 'chat' ? 'chat-1' : mode === 'cowork' ? 'cowork-1' : 'code-1';
      sessionState.activeSessionId = sessionId;
      return { sessionId, workspaceId: '', draft: false };
    }),
    prepareSessionSelection: vi.fn((mode: 'chat' | 'cowork' | 'code', sessionId: string) => {
      sessionState.activeSessionId = sessionId;
      return { sessionId, workspaceId: '', draft: false, mode };
    }),
  };
  const chatState = {
    messages: [{ id: 'chat-message' }],
    composerText: 'chat draft',
    attachments: [],
    streaming: false,
    error: null,
    runtimeStatus: null,
    memoryNotice: null,
    todoNotice: null,
    awaySummary: null,
    promptSuggestions: [],
    compactStatus: null,
    compacting: false,
    subagents: [],
    coworkPlan: null,
    usage: null,
    contextLedger: null,
    loadedSessionId: 'chat-1' as string | null,
    runSessionId: null,
    pendingSendSessionId: null,
    controller: null,
    clearLocal: vi.fn(() => {
      chatState.messages = [];
      chatState.composerText = '';
      chatState.loadedSessionId = null;
    }),
    loadSession: vi.fn(async () => undefined),
  };
  const switchSession = vi.fn(async () => undefined);
  return { chatState, sessionState, switchSession, uiState };
});

vi.mock('../../store/uiStore', () => ({
  useUiStore: { getState: () => mocks.uiState },
}));

vi.mock('../../store/sessionStore', () => ({
  useSessionStore: { getState: () => mocks.sessionState },
}));

vi.mock('../../store/chatStore', () => ({
  useChatStore: {
    getState: () => mocks.chatState,
    setState: (next: Record<string, unknown>) => Object.assign(mocks.chatState, next),
  },
}));

vi.mock('../api', () => ({ switchSession: mocks.switchSession }));

const { navigateAppMode, navigateToSession } = await import('../modeNavigation');

describe('mode navigation performance path', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(performance.now());
      return 1;
    });
    mocks.uiState.appMode = 'chat';
    mocks.uiState.activeSection = 'chat';
    mocks.sessionState.activeSessionId = 'chat-1';
    mocks.switchSession.mockResolvedValue(undefined);
    Object.assign(mocks.chatState, {
      messages: [{ id: 'chat-message' }],
      composerText: 'chat draft',
      loadedSessionId: 'chat-1',
    });
  });

  it('switches immediately and revalidates a restored snapshot with one message load', async () => {
    navigateAppMode('cowork');
    expect(mocks.uiState.appMode).toBe('cowork');
    expect(mocks.chatState.clearLocal).toHaveBeenCalledTimes(1);
    await vi.runAllTimersAsync();
    expect(mocks.switchSession).toHaveBeenCalledWith('cowork-1');
    expect(mocks.chatState.loadSession).not.toHaveBeenCalled();

    Object.assign(mocks.chatState, {
      messages: [{ id: 'cowork-message' }],
      composerText: 'cowork draft',
      loadedSessionId: 'cowork-1',
    });
    navigateAppMode('chat');

    expect(mocks.uiState.appMode).toBe('chat');
    expect(mocks.chatState.loadedSessionId).toBe('chat-1');
    expect(mocks.chatState.composerText).toBe('chat draft');
    await vi.runAllTimersAsync();

    expect(mocks.switchSession).toHaveBeenLastCalledWith('chat-1');
    expect(mocks.chatState.loadSession).toHaveBeenCalledTimes(1);
    expect(mocks.chatState.loadSession).toHaveBeenCalledWith('chat-1', { force: true });
  });

  it('does not reload the already active session', async () => {
    navigateToSession('chat-1', 'chat');
    await vi.runAllTimersAsync();

    expect(mocks.switchSession).not.toHaveBeenCalled();
    expect(mocks.chatState.loadSession).not.toHaveBeenCalled();
  });

  it('serializes backend switches so the latest mode is applied last', async () => {
    const coworkSwitch = deferred<undefined>();
    mocks.switchSession.mockImplementationOnce(() => coworkSwitch.promise).mockResolvedValueOnce(undefined);

    navigateAppMode('cowork');
    await vi.runAllTimersAsync();
    navigateAppMode('code');
    await vi.runAllTimersAsync();

    expect(mocks.switchSession).toHaveBeenCalledTimes(1);
    expect(mocks.switchSession).toHaveBeenNthCalledWith(1, 'cowork-1');

    coworkSwitch.resolve(undefined);
    await vi.runAllTimersAsync();
    await Promise.resolve();

    expect(mocks.switchSession).toHaveBeenCalledTimes(2);
    expect(mocks.switchSession).toHaveBeenNthCalledWith(2, 'code-1');
  });
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, reject, resolve };
}
