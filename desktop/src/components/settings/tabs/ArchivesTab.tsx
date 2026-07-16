import { useCallback, useEffect, useMemo, useState } from 'react';
import { Archive, RotateCcw, Search, Trash2 } from 'lucide-react';
import { archiveSession, deleteSession, getSessions } from '../../../lib/api';
import type { Language, SessionMeta } from '../../../lib/types';
import { useSessionStore } from '../../../store/sessionStore';
import { useUiStore } from '../../../store/uiStore';

export function ArchivesTab({ language }: { language: Language }) {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('all');
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const workspaces = useSessionStore(state => state.workspaces);
  const reloadActive = useSessionStore(state => state.load);
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const text = useCallback((zh: string, en: string) => (language === 'zh' ? zh : en), [language]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setMessage('');
    try {
      const payload = await getSessions(true);
      setSessions(payload.sessions);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return sessions.filter(session => {
      if (mode !== 'all' && (session.mode || 'chat') !== mode) return false;
      if (!normalized) return true;
      const workspace = workspaces.find(item => item.id === session.workspaceId)?.name || '';
      return `${session.title} ${workspace}`.toLowerCase().includes(normalized);
    });
  }, [mode, query, sessions, workspaces]);

  const restore = async (sessionId: string) => {
    await archiveSession(sessionId, false);
    await Promise.all([refresh(), reloadActive()]);
  };

  const remove = async (session: SessionMeta) => {
    const confirmed = await requestConfirm({
      title: text('永久删除已归档会话', 'Delete archived chat permanently'),
      message: text(`“${session.title}”将被永久删除，无法恢复。`, `“${session.title}” will be permanently deleted and cannot be restored.`),
      confirmLabel: text('永久删除', 'Delete permanently'),
      tone: 'danger',
      icon: 'trash',
    });
    if (!confirmed) return;
    await deleteSession(session.id);
    await refresh();
  };

  return (
    <div className="settings-card-grid archives-settings-grid">
      <section className="settings-section archives-section">
        <div className="settings-section-header settings-section-header-with-action">
          <div>
            <h3>{text('已归档会话', 'Archived chats')}</h3>
            <p className="section-desc">{text('归档会话不会出现在工作区侧边栏，但内容仍保留，可随时恢复。', 'Archived chats are hidden from workspace sidebars but remain available to restore.')}</p>
          </div>
          <span className="archives-count"><Archive size={14} /> {sessions.length}</span>
        </div>

        <div className="archives-toolbar">
          <label>
            <Search size={15} />
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder={text('搜索已归档会话', 'Search archived chats')} />
          </label>
          <select value={mode} onChange={event => setMode(event.target.value)} aria-label={text('会话类型', 'Chat type')}>
            <option value="all">{text('所有类型', 'All types')}</option>
            <option value="chat">Chat</option>
            <option value="cowork">Cowork</option>
            <option value="code">Code</option>
          </select>
        </div>

        <div className="archives-list">
          {loading && <p className="archives-empty">{text('正在读取...', 'Loading...')}</p>}
          {!loading && filtered.length === 0 && <p className="archives-empty">{text('没有匹配的已归档会话。', 'No archived chats match this view.')}</p>}
          {filtered.map(session => {
            const workspace = workspaces.find(item => item.id === session.workspaceId)?.name || text('无工作区', 'No workspace');
            return (
              <article className="archive-row" key={session.id}>
                <div>
                  <strong>{session.title || 'Metis Chat'}</strong>
                  <span>{workspace} · {(session.mode || 'chat').toUpperCase()} · {new Date((session.archivedAt || session.updatedAt) * 1000).toLocaleString()}</span>
                </div>
                <button type="button" title={text('恢复会话', 'Restore chat')} onClick={() => void restore(session.id)}><RotateCcw size={15} /></button>
                <button type="button" className="danger" title={text('永久删除', 'Delete permanently')} onClick={() => void remove(session)}><Trash2 size={15} /></button>
              </article>
            );
          })}
        </div>
        {message && <p className="notification-settings-message">{message}</p>}
      </section>
    </div>
  );
}
