import { memo } from 'react';
import { ExternalLink, Info, Wrench } from 'lucide-react';
import type { DiagnosticsPayload, StoragePayload } from '../../../lib/types';
import { useT } from '../../../hooks/useT';

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
  onRefreshDiagnostics: () => void | Promise<void>;
  onSaveDiagnosticsBundle: () => void | Promise<void>;
  savingDiagnostics: boolean;
  updateMessage: string;
}

export const AboutTab = memo(function AboutTab({
  appInfo,
  checkingUpdates,
  diagnostics,
  diagnosticsMessage,
  onCheckUpdates,
  onRefreshDiagnostics,
  onSaveDiagnosticsBundle,
  savingDiagnostics,
  updateMessage,
}: AboutTabProps) {
  const t = useT();
  const storage = diagnostics?.storage || appInfo?.storage;

  return (
    <div className="settings-card-grid about-panel">
      <section className="settings-section">
        <div className="settings-section-header">
          <Info size={16} className="section-icon" />
          <h3>Metis Desktop</h3>
        </div>
        <p className="section-desc">{t('版本')} {appInfo?.version || '26.6.15'}</p>
        <p className="section-desc">{appInfo?.packaged ? t('已安装版本') : t('开发模式')} · Electron + React + Python</p>
        <div className="about-open-source-grid">
          <article>
            <span>{t('开源协议')}</span>
            <strong>PolyForm NC</strong>
          </article>
          <article>
            <span>{t('项目主页')}</span>
            <strong>GitHub</strong>
            <small>{(appInfo?.githubHome || 'https://github.com/linyeping/Metis').replace(/^https?:\/\//, '')}</small>
          </article>
        </div>
        {appInfo?.updateUrl && <p className="section-desc">{t('更新源')} {appInfo.updateUrl}</p>}
        <button type="button" onClick={() => void window.metis?.openExternal?.(appInfo?.githubHome || 'https://github.com/linyeping/Metis')}>
          <ExternalLink size={14} />
          GitHub
        </button>
        <button type="button" disabled={checkingUpdates} onClick={() => void onCheckUpdates()}>
          {checkingUpdates ? t('检查中...') : t('检查更新')}
        </button>
        {updateMessage && <p className="section-desc">{updateMessage}</p>}
      </section>
      <section className="settings-section diagnostics-panel">
        <div className="settings-section-header">
          <Wrench size={16} className="section-icon" />
          <h3>{t('发布诊断')}</h3>
        </div>
        <p className="section-desc">{t('生成不含密钥的诊断包，包含版本、平台、后端日志、工具调用、Preview 错误和截图证据摘要。')}</p>
        <div className="diagnostics-grid">
          <article>
            <span>{t('后端状态')}</span>
            <strong>{diagnostics?.backend.status || 'unknown'}</strong>
          </article>
          <article>
            <span>{t('后端端口')}</span>
            <strong>{diagnostics?.backend.port || '-'}</strong>
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
        <label>
          <span>{t('Metis 数据')}</span>
          <code>{storage?.metisHome || t('等待诊断数据')}</code>
        </label>
        <label>
          <span>{t('Electron 数据')}</span>
          <code>{storage?.electronUserData || t('等待诊断数据')}</code>
        </label>
        <label>
          <span>{t('后端日志')}</span>
          <code>{diagnostics?.backend.logPath || t('等待诊断数据')}</code>
        </label>
        <pre>{diagnostics?.backend.logTail || t('暂无后端日志。')}</pre>
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
