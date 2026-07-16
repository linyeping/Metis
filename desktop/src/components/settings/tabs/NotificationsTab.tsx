import { useEffect, useState } from 'react';
import { Bell, Volume2 } from 'lucide-react';
import type { Language, NotificationSettings } from '../../../lib/types';

const fallback: NotificationSettings = { soundEnabled: false, desktopEnabled: true };

export function NotificationsTab({ language }: { language: Language }) {
  const [config, setConfig] = useState(fallback);
  const [supported, setSupported] = useState(true);
  const [message, setMessage] = useState('');
  const text = (zh: string, en: string) => (language === 'zh' ? zh : en);

  useEffect(() => {
    let disposed = false;
    void window.metis.notificationConfig().then(value => {
      if (disposed) return;
      setConfig({ soundEnabled: value.soundEnabled, desktopEnabled: value.desktopEnabled });
      setSupported(value.supported);
    });
    return () => { disposed = true; };
  }, []);

  const update = async (patch: Partial<NotificationSettings>) => {
    const next = { ...config, ...patch };
    setConfig(next);
    setMessage('');
    const result = await window.metis.notificationUpdateConfig(patch);
    setConfig(result.config);
    setSupported(result.supported);
  };

  const testNotification = async () => {
    const result = await window.metis.notificationTest();
    setMessage(result.ok && result.supported
      ? text('测试通知已发送。', 'Test notification sent.')
      : text('当前系统不支持桌面通知。', 'Desktop notifications are not supported on this system.'));
  };

  return (
    <div className="settings-card-grid notification-settings-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <Bell size={16} className="section-icon" />
          <h3>{text('桌面通知', 'Desktop notifications')}</h3>
        </div>
        <p className="section-desc">
          {text('Metis 最小化、进入托盘或不在前台时，任务完成会显示 Windows 通知和任务栏未读徽标。Chat、Cowork、Code 与 Design 共用此设置。', 'When Metis is minimized, in the tray, or in the background, completed tasks show a Windows notification and taskbar unread badge. This setting is shared by Chat, Cowork, Code, and Design.')}
        </p>
        <div className="notification-setting-row">
          <span>
            <strong>{text('允许桌面通知', 'Enable desktop notifications')}</strong>
            <small>{supported ? text('点击通知可返回对应会话。', 'Click a notification to return to its session.') : text('当前 Windows 环境不支持系统通知。', 'System notifications are unavailable in this Windows environment.')}</small>
          </span>
          <button className="pet-switch" type="button" role="switch" aria-checked={config.desktopEnabled} data-on={config.desktopEnabled} disabled={!supported} onClick={() => void update({ desktopEnabled: !config.desktopEnabled })}>
            <span />
          </button>
        </div>
        <button type="button" className="notification-test-button" disabled={!supported || !config.desktopEnabled} onClick={() => void testNotification()}>
          {text('发送测试通知', 'Send test notification')}
        </button>
        {message && <p className="notification-settings-message" role="status">{message}</p>}
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <Volume2 size={16} className="section-icon" />
          <h3>{text('完成提示音', 'Completion sound')}</h3>
        </div>
        <p className="section-desc">{text('任务结束时由 Windows 通知播放提示音，默认关闭。', 'Play a Windows notification sound when a task finishes. Off by default.')}</p>
        <div className="notification-setting-row">
          <span>
            <strong>{text('播放提示音', 'Play completion sound')}</strong>
            <small>{text('失败和完成都会使用系统通知声音。', 'Both failed and completed tasks use the system notification sound.')}</small>
          </span>
          <button className="pet-switch" type="button" role="switch" aria-checked={config.soundEnabled} data-on={config.soundEnabled} onClick={() => void update({ soundEnabled: !config.soundEnabled })}>
            <span />
          </button>
        </div>
      </section>
    </div>
  );
}
