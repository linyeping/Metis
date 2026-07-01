/**
 * threadUtils — MetisThread 拆分出的纯工具函数和小型共享组件。
 *
 * 这些函数/组件被 MessageBubble、ToolCallBlock、FileChangeReviewCard
 * 等多个子模块共享，放在此处避免循环依赖。
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkMetisLinks } from '../../lib/remarkMetisLinks';
import {
  AlertTriangle,
  Atom,
  Brush,
  Calculator,
  CheckCircle2,
  ClipboardCheck,
  Database,
  FilePlus2,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  Globe2,
  Image as ImageIcon,
  Link2,
  LoaderCircle,
  Mail,
  Palette,
  PencilLine,
  Play,
  Presentation,
  Search,
  SquareTerminal,
  Table2,
  Trash2,
  Video,
  Wrench,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { createElement } from 'react';
import type { ParsedFile } from '../../lib/types';

// ---------------------------------------------------------------------------
// Markdown rendering
// ---------------------------------------------------------------------------

/** Flatten headings so agent output stays visually proportional.
 *  h1→h3, h2→h4, h3/h4/h5/h6→h5 — all styled uniformly in CSS. */
export const flattenedHeadings = {
  h1: (props: any) => createElement('h3', props),
  h2: (props: any) => createElement('h4', props),
  h3: (props: any) => createElement('h5', props),
  h4: (props: any) => createElement('h5', props),
  h5: (props: any) => createElement('h6', props),
  h6: (props: any) => createElement('h6', props),
};

function safeUrlTransform(url: string): string {
  const value = String(url || '');
  // 保留 metis-file: 协议（react-markdown 默认会清洗成空 → 空 href 点击会整页 reload，FABLEADV-30）。
  if (value.startsWith('metis-file:')) return value;
  const lower = value.trim().toLowerCase();
  if (lower.startsWith('javascript:') || lower.startsWith('data:') || lower.startsWith('vbscript:')) return '';
  return value;
}

export function MarkdownText({ text = '' }: { text?: string }) {
  return createElement(
    'div',
    { className: 'markdown-body' },
    createElement(
      ReactMarkdown,
      { remarkPlugins: [remarkGfm, remarkMetisLinks], components: flattenedHeadings, urlTransform: safeUrlTransform },
      text,
    ),
  );
}

// ---------------------------------------------------------------------------
// ChangeCount — shared across FileChangeReviewCard, ToolCard, ToolActivityGroup
// ---------------------------------------------------------------------------

export function ChangeCount({ type, value }: { type: 'add' | 'remove'; value: number }) {
  const text = `${type === 'add' ? '+' : '-'}${value}`;
  return type === 'add'
    ? createElement('b', { className: 'live-change-count', key: text }, text)
    : createElement('i', { className: 'live-change-count', key: text }, text);
}

// ---------------------------------------------------------------------------
// Clipboard
// ---------------------------------------------------------------------------

export async function copyTextToClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Fallback: legacy execCommand
    const area = document.createElement('textarea');
    area.value = text;
    area.style.cssText = 'position:fixed;left:-9999px;top:-9999px;';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
    } catch {
      /* ignore */
    }
    document.body.removeChild(area);
  }
}

// ---------------------------------------------------------------------------
// Path / time formatting
// ---------------------------------------------------------------------------

export function compactPath(value: string): string {
  const parts = value.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 3) return value;
  return `.../${parts.slice(-3).join('/')}`;
}

export function formatNoticeTime(createdAt: number): string {
  if (!createdAt) return '刚刚';
  const seconds = Math.max(0, Math.round((Date.now() - createdAt) / 1000));
  if (seconds < 60) return '刚刚';
  const minutes = Math.round(seconds / 60);
  return `${minutes} 分钟前`;
}

// ---------------------------------------------------------------------------
// Tool display helpers
// ---------------------------------------------------------------------------

export function formatTool(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value ?? '');
  }
}

export function compact(value: unknown): string {
  const text = formatTool(value).replace(/\s+/g, ' ').trim();
  return text.length > 160 ? `${text.slice(0, 160)}...` : text || 'No details';
}

export function toolStatusIcon(toolName: string, status: string) {
  if (status === 'running' && toolName === 'web_research') {
    return createElement(Atom, { className: 'atom-orbit-spin', size: 14 });
  }
  if (status === 'running') return createElement(LoaderCircle, { size: 13 });
  if (status === 'error') return createElement(AlertTriangle, { size: 13 });
  if (status === 'waiting_approval') return createElement(ClipboardCheck, { size: 13 });
  return createElement(CheckCircle2, { size: 13 });
}

export function toolDisplayName(name: string): string {
  const normalized = name.replace(/^web_/, '').replace(/^browser_/, '').replace(/_/g, ' ').trim();
  return normalized ? sentenceCaseSummaryMeta(normalized) : name;
}

