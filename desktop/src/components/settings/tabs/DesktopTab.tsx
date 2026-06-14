import { memo } from 'react';
import { Eye, Keyboard, Layout, Monitor, MousePointer2, ShieldCheck } from 'lucide-react';
import type { ModelCapabilities } from '../../../lib/types';
import { useT } from '../../../hooks/useT';

interface DesktopTabProps {
  capabilities: ModelCapabilities | null;
  capabilitiesError: string;
}

export const DesktopTab = memo(function DesktopTab({ capabilities, capabilitiesError }: DesktopTabProps) {
  const t = useT();
  const supportsVision = Boolean(capabilities?.supportsVision);
  return (
    <div className="settings-card-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <Monitor size={16} className="section-icon" />
          <h3>{t('桌面操控')}</h3>
          <span className="settings-badge" data-variant={supportsVision ? 'success' : 'warning'}>
            {supportsVision ? t('可用') : t('不可用')}
          </span>
        </div>
        <p className="section-desc">
          {supportsVision
            ? `${capabilities?.model || t('当前模型')} ${t('可用于视觉桌面任务。')}`
            : capabilitiesError || `${capabilities?.model || t('当前模型')} ${t('不支持视觉桌面任务。')}`}
        </p>
        <div className="capability-matrix desktop-capability-matrix">
          <span className="cap-label"><Eye size={13} /> {t('视觉理解')}</span>
          <span className="cap-value">{supportsVision ? t('支持') : t('不支持')}</span>
          <span className="cap-label"><MousePointer2 size={13} /> {t('鼠标控制')}</span>
          <span className="cap-value">{t('支持')}</span>
          <span className="cap-label"><Keyboard size={13} /> {t('键盘输入')}</span>
          <span className="cap-value">{t('支持')}</span>
          <span className="cap-label"><ShieldCheck size={13} /> {t('紧急停止')}</span>
          <span className="cap-value">{t('支持')}</span>
        </div>
      </section>
      <section className="settings-section">
        <div className="settings-section-header">
          <Layout size={16} className="section-icon" />
          <h3>{t('窗口行为')}</h3>
        </div>
        <div className="capability-matrix">
          <span className="cap-label">{t('关闭窗口')}</span>
          <span className="cap-value">{t('进入托盘')}</span>
          <span className="cap-label">{t('标题栏')}</span>
          <span className="cap-value">{t('Electron 原生控制')}</span>
          <span className="cap-label">{t('本地预览')}</span>
          <span className="cap-value">{t('右侧工作台')}</span>
        </div>
      </section>
    </div>
  );
});
