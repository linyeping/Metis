import { memo, useCallback, useEffect, useState } from 'react';
import { CheckCircle2, KeyRound, Plug, Power, PowerOff, RefreshCw, Unplug, Zap } from 'lucide-react';
import type { ConnectorAuthorizeResult, ConnectorServiceStatus, ConnectorStatusPayload } from '../../../lib/types';
import {
  type BackendConnector,
  connectConnector,
  disconnectConnector,
  listBackendConnectors,
  testConnector,
} from '../../../lib/api';
import { useT } from '../../../hooks/useT';

export const ConnectorsTab = memo(function ConnectorsTab() {
  const t = useT();
  const [status, setStatus] = useState<ConnectorStatusPayload | null>(null);
  const [backend, setBackend] = useState<Record<string, BackendConnector>>({});
  const [busyService, setBusyService] = useState('');
  const [message, setMessage] = useState('');
  const [tokens, setTokens] = useState<Record<string, string>>({});
  const [secretValues, setSecretValues] = useState<Record<string, Record<string, string>>>({});

  const refresh = useCallback(async () => {
    const next = await window.metis.connectorStatus();
    setStatus(next);
    // Backend activation state is best-effort — if the backend is down, cards
    // simply show inactive rather than failing the whole panel.
    try {
      const rows = await listBackendConnectors();
      setBackend(Object.fromEntries(rows.map(row => [row.serviceId, row])));
    } catch {
      setBackend({});
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const authorize = useCallback(async (service: string, options: { token?: string; secrets?: Record<string, string> } = {}) => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await window.metis.connectorAuthorize(service, options);
      setMessage(resultMessage(result, t));
      if (result.ok) {
        setTokens(current => ({ ...current, [service]: '' }));
        setSecretValues(current => ({ ...current, [service]: {} }));
        await refresh();
      }
    } finally {
      setBusyService('');
    }
  }, [refresh, t]);

  const removeToken = useCallback(async (service: string) => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await window.metis.connectorDisconnect(service);
      setMessage(result.ok ? t('已删除连接器配置') : result.error || t('连接器操作失败'));
      await refresh();
    } finally {
      setBusyService('');
    }
  }, [refresh, t]);

  const activate = useCallback(async (service: string) => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await connectConnector(service);
      setMessage(result.ok ? `${t('已激活')} · ${result.tools.length} ${t('个工具')}` : result.error || t('激活失败'));
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t('激活失败'));
    } finally {
      setBusyService('');
    }
  }, [refresh, t]);

  const verify = useCallback(async (service: string) => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await testConnector(service);
      setMessage(result.ok ? `${t('连通正常')} · ${result.toolsCount} ${t('个工具')}` : result.error || t('测试失败'));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t('测试失败'));
    } finally {
      setBusyService('');
    }
  }, [t]);

  const deactivate = useCallback(async (service: string) => {
    setBusyService(service);
    setMessage('');
    try {
      const result = await disconnectConnector(service);
      setMessage(result.ok ? t('已停用') : result.error || t('停用失败'));
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t('停用失败'));
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
        <p className="section-desc">{t('第三方连接器使用标准 OAuth 或本地配置，敏感信息仅在本机加密保存。')}</p>
        <p className="section-desc">{t('授权 token 或保存配置后点“激活”启动该连接器的工具；新保存的敏感信息需重启后端才会注入。')}</p>
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
          backend={backend[service.service]}
          onAuthorize={options => void authorize(service.service, options)}
          onRemoveToken={() => void removeToken(service.service)}
          onActivate={() => void activate(service.service)}
          onTest={() => void verify(service.service)}
          onDeactivate={() => void deactivate(service.service)}
          onSecretChange={(envName, value) => setSecretValues(current => ({
            ...current,
            [service.service]: {
              ...(current[service.service] || {}),
              [envName]: value,
            },
          }))}
          onTokenChange={value => setTokens(current => ({ ...current, [service.service]: value }))}
          secretValues={secretValues[service.service] || {}}
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
  backend?: BackendConnector;
  onAuthorize: (options: { token?: string; secrets?: Record<string, string> }) => void;
  onRemoveToken: () => void;
  onActivate: () => void;
  onTest: () => void;
  onDeactivate: () => void;
  onSecretChange: (envName: string, value: string) => void;
  onTokenChange: (value: string) => void;
  secretValues: Record<string, string>;
  service: ConnectorServiceStatus;
  token: string;
  t: (value: string) => string;
}

function ConnectorCard({
  busy,
  backend,
  onAuthorize,
  onRemoveToken,
  onActivate,
  onTest,
  onDeactivate,
  onSecretChange,
  onTokenChange,
  secretValues,
  service,
  token,
  t,
}: ConnectorCardProps) {
  const active = Boolean(backend?.active);
  const secretEnvs = service.secretEnvs?.length ? service.secretEnvs : backend?.secretEnvs || [];
  const optionalSecretEnvs = service.optionalSecretEnvs?.length ? service.optionalSecretEnvs : backend?.optionalSecretEnvs || [];
  const envSecretConnector = secretEnvs.length > 0;
  const noTokenConnector = !service.tokenEnv && !envSecretConnector;
  const requiredSecretsReady = secretEnvs.every(envName => (secretValues[envName] || '').trim());
  return (
    <section className="settings-section connector-card" data-connected={service.connected} data-active={active}>
      <div className="settings-section-header">
        <CheckCircle2 size={16} className="section-icon" />
        <h3>{service.displayName}</h3>
        <span className="connector-state">{active ? t('运行中') : service.connected ? t('已连接') : t('未连接')}</span>
        {active && backend ? <span className="connector-state">{backend.toolsCount} {t('个工具')}</span> : null}
      </div>
      <p className="section-desc">{service.scopes.join(' · ')}</p>
      <label>
        <span>{t('认证方式')}</span>
        <code>{envSecretConnector ? [...secretEnvs, ...optionalSecretEnvs].join(' · ') : service.tokenEnv || t('无需 token')}</code>
      </label>
      {envSecretConnector && (
        <>
          {[...secretEnvs, ...optionalSecretEnvs].map(envName => (
            <label key={envName}>
              <span>{envName}{optionalSecretEnvs.includes(envName) ? ` · ${t('可选')}` : ''}</span>
              <input
                type={/SECRET|TOKEN|PASSWORD/i.test(envName) ? 'password' : 'text'}
                value={secretValues[envName] || ''}
                autoComplete="off"
                placeholder={envName === 'REDIRECT_URI' ? 'http://localhost:8080/callback' : envName}
                onChange={event => onSecretChange(envName, event.target.value)}
              />
            </label>
          ))}
          <div className="connector-actions">
            <button
              type="button"
              disabled={busy || !requiredSecretsReady || !service.encryptionAvailable}
              onClick={() => onAuthorize({ secrets: secretValues })}
            >
              <KeyRound size={14} />
              {t('加密保存配置')}
            </button>
            <button type="button" disabled={busy || !service.connected} onClick={onRemoveToken}>
              <Unplug size={14} />
              {t('删除配置')}
            </button>
          </div>
        </>
      )}
      {!noTokenConnector && !envSecretConnector && (
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
      )}
      {!noTokenConnector && !envSecretConnector && (
        <div className="connector-actions">
          <button type="button" disabled={busy || !service.encryptionAvailable} onClick={() => onAuthorize({})}>
            <Plug size={14} />
            {busy ? t('处理中...') : t('OAuth 连接')}
          </button>
          <button type="button" disabled={busy || !token.trim() || !service.encryptionAvailable} onClick={() => onAuthorize({ token: token.trim() })}>
            <KeyRound size={14} />
            {t('加密保存 token')}
          </button>
          <button type="button" disabled={busy || !service.connected} onClick={onRemoveToken}>
            <Unplug size={14} />
            {t('删除 token')}
          </button>
        </div>
      )}
      <div className="connector-actions">
        <button type="button" disabled={busy || active} onClick={onActivate}>
          <Power size={14} />
          {t('激活')}
        </button>
        <button type="button" disabled={busy || !active} onClick={onTest}>
          <Zap size={14} />
          {t('测试连通')}
        </button>
        <button type="button" disabled={busy || !active} onClick={onDeactivate}>
          <PowerOff size={14} />
          {t('停用')}
        </button>
      </div>
      {backend?.lastError ? <p className="section-desc warning">{backend.lastError}</p> : null}
      {service.service === 'gmail' && <p className="section-desc">{t('Gmail 敏感 scope 公开分发前需要 Google OAuth 验证；测试模式需添加测试用户。')}</p>}
    </section>
  );
}

function resultMessage(result: ConnectorAuthorizeResult, t: (value: string) => string): string {
  if (result.ok) {
    if (result.method === 'env_secrets') return t('已加密保存配置；请重启后端后再激活连接器。');
    return result.testModeNote ? `${t('已连接')} · ${result.testModeNote}` : t('已连接');
  }
  if (result.code === 'missing_client_id') {
    return result.error || t('缺少 OAuth client id。');
  }
  return result.error || t('连接器操作失败');
}
