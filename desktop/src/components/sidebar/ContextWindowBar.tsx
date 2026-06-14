import { useEffect } from 'react';
import { Gauge, Minimize2 } from 'lucide-react';
import { contextLimitForModel, contextWindowLevel, contextWindowPercent, estimateContextTokens, formatTokenCount } from '../../lib/contextWindow';
import { useChatStore } from '../../store/chatStore';

interface ContextWindowBarProps {
  model: string;
}

export function ContextWindowBar({ model }: ContextWindowBarProps) {
  const messages = useChatStore(state => state.messages);
  const usage = useChatStore(state => state.usage);
  const streaming = useChatStore(state => state.streaming);
  const compacting = useChatStore(state => state.compacting);
  const compactStatus = useChatStore(state => state.compactStatus);
  const compactContext = useChatStore(state => state.compactContext);
  const refreshCompactStatus = useChatStore(state => state.refreshCompactStatus);
  const limit = contextLimitForModel(model);
  const used = estimateContextTokens(messages, usage);
  const percent = contextWindowPercent(used, limit);
  const level = contextWindowLevel(percent);
  const cacheTotal = (usage?.promptCacheHitTokens || 0) + (usage?.promptCacheMissTokens || 0);
  const cachePercent = cacheTotal > 0 ? Math.round(((usage?.promptCacheHitTokens || 0) / cacheTotal) * 100) : 0;
  const compactDisabled = streaming || compacting || messages.length < 6;
  const compactLabel = compacting ? '压缩中...' : percent >= 70 ? '压缩上下文' : '压缩';
  const shouldSuggest = percent >= 70;
  const compactDoneLabel =
    compactStatus?.beforeContextMessages && compactStatus.afterContextMessages
      ? `模型上下文 ${compactStatus.beforeContextMessages} -> 摘要 + ${Math.max(0, compactStatus.afterContextMessages - 1)}`
      : `已压缩 ${compactStatus?.beforeCount || 0} -> ${compactStatus?.afterCount || 0}`;

  useEffect(() => {
    void refreshCompactStatus();
  }, [refreshCompactStatus]);

  return (
    <aside className="context-window-card" data-level={level} aria-label="Context window">
      <div className="context-window-head">
        <span>
          <Gauge size={14} strokeWidth={1.9} />
          Context window
        </span>
        <strong>{percent}%</strong>
      </div>
      <div className="context-window-track" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="context-window-meta">
        <span>{formatTokenCount(used)}</span>
        <span>/ {formatTokenCount(limit)}</span>
        {cacheTotal > 0 && <span>cache {cachePercent}%</span>}
      </div>
      {shouldSuggest && <p className="context-window-suggestion">上下文偏高，建议压缩后继续长任务。</p>}
      <button
        type="button"
        className="context-compact-button"
        disabled={compactDisabled}
        onClick={() => void compactContext(model)}
      >
        <Minimize2 size={12} />
        {compactLabel}
      </button>
      {compactStatus && (compactStatus.summaryPreview || compactStatus.error || compactStatus.beforeCount > 0) && (
        <div className="context-compact-status" data-ok={compactStatus.ok} data-running={compactStatus.running}>
          <strong>
            {compactStatus.running
              ? '正在压缩'
              : compactStatus.ok
                ? compactDoneLabel
                : '压缩未完成'}
          </strong>
          <span>{compactStatus.error || compactStatus.summaryPreview || '等待后端返回摘要。'}</span>
        </div>
      )}
    </aside>
  );
}
