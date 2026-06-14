/**
 * FileChangeReviewCard — 文件变更审核卡片。
 *
 * 从 MetisThread.tsx 拆分：FileChangeReviewCard 及相关
 * 辅助函数（撤销操作、变更汇总）。
 */
import { ClipboardCheck, FileDiff, Undo2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { revertFileChanges } from '../../lib/api';
import { buildFileChangePreview, summarizeFileChanges } from '../../lib/diffPreview';
import type { FileChangePreview, FileChangeSummary } from '../../lib/diffPreview';
import { useUiStore } from '../../store/uiStore';
import type { FileChangeRevertItem, FileChangeRevertResult } from '../../lib/types';
import { ChangeCount, compactPath } from './threadUtils';
import { toolPartValue } from './ToolCallBlock';

// ---------------------------------------------------------------------------
// FileChangeReviewCard
// ---------------------------------------------------------------------------

type FileChangeRevertStatus = 'active' | 'reverting' | 'reverted' | 'error';

export function FileChangeReviewCard({ summary }: { summary: FileChangeSummary }) {
  const setDiffPreview = useUiStore(state => state.setDiffPreview);
  const setDiffReview = useUiStore(state => state.setDiffReview);
  const setDiffRevertItems = useUiStore(state => state.setDiffRevertItems);
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const [revertStatus, setRevertStatus] = useState<FileChangeRevertStatus>('active');
  const [revertMessage, setRevertMessage] = useState('');
  const [revertItems, setRevertItems] = useState<FileChangeRevertItem[]>([]);
  const visibleFiles = summary.files.slice(0, 4);
  const hiddenCount = Math.max(0, summary.files.length - visibleFiles.length);
  const reverted = revertStatus === 'reverted';

  const openReview = (preview = summary.changes[0]) => {
    if (preview) {
      setDiffReview(summary, preview.id);
      return;
    }
    if (summary.changes[0]) setDiffPreview(summary.changes[0]);
  };

  useEffect(() => {
    setRevertStatus('active');
    setRevertMessage('');
    setRevertItems([]);
    setDiffRevertItems(summary.id, []);
  }, [summary.id]);

  const requestRevert = async () => {
    const confirmed = await requestConfirm({
      title: '撤销文件变更？',
      message: 'Metis 会通过后端安全通道撤销这轮文件变更。若文件已经被你手动改过，撤销会被拒绝。',
      details: formatChangeReviewDetails(summary),
      confirmLabel: '撤销',
      cancelLabel: '先审核',
      tone: 'danger',
      icon: 'warning',
    });
    if (!confirmed) return;
    setRevertStatus('reverting');
    setRevertMessage('正在校验当前文件状态...');
    try {
      const result = await revertFileChanges(summary);
      setRevertItems(result.items);
      setDiffRevertItems(summary.id, result.items);
      if (result.ok) {
        setRevertStatus('reverted');
        setRevertMessage(`已撤销 ${result.revertedCount} 个文件 · 审计 ${compactPath(result.auditPath)}`);
      } else {
        setRevertStatus('error');
        setRevertMessage(formatRevertFailure(result));
      }
    } catch (error) {
      setRevertStatus('error');
      setRevertMessage(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <section className="file-change-review-card" data-status={revertStatus} aria-label="文件变更审核">
      <header>
        <span className="file-change-review-icon">
          <FileDiff size={15} />
        </span>
        <div>
          <strong>
            {revertStatus === 'reverted'
              ? '已撤销'
              : revertStatus === 'reverting'
                ? '正在撤销'
                : revertStatus === 'error'
                  ? '撤销失败'
                  : `已编辑 ${summary.fileCount} 个文件`}
          </strong>
          <span className="file-change-counts">
            <ChangeCount type="add" value={summary.additions} />
            <ChangeCount type="remove" value={summary.removals} />
          </span>
        </div>
        <div className="file-change-review-actions">
          <button className="file-change-undo-button" type="button" disabled={reverted || revertStatus === 'reverting'} onClick={() => void requestRevert()}>
            <Undo2 size={13} />
            {revertStatus === 'reverted' ? '已撤销' : revertStatus === 'reverting' ? '撤销中' : '撤销'}
          </button>
          <button className="file-change-review-button" type="button" onClick={() => openReview()}>
            <ClipboardCheck size={13} />
            审核
          </button>
        </div>
      </header>
      <div className="file-change-file-list">
        {visibleFiles.map(file => (
          <button
            className="file-change-file-row"
            data-status={revertItemFor(file.preview, revertItems)?.status || revertStatus}
            type="button"
            key={file.path || file.title}
            onClick={() => openReview(file.preview)}
          >
            <span title={file.path}>{compactPath(file.path || file.title)}</span>
            <em>
              <ChangeCount type="add" value={file.additions} />
              <ChangeCount type="remove" value={file.removals} />
            </em>
            {revertItemFor(file.preview, revertItems) && (
              <small className="file-change-file-status">{revertLabel(revertItemFor(file.preview, revertItems)?.status || '')}</small>
            )}
          </button>
        ))}
        {hiddenCount > 0 && <p className="file-change-more">另有 {hiddenCount} 个文件，点击审核查看完整变更。</p>}
      </div>
      {revertMessage && <p className="file-change-revert-message">{revertMessage}</p>}
    </section>
  );
}

// ---------------------------------------------------------------------------
// summarizeMessageFileChanges — 消息级文件变更汇总
// ---------------------------------------------------------------------------

export function summarizeMessageFileChanges(messageId: string, content: unknown): FileChangeSummary | null {
  if (!Array.isArray(content)) return null;
  const previews: FileChangePreview[] = [];
  for (const part of content) {
    const toolPart = toolPartValue(part);
    if (!toolPart) continue;
    if (toolPart.result === undefined) continue;
    if (toolPart.metisStatus && toolPart.metisStatus !== 'success') continue;
    const preview = buildFileChangePreview(toolPart.toolName, toolPart.args, toolPart.result);
    if (preview) previews.push(preview);
  }
  return summarizeFileChanges(previews, messageId || 'assistant-message');
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function formatChangeReviewDetails(summary: FileChangeSummary): string {
  return [
    `文件: ${summary.fileCount}`,
    `变更: +${summary.additions} / -${summary.removals}`,
    '',
    ...summary.files.map(file => `${file.path || file.title}  +${file.additions} / -${file.removals}`),
  ].join('\n');
}

function revertItemFor(preview: FileChangePreview, items: FileChangeRevertItem[]): FileChangeRevertItem | null {
  return items.find(item => item.id === preview.id || item.path === preview.path) || null;
}

function revertLabel(status: string): string {
  if (status === 'reverted') return '已撤销';
  if (status === 'conflict') return '冲突';
  if (status === 'blocked') return '已拦截';
  return status || '待处理';
}

function formatRevertFailure(result: FileChangeRevertResult): string {
  const firstIssue = result.items.find(item => item.status === 'conflict' || item.status === 'blocked');
  const parts = ['撤销未完成'];
  if (result.conflictCount) parts.push(`${result.conflictCount} 个冲突`);
  if (result.blockedCount) parts.push(`${result.blockedCount} 个拦截`);
  const summary = parts.join(' · ');
  return firstIssue?.message ? `${summary}: ${firstIssue.path} · ${firstIssue.message}` : `${summary}。请先审核右栏里的文件状态。`;
}
