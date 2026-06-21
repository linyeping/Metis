import { useEffect, useState } from 'react';
import { SHORTCUTTABLE_CARDS, useUiStore, type WorkspaceCardId } from '../../store/uiStore';
import { isMacPlatform, shortcutLabel } from '../shell/Titlebar';
import { useT } from '../../hooks/useT';

const CARD_LABELS: Record<WorkspaceCardId, string> = {
  web: 'Preview',
  diff: 'Diff',
  terminal: 'Terminal',
  files: 'Files',
  activity: 'Background tasks',
  plan: 'Plan',
  tool: 'Tool output',
};

const MODIFIER_KEYS = new Set(['Shift', 'Control', 'Meta', 'Alt']);

export function ShortcutSettings() {
  const t = useT();
  const shortcuts = useUiStore(state => state.workspaceCardShortcuts);
  const setShortcut = useUiStore(state => state.setWorkspaceCardShortcut);
  const [recording, setRecording] = useState<WorkspaceCardId | null>(null);

  useEffect(() => {
    if (!recording) return undefined;
    const onKey = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === 'Escape') {
        setRecording(null);
        return;
      }
      if (MODIFIER_KEYS.has(event.key)) return;
      const mod = isMacPlatform ? event.metaKey : event.ctrlKey;
      if (!mod) return; // require the platform modifier so it can't clash with typing
      setShortcut(recording, { key: event.key.toLowerCase(), shift: event.shiftKey });
      setRecording(null);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [recording, setShortcut]);

  return (
    <div className="shortcut-settings" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <p className="settings-hint" style={{ opacity: 0.7, fontSize: 12 }}>
        {t('右上角工作区卡片的开关快捷键。主键为')} {isMacPlatform ? '⌘' : 'Ctrl'}
        {t('，点「录制」后按下组合键（可加 Shift）。')}
      </p>
      {SHORTCUTTABLE_CARDS.map(id => {
        const sc = shortcuts[id];
        const isRecording = recording === id;
        return (
          <div
            key={id}
            className="shortcut-row"
            style={{ display: 'flex', alignItems: 'center', gap: 10 }}
          >
            <span style={{ width: 96 }}>{CARD_LABELS[id]}</span>
            <kbd
              style={{
                minWidth: 110,
                padding: '3px 8px',
                borderRadius: 6,
                border: '1px solid var(--border, #ddd)',
                fontSize: 12,
                textAlign: 'center',
                opacity: isRecording ? 1 : sc ? 1 : 0.5,
              }}
            >
              {isRecording ? t('按下组合键…') : sc ? shortcutLabel(sc) : t('未设置')}
            </kbd>
            <button type="button" className="settings-button" onClick={() => setRecording(isRecording ? null : id)}>
              {isRecording ? t('取消') : t('录制')}
            </button>
            <button
              type="button"
              className="settings-button"
              disabled={!sc}
              onClick={() => setShortcut(id, null)}
            >
              {t('清除')}
            </button>
          </div>
        );
      })}
    </div>
  );
}
