import { ChevronDown, LoaderCircle, LockKeyhole, MessageCircle, Pencil, Plus, Send, Square, Trash2, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useSideChatStore, type SideChatSession } from '../../store/sideChatStore';
import { useT } from '../../hooks/useT';

interface SideChatPanelProps {
  defaultModel?: string;
  floating?: boolean;
  onClose?: () => void;
}

const SIDE_CHAT_HISTORY_HEIGHT_KEY = 'metis.sideChat.historyHeight';
const SIDE_CHAT_HISTORY_COLLAPSED_HEIGHT = 64;
const SIDE_CHAT_HISTORY_MIN_HEIGHT = 56;

function clampSideChatHistoryHeight(value: number, max = 320): number {
  return Math.min(Math.max(Math.round(value), SIDE_CHAT_HISTORY_MIN_HEIGHT), Math.max(SIDE_CHAT_HISTORY_MIN_HEIGHT, max));
}

function storedSideChatHistoryHeight(): number {
  try {
    return clampSideChatHistoryHeight(Number(localStorage.getItem(SIDE_CHAT_HISTORY_HEIGHT_KEY)) || 112);
  } catch {
    return 112;
  }
}

export function SideChatPanel({ defaultModel = '', floating = false, onClose }: SideChatPanelProps) {
  const t = useT();
  const sessions = useSideChatStore(state => state.sessions);
  const activeSessionId = useSideChatStore(state => state.activeSessionId);
  const composerText = useSideChatStore(state => state.composerText);
  const streaming = useSideChatStore(state => state.streaming);
  const error = useSideChatStore(state => state.error);
  const createSession = useSideChatStore(state => state.createSession);
  const selectSession = useSideChatStore(state => state.selectSession);
  const renameSession = useSideChatStore(state => state.renameSession);
  const deleteSession = useSideChatStore(state => state.deleteSession);
  const setComposerText = useSideChatStore(state => state.setComposerText);
  const send = useSideChatStore(state => state.send);
  const stop = useSideChatStore(state => state.stop);
  const clearActive = useSideChatStore(state => state.clearActive);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyHeight, setHistoryHeight] = useState(storedSideChatHistoryHeight);
  const [renamingId, setRenamingId] = useState('');
  const [renameDraft, setRenameDraft] = useState('');
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const historyResizeRef = useRef<{ height: number; max: number; y: number } | null>(null);
  const activeSession = sessions.find(session => session.id === activeSessionId) || sessions[0] || null;
  const messages = activeSession?.messages || [];
  const isolationLabel = t('独立上下文，不读取智能体任务、工具或工作区历史');
  const paneStyle = {
    '--side-chat-history-collapsed-height': `${SIDE_CHAT_HISTORY_COLLAPSED_HEIGHT}px`,
    '--side-chat-history-height': `${historyOpen ? historyHeight : SIDE_CHAT_HISTORY_COLLAPSED_HEIGHT}px`,
    '--side-chat-resizer-height': historyOpen ? '12px' : '0px',
  } as CSSProperties;

  useEffect(() => {
    const target = messagesRef.current;
    if (target) target.scrollTop = target.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = '0px';
    input.style.height = `${Math.min(Math.max(input.scrollHeight, 42), floating ? 156 : 150)}px`;
  }, [composerText, floating]);

  const submit = () => {
    void send();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    submit();
  };

  const startRename = (session: SideChatSession) => {
    setRenamingId(session.id);
    setRenameDraft(session.title);
  };

  const commitRename = () => {
    if (renamingId) renameSession(renamingId, renameDraft);
    setRenamingId('');
    setRenameDraft('');
  };

  const startHistoryResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (floating || !historyOpen) return;
    event.preventDefault();
    event.stopPropagation();
    const pane = event.currentTarget.closest('.side-chat-pane') as HTMLElement | null;
    const paneHeight = pane?.getBoundingClientRect().height || 620;
    const max = Math.min(Math.round(paneHeight * 0.48), Math.max(120, paneHeight - 320));
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-side-chat-history');
    historyResizeRef.current = { height: historyHeight, max, y: event.clientY };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      const start = historyResizeRef.current;
      if (!start) return;
      const nextHeight = clampSideChatHistoryHeight(start.height + moveEvent.clientY - start.y, start.max);
      setHistoryHeight(nextHeight);
      localStorage.setItem(SIDE_CHAT_HISTORY_HEIGHT_KEY, String(nextHeight));
    };
    const handleUp = () => {
      historyResizeRef.current = null;
      document.body.classList.remove('resizing-side-chat-history');
      document.removeEventListener('selectstart', preventSelection);
      try {
        resizeTarget.releasePointerCapture(event.pointerId);
      } catch {}
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };
    document.addEventListener('selectstart', preventSelection);
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  const handleHistoryResizeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!historyOpen) return;
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const pane = event.currentTarget.closest('.side-chat-pane') as HTMLElement | null;
    const paneHeight = pane?.getBoundingClientRect().height || 620;
    const max = Math.min(Math.round(paneHeight * 0.48), Math.max(120, paneHeight - 320));
    const nextHeight =
      event.key === 'Home'
        ? SIDE_CHAT_HISTORY_MIN_HEIGHT
        : event.key === 'End'
          ? max
          : historyHeight + (event.key === 'ArrowDown' ? 12 : -12);
    const clamped = clampSideChatHistoryHeight(nextHeight, max);
    setHistoryHeight(clamped);
    localStorage.setItem(SIDE_CHAT_HISTORY_HEIGHT_KEY, String(clamped));
  };

  return (
    <section className="side-chat-pane" data-floating={floating} aria-label={t('独立 Chat Dock')} style={paneStyle}>
      <aside className="side-chat-history" data-open={historyOpen} aria-label={t('独立 Chat 历史')}>
        <header>
          <div>
            <strong>{t('Chat 历史')}</strong>
            <span>{sessions.length}{t(' 条独立会话')}</span>
          </div>
          <div className="side-chat-history-head-actions">
            <button
              type="button"
              className="side-chat-history-toggle"
              title={historyOpen ? t('收起历史') : t('展开历史')}
              aria-expanded={historyOpen}
              onClick={() => setHistoryOpen(value => !value)}
            >
              <ChevronDown size={13} />
            </button>
            <button
              type="button"
              className="side-chat-history-new"
              title={t('新建独立 Chat')}
              onClick={() => {
                createSession(defaultModel);
                setHistoryOpen(true);
              }}
            >
              <Plus size={13} />
            </button>
          </div>
        </header>
        {historyOpen && <div className="side-chat-history-list">
          {sessions.map(session => (
            <article className="side-chat-history-row" data-active={session.id === activeSessionId} key={session.id}>
              {renamingId === session.id ? (
                <input
                  autoFocus
                  value={renameDraft}
                  onBlur={commitRename}
                  onChange={event => setRenameDraft(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter') commitRename();
                    if (event.key === 'Escape') {
                      setRenamingId('');
                      setRenameDraft('');
                    }
                  }}
                />
              ) : (
                <button type="button" className="side-chat-history-main" onClick={() => selectSession(session.id)}>
                  <strong>{session.title}</strong>
                  <span>{session.messages.length} {t('条消息')}</span>
                </button>
              )}
              <div className="side-chat-history-actions">
                <button type="button" title={t('重命名')} onClick={() => startRename(session)}>
                  <Pencil size={12} />
                </button>
                <button type="button" title={t('删除')} onClick={() => deleteSession(session.id)}>
                  <Trash2 size={12} />
                </button>
              </div>
            </article>
          ))}
        </div>}
      </aside>
      {!floating && (
        <div
          className="side-chat-vertical-resizer"
          aria-hidden={!historyOpen}
          aria-label={t('调整 Chat 历史高度')}
          aria-orientation="horizontal"
          aria-valuemax={320}
          aria-valuemin={SIDE_CHAT_HISTORY_MIN_HEIGHT}
          aria-valuenow={historyHeight}
          data-active={historyOpen}
          role="separator"
          tabIndex={historyOpen ? 0 : -1}
          title={t('拖动调整 Chat 上下区域高度')}
          onKeyDown={handleHistoryResizeKeyDown}
          onPointerDown={startHistoryResize}
        />
      )}
      <div className="side-chat-main">
        <div className="side-chat-boundary" aria-label={isolationLabel} title={isolationLabel}>
          <LockKeyhole size={14} />
          <div>
            <strong>{t('独立 Chat')}</strong>
            <span>{t('独立上下文')}</span>
          </div>
          <div className="side-chat-head-actions">
            <button type="button" title={t('清空当前 Chat')} onClick={clearActive}>
              <Trash2 size={13} />
            </button>
            {onClose && (
              <button type="button" title={t('收起 Chat')} onClick={onClose}>
                <X size={13} />
              </button>
            )}
          </div>
        </div>
        <div className="side-chat-messages" ref={messagesRef}>
          {messages.length === 0 && (
            <div className="rail-empty-card side-chat-empty">
              <MessageCircle size={18} />
              <strong>{t('右栏之外的独立聊天')}</strong>
              <span>{t('适合解释概念、拆想法、闲聊；不会污染智能体上下文。')}</span>
            </div>
          )}
          {messages.map(message => {
            const content = (message.content || '').trim();
            const pendingPlaceholder = message.pending && /^[.\u2026\s]+$/.test(content);
            const hasContent = Boolean(content) && !pendingPlaceholder;
            const emptyPending = Boolean(message.pending && !hasContent);
            return (
            <article className="side-chat-message" data-role={message.role} key={message.id}>
              <div className="side-chat-bubble" data-empty-pending={emptyPending} data-error={Boolean(message.error)} data-pending={Boolean(message.pending)}>
                {hasContent && <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>}
                {message.pending && (
                  <span className="side-chat-pending">
                    <LoaderCircle className="spin" size={12} />
                    {t('接收中')}
                  </span>
                )}
                {message.error && <span className="side-chat-error">{message.error}</span>}
              </div>
            </article>
            );
          })}
        </div>
        <form
          className="side-chat-composer"
          onSubmit={event => {
            event.preventDefault();
            submit();
          }}
        >
          {error && <p className="side-chat-error-line">{error}</p>}
          <textarea
            aria-label={t('独立 Chat 输入')}
            className="side-chat-input"
            disabled={streaming}
            onChange={event => setComposerText(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('问一个不会污染智能体上下文的问题...')}
            ref={inputRef}
            rows={1}
            value={composerText}
          />
          <div className="side-chat-actions">
            <div className="side-chat-footer-left" />
            {streaming ? (
              <button type="button" className="side-chat-send-button" onClick={stop}>
                <Square size={13} />
                {t('停止')}
              </button>
            ) : (
              <button type="submit" className="side-chat-send-button" disabled={!composerText.trim()}>
                <Send size={13} />
                {t('发送')}
              </button>
            )}
          </div>
        </form>
      </div>
    </section>
  );
}
