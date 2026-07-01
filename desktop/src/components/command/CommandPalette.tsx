import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Command as CommandIcon,
  FileText,
  Gauge,
  ListChecks,
  Pin,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import { commandScore, buildCommands } from '../../lib/commands';
import {
  exportSession,
  getChatRuns,
  getDeskGoalLog,
  getDeskStatus,
  getProviderStatus,
  resetConversation,
  searchSessions,
} from '../../lib/api';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import type { CommandItem } from '../../lib/commands';
import type {
  ChatRunPayload,
  ChatMessage,
  AppMode,
  DeskGoalLogEntry,
  DeskStatusPayload,
  DiagnosticsPayload,
  ProviderStatusPayload,
  RuntimeStatus,
  RuntimeSettings,
  SearchResult,
  SectionId,
} from '../../lib/types';

interface CommandPaletteProps {
  settings: RuntimeSettings | null;
  settingsChanged: () => Promise<void>;
}

type CommandCenterTab = 'search' | 'system' | 'runs' | 'provider';

const COMMAND_CENTER_TABS: CommandCenterTab[] = ['search', 'system', 'runs', 'provider'];

export function CommandPalette({ settings, settingsChanged }: CommandPaletteProps) {
  const open = useUiStore(state => state.commandOpen);
  const setOpen = useUiStore(state => state.setCommandOpen);
  const setModelPickerOpen = useUiStore(state => state.setModelPickerOpen);
  const setSettingsOpen = useUiStore(state => state.setSettingsOpen);
  const language = useUiStore(state => state.language);
  const setLanguage = useUiStore(state => state.setLanguage);
  const theme = useUiStore(state => state.theme);
  const setTheme = useUiStore(state => state.setTheme);
  const appMode = useUiStore(state => state.appMode);
  const setAppMode = useUiStore(state => state.setAppMode);
  const setActiveSection = useUiStore(state => state.setActiveSection);
  const sidebarOpen = useUiStore(state => state.sidebarOpen);
  const setSidebarOpen = useUiStore(state => state.setSidebarOpen);
  const rightRailOpen = useUiStore(state => state.rightRailOpen);
  const setRightRailOpen = useUiStore(state => state.setRightRailOpen);
  const sessions = useSessionStore(state => state.sessions);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const startDraftSession = useSessionStore(state => state.startDraftSession);
  const rememberModeState = useSessionStore(state => state.rememberModeState);
  const selectSession = useSessionStore(state => state.selectSession);
  const selectWorkspace = useSessionStore(state => state.selectWorkspace);
  const openWorkspacePath = useSessionStore(state => state.openWorkspacePath);
  const loadSessions = useSessionStore(state => state.load);
  const loadChatSession = useChatStore(state => state.loadSession);
  const clearChat = useChatStore(state => state.clearLocal);
  const rewindConversation = useChatStore(state => state.rewindLatest);
  const messages = useChatStore(state => state.messages);
  const runtimeStatus = useChatStore(state => state.runtimeStatus);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [activeTab, setActiveTab] = useState<CommandCenterTab>('search');
  const [searchHits, setSearchHits] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsPayload | null>(null);
  const [providerStatus, setProviderStatus] = useState<ProviderStatusPayload | null>(null);
  const [runs, setRuns] = useState<ChatRunPayload[]>([]);
  const [deskStatus, setDeskStatus] = useState<DeskStatusPayload | null>(null);
  const [goalLog, setGoalLog] = useState<DeskGoalLogEntry[]>([]);
  const [centerLoading, setCenterLoading] = useState(false);
  const [centerError, setCenterError] = useState('');
  const [diagnosticsMessage, setDiagnosticsMessage] = useState('');
  const [savingDiagnostics, setSavingDiagnostics] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const refreshCenter = useCallback(async (silent = false) => {
    if (!silent) setCenterLoading(true);
    setCenterError('');
    try {
      const [diagnosticsResult, providerResult, runsResult, deskResult, goalLogResult] = await Promise.allSettled([
        window.metis?.diagnostics() ?? Promise.resolve(null),
        getProviderStatus(),
        getChatRuns(),
        getDeskStatus(),
        getDeskGoalLog(10),
      ]);

      if (diagnosticsResult.status === 'fulfilled') setDiagnostics(diagnosticsResult.value);
      if (providerResult.status === 'fulfilled') setProviderStatus(providerResult.value);
      if (runsResult.status === 'fulfilled') setRuns(runsResult.value.runs);
      if (deskResult.status === 'fulfilled') setDeskStatus(deskResult.value);
      if (goalLogResult.status === 'fulfilled') setGoalLog(goalLogResult.value);

      const firstError = [diagnosticsResult, providerResult, runsResult, deskResult, goalLogResult].find(
        result => result.status === 'rejected',
      );
      if (firstError?.status === 'rejected') {
        setCenterError(firstError.reason instanceof Error ? firstError.reason.message : String(firstError.reason));
      }
      setLastUpdatedAt(Date.now());
    } finally {
      if (!silent) setCenterLoading(false);
    }
  }, []);

  const moveTab = useCallback((delta: number) => {
    setActiveTab(current => {
      const index = COMMAND_CENTER_TABS.indexOf(current);
      const next = (index + delta + COMMAND_CENTER_TABS.length) % COMMAND_CENTER_TABS.length;
      return COMMAND_CENTER_TABS[next];
    });
    setSelected(0);
  }, []);

  const chooseTab = useCallback((tab: CommandCenterTab) => {
    setActiveTab(tab);
    setSelected(0);
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [setOpen]);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setSelected(0);
    setActiveTab('search');
    setSearchHits([]);
    setDiagnosticsMessage('');
    void refreshCenter();
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [open, refreshCenter]);

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setInterval(() => void refreshCenter(true), 4500);
    return () => window.clearInterval(timer);
  }, [open, refreshCenter]);

  useEffect(() => {
    const value = query.trim();
    if (!open || value.length < 2) {
      setSearchHits([]);
      setSearchLoading(false);
      return undefined;
    }

    const handle = window.setTimeout(() => {
      setSearchLoading(true);
      void searchSessions(value)
        .then(setSearchHits)
        .catch(() => setSearchHits([]))
        .finally(() => setSearchLoading(false));
    }, 180);
    return () => window.clearTimeout(handle);
  }, [open, query]);

  const openSession = useCallback(
    async (sessionId: string, mode?: AppMode | string) => {
      const targetMode = toAppMode(mode || '') || toAppMode(sessions.find(session => session.id === sessionId)?.mode || '') || appMode;
      if (targetMode !== appMode) {
        rememberModeState(appMode);
        useSessionStore.setState({ activeSessionId: null });
        clearChat();
        setAppMode(targetMode);
      }
      setActiveSection('chat');
      await selectSession(sessionId);
      await loadChatSession(sessionId, { force: true });
    },
    [appMode, clearChat, loadChatSession, rememberModeState, selectSession, sessions, setActiveSection, setAppMode],
  );

  const commands = useMemo(
    () =>
      buildCommands({
        language,
        sessions,
        workspaces,
        activeSessionId,
        activeWorkspaceId,
        theme,
        sidebarOpen,
        rightRailOpen,
        settings,
        actions: {
          createSession: async () => {
            startDraftSession();
            clearChat();
          },
          switchSession: openSession,
          switchWorkspace: async workspaceId => {
            await selectWorkspace(workspaceId);
            await loadChatSession(useSessionStore.getState().activeSessionId);
          },
          openFolder: async () => {
            const path = await window.metis.pickFolder();
            if (!path) return;
            await openWorkspacePath(path);
            await loadChatSession(useSessionStore.getState().activeSessionId);
          },
          setTheme,
          openModelPicker: () => setModelPickerOpen(true),
          openSettings: () => setSettingsOpen(true),
          clearConversation: async () => {
            const result = await resetConversation();
            await loadSessions();
            await loadChatSession(result.sessionId);
          },
          rewindConversation,
          exportChat: async () => {
            const sessionId = useSessionStore.getState().activeSessionId;
            if (!sessionId) return;
            const content = await exportSession(sessionId, 'markdown');
            downloadText(content, `metis-chat-${new Date().toISOString().slice(0, 10)}.md`);
          },
          toggleLanguage: () => setLanguage(language === 'zh' ? 'en' : 'zh'),
          setSidebarOpen,
          setRightRailOpen,
          setActiveSection: (section: SectionId) => setActiveSection(section),
        },
      }),
    [
      activeSessionId,
      activeWorkspaceId,
      language,
      clearChat,
      loadChatSession,
      loadSessions,
      openWorkspacePath,
      openSession,
      rewindConversation,
      selectSession,
      selectWorkspace,
      sessions,
      settings,
      setActiveSection,
      setLanguage,
      setModelPickerOpen,
      setRightRailOpen,
      setSettingsOpen,
      setSidebarOpen,
      setTheme,
      startDraftSession,
      rightRailOpen,
      sidebarOpen,
      theme,
      workspaces,
    ],
  );

  const searchCommands = useMemo(() => {
    const sessionWorkspace = new Map(sessions.map(session => [session.id, session.workspaceId]));
    const sessionModes = new Map(sessions.map(session => [session.id, session.mode]));
    const workspaceNames = new Map(workspaces.map(workspace => [workspace.id, workspace.name]));
    return searchHits.slice(0, 6).map<CommandItem>(result => {
      const workspaceId = result.workspaceId || sessionWorkspace.get(result.sessionId) || '';
      const workspaceName = result.workspaceName || workspaceNames.get(workspaceId) || (language === 'zh' ? '当前工作区' : 'Current workspace');
      const targetMode = result.mode || sessionModes.get(result.sessionId);
      return {
        id: `search.${result.sessionId}`,
        title: result.title || (language === 'zh' ? '未命名会话' : 'Untitled chat'),
        subtitle: `${workspaceName} · ${snippetText(result.snippet) || (language === 'zh' ? '全文命中' : 'Full-text match')}`,
        keywords: [query, result.title, result.snippet, workspaceName],
        group: language === 'zh' ? '全文搜索' : 'Search',
        run: () => openSession(result.sessionId, targetMode),
      };
    });
  }, [language, openSession, query, searchHits, sessions, workspaces]);

  const filtered = useMemo(() => {
    const commandMatches = commands
      .map(command => ({ command, score: commandScore(command, query) }))
      .filter(item => item.score >= 0)
      .sort((a, b) => b.score - a.score)
      .map(item => item.command);
    return [...searchCommands, ...commandMatches].slice(0, 18);
  }, [commands, query, searchCommands]);

  const toolStats = useMemo(() => summarizeToolStats(messages), [messages]);

  useEffect(() => {
    setSelected(0);
  }, [query]);

  const run = async (command: CommandItem) => {
    setOpen(false);
    await command.run();
    if (command.refreshAfterRun) {
      await settingsChanged();
    }
  };

  const saveDiagnostics = async () => {
    setSavingDiagnostics(true);
    setDiagnosticsMessage('');
    try {
      const result = await window.metis?.saveDiagnosticsBundle();
      if (!result || result.canceled) {
        setDiagnosticsMessage(language === 'zh' ? '已取消生成诊断包' : 'Diagnostics bundle canceled');
      } else {
        if (result.diagnostics) setDiagnostics(result.diagnostics);
        setDiagnosticsMessage(`${language === 'zh' ? '诊断包已保存' : 'Diagnostics saved'}: ${result.path || ''}`);
      }
    } finally {
      setSavingDiagnostics(false);
    }
  };

  const handleQueryChange = (value: string) => {
    setQuery(value);
    if (value.trim()) setActiveTab('search');
  };

  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          className="command-layer"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.14 } }}
          transition={{ duration: 0.16 }}
        >
      <motion.section
        className="command-palette command-center"
        data-active-tab={activeTab}
        initial={{ y: -12, scale: 0.97, opacity: 0 }}
        animate={{ y: 0, scale: 1, opacity: 1 }}
        exit={{ y: -8, scale: 0.98, opacity: 0, transition: { duration: 0.14, ease: [0.16, 1, 0.3, 1] } }}
        transition={{ type: 'spring', stiffness: 400, damping: 30 }}
        onKeyDown={event => {
          if (event.key === 'Escape') {
            event.preventDefault();
            setOpen(false);
          } else if ((event.ctrlKey || event.metaKey) && /^[1-4]$/.test(event.key)) {
            event.preventDefault();
            const nextTab = COMMAND_CENTER_TABS[Number(event.key) - 1];
            if (nextTab) chooseTab(nextTab);
          } else if (event.altKey && event.key === 'ArrowRight') {
            event.preventDefault();
            moveTab(1);
          } else if (event.altKey && event.key === 'ArrowLeft') {
            event.preventDefault();
            moveTab(-1);
          } else if (activeTab === 'search' && event.key === 'ArrowDown') {
            event.preventDefault();
            setSelected(index => (filtered.length ? Math.min(index + 1, filtered.length - 1) : 0));
          } else if (activeTab === 'search' && event.key === 'ArrowUp') {
            event.preventDefault();
            setSelected(index => Math.max(index - 1, 0));
          } else if (activeTab === 'search' && event.key === 'Enter' && filtered[selected]) {
            event.preventDefault();
            void run(filtered[selected]);
          }
        }}
      >
        <header className="command-center-header">
          <div className="command-center-title">
            <CommandIcon size={18} />
            <div>
              <strong>Command Center</strong>
              <span>{language === 'zh' ? '搜索、状态、后台任务和诊断入口' : 'Search, status, background work, and diagnostics'}</span>
            </div>
          </div>
          <div className="command-search">
            <Search size={17} />
            <input
              ref={inputRef}
              value={query}
              placeholder={language === 'zh' ? '搜索命令、会话、主题...' : 'Search commands, chats, themes...'}
              onChange={event => handleQueryChange(event.target.value)}
            />
            <kbd>Esc</kbd>
          </div>
          <nav className="command-center-tabs" aria-label="Command Center">
            {COMMAND_CENTER_TABS.map(tab => (
              <button key={tab} type="button" data-active={activeTab === tab} onClick={() => chooseTab(tab)}>
                {tabIcon(tab)}
                <span>{tabLabel(tab, language)}</span>
              </button>
            ))}
          </nav>
          <div className="command-center-pins" aria-label="Pinned diagnostics actions">
            <button type="button" onClick={() => void refreshCenter()} disabled={centerLoading}>
              <RefreshCw className={centerLoading ? 'spin' : undefined} size={13} />
              <span>{centerLoading ? '刷新中' : '刷新'}</span>
            </button>
            <button type="button" onClick={() => void window.metis?.openLog()}>
              <FileText size={13} />
              <span>日志</span>
            </button>
            <button type="button" onClick={() => void saveDiagnostics()} disabled={savingDiagnostics}>
              <Pin size={13} />
              <span>{savingDiagnostics ? '生成中' : '诊断包'}</span>
            </button>
            <button type="button" onClick={() => setModelPickerOpen(true)}>
              <ShieldCheck size={13} />
              <span>模型</span>
            </button>
            <em>实时刷新 · {lastUpdatedAt ? formatRelativeTime(lastUpdatedAt) : '等待数据'}</em>
          </div>
        </header>

        <div className="command-center-body">
          <CommandCenterOverview
            diagnostics={diagnostics}
            providerStatus={providerStatus}
            runs={runs}
            runtimeStatus={runtimeStatus}
            toolStats={toolStats}
            centerLoading={centerLoading}
          />
          {activeTab === 'search' && (
            <div className="command-results">
              {filtered.length === 0 && (
                <div className="command-empty">
                  <CommandIcon size={18} />
                  {searchLoading ? (language === 'zh' ? '搜索中...' : 'Searching...') : language === 'zh' ? '没有匹配命令' : 'No matching command'}
                </div>
              )}
              {filtered.map((command, index) => (
                <button
                  key={command.id}
                  type="button"
                  className="command-item"
                  data-active={index === selected}
                  onMouseEnter={() => setSelected(index)}
                  onClick={() => void run(command)}
                >
                  <span>
                    <strong>{command.title}</strong>
                    {command.subtitle && <em>{command.subtitle}</em>}
                  </span>
                  <small>{command.group}</small>
                </button>
              ))}
            </div>
          )}

          {activeTab === 'system' && (
            <div className="command-center-panel command-system-panel">
              <StatusGrid
                items={[
                  ['后端', diagnostics?.backend.status || (centerLoading ? 'loading' : 'unknown')],
                  ['端口', diagnostics?.backend.port ? String(diagnostics.backend.port) : '-'],
                  ['终端', diagnostics?.terminal.backend || '-'],
                  ['Desk', deskStatus?.available ? deskStatus.goalStatus || 'idle' : deskStatus?.error || 'unavailable'],
                ]}
              />
              <section className="command-center-card">
                <header>
                  <span>后端日志</span>
                  <code>{diagnostics?.backend.logPath || '等待诊断数据'}</code>
                </header>
                <pre>{diagnostics?.backend.logTail || '暂无后端日志。'}</pre>
                <div className="command-center-actions">
                  <button type="button" onClick={() => void refreshCenter()} disabled={centerLoading}>
                    {centerLoading ? '刷新中...' : '刷新'}
                  </button>
                  <button type="button" onClick={() => void window.metis?.openLog()}>
                    打开日志
                  </button>
                  <button type="button" onClick={() => void saveDiagnostics()} disabled={savingDiagnostics}>
                    {savingDiagnostics ? '生成中...' : '生成诊断包'}
                  </button>
                </div>
                {diagnosticsMessage && <p>{diagnosticsMessage}</p>}
                {centerError && <p className="command-center-error">{centerError}</p>}
              </section>
              {goalLog.length > 0 && (
                <section className="command-center-card">
                  <header>
                    <span>最近自动化日志</span>
                    <small>{goalLog.length} 条</small>
                  </header>
                  <ol className="command-log-list">
                    {goalLog.map((entry, index) => (
                      <li key={`${entry.ts}-${entry.action}-${index}`}>
                        <strong>{entry.action || entry.status || 'log'}</strong>
                        <span>{entry.detail || formatTimestamp(entry.ts)}</span>
                      </li>
                    ))}
                  </ol>
                </section>
              )}
            </div>
          )}

          {activeTab === 'runs' && (
            <div className="command-center-panel">
              <ToolStatsGrid stats={toolStats} />
              {runs.length === 0 ? (
                <div className="command-center-empty">
                  <ListChecks size={18} />
                  <span>{centerLoading ? '加载后台任务...' : '暂无后台任务'}</span>
                </div>
              ) : (
                <div className="command-run-list">
                  {runs.slice(0, 12).map(runItem => (
                    <article key={runItem.runId || runItem.id} className="command-run-row" data-status={runItem.status || 'unknown'}>
                      <span className="command-run-dot" />
                      <div>
                        <strong>{runItem.phase || runItem.status || 'run'}</strong>
                        <small>{runItem.sessionId || 'local'} · {formatTimestamp(runItem.updatedAt || runItem.createdAt)}</small>
                      </div>
                      <em>{runItem.cancelRequested ? 'canceling' : runItem.status || '-'}</em>
                    </article>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === 'provider' && (
            <div className="command-center-panel">
              <StatusGrid
                items={[
                  ['Provider', providerStatus?.active?.displayName || providerStatus?.settings.providerId || settings?.providerId || '-'],
                  ['模型', providerStatus?.settings.model || settings?.model || '-'],
                  ['API Key', providerStatus?.settings.hasApiKey || settings?.hasApiKey ? '已配置' : '未配置'],
                  ['健康', providerStatus?.active ? (providerStatus.active.ok ? '正常' : providerStatus.active.code || '异常') : 'unknown'],
                ]}
              />
              <section className="command-center-card">
                <header>
                  <span>Provider 健康</span>
                  {providerStatus?.active?.ok ? <CheckCircle2 size={14} /> : <AlertCircle size={14} />}
                </header>
                <p>{providerStatus?.active?.message || providerStatus?.active?.title || '等待 Provider 状态。'}</p>
                {providerStatus?.active?.hint && <p>{providerStatus.active.hint}</p>}
                <div className="command-center-actions">
                  <button type="button" onClick={() => setModelPickerOpen(true)}>
                    切换模型
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setSettingsOpen(true);
                      useUiStore.getState().setSettingsSection('model');
                    }}
                  >
                    打开设置
                  </button>
                  <button type="button" onClick={() => void refreshCenter()} disabled={centerLoading}>
                    刷新状态
                  </button>
                </div>
              </section>
            </div>
          )}
        </div>
      </motion.section>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

interface ToolStats {
  total: number;
  running: number;
  success: number;
  error: number;
  categories: Array<{ category: string; count: number; running: number; error: number }>;
}

function CommandCenterOverview({
  centerLoading,
  diagnostics,
  providerStatus,
  runs,
  runtimeStatus,
  toolStats,
}: {
  centerLoading: boolean;
  diagnostics: DiagnosticsPayload | null;
  providerStatus: ProviderStatusPayload | null;
  runs: ChatRunPayload[];
  runtimeStatus: RuntimeStatus | null;
  toolStats: ToolStats;
}) {
  const activeRuns = runs.filter(run => run.status === 'running' || run.status === 'queued' || run.status === 'canceling').length;
  return (
    <section className="command-center-overview" aria-label="运行状态总览">
      <article data-tone={diagnostics?.backend.status === 'ready' ? 'ok' : centerLoading ? 'working' : 'warn'}>
        <Server size={14} />
        <span>Backend</span>
        <strong>{diagnostics?.backend.status || (centerLoading ? 'loading' : 'unknown')}</strong>
      </article>
      <article data-tone={providerStatus?.active?.ok ? 'ok' : 'warn'}>
        <ShieldCheck size={14} />
        <span>Provider</span>
        <strong>{providerStatus?.settings.model || providerStatus?.settings.providerId || '-'}</strong>
      </article>
      <article data-tone={activeRuns > 0 ? 'working' : 'ok'}>
        <Activity size={14} />
        <span>Runs</span>
        <strong>{activeRuns > 0 ? `${activeRuns} active` : `${runs.length} total`}</strong>
      </article>
      <article data-tone={runtimeStatus?.severity === 'error' ? 'danger' : toolStats.running > 0 ? 'working' : 'ok'}>
        <Gauge size={14} />
        <span>Runtime</span>
        <strong>{runtimeStatus?.display || (toolStats.total ? `tools ${toolStats.success}/${toolStats.total}` : 'idle')}</strong>
      </article>
    </section>
  );
}

function ToolStatsGrid({ stats }: { stats: ToolStats }) {
  return (
    <section className="command-center-card command-tool-stats">
      <header>
        <span>工具聚合统计</span>
        <small>{stats.total} 次工具调用</small>
      </header>
      {stats.total === 0 ? (
        <p>当前会话还没有工具调用。</p>
      ) : (
        <div className="command-tool-category-grid">
          {stats.categories.map(item => (
            <article key={item.category} data-running={item.running > 0} data-error={item.error > 0}>
              <Wrench size={13} />
              <span>{item.category}</span>
              <strong>{item.count}</strong>
              <small>{item.running > 0 ? `${item.running} running` : item.error > 0 ? `${item.error} error` : 'ok'}</small>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function summarizeToolStats(messages: ChatMessage[]): ToolStats {
  const tools = messages.flatMap(message => message.tools ?? []);
  const categoryMap = new Map<string, { category: string; count: number; running: number; error: number }>();
  for (const tool of tools) {
    const category = toolCategory(tool.toolName);
    const row = categoryMap.get(category) || { category, count: 0, running: 0, error: 0 };
    row.count += 1;
    if (tool.status === 'running' || tool.status === 'waiting_approval') row.running += 1;
    if (tool.status === 'error') row.error += 1;
    categoryMap.set(category, row);
  }
  return {
    total: tools.length,
    running: tools.filter(tool => tool.status === 'running' || tool.status === 'waiting_approval').length,
    success: tools.filter(tool => tool.status === 'success').length,
    error: tools.filter(tool => tool.status === 'error').length,
    categories: Array.from(categoryMap.values()).sort((a, b) => b.count - a.count).slice(0, 8),
  };
}

function toolCategory(name: string): string {
  const value = name.toLowerCase();
  if (/(write|edit|patch|diff|file|read|list|ls|glob)/.test(value)) return 'Files';
  if (/(shell|terminal|cmd|bash|powershell|execute|run|command)/.test(value)) return 'Shell';
  if (/(web|browser|preview|url|fetch|http)/.test(value)) return 'Web';
  if (/(agent|subagent|task|plan)/.test(value)) return 'Agents';
  if (/(permission|approval|confirm)/.test(value)) return 'Permission';
  return 'Other';
}

function StatusGrid({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="command-status-grid">
      {items.map(([label, value]) => (
        <article key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </article>
      ))}
    </div>
  );
}

function tabLabel(tab: CommandCenterTab, language: string): string {
  const zh: Record<CommandCenterTab, string> = {
    search: '搜索',
    system: '系统',
    runs: '任务',
    provider: '模型',
  };
  const en: Record<CommandCenterTab, string> = {
    search: 'Search',
    system: 'System',
    runs: 'Runs',
    provider: 'Provider',
  };
  return language === 'zh' ? zh[tab] : en[tab];
}

function tabIcon(tab: CommandCenterTab) {
  if (tab === 'system') return <Server size={14} />;
  if (tab === 'runs') return <Activity size={14} />;
  if (tab === 'provider') return <ShieldCheck size={14} />;
  return <Search size={14} />;
}

function formatRelativeTime(ms: number): string {
  if (!ms) return '-';
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 2) return '刚刚';
  if (seconds < 60) return `${seconds}s 前`;
  return `${Math.round(seconds / 60)}m 前`;
}

function downloadText(content: string, filename: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function snippetText(snippet: string): string {
  return snippet.replace(/<\/?mark>/g, '').replace(/\s+/g, ' ').trim();
}

function toAppMode(value: string): AppMode | null {
  return value === 'chat' || value === 'cowork' || value === 'code' ? value : null;
}

function formatTimestamp(seconds: number): string {
  if (!seconds) return '-';
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
