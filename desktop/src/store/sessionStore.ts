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
import type { AppMode, SessionMeta, Workspace } from '../lib/types';
import { useUiStore } from './uiStore';

interface SessionState {
  sessions: SessionMeta[];
  workspaces: Workspace[];
  activeSessionId: string | null;
  activeWorkspaceId: string;
  activeSessionByMode: Partial<Record<AppMode, string>>;
  activeWorkspaceByMode: Partial<Record<AppMode, string>>;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  newSession: () => Promise<string | null>;
  startDraftSession: () => void;
  rememberModeState: (mode: AppMode) => void;
  prepareModeSession: (mode: AppMode) => { sessionId: string | null; workspaceId: string; draft: boolean };
  prepareSessionSelection: (mode: AppMode, sessionId: string) => { sessionId: string | null; workspaceId: string; draft: boolean };
  activateModeSession: (mode: AppMode) => Promise<void>;
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
  activeSessionByMode: readModeRecord('metis.activeSessionByMode'),
  activeWorkspaceByMode: readModeRecord('metis.activeWorkspaceByMode'),
  loading: false,
  error: null,
  load: async () => {
    set({ loading: true, error: null });
    try {
      const [sessionPayload, workspacePayload] = await Promise.all([getSessions(), getWorkspaces()]);
      const appMode = useUiStore.getState().appMode;
      const next = reconcileModeState({
        sessions: sessionPayload.sessions,
        workspaces: workspacePayload.workspaces,
        activeSessionId: sessionPayload.activeSessionId,
        activeWorkspaceId: sessionPayload.activeWorkspaceId || workspacePayload.activeWorkspaceId,
        activeSessionByMode: get().activeSessionByMode,
        activeWorkspaceByMode: get().activeWorkspaceByMode,
      });
      const activeSessionId = next.activeSessionByMode[appMode] || null;
      set({
        sessions: sessionPayload.sessions,
        workspaces: workspacePayload.workspaces,
        activeSessionId: isDraftSessionId(activeSessionId) ? null : activeSessionId,
        activeWorkspaceId: next.activeWorkspaceByMode[appMode] || '',
        activeSessionByMode: next.activeSessionByMode,
        activeWorkspaceByMode: next.activeWorkspaceByMode,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : String(error) });
    }
  },
  newSession: async () => {
    const appMode = useUiStore.getState().appMode;
    const modeWorkspaceId = get().activeWorkspaceByMode[appMode] || get().activeWorkspaceId;
    if (modeWorkspaceId) {
      await switchWorkspace(modeWorkspaceId).catch(() => null);
    }
    const created = await createSession(appMode);
    await get().load();
    rememberExplicitModeState(appMode, created.id || null, created.workspaceId || modeWorkspaceId || '');
    return created.id || null;
  },
  startDraftSession: () => {
    const appMode = useUiStore.getState().appMode;
    set(state => {
      const activeSessionByMode = { ...state.activeSessionByMode, [appMode]: DRAFT_SESSION_ID };
      const activeWorkspaceByMode = { ...state.activeWorkspaceByMode };
      const workspaceId = state.activeWorkspaceId || state.activeWorkspaceByMode[appMode] || '';
      if (workspaceId) activeWorkspaceByMode[appMode] = workspaceId;
      writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
      writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
      return {
        activeSessionByMode,
        activeWorkspaceByMode,
        activeSessionId: null,
        activeWorkspaceId: workspaceId || state.activeWorkspaceId,
      };
    });
  },
  rememberModeState: mode => {
    rememberExplicitModeState(mode, get().activeSessionId, get().activeWorkspaceId);
  },
  prepareModeSession: mode => {
    const state = get();
    const modeWorkspaceId = state.activeWorkspaceByMode[mode] || state.activeWorkspaceId || '';
    const rememberedSessionId = state.activeSessionByMode[mode] || '';

    if (isDraftSessionId(rememberedSessionId)) {
      set({ activeSessionId: null, activeWorkspaceId: modeWorkspaceId || state.activeWorkspaceId });
      return { sessionId: null, workspaceId: modeWorkspaceId || state.activeWorkspaceId, draft: true };
    }

    const rememberedSession = state.sessions.find(session => session.id === rememberedSessionId && session.mode === mode) || null;
    const preferred = rememberedSession || findPreferredModeSession(state.sessions, mode, modeWorkspaceId);
    if (preferred) {
      const workspaceId = preferred.workspaceId || modeWorkspaceId || state.activeWorkspaceId;
      const activeSessionByMode = { ...state.activeSessionByMode, [mode]: preferred.id };
      const activeWorkspaceByMode = workspaceId ? { ...state.activeWorkspaceByMode, [mode]: workspaceId } : { ...state.activeWorkspaceByMode };
      writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
      writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
      set({
        activeSessionByMode,
        activeWorkspaceByMode,
        activeSessionId: preferred.id,
        activeWorkspaceId: workspaceId,
      });
      return { sessionId: preferred.id, workspaceId, draft: false };
    }

    const workspaceId = modeWorkspaceId || state.activeWorkspaceId || '';
    const activeSessionByMode = { ...state.activeSessionByMode, [mode]: DRAFT_SESSION_ID };
    const activeWorkspaceByMode = workspaceId ? { ...state.activeWorkspaceByMode, [mode]: workspaceId } : { ...state.activeWorkspaceByMode };
    writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
    writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
    set({
      activeSessionByMode,
      activeWorkspaceByMode,
      activeSessionId: null,
      activeWorkspaceId: workspaceId,
    });
    return { sessionId: null, workspaceId, draft: true };
  },
  prepareSessionSelection: (mode, sessionId) => {
    const state = get();
    const targetSession = state.sessions.find(session => session.id === sessionId && session.mode === mode) || null;
    if (!targetSession) return get().prepareModeSession(mode);

    const workspaceId = targetSession.workspaceId || state.activeWorkspaceByMode[mode] || state.activeWorkspaceId || '';
    const activeSessionByMode = { ...state.activeSessionByMode, [mode]: targetSession.id };
    const activeWorkspaceByMode = workspaceId ? { ...state.activeWorkspaceByMode, [mode]: workspaceId } : { ...state.activeWorkspaceByMode };
    writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
    writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
    set({
      activeSessionByMode,
      activeWorkspaceByMode,
      activeSessionId: targetSession.id,
      activeWorkspaceId: workspaceId,
    });
    return { sessionId: targetSession.id, workspaceId, draft: false };
  },
  activateModeSession: async mode => {
    const state = get();
    const active = state.sessions.find(session => session.id === state.activeSessionId);
    if (active?.mode === mode) return;

    const modeWorkspaceId = state.activeWorkspaceByMode[mode] || '';
    if (modeWorkspaceId && modeWorkspaceId !== state.activeWorkspaceId) {
      await switchWorkspace(modeWorkspaceId).catch(() => null);
    }

    const rememberedSessionId = state.activeSessionByMode[mode] || '';
    if (isDraftSessionId(rememberedSessionId)) {
      set({ activeSessionId: null, activeWorkspaceId: modeWorkspaceId || state.activeWorkspaceId });
      return;
    }
    const rememberedSession = state.sessions.find(session => session.id === rememberedSessionId && session.mode === mode) || null;
    const preferred = rememberedSession || findPreferredModeSession(state.sessions, mode, modeWorkspaceId);
    if (preferred) {
      await get().selectSession(preferred.id);
      return;
    }

    rememberDraftModeState(mode, modeWorkspaceId || state.activeWorkspaceId);
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
    const appMode = useUiStore.getState().appMode;
    const workspace = await createWorkspace(path);
    if (workspace.id) {
      await switchWorkspace(workspace.id);
      rememberDraftModeState(appMode, workspace.id);
    }
    await get().load();
  },
  selectWorkspace: async workspaceId => {
    const appMode = useUiStore.getState().appMode;
    await switchWorkspace(workspaceId);
    rememberDraftModeState(appMode, workspaceId);
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

const APP_MODES: AppMode[] = ['chat', 'cowork', 'code'];
const DRAFT_SESSION_ID = '__metis_draft_session__';

function findPreferredModeSession(sessions: SessionMeta[], mode: AppMode, activeWorkspaceId: string): SessionMeta | null {
  const matches = sessions.filter(session => session.mode === mode);
  if (matches.length === 0) return null;

  const sameWorkspace = matches.filter(session => (session.workspaceId || '') === activeWorkspaceId);
  const pool = sameWorkspace.length > 0 ? sameWorkspace : matches;
  return [...pool].sort((left, right) => right.updatedAt - left.updatedAt)[0] || null;
}

function rememberExplicitModeState(mode: AppMode, sessionId: string | null, workspaceId: string): void {
  useSessionStore.setState(state => {
    const activeSessionByMode = { ...state.activeSessionByMode };
    const activeWorkspaceByMode = { ...state.activeWorkspaceByMode };
    if (sessionId) activeSessionByMode[mode] = sessionId;
    if (workspaceId) activeWorkspaceByMode[mode] = workspaceId;
    writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
    writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
    return {
      activeSessionByMode,
      activeWorkspaceByMode,
      activeSessionId: mode === useUiStore.getState().appMode && sessionId ? sessionId : state.activeSessionId,
      activeWorkspaceId: mode === useUiStore.getState().appMode && workspaceId ? workspaceId : state.activeWorkspaceId,
    };
  });
}

function rememberDraftModeState(mode: AppMode, workspaceId: string): void {
  useSessionStore.setState(state => {
    const activeSessionByMode = { ...state.activeSessionByMode, [mode]: DRAFT_SESSION_ID };
    const activeWorkspaceByMode = { ...state.activeWorkspaceByMode };
    const resolvedWorkspaceId = workspaceId || state.activeWorkspaceId || state.activeWorkspaceByMode[mode] || '';
    if (resolvedWorkspaceId) activeWorkspaceByMode[mode] = resolvedWorkspaceId;
    writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
    writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
    return {
      activeSessionByMode,
      activeWorkspaceByMode,
      activeSessionId: mode === useUiStore.getState().appMode ? null : state.activeSessionId,
      activeWorkspaceId: mode === useUiStore.getState().appMode && resolvedWorkspaceId ? resolvedWorkspaceId : state.activeWorkspaceId,
    };
  });
}

function reconcileModeState(input: {
  sessions: SessionMeta[];
  workspaces: Workspace[];
  activeSessionId: string | null;
  activeWorkspaceId: string;
  activeSessionByMode: Partial<Record<AppMode, string>>;
  activeWorkspaceByMode: Partial<Record<AppMode, string>>;
}): { activeSessionByMode: Partial<Record<AppMode, string>>; activeWorkspaceByMode: Partial<Record<AppMode, string>> } {
  const workspaceIds = new Set(input.workspaces.map(workspace => workspace.id));
  const activeSessionByMode = { ...input.activeSessionByMode };
  const activeWorkspaceByMode = { ...input.activeWorkspaceByMode };
  const draftModes = new Set(APP_MODES.filter(mode => isDraftSessionId(input.activeSessionByMode[mode] || '')));
  const backendActive = input.sessions.find(session => session.id === input.activeSessionId);
  const backendMode = toAppMode(backendActive?.mode || '');
  if (backendActive && backendMode && !draftModes.has(backendMode)) {
    activeSessionByMode[backendMode] = backendActive.id;
    if (backendActive.workspaceId) activeWorkspaceByMode[backendMode] = backendActive.workspaceId;
  }

  for (const mode of APP_MODES) {
    if (draftModes.has(mode)) {
      activeSessionByMode[mode] = DRAFT_SESSION_ID;
      if (activeWorkspaceByMode[mode] && !workspaceIds.has(activeWorkspaceByMode[mode] || '')) {
        delete activeWorkspaceByMode[mode];
      }
      continue;
    }
    const rememberedSession = input.sessions.find(session => session.id === activeSessionByMode[mode] && session.mode === mode);
    const preferred = rememberedSession || findPreferredModeSession(input.sessions, mode, activeWorkspaceByMode[mode] || '');
    if (preferred) {
      activeSessionByMode[mode] = preferred.id;
      if (preferred.workspaceId) activeWorkspaceByMode[mode] = preferred.workspaceId;
    } else {
      delete activeSessionByMode[mode];
    }

    if (activeWorkspaceByMode[mode] && !workspaceIds.has(activeWorkspaceByMode[mode] || '')) {
      delete activeWorkspaceByMode[mode];
    }
  }

  if (input.activeWorkspaceId && workspaceIds.has(input.activeWorkspaceId) && backendMode && !draftModes.has(backendMode)) {
    activeWorkspaceByMode[backendMode] = input.activeWorkspaceId;
  }

  writeModeRecord('metis.activeSessionByMode', activeSessionByMode);
  writeModeRecord('metis.activeWorkspaceByMode', activeWorkspaceByMode);
  return { activeSessionByMode, activeWorkspaceByMode };
}

function toAppMode(value: string): AppMode | null {
  return APP_MODES.includes(value as AppMode) ? (value as AppMode) : null;
}

function isDraftSessionId(value: string | null): boolean {
  return value === DRAFT_SESSION_ID;
}

function readModeRecord(key: string): Partial<Record<AppMode, string>> {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Partial<Record<AppMode, string>> = {};
    for (const mode of APP_MODES) {
      const value = (parsed as Record<string, unknown>)[mode];
      if (typeof value === 'string' && value) out[mode] = value;
    }
    return out;
  } catch {
    return {};
  }
}

function writeModeRecord(key: string, value: Partial<Record<AppMode, string>>): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage failures; the backend session list remains the fallback.
  }
}
