import { useEffect } from 'react';
import { themes, themeMode } from '../lib/themes';
import type { FontFamily } from '../lib/types';
import { useUiStore } from '../store/uiStore';

const fontStacks: Record<FontFamily, string> = {
  'official-sans': "'Anthropic Sans Web Text', 'Inter', 'Segoe UI', 'Microsoft YaHei UI', system-ui, sans-serif",
  system: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  'microsoft-yahei': "'Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI', system-ui, sans-serif",
  inter: "'Inter', 'Segoe UI', system-ui, sans-serif",
};

export function useTheme(): void {
  const theme = useUiStore(state => state.theme);
  const codeFontSize = useUiStore(state => state.codeFontSize);
  const fontFamily = useUiStore(state => state.fontFamily);
  const uiFontSize = useUiStore(state => state.uiFontSize);

  useEffect(() => {
    const values = themes[theme];
    const mode = themeMode[theme] ?? 'dark';
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.mode = mode;
    for (const [key, value] of Object.entries(values)) {
      root.style.setProperty(key, value);
    }
    void window.metis?.setNativeTheme?.(mode);
  }, [theme]);

  useEffect(() => {
    document.documentElement.style.setProperty('--font-sans', fontStacks[fontFamily] || fontStacks['official-sans']);
  }, [fontFamily]);

  useEffect(() => {
    const root = document.documentElement;
    root.style.setProperty('--ui-font-size', `${uiFontSize}px`);
    root.style.setProperty('--ui-font-size-sm', `${Math.max(10, uiFontSize - 2)}px`);
    root.style.setProperty('--ui-font-size-xs', `${Math.max(9, uiFontSize - 3)}px`);
    root.style.setProperty('--ui-font-size-lg', `${uiFontSize + 1}px`);
    root.style.setProperty('--code-font-size', `${codeFontSize}px`);
  }, [codeFontSize, uiFontSize]);
}
