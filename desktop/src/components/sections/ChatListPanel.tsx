import { Clock, MessageSquare, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { useChatStore } from '../../store/chatStore';
import { useT } from '../../hooks/useT';

export function ChatListPanel() {
  const t = useT();
  const sessions = useSessionStore(state => state.sessions);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const deleteSessionById = useSessionStore(state => state.deleteSessionById);
  const loadChatSession = useChatStore(state => state.loadSession);
  const setActiveSection = useUiStore(state => state.setActiveSection);

  const [selectedId, setSelectedId] = useState<string>(activeSessionId || '');

  const chatSessions = useMemo(() => sessions.filter(s => s.mode === 'chat' || !s.mode), [sessions]);

  const grouped = useMemo(() => {
    const groups: Record<string, typeof chatSessions> = {};
    chatSessions.forEach(session => {
      const d = new Date(session.updatedAt * 1000);
      const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
      if (!groups[month]) groups[month] = [];
      groups[month].push(session);
    });
    return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]));
  }, [chatSessions]);

  const selectedSession = useMemo(() => chatSessions.find(s => s.id === selectedId), [chatSessions, selectedId]);

  const openSession = async (id: string) => {
    await loadChatSession(id);
    setActiveSection('chat');
  };

  const removeSession = async (id: string) => {
    await deleteSessionById(id);
    if (selectedId === id) setSelectedId('');
  };

  return (
    <section className="zone-panel" data-zone="chat-list">
      <header className="zone-header">
        <div>
          <MessageSquare size={22} />
          <span>
            <em>Chat History</em>
            <strong>{t('全部对话')}</strong>
          </span>
        </div>
        <div className="zone-header-actions">
          <span className="zone-pill" data-ok="true">{chatSessions.length} {t('个会话')}</span>
        </div>
      </header>
      
      <div className="skills-workbench">
        <div className="zone-list skill-list-live">
          {grouped.length === 0 && (
            <article className="zone-empty">
              <MessageSquare size={18} />
              <span>{t('暂无会话')}</span>
            </article>
          )}
          {grouped.map(([month, items]) => (
            <div className="skill-group" key={month}>
              <span className="skill-group-label">{month}</span>
              {items.map(session => (
                <article
                  className="zone-row skill-row"
                  data-active={selectedId === session.id}
                  key={session.id}
                  onClick={() => setSelectedId(session.id)}
                >
                  <div>
                    <strong>{session.title || 'New Chat'}</strong>
                    <div className="skill-badges">
                      <em>{new Date(session.updatedAt * 1000).toLocaleString()}</em>
                      <em>{session.messageCount} msgs</em>
                    </div>
                  </div>
                  <div className="row-actions skill-actions">
                    <button
                      className="skill-detail-button"
                      type="button"
                      onClick={(e) => { e.stopPropagation(); void openSession(session.id); }}
                    >
                      {t('继续对话')}
                    </button>
                    <button
                      className="danger-action skill-delete-button"
                      type="button"
                      onClick={(e) => { e.stopPropagation(); void removeSession(session.id); }}
                    >
                      <Trash2 size={13} />
                      {t('删除')}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ))}
        </div>
        <aside className="skill-detail-panel">
          {!selectedSession ? (
            <div className="zone-empty">
              <MessageSquare size={18} />
              <span>{t('选择一个会话')}</span>
            </div>
          ) : (
            <>
              <header>
                <div>
                  <strong>{selectedSession.title || 'New Chat'}</strong>
                  <span>ID: {selectedSession.id}</span>
                  <small>
                    {t('创建于: ')} {new Date(selectedSession.createdAt * 1000).toLocaleString()}
                    {' · '}
                    {selectedSession.messageCount} {t('条消息')}
                  </small>
                </div>
              </header>
              <div className="skill-detail-actions">
                <button className="skill-toggle-button" type="button" onClick={() => void openSession(selectedSession.id)}>
                  <MessageSquare size={13} />
                  {t('进入对话')}
                </button>
              </div>
              <div style={{ padding: '16px', color: 'var(--text-secondary)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
                  <Clock size={16} />
                  <strong>{t('历史预览 (开发中)')}</strong>
                </div>
                <div style={{ background: 'var(--bg-tertiary)', padding: '12px', borderRadius: '6px', fontSize: '13px' }}>
                  [占位] {t('这里将展示会话的历史内容预览')}...
                </div>
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
