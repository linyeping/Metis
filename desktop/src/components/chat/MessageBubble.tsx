/**
 * MessageBubble — 消息渲染组件。
 *
 * 从 MetisThread.tsx 拆分：AssistantMessage、UserMessage、
 * UserAttachmentList、SystemMessage、ContextSummaryCard。
 */
import {
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
  RotateCcw,
  Sparkles,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import type { MouseEvent } from 'react';
import { useChatStore } from '../../store/chatStore';
import { useUiStore } from '../../store/uiStore';
import { apiBase, getWorkspaceFile } from '../../lib/api';
import { isCompactBoundary, parseCompactBoundary } from '../../lib/compactBoundary';
import { chatLinkActionFromHref } from '../../lib/metisLinks';
import type { ParsedFile } from '../../lib/types';
import { isPreviewableWebFilePath, localFilePreviewUrl } from '../../lib/webPreview';
import { useT } from '../../hooks/useT';
import { FileChangeReviewCard, summarizeMessageFileChanges } from './FileChangeReviewCard';
import { ToolActivityGroup, ToolCard } from './ToolCallBlock';
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

export function AssistantMessage() {
  const t = useT();
  const status = useAuiState(state => state.message.status?.type);
  const messageId = useAuiState(state => state.message.id);
  const content = useAuiState(state => state.message.content);
  const text = useAuiState(state => messageText(state.message.content));
  const setWebPreviewUrl = useUiStore(state => state.setWebPreviewUrl);
  const setPreviewPath = useUiStore(state => state.setPreviewPath);
  const pushToast = useUiStore(state => state.pushToast);
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const [copied, setCopied] = useState(false);
  const fileChangeSummary = useMemo(() => summarizeMessageFileChanges(messageId, content), [content, messageId]);
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
      <div className="message-bubble assistant-bubble">
        <MessagePrimitive.Parts
          unstable_showEmptyOnNonTextEnd={false}
          components={{
            Text: MarkdownText,
            ToolGroup: ToolActivityGroup,
            tools: { Fallback: ToolCard },
          }}
        />
        {fileChangeSummary && <FileChangeReviewCard summary={fileChangeSummary} />}
        {text.trim() && (
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

// ---------------------------------------------------------------------------
// UserMessage
// ---------------------------------------------------------------------------

export function UserMessage() {
  const t = useT();
  const messageId = useAuiState(state => state.message.id);
  const text = useAuiState(state => messageText(state.message.content));
  const attachments = useAuiState(state => messageAttachments(state.message.metadata));
  const rewindToMessage = useChatStore(state => state.rewindToMessage);
  return (
    <MessagePrimitive.Root className="message-row user">
      <div className="user-message-shell">
        <div className="user-message-actions">
          <button type="button" className="user-rewind-button" title={t('回到这里')} onClick={() => void rewindToMessage(messageId)}>
            <RotateCcw size={13} />
            <span>{t('回到这里')}</span>
          </button>
        </div>
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
