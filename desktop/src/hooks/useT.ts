import { useCallback } from 'react';
import { translate } from '../lib/i18n';
import { useUiStore } from '../store/uiStore';

// 组件内用：const t = useT(); ... t('中文')。订阅 language，切换语言即时刷新。
export function useT(): (zh: string) => string {
  const language = useUiStore(state => state.language);
  return useCallback((zh: string) => translate(zh, language), [language]);
}
