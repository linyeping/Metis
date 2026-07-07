import { memo, useState } from 'react';
import { CheckCircle2, Globe, Loader2, Router, ShieldCheck, WifiOff, XCircle } from 'lucide-react';
import { checkNetworkSettings } from '../../../lib/api';
import type { NetworkCheckPayload, RuntimeSettings } from '../../../lib/types';
import { useT } from '../../../hooks/useT';
import { stripConfigWhitespace } from '../settingsShared';

interface NetworkTabProps {
  apiKey?: string;
  onSettingsChange: (value: RuntimeSettings) => void;
  settings: RuntimeSettings;
}

const proxyModes: Array<{
  mode: RuntimeSettings['proxyMode'];
  icon: typeof Globe;
  title: string;
  desc: string;
}> = [
  {
    mode: 'off',
    icon: WifiOff,
    title: '直连',
    desc: '忽略系统代理和环境变量，LLM 请求直接访问供应商。',
  },
  {
    mode: 'system',
    icon: Globe,
    title: '系统代理',
    desc: '使用 Windows 代理和 HTTP_PROXY/HTTPS_PROXY 环境变量。',
  },
  {
    mode: 'custom',
    icon: Router,
    title: '手动代理',
    desc: '固定使用下面填写的 HTTP / SOCKS 代理。',
  },
];

export const NetworkTab = memo(function NetworkTab({ apiKey = '', onSettingsChange, settings }: NetworkTabProps) {
  const t = useT();
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<NetworkCheckPayload | null>(null);
  const [checkError, setCheckError] = useState('');
  const proxyUrl = settings.proxyMode === 'custom'
    ? `${settings.proxyScheme || 'http'}://${settings.proxyHost || '127.0.0.1'}:${settings.proxyPort || '7890'}`
    : settings.proxyMode === 'off'
      ? 'direct'
      : 'system';

  const runCheck = async () => {
    setChecking(true);
    setCheckError('');
    setCheckResult(null);
    try {
      const result = await checkNetworkSettings({
        ...settings,
        baseUrl: stripConfigWhitespace(settings.baseUrl),
        apiKey: stripConfigWhitespace(apiKey) || stripConfigWhitespace(settings.apiKey),
      });
      setCheckResult(result);
    } catch (error) {
      setCheckError(error instanceof Error ? error.message : String(error));
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="settings-card-grid network-settings-grid">
      <section className="settings-section network-settings-main">
        <div className="settings-section-header">
          <Globe size={16} className="section-icon" />
          <h3>{t('代理设置')}</h3>
        </div>
        <p className="section-desc">控制模型请求的出站网络路径。改完后先测试，确认通过再保存。</p>

        <div className="network-mode-list" role="radiogroup" aria-label="Proxy mode">
          {proxyModes.map(item => {
            const Icon = item.icon;
            const active = settings.proxyMode === item.mode;
            return (
              <button
                key={item.mode}
                type="button"
                className={`network-mode-card${active ? ' active' : ''}`}
                aria-checked={active}
                role="radio"
                onClick={() => onSettingsChange({ ...settings, proxyMode: item.mode })}
              >
                <Icon size={16} />
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.desc}</small>
                </span>
              </button>
            );
          })}
        </div>

        <div className="settings-inline-grid">
          <label>
            <span>{t('协议')}</span>
            <select
              value={settings.proxyScheme}
              disabled={settings.proxyMode !== 'custom'}
              onChange={event => onSettingsChange({ ...settings, proxyScheme: event.target.value })}
            >
              <option value="http">http</option>
              <option value="https">https</option>
              <option value="socks5">socks5</option>
              <option value="socks5h">socks5h</option>
            </select>
          </label>
          <label>
            <span>{t('端口')}</span>
            <input
              value={settings.proxyPort}
              disabled={settings.proxyMode !== 'custom'}
              inputMode="numeric"
              placeholder="7890"
              spellCheck={false}
              onChange={event => onSettingsChange({ ...settings, proxyPort: event.target.value.replace(/[^\d]/g, '') })}
            />
          </label>
        </div>
        <label>
          <span>{t('网关 / 主机')}</span>
          <input
            value={settings.proxyHost}
            disabled={settings.proxyMode !== 'custom'}
            placeholder="127.0.0.1"
            spellCheck={false}
            onChange={event => onSettingsChange({ ...settings, proxyHost: event.target.value })}
          />
        </label>
        <label>
          <span>{t('绕过地址')}</span>
          <input
            value={settings.proxyBypass}
            placeholder="localhost,127.0.0.1,::1"
            spellCheck={false}
            onChange={event => onSettingsChange({ ...settings, proxyBypass: event.target.value })}
          />
        </label>
      </section>

      <section className="settings-section network-check-section">
        <div className="settings-section-header">
          <ShieldCheck size={16} className="section-icon" />
          <h3>连接检查</h3>
        </div>
        <div className="network-effective-box">
          <span>当前路径</span>
          <strong>{proxyUrl}</strong>
          <small>{settings.baseUrl || '未配置 Base URL'} · {settings.model || '未配置模型'}</small>
        </div>
        <button className="settings-primary-action" type="button" disabled={checking} onClick={runCheck}>
          {checking ? <Loader2 size={15} className="spin-icon" /> : <ShieldCheck size={15} />}
          {checking ? '正在测试' : '测试模型连接'}
        </button>

        {checkError ? (
          <div className="network-check-result error">
            <XCircle size={16} />
            <span>{checkError}</span>
          </div>
        ) : null}

        {checkResult ? (
          <div className={`network-check-result ${checkResult.ok ? 'ok' : 'error'}`}>
            {checkResult.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <span>
              <strong>{checkResult.message}</strong>
              <small>
                {checkResult.models.message || checkResult.validation.message}
                {checkResult.elapsedMs ? ` · ${checkResult.elapsedMs}ms` : ''}
              </small>
              {checkResult.hint ? <small>{checkResult.hint}</small> : null}
              <small>
                实际代理：{checkResult.effectiveProxy.proxyUrl || (checkResult.effectiveProxy.bypassed ? '直连' : '系统代理')}
              </small>
            </span>
          </div>
        ) : null}
      </section>
    </div>
  );
});
