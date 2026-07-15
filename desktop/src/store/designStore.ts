import { create } from 'zustand';
import type { CreateDesignProjectInput, DesignProjectSummary, DesignRuntimeStatus, DesignSystemSummary, DesignViewStatus } from '../lib/design';

type DesignPage = 'home' | 'studio';

interface DesignState {
  page: DesignPage;
  activeProjectId: string;
  activePagePath: string;
  studioTitle: string;
  projects: DesignProjectSummary[];
  designSystems: DesignSystemSummary[];
  runtime: DesignRuntimeStatus;
  view: DesignViewStatus;
  loadingProjects: boolean;
  creatingProject: boolean;
  error: string;
  initialize: (language?: 'zh' | 'en') => Promise<void>;
  refreshProjects: () => Promise<void>;
  refreshDesignSystems: () => Promise<void>;
  createProject: (input: CreateDesignProjectInput) => Promise<boolean>;
  openProject: (projectId: string) => Promise<boolean>;
  openPage: (pagePath: string, title: string) => Promise<boolean>;
  showHome: () => void;
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
  page: 'home',
  activeProjectId: '',
  activePagePath: '',
  studioTitle: '',
  projects: [],
  designSystems: [],
  runtime: emptyRuntime,
  view: emptyView,
  loadingProjects: false,
  creatingProject: false,
  error: '',

  initialize: async language => {
    if (!window.metis?.designRuntimeStart) return;
    try {
      const runtime = await window.metis.designRuntimeStart(language === 'en' ? 'en' : 'zh-CN');
      set({ runtime, error: runtime.state === 'ready' ? '' : runtime.error });
      if (runtime.state === 'ready') {
        await Promise.all([get().refreshProjects(), get().refreshDesignSystems()]);
      }
    } catch (error) {
      set({
        runtime: { ...get().runtime, state: 'error', error: error instanceof Error ? error.message : String(error) },
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },

  refreshProjects: async () => {
    if (!window.metis?.designProjectsList) return;
    set({ loadingProjects: true });
    const result = await window.metis.designProjectsList();
    set({
      projects: result.ok ? result.projects.sort((a, b) => b.updatedAt - a.updatedAt) : get().projects,
      loadingProjects: false,
      error: result.ok ? '' : (result.error || '无法读取 Design 项目。'),
    });
  },

  refreshDesignSystems: async () => {
    if (!window.metis?.designSystemsList) return;
    const result = await window.metis.designSystemsList();
    set({
      designSystems: result.ok ? result.systems : get().designSystems,
      error: result.ok ? get().error : (result.error || '无法读取 Design System。'),
    });
  },

  createProject: async input => {
    if (!window.metis?.designProjectCreate) return false;
    set({ creatingProject: true, error: '' });
    const result = await window.metis.designProjectCreate(input);
    if (!result.ok || !result.project) {
      set({ creatingProject: false, error: result.error || '无法创建 Design 项目。' });
      return false;
    }
    set(state => ({
      creatingProject: false,
      projects: [result.project!, ...state.projects.filter(project => project.id !== result.project!.id)],
    }));
    return get().openProject(result.project.id);
  },

  openProject: async projectId => {
    if (!window.metis?.designViewLoad || !projectId) return false;
    set({ activeProjectId: projectId, activePagePath: '', studioTitle: '', page: 'studio', error: '' });
    const result = await window.metis.designViewLoad(projectId);
    if (!result.ok) {
      set({ error: result.error || '无法打开 Design Studio。' });
      return false;
    }
    return true;
  },

  openPage: async (pagePath, title) => {
    if (!window.metis?.designViewLoadPage || !pagePath) return false;
    set({ activeProjectId: '', activePagePath: pagePath, studioTitle: title, page: 'studio', error: '' });
    const result = await window.metis.designViewLoadPage(pagePath);
    if (!result.ok) {
      set({ error: result.error || '无法打开 Design 页面。' });
      return false;
    }
    return true;
  },

  showHome: () => {
    void window.metis?.designViewSetLayout?.({ visible: false });
    set({ page: 'home', activeProjectId: '', activePagePath: '', studioTitle: '', error: '' });
    void Promise.all([get().refreshProjects(), get().refreshDesignSystems()]);
  },

  setRuntime: runtime => set({ runtime, error: runtime.state === 'error' ? runtime.error : get().error }),
  setView: view => set({ view, error: view.state === 'error' ? view.error : get().error }),
}));
