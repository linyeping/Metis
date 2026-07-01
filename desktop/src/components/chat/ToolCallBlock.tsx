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
  BookOpenText,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  FileDiff,
  FileText,
  Search,
  PackageCheck,
} from 'lucide-react';
import { Children, createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { PropsWithChildren, ReactNode } from 'react';
import { apiBase, getResearchJob, getResearchJobs } from '../../lib/api';
import { buildFileChangePreview, countDiffLines } from '../../lib/diffPreview';
import type { FileChangePreview } from '../../lib/diffPreview';
import { isPreviewableWebFilePath, localFilePreviewUrl } from '../../lib/webPreview';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import type { ChatToolEvent, ResearchJob } from '../../lib/types';
import {
  ChangeCount,
  compact,
  compactPath,
  elapsedText,
  formatTool,
  isToolError,
  toolCommandPreview,
  toolDisplayName,
  toolKindGlyph,
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

type EvidenceChainCardSummary = {
  errors: number;
  last: string;
  summary: string;
};

type ArtifactActivityCardSummary = {
  artifactCount: number;
  errors: number;
  last: string;
  outputPath: string;
  summary: string;
  items: ArtifactActivityItem[];
};

type ResearchActivityCardSummary = {
  errors: number;
  fileName: string;
  jobId: string;
  last: string;
  opened: number;
  reportPath: string;
  searchResults: number;
  sources: number;
  summary: string;
  title: string;
};

export type CompletedResearchReportSummary = {
  fileName?: string;
  jobId: string;
  reportPath?: string;
  summary: string;
  title: string;
};

type ArtifactActivityItem = {
  at?: string;
  command?: string;
  detail?: string;
  duration_ms?: number;
  event?: string;
  ok?: boolean;
  path?: string;
  title?: string;
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
  const searchActivity = useMemo(() => summarizeSearchToolActivity(activitySnapshot, t), [activitySnapshot, t]);
  const completedResearchReport = useMemo(() => completedResearchReportFromSnapshot(activitySnapshot, t), [activitySnapshot, t]);
  const longList = steps > 12;

  if (searchActivity?.allSearchTools) {
    if (completedResearchReport && searchActivity.running === 0) {
      return <CompletedResearchReportEntry report={completedResearchReport} />;
    }
    if (searchActivity.running === 0 && searchActivity.errors === 0) {
      if (messageRunning) return <SearchActivityStrip activity={searchActivity} />;
      return completedResearchReport ? <CompletedResearchReportEntry report={completedResearchReport} /> : null;
    }
    return <SearchActivityStrip activity={searchActivity} />;
  }

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

export function CompletedResearchReportEntry({ report }: { report: CompletedResearchReportSummary }) {
  const t = useT();
  const setResearchReportView = useUiStore(state => state.setResearchReportView);
  const [resolvedReport, setResolvedReport] = useState(report);
  useEffect(() => {
    let disposed = false;
    setResolvedReport(report);
    if (!report.jobId || (report.fileName && report.reportPath)) return undefined;
    void getResearchJob(report.jobId)
      .then(job => {
        if (disposed) return;
        setResolvedReport({
          fileName: job.report_filename || report.fileName,
          jobId: job.id || report.jobId,
          reportPath: job.report_path || report.reportPath,
          summary: report.summary || researchJobEntrySummary(job, t),
          title: job.title || job.query || report.title,
        });
      })
      .catch(() => {
        if (!disposed) setResolvedReport(report);
      });
    return () => {
      disposed = true;
    };
  }, [report.fileName, report.jobId, report.reportPath, report.summary, report.title, t]);
  const fileName = resolvedReport.fileName || researchReportFileName(resolvedReport.title || resolvedReport.summary || t('研究报告'));
  return (
    <div className="research-report-entry">
      <span className="research-report-entry-icon">
        <FileText size={15} />
      </span>
      <div>
        <strong title={fileName}>{fileName}</strong>
        <small>{resolvedReport.summary || t('研究报告已生成')}</small>
      </div>
      <button type="button" disabled={!resolvedReport.jobId} onClick={() => resolvedReport.jobId && setResearchReportView(resolvedReport.jobId)}>
        <BookOpenText size={12} />
        {t('打开')}
      </button>
    </div>
  );
}

function researchJobEntrySummary(job: ResearchJob, t: (zh: string) => string): string {
  const sourceCount = job.stats?.sources || job.sources?.length || 0;
  return sourceCount > 0 ? `${t('Markdown 报告')} · ${sourceCount} ${t('个来源')}` : t('Markdown 报告已生成');
}

type SearchToolActivitySummary = {
  allSearchTools: boolean;
  domains: string[];
  errors: number;
  kind: string;
  label: string;
  query: string;
  running: number;
  sources: number;
};

function SearchActivityStrip({ activity }: { activity: SearchToolActivitySummary }) {
  const t = useT();
  const liveDomains = useLiveResearchDomains(activity);
  const domains = useMemo(
    () => uniqueDomains([...liveDomains, ...activity.domains]).slice(0, 5),
    [activity.domains, liveDomains],
  );
  const activeDomainIndex = useRotatingIndex(domains.length, activity.errors === 0 && (activity.running > 0 || domains.length > 1));
  const activeDomain = domains[activeDomainIndex] || '';
  const visibleDomains = useMemo(() => rotateDomains(domains, activeDomainIndex), [activeDomainIndex, domains]);
  return (
    <div
      className="search-activity-strip"
      data-errors={activity.errors > 0}
      data-has-domains={domains.length > 0}
      data-live={activity.errors === 0 && (activity.running > 0 || domains.length > 1)}
    >
      <Search size={13} />
      <span>{searchActivityLabel(activity, t, activeDomain)}</span>
      <em>{activity.query || t('整理来源')}</em>
      <div className="search-domain-stream" aria-label={t('正在访问的来源')}>
        {visibleDomains.length > 0 ? (
          visibleDomains.map((domain, index) => (
            <b className="search-domain-chip" data-active={index === 0} key={domain} title={domain}>
              <i aria-hidden="true">{domainInitial(domain)}</i>
              <span>{domain}</span>
            </b>
          ))
        ) : (
          <b className="search-domain-chip search-domain-chip-placeholder" aria-label={t('检索来源中')}>
            <i aria-hidden="true" />
            <span>{t('检索来源中')}</span>
          </b>
        )}
      </div>
    </div>
  );
}

function useRotatingIndex(length: number, enabled: boolean): number {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (!enabled || length <= 1) {
      setIndex(0);
      return undefined;
    }
    const timer = window.setInterval(() => {
      setIndex(value => (value + 1) % length);
    }, 1050);
    return () => window.clearInterval(timer);
  }, [enabled, length]);
  return length > 0 ? index % length : 0;
}

function useLiveResearchDomains(activity: SearchToolActivitySummary): string[] {
  const [domains, setDomains] = useState<string[]>([]);
  const lastDomainsRef = useRef<string[]>([]);
  useEffect(() => {
    if (activity.running <= 0 || activity.errors > 0) {
      setDomains([]);
      lastDomainsRef.current = [];
      return undefined;
    }

    let disposed = false;
    const refresh = async () => {
      try {
        const jobs = (await getResearchJobs(10)).jobs;
        const match = pickLiveResearchJob(jobs, activity);
        if (!match?.id) return;
        const job = await getResearchJob(match.id);
        if (disposed) return;
        const next = researchDomainsFromJob(job);
        if (next.length > 0) {
          lastDomainsRef.current = next;
          setDomains(next);
        } else {
          setDomains(lastDomainsRef.current);
        }
      } catch {
        if (!disposed) setDomains(current => current);
      }
    };

    void refresh();
    const timer = window.setInterval(() => void refresh(), 1600);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [activity.errors, activity.kind, activity.query, activity.running]);
  return domains;
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
  const setResearchJobPreview = useUiStore(state => state.setResearchJobPreview);
  const setResearchReportView = useUiStore(state => state.setResearchReportView);
  const appMode = useUiStore(state => state.appMode);
  const status = metisStatus || (result === undefined ? 'running' : isToolError(result) ? 'error' : 'success');
  const commandPreview = useMemo(() => toolCommandPreview(toolName, args, result), [args, result, toolName]);
  const artifactActivity = useMemo(() => artifactActivitySummaryFromResult(result, t), [result, t]);
  const summary = useMemo(
    () => artifactActivity?.summary || commandPreview || metisSummary || compact(result ?? args),
    [args, artifactActivity?.summary, commandPreview, metisSummary, result],
  );
  const fileChangePreview = useMemo(() => buildFileChangePreview(toolName, args, result), [args, result, toolName]);
  const fileChangeCounts = useMemo(() => (fileChangePreview ? countDiffLines(fileChangePreview) : null), [fileChangePreview]);
  const browserActivity = useMemo(() => browserActivitySummaryFromResult(result, t), [result, t]);
  const researchActivity = useMemo(() => researchActivitySummaryFromResult(result, t), [result, t]);
  const evidenceChain = useMemo(() => evidenceChainSummaryFromResult(result, t), [result, t]);
  const autoOpenedDiffRef = useRef('');
  const autoOpenedWebRef = useRef('');
  const autoOpenedResearchRef = useRef('');
  const elapsed = elapsedText(metisStartedAt, metisFinishedAt);
  const statusText = status === 'waiting_approval' ? t('待确认') : status === 'running' ? t('运行中') : status === 'error' ? t('错误') : t('完成');
  const details = stripResearchPayloadText(formatTool(result ?? args));
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

  useEffect(() => {
    if (appMode === 'chat' || !isResearchActivityTool(toolName) || status !== 'running') return;
    const marker = `running:${toolName}:${toolCallId || cardId}`;
    if (autoOpenedResearchRef.current === marker) return;
    autoOpenedResearchRef.current = marker;
    setResearchJobPreview();
  }, [appMode, cardId, setResearchJobPreview, status, toolCallId, toolName]);

  useEffect(() => {
    const jobId = researchActivity?.jobId || '';
    if (appMode === 'chat' || !jobId || status === 'running' || status === 'waiting_approval') return;
    if (autoOpenedResearchRef.current === jobId) return;
    autoOpenedResearchRef.current = jobId;
    setResearchJobPreview(jobId);
  }, [appMode, researchActivity?.jobId, setResearchJobPreview, status]);

  if (isResearchActivityTool(toolName)) {
    const failedResearchActivity = status === 'error' && researchActivityIsFailedResult(result);
    if (status === 'running' || status === 'waiting_approval' || failedResearchActivity) {
      return (
        <SearchActivityStrip
          activity={{
            allSearchTools: true,
            domains: researchDomainsFromTool(args, result),
            errors: status === 'error' ? 1 : 0,
            kind: toolName,
            label: researchRunningLabel(toolName, t),
            query: firstToolString(args, ['query', 'question', 'url', 'prompt']).slice(0, 120),
            running: status === 'running' || status === 'waiting_approval' ? 1 : 0,
            sources: researchSourceCountFromTool(result),
          }}
        />
      );
    }
    if (toolNameToResearchKind(toolName) === 'research' && researchActivity?.jobId) {
      return (
        <CompletedResearchReportEntry
          report={{
            fileName: researchActivity.fileName,
            jobId: researchActivity.jobId,
            reportPath: researchActivity.reportPath,
            summary: researchActivity.summary,
            title: researchActivity.title,
          }}
        />
      );
    }
    if (status === 'error') {
      return (
        <SearchActivityStrip
          activity={{
            allSearchTools: true,
            domains: researchDomainsFromTool(args, result),
            errors: 1,
            kind: toolName,
            label: researchRunningLabel(toolName, t),
            query: firstToolString(args, ['query', 'question', 'url', 'prompt']).slice(0, 120),
            running: 0,
            sources: researchSourceCountFromTool(result),
          }}
        />
      );
    }
    return null;
  }

  return (
    <div className="tool-card tool-activity-row" data-open={open} data-status={status} data-call-id={toolCallId || cardId}>
      <button className="tool-card-head tool-activity-head" type="button" onClick={() => setToolCardExpanded(cardId, !open)}>
        <span className="tool-activity-caret">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
        <span className="tool-activity-status">{toolStatusIcon(toolName, status)}</span>
        <span className="tool-kind-logo" aria-hidden="true">{toolKindGlyph(toolName)}</span>
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
      {researchActivity && (
        <div className="tool-browser-activity-summary tool-research-activity-summary" data-blocked={false} data-errors={researchActivity.errors > 0}>
          <Search size={12} />
          <span>{researchActivity.summary}</span>
          {researchActivity.last && <code>{researchActivity.last}</code>}
        </div>
      )}
      {evidenceChain && (
        <div className="tool-browser-activity-summary" data-blocked={false} data-errors={evidenceChain.errors > 0}>
          <ClipboardCheck size={12} />
          <span>{evidenceChain.summary}</span>
          {evidenceChain.last && <code>{evidenceChain.last}</code>}
        </div>
      )}
      {artifactActivity && (
        <div className="tool-browser-activity-summary tool-artifact-activity-summary" data-blocked={false} data-errors={artifactActivity.errors > 0}>
          <PackageCheck size={12} />
          <span>{artifactActivity.summary}</span>
          {artifactActivity.last && <code>{artifactActivity.last}</code>}
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
        {researchActivity?.jobId && (
          <button
            className="tool-card-research"
            type="button"
            onClick={() => setResearchJobPreview(researchActivity.jobId)}
          >
            <Search size={12} />
            {t('研究')}
          </button>
        )}
      </div>
      {status === 'error' && metisErrorHint ? <p className="tool-error-hint">{metisErrorHint}</p> : null}
      {open ? (
        <div className="tool-activity-details">
          {commandPreview && <pre className="tool-command-preview">{commandPreview}</pre>}
          {artifactActivity && <ToolArtifactActivityTimeline activity={artifactActivity} />}
          {fileChangePreview ? <ToolInlineDiffPreview preview={fileChangePreview} /> : <pre>{details}</pre>}
        </div>
      ) : null}
    </div>
  );
}

function ToolArtifactActivityTimeline({ activity }: { activity: ArtifactActivityCardSummary }) {
  const t = useT();
  return (
    <div className="tool-artifact-activity-timeline" aria-label={t('报告活动')}>
      <header>
        <span>{t('报告活动')}</span>
        <code>{compactPath(activity.outputPath || activity.last || '')}</code>
      </header>
      <div className="tool-artifact-activity-steps">
        {activity.items.map((item, index) => (
          <div className="tool-artifact-activity-step" data-ok={item.ok !== false} key={`${item.event || 'step'}-${index}`}>
            <span>{item.ok === false ? '!' : '✓'}</span>
            <div>
              <strong>{item.title || item.event || `${t('步骤')} ${index + 1}`}</strong>
              <small>
                {item.detail || item.command || item.path || ''}
                {typeof item.duration_ms === 'number' ? ` · ${item.duration_ms}ms` : ''}
              </small>
              {item.path && <code>{compactPath(item.path)}</code>}
            </div>
          </div>
        ))}
      </div>
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

function isResearchActivityTool(toolName: string): boolean {
  return ['web_search', 'web_research', 'fetch_content'].includes(String(toolName || '').trim());
}

function summarizeSearchToolActivity(snapshot: string, t: (zh: string) => string = (s) => s): SearchToolActivitySummary | null {
  const tools = toolPartsFromSnapshot(snapshot);
  if (tools.length === 0 || !tools.every(tool => isResearchActivityTool(tool.toolName))) return null;
  const running = tools.filter(tool => tool.metisStatus === 'running' || tool.metisStatus === 'waiting_approval');
  const errors = tools.filter(tool => tool.metisStatus === 'error');
  const focus = running.at(-1) || errors.at(-1) || tools.at(-1);
  return {
    allSearchTools: true,
    domains: Array.from(new Set(tools.flatMap(tool => researchDomainsFromTool(tool.args, tool.result)))).slice(0, 6),
    errors: errors.length,
    kind: focus?.toolName || '',
    label: focus ? researchRunningLabel(focus.toolName, t) : t('正在搜索'),
    query: focus ? firstToolString(focus.args, ['query', 'question', 'url', 'prompt']).slice(0, 120) : '',
    running: running.length,
    sources: tools.reduce((total, tool) => total + researchSourceCountFromTool(tool.result), 0),
  };
}

export function completedResearchReportFromContent(content: unknown, t: (zh: string) => string = (s) => s): CompletedResearchReportSummary | null {
  return completedResearchReportFromTools(toolPartsFromContent(content), t);
}

function completedResearchReportFromSnapshot(snapshot: string, t: (zh: string) => string = (s) => s): CompletedResearchReportSummary | null {
  return completedResearchReportFromTools(toolPartsFromSnapshot(snapshot), t);
}

function completedResearchReportFromTools(tools: ToolActivityPart[], t: (zh: string) => string): CompletedResearchReportSummary | null {
  for (const tool of [...tools].reverse()) {
    if (toolNameToResearchKind(tool.toolName) !== 'research') continue;
    if (tool.metisStatus === 'running' || tool.metisStatus === 'waiting_approval') continue;
    const activity = researchActivitySummaryFromResult(tool.result, t);
    if (!activity?.jobId) continue;
    if (tool.metisStatus === 'error' && researchActivityIsFailedResult(tool.result)) continue;
    return {
      fileName: activity.fileName,
      jobId: activity.jobId,
      reportPath: activity.reportPath,
      summary: activity.summary,
      title: activity.title,
    };
  }
  return null;
}

function pickLiveResearchJob(jobs: ResearchJob[], activity: SearchToolActivitySummary): ResearchJob | null {
  if (!jobs.length) return null;
  const kind = toolNameToResearchKind(activity.kind);
  const query = normalizeSearchText(activity.query);
  const now = Date.now();
  const candidates = jobs.filter(job => {
    const status = String(job.status || '').toLowerCase();
    if (!['running', 'queued', 'partial', 'complete'].includes(status)) return false;
    if (status === 'complete' && now - Number(job.updated_at || 0) > 15_000) return false;
    if (kind && toolNameToResearchKind(String(job.kind || '')) !== kind) return false;
    if (!query) return status === 'running' || status === 'queued';
    const jobQuery = normalizeSearchText(`${job.query || ''} ${job.title || ''}`);
    return jobQuery.includes(query) || query.includes(jobQuery);
  });
  if (!candidates.length) return null;
  return [...candidates]
    .sort((left, right) => Number(right.updated_at || 0) - Number(left.updated_at || 0))[0] || null;
}

function toolNameToResearchKind(value: string): string {
  const name = String(value || '').toLowerCase();
  if (name.includes('fetch_content') || name === 'fetch') return 'fetch';
  if (name.includes('web_research') || name === 'research') return 'research';
  if (name.includes('web_search') || name === 'search') return 'search';
  return '';
}

function normalizeSearchText(value: string): string {
  return String(value || '')
    .toLowerCase()
    .replace(/^https?:\/\//, '')
    .replace(/^www\./, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 160);
}

function researchRunningLabel(toolName: string, t: (zh: string) => string): string {
  const name = String(toolName || '').toLowerCase();
  if (name === 'web_research') return t('正在深度研究');
  if (name === 'fetch_content') return t('正在读取来源');
  return t('正在搜索');
}

function searchActivityLabel(activity: SearchToolActivitySummary, t: (zh: string) => string, activeDomain = ''): string {
  if (activity.errors > 0) return t('搜索遇到问题');
  const domain = activeDomain ? ` ${activeDomain}` : '';
  const count = activity.sources > 0 ? ` · ${activity.sources} ${t('个来源')}` : '';
  const kind = toolNameToResearchKind(activity.kind);
  if (activity.running > 0) {
    if (kind === 'research') return `${t('正在研究')}${domain}`;
    if (kind === 'fetch') return `${t('正在读取')}${domain}`;
    return `${t('正在搜索')}${domain}`;
  }
  if (kind === 'research') return `${t('已完成研究')}${count}`;
  if (kind === 'fetch') return `${t('来源已读取')}${count}`;
  return `${t('已检索')}${count}`;
}

function researchDomainsFromTool(args: unknown, result: unknown): string[] {
  const domains: string[] = [];
  const payload = resultObject(result);
  const activity = payload?.research_activity || payload;
  const sources = activity && typeof activity === 'object' && Array.isArray((activity as Record<string, unknown>).sources)
    ? (activity as Record<string, unknown>).sources as unknown[]
    : [];
  for (const source of sources) {
    if (!source || typeof source !== 'object') continue;
    const row = source as Record<string, unknown>;
    const domain = String(row.domain || safeHost(String(row.url || '')) || '').trim();
    if (domain) domains.push(domain.replace(/^www\./i, ''));
  }
  for (const key of ['url', 'href', 'source_url']) {
    const value = firstToolString(args, [key]);
    const domain = safeHost(value);
    if (domain) domains.push(domain.replace(/^www\./i, ''));
  }
  return uniqueDomains(domains);
}

function researchSourceCountFromTool(result: unknown): number {
  const payload = resultObject(result);
  const activity = payload?.research_activity || payload;
  if (!activity || typeof activity !== 'object') return 0;
  const row = activity as Record<string, unknown>;
  const stats = row.stats && typeof row.stats === 'object' ? row.stats as Record<string, unknown> : null;
  const statsSources = stats ? Number(stats.sources || stats.search_results || 0) : 0;
  if (Number.isFinite(statsSources) && statsSources > 0) return statsSources;
  return Array.isArray(row.sources) ? row.sources.length : 0;
}

function researchDomainsFromJob(job: ResearchJob): string[] {
  const domains: string[] = [];
  for (const source of job.sources || []) {
    domains.push(domainFromRecord(source));
  }
  for (const evidence of job.evidence || []) {
    domains.push(domainFromRecord(evidence));
  }
  for (const failure of job.failures || []) {
    domains.push(domainFromRecord(failure));
  }
  for (const attempt of job.attempts || []) {
    domains.push(domainFromRecord(attempt));
  }
  return uniqueDomains(domains).slice(0, 8);
}

function rotateDomains(domains: string[], activeIndex: number): string[] {
  if (domains.length <= 1) return domains;
  const index = Math.max(0, Math.min(activeIndex, domains.length - 1));
  return [...domains.slice(index), ...domains.slice(0, index)];
}

function domainFromRecord(row: object | null | undefined): string {
  const data = (row || {}) as Record<string, unknown>;
  const explicit = String(data.domain || data.host || data.source || '').trim();
  if (explicit && !/^https?:\/\//i.test(explicit)) return explicit.replace(/^www\./i, '');
  for (const key of ['url', 'href', 'source_url', 'final_url']) {
    const domain = safeHost(String(data[key] || ''));
    if (domain) return domain.replace(/^www\./i, '');
  }
  return '';
}

function uniqueDomains(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const normalized = normalizeDomain(value);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

function normalizeDomain(value: string): string {
  const text = String(value || '').trim();
  if (!text) return '';
  const host = safeHost(text) || text;
  return host
    .replace(/^https?:\/\//i, '')
    .replace(/^www\./i, '')
    .replace(/\/.*$/, '')
    .toLowerCase();
}

function domainInitial(domain: string): string {
  const value = domain.replace(/^www\./i, '').trim();
  return (value[0] || 'w').toUpperCase();
}

function safeHost(value: string): string {
  try {
    return new URL(value).hostname;
  } catch {
    return '';
  }
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

function researchActivitySummaryFromResult(result: unknown, t: (text: string) => string): ResearchActivityCardSummary | null {
  const payload = resultObject(result);
  const activity = payload?.research_activity || payload;
  if (!activity || typeof activity !== 'object') return null;
  const row = activity as { kind?: unknown; schema?: unknown; sources?: unknown[]; stats?: Record<string, unknown> };
  if (String(row.schema || '') !== 'metis.research_activity.v1' && !row.sources && !row.stats) return null;
  const stats = row.stats || {};
  const sources = Array.isArray(row.sources) ? row.sources : [];
  const title = String((activity as Record<string, unknown>).title || (activity as Record<string, unknown>).query || '').trim();
  const opened = numberField(stats.opened);
  const searchResults = numberField(stats.search_results);
  const failures = numberField(stats.failures);
  const sourceCount = numberField(stats.sources) || sources.length;
  const lastSource = sources.find(item => item && typeof item === 'object' && String((item as Record<string, unknown>).status || '') === 'opened')
    || sources.find(item => item && typeof item === 'object')
    || null;
  const last = lastSource && typeof lastSource === 'object'
    ? String((lastSource as Record<string, unknown>).domain || (lastSource as Record<string, unknown>).title || '').slice(0, 180)
    : '';
  const kind = String(row.kind || '');
  const parts = [
    searchResults > 0 ? `${searchResults} ${t('条搜索')}` : '',
    sourceCount > 0 ? `${sourceCount} ${t('个来源')}` : '',
    opened > 0 ? `${opened} ${t('已读取')}` : '',
    failures > 0 ? `${failures} ${t('失败')}` : '',
  ].filter(Boolean);
  return {
    errors: failures,
    fileName: String((row as Record<string, unknown>).report_filename || (row as Record<string, unknown>).reportFilename || ''),
    jobId: String((row as Record<string, unknown>).job_id || (row as Record<string, unknown>).jobId || ''),
    last,
    opened,
    reportPath: String((row as Record<string, unknown>).report_path || (row as Record<string, unknown>).reportPath || ''),
    searchResults,
    sources: sourceCount,
    summary: `${kind === 'fetch_content' ? t('来源读取') : t('研究活动')} · ${parts.length > 0 ? parts.join(' · ') : t('暂无来源')}`,
    title,
  };
}

function researchActivityIsFailedResult(result: unknown): boolean {
  const payload = resultObject(result);
  const activity = payload?.research_activity || payload;
  if (!activity || typeof activity !== 'object') return false;
  const row = activity as Record<string, unknown>;
  const schema = String(row.schema || '');
  const kind = String(row.kind || '').toLowerCase();
  if (schema !== 'metis.research_activity.v1' && !['search', 'research', 'fetch', 'fetch_content'].includes(kind)) {
    return false;
  }
  const status = String(row.job_status || row.status || '').toLowerCase();
  return status === 'error' || status === 'failed' || payload?.ok === false;
}

function researchReportFileName(value: string): string {
  const base = String(value || '研究报告')
    .replace(/[\\/:*?"<>|\r\n\t]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 72) || '研究报告';
  return /\.md$/i.test(base) ? base : `${base}.md`;
}

function evidenceChainSummaryFromResult(result: unknown, t: (text: string) => string): EvidenceChainCardSummary | null {
  const payload = resultObject(result);
  if (!payload) return null;
  const checks = payload.checks && typeof payload.checks === 'object' ? payload.checks as Record<string, unknown> : null;
  const verdict = payload.verdict && typeof payload.verdict === 'object' ? payload.verdict as Record<string, unknown> : null;
  const evidence = Array.isArray(payload.evidence_chain_v2)
    ? payload.evidence_chain_v2
    : Array.isArray(payload.evidenceChainV2)
      ? payload.evidenceChainV2
      : Array.isArray(payload.evidence_chain)
        ? payload.evidence_chain
        : Array.isArray(payload.evidenceChain)
          ? payload.evidenceChain
          : [];
  if (!checks && evidence.length === 0) return null;
  const checkValues = checks ? Object.values(checks).map(Boolean) : [];
  const passed = checkValues.filter(Boolean).length;
  const failed = Number(verdict?.failed ?? checkValues.length - passed) || 0;
  const lastEvidence = evidence.length > 0 && typeof evidence[evidence.length - 1] === 'object'
    ? evidence[evidence.length - 1] as Record<string, unknown>
    : null;
  const last = evidenceSummaryText(lastEvidence);
  const verdictSummary = typeof verdict?.summary === 'string' ? verdict.summary : '';
  const total = Number(verdict?.total ?? checkValues.length) || checkValues.length;
  const summary = verdictSummary
    ? `${t('验收证据')} · ${verdictSummary}`
    : total > 0
      ? `${t('验收证据')} · ${passed}/${total} ${t('通过')}`
      : `${t('验收证据')} · ${evidence.length} ${t('条')}`;
  return { errors: failed, last, summary };
}

function artifactActivitySummaryFromResult(result: unknown, t: (text: string) => string): ArtifactActivityCardSummary | null {
  const payload = resultObject(result);
  const activity = payload?.artifact_activity || payload?.artifactActivity;
  if (!activity || typeof activity !== 'object') return null;
  const row = activity as Record<string, unknown>;
  const items = Array.isArray(row.items) ? row.items.filter(isArtifactActivityItem) : [];
  const artifacts = Array.isArray(row.artifacts) ? row.artifacts : [];
  const errors = items.filter(item => item.ok === false).length;
  const outputPath = String(row.output_path || row.outputPath || payload?.output_path || '');
  const lastItem = [...items].reverse().find(item => item.detail || item.path || item.command || item.title);
  const last = String(lastItem?.detail || lastItem?.path || lastItem?.command || lastItem?.title || outputPath).slice(0, 180);
  const summary = String(row.summary || '').trim()
    || `${t('报告活动')} · ${items.length} ${t('步')} · ${artifacts.length} ${t('个产物')}`;
  return {
    artifactCount: artifacts.length,
    errors,
    items,
    last,
    outputPath,
    summary,
  };
}

function isArtifactActivityItem(value: unknown): value is ArtifactActivityItem {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function evidenceSummaryText(item: Record<string, unknown> | null): string {
  if (!item) return '';
  const kind = String(item.kind || item.source || '').trim();
  if (typeof item.summary === 'string' && item.summary.trim()) {
    return item.summary.slice(0, 180);
  }
  if (kind === 'screenshot') {
    return String(item.path || item.saved_path || '').slice(0, 180);
  }
  if (kind === 'text_match') {
    return `${String(item.query || '').slice(0, 80)} ${item.matched === false ? 'not matched' : 'matched'}`;
  }
  if (kind === 'window') {
    return String(item.title || item.exe || '').slice(0, 160);
  }
  return String(item.summary || item.text || kind || '').slice(0, 180);
}

function resultObject(result: unknown): Record<string, unknown> | null {
  if (result && typeof result === 'object') return result as Record<string, unknown>;
  if (typeof result !== 'string') return null;
  const embedded = researchPayloadFromText(result);
  if (embedded) return embedded;
  try {
    const parsed = JSON.parse(result) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function researchPayloadFromText(text: string): Record<string, unknown> | null {
  const match = String(text || '').match(/<!--\s*METIS_RESEARCH_JSON\s+([\s\S]*?)\s*-->/);
  if (!match?.[1]) return null;
  try {
    const parsed = JSON.parse(match[1]) as unknown;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function stripResearchPayloadText(text: string): string {
  return String(text || '').replace(/<!--\s*METIS_RESEARCH_JSON\s+[\s\S]*?\s*-->\s*/g, '').trim();
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
