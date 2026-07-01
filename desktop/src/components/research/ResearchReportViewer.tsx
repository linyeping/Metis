import {
  AlertTriangle,
  BookOpenText,
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  FileDown,
  ListTree,
  LoaderCircle,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { exportResearchJob, getResearchJob } from '../../lib/api';
import type { ResearchJob, ResearchJobSource } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import { copyTextToClipboard, MarkdownText } from '../chat/threadUtils';

type ReportHeading = {
  id: string;
  level: number;
  text: string;
};

type CitationSource = {
  id: string;
  index: number;
  source: ResearchJobSource;
};

export function ResearchReportViewer() {
  const t = useT();
  const jobId = useUiStore(state => state.activeResearchReportJobId);
  const closeReport = useUiStore(state => state.setResearchReportView);
  const pushToast = useUiStore(state => state.pushToast);
  const [job, setJob] = useState<ResearchJob | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [activeHeadingId, setActiveHeadingId] = useState('');
  const [expandedSourceIds, setExpandedSourceIds] = useState<Set<string>>(() => new Set());
  const [focusedSourceId, setFocusedSourceId] = useState('');
  const reportRef = useRef<HTMLDivElement | null>(null);

  const report = useMemo(() => (job ? (job.report || fallbackResearchReport(job, t)) : ''), [job, t]);
  const headings = useMemo(() => reportHeadings(report, job?.id || 'research'), [job?.id, report]);
  const citations = useMemo(() => (job ? researchCitationSources(job, report) : []), [job, report]);
  const sourceCount = job?.sources?.length || 0;
  const isRunning = job?.status === 'running' || job?.status === 'queued';

  const close = useCallback(() => closeReport(''), [closeReport]);

  useEffect(() => {
    if (!jobId) return undefined;
    let disposed = false;

    const refresh = async () => {
      try {
        const next = await getResearchJob(jobId);
        if (disposed) return;
        setJob(next);
        setError('');
      } catch (err) {
        if (disposed) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    };

    setJob(null);
    setError('');
    setFocusedSourceId('');
    setExpandedSourceIds(new Set());
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2600);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [close, jobId]);

  useEffect(() => {
    if (!reportRef.current || headings.length === 0) return;
    const nodes = Array.from(reportRef.current.querySelectorAll<HTMLElement>('h1, h2, h3'));
    nodes.forEach((node, index) => {
      const heading = headings[index];
      if (heading) node.id = heading.id;
    });
    setActiveHeadingId(headings[0]?.id || '');
  }, [headings, report]);

  const syncActiveHeading = useCallback(() => {
    const root = reportRef.current;
    if (!root || headings.length === 0) return;
    const rootTop = root.getBoundingClientRect().top;
    let active = headings[0]?.id || '';
    for (const heading of headings) {
      const node = document.getElementById(heading.id);
      if (!node) continue;
      if (node.getBoundingClientRect().top - rootTop <= 74) active = heading.id;
      else break;
    }
    setActiveHeadingId(current => (current === active ? current : active));
  }, [headings]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(syncActiveHeading);
    return () => window.cancelAnimationFrame(frame);
  }, [report, syncActiveHeading]);

  const jumpHeading = (id: string) => {
    setActiveHeadingId(id);
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const jumpSource = (sourceId: string) => {
    setSourcesOpen(true);
    setFocusedSourceId(sourceId);
    setExpandedSourceIds(current => new Set(current).add(sourceId));
    window.requestAnimationFrame(() => {
      document.getElementById(sourceDomId(jobId, sourceId))?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  };

  const setSourceExpanded = (sourceId: string, expanded: boolean) => {
    setExpandedSourceIds(current => {
      const next = new Set(current);
      if (expanded) next.add(sourceId);
      else next.delete(sourceId);
      return next;
    });
  };

  const copyReport = async () => {
    if (!report.trim()) return;
    setBusy(true);
    try {
      await copyTextToClipboard(report);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } finally {
      setBusy(false);
    }
  };

  const downloadReport = async () => {
    if (!job) return;
    setBusy(true);
    try {
      const content = await exportResearchJob(job.id, 'markdown');
      downloadText(content || report, `${safeFilename(job.title || job.query || job.id || 'research-report')}.md`);
    } catch (err) {
      pushToast({
        title: t('导出研究报告失败'),
        description: err instanceof Error ? err.message : String(err),
        type: 'warning',
      });
    } finally {
      setBusy(false);
    }
  };

  if (!jobId) return null;

  return (
    <div
      className="research-report-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={t('研究报告')}
      onMouseDown={event => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <section className="research-report-shell">
        <header className="research-report-topbar">
          <div>
            <BookOpenText size={16} />
            <span>{researchKindLabel(job?.kind || 'research', t)}</span>
            {isRunning && (
              <em>
                <LoaderCircle className="spin" size={12} />
                {t('生成中')}
              </em>
            )}
          </div>
          <strong>{job?.title || job?.query || t('研究报告')}</strong>
          <div className="research-report-topbar-actions">
            <button type="button" disabled={!job || busy} onClick={() => void copyReport()}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? t('已复制') : t('复制')}
            </button>
            <button type="button" disabled={!job || busy} onClick={() => void downloadReport()}>
              <FileDown size={13} />
              {t('导出')}
            </button>
            <button type="button" className="research-report-close" title={t('关闭')} onClick={close}>
              <X size={14} />
            </button>
          </div>
        </header>

        {error && (
          <p className="research-report-error">
            <AlertTriangle size={14} />
            {error}
          </p>
        )}

        {!job && !error ? (
          <div className="research-report-loading">
            <LoaderCircle className="spin" size={18} />
            <span>{t('正在打开研究报告')}</span>
          </div>
        ) : job ? (
          <div className="research-report-layout">
            <aside className="research-report-outline">
              <div className="research-report-side-title">
                <ListTree size={13} />
                <span>{t('目录')}</span>
              </div>
              <nav>
                {headings.length > 0 ? headings.map(heading => (
                  <button
                    type="button"
                    data-active={activeHeadingId === heading.id}
                    data-level={heading.level}
                    key={heading.id}
                    onClick={() => jumpHeading(heading.id)}
                  >
                    {heading.text}
                  </button>
                )) : (
                  <small>{t('暂无目录')}</small>
                )}
              </nav>
            </aside>

            <main className="research-report-document" ref={reportRef} onScroll={syncActiveHeading}>
              {citations.length > 0 && (
                <div className="research-report-citations" aria-label={t('来源引用')}>
                  <span>{t('引用')}</span>
                  {citations.map(row => (
                    <button
                      type="button"
                      key={`${row.id}-${row.source.url || row.source.title || ''}`}
                      title={researchSourceTitle(row.source, t)}
                      onClick={() => jumpSource(row.id)}
                    >
                      [{row.source.rank || row.index + 1}] {row.source.domain || researchHost(row.source.url || '') || researchSourceTitle(row.source, t)}
                    </button>
                  ))}
                </div>
              )}
              <MarkdownText text={report} />
            </main>

            <aside className="research-report-sources" data-open={sourcesOpen}>
              <button className="research-report-source-toggle" type="button" onClick={() => setSourcesOpen(value => !value)}>
                <ChevronDown size={13} />
                <span>{t('来源')}</span>
                <em>{sourceCount}</em>
              </button>
              {sourcesOpen && (
                <div className="research-report-source-list">
                  {job.sources.length > 0 ? job.sources.map((source, index) => {
                    const sourceId = researchSourceId(source, index);
                    return (
                      <details
                        className="research-report-source-item"
                        data-focus={focusedSourceId === sourceId}
                        id={sourceDomId(job.id, sourceId)}
                        key={`${sourceId}-${source.url || source.title || ''}`}
                        open={expandedSourceIds.has(sourceId)}
                        onToggle={event => setSourceExpanded(sourceId, event.currentTarget.open)}
                      >
                        <summary>
                          <ResearchSourceLogo source={source} />
                          <strong title={researchSourceTitle(source, t)}>{researchSourceTitle(source, t)}</strong>
                          <em>{source.rank || index + 1}</em>
                        </summary>
                        <div>
                          {source.snippet && <p>{source.snippet}</p>}
                          {source.error && <code>{source.error}</code>}
                          {source.url && (
                            <button type="button" onClick={() => void window.metis?.openExternal?.(source.url || '')}>
                              <ExternalLink size={12} />
                              {t('默认浏览器打开')}
                            </button>
                          )}
                        </div>
                      </details>
                    );
                  }) : (
                    <small>{t('暂无来源')}</small>
                  )}
                </div>
              )}
            </aside>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function reportHeadings(report: string, jobId: string): ReportHeading[] {
  const rows: ReportHeading[] = [];
  const seen = new Map<string, number>();
  for (const match of String(report || '').matchAll(/^(#{1,3})\s+(.+)$/gm)) {
    const level = match[1]?.length || 1;
    const text = stripMarkdownInline(match[2] || '').trim();
    if (!text) continue;
    const slug = safeDomFragment(`${jobId}-${text.toLowerCase()}`);
    const count = seen.get(slug) || 0;
    seen.set(slug, count + 1);
    rows.push({ id: `research-report-heading-${slug}-${count + 1}`, level, text });
  }
  return rows.slice(0, 24);
}

function stripMarkdownInline(value: string): string {
  return String(value || '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/#+$/g, '')
    .trim();
}

function researchCitationSources(job: ResearchJob, report: string): CitationSource[] {
  const rows = (job.sources || []).map((source, index) => ({ id: researchSourceId(source, index), index, source }));
  const text = String(report || '');
  const cited = rows.filter(row => {
    const url = row.source.url || '';
    const title = row.source.title || '';
    const rank = row.source.rank || row.index + 1;
    return Boolean(
      (url && text.includes(url)) ||
      (title && text.includes(title)) ||
      new RegExp(`\\[${rank}\\]|source\\s*${rank}`, 'i').test(text),
    );
  });
  return (cited.length > 0 ? cited : rows.filter(row => Boolean(row.source.url))).slice(0, 16);
}

function fallbackResearchReport(job: ResearchJob, t: (text: string) => string): string {
  const lines = [`# ${job.title || job.query || t('研究报告')}`, ''];
  if (job.query) lines.push(`## ${t('问题')}`, '', job.query, '');
  if (job.evidence?.length) {
    lines.push(`## ${t('证据')}`, '');
    for (const item of job.evidence.slice(0, 10)) {
      lines.push(`### ${item.title || item.url || t('证据')}`);
      const text = item.text || item.snippet || '';
      if (text) lines.push('', text.slice(0, 1200));
      lines.push('');
    }
  }
  if (job.sources?.length) {
    lines.push(`## ${t('来源')}`, '');
    for (const source of job.sources.slice(0, 24)) {
      const label = researchSourceTitle(source, t);
      lines.push(source.url ? `- [${label}](${source.url})` : `- ${label}`);
    }
  }
  return lines.join('\n').trim() || t('暂无报告内容');
}

function ResearchSourceLogo({ source }: { source: ResearchJobSource }) {
  const host = source.domain || researchHost(source.url || '');
  const initial = host.replace(/^www\./i, '').charAt(0).toUpperCase();
  return (
    <span className="research-source-logo" aria-hidden="true">
      <span>{initial || '?'}</span>
    </span>
  );
}

function researchKindLabel(kind: string, t: (text: string) => string): string {
  if (kind === 'search') return t('搜索结果');
  if (kind === 'fetch_content') return t('来源读取');
  return t('研究报告');
}

function researchSourceId(source: ResearchJobSource, index: number): string {
  return String(source.id || `s${index + 1}`);
}

function sourceDomId(jobId: string, sourceId: string): string {
  return `research-report-source-${safeDomFragment(jobId)}-${safeDomFragment(sourceId)}`;
}

function researchHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

function researchSourceTitle(source: ResearchJobSource, t: (text: string) => string): string {
  const title = String(source.title || '').trim();
  if (title && !/^\(?untitled\)?$/i.test(title) && !/r\.jina\.ai/i.test(title)) return title;
  return source.domain || researchHost(source.url || '') || source.url || t('来源');
}

function safeDomFragment(value: string): string {
  return String(value || '').replace(/[^A-Za-z0-9_-]+/g, '_');
}

function safeFilename(value: string): string {
  return String(value || 'research-report')
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80) || 'research-report';
}

function downloadText(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
