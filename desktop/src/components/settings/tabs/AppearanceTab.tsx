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
  uiFontSize: number;
}

const themeGroups: Array<{ mode: AppearanceMode; label: string }> = [
  { mode: 'light', label: '白天' },
  { mode: 'dark', label: '夜晚' },
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
  uiFontSize,
}: AppearanceTabProps) {
  const t = useT();
  const currentModeTheme = appearanceMode === 'light' ? lightTheme : darkTheme;

  return (
    <div className="settings-card-grid appearance-settings-grid">
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
        <div className="appearance-preview-grid" role="group" aria-label={t('外观模式')}>
          {themeGroups.map(group => {
            const optionTheme = group.mode === 'light' ? lightTheme : darkTheme;
            const palette = themes[optionTheme];
            const Icon = group.mode === 'light' ? Sun : Moon;
            return (
              <button
                type="button"
                className="appearance-preview-card"
                key={group.mode}
                data-active={appearanceMode === group.mode}
                onClick={() => onAppearanceModeChange(group.mode)}
              >
                <span
                  className="appearance-preview-window"
                  style={{
                    background: palette['--bg'],
                    borderColor: palette['--border'],
                  }}
                  aria-hidden="true"
                >
                  <span
                    className="appearance-preview-sidebar"
                    style={{ background: palette['--bg-secondary'], borderColor: palette['--border'] }}
                  />
                  <span className="appearance-preview-content">
                    <i style={{ background: palette['--text-faint'] }} />
                    <i style={{ background: palette['--accent'] }} />
                    <i style={{ background: palette['--bg-tertiary'] }} />
                  </span>
                </span>
                <strong>
                  <Icon size={14} />
                  {t(group.label)}
                </strong>
                <small>{themeLabels[optionTheme][language]}</small>
              </button>
            );
          })}
        </div>
        <div className="appearance-theme-panel">
          <label>
            <span>{t('当前模式主题')}</span>
            <select value={currentModeTheme} onChange={event => onThemeChange(event.target.value as ThemeName)}>
              {themeNames
                .filter(name => themeMode[name] === appearanceMode)
                .map(name => (
                  <option value={name} key={name}>
                    {themeLabels[name][language]}
                  </option>
                ))}
            </select>
          </label>
        </div>
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
              <option value={option.value} key={option.value}>
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
