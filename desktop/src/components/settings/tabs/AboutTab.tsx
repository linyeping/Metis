import { memo } from 'react';
import { Activity, ExternalLink, Info, ShieldCheck, Wrench } from 'lucide-react';
import type { DiagnosticsPayload, StoragePayload } from '../../../lib/types';
import { useT } from '../../../hooks/useT';
import logo from '../../../assets/logo.png';

type AppInfo = {
  name: string;
  version: string;
  packaged: boolean;
  updateUrl: string;
  githubHome?: string;
  fakeBackend?: boolean;
  storage?: StoragePayload;
};

interface AboutTabProps {
  appInfo: AppInfo | null;
  checkingUpdates: boolean;
  diagnostics: DiagnosticsPayload | null;
  diagnosticsMessage: string;
  onCheckUpdates: () => void | Promise<void>;
  onInstallUpdate: () => void | Promise<void>;
  onRefreshDiagnostics: () => void | Promise<void>;
  onSaveDiagnosticsBundle: () => void | Promise<void>;
  savingDiagnostics: boolean;
  updateMessage: string;
  updateReady: boolean;
}

export const AboutTab = memo(function AboutTab({
  appInfo,
  checkingUpdates,
  diagnostics,
  diagnosticsMessage,
  onCheckUpdates,
  onInstallUpdate,
  onRefreshDiagnostics,
  onSaveDiagnosticsBundle,
  savingDiagnostics,
  updateMessage,
  updateReady,
}: AboutTabProps) {
  const t = useT();
  const storage = diagnostics?.storage || appInfo?.storage;

  return (
    <div className="settings-card-grid about-panel">
      <section className="settings-section">
        <div className="about-product-hero">
          <img src={logo} alt="" />
          <span>
            <small>{t('桌面智能工作区')}</small>
            <h3>Metis Desktop</h3>
            <p>{t('对话、协作、编码和设计集中在一个安静可靠的桌面环境中。')}</p>
          </span>
          <em>v{appInfo?.version || '26.7.27'}</em>
        </div>
        <div className="about-status-grid">
          <article>
            <ShieldCheck size={16} />
            <span><strong>{t('本地优先')}</strong><small>{t('密钥和工作数据保存在本机')}</small></span>
          </article>
          <article>
            <Activity size={16} />
            <span><strong>{appInfo?.packaged ? t('正式版本') : t('开发模式')}</strong><small>{t('当前安装通道')}</small></span>
          </article>
          <article>
            <Info size={16} />
            <span><strong>PolyForm NC</strong><small>{t('开源协议')}</small></span>
          </article>
        </div>
        <div className="about-actions">
          <button type="button" onClick={() => void window.metis?.openExternal?.(appInfo?.githubHome || 'https://github.com/linyeping/Metis')}>
            <ExternalLink size={14} />
            GitHub
          </button>
          <button type="button" disabled={checkingUpdates} onClick={() => void onCheckUpdates()}>
            {checkingUpdates ? t('检查中...') : t('检查更新')}
          </button>
          {updateReady && (
            <button type="button" className="primary" onClick={() => void onInstallUpdate()}>
              {t('重启更新')}
            </button>
          )}
        </div>
        {updateMessage && <p className="section-desc">{updateMessage}</p>}
      </section>
      <section className="settings-section diagnostics-panel">
        <div className="settings-section-header">
          <Wrench size={16} className="section-icon" />
          <h3>{t('发布诊断')}</h3>
        </div>
        <p className="section-desc">{t('遇到问题时生成不含密钥的支持包，便于定位故障。')}</p>
        <div className="diagnostics-grid">
          <article>
            <span>{t('后端状态')}</span>
            <strong>{diagnostics?.backend.status || 'unknown'}</strong>
          </article>
          <article>
            <span>{t('终端后端')}</span>
            <strong>{diagnostics?.terminal.backend || '-'}</strong>
          </article>
          <article>
            <span>{t('数据根')}</span>
            <strong>{storage?.source || '-'}</strong>
          </article>
        </div>
        <details className="diagnostics-details">
          <summary>{t('高级诊断信息')}</summary>
          <label><span>{t('Metis 数据')}</span><code>{storage?.metisHome || t('等待诊断数据')}</code></label>
          <label><span>{t('Electron 数据')}</span><code>{storage?.electronUserData || t('等待诊断数据')}</code></label>
          <label><span>{t('后端日志')}</span><code>{diagnostics?.backend.logPath || t('等待诊断数据')}</code></label>
          <pre>{diagnostics?.backend.logTail || t('暂无后端日志。')}</pre>
        </details>
        <div className="diagnostics-actions">
          <button type="button" onClick={() => void onRefreshDiagnostics()}>
            {t('刷新诊断')}
          </button>
          <button type="button" onClick={() => void window.metis.openLog()}>
            {t('打开日志')}
          </button>
          <button type="button" disabled={savingDiagnostics} onClick={() => void onSaveDiagnosticsBundle()}>
            {savingDiagnostics ? t('生成中...') : t('生成诊断包')}
          </button>
        </div>
        {diagnosticsMessage && <p className="section-desc">{diagnosticsMessage}</p>}
      </section>
    </div>
  );
});
