import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, LoaderCircle, Network, PanelRightOpen, X } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { ChatSubagentEvent } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';

export function SubagentGroup({ items, onDismiss }: { items: ChatSubagentEvent[]; onDismiss: () => void }) {
  const t = useT();
  const stats = useMemo(() => subagentStats(items), [items]);
  const setRightRailMode = useUiStore(state => state.setRightRailMode);

  if (items.length === 0) return null;

  return (
    <section className="subagent-group" aria-label={t('子代理并行状态')} data-compact="true">
      <button className="subagent-strip-main" type="button" onClick={() => setRightRailMode('activity')}>
        <Network size={15} />
        <div>
          <strong>{t('并行子代理')}</strong>
          <span>
            {stats.done}/{stats.total} {t('完成')}
            {stats.running ? ` · ${stats.running} ${t('运行中')}` : ''}
            {stats.error ? ` · ${stats.error} ${t('错误')}` : ''}
          </span>
        </div>
        <em>{stats.progress}%</em>
      </button>
      <div className="subagent-overall-progress" aria-label={`${t('整体进度')} ${stats.progress}%`}>
        <span style={{ width: `${stats.progress}%` }} />
      </div>
      <button className="subagent-open-activity-button" type="button" onClick={() => setRightRailMode('activity')}>
        <PanelRightOpen size={13} />
        {t('活动')}
      </button>
      <button className="subagent-dismiss-button" type="button" aria-label={t('隐藏子代理状态')} onClick={onDismiss}>
        <X size={13} />
      </button>
    </section>
  );
}

export function SubagentActivityPanel({ items }: { items: ChatSubagentEvent[] }) {
  const t = useT();
  const stats = useMemo(() => subagentStats(items), [items]);

  if (items.length === 0) {
    return (
      <div className="subagent-activity-empty">
        <Network size={18} />
        <strong>{t('暂无子代理活动')}</strong>
        <span>{t('并行子代理启动后会在这里显示每个任务的进度和结果。')}</span>
      </div>
    );
  }

  return (
    <section className="subagent-activity-panel" aria-label={t('子代理活动')}>
      <header>
        <span className="subagent-activity-icon">
          <Network size={15} />
        </span>
        <div>
          <strong>{t('并行子代理')}</strong>
          <span>
            {stats.done}/{stats.total} {t('完成')}
            {stats.running ? ` · ${stats.running} ${t('运行中')}` : ''}
            {stats.error ? ` · ${stats.error} ${t('错误')}` : ''}
          </span>
        </div>
        <em>{stats.progress}%</em>
      </header>
      <div className="subagent-overall-progress" aria-label={`${t('整体进度')} ${stats.progress}%`}>
        <span style={{ width: `${stats.progress}%` }} />
      </div>
      <div className="subagent-list">
        {items.map(item => (
          <SubagentCard item={item} key={item.taskId} />
        ))}
      </div>
    </section>
  );
}

function SubagentCard({ item }: { item: ChatSubagentEvent }) {
  const t = useT();
  const [open, setOpen] = useState(item.status === 'error');
  const setToolPreview = useUiStore(state => state.setToolPreview);
  const resultText = formatResult(item.result);
  const summary = item.summary || compactResult(item.result);
  const progress = clampProgress(item.progress);
  const elapsed = elapsedText(item.startedAt, item.finishedAt || item.updatedAt);
  const StatusIcon = item.status === 'error' ? AlertTriangle : item.status === 'done' ? CheckCircle2 : LoaderCircle;

  return (
    <article className="subagent-card" data-status={item.status}>
      <button className="subagent-open-button" type="button" onClick={() => setOpen(value => !value)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <StatusIcon className={item.status === 'running' ? 'spin' : undefined} size={14} />
        <span>{item.name}</span>
        {elapsed && <small>{elapsed}</small>}
        <em>{t(statusText(item.status))}</em>
      </button>
      <div className="subagent-card-body">
        <div className="subagent-progress" aria-label={`${item.name} ${progress}%`}>
          <span style={{ width: `${progress}%` }} />
        </div>
        <p>{summary || t('等待子代理输出')}</p>
        <div className="subagent-card-actions">
          <b>{progress}%</b>
          {resultText && (
            <button
              className="subagent-right-rail-button"
              type="button"
              onClick={() => setToolPreview({ title: `Subagent · ${item.name}`, content: resultText })}
            >
              {t('右栏查看')}
            </button>
          )}
        </div>
      </div>
      {open && resultText && <pre className="subagent-result">{resultText}</pre>}
    </article>
  );
}

function subagentStats(items: ChatSubagentEvent[]) {
  const total = items.length;
  const done = items.filter(item => item.status === 'done').length;
  const error = items.filter(item => item.status === 'error').length;
  const running = items.filter(item => item.status === 'running').length;
  const progress = total ? Math.round(items.reduce((sum, item) => sum + clampProgress(item.progress), 0) / total) : 0;
  return { total, done, error, running, progress };
}

function statusText(status: ChatSubagentEvent['status']): string {
  if (status === 'running') return '运行中';
  if (status === 'error') return '错误';
  return '完成';
}

function elapsedText(startedAt?: number, finishedAt?: number): string {
  if (!startedAt || !finishedAt || finishedAt < startedAt) return '';
  const ms = finishedAt - startedAt;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function compactResult(value: unknown): string {
  const text = formatResult(value).replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text.length > 150 ? `${text.slice(0, 150)}...` : text;
}

function formatResult(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function clampProgress(value: number): number {
  return Math.min(Math.max(value, 0), 100);
}
