import { memo } from 'react';
import { Globe, Shield } from 'lucide-react';
import type { RuntimeSettings } from '../../../lib/types';
import { useT } from '../../../hooks/useT';

interface NetworkTabProps {
  onSettingsChange: (value: RuntimeSettings) => void;
  settings: RuntimeSettings;
}

export const NetworkTab = memo(function NetworkTab({ onSettingsChange, settings }: NetworkTabProps) {
  const t = useT();
  return (
    <div className="settings-card-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <Globe size={16} className="section-icon" />
          <h3>{t('代理设置')}</h3>
        </div>
        <p className="section-desc">{t('给 LLM 请求使用。Clash 常见配置是 HTTP 代理 `127.0.0.1:7890`。')}</p>
        <label>
          <span>{t('代理模式')}</span>
          <select
            value={settings.proxyMode}
            onChange={event => onSettingsChange({ ...settings, proxyMode: event.target.value as RuntimeSettings['proxyMode'] })}
          >
            <option value="system">{t('跟随系统 / 环境变量')}</option>
            <option value="custom">{t('自定义 Clash / HTTP 代理')}</option>
            <option value="off">{t('关闭代理')}</option>
          </select>
        </label>
        <div className="settings-inline-grid">
          <label>
            <span>{t('协议')}</span>
            <select
              value={settings.proxyScheme}
              disabled={settings.proxyMode !== 'custom'}
              onChange={event => onSettingsChange({ ...settings, proxyScheme: event.target.value })}
            >
              <option value="http">http</option>
              <option value="socks5">socks5</option>
            </select>
          </label>
          <label>
            <span>{t('端口')}</span>
            <input
              value={settings.proxyPort}
              disabled={settings.proxyMode !== 'custom'}
              placeholder="7890"
              spellCheck={false}
              onChange={event => onSettingsChange({ ...settings, proxyPort: event.target.value })}
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
      <section className="settings-section">
        <div className="settings-section-header">
          <Shield size={16} className="section-icon" />
          <h3>{t('实际开发提示')}</h3>
        </div>
        <p className="section-desc">{t('如果 DeepSeek / OpenAI 中转站不通，优先检查代理模式、Base URL、模型名和 API Key 是否匹配。')}</p>
        <p className="section-desc">{t('关闭代理会让 Metis LLM 请求忽略环境变量和 Windows 系统代理。')}</p>
      </section>
    </div>
  );
});
