import { memo, useCallback, useEffect, useState } from 'react';
import { CheckCircle2, KeyRound, Plug, RefreshCw, Unplug } from 'lucide-react';
import type { ConnectorAuthorizeResult, ConnectorServiceStatus, ConnectorStatusPayload } from '../../../lib/types';
import { useT } from '../../../hooks/useT';

export const ConnectorsTab = memo(function ConnectorsTab() {
  const t = useT();
  const [status, setStatus] = useState<ConnectorStatusPayload | null>(null);
  const [busyService, setBusyService] = useState('');
  const [message, setMessage] = useState('');
  const [tokens, setTokens] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    const next = await window.metis.connectorStatus();
    setStatus(next);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const authorize = useCallback(async (service: string, token = '') => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await window.metis.connectorAuthorize(service, token.trim() ? { token: token.trim() } : {});
      setMessage(resultMessage(result, t));
      if (result.ok) {
        setTokens(current => ({ ...current, [service]: '' }));
        await refresh();
      }
    } finally {
      setBusyService('');
    }
  }, [refresh, t]);

  const disconnect = useCallback(async (service: string) => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await window.metis.connectorDisconnect(service);
      setMessage(result.ok ? t('已断开连接器') : result.error || t('连接器操作失败'));
      await refresh();
    } finally {
      setBusyService('');
    }
  }, [refresh, t]);

  const services = status?.services || [];
  return (
    <div className="settings-card-grid connectors-panel">
      <section className="settings-section">
        <div className="settings-section-header">
          <Plug size={16} className="section-icon" />
          <h3>{t('连接器')}</h3>
        </div>
        <p className="section-desc">{t('第三方连接器使用标准 OAuth，token 仅在本机加密保存。')}</p>
        <button type="button" onClick={() => void refresh()}>
          <RefreshCw size={14} />
          {t('刷新状态')}
        </button>
        {!status?.encryptionAvailable && <p className="section-desc warning">{t('当前系统暂不可用安全加密存储。')}</p>}
        {message && <p className="section-desc">{message}</p>}
      </section>

      {services.map(service => (
        <ConnectorCard
          key={service.service}
          busy={busyService === service.service}
          onAuthorize={token => void authorize(service.service, token)}
          onDisconnect={() => void disconnect(service.service)}
          onTokenChange={value => setTokens(current => ({ ...current, [service.service]: value }))}
          service={service}
          token={tokens[service.service] || ''}
          t={t}
        />
      ))}
    </div>
  );
});

interface ConnectorCardProps {
  busy: boolean;
  onAuthorize: (token: string) => void;
  onDisconnect: () => void;
  onTokenChange: (value: string) => void;
  service: ConnectorServiceStatus;
  token: string;
  t: (value: string) => string;
}

function ConnectorCard({ busy, onAuthorize, onDisconnect, onTokenChange, service, token, t }: ConnectorCardProps) {
  return (
    <section className="settings-section connector-card" data-connected={service.connected}>
      <div className="settings-section-header">
        <CheckCircle2 size={16} className="section-icon" />
        <h3>{service.displayName}</h3>
        <span className="connector-state">{service.connected ? t('已连接') : t('未连接')}</span>
      </div>
      <p className="section-desc">{service.scopes.join(' · ')}</p>
      <label>
        <span>{t('Token 环境变量')}</span>
        <code>{service.tokenEnv}</code>
      </label>
      <label>
        <span>{service.service === 'github' ? t('Personal access token（可选）') : t('本地测试 token（可选）')}</span>
        <input
          type="password"
          value={token}
          autoComplete="off"
          placeholder={service.service === 'github' ? 'ghp_...' : t('留空则尝试 OAuth')}
          onChange={event => onTokenChange(event.target.value)}
        />
      </label>
      <div className="connector-actions">
        <button type="button" disabled={busy || !service.encryptionAvailable} onClick={() => onAuthorize('')}>
          <Plug size={14} />
          {busy ? t('连接中...') : t('OAuth 连接')}
        </button>
        <button type="button" disabled={busy || !token.trim() || !service.encryptionAvailable} onClick={() => onAuthorize(token)}>
          <KeyRound size={14} />
          {t('加密保存 token')}
        </button>
        <button type="button" disabled={busy || !service.connected} onClick={onDisconnect}>
          <Unplug size={14} />
          {t('断开')}
        </button>
      </div>
      {service.service === 'gmail' && <p className="section-desc">{t('Gmail 敏感 scope 公开分发前需要 Google OAuth 验证；测试模式需添加测试用户。')}</p>}
    </section>
  );
}

function resultMessage(result: ConnectorAuthorizeResult, t: (value: string) => string): string {
  if (result.ok) {
    return result.testModeNote ? `${t('已连接')} · ${result.testModeNote}` : t('已连接');
  }
  if (result.code === 'missing_client_id') {
    return result.error || t('缺少 OAuth client id。');
  }
  return result.error || t('连接器操作失败');
}
