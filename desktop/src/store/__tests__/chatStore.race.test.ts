import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage, ChatRunPayload, Session } from '../../lib/types';

vi.mock('../../lib/api', () => ({
  cancelChatRun: vi.fn(async () => runPayload({ status: 'canceled' })),
  chatStream: vi.fn(async () => undefined),
  compactConversation: vi.fn(async () => ({})),
  createSession: vi.fn(async () => sessionMeta('session-new')),
  deleteSession: vi.fn(async () => undefined),
  getActiveSessionRun: vi.fn(async () => ({ ok: false, run: null })),
  getCompactStatus: vi.fn(async () => ({ running: false })),
  getSession: vi.fn(async () => sessionPayload('session-new', [])),
  getSessions: vi.fn(async () => ({
    sessions: [sessionMeta('session-new')],
    activeSessionId: 'session-new',
    activeWorkspaceId: 'workspace-1',
  })),
  getWorkspaces: vi.fn(async () => ({
    workspaces: [],
    activeWorkspaceId: 'workspace-1',
  })),
  parseUpload: vi.fn(async () => ({})),
  removeWorkspace: vi.fn(async () => undefined),
  renameSessionTitle: vi.fn(async () => undefined),
  runEventStream: vi.fn(async () => undefined),
  startChatRun: vi.fn(async () => runPayload()),
  switchSession: vi.fn(async () => undefined),
  switchWorkspace: vi.fn(async () => undefined),
  clearWorkspaceSessions: vi.fn(async () => undefined),
  createWorkspace: vi.fn(async () => ({ id: 'workspace-1', name: 'Workspace', path: '', createdAt: 1, updatedAt: 1 })),
}));

const api = await import('../../lib/api');
const { useChatStore } = await import('../chatStore');
const { useSessionStore } = await import('../sessionStore');
const { clearActiveRunController, processedRunSeq } = await import('../runManager');

