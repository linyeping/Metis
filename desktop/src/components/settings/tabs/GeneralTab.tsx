import { memo, useEffect, useState } from 'react';
import { AppWindow, LayoutPanelLeft, Rocket } from 'lucide-react';
import { useT } from '../../../hooks/useT';
import type { AppMode, WindowCloseBehavior } from '../../../lib/types';

export const GeneralTab = memo(function GeneralTab() {
  const t = useT();
  const [behavior, setBehavior] = useState<WindowCloseBehavior>('tray');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [startupMode, setStartupMode] = useState(() => localStorage.getItem('metis.startupMode') || 'remember');
  const [startupSidebarOpen, setStartupSidebarOpen] = useState(() => localStorage.getItem('metis.startupSidebarOpen') !== 'false');

  useEffect(() => {
    let disposed = false;
    void window.metis.getWindowCloseBehavior()
      .then(result => {
        if (!disposed) setBehavior(result.behavior);
      })
      .catch(() => {
        if (!disposed) setError(t('窗口关闭行为读取失败，请重试。'));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [t]);

  const updateBehavior = async (next: WindowCloseBehavior) => {
    const previous = behavior;
    setBehavior(next);
    setSaving(true);
    setError('');
    try {
      const result = await window.metis.setWindowCloseBehavior(next);
      setBehavior(result.behavior);
      if (!result.ok) setError(t('窗口关闭行为保存失败，请重试。'));
    } catch {
      setBehavior(previous);
      setError(t('窗口关闭行为保存失败，请重试。'));
    } finally {
      setSaving(false);
    }
  };

  const updateStartupMode = (value: string) => {
    setStartupMode(value);
    if (value === 'remember') localStorage.removeItem('metis.startupMode');
    else localStorage.setItem('metis.startupMode', value as AppMode);
  };

  const updateStartupSidebar = (value: boolean) => {
    setStartupSidebarOpen(value);
    localStorage.setItem('metis.startupSidebarOpen', String(value));
  };

  return (
    <div className="settings-card-grid general-settings-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <AppWindow size={16} className="section-icon" />
          <h3>{t('窗口')}</h3>
        </div>
        <div className="window-close-behavior-row">
          <div className="window-close-behavior-copy">
            <strong>{t('窗口关闭行为')}</strong>
            <p>{t('选择关闭窗口时的默认行为')}</p>
          </div>
          <select
            aria-label={t('窗口关闭行为')}
            disabled={loading || saving}
            value={behavior}
            onChange={event => void updateBehavior(event.currentTarget.value as WindowCloseBehavior)}
          >
            <option value="ask">{t('每次询问')}</option>
            <option value="tray">{t('最小化到托盘')}</option>
            <option value="quit">{t('退出应用')}</option>
          </select>
        </div>
        {error && <p className="section-desc section-desc-warning" role="alert">{error}</p>}
      </section>
      <section className="settings-section">
        <div className="settings-section-header">
          <Rocket size={16} className="section-icon" />
          <h3>{t('启动偏好')}</h3>
        </div>
        <div className="window-close-behavior-row">
          <div className="window-close-behavior-copy">
            <strong>{t('启动界面')}</strong>
            <p>{t('选择每次打开 Metis 时首先进入的工作模式。')}</p>
          </div>
          <select value={startupMode} onChange={event => updateStartupMode(event.currentTarget.value)}>
            <option value="remember">{t('记住上次模式')}</option>
            <option value="chat">Chat</option>
            <option value="cowork">Cowork</option>
            <option value="code">Code</option>
          </select>
        </div>
        <label className="general-toggle-row">
          <span>
            <LayoutPanelLeft size={16} />
            <span>
              <strong>{t('启动时展开侧栏')}</strong>
              <small>{t('关闭后以更专注的主工作区启动。')}</small>
            </span>
          </span>
          <input type="checkbox" checked={startupSidebarOpen} onChange={event => updateStartupSidebar(event.currentTarget.checked)} />
        </label>
      </section>
    </div>
  );
});
