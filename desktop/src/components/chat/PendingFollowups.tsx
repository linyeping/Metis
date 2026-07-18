import { AnimatePresence, motion } from 'framer-motion';
import { CornerDownRight, ListPlus, MoreHorizontal, Pencil, Play, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useT } from '../../hooks/useT';
import { MAX_PENDING_FOLLOWUPS, VISIBLE_PENDING_FOLLOWUPS } from '../../lib/followups';
import type { ChatFollowupBehavior, ChatRunFollowup } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';

const EMPTY_FOLLOWUPS: ChatRunFollowup[] = [];

export function PendingFollowups() {
  const t = useT();
  const sessionId = useSessionStore(state => state.activeSessionId);
  const items = useChatStore(state => (
    sessionId ? state.followupsBySession[sessionId] ?? EMPTY_FOLLOWUPS : EMPTY_FOLLOWUPS
  ));
  const streaming = useChatStore(state => state.streaming);
  const paused = useChatStore(state => Boolean(sessionId && state.pausedFollowupSessions[sessionId]));
  const updateBehavior = useChatStore(state => state.updateFollowupBehavior);
  const editFollowup = useChatStore(state => state.editFollowup);
  const removeFollowup = useChatStore(state => state.removeFollowup);
  const runNext = useChatStore(state => state.runNextFollowup);
  const rootRef = useRef<HTMLElement | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);

  useEffect(() => {
    if (!openMenuId) return undefined;
    const closeMenu = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return;
      setOpenMenuId(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMenuId(null);
    };
    window.addEventListener('pointerdown', closeMenu);
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      window.removeEventListener('pointerdown', closeMenu);
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [openMenuId]);

  if (!sessionId || items.length === 0) return null;

  return (
    <motion.section
      className="pending-followups"
      ref={rootRef}
      aria-label={t('待处理消息')}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 4 }}
    >
      <header className="pending-followups-header">
        <span><ListPlus size={13} />{t('待处理消息')} {items.length}/{MAX_PENDING_FOLLOWUPS}</span>
        {!streaming && paused && items.some(item => item.behavior === 'queue') && (
          <button type="button" onClick={() => void runNext(sessionId)}>
            <Play size={11} />{t('继续队列')}
          </button>
        )}
      </header>
      <div className="pending-followups-list" data-visible-rows={VISIBLE_PENDING_FOLLOWUPS}>
        <AnimatePresence initial={false}>
          {items.map(item => (
            <motion.div
              className="pending-followup-row"
              key={item.id}
              layout
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
            >
              <span className="pending-followup-message" title={item.message}>{item.message}</span>
              <small>{followupStatus(item.behavior, item.status, paused, t)}</small>
              <button
                type="button"
                className="pending-followup-behavior"
                data-behavior={item.behavior}
                disabled={!streaming || item.status !== 'pending'}
                title={item.behavior === 'queue' ? t('切换为引导') : t('切换为排队')}
                onClick={() => void updateBehavior(item.id, oppositeBehavior(item.behavior))}
              >
                {item.behavior === 'queue' ? <ListPlus size={11} /> : <CornerDownRight size={11} />}
                {item.behavior === 'queue' ? t('排队') : t('引导')}
              </button>
              <button
                type="button"
                className="pending-followup-action pending-followup-remove"
                aria-label={t('删除待处理消息')}
                title={t('删除待处理消息')}
                onClick={() => void removeFollowup(item.id)}
              >
                <Trash2 size={13} />
              </button>
              <div className="pending-followup-more-wrap">
                <button
                  type="button"
                  className="pending-followup-action"
                  aria-label={t('更多操作')}
                  aria-haspopup="menu"
                  aria-expanded={openMenuId === item.id}
                  onClick={() => setOpenMenuId(current => current === item.id ? null : item.id)}
                >
                  <MoreHorizontal size={14} />
                </button>
                <AnimatePresence>
                  {openMenuId === item.id && (
                    <motion.div
                      className="pending-followup-menu"
                      role="menu"
                      initial={{ opacity: 0, scale: 0.96, x: 4 }}
                      animate={{ opacity: 1, scale: 1, x: 0 }}
                      exit={{ opacity: 0, scale: 0.97, x: 3 }}
                    >
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          setOpenMenuId(null);
                          void editFollowup(item.id).then(() => {
                            requestAnimationFrame(() => {
                              document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus();
                            });
                          });
                        }}
                      >
                        <Pencil size={13} />
                        {t('编辑消息')}
                      </button>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </motion.section>
  );
}

function oppositeBehavior(behavior: ChatFollowupBehavior): ChatFollowupBehavior {
  return behavior === 'queue' ? 'steer' : 'queue';
}

function followupStatus(
  behavior: ChatFollowupBehavior,
  status: string,
  paused: boolean,
  t: (value: string) => string,
): string {
  if (paused || status === 'paused') return t('已暂停');
  return behavior === 'steer' ? t('等待当前步骤') : t('本轮完成后');
}
