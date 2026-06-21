import { memo } from 'react';
import { Keyboard, Moon, Palette, Sun, Type } from 'lucide-react';
import { themeLabels, themeMode, themeNames, themes } from '../../../lib/themes';
import type { FontFamily, Language, ThemeName } from '../../../lib/types';
import { tr } from '../../../lib/i18n';
import { FontSizeControl } from '../FontSizeControl';
import { ShortcutSettings } from '../ShortcutSettings';
import { fontOptions } from '../settingsShared';
import { useT } from '../../../hooks/useT';

type AppearanceMode = 'light' | 'dark';

interface AppearanceTabProps {
  appearanceMode: AppearanceMode;
  codeFontSize: number;
  darkTheme: ThemeName;
  fontFamily: FontFamily;
  language: Language;
  lightTheme: ThemeName;
  onAppearanceModeChange: (value: AppearanceMode) => void;
  onCodeFontSizeChange: (value: number) => void;
  onFontFamilyChange: (value: FontFamily) => void;
  onLanguageChange: (value: Language) => void;
  onThemeChange: (value: ThemeName) => void;
  onUiFontSizeChange: (value: number) => void;
  theme: ThemeName;
  uiFontSize: number;
}

const themeGroups: Array<{ mode: AppearanceMode; label: string }> = [
  { mode: 'light', label: '浅色（白天）' },
  { mode: 'dark', label: '深色（夜晚）' },
];

export const AppearanceTab = memo(function AppearanceTab({
  appearanceMode,
  codeFontSize,
  darkTheme,
  fontFamily,
  language,
  lightTheme,
  onAppearanceModeChange,
  onCodeFontSizeChange,
  onFontFamilyChange,
  onLanguageChange,
  onThemeChange,
  onUiFontSizeChange,
  theme,
  uiFontSize,
}: AppearanceTabProps) {
  const t = useT();
  return (
    <div className="settings-card-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <Palette size={16} className="section-icon" />
          <h3>{t('主题与语言')}</h3>
        </div>
        <label>
          <span>{tr(language, 'language')}</span>
          <select value={language} onChange={event => onLanguageChange(event.target.value as Language)}>
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
        </label>
        <div className="appearance-mode-toggle" role="group" aria-label={`${t('白天')} / ${t('夜晚')}`}>
          <button type="button" data-active={appearanceMode === 'light'} onClick={() => onAppearanceModeChange('light')}>
            <Sun size={14} />
            {t('白天')}
          </button>
          <button type="button" data-active={appearanceMode === 'dark'} onClick={() => onAppearanceModeChange('dark')}>
            <Moon size={14} />
            {t('夜晚')}
          </button>
        </div>
        {themeGroups.map(group => {
          const savedForMode = group.mode === 'light' ? lightTheme : darkTheme;
          return (
            <div className="theme-group" key={group.mode}>
              <p className="theme-group-label">{t(group.label)}</p>
              <div className="theme-grid">
                {themeNames
                  .filter(name => themeMode[name] === group.mode)
                  .map(name => {
                    const palette = themes[name];
                    return (
                      <button
                        type="button"
                        key={name}
                        data-active={savedForMode === name}
                        data-current={theme === name}
                        onClick={() => onThemeChange(name)}
                      >
                        <span
                          className="theme-dot"
                          style={{
                            background: `conic-gradient(from 130deg, ${palette['--accent']} 0 33%, ${palette['--accent-ink']} 33% 66%, ${palette['--bg-tertiary']} 66% 100%)`,
                            borderColor: palette['--border'],
                          }}
                        />
                        <strong>{themeLabels[name][language]}</strong>
                      </button>
                    );
                  })}
              </div>
            </div>
          );
        })}
      </section>
      <section className="settings-section">
        <div className="settings-section-header">
          <Type size={16} className="section-icon" />
          <h3>{t('字体')}</h3>
        </div>
        <label>
          <span>{t('字体')}</span>
          <select value={fontFamily} onChange={event => onFontFamilyChange(event.target.value as FontFamily)}>
            {fontOptions.map(option => (
              <option value={option.value}>
                {t(option.label)}
              </option>
            ))}
          </select>
          <small>{t(fontOptions.find(option => option.value === fontFamily)?.hint ?? '')}</small>
        </label>
        <FontSizeControl
          description={t('调整 Metis UI 使用的基础字号，聊天、侧栏、按钮和设置页会一起变大。')}
          label={t('UI 字号')}
          max={18}
          min={12}
          onChange={onUiFontSizeChange}
          value={uiFontSize}
        />
        <FontSizeControl
          description={t('调整代码块、终端、Diff、工具输出和文件预览的基础字号。')}
          label={t('代码字号')}
          max={16}
          min={11}
          onChange={onCodeFontSizeChange}
          value={codeFontSize}
        />
      </section>
      <section className="settings-section">
        <div className="settings-section-header">
          <Keyboard size={16} className="section-icon" />
          <h3>{t('快捷键')}</h3>
        </div>
        <ShortcutSettings />
      </section>
    </div>
  );
});
