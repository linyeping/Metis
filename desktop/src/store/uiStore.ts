import { create } from 'zustand';
import type { FileChangePreview, FileChangeSummary } from '../lib/diffPreview';
import type { FileChangeRevertItem, FontFamily, Language, SectionId, SettingsSection, ThemeName } from '../lib/types';
import { themeMode } from '../lib/themes';

type AppearanceMode = 'light' | 'dark';

type RightRailMode = 'files' | 'tool' | 'web' | 'diff' | 'activity';

export type WorkspaceCardId = 'web' | 'terminal' | 'files' | 'diff' | 'activity' | 'plan' | 'tool';
export type WorkspaceCardColumnId = 'left' | 'middle' | 'right';
export type WorkspaceCardVisibility = Record<WorkspaceCardId, boolean>;

// A card toggle shortcut. The primary modifier is always the platform key
// (Ctrl on Windows/Linux, ⌘ on macOS); `shift` is the optional extra. null
// means the card has no shortcut.
export interface WorkspaceCardShortcut {
  key: string;
  shift?: boolean;
}
export type WorkspaceCardShortcuts = Partial<Record<WorkspaceCardId, WorkspaceCardShortcut | null>>;

export const DEFAULT_WORKSPACE_CARD_SHORTCUTS: WorkspaceCardShortcuts = {
  web: { key: 'p', shift: true },
  diff: { key: 'd', shift: true },
  terminal: { key: '`' },
  files: { key: 'f', shift: true },
};

// Cards that support a toggle shortcut (in display order).
export const SHORTCUTTABLE_CARDS: WorkspaceCardId[] = ['web', 'diff', 'terminal', 'files'];
export type WorkspaceCardColumnWidths = {
  left: number;
  middle: number;
};
export type WorkspaceCardRowSplits = Record<WorkspaceCardColumnId, number>;

interface ToolPreview {
  title: string;
  content: string;
}

export interface WebPreviewTab {
  id: string;
  url: string;
  title: string;
  zoom: number;
  loading: boolean;
  error: string;
}

export type ToastType = 'error' | 'warning' | 'info' | 'success';

export interface ToastNotice {
  id: string;
  title: string;
  description: string;
  action?: string;
  type: ToastType;
  duration?: number;
  sessionId?: string | null;
}

type ToastInput = Omit<ToastNotice, 'id'>;

export type AppDialogTone = 'default' | 'danger';

export type AppDialogIcon = 'info' | 'warning' | 'trash' | 'external';

export interface AppDialogChoice {
  value: string;
  label: string;
  description?: string;
}

export interface AppDialogResult {
  confirmed: boolean;
  choice: string;
}

export interface AppDialogRequest {
  id: string;
  title: string;
  message: string;
  details?: string;
  choices?: AppDialogChoice[];
  defaultChoice?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  tone: AppDialogTone;
  icon: AppDialogIcon;
}

type AppDialogInput = Omit<AppDialogRequest, 'id' | 'tone' | 'icon'> & {
  tone?: AppDialogTone;
  icon?: AppDialogIcon;
};

