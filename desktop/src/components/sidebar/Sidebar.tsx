import { Archive, ChevronDown, ChevronRight, Folder, FolderOpen, Mail, MoreHorizontal, Paintbrush, Pencil, Plus, Settings, Trash2 } from 'lucide-react';
import { createElement, useEffect, useMemo, useRef, useState, type CSSProperties, type Dispatch, type KeyboardEvent, type SetStateAction } from 'react';
import { createPortal } from 'react-dom';
import { listActiveRuns } from '../../lib/api';
import { navigateToSession } from '../../lib/modeNavigation';
import type { ChatRunStatus, SessionMeta, Workspace } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { ModeSwitcher } from './ModeSwitcher';
import { SidebarNav } from './SidebarNav';
import { SessionSearch } from './SessionSearch';
import { useT } from '../../hooks/useT';

export function Sidebar() {
  const t = useT();
  const setSettingsOpen = useUiStore(state => state.setSettingsOpen);
  const setProductSurface = useUiStore(state => state.setProductSurface);
  const appMode = useUiStore(state => state.appMode);
  const sessions = useSessionStore(state => state.sessions);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const startDraftSession = useSessionStore(state => state.startDraftSession);
  const selectSession = useSessionStore(state => state.selectSession);
  const selectWorkspace = useSessionStore(state => state.selectWorkspace);
  const deleteSessionById = useSessionStore(state => state.deleteSessionById);
  const renameSessionById = useSessionStore(state => state.renameSessionById);
  const archiveSessionById = useSessionStore(state => state.archiveSessionById);
  const markSessionUnreadById = useSessionStore(state => state.markSessionUnreadById);
  const openWorkspacePath = useSessionStore(state => state.openWorkspacePath);
  const clearWorkspace = useSessionStore(state => state.clearWorkspace);
  const removeWorkspaceById = useSessionStore(state => state.removeWorkspaceById);
  const loadChatSession = useChatStore(state => state.loadSession);
  const clearChat = useChatStore(state => state.clearLocal);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<string | null>(null);
  const [runStatuses, setRunStatuses] = useState<Record<string, ChatRunStatus>>({});
  const [expandedLists, setExpandedLists] = useState<Record<string, boolean>>({});
  const workspacePaths = useMemo(() => new Map(workspaces.map(workspace => [workspace.id, workspace.path])), [workspaces]);

  const modeSessions = useMemo(
    () => sessions.filter(session => session.mode === appMode || (appMode === 'chat' && !session.mode)),
    [appMode, sessions],
  );
  const unscopedModeSessions = useMemo(
    () => modeSessions.filter(session => !session.workspaceId),
    [modeSessions],
  );
  const grouped = useMemo(() => {
    const workspaceIds = new Set(modeSessions.map(session => session.workspaceId).filter(Boolean));
    if (activeWorkspaceId) workspaceIds.add(activeWorkspaceId);

    const relevantWorkspaces = workspaces.filter(w => workspaceIds.has(w.id));
    return relevantWorkspaces.map(workspace => ({
      workspace,
      sessions: modeSessions.filter(session => session.workspaceId === workspace.id),
    }));
  }, [activeWorkspaceId, modeSessions, workspaces]);

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
    startDraftSession(workspaceId || null);
    clearChat();
  };

  useEffect(() => {
    let disposed = false;
    let refreshInFlight = false;
    const sessionIds = sessions.map(session => session.id).filter(Boolean);
    if (sessionIds.length === 0) {
      setRunStatuses(current => (Object.keys(current).length === 0 ? current : {}));
      return undefined;
    }

    const refresh = async () => {
      if (refreshInFlight) return;
      refreshInFlight = true;
      try {
        const sessionIdSet = new Set(sessionIds);
        const payload = await listActiveRuns().catch(() => ({ runs: [] }));
        if (disposed) return;
        const next: Record<string, ChatRunStatus> = {};
        for (const run of payload.runs) {
          if (!sessionIdSet.has(run.sessionId) || next[run.sessionId] || !isActiveRunStatus(run.status)) continue;
          next[run.sessionId] = run.status;
        }
        setRunStatuses(current => sameRunStatuses(current, next) ? current : next);
      } finally {
        refreshInFlight = false;
      }
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
      <ModeSwitcher />
      <div className="sidebar-mode-surface" data-mode={appMode}>
        <SidebarNav />
        <div className="sidebar-search-row" data-has-folder={appMode !== 'chat'}>
          <SessionSearch />
          {appMode !== 'chat' && (
            <button className="sidebar-folder-button" type="button" title={t('打开文件夹')} aria-label={t('打开文件夹')} onClick={openFolder}>
              <FolderOpen size={17} />
            </button>
          )}
        </div>

        <div className="workspace-list">
          {appMode === 'chat' ? (
            <div className="session-list session-list-chat">
              {modeSessions.length === 0 && <p className="empty-line">{t('暂无会话')}</p>}
              {modeSessions.slice(0, expandedLists.chat ? undefined : 4).map(session =>
                createElement(SessionRow, {
                  key: session.id,
                  active: session.id === activeSessionId,
                  archiveSessionById,
                  deleteSessionById,
                  markSessionUnreadById,
                  renameSessionById,
                  runStatus: runStatuses[session.id] || '',
                  session,
                  workspacePath: workspacePaths.get(session.workspaceId) || '',
                })
              )}
              {modeSessions.length > 4 && (
                <button className="session-expand-button" type="button" onClick={() => setExpandedLists(value => ({ ...value, chat: !value.chat }))}>
                  {expandedLists.chat ? t('收起显示') : `${t('展开显示')} (${modeSessions.length - 4})`} <ChevronDown size={14} data-open={Boolean(expandedLists.chat)} />
                </button>
              )}
            </div>
          ) : (
            <>
              {unscopedModeSessions.length > 0 && (
                <div className="session-list session-list-unscoped">
                  {unscopedModeSessions.slice(0, expandedLists.unscoped ? undefined : 4).map(session =>
                    createElement(SessionRow, {
                      key: session.id,
                      active: session.id === activeSessionId,
                      archiveSessionById,
                      deleteSessionById,
                      markSessionUnreadById,
                      renameSessionById,
                      runStatus: runStatuses[session.id] || '',
                      session,
                    }),
                  )}
                  {unscopedModeSessions.length > 4 && (
                    <button className="session-expand-button" type="button" onClick={() => setExpandedLists(value => ({ ...value, unscoped: !value.unscoped }))}>
                      {expandedLists.unscoped ? t('收起显示') : `${t('展开显示')} (${unscopedModeSessions.length - 4})`} <ChevronDown size={14} data-open={Boolean(expandedLists.unscoped)} />
                    </button>
                  )}
                </div>
              )}
              {grouped.map(group =>
                createElement(WorkspaceGroup, {
                  key: group.workspace.id,
                  activeSessionId,
                  activeWorkspaceId,
                  archiveSessionById,
                  clearWorkspace,
                  createChat,
                  deleteSessionById,
                  group,
                  loadChatSession,
                  menu,
                  markSessionUnreadById,
                  open,
                  renameSessionById,
                  removeWorkspaceById,
                  runStatuses,
                  selectWorkspace,
                  setMenu,
                  setOpen,
                }),
              )}
            </>
          )}
        </div>
      </div>
      <div className="sidebar-product-actions">
        <button className="sidebar-design-button" type="button" onClick={() => setProductSurface('design')}>
          <Paintbrush size={15} />
          <span>Design</span>
        </button>
        <button className="sidebar-settings-button" type="button" onClick={() => setSettingsOpen(true)}>
          <Settings size={15} />
          <span>{t('设置')}</span>
        </button>
      </div>
    </div>
  );
}

function sameRunStatuses(left: Record<string, ChatRunStatus>, right: Record<string, ChatRunStatus>): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length && leftKeys.every(key => left[key] === right[key]);
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
  deleteSessionById: (sessionId: string) => Promise<void>;
  renameSessionById: (sessionId: string, title: string) => Promise<void>;
  archiveSessionById: (sessionId: string) => Promise<void>;
  markSessionUnreadById: (sessionId: string, unread: boolean) => Promise<void>;
  clearWorkspace: (workspaceId: string) => Promise<void>;
  removeWorkspaceById: (workspaceId: string) => Promise<void>;
  loadChatSession: (sessionId: string | null, options?: { force?: boolean }) => Promise<void>;
  runStatuses: Record<string, ChatRunStatus>;
}

