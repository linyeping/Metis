import { create } from 'zustand';
import { normalizeChatStreamEvent } from '../lib/agentEvents';
import { sideChatStream } from '../lib/api';

export interface SideChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
  pending?: boolean;
  error?: string;
}

export interface SideChatSession {
  id: string;
  title: string;
  model: string;
  messages: SideChatMessage[];
  createdAt: number;
  updatedAt: number;
}

interface SideChatState {
  sessions: SideChatSession[];
  activeSessionId: string;
  composerText: string;
  streaming: boolean;
  error: string | null;
  controller: AbortController | null;
  setComposerText: (value: string) => void;
  createSession: (model?: string) => string;
  selectSession: (sessionId: string) => void;
  renameSession: (sessionId: string, title: string) => void;
  deleteSession: (sessionId: string) => void;
  setSessionModel: (sessionId: string, model: string) => void;
  send: (overrideText?: string) => Promise<void>;
  stop: () => void;
  clearActive: () => void;
}

const STORAGE_KEY = 'metis.sideChat.sessions.v2';
const LEGACY_MESSAGES_KEY = 'metis.sideChat.messages.v1';
const ACTIVE_KEY = 'metis.sideChat.activeSessionId.v2';
const MAX_STORED_SESSIONS = 24;
const MAX_STORED_MESSAGES = 80;

const initial = readStoredState();

export const useSideChatStore = create<SideChatState>((set, get) => ({
  sessions: initial.sessions,
  activeSessionId: initial.activeSessionId,
  composerText: '',
  streaming: false,
  error: null,
  controller: null,
  setComposerText: composerText => set({ composerText }),
  createSession: (model = '') => {
    const session = createBlankSession(model);
    set(state => {
      const sessions = compactSessions([session, ...state.sessions]);
      persistState(sessions, session.id);
      return { activeSessionId: session.id, composerText: '', error: null, sessions };
    });
    return session.id;
  },
  selectSession: activeSessionId => {
    if (!get().sessions.some(session => session.id === activeSessionId)) return;
    localStorage.setItem(ACTIVE_KEY, activeSessionId);
    set({ activeSessionId, composerText: '', error: null });
  },
  renameSession: (sessionId, title) => {
    const nextTitle = title.trim().slice(0, 48);
    if (!nextTitle) return;
    set(state => {
      const sessions = state.sessions.map(session =>
        session.id === sessionId ? { ...session, title: nextTitle, updatedAt: Date.now() } : session,
      );
      persistState(sessions, state.activeSessionId);
      return { sessions };
    });
  },
  deleteSession: sessionId => {
    const state = get();
    const filtered = state.sessions.filter(session => session.id !== sessionId);
    const sessions = filtered.length ? filtered : [createBlankSession()];
    const activeSessionId = state.activeSessionId === sessionId ? sessions[0].id : state.activeSessionId;
    persistState(sessions, activeSessionId);
    set({ activeSessionId, composerText: '', controller: null, error: null, sessions });
  },
  setSessionModel: (sessionId, model) => {
    const nextModel = model.trim();
    set(state => {
      const sessions = state.sessions.map(session =>
        session.id === sessionId ? { ...session, model: nextModel, updatedAt: Date.now() } : session,
      );
      persistState(sessions, state.activeSessionId);
      return { sessions };
    });
  },
  send: async overrideText => {
    const text = (overrideText ?? get().composerText).trim();
    if (!text || get().streaming) return;

    const session = ensureActiveSession(get, set);
    const now = Date.now();
    const controller = new AbortController();
    const userMessage: SideChatMessage = {
      id: `side-user-${now}`,
      role: 'user',
      content: text,
      createdAt: now,
    };
    const assistantMessage: SideChatMessage = {
      id: `side-assistant-${now}`,
      role: 'assistant',
      content: '',
      createdAt: now + 1,
      pending: true,
    };
    const requestMessages = [...session.messages, userMessage]
      .filter(message => message.content.trim())
      .slice(-MAX_STORED_MESSAGES)
      .map(message => ({ role: message.role, content: message.content }));

    set(state => {
      const sessions = updateSessionMessages(state.sessions, session.id, messages =>
        compactMessages([...messages, userMessage, assistantMessage]),
      ).map(item =>
        item.id === session.id && item.title === '新聊天'
          ? { ...item, title: autoTitle(text), updatedAt: now }
          : item.id === session.id
            ? { ...item, updatedAt: now }
            : item,
      );
      persistState(sessions, session.id);
      return { activeSessionId: session.id, composerText: '', controller, error: null, sessions, streaming: true };
    });

    try {
      await sideChatStream(
        { messages: requestMessages, model: session.model },
        event => {
          const normalized = normalizeChatStreamEvent(event);
          if (normalized.kind === 'text_delta' || normalized.kind === 'content_delta' || normalized.kind === 'content') {
            appendAssistantText(session.id, assistantMessage.id, normalized.text);
          } else if (normalized.kind === 'error') {
            const message = normalized.error.message || normalized.error.title || 'Side chat failed.';
            markAssistantError(session.id, assistantMessage.id, message);
          }
        },
        controller.signal,
      );
    } catch (error) {
      if (!controller.signal.aborted) {
        markAssistantError(session.id, assistantMessage.id, error instanceof Error ? error.message : String(error));
      }
    } finally {
      set(state => {
        const sessions = updateSessionMessages(state.sessions, session.id, messages =>
          messages.map(message => (message.id === assistantMessage.id ? { ...message, pending: false } : message)),
        );
        persistState(sessions, state.activeSessionId || session.id);
        return { controller: null, sessions, streaming: false };
      });
    }
  },
  stop: () => {
    const state = get();
    state.controller?.abort();
    const sessions = updateSessionMessages(state.sessions, state.activeSessionId, messages =>
      messages.map(message =>
        message.pending ? { ...message, pending: false, error: message.error || '已停止。' } : message,
      ),
    );
    persistState(sessions, state.activeSessionId);
    set({ controller: null, sessions, streaming: false });
  },
  clearActive: () => {
    const state = get();
    state.controller?.abort();
    const sessions = updateSessionMessages(state.sessions, state.activeSessionId, () => []);
    persistState(sessions, state.activeSessionId);
    set({ composerText: '', controller: null, error: null, sessions, streaming: false });
  },
}));

