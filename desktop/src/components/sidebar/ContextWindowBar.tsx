import { useEffect } from 'react';
import { ChevronDown, Gauge, Minimize2 } from 'lucide-react';
import { contextLimitForModel, contextWindowLevel, contextWindowPercent, estimateContextTokens, formatTokenCount } from '../../lib/contextWindow';
import type { ContextLedger } from '../../lib/types';
import { useT } from '../../hooks/useT';
import { useChatStore } from '../../store/chatStore';
import { useUiStore } from '../../store/uiStore';

interface ContextWindowBarProps {
  model: string;
}

export function ContextWindowBar({ model }: ContextWindowBarProps) {
  const t = useT();
  const messages = useChatStore(state => state.messages);
  const usage = useChatStore(state => state.usage);
  const contextLedger = useChatStore(state => state.contextLedger);
  const streaming = useChatStore(state => state.streaming);
  const compacting = useChatStore(state => state.compacting);
  const compactStatus = useChatStore(state => state.compactStatus);
  const compactContext = useChatStore(state => state.compactContext);
  const refreshCompactStatus = useChatStore(state => state.refreshCompactStatus);
  const contextDetailsOpen = useUiStore(state => state.contextDetailsOpen);
  const toggleContextDetailsOpen = useUiStore(state => state.toggleContextDetailsOpen);
  const limit = contextLedger?.contextLimit || contextLimitForModel(model);
  const used = contextLedger?.estimatedTotalTokens || estimateContextTokens(messages, usage);
  const percent = contextWindowPercent(used, limit);
  const level = contextWindowLevel(percent);
  const cacheHit = contextLedger?.cacheHitTokens ?? usage?.promptCacheHitTokens ?? 0;
  const cacheMiss = contextLedger?.cacheMissTokens ?? usage?.promptCacheMissTokens ?? 0;
  const cacheTotal = cacheHit + cacheMiss;
  const cachePercent = cacheTotal > 0 ? Math.round((cacheHit / cacheTotal) * 100) : 0;
  const detailRows = contextLedger ? contextRows(contextLedger, limit, t) : fallbackRows(used, limit, t);
  const compactDisabled = streaming || compacting || messages.length < 6;
  const compactLabel = compacting ? t('压缩中...') : percent >= 70 ? t('压缩上下文') : t('压缩');
  const shouldSuggest = percent >= 70;
  const compactDoneLabel =
    compactStatus?.beforeContextMessages && compactStatus.afterContextMessages
      ? `模型上下文 ${compactStatus.beforeContextMessages} -> 摘要 + ${Math.max(0, compactStatus.afterContextMessages - 1)}`
      : `已压缩 ${compactStatus?.beforeCount || 0} -> ${compactStatus?.afterCount || 0}`;

  useEffect(() => {
    void refreshCompactStatus();
  }, [refreshCompactStatus]);

  return (
    <aside className="context-window-card" data-level={level} aria-label={t('上下文窗口')}>
      <button
        type="button"
        className="context-window-head context-window-toggle"
        aria-expanded={contextDetailsOpen}
        onClick={toggleContextDetailsOpen}
      >
        <span>
          <Gauge size={14} strokeWidth={1.9} />
          {t('上下文窗口')}
        </span>
        <strong>
          {percent}%
          <ChevronDown size={13} data-open={contextDetailsOpen} />
        </strong>
      </button>
      <div className="context-window-track" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
      <div className="context-window-meta">
        <span>{formatTokenCount(used)}</span>
        <span>/ {formatTokenCount(limit)}</span>
        {cacheTotal > 0 && <span>{t('缓存')} {cachePercent}%</span>}
      </div>
      {contextDetailsOpen && (
        <div className="context-window-details">
          {detailRows.map(row => (
            <div className="context-window-detail-row" key={row.id}>
              <div>
                <span>{row.label}</span>
                <strong>{formatTokenCount(row.tokens)}</strong>
              </div>
              <div className="context-window-detail-track" aria-hidden="true">
                <span style={{ width: `${row.percent}%` }} />
              </div>
            </div>
          ))}
          {cacheTotal > 0 && (
            <div className="context-window-cache-row">
              <span>{t('缓存命中')}</span>
              <strong>{cachePercent}%</strong>
            </div>
          )}
        </div>
      )}
      {shouldSuggest && <p className="context-window-suggestion">{t('上下文偏高，建议压缩后继续长任务。')}</p>}
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
              ? t('正在压缩')
              : compactStatus.ok
                ? compactDoneLabel
                : t('压缩未完成')}
          </strong>
          <span>{compactStatus.error || compactStatus.summaryPreview || t('等待后端返回摘要。')}</span>
        </div>
      )}
    </aside>
  );
}

interface ContextRow {
  id: string;
  label: string;
  tokens: number;
  percent: number;
}

function contextRows(ledger: ContextLedger, limit: number, t: (value: string) => string): ContextRow[] {
  const rows: Array<Omit<ContextRow, 'percent'>> = [];
  const system = ledger.systemBreakdown;
  const schema = ledger.schemaBreakdown;
  pushRow(rows, 'systemPrompt', t('系统提示词'), system?.systemPrompt ?? ledger.systemTokens);
  pushRow(rows, 'skills', t('技能'), system?.skills ?? 0);
  pushRow(rows, 'memory', t('记忆'), system?.memory ?? 0);
  if (schema) {
    pushRow(rows, 'mcp', t('MCP 工具'), schema.mcp);
    pushRow(rows, 'builtin', t('内置工具'), schema.builtin);
  } else {
    pushRow(rows, 'tools', t('工具'), ledger.schemaTokens);
  }
  pushRow(rows, 'messages', t('消息'), ledger.historyTokens);
  pushRow(rows, 'free', t('剩余'), Math.max(0, limit - ledger.estimatedTotalTokens), true);
  return rows.map(row => ({ ...row, percent: contextWindowPercent(row.tokens, limit) }));
}

function fallbackRows(used: number, limit: number, t: (value: string) => string): ContextRow[] {
  return [
    { id: 'messages', label: t('消息'), tokens: used, percent: contextWindowPercent(used, limit) },
    { id: 'free', label: t('剩余'), tokens: Math.max(0, limit - used), percent: contextWindowPercent(Math.max(0, limit - used), limit) },
  ];
}

function pushRow(rows: Array<Omit<ContextRow, 'percent'>>, id: string, label: string, tokens: number, keepZero = false): void {
  const value = Number.isFinite(tokens) ? Math.max(0, Math.round(tokens)) : 0;
  if (!keepZero && value <= 0) return;
  rows.push({ id, label, tokens: value });
}