interface UiState {
  activeSection: SectionId;
  codeFontSize: number;
  fontFamily: FontFamily;
  language: Language;
  theme: ThemeName;
  appearanceMode: AppearanceMode;
  lightTheme: ThemeName;
  darkTheme: ThemeName;
  uiFontSize: number;
  settingsOpen: boolean;
  settingsSection: SettingsSection;
  contextDetailsOpen: boolean;
  commandOpen: boolean;
  modelPickerOpen: boolean;
  workspaceMenuOpen: boolean;
  previewFrozenSrc: string | null;
  rightRailOpen: boolean;
  rightRailWidth: number;
  rightRailMode: RightRailMode;
  sidebarOpen: boolean;
  sidebarWidth: number;
  sideChatOpen: boolean;
  sideChatWidth: number;
  terminalOpen: boolean;
  terminalHeight: number;
  workspaceCardVisibility: WorkspaceCardVisibility;
  workspaceCardColumnWidths: WorkspaceCardColumnWidths;
  workspaceCardRowSplits: WorkspaceCardRowSplits;
  previewPath: string | null;
  toolPreview: ToolPreview | null;
  diffPreview: FileChangePreview | null;
  diffSummary: FileChangeSummary | null;
  activeDiffFileId: string;
  diffRevertSummaryId: string;
  diffRevertItems: FileChangeRevertItem[];
  workspaceRefreshNonce: number;
  expandedToolCards: Set<string>;
  webPreviewTabs: WebPreviewTab[];
  activeWebPreviewId: string;
  webPreviewUrl: string;
  toasts: ToastNotice[];
  appDialog: AppDialogRequest | null;
  setActiveSection: (section: SectionId) => void;
  setCodeFontSize: (size: number) => void;
  setFontFamily: (fontFamily: FontFamily) => void;
  setLanguage: (language: Language) => void;
  setTheme: (theme: ThemeName) => void;
  setAppearanceMode: (mode: AppearanceMode) => void;
  setUiFontSize: (size: number) => void;
  setSettingsOpen: (open: boolean) => void;
  setSettingsSection: (section: SettingsSection) => void;
  setContextDetailsOpen: (open: boolean) => void;
  toggleContextDetailsOpen: () => void;
  setCommandOpen: (open: boolean) => void;
  setModelPickerOpen: (open: boolean) => void;
  setWorkspaceMenuOpen: (open: boolean) => void;
  setPreviewFrozenSrc: (src: string | null) => void;
  setRightRailOpen: (open: boolean) => void;
  setRightRailWidth: (width: number) => void;
  setRightRailMode: (mode: RightRailMode) => void;
  setSidebarOpen: (open: boolean) => void;
  setSidebarWidth: (width: number) => void;
  setSideChatOpen: (open: boolean) => void;
  setSideChatWidth: (width: number) => void;
  setTerminalOpen: (open: boolean) => void;
  setTerminalHeight: (height: number) => void;
  setWorkspaceCardVisible: (cardId: WorkspaceCardId, visible: boolean) => void;
  workspaceCardShortcuts: WorkspaceCardShortcuts;
  setWorkspaceCardShortcut: (cardId: WorkspaceCardId, shortcut: WorkspaceCardShortcut | null) => void;
  toggleWorkspaceCard: (cardId: WorkspaceCardId) => void;
  setWorkspaceCardColumnWidths: (widths: WorkspaceCardColumnWidths) => void;
  setWorkspaceCardRowSplit: (columnId: WorkspaceCardColumnId, percent: number) => void;
  setPreviewPath: (path: string | null) => void;
  setToolPreview: (preview: ToolPreview) => void;
  setDiffPreview: (preview: FileChangePreview) => void;
  setDiffReview: (summary: FileChangeSummary, activeFileId?: string) => void;
  setActiveDiffFile: (fileId: string) => void;
  setDiffRevertItems: (summaryId: string, items: FileChangeRevertItem[]) => void;
  refreshWorkspaceView: () => void;
  setToolCardExpanded: (cardId: string, expanded: boolean) => void;
  clearExpandedToolCards: () => void;
  setWebPreviewUrl: (url: string) => void;
  activateWebPreviewTab: (id: string) => void;
  closeWebPreviewTab: (id: string) => void;
  updateWebPreviewTab: (id: string, patch: Partial<Omit<WebPreviewTab, 'id'>>) => void;
  setWebPreviewZoom: (id: string, zoom: number) => void;
  pushToast: (toast: ToastInput) => string;
  dismissToast: (id: string) => void;
  clearToastsForSession: (sessionId: string | null) => void;
  requestConfirm: (dialog: AppDialogInput) => Promise<boolean>;
  requestChoice: (dialog: AppDialogInput) => Promise<AppDialogResult>;
  closeAppDialog: (confirmed: boolean, choice?: string) => void;
}

function storedTheme(): ThemeName {
  const value = localStorage.getItem('metis.theme') as ThemeName | null;
  return value && themeMode[value] ? value : 'cathedral-obsidian';
}

function storedLightTheme(): ThemeName {
  const value = localStorage.getItem('metis.lightTheme') as ThemeName | null;
  if (value && themeMode[value] === 'light') return value;
  const legacy = storedTheme();
  return themeMode[legacy] === 'light' ? legacy : 'templar-silver';
}

function storedDarkTheme(): ThemeName {
  const value = localStorage.getItem('metis.darkTheme') as ThemeName | null;
  if (value && themeMode[value] === 'dark') return value;
  const legacy = storedTheme();
  return themeMode[legacy] === 'dark' ? legacy : 'cathedral-obsidian';
}