function ensureActiveSession(get: () => SideChatState, set: (partial: Partial<SideChatState>) => void): SideChatSession {
  const state = get();
  const existing = state.sessions.find(session => session.id === state.activeSessionId);
  if (existing) return existing;
  const session = createBlankSession();
  const sessions = compactSessions([session, ...state.sessions]);
  persistState(sessions, session.id);
  set({ activeSessionId: session.id, sessions });
  return session;
}

function appendAssistantText(sessionId: string, messageId: string, text: string): void {
  if (!text) return;
  useSideChatStore.setState(state => {
    const sessions = updateSessionMessages(state.sessions, sessionId, messages =>
      messages.map(message => (message.id === messageId ? { ...message, content: message.content + text } : message)),
    );
    persistState(sessions, state.activeSessionId);
    return { sessions };
  });
}

function markAssistantError(sessionId: string, messageId: string, error: string): void {
  useSideChatStore.setState(state => {
    const sessions = updateSessionMessages(state.sessions, sessionId, messages =>
      messages.map(message =>
        message.id === messageId
          ? {
              ...message,
              content: message.content || error,
              error,
              pending: false,
            }
          : message,
      ),
    );
    persistState(sessions, state.activeSessionId);
    return { error, sessions };
  });
}

function updateSessionMessages(
  sessions: SideChatSession[],
  sessionId: string,
  updater: (messages: SideChatMessage[]) => SideChatMessage[],
): SideChatSession[] {
  const now = Date.now();
  return sessions.map(session =>
    session.id === sessionId
      ? {
          ...session,
          messages: compactMessages(updater(session.messages)),
          updatedAt: now,
        }
      : session,
  );
}

