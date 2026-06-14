/**
 * NoticeCards — 通知和状态指示组件。
 *
 * 从 MetisThread.tsx 拆分：LearningNotice、RunRecoveryNotice、
 * RuntimeStatusBar、ContextOrganizingNotice。
 */
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  ClipboardCheck,
  LoaderCircle,
  Settings,
  Sparkles,
  X,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { useUiStore } from '../../store/uiStore';
import type { ChatMemoryNotice, ChatRunRecoverySnapshot, ChatTodoNotice, RuntimeStatus } from '../../lib/types';
import { compactPath, formatNoticeTime } from './threadUtils';
import { useT } from '../../hooks/useT';

// ---------------------------------------------------------------------------
// LearningNotice — 记忆学习通知
// ---------------------------------------------------------------------------

export function LearningNotice({ notice, onDismiss }: { notice: ChatMemoryNotice; onDismiss: () => void }) {
  const t = useT();
  const setActiveSection = useUiStore(state => state.setActiveSection);
  const setSettingsOpen = useUiStore(state => state.setSettingsOpen);
  const setSettingsSection = useUiStore(state => state.setSettingsSection);

  const openMemory = () => {
    setSettingsSection('conversation');
    setSettingsOpen(true);
  };
  const openSkills = () => {
    setActiveSection('skills');
  };

  return (
    <aside className="learning-notice" aria-label={t('自学习提示')}>
      <div className="learning-head">
        <span className="learning-icon">
          <Brain size={15} />
        </span>
        <div>
          <strong>{notice.message || t('已更新记忆')}</strong>
          <small>{formatNoticeTime(notice.createdAt)}</small>
        </div>
        <button className="icon-button subtle" type="button" aria-label={t('关闭学习提示')} onClick={onDismiss}>
          <X size={14} />
        </button>
      </div>
      <div className="learning-badges">
        <span>
          <Brain size={12} />
          {t('记忆')} +{notice.memoryCount}
        </span>
        <span>
          <Sparkles size={12} />
          {t('技能')} +{notice.skillCount}
        </span>
      </div>
      {(notice.memoryPath || notice.skillPath) && (
        <div className="learning-paths">
          {notice.memoryPath && <p title={notice.memoryPath}>{t('记忆')} {compactPath(notice.memoryPath)}</p>}
          {notice.skillPath && <p title={notice.skillPath}>{t('技能')} {compactPath(notice.skillPath)}</p>}
        </div>
      )}
      <div className="learning-actions">
        <button type="button" onClick={openMemory}>
          <Settings size={13} />
          {t('查看记忆')}
        </button>
        <button type="button" onClick={openSkills}>
          <Sparkles size={13} />
          {t('查看技能')}
        </button>
      </div>
    </aside>
  );
}

