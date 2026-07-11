import { AnimatePresence, motion } from 'framer-motion';
import { CornerDownRight, ListPlus, Play, X } from 'lucide-react';
import { useT } from '../../hooks/useT';
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
  const removeFollowup = useChatStore(state => state.removeFollowup);
  const runNext = useChatStore(state => state.runNextFollowup);

  if (!sessionId || items.length === 0) return null;

  return (
    <motion.section
      className="pending-followups"
      aria-label={t('待处理消息')}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 4 }}
    >
      <header className="pending-followups-header">
        <span><ListPlus size={13} />{t('待处理消息')} {items.length}/5</span>
        {!streaming && paused && items.some(item => item.behavior === 'queue') && (
          <button type="button" onClick={() => void runNext(sessionId)}>
            <Play size={11} />{t('继续队列')}
          </button>
        )}
      </header>
      <div className="pending-followups-list">
        <AnimatePresence initial={false}>
          {items.slice(0, 5).map(item => (
            <motion.div
              className="pending-followup-row"
              key={item.id}
              layout
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
            >
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
              <span className="pending-followup-message" title={item.message}>{item.message}</span>
              <small>{followupStatus(item.behavior, item.status, paused, t)}</small>
              <button
                type="button"
                className="pending-followup-remove"
                aria-label={t('删除待处理消息')}
                title={t('删除待处理消息')}
                onClick={() => void removeFollowup(item.id)}
              >
                <X size={13} />
              </button>
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
