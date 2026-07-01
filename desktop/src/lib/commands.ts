import type { Language, RuntimeSettings, SectionId, SessionMeta, ThemeName, Workspace } from './types';
import { themeLabels, themeNames } from './themes';

export interface CommandItem {
  id: string;
  title: string;
  subtitle?: string;
  keywords: string[];
  group: string;
  refreshAfterRun?: boolean;
  run: () => Promise<void> | void;
}

export interface ModelPreset {
  id: string;
  provider: string;
  backend: string;
  baseUrl: string;
  model: string;
  note: string;
}

export interface CommandContext {
  language: Language;
  sessions: SessionMeta[];
  workspaces: Workspace[];
  activeSessionId: string | null;
  activeWorkspaceId: string;
  theme: ThemeName;
  sidebarOpen: boolean;
  rightRailOpen: boolean;
  settings: RuntimeSettings | null;
  actions: {
    createSession: () => Promise<void>;
    switchSession: (sessionId: string, mode?: SessionMeta['mode']) => Promise<void>;
    switchWorkspace: (workspaceId: string) => Promise<void>;
    openFolder: () => Promise<void>;
    setTheme: (theme: ThemeName) => void;
    openModelPicker: () => void;
    openSettings: () => void;
    clearConversation: () => Promise<void>;
    rewindConversation: () => Promise<void>;
    exportChat: () => Promise<void>;
    toggleLanguage: () => void;
    setSidebarOpen: (open: boolean) => void;
    setRightRailOpen: (open: boolean) => void;
    setActiveSection: (section: SectionId) => void;
  };
}

export const modelPresets: ModelPreset[] = [
  {
    id: 'deepseek-v4-flash',
    provider: 'DeepSeek',
    backend: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-v4-flash',
    note: 'Fast default · 1M context',
  },
  {
    id: 'deepseek-v4-pro',
    provider: 'DeepSeek',
    backend: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    model: 'deepseek-v4-pro',
    note: 'Higher quality · 1M context',
  },
  {
    id: 'kimi-k2-6',
    provider: 'Kimi',
    backend: 'kimi',
    baseUrl: 'https://api.moonshot.cn/v1',
    model: 'kimi-k2.6',
    note: 'Moonshot · 256K context',
  },
  {
    id: 'glm-5-1',
    provider: 'Zhipu GLM',
    backend: 'zhipu-glm',
    baseUrl: 'https://open.bigmodel.cn/api/coding/paas/v4',
    model: 'glm-5.1',
    note: 'Coding API · 200K context',
  },
  {
    id: 'qwen3-coder-plus',
    provider: 'Bailian / Qwen',
    backend: 'bailian',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3-coder-plus',
    note: 'Coder · 1M context',
  },
  {
    id: 'qwen3-max',
    provider: 'Bailian / Qwen',
    backend: 'bailian',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen3-max',
    note: 'General · 256K context',
  },
  {
    id: 'gpt-4.1',
    provider: 'OpenAI',
    backend: 'openai',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4.1',
    note: 'Large context',
  },
  {
    id: 'gpt-4.1-mini',
    provider: 'OpenAI',
    backend: 'openai',
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4.1-mini',
    note: 'Balanced',
  },
  {
    id: 'claude-sonnet-4',
    provider: 'Anthropic',
    backend: 'anthropic',
    baseUrl: 'https://api.anthropic.com/v1/messages',
    model: 'claude-sonnet-4-20250514',
    note: 'Sonnet',
  },
  {
    id: 'claude-opus-4',
    provider: 'Anthropic',
    backend: 'anthropic',
    baseUrl: 'https://api.anthropic.com/v1/messages',
    model: 'claude-opus-4-20250514',
    note: 'Opus',
  },
  {
    id: 'gemini-2-flash',
    provider: 'Gemini',
    backend: 'gemini',
    baseUrl: '',
    model: 'gemini-2.0-flash',
    note: 'Gemini Flash',
  },
];

