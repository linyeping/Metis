import { Folder, FolderOpen, MessageSquarePlus, Plus } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useT } from '../../hooks/useT';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';

export function ProjectPanel() {
  const t = useT();
  const workspaces = useSessionStore(state => state.workspaces);
  const sessions = useSessionStore(state => state.sessions);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const openWorkspacePath = useSessionStore(state => state.openWorkspacePath);
  const selectWorkspace = useSessionStore(state => state.selectWorkspace);
  const startDraftSession = useSessionStore(state => state.startDraftSession);
  const clearChat = useChatStore(state => state.clearLocal);
  const setActiveSection = useUiStore(state => state.setActiveSection);
  const [busy, setBusy] = useState('');

  const projects = useMemo(() => [...workspaces].sort((left, right) => right.updatedAt - left.updatedAt), [workspaces]);
  const sessionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const session of sessions) {
      if (session.workspaceId) counts.set(session.workspaceId, (counts.get(session.workspaceId) || 0) + 1);
    }
    return counts;
  }, [sessions]);

  const createProject = async () => {
    const path = await window.metis.pickFolder();
    if (!path) return;
    setBusy('create');
    try {
      await openWorkspacePath(path);
    } finally {
      setBusy('');
    }
  };

  const startInProject = async (workspaceId: string) => {
    setBusy(workspaceId);
    try {
      if (workspaceId !== activeWorkspaceId) await selectWorkspace(workspaceId);
      startDraftSession(workspaceId);
      clearChat();
      setActiveSection('chat');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="zone-panel project-panel">
      <header className="zone-header">
        <div>
          <Folder size={20} />
          <span>
            <em>{t('长期工作空间')}</em>
            <strong>{t('项目')}</strong>
          </span>
        </div>
        <div className="zone-header-actions">
          <button type="button" disabled={Boolean(busy)} onClick={() => void createProject()}>
            <Plus size={14} /> {busy === 'create' ? t('打开中...') : t('打开项目文件夹')}
          </button>
        </div>
      </header>
      <p className="project-panel-intro">
        {t('项目保存长期 workspace、规则、记忆和共享文件；会话文件只属于当前对话，并保留在对话右上角。')}
      </p>
      <div className="project-scope-strip">
        <span>{t('工作区')}</span><span>{t('项目规则')}</span><span>{t('长期记忆')}</span><span>{t('共享文件')}</span>
      </div>
      <div className="zone-list project-list">
        {projects.length === 0 ? (
          <div className="zone-empty">
            <FolderOpen size={24} />
            <span>{t('还没有项目')}</span>
            <small>{t('选择一个本机文件夹，Metis 会把它作为长期工作空间。')}</small>
          </div>
        ) : projects.map(project => (
          <article className="zone-row project-row" data-active={project.id === activeWorkspaceId} key={project.id}>
            <div>
              <strong>{project.name}</strong>
              <span title={project.path}>{project.path}</span>
              <small>
                {sessionCounts.get(project.id) || 0} {t('个会话')} · {new Date(project.updatedAt * 1000).toLocaleString()}
              </small>
            </div>
            <div className="row-actions">
              <button type="button" title={t('在资源管理器中打开')} onClick={() => void window.metis.openPath(project.path)}>
                <FolderOpen size={14} /> {t('打开目录')}
              </button>
              <button type="button" disabled={Boolean(busy)} onClick={() => void startInProject(project.id)}>
                <MessageSquarePlus size={14} /> {busy === project.id ? t('切换中...') : t('新建项目对话')}
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
