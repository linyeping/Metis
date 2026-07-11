import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage, ChatRunPayload, Session } from '../../lib/types';

vi.mock('../../lib/api', () => ({
  cancelChatRun: vi.fn(async () => runPayload({ status: 'canceled' })),
  chatStream: vi.fn(async () => undefined),
  compactConversation: vi.fn(async () => ({})),
  createRun: vi.fn(async () => runPayload()),
  createRunFollowup: vi.fn(async (_runId: string, body: { id: string; message: string; behavior: 'queue' | 'steer' }) => ({ ...body, status: 'pending', createdAt: 1, updatedAt: 1 })),
  createSession: vi.fn(async () => sessionMeta('session-new')),
  deleteRunFollowup: vi.fn(async () => undefined),
  deleteSession: vi.fn(async () => undefined),
  getActiveSessionRun: vi.fn(async () => ({ ok: false, run: null })),
  getAwaySummary: vi.fn(async () => ({ ok: false, summary: null })),
  getCompactStatus: vi.fn(async () => ({ running: false })),
  getComposerDeepResearchEnabled: vi.fn(async () => false),
  getPromptSuggestions: vi.fn(async () => ({ ok: false, suggestions: [] })),
  getRun: vi.fn(async () => runPayload({ status: 'done' })),
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
  updateRunFollowup: vi.fn(async (_runId: string, id: string, behavior: 'queue' | 'steer') => ({ id, message: '', behavior, status: 'pending', createdAt: 1, updatedAt: 1 })),
  clearWorkspaceSessions: vi.fn(async () => undefined),
  createWorkspace: vi.fn(async () => ({ id: 'workspace-1', name: 'Workspace', path: '', createdAt: 1, updatedAt: 1 })),
}));

const api = await import('../../lib/api');
const { useChatStore } = await import('../chatStore');
const { useSessionStore } = await import('../sessionStore');
const { useUiStore } = await import('../uiStore');
const { clearActiveRunController, processedRunSeq, setActiveRunController } = await import('../runManager');

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
    useUiStore.setState({ appMode: 'chat', codeExecutionProfile: 'local_worktree' });
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
      followupBehavior: 'queue',
      followupsBySession: {},
      pausedFollowupSessions: {},
    });
    vi.mocked(api.getActiveSessionRun).mockResolvedValue({ ok: false, run: null });
  });

  it('send creates a session, then passive loadSession does not wipe optimistic messages', async () => {
    const start = deferred<ChatRunPayload>();
    vi.mocked(api.createRun).mockReturnValueOnce(start.promise);
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

  it('send forwards the deep research toggle to the run API', async () => {
    vi.mocked(api.getComposerDeepResearchEnabled).mockResolvedValueOnce(true);

    await useChatStore.getState().send('research this');

    expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({
      message: 'research this',
      session_id: 'session-new',
      surface_mode: 'chat',
      deep_research: true,
    }));
  });

  it('adds a running follow-up with the selected queue behavior', async () => {
    useSessionStore.setState({ activeSessionId: 'session-1' });
    const controller = new AbortController();
    setActiveRunController('session-1', { assistantId: 'assistant-1', controller, runId: 'run-1' });
    useChatStore.setState({
      composerText: 'do this after the current turn',
      streaming: true,
      runSessionId: 'session-1',
      controller,
    });

    await useChatStore.getState().submitFollowup('queue');

    expect(api.createRunFollowup).toHaveBeenCalledWith('run-1', expect.objectContaining({
      message: 'do this after the current turn',
      behavior: 'queue',
    }));
    expect(useChatStore.getState().composerText).toBe('');
    expect(useChatStore.getState().followupsBySession['session-1']).toHaveLength(1);
    expect(useChatStore.getState().followupsBySession['session-1'][0].behavior).toBe('queue');
  });

  it('stopping pauses queued work and converts unapplied steering to queue', async () => {
    useSessionStore.setState({ activeSessionId: 'session-1' });
    const controller = new AbortController();
    setActiveRunController('session-1', { assistantId: 'assistant-1', controller, runId: 'run-1' });
    useChatStore.setState({
      streaming: true,
      runSessionId: 'session-1',
      controller,
      followupsBySession: {
        'session-1': [{
          id: 'followup-steer',
          message: 'change direction',
          behavior: 'steer',
          status: 'pending',
          createdAt: 1,
          updatedAt: 1,
          runId: 'run-1',
        }],
      },
    });

    useChatStore.getState().stop();

    const followup = useChatStore.getState().followupsBySession['session-1'][0];
    expect(followup.behavior).toBe('queue');
    expect(followup.status).toBe('paused');
    expect(useChatStore.getState().pausedFollowupSessions['session-1']).toBe(true);
  });

  it('send uses local_vm execution profile for Code mode when enabled', async () => {
    useUiStore.setState({ appMode: 'code', codeExecutionProfile: 'local_vm' });

    await useChatStore.getState().send('test in vm');

    expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({
      message: 'test in vm',
      session_id: 'session-new',
      surface_mode: 'code',
      execution_profile: 'local_vm',
    }));
  });

  it('does not fall back to /chat when creating a run fails', async () => {
    vi.mocked(api.createRun).mockRejectedValueOnce(new Error('HTTP 404'));

    await useChatStore.getState().send('no fallback');

    expect(api.chatStream).not.toHaveBeenCalled();
    expect(useChatStore.getState().error).toContain('HTTP 404');
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
    vi.mocked(api.createRun).mockReturnValueOnce(start.promise);

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
    vi.mocked(api.createRun).mockReturnValueOnce(start.promise);

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

  it('ignores an older session load after the active session changes', async () => {
    const first = deferred<Session>();
    const second = deferred<Session>();
    useSessionStore.setState({ activeSessionId: 'session-1' });
    vi.mocked(api.getSession).mockImplementation(sessionId => sessionId === 'session-1' ? first.promise : second.promise);

    const firstLoad = useChatStore.getState().loadSession('session-1');
    await waitUntil(() => vi.mocked(api.getSession).mock.calls.length === 1);
    useSessionStore.setState({ activeSessionId: 'session-new' });
    const secondLoad = useChatStore.getState().loadSession('session-new');
    second.resolve(sessionPayload('session-new', [{ role: 'user', content: 'new active transcript' }]));
    await secondLoad;
    first.resolve(sessionPayload('session-1', [{ role: 'user', content: 'stale transcript' }]));
    await firstLoad;

    expect(useChatStore.getState().loadedSessionId).toBe('session-new');
    expect(useChatStore.getState().messages.some(message => message.content === 'new active transcript')).toBe(true);
    expect(useChatStore.getState().messages.some(message => message.content === 'stale transcript')).toBe(false);
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
    turnId: 'turn-run-1',
    sessionId: 'session-new',
    assistantId: 'assistant-test',
    mode: 'chat',
    surfaceMode: 'chat',
    executionProfile: 'local_direct',
    workspaceRoot: '',
    sourceWorkspaceRoot: '',
    worktreeId: '',
    worktreePath: '',
    worktreeWorkspaceRoot: '',
    worktree: null,
    schemaVersion: 1,
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