describe('chatStore loadSession runtime correctness', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    processedRunSeq.clear();
    clearActiveRunController('session-new');
    clearActiveRunController('session-1');
    useSessionStore.setState({
      sessions: [],
      workspaces: [],
      activeSessionId: null,
      activeWorkspaceId: '',
      loading: false,
      error: null,
    });
    useChatStore.setState({
      messages: [],
      composerText: '',
      attachments: [],
      streaming: false,
      error: null,
      runtimeStatus: null,
      memoryNotice: null,
      recoveryNotice: null,
      compactStatus: null,
      compacting: false,
      subagents: [],
      controller: null,
      runSessionId: null,
      pendingSendSessionId: null,
      usage: null,
      contextLedger: null,
    });
    vi.mocked(api.getActiveSessionRun).mockResolvedValue({ ok: false, run: null });
  });

  it('send creates a session, then passive loadSession does not wipe optimistic messages', async () => {
    const start = deferred<ChatRunPayload>();
    vi.mocked(api.startChatRun).mockReturnValueOnce(start.promise);
    vi.mocked(api.getSession).mockResolvedValue(sessionPayload('session-new', []));

    const sendPromise = useChatStore.getState().send('first hello');
    await waitUntil(() => useChatStore.getState().streaming && useChatStore.getState().runSessionId === 'session-new');

    await useChatStore.getState().loadSession('session-new');

    const messages = useChatStore.getState().messages;
    expect(messages.some(message => message.role === 'user' && message.content === 'first hello')).toBe(true);
    expect(messages.some(message => message.role === 'assistant' && message.pending)).toBe(true);
    expect(api.getSession).not.toHaveBeenCalled();

    start.resolve(runPayload());
    await sendPromise;
  });

  it('loadSession skips destructive overwrite when streaming in the same session', async () => {
    const localMessages = [chatMessage('local-user', 'user', 'local draft')];
    useChatStore.setState({
      messages: localMessages,
      streaming: true,
      runSessionId: 'session-1',
    });
    vi.mocked(api.getSession).mockResolvedValue(sessionPayload('session-1', [{ role: 'user', content: 'backend old' }]));

    await useChatStore.getState().loadSession('session-1');

    expect(useChatStore.getState().messages).toEqual(localMessages);
    expect(api.getSession).not.toHaveBeenCalled();
  });

  it('loadSession rechecks the active run guard after async fetches resolve', async () => {
    const session = deferred<Session>();
    const start = deferred<ChatRunPayload>();
    useSessionStore.setState({ activeSessionId: 'session-1' });
    vi.mocked(api.getSession).mockReturnValueOnce(session.promise);
    vi.mocked(api.startChatRun).mockReturnValueOnce(start.promise);

    const loadPromise = useChatStore.getState().loadSession('session-1');
    await waitUntil(() => vi.mocked(api.getSession).mock.calls.length > 0);
    const sendPromise = useChatStore.getState().send('optimistic survives');
    await waitUntil(() => useChatStore.getState().streaming && useChatStore.getState().runSessionId === 'session-1');

    session.resolve(sessionPayload('session-1', [{ role: 'user', content: 'backend old' }]));
    await loadPromise;

    expect(useChatStore.getState().messages.some(message => message.content === 'optimistic survives')).toBe(true);
    expect(useChatStore.getState().messages.some(message => message.content === 'backend old')).toBe(false);

    start.resolve(runPayload({ sessionId: 'session-1' }));
    await sendPromise;
  });

  it('pending session creation blocks passive loadSession before optimistic messages are set', async () => {
    const created = deferred<{ id: string; workspaceId: string }>();
    const start = deferred<ChatRunPayload>();
    vi.mocked(api.createSession).mockReturnValueOnce(created.promise);
    vi.mocked(api.startChatRun).mockReturnValueOnce(start.promise);

    const sendPromise = useChatStore.getState().send('first message');
    await waitUntil(() => useChatStore.getState().pendingSendSessionId === '__pending_send_session__');

    await useChatStore.getState().loadSession('session-new');

    expect(api.getSession).not.toHaveBeenCalled();
    created.resolve({ id: 'session-new', workspaceId: 'workspace-1' });
    await waitUntil(() => useChatStore.getState().streaming && useChatStore.getState().runSessionId === 'session-new');
    start.resolve(runPayload());
    await sendPromise;
  });

  it('loadSession force reloads even while streaming', async () => {
    useChatStore.setState({
      messages: [chatMessage('local-user', 'user', 'local draft')],
      streaming: true,
      runSessionId: 'session-1',
    });
    vi.mocked(api.getSession).mockResolvedValue(sessionPayload('session-1', [{ role: 'user', content: 'backend truth' }]));

    await useChatStore.getState().loadSession('session-1', { force: true });

    expect(api.getSession).toHaveBeenCalledWith('session-1');
    expect(useChatStore.getState().messages.some(message => message.content === 'backend truth')).toBe(true);
    expect(useChatStore.getState().messages.some(message => message.content === 'local draft')).toBe(false);
    expect(useChatStore.getState().streaming).toBe(false);
    expect(useChatStore.getState().runSessionId).toBeNull();
  });

  it('loadSession still performs a normal full reload when no run is active', async () => {
    useChatStore.setState({
      messages: [chatMessage('stale-user', 'user', 'stale local')],
      streaming: false,
      runSessionId: null,
    });
    vi.mocked(api.getSession).mockResolvedValue(sessionPayload('session-1', [
      { role: 'user', content: 'backend user' },
      { role: 'assistant', content: 'backend assistant' },
    ]));

    await useChatStore.getState().loadSession('session-1');

    expect(api.getSession).toHaveBeenCalledWith('session-1');
    expect(useChatStore.getState().messages.map(message => message.content)).toEqual(['backend user', 'backend assistant']);
  });
});

function sessionMeta(id: string) {
  return {
    id,
    title: 'Metis Chat',
    workspaceId: 'workspace-1',
    messageCount: 0,
    createdAt: 1,
    updatedAt: 1,
  };
}

function sessionPayload(id: string, history: Session['history']): Session {
  return {
    id,
    title: 'Metis Chat',
    workspaceId: 'workspace-1',
    mode: 'agent',
    history,
    compactState: null,
    createdAt: 1,
    updatedAt: 1,
  };
}

function chatMessage(id: string, role: ChatMessage['role'], content: string): ChatMessage {
  return { id, role, content, createdAt: 1 };
}

function runPayload(overrides: Partial<ChatRunPayload> = {}): ChatRunPayload {
  return {
    ok: true,
    runId: 'run-1',
    id: 'run-1',
    sessionId: 'session-new',
    assistantId: 'assistant-test',
    status: 'running',
    phase: 'running',
    cancelRequested: false,
    createdAt: 1,
    updatedAt: 1,
    startedAt: 1,
    finishedAt: 0,
    eventCount: 0,
    lastSeq: 0,
    error: '',
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function waitUntil(predicate: () => boolean): Promise<void> {
  for (let index = 0; index < 30; index += 1) {
    if (predicate()) return;
    await new Promise(resolve => window.setTimeout(resolve, 0));
  }
  throw new Error('Timed out waiting for condition');
}
