/**
 * MessageBubble — 消息渲染组件。
 *
 * 从 MetisThread.tsx 拆分：AssistantMessage、UserMessage、
 * UserAttachmentList、SystemMessage、ContextSummaryCard。
 */
import {
  groupPartByType,
  type EnrichedPartState,
  MessagePartPrimitive,
  MessagePrimitive,
  useAuiState,
} from '@assistant-ui/react';
import {
  ClipboardCheck,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  Image as ImageIcon,
  Sparkles,
  Undo2,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { MouseEvent } from 'react';
import { useChatStore } from '../../store/chatStore';
import { useUiStore } from '../../store/uiStore';
import { apiBase, getResearchJobs, getWorkspaceFile } from '../../lib/api';
import { isCompactBoundary, parseCompactBoundary } from '../../lib/compactBoundary';
import { chatLinkActionFromHref } from '../../lib/metisLinks';
import type { ParsedFile, ResearchJob } from '../../lib/types';
import type { FileChangeSummary } from '../../lib/diffPreview';
import { isPreviewableWebFilePath, localFilePreviewUrl } from '../../lib/webPreview';
import { useT } from '../../hooks/useT';
import { FileChangeReviewCard, summarizeMessageFileChanges } from './FileChangeReviewCard';
import { completedResearchReportFromContent, CompletedResearchReportEntry, ToolActivityGroup, ToolCard } from './ToolCallBlock';
import type { CompletedResearchReportSummary } from './ToolCallBlock';
import {
  attachmentMeta,
  copyTextToClipboard,
  isContextSummary,
  MarkdownText,
  messageAttachments,
  messageText,
  parseContextSummary,
} from './threadUtils';

// ---------------------------------------------------------------------------
// AssistantMessage
// ---------------------------------------------------------------------------

type AssistantPartGroupKey = 'group-tool-activity';

const assistantPartGroupBy = groupPartByType<AssistantPartGroupKey>({
  'tool-call': ['group-tool-activity'],
});

export function AssistantMessage() {
  const t = useT();
  const status = useAuiState(state => state.message.status?.type);
  const messageId = useAuiState(state => state.message.id);
  const content = useAuiState(state => state.message.content);
  const text = useAuiState(state => messageText(state.message.content));
  const appMode = useUiStore(state => state.appMode);
  const setWebPreviewUrl = useUiStore(state => state.setWebPreviewUrl);
  const setPreviewPath = useUiStore(state => state.setPreviewPath);
  const pushToast = useUiStore(state => state.pushToast);
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const [copied, setCopied] = useState(false);
  const fileChangeSummary = useMemo(() => summarizeMessageFileChanges(messageId, content), [content, messageId]);
  const toolStats = useMemo(() => messageToolStats(content), [content]);
  const completedResearchReport = useMemo(() => completedResearchReportFromContent(content, t), [content, t]);
  const longResearchReportText = useMemo(() => isLongResearchReportText(text), [text]);
  const latestResearchReport = useLatestResearchReport(longResearchReportText && !completedResearchReport, text, t);
  const effectiveResearchReport = completedResearchReport || latestResearchReport || (longResearchReportText ? placeholderResearchReport(text, t) : null);
  const suppressLongResearchReport = Boolean(longResearchReportText && effectiveResearchReport);
  const showStandaloneResearchReportEntry = Boolean(suppressLongResearchReport && effectiveResearchReport && !toolStats.hasTools);
  const openFileLink = async (path: string) => {
    try {
      await getWorkspaceFile(path);
      if (isPreviewableWebFilePath(path)) {
        const url = localFilePreviewUrl(await apiBase(), path);
        if (!url) throw new Error(t('不支持预览此 HTML 文件'));
        setWebPreviewUrl(url);
        return;
      }
      setPreviewPath(path);
    } catch (err) {
      pushToast({
        title: t('无法预览文件'),
        description: err instanceof Error ? err.message : String(err),
        type: 'warning',
      });
    }
  };
  const openExternalLink = async (url: string) => {
    try {
      if (window.metis?.openExternal) {
        const result = await window.metis.openExternal(url);
        if (!result?.ok) throw new Error(t('默认浏览器拒绝打开此链接'));
        return;
      }
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (err) {
      pushToast({
        title: t('无法用默认浏览器打开链接'),
        description: err instanceof Error ? err.message : String(err),
        type: 'warning',
      });
    }
  };
  const copyAssistantText = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!text.trim()) return;
    await copyTextToClipboard(text);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1000);
  };
  return (
    <MessagePrimitive.Root
      className="message-row assistant"
      data-running={status === 'running'}
      onClick={(event: MouseEvent<HTMLElement>) => {
        const target = event.target as HTMLElement;
        const anchor = target.closest('a');
        if (!anchor) return;
        // 聊天区任何链接点击都不允许真导航——否则空/相对 href 会把整个 app 重载（FABLEADV-30）。
        event.preventDefault();
        const href = anchor.getAttribute('href') || '';
        const action = chatLinkActionFromHref(href, anchor.getAttribute('data-link-kind') || '');
        if (!action) return;
        if (action.kind === 'file') {
          void openFileLink(action.path);
          return;
        }
        if (appMode === 'chat') {
          void openExternalLink(action.url);
          return;
        }
        void requestConfirm({
          title: t('在右栏打开链接？'),
          message: t('确认后会在 Metis 右侧预览栏打开这个网页。'),
          details: action.url,
          confirmLabel: t('打开链接'),
          cancelLabel: t('取消'),
          icon: 'external',
        }).then(confirmed => {
          if (confirmed) setWebPreviewUrl(action.url);
        });
      }}
    >
      <div className="assistant-message-stack" data-has-tools={toolStats.hasTools}>
        <AssistantMessageParts
          completedResearchReport={effectiveResearchReport}
          fileChangeSummary={fileChangeSummary}
          lastToolIndex={toolStats.lastToolIndex}
          suppressLongResearchReport={suppressLongResearchReport}
        />
        {showStandaloneResearchReportEntry && effectiveResearchReport && (
          <CompletedResearchReportEntry report={effectiveResearchReport} />
        )}
        {text.trim() && !suppressLongResearchReport && (
          <div className="assistant-message-actions">
            <button className="assistant-copy-button" type="button" onClick={event => void copyAssistantText(event)} title={t('复制回复')}>
              {copied ? <ClipboardCheck size={14} /> : <Copy size={14} />}
              <span>{copied ? t('已复制') : t('复制')}</span>
            </button>
          </div>
        )}
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessageParts({
  completedResearchReport,
  fileChangeSummary,
  lastToolIndex,
  suppressLongResearchReport,
}: {
  completedResearchReport: CompletedResearchReportSummary | null;
  fileChangeSummary: FileChangeSummary | null;
  lastToolIndex: number;
  suppressLongResearchReport: boolean;
}) {
  return (
    <MessagePrimitive.GroupedParts groupBy={assistantPartGroupBy} indicator="never">
      {({ part, children }) => {
        switch (part.type) {
          case 'group-tool-activity': {
            const startIndex = part.indices[0] ?? 0;
            const endIndex = part.indices[part.indices.length - 1] ?? startIndex;
            const showFileChangeSummary = Boolean(fileChangeSummary && part.indices.includes(lastToolIndex));
            return (
              <>
                <ToolActivityGroup startIndex={startIndex} endIndex={endIndex}>
                  {children}
                </ToolActivityGroup>
                {showFileChangeSummary && fileChangeSummary && <FileChangeReviewCard summary={fileChangeSummary} />}
              </>
            );
          }
          case 'tool-call':
            return renderToolPart(part);
          case 'text':
            if (!part.text.trim() && part.status?.type !== 'running') return null;
            if (suppressLongResearchReport && isLongResearchReportText(part.text)) return null;
            return (
              <div className="assistant-prose-block">
                <MarkdownText text={part.text} />
              </div>
            );
          case 'image':
            return <MessagePartPrimitive.Image />;
          case 'data':
            return part.dataRendererUI || null;
          case 'indicator':
          case 'reasoning':
          case 'source':
          case 'file':
          case 'audio':
          default:
            return null;
        }
      }}
    </MessagePrimitive.GroupedParts>
  );
}

function renderToolPart(part: Extract<EnrichedPartState, { type: 'tool-call' }>) {
  return part.toolUI || <ToolCard {...part} />;
}

function messageToolStats(content: unknown): { hasTools: boolean; lastToolIndex: number } {
  if (!Array.isArray(content)) return { hasTools: false, lastToolIndex: -1 };
  let lastToolIndex = -1;
  for (let index = 0; index < content.length; index += 1) {
    const part = content[index];
    if (part && typeof part === 'object' && (part as { type?: unknown }).type === 'tool-call') {
      lastToolIndex = index;
    }
  }
  return { hasTools: lastToolIndex >= 0, lastToolIndex };
}

function isLongResearchReportText(text: string): boolean {
  const value = String(text || '').trim();
  if (value.length < 1400) return false;
  const headingCount = (value.match(/^#{1,3}\s+/gm) || []).length;
  const urlCount = (value.match(/https?:\/\/[^\s)]+/g) || []).length;
  const reportCue = /(研究报告|调研报告|深度研究|最终判断|来源|引用|证据)/.test(value);
  return reportCue && (headingCount >= 3 || urlCount >= 4);
}

function useLatestResearchReport(enabled: boolean, text: string, t: (zh: string) => string): CompletedResearchReportSummary | null {
  const [report, setReport] = useState<CompletedResearchReportSummary | null>(null);
  useEffect(() => {
    if (!enabled) {
      setReport(null);
      return undefined;
    }
    let disposed = false;
    const refresh = async () => {
      try {
        const jobs = (await getResearchJobs(12)).jobs;
        const job = pickLikelyResearchJob(jobs, text);
        if (disposed) return;
        setReport(job ? researchReportFromJob(job, t) : null);
      } catch {
        if (!disposed) setReport(null);
      }
    };
    void refresh();
    const timer = window.setTimeout(() => void refresh(), 1800);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [enabled, text, t]);
  return report;
}

function pickLikelyResearchJob(jobs: ResearchJob[], text: string): ResearchJob | null {
  const candidates = jobs.filter(job => {
    const status = String(job.status || '').toLowerCase();
    return String(job.kind || '') === 'research' && ['complete', 'partial', 'running'].includes(status);
  });
  if (!candidates.length) return null;
  const normalizedText = normalizeResearchMatchText(text);
  return [...candidates].sort((left, right) => {
    const leftScore = researchJobMatchScore(left, normalizedText);
    const rightScore = researchJobMatchScore(right, normalizedText);
    if (rightScore !== leftScore) return rightScore - leftScore;
    return Number(right.updated_at || 0) - Number(left.updated_at || 0);
  })[0] || null;
}

function researchJobMatchScore(job: ResearchJob, normalizedText: string): number {
  let score = 0;
  const title = normalizeResearchMatchText(job.title || '');
  const query = normalizeResearchMatchText(job.query || '');
  if (title && normalizedText.includes(title.slice(0, 42))) score += 8;
  if (query && normalizedText.includes(query.slice(0, 42))) score += 5;
  if (job.report_filename) score += 3;
  if (job.report) score += 3;
  score += Math.min(3, job.sources?.length || 0);
  return score;
}

function researchReportFromJob(job: ResearchJob, t: (zh: string) => string): CompletedResearchReportSummary {
  const sourceCount = job.stats?.sources || job.sources?.length || 0;
  const summary = sourceCount > 0
    ? `${t('Markdown 报告')} · ${sourceCount} ${t('个来源')}`
    : t('Markdown 报告已生成');
  return {
    fileName: job.report_filename || '',
    jobId: job.id,
    reportPath: job.report_path || '',
    summary,
    title: job.title || job.query || t('研究报告'),
  };
}

function placeholderResearchReport(text: string, t: (zh: string) => string): CompletedResearchReportSummary {
  const title = extractResearchReportTitle(text) || t('研究报告');
  return {
    fileName: `${safeReportFilename(title)}.md`,
    jobId: '',
    reportPath: '',
    summary: t('正在整理 Markdown 报告入口'),
    title,
  };
}

function extractResearchReportTitle(text: string): string {
  const heading = String(text || '').match(/^\s*#{1,3}\s+(.+)$/m)?.[1] || '';
  if (heading.trim()) return stripInlineMarkdown(heading).slice(0, 90);
  const bold = String(text || '').match(/\*\*([^*\n]{6,90})\*\*/)?.[1] || '';
  return stripInlineMarkdown(bold).slice(0, 90);
}

function normalizeResearchMatchText(value: string): string {
  return stripInlineMarkdown(value).replace(/\s+/g, '').toLowerCase().slice(0, 4000);
}

function stripInlineMarkdown(value: string): string {
  return String(value || '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^#+\s*/, '')
    .trim();
}

function safeReportFilename(value: string): string {
  return String(value || '研究报告')
    .replace(/[\\/:*?"<>|\r\n\t]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 72) || '研究报告';
}

// ---------------------------------------------------------------------------
// UserMessage
// ---------------------------------------------------------------------------

export function UserMessage() {
  const t = useT();
  const messageId = useAuiState(state => state.message.id);
  const text = useAuiState(state => messageText(state.message.content));
  const attachments = useAuiState(state => messageAttachments(state.message.metadata));
  const undoLastTurn = useChatStore(state => state.undoLastTurn);
  const isLatestUserMessage = useChatStore(state => {
    const lastUser = [...state.messages].reverse().find(message => message.role === 'user');
    return lastUser?.id === messageId;
  });
  const [copied, setCopied] = useState(false);
  const copyUserText = async (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const payload = text.trim() || attachments.map(attachment => attachment.name).filter(Boolean).join('\n');
    if (!payload) return;
    await copyTextToClipboard(payload);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1000);
  };
  const rewindUserTurn = (event: MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    void undoLastTurn();
  };
  return (
    <MessagePrimitive.Root className="message-row user">
      <div className="user-message-shell">
        {(text.trim() || attachments.length > 0 || isLatestUserMessage) && (
          <div className="user-message-actions">
            <button type="button" className="user-action-button user-action-copy" title={t('复制消息')} aria-label={t('复制消息')} onClick={event => void copyUserText(event)}>
              {copied ? <ClipboardCheck size={13} /> : <Copy size={13} />}
            </button>
            {isLatestUserMessage && (
              <button type="button" className="user-action-button user-action-rewind" title={t('撤回并编辑')} aria-label={t('撤回并编辑')} onClick={rewindUserTurn}>
                <Undo2 size={14} />
              </button>
            )}
          </div>
        )}
        <div className="message-bubble user-bubble">
          {text && <p>{text}</p>}
          {attachments.length > 0 && <UserAttachmentList attachments={attachments} />}
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// UserAttachmentList
// ---------------------------------------------------------------------------

export function UserAttachmentList({ attachments }: { attachments: ParsedFile[] }) {
  const t = useT();
  return (
    <div className="message-attachment-list" aria-label={t('附件')}>
      {attachments.map(attachment => (
        <article className="message-attachment-card" key={attachment.path || attachment.name}>
          <span className="message-attachment-icon" data-kind={attachment.kind}>
            {attachment.kind === 'image' ? <ImageIcon size={15} /> : <FileText size={15} />}
          </span>
          <span>
            <strong title={attachment.name}>{attachment.name}</strong>
            <small>{attachmentMeta(attachment)}</small>
          </span>
        </article>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SystemMessage
// ---------------------------------------------------------------------------

export function SystemMessage() {
  const text = useAuiState(state => messageText(state.message.content));
  const compactStatus = useChatStore(state => state.compactStatus);
  if (!text) return null;
  if (isContextSummary(text)) {
    return (
      <MessagePrimitive.Root className="message-row system context-summary-row">
        <ContextSummaryCard text={text} compactStatus={compactStatus} />
      </MessagePrimitive.Root>
    );
  }
  if (isCompactBoundary(text)) {
    return <CompactBoundaryDivider text={text} />;
  }
  return (
    <MessagePrimitive.Root className="message-row system">
      <div className="system-pill">{text}</div>
    </MessagePrimitive.Root>
  );
}

function CompactBoundaryDivider({ text }: { text: string }) {
  const t = useT();
  const { summary } = useMemo(() => parseCompactBoundary(text), [text]);
  const [expanded, setExpanded] = useState(false);
  return (
    <MessagePrimitive.Root className="message-row system compact-boundary-row">
      <div className="compact-boundary-divider">
        <span aria-hidden="true" />
        <button type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded}>
          <Sparkles size={13} />
          <strong>{t('已压缩上下文')}</strong>
          <small>{t('此线以上的内容模型不再逐条读取')}</small>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        <span aria-hidden="true" />
        {expanded && summary && (
          <div className="compact-boundary-summary">
            <MarkdownText text={summary} />
          </div>
        )}
      </div>
    </MessagePrimitive.Root>
  );
}

// ---------------------------------------------------------------------------
// ContextSummaryCard
// ---------------------------------------------------------------------------

function ContextSummaryCard({ text, compactStatus }: { text: string; compactStatus: ReturnType<typeof useChatStore.getState>['compactStatus'] }) {
  const t = useT();
  const summary = useMemo(() => parseContextSummary(text), [text]);
  const [visible, setVisible] = useState(true);
  const [collapsed, setCollapsed] = useState(false);
  const countLabel =
    compactStatus?.ok && compactStatus.beforeCount > 0 && compactStatus.afterCount > 0
      ? `${compactStatus.beforeCount} -> ${compactStatus.afterCount} ${t('条')}`
      : '';
  useEffect(() => {
    setVisible(true);
    setCollapsed(false);
    const timer = window.setTimeout(() => setCollapsed(true), 9000);
    return () => window.clearTimeout(timer);
  }, [text]);
  if (!visible) return null;
  if (collapsed) {
    return (
      <span
        className="context-summary-chip"
        role="button"
        tabIndex={0}
        onClick={() => setCollapsed(false)}
        onKeyDown={event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setCollapsed(false);
          }
        }}
      >
        <Sparkles size={13} />
        <span>{t('上下文已整理')}</span>
        {countLabel && <em>{countLabel}</em>}
        <button
          type="button"
          aria-label={t('关闭上下文整理提示')}
          onClick={event => {
            event.stopPropagation();
            setVisible(false);
          }}
        >
          <X size={12} />
        </button>
      </span>
    );
  }
  return (
    <article className="context-summary-card" aria-label={t('上下文已整理')}>
      <header>
        <span>
          <Sparkles size={15} />
        </span>
        <div>
          <strong>{t('上下文已整理')}</strong>
          <small>{summary.meta || t('压缩后的项目摘要')}</small>
        </div>
        {countLabel && <em>{countLabel}</em>}
        <button type="button" className="context-summary-close" aria-label={t('收起上下文整理提示')} onClick={() => setCollapsed(true)}>
          <X size={13} />
        </button>
      </header>
      <MarkdownText text={summary.body} />
    </article>
  );
}