export function buildCommands(context: CommandContext): CommandItem[] {
  const zh = context.language === 'zh';
  const commands: CommandItem[] = [
    {
      id: 'session.new',
      title: zh ? '新建会话' : 'New chat',
      subtitle: zh ? '打开空白对话，发送后创建记录' : 'Open a blank chat; create it when you send',
      keywords: ['new', 'chat', 'session', '新建', '会话'],
      group: zh ? '会话' : 'Session',
      run: context.actions.createSession,
    },
    {
      id: 'workspace.open',
      title: zh ? '打开文件夹' : 'Open folder',
      subtitle: zh ? '新建或切换到一个工作区' : 'Create or switch to a workspace',
      keywords: ['folder', 'workspace', 'open', '打开', '文件夹', '工作区'],
      group: zh ? '工作区' : 'Workspace',
      run: context.actions.openFolder,
    },
    {
      id: 'model.open',
      title: zh ? '切换模型' : 'Switch model',
      subtitle: context.settings?.model || 'DeepSeek / OpenAI / Anthropic / Gemini',
      keywords: ['model', 'provider', 'llm', '模型', '厂商'],
      group: zh ? '模型' : 'Model',
      run: context.actions.openModelPicker,
    },
    {
      id: 'settings.open',
      title: zh ? '打开设置' : 'Open settings',
      keywords: ['settings', 'config', '设置', '配置'],
      group: zh ? '应用' : 'App',
      run: context.actions.openSettings,
    },
    {
      id: 'layout.sidebar.toggle',
      title: context.sidebarOpen ? (zh ? '隐藏侧栏' : 'Hide sidebar') : zh ? '显示侧栏' : 'Show sidebar',
      subtitle: zh ? '切换工作区与会话侧栏' : 'Toggle workspace and chat sidebar',
      keywords: ['sidebar', 'layout', 'side', '侧栏', '工作区'],
      group: zh ? '布局' : 'Layout',
      run: () => context.actions.setSidebarOpen(!context.sidebarOpen),
    },
    {
      id: 'layout.rightRail.toggle',
      title: context.rightRailOpen ? (zh ? '隐藏右栏' : 'Hide right rail') : zh ? '显示右栏' : 'Show right rail',
      subtitle: zh ? '切换文件/工具/网页预览栏' : 'Toggle file, tool, and web preview rail',
      keywords: ['right', 'rail', 'preview', '右栏', '预览'],
      group: zh ? '布局' : 'Layout',
      run: () => context.actions.setRightRailOpen(!context.rightRailOpen),
    },
    {
      id: 'conversation.clear',
      title: zh ? '清空当前对话' : 'Clear conversation',
      subtitle: zh ? '开始一条空白会话' : 'Start with an empty chat',
      keywords: ['clear', 'reset', 'conversation', '清空', '重置'],
      group: zh ? '会话' : 'Session',
      run: context.actions.clearConversation,
    },
    {
      id: 'conversation.rewind',
      title: zh ? '回滚到上一个 checkpoint' : 'Rewind to checkpoint',
      subtitle: zh ? '选择回滚对话、文件，或两者都回滚' : 'Choose conversation, files, or both',
      keywords: ['rewind', 'checkpoint', 'undo', '回滚', '快照', '撤回'],
      group: zh ? '会话' : 'Session',
      run: context.actions.rewindConversation,
    },
    {
      id: 'conversation.export',
      title: zh ? '导出对话' : 'Export chat',
      subtitle: context.activeSessionId ? 'Markdown' : zh ? '当前没有会话' : 'No active session',
      keywords: ['export', 'download', 'markdown', '导出', '下载'],
      group: zh ? '会话' : 'Session',
      run: context.actions.exportChat,
    },
    {
      id: 'language.toggle',
      title: zh ? '切换语言' : 'Switch language',
      subtitle: zh ? 'English' : '中文',
      keywords: ['language', 'locale', '语言', '中文', 'english'],
      group: zh ? '应用' : 'App',
      run: context.actions.toggleLanguage,
    },
  ];

  for (const section of sectionCommands(context.language)) {
    commands.push({
      ...section,
      run: () => context.actions.setActiveSection(section.section),
    });
  }

  for (const theme of themeNames) {
    const label = themeLabels[theme][context.language];
    commands.push({
      id: `theme.${theme}`,
      title: zh ? `切换主题：${label}` : `Theme: ${label}`,
      subtitle: context.theme === theme ? (zh ? '当前主题' : 'Current theme') : undefined,
      keywords: ['theme', 'appearance', '主题', label],
      group: zh ? '主题' : 'Theme',
      run: () => context.actions.setTheme(theme),
    });
  }

  for (const workspace of context.workspaces.slice(0, 12)) {
    commands.push({
      id: `workspace.${workspace.id}`,
      title: zh ? `切换工作区：${workspace.name || '当前工作区'}` : `Workspace: ${workspace.name || 'Current workspace'}`,
      subtitle: workspace.id === context.activeWorkspaceId ? (zh ? '当前工作区' : 'Current workspace') : workspace.path,
      keywords: ['workspace', 'folder', 'switch', '工作区', '文件夹', workspace.name, workspace.path],
      group: zh ? '工作区' : 'Workspace',
      run: () => context.actions.switchWorkspace(workspace.id),
    });
  }

  for (const session of context.sessions.slice(0, 12)) {
    commands.push({
      id: `session.${session.id}`,
      title: session.title || (zh ? '未命名会话' : 'Untitled chat'),
      subtitle: zh ? `${session.messageCount} 条消息` : `${session.messageCount} messages`,
      keywords: ['switch', 'session', 'chat', '切换', '会话', session.title],
      group: zh ? '最近会话' : 'Recent chats',
      run: () => context.actions.switchSession(session.id, session.mode),
    });
  }

  return commands;
}