function WorkspaceGroup({
  activeSessionId,
  activeWorkspaceId,
  archiveSessionById,
  clearWorkspace,
  createChat,
  deleteSessionById,
  group,
  loadChatSession,
  menu,
  markSessionUnreadById,
  open,
  renameSessionById,
  removeWorkspaceById,
  runStatuses,
  selectWorkspace,
  setMenu,
  setOpen,
}: WorkspaceGroupProps) {
  const t = useT();
  const workspace = group.workspace;
  const id = workspace.id || 'default';
  const isOpen = open[id] ?? true;
  const isActive = workspace.id === activeWorkspaceId;
  const [expanded, setExpanded] = useState(false);
  const visibleSessions = expanded ? group.sessions : group.sessions.slice(0, 4);

  return (
    <section className="workspace-group" style={workspaceColor(workspace.name || id)}>
      <div className="workspace-row" data-active={isActive}>
        <button
          className="workspace-main"
          type="button"
          onClick={async () => {
            setOpen(state => ({ ...state, [id]: !isOpen }));
            // 只在工作区尚未激活时才切换，避免每次展开/折叠都触发 selectWorkspace
            if (workspace.id && !isActive) {
              await selectWorkspace(workspace.id);
              await loadChatSession(useSessionStore.getState().activeSessionId);
            }
          }}
        >
          <ChevronRight className="workspace-chevron" data-open={isOpen} size={14} />
          <Folder className="workspace-folder-icon" size={13} />
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
          {visibleSessions.map(session =>
            createElement(SessionRow, {
              key: session.id,
              active: session.id === activeSessionId,
              archiveSessionById,
              deleteSessionById,
              markSessionUnreadById,
              renameSessionById,
              runStatus: runStatuses[session.id] || '',
              session,
              workspacePath: workspace.path,
            }),
          )}
          {group.sessions.length > 4 && (
            <button className="session-expand-button" type="button" onClick={() => setExpanded(value => !value)}>
              {expanded ? t('收起显示') : `${t('展开显示')} (${group.sessions.length - 4})`} <ChevronDown size={14} data-open={expanded} />
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function SessionRow({
  active,
  archiveSessionById,
  deleteSessionById,
  markSessionUnreadById,
  renameSessionById,
  runStatus,
  session,
  workspacePath = '',
}: {
  active: boolean;
  session: SessionMeta;
  runStatus: ChatRunStatus;
  workspacePath?: string;
  archiveSessionById: (sessionId: string) => Promise<void>;
  deleteSessionById: (sessionId: string) => Promise<void>;
  markSessionUnreadById: (sessionId: string, unread: boolean) => Promise<void>;
  renameSessionById: (sessionId: string, title: string) => Promise<void>;
}) {
  const t = useT();
  const appMode = useUiStore(state => state.appMode);
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const loadChatSession = useChatStore(state => state.loadSession);
  const [renaming, setRenaming] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const [renameDraft, setRenameDraft] = useState(session.title || 'Metis Chat');
  const menuRef = useRef<HTMLDivElement>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!renaming) setRenameDraft(session.title || 'Metis Chat');
  }, [renaming, session.title]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node) && !contextMenuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('pointerdown', close);
    const closeOnViewportChange = () => setMenuOpen(false);
    window.addEventListener('resize', closeOnViewportChange);
    window.addEventListener('scroll', closeOnViewportChange, true);
    return () => {
      document.removeEventListener('pointerdown', close);
      window.removeEventListener('resize', closeOnViewportChange);
      window.removeEventListener('scroll', closeOnViewportChange, true);
    };
  }, [menuOpen]);

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
    <div className="session-item" data-active={active} data-running={Boolean(runStatus)} data-unread={Boolean(session.unread)} ref={menuRef}>
      {renaming ? (
        <div className="session-main session-main-edit">
          <span
            className="session-state-dot"
            data-status={runStatus || (session.unread ? 'unread' : 'idle')}
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
          title={`${session.title || 'Metis Chat'} · ${session.messageCount} ${t('条消息')}`}
          onClick={() => {
            const targetMode = (session.mode as import('../../lib/types').AppMode) || appMode;
            navigateToSession(session.id, targetMode);
          }}
        >
          <span
            className="session-state-dot"
            data-status={runStatus || (session.unread ? 'unread' : 'idle')}
            role={runStatus ? 'status' : undefined}
            aria-label={runStatus ? `${t('会话')} ${runStatus}` : undefined}
            title={runStatus || 'idle'}
          />
          <ScrollingSessionTitle title={session.title || 'Metis Chat'} />
        </button>
      )}
      <button
        className="session-more-button"
        type="button"
        title={t('会话操作')}
        aria-label={t('会话操作')}
        aria-expanded={menuOpen}
        onClick={event => {
          const nextOpen = !menuOpen;
          if (nextOpen) {
            const bounds = event.currentTarget.getBoundingClientRect();
            const menuHeight = workspacePath ? 176 : 142;
            const top = bounds.bottom + menuHeight <= window.innerHeight - 8
              ? bounds.bottom + 4
              : Math.max(8, bounds.top - menuHeight - 4);
            setMenuPosition({ top, left: Math.max(8, Math.min(window.innerWidth - 198, bounds.right - 190)) });
          }
          setMenuOpen(nextOpen);
        }}
      >
        <MoreHorizontal size={15} />
      </button>
      {menuOpen && createPortal(
        <div ref={contextMenuRef} className="session-context-menu session-context-menu-portal" role="menu" style={menuPosition}>
          {workspacePath && (
            <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); void window.metis.openPath(workspacePath); }}>
              <FolderOpen size={14} /> {t('在资源管理器中打开')}
            </button>
          )}
          <button className="rename-session" type="button" role="menuitem" onClick={() => { setMenuOpen(false); setRenameDraft(session.title || 'Metis Chat'); setRenaming(true); }}>
            <Pencil size={14} /> {t('重命名会话')}
          </button>
          <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); void markSessionUnreadById(session.id, !session.unread); }}>
            <Mail size={14} /> {session.unread ? t('标记为已读') : t('标记为未读')}
          </button>
          <button type="button" role="menuitem" onClick={async () => {
            setMenuOpen(false);
            await archiveSessionById(session.id);
            await loadChatSession(useSessionStore.getState().activeSessionId);
          }}>
            <Archive size={14} /> {t('归档')}
          </button>
          <button className="delete-session" type="button" role="menuitem" onClick={async () => {
            setMenuOpen(false);
            const confirmed = await requestConfirm({
              title: t('删除会话'),
              message: t('此会话及其历史将被永久删除。'),
              confirmLabel: t('删除'),
              tone: 'danger',
              icon: 'trash',
            });
            if (!confirmed) return;
            await deleteSessionById(session.id);
            await loadChatSession(useSessionStore.getState().activeSessionId);
          }}>
            <Trash2 size={14} /> {t('删除会话')}
          </button>
        </div>,
        document.body,
      )}
    </div>
  );
}

function ScrollingSessionTitle({ title }: { title: string }) {
  const trackRef = useRef<HTMLSpanElement>(null);
  const animationRef = useRef<Animation | null>(null);

  const start = () => {
    const track = trackRef.current;
    const viewport = track?.parentElement;
    if (!track || !viewport) return;
    const distance = Math.max(0, track.scrollWidth - viewport.clientWidth);
    if (distance < 2) return;
    animationRef.current?.cancel();
    animationRef.current = track.animate(
      [{ transform: 'translateX(0)' }, { transform: `translateX(-${distance}px)` }],
      { duration: Math.max(1800, distance * 36), delay: 450, direction: 'alternate', easing: 'ease-in-out', iterations: Infinity },
    );
  };

  const stop = () => {
    animationRef.current?.cancel();
    animationRef.current = null;
  };

  useEffect(() => stop, []);
  return <span className="session-title" onPointerEnter={start} onPointerLeave={stop}><span ref={trackRef}>{title}</span></span>;
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
