import { create } from 'zustand';
import type { DesignRuntimeStatus, DesignViewStatus } from '../lib/design';

interface DesignState {
  activePagePath: string;
  studioTitle: string;
  runtime: DesignRuntimeStatus;
  view: DesignViewStatus;
  error: string;
  initialize: (language?: 'zh' | 'en') => Promise<void>;
  openPage: (pagePath: string, title: string) => Promise<boolean>;
  setRuntime: (runtime: DesignRuntimeStatus) => void;
  setView: (view: DesignViewStatus) => void;
}

const emptyRuntime: DesignRuntimeStatus = {
  state: 'idle',
  url: '',
  error: '',
  version: '0.15.1',
  repository: 'https://github.com/linyeping/Metis',
  sourceRoot: '',
  logs: [],
};

const emptyView: DesignViewStatus = {
  state: 'hidden',
  url: '',
  title: '',
  visible: false,
  loading: false,
  error: '',
  bounds: null,
};

export const useDesignStore = create<DesignState>((set, get) => ({
  activePagePath: '',
  studioTitle: '',
  runtime: emptyRuntime,
  view: emptyView,
  error: '',

  initialize: async language => {
    if (!window.metis?.designRuntimeStart) return;
    try {
      const runtime = await window.metis.designRuntimeStart(language === 'en' ? 'en' : 'zh-CN');
      set({ runtime, error: runtime.state === 'ready' ? '' : runtime.error });
      if (runtime.state === 'ready') {
        const state = get();
        if (!state.activePagePath) {
          await state.openPage('/', language === 'en' ? 'Home' : '主页');
        }
      }
    } catch (error) {
      set({
        runtime: { ...get().runtime, state: 'error', error: error instanceof Error ? error.message : String(error) },
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },

  openPage: async (pagePath, title) => {
    if (!window.metis?.designViewLoadPage || !pagePath) return false;
    set({ activePagePath: pagePath, studioTitle: title, error: '' });
    const result = await window.metis.designViewLoadPage(pagePath);
    if (!result.ok) {
      set({ error: result.error || '无法打开 Design 页面。' });
      return false;
    }
    return true;
  },

  setRuntime: runtime => set({ runtime, error: runtime.state === 'error' ? runtime.error : get().error }),
  setView: view => set({ view, error: view.state === 'error' ? view.error : get().error }),
}));