export function commandScore(command: CommandItem, query: string): number {
  const needle = query.trim().toLowerCase();
  if (!needle) return 1;
  const haystack = [command.title, command.subtitle || '', command.group, ...command.keywords]
    .join(' ')
    .toLowerCase();
  if (haystack.includes(needle)) return 100 + needle.length;
  return fuzzyScore(needle, haystack);
}

function fuzzyScore(needle: string, haystack: string): number {
  let score = 0;
  let cursor = 0;
  let streak = 0;
  for (const char of needle) {
    const index = haystack.indexOf(char, cursor);
    if (index === -1) return -1;
    streak = index === cursor ? streak + 1 : 0;
    score += 5 + streak * 2 - Math.min(index - cursor, 8);
    cursor = index + 1;
  }
  return score;
}

function sectionCommands(language: Language): Array<CommandItem & { section: SectionId }> {
  const zh = language === 'zh';
  return [
    {
      id: 'section.skills',
      title: zh ? '跳到技能' : 'Go to skills',
      keywords: ['skills', 'section', '技能'],
      group: zh ? '导航' : 'Navigation',
      section: 'skills',
      run: () => undefined,
    },
    {
      id: 'section.mcp',
      title: zh ? '跳到连接器' : 'Go to connectors',
      keywords: ['mcp', 'connector', '连接器'],
      group: zh ? '导航' : 'Navigation',
      section: 'mcp',
      run: () => undefined,
    },
    {
      id: 'section.store',
      title: zh ? '跳到 Store' : 'Go to Store',
      keywords: ['store', 'marketplace', 'skill', 'mcp', '商店', '市场'],
      group: zh ? '导航' : 'Navigation',
      section: 'store',
      run: () => undefined,
    },
    {
      id: 'section.computer',
      title: zh ? '跳到操控' : 'Go to computer control',
      keywords: ['computer', 'desktop', 'control', '操控'],
      group: zh ? '导航' : 'Navigation',
      section: 'computer',
      run: () => undefined,
    },
    {
      id: 'section.cron',
      title: zh ? '跳到自动化' : 'Go to automation',
      keywords: ['cron', 'automation', 'schedule', '自动化', '定时'],
      group: zh ? '导航' : 'Navigation',
      section: 'cron',
      run: () => undefined,
    },
  ];
}
