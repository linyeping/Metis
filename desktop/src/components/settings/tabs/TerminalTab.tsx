import { memo, useCallback, useState } from 'react';
import { Check, FolderOpen, RotateCcw, Terminal, Workflow } from 'lucide-react';
import type { RuntimeSettings } from '../../../lib/types';
import { terminalShellLabel, terminalShellOptions } from '../settingsShared';
import { useT } from '../../../hooks/useT';

interface TerminalTabProps {
  onSettingsChange: (value: RuntimeSettings) => void;
  settings: RuntimeSettings;
}

export const TerminalTab = memo(function TerminalTab({ onSettingsChange, settings }: TerminalTabProps) {
  const t = useT();
  /* ── Python interpreter local draft ─────────────────────────────── */
  const [draft, setDraft] = useState(settings.pythonPath || '');
  const [verified, setVerified] = useState<'idle' | 'ok' | 'bad'>('idle');

  const isDirty = draft !== (settings.pythonPath || '');

  const pickPython = useCallback(async () => {
    const selected = await window.metis.pickPythonExe();
    if (selected) {
      setDraft(selected);
      setVerified('idle');
    }
  }, []);

  const confirmPython = useCallback(() => {
    const trimmed = draft.trim();
    onSettingsChange({ ...settings, pythonPath: trimmed });
    setVerified(trimmed ? 'ok' : 'idle');
  }, [draft, onSettingsChange, settings]);

  const resetPython = useCallback(() => {
    setDraft('');
    onSettingsChange({ ...settings, pythonPath: '' });
    setVerified('idle');
  }, [onSettingsChange, settings]);

  return (
    <div className="settings-card-grid">
      {/* ── Shell ────────────────────────────────────────────────── */}
      <details className="settings-section settings-disclosure terminal-settings-disclosure" open>
        <summary>
          <div className="settings-section-header">
            <Terminal size={16} className="section-icon" />
            <span>
              <h3>{t('默认终端')}</h3>
              <p className="section-desc">{t('配置 Metis 终端卡片和终端入口的新建 Shell。')}</p>
            </span>
          </div>
          <span>{terminalShellLabel(settings.terminalShell)}</span>
        </summary>
        <div className="settings-disclosure-body">
          <label>
            <span>{t('默认 Shell')}</span>
            <select
              className="terminal-shell-select"
              value={settings.terminalShell}
              onChange={event => onSettingsChange({ ...settings, terminalShell: event.target.value as RuntimeSettings['terminalShell'] })}
            >
              {terminalShellOptions.map(option => (
                <option key={option.value} value={option.value}>
                  {t(option.label)}
                </option>
              ))}
            </select>
            <small>{t(terminalShellOptions.find(option => option.value === settings.terminalShell)?.hint ?? '')}</small>
          </label>
        </div>
      </details>

      {/* ── Python interpreter ───────────────────────────────────── */}
      <section className="settings-section settings-card">
        <div className="settings-section-header">
          <Terminal size={16} className="section-icon" />
          <span>
            <h3>{t('Python 解释器')}</h3>
            <p className="section-desc">{t('类似 PyCharm 的解释器选择，指定后端和工具链使用的 Python 环境。')}</p>
          </span>
          <span className={`python-status-badge ${settings.pythonPath ? 'custom' : 'auto'}`}>
            {settings.pythonPath ? t('已指定') : t('自动检测')}
          </span>
        </div>

        <div className="python-path-field">
          <label className="python-path-label">{t('Python 路径')}</label>
          <div className="python-path-row">
            <input
              type="text"
              className="python-path-input"
              value={draft}
              placeholder={t('自动检测（留空即可）— 也可粘贴 python.exe 完整路径')}
              onChange={event => { setDraft(event.target.value); setVerified('idle'); }}
            />
            <button type="button" className="python-path-browse" onClick={pickPython} title={t('浏览选择 python.exe')}>
              <FolderOpen size={14} />
            </button>
          </div>
        </div>

        {/* Action buttons */}
        <div className="python-actions">
          <button
            type="button"
            className={`python-confirm-btn ${isDirty ? 'dirty' : ''} ${verified === 'ok' ? 'saved' : ''}`}
            onClick={confirmPython}
            disabled={!isDirty && verified !== 'idle'}
          >
            <Check size={13} />
            <span>{verified === 'ok' ? t('已保存') : isDirty ? t('确认保存') : t('保存')}</span>
          </button>
          {(draft || settings.pythonPath) && (
            <button type="button" className="python-reset-btn" onClick={resetPython} title={t('恢复自动检测')}>
              <RotateCcw size={13} />
              <span>{t('恢复自动检测')}</span>
            </button>
          )}
        </div>

        {/* Hints */}
        <div className="python-hints">
          {settings.pythonPath && !isDirty && (
            <small className="python-hint current">{t('当前使用：')}{settings.pythonPath}</small>
          )}
          {isDirty && draft && (
            <small className="python-hint pending">{t('待确认：')}{draft}{t('（点击「确认保存」生效）')}</small>
          )}
          <small className="python-hint">
            {t('Anaconda 环境：选择 D:\\Anaconda3\\envs\\环境名\\python.exe')}
          </small>
          <small className="python-hint">
            {t('系统 Python：选择 C:\\Python312\\python.exe 或类似路径')}
          </small>
          <small className="python-hint muted">{t('修改后需要重启 Metis 或点击设置面板「保存」按钮后重启生效。')}</small>
        </div>
      </section>

      {/* ── Tips ──────────────────────────────────────────────────── */}
      <section className="settings-section">
        <div className="settings-section-header">
          <Workflow size={16} className="section-icon" />
          <h3>{t('常用场景')}</h3>
        </div>
        <p className="section-desc">{t('npm run dev、git status、python -m pytest 等命令可在底栏终端连续运行。')}</p>
      </section>
    </div>
  );
});
