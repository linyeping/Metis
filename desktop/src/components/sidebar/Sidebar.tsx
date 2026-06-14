import { ChevronRight, FolderOpen, MoreHorizontal, Pencil, Plus, Trash2 } from 'lucide-react';
import { createElement, useEffect, useMemo, useState, type CSSProperties, type Dispatch, type KeyboardEvent, type SetStateAction } from 'react';
import { getActiveSessionRun } from '../../lib/api';
import type { ChatRunStatus, SessionMeta, Workspace } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { ContextWindowBar } from './ContextWindowBar';
import { SessionSearch } from './SessionSearch';
import { useT } from '../../hooks/useT';

interface SidebarProps {
  model?: string;
}

export function Sidebar({ model = '' }: SidebarProps) {
  const t = useT();
  const sessions = useSessionStore(state => state.sessions);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const newSession = useSessionStore(state => state.newSession);
  const selectSession = useSessionStore(state => state.selectSession);
  const selectWorkspace = useSessionStore(state => state.selectWorkspace);
  const deleteSessionById = useSessionStore(state => state.deleteSessionById);
  const renameSessionById = useSessionStore(state => state.renameSessionById);
  const openWorkspacePath = useSessionStore(state => state.openWorkspacePath);
  const clearWorkspace = useSessionStore(state => state.clearWorkspace);
  const removeWorkspaceById = useSessionStore(state => state.removeWorkspaceById);
  const loadChatSession = useChatStore(state => state.loadSession);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<string | null>(null);
  const [runStatuses, setRunStatuses] = useState<Record<string, ChatRunStatus>>({});

  const grouped = useMemo(() => {
    const fallback = workspaces.length ? workspaces : [{ id: activeWorkspaceId || '', name: '当前工作区', path: '', createdAt: 0, updatedAt: 0 }];
    return fallback.map(workspace => ({
      workspace,
      sessions: sessions.filter(session => (session.workspaceId || '') === (workspace.id || '')),
    }));
  }, [activeWorkspaceId, sessions, workspaces]);

  const openFolder = async () => {
    const path = await window.metis.pickFolder();
    if (path) {
      await openWorkspacePath(path);
      await loadChatSession(useSessionStore.getState().activeSessionId);
    }
  };

  const createChat = async (workspaceId?: string) => {
    if (workspaceId && workspaceId !== activeWorkspaceId) {
      await selectWorkspace(workspaceId);
    }
    const sessionId = await newSession();
    await loadChatSession(sessionId);
  };

  useEffect(() => {
    let disposed = false;
    const sessionIds = sessions.map(session => session.id).filter(Boolean);
    if (sessionIds.length === 0) {
      setRunStatuses({});
      return undefined;
    }

    const refresh = async () => {
      const entries = await Promise.all(
        sessionIds.map(async sessionId => {
          const payload = await getActiveSessionRun(sessionId).catch(() => ({ ok: false, run: null }));
          const status = payload.run?.status || '';
          return [sessionId, isActiveRunStatus(status) ? status : ''] as const;
        }),
      );
      if (disposed) return;
      setRunStatuses(
        Object.fromEntries(entries.filter(([, status]) => Boolean(status))),
      );
    };

    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [sessions]);

  return (
    <div className="sidebar">
      <div className="sidebar-search-row">
        <SessionSearch />
        <button className="sidebar-folder-button" type="button" title={t('打开文件夹')} aria-label={t('打开文件夹')} onClick={openFolder}>
          <FolderOpen size={17} />
        </button>
      </div>

      <div className="workspace-list">
        {grouped.map(group =>
          createElement(WorkspaceGroup, {
            key: group.workspace.id || 'default',
            activeSessionId,
            activeWorkspaceId,
            clearWorkspace,
            createChat,
            deleteSessionById,
            group,
            loadChatSession,
            menu,
            open,
            renameSessionById,
            removeWorkspaceById,
            runStatuses,
            selectSession,
            selectWorkspace,
            setMenu,
            setOpen,
          }),
        )}
      </div>
      <ContextWindowBar model={model} />
    </div>
  );
}

interface WorkspaceGroupProps {
  group: { workspace: Workspace; sessions: SessionMeta[] };
  activeSessionId: string | null;
  activeWorkspaceId: string;
  open: Record<string, boolean>;
  menu: string | null;
  setOpen: Dispatch<SetStateAction<Record<string, boolean>>>;
  setMenu: Dispatch<SetStateAction<string | null>>;
  createChat: (workspaceId?: string) => Promise<void>;
  selectWorkspace: (workspaceId: string) => Promise<void>;
  selectSession: (sessionId: string) => Promise<void>;
  deleteSessionById: (sessionId: string) => Promise<void>;
  renameSessionById: (sessionId: string, title: string) => Promise<void>;
  clearWorkspace: (workspaceId: string) => Promise<void>;
  removeWorkspaceById: (workspaceId: string) => Promise<void>;
  loadChatSession: (sessionId: string | null, options?: { force?: boolean }) => Promise<void>;
  runStatuses: Record<string, ChatRunStatus>;
}

function WorkspaceGroup({
  activeSessionId,
  activeWorkspaceId,
  clearWorkspace,
  createChat,
  deleteSessionById,
  group,
  loadChatSession,
  menu,
  open,
  renameSessionById,
  removeWorkspaceById,
  runStatuses,
  selectSession,
  selectWorkspace,
  setMenu,
  setOpen,
}: WorkspaceGroupProps) {
  const t = useT();
  const workspace = group.workspace;
  const id = workspace.id || 'default';
  const isOpen = open[id] ?? true;
  const isActive = workspace.id === activeWorkspaceId;

  return (
    <section className="workspace-group" style={workspaceColor(workspace.name || id)}>
      <div className="workspace-row" data-active={isActive}>
        <button
          className="workspace-main"
          type="button"
          onClick={async () => {
            setOpen(state => ({ ...state, [id]: !isOpen }));
            if (workspace.id) {
              await selectWorkspace(workspace.id);
              await loadChatSession(useSessionStore.getState().activeSessionId);
            }
          }}
        >
          <ChevronRight className="workspace-chevron" data-open={isOpen} size={15} />
          <span>{t(workspace.name || '当前工作区')}</span>
          <em>{group.sessions.length}</em>
        </button>
        <button className="mini-action" type="button" title={t('新建会话')} onClick={() => void createChat(workspace.id)}>
          <Plus size={14} />
        </button>
        <button className="mini-action" type="button" title={t('菜单')} onClick={() => setMenu(menu === id ? null : id)}>
          <MoreHorizontal size={15} />
        </button>
        {menu === id && (
          <div className="workspace-menu">
            <button
              type="button"
              onClick={() => {
                setMenu(null);
                if (workspace.id) void clearWorkspace(workspace.id);
              }}
            >
              {t('清空会话')}
            </button>
            <button
              type="button"
              onClick={() => {
                setMenu(null);
                if (workspace.id) void removeWorkspaceById(workspace.id);
              }}
            >
              {t('移除工作区')}
            </button>
          </div>
        )}
      </div>
      <div className="session-list-shell" data-open={isOpen}>
        <div className="session-list">
          {group.sessions.length === 0 && <p className="empty-line">{t('暂无会话')}</p>}
          {group.sessions.map(session =>
            createElement(SessionRow, {
              key: session.id,
              active: session.id === activeSessionId,
              deleteSessionById,
              loadChatSession,
              renameSessionById,
              runStatus: runStatuses[session.id] || '',
              selectSession,
              session,
            }),
          )}
        </div>
      </div>
    </section>
  );
}

function SessionRow({
  active,
  deleteSessionById,
  loadChatSession,
  renameSessionById,
  runStatus,
  selectSession,
  session,
}: {
  active: boolean;
  session: SessionMeta;
  runStatus: ChatRunStatus;
  selectSession: (sessionId: string) => Promise<void>;
  deleteSessionById: (sessionId: string) => Promise<void>;
  renameSessionById: (sessionId: string, title: string) => Promise<void>;
  loadChatSession: (sessionId: string | null, options?: { force?: boolean }) => Promise<void>;
}) {
  const t = useT();
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState(session.title || 'Metis Chat');

  useEffect(() => {
    if (!renaming) setRenameDraft(session.title || 'Metis Chat');
  }, [renaming, session.title]);

  const commitRename = async () => {
    const nextTitle = renameDraft.trim().slice(0, 80);
    if (!nextTitle || nextTitle === (session.title || 'Metis Chat')) {
      setRenaming(false);
      setRenameDraft(session.title || 'Metis Chat');
      return;
    }
    await renameSessionById(session.id, nextTitle);
    setRenaming(false);
  };

  const handleRenameKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      void commitRename();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setRenameDraft(session.title || 'Metis Chat');
      setRenaming(false);
    }
  };

  return (
    <div className="session-item" data-active={active} data-running={Boolean(runStatus)}>
      {renaming ? (
        <div className="session-main session-main-edit">
          <span
            className="session-state-dot"
            data-status={runStatus || 'idle'}
            role={runStatus ? 'status' : undefined}
            aria-label={runStatus ? `${t('会话')} ${runStatus}` : undefined}
            title={runStatus || 'idle'}
          />
          <input
            autoFocus
            className="session-rename-input"
            value={renameDraft}
            onBlur={() => void commitRename()}
            onChange={event => setRenameDraft(event.target.value)}
            onKeyDown={handleRenameKey}
          />
        </div>
      ) : (
        <button
          className="session-main"
          type="button"
          title={`${session.messageCount} ${t('条消息')}`}
          onClick={async () => {
            await selectSession(session.id);
            await loadChatSession(session.id);
          }}
        >
          <span
            className="session-state-dot"
            data-status={runStatus || 'idle'}
            role={runStatus ? 'status' : undefined}
            aria-label={runStatus ? `${t('会话')} ${runStatus}` : undefined}
            title={runStatus || 'idle'}
          />
          <span className="session-title">{session.title || 'Metis Chat'}</span>
        </button>
      )}
      <button
        className="rename-session"
        type="button"
        title={t('重命名会话')}
        onClick={() => {
          setRenameDraft(session.title || 'Metis Chat');
          setRenaming(true);
        }}
      >
        <Pencil size={13} />
      </button>
      <button
        className="delete-session"
        type="button"
        title={t('删除会话')}
        onClick={async () => {
          await deleteSessionById(session.id);
          const nextActive = useSessionStore.getState().activeSessionId;
          await loadChatSession(nextActive);
        }}
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

function isActiveRunStatus(status: string): status is ChatRunStatus {
  return status === 'queued' || status === 'running' || status === 'canceling';
}

const WORKSPACE_COLORS = [
  '#5A8A70', // sage green
  '#8A6B5A', // warm brown
  '#5A6B8A', // steel blue
  '#8A5A7A', // dusty rose
  '#6B8A5A', // olive
  '#7A5A8A', // muted purple
  '#8A7A5A', // gold brown
  '#5A8A8A', // teal
];

function workspaceColor(name: string): CSSProperties {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
  return { '--ws-color': WORKSPACE_COLORS[Math.abs(hash) % WORKSPACE_COLORS.length] } as CSSProperties;
}