export function toolKindGlyph(name: string) {
  const normalized = String(name || '').trim().toLowerCase();
  if (!normalized) return toolKindIcon(Wrench);

  if (/(^|_)(read_file|read_multiple_files|read_file_chunk|get_workspace_file|open_file)(_|$)/.test(normalized)) return toolKindIcon(FolderOpen);
  if (/(^|_)(list_directory|glob_search)(_|$)/.test(normalized)) return toolKindIcon(Folder);
  if (/(^|_)(write_file|append_to_file|create_file)(_|$)/.test(normalized)) return toolKindIcon(FilePlus2);
  if (/(edit|replace|patch|rename|refactor|undo_edit)/.test(normalized)) return toolKindIcon(PencilLine);
  if (/(delete_file|delete_directory|remove)/.test(normalized)) return toolKindIcon(Trash2);
  if (/(execute|bash|shell|powershell|terminal|cmd)/.test(normalized)) return toolKindIcon(SquareTerminal);
  if (/(run_|_run$|verify_compilation|test_runner|office_report_from_code_run|desktop_action)/.test(normalized)) return toolKindIcon(Play);
  if (/(browse|open_url|web_fetch|preview_browser|browser_)/.test(normalized)) return toolKindIcon(Globe2);
  if (/(web_search|research|search|grep_search|semantic_search)/.test(normalized)) return toolKindIcon(Search);
  if (/(twitter|x_search)/.test(normalized)) return toolKindIcon(X);
  if (/(screenshot|image_view|view_image|image_read)/.test(normalized)) return toolKindIcon(ImageIcon);
  if (/(generate_image|image_generation|imagegen)/.test(normalized)) return toolKindIcon(Palette);
  if (/(edit_image|image_edit)/.test(normalized)) return toolKindIcon(Brush);
  if (/(video)/.test(normalized)) return toolKindIcon(Video);
  if (/(pdf)/.test(normalized)) return toolKindIcon(FileText, 'PDF');
  if (/(docx|word)/.test(normalized)) return toolKindIcon(FileText, 'DOC');
  if (/(xlsx|excel|sheet|csv|tsv)/.test(normalized)) return toolKindIcon(Table2);
  if (/(ppt|slides|presentation)/.test(normalized)) return toolKindIcon(Presentation);
  if (/(git)/.test(normalized)) return toolKindIcon(GitBranch);
  if (/(sql|database|db_query|sqlite|postgres|mysql)/.test(normalized)) return toolKindIcon(Database);
  if (/(api|request|http)/.test(normalized)) return toolKindIcon(Link2);
  if (/(math|calc|calculate)/.test(normalized)) return toolKindIcon(Calculator);
  if (/(mail|email|gmail)/.test(normalized)) return toolKindIcon(Mail);
  return toolKindIcon(Wrench);
}

function toolKindIcon(Icon: LucideIcon, badge = '') {
  return createElement(
    'span',
    { className: 'tool-kind-mark', 'data-badge': badge || undefined },
    createElement(Icon, { size: 12, strokeWidth: 1.9 }),
  );
}

export function toolCommandPreview(toolName: string, args: unknown, result: unknown): string {
  const name = toolName.toLowerCase();
  if (name === 'office_report_from_code_run') {
    const command = firstStringField(args, ['command', 'script_path']);
    return command ? `$ ${command}` : '';
  }
  if (!/(shell|terminal|cmd|bash|powershell|execute|run|command)/.test(name)) return '';
  const command = firstStringField(args, ['command', 'cmd', 'shell_command', 'script', 'code']);
  if (command) return `$ ${command}`;
  const resultCommand = firstStringField(result, ['command', 'cmd', 'shell_command']);
  return resultCommand ? `$ ${resultCommand}` : '';
}

export function toolProgressText(toolName: string, status: string): string {
  if (toolName === 'web_research' && status === 'running') return '正在深度研究...';
  const name = toolDisplayName(toolName);
  if (status === 'running') return `正在执行 ${name}`;
  if (status === 'waiting_approval') return `${name} 等待确认`;
  if (status === 'error') return `${name} 执行失败，展开查看详情`;
  return `${name} 已完成`;
}

export function firstStringField(value: unknown, keys: string[]): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
  const row = value as Record<string, unknown>;
  for (const key of keys) {
    const field = row[key];
    if (typeof field === 'string' && field.trim()) return field.trim();
  }
  return '';
}

export function isToolError(value: unknown): boolean {
  const text = formatTool(value).trim();
  const head = text.slice(0, 240).toLowerCase();
  return (
    text.startsWith('❌') ||
    /^error\b/i.test(text) ||
    head.includes('traceback') ||
    head.includes('exception') ||
    head.includes('permission denied') ||
    head.includes('access denied')
  );
}

export function elapsedText(startedAt?: number, finishedAt?: number): string {
  if (!startedAt || !finishedAt || finishedAt < startedAt) return '';
  const ms = finishedAt - startedAt;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// ---------------------------------------------------------------------------
// Message content extraction
// ---------------------------------------------------------------------------

export function messageText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  return content
    .map(part => {
      if (typeof part === 'string') return part;
      if (!part || typeof part !== 'object') return '';
      const row = part as { text?: unknown; type?: unknown };
      return (!row.type || row.type === 'text') && typeof row.text === 'string' ? row.text : '';
    })
    .join('');
}

export function messageAttachments(metadata: unknown): ParsedFile[] {
  const custom = metadata && typeof metadata === 'object' ? (metadata as { custom?: { attachments?: unknown } }).custom : null;
  return Array.isArray(custom?.attachments) ? (custom.attachments as ParsedFile[]) : [];
}

export function attachmentMeta(attachment: ParsedFile): string {
  const kind = attachment.extension || attachment.mime || attachment.kind;
  const size = formatAttachmentBytes(attachment.size);
  return `${kind}${size ? ` · ${size}` : ''}${attachment.truncated ? ' · 已截断' : ''}`;
}

export function formatAttachmentBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// Context summary detection
// ---------------------------------------------------------------------------

export function isContextSummary(text: string): boolean {
  return /^\s*\[Context Summary\b/i.test(text);
}

export function parseContextSummary(text: string): { meta: string; body: string } {
  const trimmed = text.trim();
  const match = trimmed.match(/^\[Context Summary(?:\s*-\s*([^\]]+))?\]\s*/i);
  const body = match ? trimmed.slice(match[0].length).trim() : trimmed;
  return {
    meta: sentenceCaseSummaryMeta(match?.[1] || ''),
    body: body || '关键上下文已经压缩整理，可继续当前任务。',
  };
}

export function sentenceCaseSummaryMeta(value: string): string {
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}