function compactMessages(messages: SideChatMessage[]): SideChatMessage[] {
  return messages.slice(-MAX_STORED_MESSAGES);
}

function compactSessions(sessions: SideChatSession[]): SideChatSession[] {
  return sessions
    .slice()
    .sort((left, right) => right.updatedAt - left.updatedAt)
    .slice(0, MAX_STORED_SESSIONS);
}

function createBlankSession(model = ''): SideChatSession {
  const now = Date.now();
  return {
    id: `side-session-${now}-${Math.random().toString(36).slice(2, 8)}`,
    title: '新聊天',
    model,
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

function autoTitle(text: string): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  return normalized.length > 22 ? `${normalized.slice(0, 21)}...` : normalized || '新聊天';
}

function readStoredState(): { sessions: SideChatSession[]; activeSessionId: string } {
  const sessions = readStoredSessions();
  const activeSessionId = localStorage.getItem(ACTIVE_KEY) || sessions[0]?.id || '';
  if (sessions.some(session => session.id === activeSessionId)) {
    return { sessions, activeSessionId };
  }
  const fallback = sessions[0] || createBlankSession();
  return { sessions: sessions.length ? sessions : [fallback], activeSessionId: fallback.id };
}

function readStoredSessions(): SideChatSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (Array.isArray(parsed) && parsed.length) {
      const sessions = parsed.map(normalizeStoredSession).filter(Boolean) as SideChatSession[];
      if (sessions.length) return compactSessions(sessions);
    }
  } catch {}
  return readLegacySession();
}

function readLegacySession(): SideChatSession[] {
  try {
    const raw = localStorage.getItem(LEGACY_MESSAGES_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed) || !parsed.length) return [createBlankSession()];
    const session = createBlankSession();
    session.title = '旧 Chat';
    session.messages = compactMessages(parsed.map(normalizeStoredMessage).filter(Boolean) as SideChatMessage[]);
    return [session];
  } catch {
    return [createBlankSession()];
  }
}

function normalizeStoredSession(value: unknown): SideChatSession | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Partial<SideChatSession>;
  const messages = Array.isArray(item.messages) ? item.messages.map(normalizeStoredMessage).filter(Boolean) as SideChatMessage[] : [];
  return {
    id: typeof item.id === 'string' && item.id ? item.id : `side-session-${Date.now()}`,
    title: typeof item.title === 'string' && item.title.trim() ? item.title.trim().slice(0, 48) : '新聊天',
    model: typeof item.model === 'string' ? item.model : '',
    messages: compactMessages(messages),
    createdAt: typeof item.createdAt === 'number' ? item.createdAt : Date.now(),
    updatedAt: typeof item.updatedAt === 'number' ? item.updatedAt : Date.now(),
  };
}

function normalizeStoredMessage(value: unknown): SideChatMessage | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Partial<SideChatMessage>;
  if (item.role !== 'user' && item.role !== 'assistant') return null;
  if (typeof item.content !== 'string') return null;
  return {
    id: typeof item.id === 'string' && item.id ? item.id : `side-message-${Date.now()}`,
    role: item.role,
    content: item.content,
    createdAt: typeof item.createdAt === 'number' ? item.createdAt : Date.now(),
    error: typeof item.error === 'string' ? item.error : '',
  };
}

function persistState(sessions: SideChatSession[], activeSessionId: string): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify(
      compactSessions(sessions).map(session => ({
        id: session.id,
        title: session.title,
        model: session.model,
        messages: compactMessages(session.messages).map(message => ({
          id: message.id,
          role: message.role,
          content: message.content,
          createdAt: message.createdAt,
          error: message.error || '',
        })),
        createdAt: session.createdAt,
        updatedAt: session.updatedAt,
      })),
    ),
  );
  localStorage.setItem(ACTIVE_KEY, activeSessionId);
}
