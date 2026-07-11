import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../lib/api', () => ({
  clearWorkspaceSessions: vi.fn(async () => undefined),
  createSession: vi.fn(async (_mode: string = 'chat', workspaceId?: string | null) => ({
    id: 'session-created',
    workspaceId: workspaceId === undefined ? 'workspace-1' : workspaceId || '',
  })),
  createWorkspace: vi.fn(async () => ({ id: 'workspace-2', name: 'Other', path: '', createdAt: 2, updatedAt: 2 })),
  deleteSession: vi.fn(async () => undefined),
  getSessions: vi.fn(async () => ({ sessions: [], activeSessionId: null, activeWorkspaceId: 'workspace-1' })),
  getWorkspaces: vi.fn(async () => ({
    workspaces: [
      { id: 'workspace-1', name: 'Miro', path: '', createdAt: 1, updatedAt: 1 },
      { id: 'workspace-2', name: 'Other', path: '', createdAt: 2, updatedAt: 2 },
    ],
    activeWorkspaceId: 'workspace-1',
  })),
  removeWorkspace: vi.fn(async () => undefined),
  renameSessionTitle: vi.fn(async () => undefined),
  switchSession: vi.fn(async () => undefined),
  switchWorkspace: vi.fn(async () => undefined),
}));

const api = await import('../../lib/api');
const { useSessionStore } = await import('../sessionStore');
const { useUiStore } = await import('../uiStore');

describe('sessionStore mode drafts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useUiStore.setState({ appMode: 'cowork' });
    useSessionStore.setState({
      sessions: [],
      workspaces: [
        { id: 'workspace-1', name: 'Miro', path: '', createdAt: 1, updatedAt: 1 },
        { id: 'workspace-2', name: 'Other', path: '', createdAt: 2, updatedAt: 2 },
      ],
      activeSessionId: null,
      activeWorkspaceId: 'workspace-1',
      activeSessionByMode: {},
      activeWorkspaceByMode: { cowork: 'workspace-1' },
      loading: false,
      error: null,
    });
  });

  it('does not create a persisted session when switching into an empty mode', async () => {
    await useSessionStore.getState().activateModeSession('cowork');

    expect(api.createSession).not.toHaveBeenCalled();
    expect(useSessionStore.getState().activeSessionId).toBeNull();
    expect(useSessionStore.getState().activeWorkspaceId).toBe('workspace-1');
    expect(JSON.parse(localStorage.getItem('metis.activeSessionByMode') || '{}')).toEqual({
      cowork: '__metis_draft_session__',
    });
  });

  it('creates the global new conversation without inheriting the active workspace', async () => {
    useSessionStore.getState().startDraftSession();

    expect(useSessionStore.getState().activeWorkspaceId).toBe('');
    expect(useSessionStore.getState().activeWorkspaceByMode.cowork).toBeUndefined();

    await useSessionStore.getState().newSession();

    expect(api.createSession).toHaveBeenCalledWith('cowork', '');
    expect(useSessionStore.getState().activeWorkspaceId).toBe('');
  });

  it('keeps a global draft unscoped after visiting another mode workspace', () => {
    useSessionStore.getState().startDraftSession();
    useUiStore.setState({ appMode: 'code' });
    useSessionStore.getState().startDraftSession('workspace-2');

    const target = useSessionStore.getState().prepareModeSession('cowork');

    expect(target).toEqual({ sessionId: null, workspaceId: '', draft: true });
    expect(useSessionStore.getState().activeWorkspaceId).toBe('');
  });

  it('keeps workspace scope when the new conversation comes from a workspace row', async () => {
    useUiStore.setState({ appMode: 'code' });
    useSessionStore.getState().startDraftSession('workspace-2');

    await useSessionStore.getState().newSession();

    expect(api.switchWorkspace).toHaveBeenCalledWith('workspace-2');
    expect(api.createSession).toHaveBeenCalledWith('code', 'workspace-2');
    expect(useSessionStore.getState().activeWorkspaceId).toBe('workspace-2');
  });

  it('keeps an explicitly selected empty workspace instead of jumping back to an old session workspace', async () => {
    useUiStore.setState({ appMode: 'code' });
    useSessionStore.setState({
      sessions: [{ id: 'old-miro', title: 'Old Miro', workspaceId: 'workspace-1', mode: 'code', messageCount: 1, createdAt: 1, updatedAt: 1 }],
      activeSessionId: 'old-miro',
      activeWorkspaceId: 'workspace-1',
      activeSessionByMode: { code: 'old-miro' },
      activeWorkspaceByMode: { code: 'workspace-1' },
    });
    vi.mocked(api.getSessions).mockResolvedValueOnce({
      sessions: [{ id: 'old-miro', title: 'Old Miro', workspaceId: 'workspace-1', mode: 'code', messageCount: 1, createdAt: 1, updatedAt: 1 }],
      activeSessionId: 'old-miro',
      activeWorkspaceId: 'workspace-1',
    });

    await useSessionStore.getState().selectWorkspace('workspace-2');

    expect(api.switchWorkspace).toHaveBeenCalledWith('workspace-2');
    expect(useSessionStore.getState().activeSessionId).toBeNull();
    expect(useSessionStore.getState().activeWorkspaceId).toBe('workspace-2');
    expect(JSON.parse(localStorage.getItem('metis.activeSessionByMode') || '{}')).toEqual({
      code: '__metis_draft_session__',
    });
    expect(JSON.parse(localStorage.getItem('metis.activeWorkspaceByMode') || '{}')).toEqual({
      code: 'workspace-2',
    });
  });
});
