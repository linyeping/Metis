/**
 * ToolCallBlock — 工具调用展示组件（折叠/展开）。
 *
 * 从 MetisThread.tsx 拆分：ToolCard、ToolInlineDiffPreview、
 * ToolActivityGroup 及相关辅助函数。
 */
import {
  type ToolCallMessagePartProps,
  useAuiState,
} from '@assistant-ui/react';
import {
  Activity,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  FileDiff,
  FileText,
} from 'lucide-react';
import { Children, createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { PropsWithChildren, ReactNode } from 'react';
import { apiBase } from '../../lib/api';
import { buildFileChangePreview, countDiffLines } from '../../lib/diffPreview';
import type { FileChangePreview } from '../../lib/diffPreview';
import { isPreviewableWebFilePath, localFilePreviewUrl } from '../../lib/webPreview';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import type { ChatToolEvent } from '../../lib/types';
import {
  ChangeCount,
  compact,
  compactPath,
  elapsedText,
  formatTool,
  isToolError,
  toolCommandPreview,
  toolDisplayName,
  toolProgressText,
  toolStatusIcon,
} from './threadUtils';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MetisToolCardProps = ToolCallMessagePartProps & {
  metisStatus?: ChatToolEvent['status'];
  metisSummary?: string;
  metisErrorHint?: string;
  metisStartedAt?: number;
  metisFinishedAt?: number;
};

type FileChangeToolPart = {
  args?: unknown;
  metisFinishedAt?: unknown;
  metisStartedAt?: unknown;
  metisStatus?: string;
  result?: unknown;
  toolCallId?: unknown;
  toolName?: unknown;
  type?: unknown;
};

export type ToolActivitySummary = {
  completed: number;
  diff: { additions: number; removals: number };
  errors: number;
  progress: string;
  running: number;
  summary: string;
};

type ToolActivityContextValue = {
  collapseSignal: number;
  expandSignal: number;
};

type BrowserActivityCardSummary = {
  blocked: number;
  errors: number;
  last: string;
  summary: string;
};

const ToolActivityContext = createContext<ToolActivityContextValue>({ collapseSignal: 0, expandSignal: 0 });

// ---------------------------------------------------------------------------
// ToolActivityGroup — 工具活动组（折叠/展开步骤）
// ---------------------------------------------------------------------------

export function ToolActivityGroup({ children, endIndex, startIndex }: PropsWithChildren<{ startIndex: number; endIndex: number }>) {
  const t = useT();
  const childItems = Children.toArray(children);
  const steps = Math.max(1, childItems.length || endIndex - startIndex + 1);
  const [open, setOpen] = useState(false);
  const [expandSignal, setExpandSignal] = useState(0);
  const [collapseSignal, setCollapseSignal] = useState(0);
  // 用户一旦手动开/收过这一组，就不再自动展开——否则有报错时整组会被反复强制
  // 撑开（26 步全甩出来收不起来 = 用户报告的"堆叠在一起"）。
  const userToggledRef = useRef(false);
  const messageRunning = useAuiState(state => state.message.status?.type === 'running' && state.thread.isRunning);
  const activitySnapshot = useAuiState(state => toolActivitySnapshot(state.message.content, startIndex, endIndex));
  const activity = useMemo(() => summarizeToolActivity(activitySnapshot, t), [activitySnapshot, t]);
  const longList = steps > 12;

  useEffect(() => {
    // 只在用户尚未手动操作、且步数不多时，才因报错自动展开；长列表(>12)不自动撑开。
    if (activity.errors > 0 && !userToggledRef.current && !longList) setOpen(true);
  }, [activity.errors, longList]);

  const toggleOpen = () => {
    userToggledRef.current = true;
    setOpen(value => !value);
  };

  return (
    <section className="tool-activity-group" data-open={open} data-running={messageRunning}>
      <div className="tool-activity-group-head">
        <button className="tool-activity-toggle" type="button" onClick={toggleOpen}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          <Activity size={13} />
          <span>{t('工具活动')}</span>
          <small>{steps}{t(' 步')}</small>
          <em>{activity.summary}</em>
        </button>
        <div className="tool-activity-group-actions">
          <button type="button" onClick={() => { setOpen(true); setExpandSignal(value => value + 1); }}>
            {t('全部展开')}
          </button>
          <button type="button" onClick={() => setCollapseSignal(value => value + 1)}>
            {t('全部收起')}
          </button>
        </div>
      </div>
      <div className="tool-activity-progress">
        <span>{activity.progress}</span>
        <span className="tool-activity-counts">
          <b>✓{activity.completed}</b>
          {activity.running > 0 && <em>▶{activity.running}</em>}
          {activity.errors > 0 && <strong>!{activity.errors}</strong>}
        </span>
        {activity.diff.additions > 0 || activity.diff.removals > 0 ? (
          <span className="tool-activity-diff-count">
            <ChangeCount type="add" value={activity.diff.additions} />
            <ChangeCount type="remove" value={activity.diff.removals} />
          </span>
        ) : null}
      </div>
      <div className="tool-activity-group-body" aria-hidden={!open} data-long={longList}>
        <ToolActivityContext.Provider value={{ collapseSignal, expandSignal }}>
          {childItems as ReactNode[]}
        </ToolActivityContext.Provider>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// ToolCard — 单个工具调用卡片
// ---------------------------------------------------------------------------

export function ToolCard({
  toolCallId,
  toolName,
  args,
  result,
  metisStatus,
  metisSummary,
  metisErrorHint,
  metisStartedAt,
  metisFinishedAt,
}: MetisToolCardProps) {
  const t = useT();
  const { collapseSignal, expandSignal } = useContext(ToolActivityContext);
  const setToolPreview = useUiStore(state => state.setToolPreview);
  const setDiffPreview = useUiStore(state => state.setDiffPreview);
  const setWebPreviewUrl = useUiStore(state => state.setWebPreviewUrl);
  const status = metisStatus || (result === undefined ? 'running' : isToolError(result) ? 'error' : 'success');
  const commandPreview = useMemo(() => toolCommandPreview(toolName, args, result), [args, result, toolName]);
  const summary = useMemo(
    () => commandPreview || metisSummary || compact(result ?? args),
    [args, commandPreview, metisSummary, result],
  );
  const fileChangePreview = useMemo(() => buildFileChangePreview(toolName, args, result), [args, result, toolName]);
  const fileChangeCounts = useMemo(() => (fileChangePreview ? countDiffLines(fileChangePreview) : null), [fileChangePreview]);
  const browserActivity = useMemo(() => browserActivitySummaryFromResult(result, t), [result, t]);
  const autoOpenedDiffRef = useRef('');
  const autoOpenedWebRef = useRef('');
  const elapsed = elapsedText(metisStartedAt, metisFinishedAt);
  const statusText = status === 'waiting_approval' ? t('待确认') : status === 'running' ? t('运行中') : status === 'error' ? t('错误') : t('完成');
  const details = formatTool(result ?? args);
  const cardId = useMemo(() => stableToolCardId(toolCallId, toolName, args), [args, toolCallId, toolName]);
  const open = useUiStore(state => state.expandedToolCards.has(cardId));
  const setToolCardExpanded = useUiStore(state => state.setToolCardExpanded);
  const toolProgress = fileChangePreview && fileChangeCounts
    ? `${compactPath(fileChangePreview.path || fileChangePreview.title)} `
    : desktopExpertProgressText(toolName, status, args, result, t) || toolProgressText(toolName, status);

  useEffect(() => {
    if (status === 'error') setToolCardExpanded(cardId, true);
  }, [cardId, setToolCardExpanded, status]);

  useEffect(() => {
    if (expandSignal > 0) setToolCardExpanded(cardId, true);
  }, [cardId, expandSignal, setToolCardExpanded]);

  useEffect(() => {
    // "全部折叠"应能收起所有卡，包括失败卡（自动展开只在首次出错触发，不会再把它弹开）。
    if (collapseSignal > 0) setToolCardExpanded(cardId, false);
  }, [cardId, collapseSignal, setToolCardExpanded]);

  useEffect(() => {
    if (!fileChangePreview || status === 'running' || status === 'waiting_approval') return;
    if (autoOpenedDiffRef.current === fileChangePreview.id) return;
    autoOpenedDiffRef.current = fileChangePreview.id;
    if (status === 'success' && isPreviewableWebFilePath(fileChangePreview.path)) {
      autoOpenedWebRef.current = fileChangePreview.id;
      void apiBase()
        .then(base => {
          const url = localFilePreviewUrl(base, fileChangePreview.path);
          if (url && autoOpenedWebRef.current === fileChangePreview.id) {
            setWebPreviewUrl(url);
          }
        })
        .catch(() => {
          setDiffPreview(fileChangePreview);
        });
      return;
    }
    setDiffPreview(fileChangePreview);
  }, [fileChangePreview, setDiffPreview, setWebPreviewUrl, status]);

  return (
    <div className="tool-card tool-activity-row" data-open={open} data-status={status} data-call-id={toolCallId || cardId}>
      <button className="tool-card-head tool-activity-head" type="button" onClick={() => setToolCardExpanded(cardId, !open)}>
        <span className="tool-activity-caret">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
        <span className="tool-activity-status">{toolStatusIcon(status)}</span>
        <span className="tool-title">{toolDisplayName(toolName)}</span>
        <small>{summary}</small>
        <span className="tool-activity-meta">
          {elapsed && <b>{elapsed}</b>}
          <em>{statusText}</em>
        </span>
      </button>
      <p className="tool-progress-line">
        <span>{toolProgress}</span>
        {fileChangeCounts && (
          <span className="tool-activity-diff-count">
            <ChangeCount type="add" value={fileChangeCounts.additions} />
            <ChangeCount type="remove" value={fileChangeCounts.removals} />
          </span>
        )}
      </p>
      {browserActivity && (
        <div className="tool-browser-activity-summary" data-blocked={browserActivity.blocked > 0} data-errors={browserActivity.errors > 0}>
          <Activity size={12} />
          <span>{browserActivity.summary}</span>
          {browserActivity.last && <code>{browserActivity.last}</code>}
        </div>
      )}
      <div className="tool-card-actions">
        <button
          className="tool-card-open"
          type="button"
          onClick={() => setToolPreview({ title: toolName, content: details })}
        >
          <FileText size={12} />
          {t('详情')}
        </button>
        {fileChangePreview && (
          <button
            className="tool-card-diff"
            type="button"
            onClick={() => setDiffPreview(fileChangePreview)}
          >
            <FileDiff size={12} />
            {t('变更')}
          </button>
        )}
      </div>
      {status === 'error' && metisErrorHint ? <p className="tool-error-hint">{metisErrorHint}</p> : null}
      {open ? (
        <div className="tool-activity-details">
          {commandPreview && <pre className="tool-command-preview">{commandPreview}</pre>}
          {fileChangePreview ? <ToolInlineDiffPreview preview={fileChangePreview} /> : <pre>{details}</pre>}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ToolInlineDiffPreview — 工具内联 Diff
// ---------------------------------------------------------------------------

function ToolInlineDiffPreview({ preview }: { preview: FileChangePreview }) {
  const t = useT();
  const counts = countDiffLines(preview);
  const visibleLines = preview.diffLines.slice(0, 120);
  const hiddenCount = Math.max(0, preview.diffLines.length - visibleLines.length);
  return (
    <div className="tool-inline-diff" aria-label={t('工具 Diff 摘要')}>
      <header>
        <span title={preview.path}>{compactPath(preview.path || preview.title)}</span>
        <span className="tool-activity-diff-count">
          <ChangeCount type="add" value={counts.additions} />
          <ChangeCount type="remove" value={counts.removals} />
        </span>
      </header>
      <div className="tool-inline-diff-table">
        {visibleLines.map((line, index) => (
          <div className="tool-inline-diff-line" data-kind={line.kind} key={`${index}-${line.kind}-${line.oldLine ?? ''}-${line.newLine ?? ''}`}>
            <span>{line.oldLine ?? ''}</span>
            <span>{line.newLine ?? ''}</span>
            <code>{`${line.kind === 'add' ? '+ ' : line.kind === 'remove' ? '- ' : '  '}${line.text}`}</code>
          </div>
        ))}
        {hiddenCount > 0 && <p>{t('另有 ')}{hiddenCount}{t(' 行，打开右栏 Diff 查看完整内容。')}</p>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool part extraction helpers
// ---------------------------------------------------------------------------

type ToolActivityPart = {
  args: unknown;
  callId: string;
  metisFinishedAt?: number;
  metisStartedAt?: number;
  metisStatus: string;
  result: unknown;
  toolName: string;
};

function stableToolCardId(toolCallId: unknown, toolName: string, args: unknown): string {
  const callId = String(toolCallId || '').trim();
  if (callId) return callId;
  return `${toolName || 'tool'}:${hashToolCardSeed(formatTool(args).slice(0, 800))}`;
}

function hashToolCardSeed(value: string): string {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }
  return hash.toString(16);
}

function browserActivitySummaryFromResult(result: unknown, t: (text: string) => string): BrowserActivityCardSummary | null {
  const payload = resultObject(result);
  const activity = payload?.browser_activity || payload?.browserActivity;
  if (!activity || typeof activity !== 'object') return null;
  const row = activity as { counts?: Record<string, unknown>; items?: unknown[] };
  const counts = row.counts || {};
  const items = Array.isArray(row.items) ? row.items : [];
  const lastItem = items.length > 0 && typeof items[items.length - 1] === 'object'
    ? (items[items.length - 1] as Record<string, unknown>)
    : null;
  const navigate = numberField(counts.navigate);
  const observe = numberField(counts.observe);
  const action = numberField(counts.action);
  const screenshot = numberField(counts.screenshot);
  const blocked = numberField(counts.blocked);
  const errors = numberField(counts.errors);
  const parts = [
    navigate > 0 ? `${navigate} ${t('导航')}` : '',
    observe > 0 ? `${observe} ${t('观察')}` : '',
    action > 0 ? `${action} ${t('动作')}` : '',
    screenshot > 0 ? `${screenshot} ${t('截图')}` : '',
  ].filter(Boolean);
  return {
    blocked,
    errors,
    last: String(lastItem?.summary || lastItem?.error || '').slice(0, 160),
    summary: `${t('浏览器活动')} · ${parts.length > 0 ? parts.join(' · ') : t('暂无记录')}`,
  };
}

function resultObject(result: unknown): Record<string, unknown> | null {
  if (result && typeof result === 'object') return result as Record<string, unknown>;
  if (typeof result !== 'string') return null;
  try {
    const parsed = JSON.parse(result) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function numberField(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

export function toolPartValue(value: unknown): ToolActivityPart | null {
  if (!value || typeof value !== 'object') return null;
  const part = value as FileChangeToolPart;
  if (part.type !== 'tool-call' && part.toolName === undefined) return null;
  if (typeof part.toolName !== 'string' || !part.toolName) return null;
  return {
    args: part.args,
    callId: typeof part.toolCallId === 'string' ? part.toolCallId : '',
    metisFinishedAt: typeof part.metisFinishedAt === 'number' ? part.metisFinishedAt : undefined,
    metisStartedAt: typeof part.metisStartedAt === 'number' ? part.metisStartedAt : undefined,
    metisStatus: typeof part.metisStatus === 'string' ? part.metisStatus : '',
    result: part.result,
    toolName: part.toolName,
  };
}

export function toolPartsFromContent(content: unknown): ToolActivityPart[] {
  if (!Array.isArray(content)) return [];
  return content.map(toolPartValue).filter((part): part is ToolActivityPart => Boolean(part));
}

function toolPartsFromSnapshot(snapshot: string): ToolActivityPart[] {
  try {
    const parsed = JSON.parse(snapshot) as unknown;
    if (!Array.isArray(parsed)) return [];
    const tools: ToolActivityPart[] = [];
    for (const item of parsed) {
      if (!item || typeof item !== 'object') continue;
      const row = item as { args?: unknown; callId?: unknown; metisFinishedAt?: unknown; metisStartedAt?: unknown; metisStatus?: unknown; result?: unknown; toolName?: unknown };
      if (typeof row.toolName !== 'string') continue;
      tools.push({
        args: row.args,
        callId: typeof row.callId === 'string' ? row.callId : '',
        metisFinishedAt: typeof row.metisFinishedAt === 'number' ? row.metisFinishedAt : undefined,
        metisStartedAt: typeof row.metisStartedAt === 'number' ? row.metisStartedAt : undefined,
        metisStatus: typeof row.metisStatus === 'string' ? row.metisStatus : '',
        result: row.result,
        toolName: row.toolName,
      });
    }
    return tools;
  } catch {
    return [];
  }
}

function desktopExpertProgressText(
  toolName: string,
  status: string,
  args: unknown,
  result: unknown,
  t: (zh: string) => string = (s) => s,
): string {
  const name = String(toolName || '').toLowerCase();
  if (!isDesktopExpertToolName(name)) return '';
  if (status === 'waiting_approval') return t('Desktop Expert 等待确认');
  if (status === 'error') return t('Desktop Expert 已停止，展开查看失败原因');
  if (status === 'running') {
    if (name.includes('observe') || name.includes('screenshot') || name.includes('capture') || name.includes('status')) {
      return t('Desktop Expert 正在观察屏幕');
    }
    if (name.includes('action')) return desktopExpertActionText(args, t);
    return t('Desktop Expert 正在观察、计划、执行并验证');
  }
  if (name.includes('observe') || name.includes('screenshot') || name.includes('capture')) return t('Desktop Expert 已完成观察');
  if (name.includes('status')) return t('Desktop Expert 已读取运行状态');
  if (name.includes('action')) return t('Desktop Expert 已执行动作，等待验证');
  return desktopExpertResultText(result, t);
}

function isDesktopExpertToolName(name: string): boolean {
  return (
    name === 'desktop_expert' ||
    name.startsWith('desktop_win2_') ||
    name === 'desktop_vision_task' ||
    name.startsWith('desktop_window_') ||
    name === 'desktop_action' ||
    name === 'desktop_screenshot'
  );
}

function desktopExpertActionText(args: unknown, t: (zh: string) => string): string {
  const action = firstToolString(args, ['action', 'kind', 'type']).toLowerCase();
  if (action.includes('click')) return t('Desktop Expert 正在点击目标');
  if (action.includes('type') || action.includes('input')) return t('Desktop Expert 正在输入文本');
  if (action.includes('key') || action.includes('press')) return t('Desktop Expert 正在按键操作');
  if (action.includes('scroll')) return t('Desktop Expert 正在滚动页面');
  return t('Desktop Expert 正在执行桌面动作');
}

function desktopExpertResultText(result: unknown, t: (zh: string) => string): string {
  const text = formatTool(result);
  const lower = text.toLowerCase();
  if (/"fallback_recommended"\s*:\s*true/.test(lower) || lower.includes('fallback recommended')) {
    return t('Desktop Expert 已结束，建议切换备用视觉验证');
  }
  if (/"status"\s*:\s*"max_steps"/.test(lower)) {
    return t('Desktop Expert 已停止在步数上限，展开查看验证状态');
  }
  if (/"status"\s*:\s*"error"/.test(lower) || lower.includes('[expert error:') || lower.includes('failed')) {
    return t('Desktop Expert 已失败，展开查看详情');
  }
  if (/"status"\s*:\s*"done"/.test(lower) || lower.includes('goal satisfied') || lower.includes('completed')) {
    return t('Desktop Expert 已完成并验证');
  }
  return t('Desktop Expert 已完成');
}

function firstToolString(value: unknown, keys: string[]): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
  const row = value as Record<string, unknown>;
  for (const key of keys) {
    const field = row[key];
    if (typeof field === 'string' && field.trim()) return field.trim();
  }
  return '';
}

function toolActivitySnapshot(content: unknown, startIndex?: number, endIndex?: number): string {
  // FABLEADV-20: 按 content-part 范围切片，让每个工具活动组只统计自己那段，
  // 避免所有组都显示全局计数（如 "52/52"）。
  let source = content;
  if (Array.isArray(content) && typeof startIndex === 'number' && typeof endIndex === 'number') {
    source = content.slice(startIndex, endIndex + 1);
  }
  const tools = toolPartsFromContent(source);
  if (tools.length === 0) return '[]';
  try {
    return JSON.stringify(tools.map(tool => ({
      args: tool.args,
      callId: tool.callId,
      metisFinishedAt: tool.metisFinishedAt,
      metisStartedAt: tool.metisStartedAt,
      metisStatus: tool.metisStatus,
      result: tool.result,
      toolName: tool.toolName,
    })));
  } catch {
    return JSON.stringify(tools.map(tool => ({
      args: compact(tool.args),
      callId: tool.callId,
      metisFinishedAt: tool.metisFinishedAt,
      metisStartedAt: tool.metisStartedAt,
      metisStatus: tool.metisStatus,
      result: compact(tool.result),
      toolName: tool.toolName,
    })));
  }
}

export function summarizeToolActivity(snapshot: string, t: (zh: string) => string = (s) => s): ToolActivitySummary {
  const tools = toolPartsFromSnapshot(snapshot);
  if (tools.length === 0) {
    return { completed: 0, diff: { additions: 0, removals: 0 }, errors: 0, progress: t('等待工具事件'), running: 0, summary: t('暂无工具') };
  }
  let additions = 0;
  let removals = 0;
  for (const tool of tools) {
    const preview = buildFileChangePreview(tool.toolName, tool.args, tool.result);
    if (!preview) continue;
    const counts = countDiffLines(preview);
    additions += counts.additions;
    removals += counts.removals;
  }
  const running = tools.filter(tool => tool.metisStatus === 'running' || tool.metisStatus === 'waiting_approval');
  const errors = tools.filter(tool => tool.metisStatus === 'error');
  const completed = tools.length - running.length - errors.length;
  const current = running.at(-1) || tools.at(-1);
  const currentIndex = current ? tools.indexOf(current) + 1 : tools.length;
  const currentElapsed = current ? elapsedText(current.metisStartedAt, current.metisFinishedAt || Date.now()) : '';
  const currentLabel = current
    ? `${t('步骤')} ${currentIndex} · ${toolDisplayName(current.toolName)} · ${compact(current.result ?? current.args)}${currentElapsed ? ` · ${currentElapsed}` : ''}`
    : '';
  const summary = running.length
    ? `${t('正在执行 ')}${running.length}${t(' 个工具')}`
    : errors.length
      ? `${errors.length}${t(' 个工具失败')}`
      : `${completed}/${tools.length} ${t('完成')}`;
  return {
    completed,
    diff: { additions, removals },
    errors: errors.length,
    progress: currentLabel || summary,
    running: running.length,
    summary,
  };
}
