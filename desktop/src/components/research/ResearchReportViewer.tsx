import {
  AlertTriangle,
  BookOpenText,
  ChevronRight,
  FolderInput,
  ExternalLink,
  FileDown,
  ListTree,
  LoaderCircle,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { exportResearchJob, getResearchJob, registerArtifact } from '../../lib/api';
import { syncDocumentLibraryFromArtifacts, upsertDocumentLibraryItem } from '../../lib/documentLibrary';
import type { ResearchJob, ResearchJobSource } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useSessionStore } from '../../store/sessionStore';
import { useT } from '../../hooks/useT';
import { MarkdownText } from '../chat/threadUtils';

type ReportHeading = {
  id: string;
  level: number;
  number: string;
  text: string;
};

export function ResearchReportViewer() {
  const t = useT();
  const jobId = useUiStore(state => state.activeResearchReportJobId);
  const closeReport = useUiStore(state => state.setResearchReportView);
  const pushToast = useUiStore(state => state.pushToast);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const [job, setJob] = useState<ResearchJob | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [activeHeadingId, setActiveHeadingId] = useState('');
  const [focusedSourceId, setFocusedSourceId] = useState('');
  const exportMenuRef = useRef<HTMLDivElement | null>(null);
  const reportRef = useRef<HTMLDivElement | null>(null);

  const report = useMemo(() => (job ? sanitizeResearchReport(job.report || fallbackResearchReport(job, t)) : ''), [job, t]);
  const headings = useMemo(() => reportHeadings(report, job?.id || 'research'), [job?.id, report]);
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
    setOutlineOpen(false);
    setSourcesOpen(false);
    setExportMenuOpen(false);
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2600);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return undefined;
    void window.metis?.previewSetOccluded?.(true);
    void window.metis?.previewSetLayoutIntent?.({ visible: false, reason: 'research-report-viewer' });
    return undefined;
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
    const root = reportRef.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll<HTMLElement>('h1, h2, h3'));
    nodes.forEach((node, index) => {
      const heading = headings[index];
      if (heading) node.id = heading.id;
    });
    setActiveHeadingId(headings[0]?.id || '');
  }, [headings, report]);

  useEffect(() => {
    if (!exportMenuOpen) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      const root = exportMenuRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) {
        setExportMenuOpen(false);
      }
    };
    window.addEventListener('pointerdown', onPointerDown);
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, [exportMenuOpen]);

  const syncActiveHeading = useCallback(() => {
    const root = reportRef.current;
    if (!root || headings.length === 0) return;
    const rootTop = root.getBoundingClientRect().top;
    let active = headings[0]?.id || '';
    for (const heading of headings) {
      const node = findHeadingNode(root, heading);
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

  const jumpHeading = (heading: ReportHeading) => {
    setActiveHeadingId(heading.id);
    window.requestAnimationFrame(() => {
      const root = reportRef.current;
      const node = root ? findHeadingNode(root, heading) : null;
      if (root && node) {
        const targetTop = node.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 16;
        root.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' });
      }
    });
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

  const saveReportToLibrary = async () => {
    if (!job) return;
    setBusy(true);
    try {
      if (!job.artifact_id && job.report_path) {
        await registerArtifact({
          kind: 'report',
          title: job.title || job.query || job.report_filename || t('研究报告'),
          path: job.report_path,
          mime: 'text/markdown',
          sessionId: activeSessionId || undefined,
          metadata: {
            job_id: job.id,
            research_kind: job.kind,
            query: job.query,
            source_count: sourceCount,
          },
        });
      }
      if (activeSessionId) await syncDocumentLibraryFromArtifacts({ sessionId: activeSessionId, includeUnscoped: false });
      pushToast({
        title: t('已保存到会话文件'),
        description: t('可在 Chat 右上角会话文件中查看。'),
        type: 'success',
      });
    } catch (err) {
      upsertDocumentLibraryItem({
        id: `research:${job.id}`,
        sessionId: activeSessionId || '',
        jobId: job.id,
        kind: 'research_report',
        path: job.report_path || '',
        source: 'research_fallback',
        subtitle: sourceCount ? `${t('Markdown 报告')} · ${sourceCount} ${t('个来源')}` : t('Markdown 报告'),
        title: job.title || job.query || job.report_filename || t('研究报告'),
      });
      pushToast({
        title: t('已保存到本地缓存'),
        description: err instanceof Error ? err.message : t('后端 artifact registry 暂不可用。'),
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
      aria-modal="false"
      aria-label={t('研究报告')}
    >
      <section className="research-report-shell">
        <header className="research-report-topbar">
          <div className="research-report-title">
            <BookOpenText size={16} />
            <div>
              <strong>{job?.title || job?.query || t('研究报告')}</strong>
              <span>
                {researchKindLabel(job?.kind || 'research', t)}{sourceCount ? ` · ${sourceCount} ${t('个来源')}` : ''}
                {isRunning ? ` · ${t('生成中')}` : ''}
              </span>
            </div>
          </div>
          <div className="research-report-topbar-actions">
            <button type="button" data-active={outlineOpen} disabled={!job} onClick={() => setOutlineOpen(value => !value)}>
              <ListTree size={13} />
              {t('目录')}
            </button>
            <button type="button" data-active={sourcesOpen} disabled={!job || sourceCount === 0} onClick={() => setSourcesOpen(value => !value)}>
              <ExternalLink size={13} />
              {t('来源')}
            </button>
            <div className="research-report-export-wrap" ref={exportMenuRef}>
              <button
                type="button"
                data-active={exportMenuOpen}
                disabled={!job || busy}
                onClick={() => setExportMenuOpen(value => !value)}
              >
                <FileDown size={13} />
                {t('分享和导出')}
                <ChevronRight className="research-report-export-chevron disclosure-chevron" data-open={exportMenuOpen} size={12} />
              </button>
              <div className="research-report-export-menu" data-open={exportMenuOpen} role="menu" aria-hidden={!exportMenuOpen}>
                <button
                  type="button"
                  role="menuitem"
                  disabled={!job || busy}
                  onClick={() => {
                    setExportMenuOpen(false);
                    void downloadReport();
                  }}
                >
                  <FileDown size={13} />
                  {t('导出为 Markdown')}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={!job}
                  onClick={() => {
                    setExportMenuOpen(false);
                    void saveReportToLibrary();
                  }}
                >
                  <FolderInput size={13} />
                  {t('保存到会话文件')}
                </button>
              </div>
            </div>
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
          <div className="research-report-layout" data-outline={outlineOpen} data-sources={sourcesOpen}>
            {outlineOpen && (
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
                      onClick={() => jumpHeading(heading)}
                    >
                      <span>{heading.number}</span>
                      <em>{heading.text}</em>
                    </button>
                  )) : (
                    <small>{t('暂无目录')}</small>
                  )}
                </nav>
              </aside>
            )}

            <main className="research-report-document" ref={reportRef} onScroll={syncActiveHeading}>
              <MarkdownText text={report} />
            </main>

            {sourcesOpen && (
            <aside className="research-report-sources" data-open={sourcesOpen}>
              <div className="research-report-source-toggle">
                <span>{t('来源')}</span>
                <em>{sourceCount}</em>
              </div>
              {sourcesOpen && (
                <div className="research-report-source-list">
                  {job.sources.length > 0 ? job.sources.map((source, index) => {
                    const sourceId = researchSourceId(source, index);
                    const sourceUrl = researchSourceUrl(source);
                    const sourceLabel = researchSourceLinkLabel(source, t);
                    return (
                      <button
                        type="button"
                        className="research-report-source-item"
                        data-focus={focusedSourceId === sourceId}
                        id={sourceDomId(job.id, sourceId)}
                        key={`${sourceId}-${source.url || source.title || ''}`}
                        onClick={() => sourceUrl ? void window.metis?.openExternal?.(sourceUrl) : undefined}
                      >
                        <div>
                          <ResearchSourceLogo source={source} />
                          <span title={sourceUrl || researchSourceTitle(source, t)}>{sourceLabel}</span>
                        </div>
                      </button>
                    );
                  }) : (
                    <small>{t('暂无来源')}</small>
                  )}
                </div>
              )}
            </aside>
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}

function reportHeadings(report: string, jobId: string): ReportHeading[] {
  const rows: ReportHeading[] = [];
  const seen = new Map<string, number>();
  const counters = [0, 0, 0];
  for (const match of String(report || '').matchAll(/^(#{1,3})\s+(.+)$/gm)) {
    const level = match[1]?.length || 1;
    const text = stripMarkdownInline(match[2] || '').trim();
    if (!text) continue;
    if (isResearchNoiseHeading(text) || isResearchNoiseLine(text)) continue;
    counters[level - 1] += 1;
    for (let index = level; index < counters.length; index += 1) counters[index] = 0;
    const number = counters.slice(0, level).filter(value => value > 0).join('.');
    const slug = safeDomFragment(`${jobId}-${text.toLowerCase()}`);
    const count = seen.get(slug) || 0;
    seen.set(slug, count + 1);
    rows.push({ id: `research-report-heading-${slug}-${count + 1}`, level, number, text });
  }
  return rows.slice(0, 24);
}

function findHeadingNode(root: HTMLElement, heading: ReportHeading): HTMLElement | null {
  const byId = root.querySelector<HTMLElement>(`#${cssEscape(heading.id)}`);
  if (byId) return byId;
  const nodes = Array.from(root.querySelectorAll<HTMLElement>('h1, h2, h3'));
  const byText = nodes.find(node => stripMarkdownInline(node.textContent || '') === heading.text);
  if (byText) byText.id = heading.id;
  return byText || null;
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

function sanitizeResearchReport(value: string): string {
  const lines = String(value || '')
    .replace(/\[\s*\.{3}\s*truncated\s+\d+\s+chars\s*\.{3}\s*\]/gi, '')
    .split(/\r?\n/);
  const output: string[] = [];
  let skipLevel = 0;
  for (const line of lines) {
    const heading = line.trim().match(/^(#{1,6})\s+(.+)$/);
    if (heading && skipLevel > 0 && heading[1].length <= skipLevel) {
      skipLevel = 0;
    }
    if (skipLevel > 0) continue;
    if (heading && isResearchNoiseHeading(heading[2])) {
      skipLevel = heading[1].length;
      continue;
    }
    if (isResearchNoiseLine(line)) continue;
    output.push(line);
  }
  return output.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function isResearchNoiseHeading(value: string): boolean {
  const text = String(value || '').trim();
  return /^(目录|目錄|table\s+of\s+contents|contents|toc|report|evidence\s+opened|raw\s+evidence)$/i.test(text) ||
    /read\s+failures|读取失败|抓取失败|连接错误|错误来源|failed\s+reads?/i.test(text);
}

function isResearchNoiseLine(value: string): boolean {
  const text = String(value || '');
  return [
    /^\s*(?:On This Page|In this article|Table of contents|Back to Blog|Sign In|Log In|Subscribe|Newsletter|Previous|Next|Top)\s*$/i,
    /^\s*(?:Jump to content|Main menu|Navigation|Contribute|Personal tools|Appearance|Search|Share|Copy link|Mail)\s*$/i,
    /^\s*(?:Get API key|Cookbook|Community|Docs|API reference|Overview|Get Started|Pricing|Coding agent setup)\s*$/i,
    /^\s*(?:Use your Google Account|Email or phone|Forgot email\?|Not your computer\?|Create account|Next)\s*$/i,
    /^\s*\d+\s+sections?\s*$/i,
    /^\s*(?:x|go|open|read|more)\s*$/i,
    /^\s*(?:首页\s*[»>]|Tags?:|标签[:：])/i,
    /^[^\n]{0,120}(?:Gemini|Claude|OpenAI|Google|谷歌|模型|教程|创作|新闻)[^\n]{0,120}[：:]\s*(?:Go|Open|Read|More)\s*$/i,
    /^\s*[-*]?\s*(?:Status|Provider|Providers|Query|Search query|Report status|Chat output policy|Search results|Evidence pages opened|Read failures|Partial evidence|URL)\s*[:：]/i,
    /\[\s*\.{3}\s*truncated\s+\d+\s+chars\s*\.{3}\s*\]/i,
    /Afrikaans.+English.+Español.+Français/i,
    /\[[^\]]{2,80}\]\([^)]+\)\s*\[[^\]]{2,80}\]\([^)]+\)/,
    /MCP ServersMCP Servers|Agent SkillsAgent Skills|DocumentationDocumentation/i,
    /\bConnection(?:Aborted)?Error\b/i,
    /\bHTTPS?ConnectionPool\b/i,
    /\bMax retries exceeded\b/i,
    /\bSSLError\b/i,
    /\bUNEXPECTED_EOF_WHILE_READING\b/i,
    /\bCERTIFICATE_VERIFY_FAILED\b/i,
    /页面读取失败/,
    /你的主机中的软件中止了一个已建立的连接/,
    /会员\s*充值/,
    /客服\s*微信/,
    /微信号/,
    /请添加客服/,
    /代充/,
    /出售\s*账号/,
    /账号\s*出售/,
    /gpthuiyuan/i,
  ].some(pattern => pattern.test(text));
}

function ResearchSourceLogo({ source }: { source: ResearchJobSource }) {
  const [iconIndex, setIconIndex] = useState(0);
  const [failed, setFailed] = useState(false);
  const host = researchSourceFaviconHost(source);
  const candidates = sourceFaviconCandidates(host);
  const faviconUrl = !failed && candidates.length > 0 ? candidates[Math.min(iconIndex, candidates.length - 1)] : '';
  const brand = sourceLogoBrand(host);
  return (
    <span className="research-source-logo" aria-hidden="true" style={{ '--source-logo-hue': sourceLogoHue(host) } as CSSProperties}>
      {faviconUrl && !failed ? (
        <img
          src={faviconUrl}
          alt=""
          loading="lazy"
          onError={() => {
            if (iconIndex < candidates.length - 1) setIconIndex(value => value + 1);
            else setFailed(true);
          }}
        />
      ) : (
        <span className="research-source-logo-fallback" data-brand={brand} />
      )}
    </span>
  );
}

function sourceFaviconCandidates(host: string): string[] {
  const value = cleanReadableDomain(host);
  if (!value) return [];
  const encoded = encodeURIComponent(value);
  return [
    `https://icons.duckduckgo.com/ip3/${value}.ico`,
    `https://www.google.com/s2/favicons?domain=${encoded}&sz=64`,
    `https://${value}/favicon.ico`,
  ];
}

function sourceLogoBrand(host: string): string {
  const value = String(host || '').toLowerCase();
  if (/google|gemini|deepmind/.test(value)) return 'google';
  if (/github/.test(value)) return 'github';
  if (/youtube/.test(value)) return 'youtube';
  return 'generic';
}

function sourceLogoHue(host: string): number {
  const value = String(host || 'source');
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) % 360;
  return hash;
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
  const url = researchSourceUrl(source);
  return researchHost(url) || cleanReadableDomain(source.domain || '') || url || t('来源');
}

function researchSourceDomain(source: ResearchJobSource, t: (text: string) => string): string {
  return cleanReadableDomain(researchHost(researchSourceUrl(source)) || source.domain || researchSourceTitle(source, t));
}

function researchSourceLinkLabel(source: ResearchJobSource, t: (text: string) => string): string {
  const url = researchSourceUrl(source);
  if (!url) return researchSourceDomain(source, t) || researchSourceTitle(source, t);
  try {
    const parsed = new URL(url);
    const host = cleanReadableDomain(parsed.hostname);
    const path = `${parsed.pathname || ''}${parsed.search || ''}`.replace(/\/$/, '');
    return `${host}${path || ''}` || url;
  } catch {
    return url.replace(/^https?:\/\//i, '').replace(/^www\./i, '') || researchSourceTitle(source, t);
  }
}

function researchSourceUrl(source: ResearchJobSource): string {
  return unwrapReaderUrl(String(source.url || '').trim());
}

function researchSourceFaviconHost(source: ResearchJobSource): string {
  return cleanReadableDomain(researchHost(researchSourceUrl(source)) || source.domain || '');
}

function cleanReadableDomain(value: string): string {
  const domain = String(value || '').replace(/^www\./i, '').trim();
  return /r\.jina\.ai/i.test(domain) ? '' : domain;
}

function unwrapReaderUrl(value: string): string {
  let current = String(value || '').trim();
  for (let index = 0; index < 4; index += 1) {
    const parsed = safeUrl(current);
    if (!parsed || parsed.hostname !== 'r.jina.ai') break;
    let next = decodeURIComponent(parsed.pathname.replace(/^\/+/, ''));
    next = next.replace(/^https?:\/\/(https?:\/\/)/i, '$1');
    if (!/^https?:\/\//i.test(next)) next = next.replace(/^(https?:)\/+/i, '$1//');
    if (!/^https?:\/\//i.test(next) || next === current) break;
    current = next;
  }
  return current;
}

function safeUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function cssEscape(value: string): string {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') return CSS.escape(value);
  return value.replace(/["\\]/g, '\\$&');
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
