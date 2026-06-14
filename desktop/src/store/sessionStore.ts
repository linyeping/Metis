import { create } from 'zustand';
import {
  clearWorkspaceSessions,
  createSession,
  createWorkspace,
  deleteSession,
  getSessions,
  getWorkspaces,
  removeWorkspace,
  renameSessionTitle,
  switchSession,
  switchWorkspace,
} from '../lib/api';
import type { SessionMeta, Workspace } from '../lib/types';
import { useUiStore } from './uiStore';

interface SessionState {
  sessions: SessionMeta[];
  workspaces: Workspace[];
  activeSessionId: string | null;
  activeWorkspaceId: string;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  newSession: () => Promise<string | null>;
  selectSession: (sessionId: string) => Promise<void>;
  deleteSessionById: (sessionId: string) => Promise<void>;
  renameSessionById: (sessionId: string, title: string) => Promise<void>;
  openWorkspacePath: (path: string) => Promise<void>;
  selectWorkspace: (workspaceId: string) => Promise<void>;
  clearWorkspace: (workspaceId: string) => Promise<void>;
  removeWorkspaceById: (workspaceId: string) => Promise<void>;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  workspaces: [],
  activeSessionId: null,
  activeWorkspaceId: '',
  loading: false,
  error: null,
  load: async () => {
    set({ loading: true, error: null });
    try {
      const [sessionPayload, workspacePayload] = await Promise.all([getSessions(), getWorkspaces()]);
      set({
        sessions: sessionPayload.sessions,
        workspaces: workspacePayload.workspaces,
        activeSessionId: sessionPayload.activeSessionId,
        activeWorkspaceId: sessionPayload.activeWorkspaceId || workspacePayload.activeWorkspaceId,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  },
  newSession: async () => {
    const created = await createSession();
    await get().load();
    return created.id || null;
  },
  selectSession: async sessionId => {
    const previousSessionId = get().activeSessionId;
    await switchSession(sessionId);
    await get().load();
    if (previousSessionId && previousSessionId !== get().activeSessionId) {
      useUiStore.getState().clearToastsForSession(previousSessionId);
    }
  },
  deleteSessionById: async sessionId => {
    await deleteSession(sessionId);
    await get().load();
  },
  renameSessionById: async (sessionId, title) => {
    const nextTitle = title.trim().slice(0, 80);
    if (!nextTitle) return;
    await renameSessionTitle(sessionId, nextTitle);
    await get().load();
  },
  openWorkspacePath: async path => {
    const workspace = await createWorkspace(path);
    if (workspace.id) {
      await switchWorkspace(workspace.id);
    }
    await get().load();
  },
  selectWorkspace: async workspaceId => {
    await switchWorkspace(workspaceId);
    await get().load();
  },
  clearWorkspace: async workspaceId => {
    await clearWorkspaceSessions(workspaceId);
    await get().load();
  },
  removeWorkspaceById: async workspaceId => {
    await removeWorkspace(workspaceId);
    await get().load();
  },
}));