function storedAppearanceMode(): AppearanceMode {
  const value = localStorage.getItem('metis.appearanceMode');
  if (value === 'light' || value === 'dark') return value;
  // 兼容旧版：按旧 theme 的明暗推断当前模式
  return themeMode[storedTheme()] ?? 'dark';
}

function initialActiveTheme(): ThemeName {
  return storedAppearanceMode() === 'light' ? storedLightTheme() : storedDarkTheme();
}

function storedLanguage(): Language {
  const value = localStorage.getItem('metis.language') as Language | null;
  return value || 'zh';
}

function storedFontFamily(): FontFamily {
  const value = localStorage.getItem('metis.fontFamily') as FontFamily | null;
  return value || 'official-sans';
}

function storedNumber(key: string, fallback: number, min: number, max: number): number {
  const value = Number(localStorage.getItem(key));
  if (!Number.isFinite(value)) return fallback;
  return clampNumber(Math.round(value), min, max);
}

const defaultWorkspaceCardVisibility: WorkspaceCardVisibility = {
  web: true,
  terminal: false,
  files: true,
  diff: true,
  activity: true,
  plan: true,
  tool: false,
};

const defaultWorkspaceCardColumnWidths: WorkspaceCardColumnWidths = {
  left: 40,
  middle: 30,
};

const defaultWorkspaceCardRowSplits: WorkspaceCardRowSplits = {
  left: 50,
  middle: 50,
  right: 50,
};

let dialogResolver: ((result: AppDialogResult) => void) | null = null;
let dialogCounter = 0;
let toastCounter = 0;
let webTabCounter = 0;
const MIN_WEB_ZOOM = 0.5;
const MAX_WEB_ZOOM = 2;
const MAX_EXPANDED_TOOL_CARDS = 500;

function webTabTitle(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.hostname || url;
  } catch {
    return url;
  }
}

function normalizeWebZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.round(Math.min(Math.max(value, MIN_WEB_ZOOM), MAX_WEB_ZOOM) * 100) / 100;
}

