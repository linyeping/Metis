import { memo } from 'react';
import { Gauge, RefreshCw } from 'lucide-react';
import type { ProviderUsagePayload, RuntimeSettings } from '../../../lib/types';
import { formatInteger, formatMoneyValue } from '../settingsShared';
import { useT } from '../../../hooks/useT';

interface UsageTabProps {
  loadingUsage: boolean;
  onRefreshProviderUsage: () => void | Promise<void>;
  providerUsage: ProviderUsagePayload | null;
  settings: RuntimeSettings;
}

export const UsageTab = memo(function UsageTab({
  loadingUsage,
  onRefreshProviderUsage,
  providerUsage,
  settings,
}: UsageTabProps) {
  const t = useT();
  return (
    <div className="settings-card-grid">
      <section className="settings-section provider-usage-card" data-status={providerUsage?.status || 'idle'}>
        <div className="settings-section-header">
          <Gauge size={16} className="section-icon" />
          <h3>{t('额度 / Usage')}</h3>
        </div>
        <div className="provider-usage-head">
          <div>
            <p>{settings.providerId || settings.backend} · {settings.model || t('未选择模型')}</p>
          </div>
          <button type="button" disabled={loadingUsage} onClick={() => void onRefreshProviderUsage()}>
            <RefreshCw size={14} />
            {loadingUsage ? t('查询中...') : t('刷新额度')}
          </button>
        </div>
        {!providerUsage && <p>{t('点击刷新后读取供应商只读额度接口；不会发起模型调用。')}</p>}
        {providerUsage && (
          <>
            <div className="usage-balance-row">
              <span>{providerUsage.planName || providerUsage.mode || 'Provider usage'}</span>
              <strong>
                {formatMoneyValue(providerUsage.remaining || providerUsage.balance)} {providerUsage.unit || ''}
              </strong>
            </div>
            <div className="usage-status-line">
              <span>{t(providerUsage.message)}</span>
              {providerUsage.hint && <em>{t(providerUsage.hint)}</em>}
            </div>
            <div className="usage-stat-grid">
              <article>
                <span>{t('今日请求')}</span>
                <strong>{formatInteger(providerUsage.today.requests)}</strong>
              </article>
              <article>
                <span>{t('今日 Tokens')}</span>
                <strong>{formatInteger(providerUsage.today.totalTokens)}</strong>
              </article>
              <article>
                <span>{t('今日费用')}</span>
                <strong>{formatMoneyValue(providerUsage.today.cost)}</strong>
              </article>
              <article>
                <span>{t('累计请求')}</span>
                <strong>{formatInteger(providerUsage.total.requests)}</strong>
              </article>
              <article>
                <span>{t('累计 Tokens')}</span>
                <strong>{formatInteger(providerUsage.total.totalTokens)}</strong>
              </article>
              <article>
                <span>{t('累计费用')}</span>
                <strong>{formatMoneyValue(providerUsage.total.cost)}</strong>
              </article>
            </div>
            {providerUsage.usageUrl && <code>{providerUsage.usageUrl}</code>}
          </>
        )}
      </section>
      <section className="settings-section">
        <div className="settings-section-header">
          <Gauge size={16} className="section-icon" />
          <h3>{t('读取规则')}</h3>
        </div>
        <p className="section-desc">{t('PinAI / 自定义 OpenAI 中转站优先读取 `/v1/usage`；DeepSeek 读取 `/user/balance`。')}</p>
        <p className="section-desc">{t('如果供应商不开放额度接口，Metis 会显示不支持，不会编造百分比。')}</p>
      </section>
    </div>
  );
});
