import { memo, useEffect, useState } from 'react';
import { AppWindow } from 'lucide-react';
import { useT } from '../../../hooks/useT';
import type { WindowCloseBehavior } from '../../../lib/types';

export const GeneralTab = memo(function GeneralTab() {
  const t = useT();
  const [behavior, setBehavior] = useState<WindowCloseBehavior>('tray');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

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
    </div>
  );
});