function limitedExpandedToolCards(value: Set<string>): Set<string> {
  if (value.size <= MAX_EXPANDED_TOOL_CARDS) return value;
  return new Set(Array.from(value).slice(-MAX_EXPANDED_TOOL_CARDS));
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function storedJson<T>(key: string, fallback: T, validate: (value: unknown) => value is T): T {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '');
    return validate(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function storedWorkspaceCardVisibility(): WorkspaceCardVisibility {
  return {
    ...defaultWorkspaceCardVisibility,
    ...storedJson<Partial<WorkspaceCardVisibility>>('metis.workspaceCardVisibility', {}, isRecord),
    tool: false,
  };
}

function storedWorkspaceCardColumnWidths(): WorkspaceCardColumnWidths {
  const value = storedJson<Partial<WorkspaceCardColumnWidths>>('metis.workspaceCardColumnWidths', {}, isRecord);
  return normalizeWorkspaceCardColumnWidths({
    left: typeof value.left === 'number' ? value.left : defaultWorkspaceCardColumnWidths.left,
    middle: typeof value.middle === 'number' ? value.middle : defaultWorkspaceCardColumnWidths.middle,
  });
}

function storedWorkspaceCardRowSplits(): WorkspaceCardRowSplits {
  const value = storedJson<Partial<WorkspaceCardRowSplits>>('metis.workspaceCardRowSplits', {}, isRecord);
  const normalizeStoredSplit = (columnId: WorkspaceCardColumnId, fallback: number, legacyDefault: number): number => {
    const stored = typeof value[columnId] === 'number' ? value[columnId] : fallback;
    const migrated = stored === legacyDefault ? 50 : stored;
    return clampNumber(Math.round(migrated), 24, 76);
  };
  return {
    left: normalizeStoredSplit('left', defaultWorkspaceCardRowSplits.left, 58),
    middle: normalizeStoredSplit('middle', defaultWorkspaceCardRowSplits.middle, 54),
    right: normalizeStoredSplit('right', defaultWorkspaceCardRowSplits.right, 52),
  };
}

function normalizeWorkspaceCardColumnWidths(widths: WorkspaceCardColumnWidths): WorkspaceCardColumnWidths {
  const left = clampNumber(Math.round(widths.left), 22, 58);
  const middleMax = 82 - left;
  return {
    left,
    middle: clampNumber(Math.round(widths.middle), 20, Math.max(20, middleMax)),
  };
}

function persistWorkspaceCardVisibility(value: WorkspaceCardVisibility): WorkspaceCardVisibility {
  localStorage.setItem('metis.workspaceCardVisibility', JSON.stringify(value));
  return value;
}

function storedWorkspaceCardShortcuts(): WorkspaceCardShortcuts {
  const merged: WorkspaceCardShortcuts = { ...DEFAULT_WORKSPACE_CARD_SHORTCUTS };
  try {
    const raw = localStorage.getItem('metis.workspaceCardShortcuts');
    if (raw) {
      const parsed = JSON.parse(raw) as WorkspaceCardShortcuts;
      for (const id of SHORTCUTTABLE_CARDS) {
        if (id in parsed) merged[id] = parsed[id];
      }
    }
  } catch {
    // ignore malformed storage
  }
  return merged;
}

function persistWorkspaceCardShortcuts(value: WorkspaceCardShortcuts): WorkspaceCardShortcuts {
  localStorage.setItem('metis.workspaceCardShortcuts', JSON.stringify(value));
  return value;
}

function hasVisibleWorkspaceCard(value: WorkspaceCardVisibility): boolean {
  return Object.values(value).some(Boolean);
}

function cardForRightRailMode(mode: RightRailMode): WorkspaceCardId {
  if (mode === 'web') return 'web';
  if (mode === 'diff') return 'diff';
  if (mode === 'activity') return 'activity';
  if (mode === 'tool') return 'tool';
  return 'files';
}

const initialWorkspaceCardVisibility = storedWorkspaceCardVisibility();

export const useUiStore = create<UiState>(set => ({
  activeSection: 'chat',
  codeFontSize: storedNumber('metis.codeFontSize', 12, 11, 16),
  fontFamily: storedFontFamily(),
  language: storedLanguage(),
  theme: initialActiveTheme(),
  appearanceMode: storedAppearanceMode(),
  lightTheme: storedLightTheme(),
  darkTheme: storedDarkTheme(),
  uiFontSize: storedNumber('metis.uiFontSize', 14, 12, 18),
  settingsOpen: false,
  settingsSection: 'appearance',
  contextDetailsOpen: false,
  commandOpen: false,
  modelPickerOpen: false,
  workspaceMenuOpen: false,
  previewFrozenSrc: null,
  rightRailOpen: hasVisibleWorkspaceCard(initialWorkspaceCardVisibility),
  rightRailWidth: 780,
  rightRailMode: 'files',
  sidebarOpen: true,
  sidebarWidth: storedNumber('metis.sidebarWidth', 284, 220, 520),
  sideChatOpen: false,
  sideChatWidth: storedNumber('metis.sideChatWidth', 320, 286, 320),
  terminalOpen: false,
  terminalHeight: 220,
  workspaceCardVisibility: initialWorkspaceCardVisibility,
  workspaceCardShortcuts: storedWorkspaceCardShortcuts(),
  workspaceCardColumnWidths: storedWorkspaceCardColumnWidths(),
  workspaceCardRowSplits: storedWorkspaceCardRowSplits(),
  previewPath: null,
  toolPreview: null,
  diffPreview: null,
  diffSummary: null,
  activeDiffFileId: '',
  diffRevertSummaryId: '',
  diffRevertItems: [],
  workspaceRefreshNonce: 0,
  expandedToolCards: new Set(),
  webPreviewTabs: [],
  activeWebPreviewId: '',
  webPreviewUrl: '',
  toasts: [],
  appDialog: null,
  setActiveSection: activeSection => set({ activeSection }),
  setCodeFontSize: size => {
    const codeFontSize = clampNumber(Math.round(size), 11, 16);
    localStorage.setItem('metis.codeFontSize', String(codeFontSize));
    set({ codeFontSize });
  },
  setFontFamily: fontFamily => {
    localStorage.setItem('metis.fontFamily', fontFamily);
    set({ fontFamily });
  },
  setLanguage: language => {
    localStorage.setItem('metis.language', language);
    set({ language });
  },
  setTheme: theme => {
    // 选一个主题 = 把它存进它所属的模式槽，并切到那个模式。浅/深各记各的。
    const mode: AppearanceMode = themeMode[theme] ?? 'dark';
    localStorage.setItem('metis.theme', theme);
    localStorage.setItem('metis.appearanceMode', mode);
    localStorage.setItem(mode === 'light' ? 'metis.lightTheme' : 'metis.darkTheme', theme);
    set(state => ({
      theme,
      appearanceMode: mode,
      lightTheme: mode === 'light' ? theme : state.lightTheme,
      darkTheme: mode === 'dark' ? theme : state.darkTheme,
    }));
  },
  setAppearanceMode: mode => {
    // 切换白天/夜晚 = 切到该模式下各自保存的主题。
    localStorage.setItem('metis.appearanceMode', mode);
    set(state => {
      const theme = mode === 'light' ? state.lightTheme : state.darkTheme;
      localStorage.setItem('metis.theme', theme);
      return { appearanceMode: mode, theme };
    });
  },
  setUiFontSize: size => {
    const uiFontSize = clampNumber(Math.round(size), 12, 18);
    localStorage.setItem('metis.uiFontSize', String(uiFontSize));
    set({ uiFontSize });
  },
  setSettingsOpen: settingsOpen => set({ settingsOpen }),
  setSettingsSection: settingsSection => set({ settingsSection }),
  setContextDetailsOpen: contextDetailsOpen => set({ contextDetailsOpen }),
  toggleContextDetailsOpen: () => set(state => ({ contextDetailsOpen: !state.contextDetailsOpen })),
  setCommandOpen: commandOpen => set({ commandOpen }),
  setModelPickerOpen: modelPickerOpen => set({ modelPickerOpen }),
  setWorkspaceMenuOpen: workspaceMenuOpen => set({ workspaceMenuOpen }),
  setPreviewFrozenSrc: previewFrozenSrc => set({ previewFrozenSrc }),
  setRightRailOpen: rightRailOpen => set({ rightRailOpen }),
  setRightRailWidth: rightRailWidth => set({ rightRailWidth: Math.min(Math.max(rightRailWidth, 420), 1180) }),
  setRightRailMode: rightRailMode =>
    set(state => {
      const cardId = cardForRightRailMode(rightRailMode);
      return {
        rightRailMode,
        rightRailOpen: true,
        workspaceCardVisibility: persistWorkspaceCardVisibility({
          ...state.workspaceCardVisibility,
          [cardId]: true,
        }),
      };
    }),
  setSidebarOpen: sidebarOpen => set({ sidebarOpen }),
  setSidebarWidth: width => {
    const sidebarWidth = clampNumber(Math.round(width), 220, 520);
    localStorage.setItem('metis.sidebarWidth', String(sidebarWidth));
    set({ sidebarWidth });
  },
  setSideChatOpen: sideChatOpen => set({ sideChatOpen }),
  setSideChatWidth: width => {
    const sideChatWidth = clampNumber(Math.round(width), 286, 320);
    localStorage.setItem('metis.sideChatWidth', String(sideChatWidth));
    set({ sideChatWidth });
  },
  setTerminalOpen: terminalOpen =>
    set(state => {
      const workspaceCardVisibility = persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        terminal: terminalOpen,
      });
      return {
        terminalOpen,
        rightRailOpen: terminalOpen ? true : hasVisibleWorkspaceCard(workspaceCardVisibility),
        workspaceCardVisibility,
      };
    }),
  setTerminalHeight: terminalHeight => set({ terminalHeight: Math.min(Math.max(terminalHeight, 140), 420) }),
  setWorkspaceCardShortcut: (cardId, shortcut) =>
    set(state => ({
      workspaceCardShortcuts: persistWorkspaceCardShortcuts({ ...state.workspaceCardShortcuts, [cardId]: shortcut }),
    })),
  setWorkspaceCardVisible: (cardId, visible) =>
    set(state => {
      const workspaceCardVisibility = persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        [cardId]: visible,
      });
      return {
        rightRailOpen: visible ? true : hasVisibleWorkspaceCard(workspaceCardVisibility),
        terminalOpen: cardId === 'terminal' ? visible : state.terminalOpen,
        workspaceCardVisibility,
      };
    }),
  toggleWorkspaceCard: cardId =>
    set(state => {
      const visible = !state.workspaceCardVisibility[cardId];
      const workspaceCardVisibility = persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        [cardId]: visible,
      });
      return {
        rightRailOpen: visible ? true : hasVisibleWorkspaceCard(workspaceCardVisibility),
        terminalOpen: cardId === 'terminal' ? visible : state.terminalOpen,
        workspaceCardVisibility,
      };
    }),
  setWorkspaceCardColumnWidths: widths => {
    const workspaceCardColumnWidths = normalizeWorkspaceCardColumnWidths(widths);
    localStorage.setItem('metis.workspaceCardColumnWidths', JSON.stringify(workspaceCardColumnWidths));
    set({ workspaceCardColumnWidths });
  },
  setWorkspaceCardRowSplit: (columnId, percent) =>
    set(state => {
      const workspaceCardRowSplits = {
        ...state.workspaceCardRowSplits,
        [columnId]: clampNumber(Math.round(percent), 24, 76),
      };
      localStorage.setItem('metis.workspaceCardRowSplits', JSON.stringify(workspaceCardRowSplits));
      return { workspaceCardRowSplits };
    }),
  setPreviewPath: previewPath =>
    set(state => ({
      previewPath,
      rightRailMode: 'files',
      rightRailOpen: previewPath ? true : undefined,
      workspaceCardVisibility: persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        files: previewPath ? true : state.workspaceCardVisibility.files,
      }),
    })),
  setToolPreview: toolPreview =>
    set(state => ({
      toolPreview,
      rightRailMode: 'activity',
      rightRailOpen: true,
      workspaceCardVisibility: persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        activity: true,
        tool: false,
      }),
    })),
  setDiffPreview: diffPreview =>
    set(state => ({
      activeDiffFileId: diffPreview.id,
      diffPreview,
      diffSummary: null,
      rightRailMode: 'diff',
      rightRailOpen: true,
      workspaceCardVisibility: persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        diff: true,
      }),
    })),
  setDiffReview: (diffSummary, activeFileId) => {
    const activePreview =
      diffSummary.files.find(file => file.preview.id === activeFileId)?.preview ||
      diffSummary.files[0]?.preview ||
      diffSummary.changes[0] ||
      null;
    set(state => ({
      activeDiffFileId: activePreview?.id || '',
      diffPreview: activePreview,
      diffSummary,
      rightRailMode: 'diff',
      rightRailOpen: true,
      workspaceCardVisibility: persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        diff: true,
      }),
    }));
  },
  setActiveDiffFile: activeDiffFileId =>
    set(state => ({
      activeDiffFileId,
      rightRailMode: 'diff',
      rightRailOpen: true,
      workspaceCardVisibility: persistWorkspaceCardVisibility({
        ...state.workspaceCardVisibility,
        diff: true,
      }),
    })),
  setDiffRevertItems: (diffRevertSummaryId, diffRevertItems) => set({ diffRevertSummaryId, diffRevertItems }),
  refreshWorkspaceView: () => set(state => ({ workspaceRefreshNonce: state.workspaceRefreshNonce + 1 })),
  setToolCardExpanded: (cardId, expanded) =>
    set(state => {
      const id = cardId.trim();
      if (!id) return {};
      const expandedToolCards = new Set(state.expandedToolCards);
      if (expanded) {
        expandedToolCards.add(id);
      } else {
        expandedToolCards.delete(id);
      }
      return { expandedToolCards: limitedExpandedToolCards(expandedToolCards) };
    }),
  clearExpandedToolCards: () => set({ expandedToolCards: new Set() }),
  setWebPreviewUrl: webPreviewUrl =>
    set(state => {
      const url = webPreviewUrl.trim();
      if (!url) {
        return {
          webPreviewUrl: '',
          activeWebPreviewId: '',
          rightRailMode: 'web',
          rightRailOpen: true,
          workspaceCardVisibility: persistWorkspaceCardVisibility({
            ...state.workspaceCardVisibility,
            web: true,
          }),
        };
      }
      const existing = state.webPreviewTabs.find(tab => tab.url === url);
      if (existing) {
        return {
          activeWebPreviewId: existing.id,
          webPreviewUrl: existing.url,
          rightRailMode: 'web',
          rightRailOpen: true,
          workspaceCardVisibility: persistWorkspaceCardVisibility({
            ...state.workspaceCardVisibility,
            web: true,
          }),
        };
      }
      const tab = {
        id: `web-tab-${Date.now()}-${++webTabCounter}`,
        error: '',
        loading: false,
        title: webTabTitle(url),
        url,
        zoom: 1,
      };
      return {
        activeWebPreviewId: tab.id,
        webPreviewTabs: [...state.webPreviewTabs, tab].slice(-8),
        webPreviewUrl: tab.url,
        rightRailMode: 'web',
        rightRailOpen: true,
        workspaceCardVisibility: persistWorkspaceCardVisibility({
          ...state.workspaceCardVisibility,
          web: true,
        }),
      };
    }),
  activateWebPreviewTab: id =>
    set(state => {
      const tab = state.webPreviewTabs.find(item => item.id === id);
      if (!tab) return {};
      return {
        activeWebPreviewId: tab.id,
        webPreviewUrl: tab.url,
        rightRailMode: 'web',
        rightRailOpen: true,
        workspaceCardVisibility: persistWorkspaceCardVisibility({
          ...state.workspaceCardVisibility,
          web: true,
        }),
      };
    }),
  closeWebPreviewTab: id =>
    set(state => {
      const index = state.webPreviewTabs.findIndex(tab => tab.id === id);
      if (index === -1) return {};
      const nextTabs = state.webPreviewTabs.filter(tab => tab.id !== id);
      if (state.activeWebPreviewId !== id) {
        return { webPreviewTabs: nextTabs };
      }
      const nextActive = nextTabs[Math.min(index, nextTabs.length - 1)] || null;
      return {
        activeWebPreviewId: nextActive?.id || '',
        webPreviewTabs: nextTabs,
        webPreviewUrl: nextActive?.url || '',
      };
    }),
  updateWebPreviewTab: (id, patch) =>
    set(state => {
      const tabs = state.webPreviewTabs.map(tab => {
        if (tab.id !== id) return tab;
        const nextZoom = patch.zoom === undefined ? tab.zoom : normalizeWebZoom(patch.zoom);
        return {
          ...tab,
          ...patch,
          title: patch.title || tab.title,
          url: patch.url || tab.url,
          zoom: nextZoom,
        };
      });
      const active = tabs.find(tab => tab.id === state.activeWebPreviewId);
      return {
        webPreviewTabs: tabs,
        webPreviewUrl: active?.url || state.webPreviewUrl,
      };
    }),
  setWebPreviewZoom: (id, zoom) =>
    set(state => ({
      webPreviewTabs: state.webPreviewTabs.map(tab => (tab.id === id ? { ...tab, zoom: normalizeWebZoom(zoom) } : tab)),
    })),
  pushToast: toast => {
    const id = `toast-${Date.now()}-${++toastCounter}`;
    set(state => ({
      toasts: [
        ...state.toasts.filter(
          item =>
            item.title !== toast.title ||
            item.description !== toast.description ||
            item.type !== toast.type ||
            item.sessionId !== toast.sessionId,
        ),
        { ...toast, id },
      ].slice(-5),
    }));
    return id;
  },
  dismissToast: id => set(state => ({ toasts: state.toasts.filter(toast => toast.id !== id) })),
  clearToastsForSession: sessionId =>
    set(state => ({
      toasts: state.toasts.filter(toast => !toast.sessionId || toast.sessionId !== sessionId),
    })),
  requestConfirm: dialog =>
    new Promise(resolve => {
      dialogResolver?.({ confirmed: false, choice: '' });
      dialogResolver = result => resolve(result.confirmed);
      set({
        appDialog: {
          ...dialog,
          id: `app-dialog-${++dialogCounter}`,
          tone: dialog.tone ?? 'default',
          icon: dialog.icon ?? (dialog.tone === 'danger' ? 'warning' : 'info'),
        },
      });
    }),
  requestChoice: dialog =>
    new Promise(resolve => {
      dialogResolver?.({ confirmed: false, choice: '' });
      dialogResolver = resolve;
      set({
        appDialog: {
          ...dialog,
          id: `app-dialog-${++dialogCounter}`,
          tone: dialog.tone ?? 'default',
          icon: dialog.icon ?? (dialog.tone === 'danger' ? 'warning' : 'info'),
        },
      });
    }),
  closeAppDialog: (confirmed, choice = '') => {
    const resolve = dialogResolver;
    dialogResolver = null;
    set({ appDialog: null });
    resolve?.({ confirmed, choice });
  },
}));