export function TodoNotice({ notice, onDismiss }: { notice: ChatTodoNotice; onDismiss: () => void }) {
  const current = notice.todos.find(item => {
    const status = String(item.status || '').toLowerCase();
    return status === 'in_progress' || status === 'active' || status === 'doing';
  });
  const currentText = String(current?.content || current?.task || current?.title || '').trim();
  const t = useT();

  return (
    <aside className="todo-notice" aria-label={t('任务清单更新')}>
      <div className="todo-head">
        <span className="todo-icon">
          <ClipboardCheck size={15} />
        </span>
        <div>
          <strong>{t('任务清单已更新')}</strong>
          <small>{formatNoticeTime(notice.createdAt)}</small>
        </div>
        <button className="icon-button subtle" type="button" aria-label={t('关闭任务清单提示')} onClick={onDismiss}>
          <X size={14} />
        </button>
      </div>
      <div className="todo-badges">
        <span>{t('总计')} {notice.todos.length}</span>
        <span>{t('进行中')} {notice.activeCount}</span>
        <span>{t('完成')} {notice.doneCount}</span>
      </div>
      {(currentText || notice.summary) && (
        <p title={notice.summary || currentText}>{currentText || notice.summary}</p>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// RunRecoveryNotice — 中断恢复通知
// ---------------------------------------------------------------------------

export function RunRecoveryNotice({
  notice,
  onContinue,
  onResume,
  onMarkFailed,
  onClear,
}: {
  notice: ChatRunRecoverySnapshot;
  onContinue: () => void;
  onResume: () => void;
  onMarkFailed: () => void;
  onClear: () => void;
}) {
  const t = useT();
  return (
    <aside className="run-recovery-notice" aria-label={t('长任务恢复提示')}>
      <div className="run-recovery-head">
        <span className="run-recovery-icon">
          <AlertTriangle size={15} />
        </span>
        <div>
          <strong>{t('检测到未完成的上次运行')}</strong>
          <small>
            {formatNoticeTime(notice.updatedAt)} · {notice.phase || 'streaming'} · {t('工具')} {notice.toolCount}
          </small>
        </div>
      </div>
      <p>{notice.display || t('上一次运行没有正常结束。')}</p>
      {notice.checkpoint && <p>{t('中断点: ')}{notice.checkpoint}</p>}
      {notice.lastUserPreview && <p>{t('原需求: ')}{notice.lastUserPreview}</p>}
      <blockquote>{notice.preview}</blockquote>
      <div className="run-recovery-actions">
        <button type="button" onClick={onResume} disabled={notice.canResume === false}>
          {t('继续执行')}
        </button>
        <button type="button" onClick={onContinue}>
          {t('继续查看')}
        </button>
        <button type="button" onClick={onMarkFailed}>
          {t('标记失败')}
        </button>
        <button type="button" onClick={onClear}>
          {t('清理状态')}
        </button>
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// RuntimeStatusBar — 运行时状态指示
// ---------------------------------------------------------------------------

export function RuntimeStatusBar({ status }: { status: RuntimeStatus | null }) {
  const t = useT();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!status || status.severity !== 'working') return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [status]);
  if (!status) return null;
  const Icon = status.severity === 'error' ? AlertTriangle : status.severity === 'done' ? CheckCircle2 : status.severity === 'working' ? LoaderCircle : Activity;
  const elapsed = status.startedAt ? liveElapsed(now - status.startedAt) : '';
  const meta = [
    status.turn ? `${t('第 ')}${status.turn}${t(' 轮')}` : '',
    status.toolCalls ? `${status.toolCalls}${t(' 步')}` : '',
    elapsed,
  ].filter(Boolean).join(' · ');
  const jumpToTool = () => {
    if (!status.callId) return;
    const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
      ? CSS.escape(status.callId)
      : status.callId.replace(/"/g, '\\"');
    const target = document.querySelector(`[data-call-id="${escaped}"]`);
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  };
  return (
    <div className="runtime-status-wrap" aria-live="polite">
      <button className="runtime-status" data-severity={status.severity} type="button" onClick={jumpToTool} disabled={!status.callId}>
        <Icon className={status.severity === 'working' ? 'spin' : undefined} size={14} />
        <span>{status.display}</span>
        {meta && <b>{meta}</b>}
        {status.hint && <em>{status.hint}</em>}
      </button>
    </div>
  );
}

function liveElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m${String(rest).padStart(2, '0')}s`;
}

// ---------------------------------------------------------------------------
// ContextOrganizingNotice — 上下文压缩动画
// ---------------------------------------------------------------------------

export function ContextOrganizingNotice() {
  const t = useT();
  return (
    <div className="message-row system inline-compaction-row" aria-live="polite" data-transient="true">
      <aside className="context-organizing-notice" aria-label={t('上下文整理中')}>
        <span className="context-box-stage" aria-hidden="true">
          <span className="context-box-hop">
            <span className="context-cube">
              <i className="context-cube-face front" />
              <i className="context-cube-face back" />
              <i className="context-cube-face right" />
              <i className="context-cube-face left" />
              <i className="context-cube-face top" />
              <i className="context-cube-face bottom" />
            </span>
          </span>
          <span className="context-gold-rail" />
        </span>
        <div className="context-organizing-copy">
          <strong>{t('正在整理上下文')}</strong>
          <small>{t('收束历史消息，保留关键线索')}</small>
        </div>
      </aside>
    </div>
  );
}
