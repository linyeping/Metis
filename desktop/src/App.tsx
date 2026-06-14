import { useCallback, useEffect, useRef, useState } from 'react';
import { getFirstRun, getSettings, pingHealth } from './lib/api';
import type { BootEvent, BootState, RuntimeSettings } from './lib/types';
import { useTheme } from './hooks/useTheme';
import { AppShell } from './components/shell/AppShell';
import { BootOverlay } from './components/shell/BootOverlay';
import { Sidebar } from './components/sidebar/Sidebar';
import { MetisThread } from './components/chat/MetisThread';
import { SettingsDialog } from './components/settings/SettingsDialog';
import { SetupWizard } from './components/setup/SetupWizard';
import { RightRail } from './components/rightrail/RightRail';
import { SideChatPanel } from './components/rightrail/SideChatPanel';
import { SectionMain } from './components/sections/Sections';
import { CommandPalette } from './components/command/CommandPalette';
import { ToastViewport } from './components/common/Toast';
import { ModelPickerOverlay } from './components/command/ModelPickerOverlay';
import { CronPanel } from './components/cron/CronPanel';
import { AppDialog } from './components/dialog/AppDialog';
import { useChatStore } from './store/chatStore';
import { useSessionStore } from './store/sessionStore';
import { useUiStore } from './store/uiStore';

const emptyBootState: BootState = {
  status: 'idle',
  port: null,
  error: null,
  reconnect: null,
  events: [],
  logPath: '',
};

function applyBootEvent(state: BootState, event: BootEvent): BootState {
  const events = [...state.events, event].slice(-160);
  if (event.phase === 'ready') {
    return {
      ...state,
      status: 'ready',
      port: event.port ?? state.port,
      error: null,
      reconnect: null,
      events,
      logPath: event.logPath ?? state.logPath,
    };
  }

  if (event.phase === 'restarting') {
    return {
      ...state,
      status: 'starting',
      reconnect: { attempt: event.attempt ?? 0, limit: event.limit ?? 0 },
      events,
      logPath: event.logPath ?? state.logPath,
    };
  }

  if (event.phase === 'error' || event.phase === 'exit') {
    return {
      ...state,
      status: 'error',
      port: null,
      error: {
        title: event.title || '后端启动失败',
        detail: event.detail || event.logTail || '',
        logTail: event.logTail,
      },
      reconnect: event.attempt && event.limit ? { attempt: event.attempt, limit: event.limit } : state.reconnect,
      events,
      logPath: event.logPath ?? state.logPath,
    };
  }

  return {
    ...state,
    status: state.status === 'ready' && event.phase === 'log' ? 'ready' : 'starting',
    events,
    logPath: event.logPath ?? state.logPath,
  };
}

export function App() {
  useTheme();
  const activeSection = useUiStore(state => state.activeSection);
  const setSideChatOpen = useUiStore(state => state.setSideChatOpen);
  const settingsOpen = useUiStore(state => state.settingsOpen);
  const commandOpen = useUiStore(state => state.commandOpen);
  const modelPickerOpen = useUiStore(state => state.modelPickerOpen);
  const workspaceMenuOpen = useUiStore(state => state.workspaceMenuOpen);
  const appDialog = useUiStore(state => state.appDialog);
  const loadSessions = useSessionStore(state => state.load);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const loadChatSession = useChatStore(state => state.loadSession);
  const [backendReady, setBackendReady] = useState(false);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [firstRun, setFirstRun] = useState(false);
  const [bootState, setBootState] = useState<BootState>(emptyBootState);
  const [healthReconnect, setHealthReconnect] = useState<{ attempt: number; limit: number } | null>(null);
  const loadedPort = useRef<number | null>(null);
  // 用户「暂时跳过」首启向导后，本会话不再因 refresh 重新检测 first_run 而把向导弹回来。
  const firstRunDismissed = useRef(false);

  const refresh = useCallback(async () => {
    await loadSessions();
    const runtimeSettings = await getSettings();
    setSettings(runtimeSettings);
    const firstRunStatus = await getFirstRun();
    setFirstRun(firstRunDismissed.current ? false : firstRunStatus.firstRun);
    setBackendReady(true);
  }, [loadSessions]);

  const refreshAfterReady = useCallback(
    (port: number | null | undefined) => {
      if (!port || loadedPort.current === port) {
        return;
      }

      loadedPort.current = port;
      void refresh().catch(error => {
        loadedPort.current = null;
        setBackendReady(false);
        setBootState(state =>
          applyBootEvent(state, {
            phase: 'error',
            title: '后端就绪后读取数据失败',
            detail: error instanceof Error ? error.message : String(error),
          }),
        );
      });
    },
    [refresh],
  );

  useEffect(() => {
    if (!window.metis) {
      setBackendReady(true);
      return undefined;
    }

    let disposed = false;
    const handleBootEvent = (event: BootEvent) => {
      if (disposed) return;
      setBootState(state => applyBootEvent(state, event));
      if (event.phase === 'ready') {
        refreshAfterReady(event.port);
      } else if (event.phase === 'error' || event.phase === 'exit') {
        loadedPort.current = null;
        setBackendReady(false);
      }
    };

    void window.metis
      .bootState()
      .then(state => {
        if (disposed) return;
        setBootState(state);
        if (state.status === 'ready') {
          refreshAfterReady(state.port);
        } else if (state.status === 'error') {
          setBackendReady(false);
        }
      })
      .catch(error => {
        if (disposed) return;
        setBootState(state =>
          applyBootEvent(state, {
            phase: 'error',
            title: '读取启动状态失败',
            detail: error instanceof Error ? error.message : String(error),
          }),
        );
      });

    const unsubscribe = window.metis.onBootEvent(handleBootEvent);
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [refreshAfterReady]);

  useEffect(() => {
    void loadChatSession(activeSessionId);
  }, [activeSessionId, loadChatSession]);

  // FABLEADV-34: 心跳探测——进程活着但 API 假死时也能显示"正在重新连接 x/5"并自动恢复。
  useEffect(() => {
    if (!window.metis || !backendReady) {
      setHealthReconnect(null);
      return undefined;
    }
    let disposed = false;
    let fails = 0;
    const LIMIT = 5;
    const tick = async () => {
      const ok = await pingHealth();
      if (disposed) return;
      if (ok) {
        fails = 0;
        setHealthReconnect(null);
        return;
      }
      fails += 1;
      setHealthReconnect({ attempt: Math.min(fails, LIMIT), limit: LIMIT });
      if (fails >= LIMIT) {
        fails = 0;
        setHealthReconnect(null);
        try {
          void window.metis?.retryBackend?.();
        } catch {
          /* ignore */
        }
      }
    };
    const timer = window.setInterval(() => void tick(), 8000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [backendReady]);

  // 任意 DOM 浮层打开时，让主进程藏掉原生 preview 视图——它没有 z-index，否则会盖在弹窗上面。
  useEffect(() => {
    const occluded =
      settingsOpen || commandOpen || modelPickerOpen || workspaceMenuOpen || Boolean(appDialog) || firstRun || bootState.status !== 'ready' || !backendReady;
    void window.metis?.previewSetOccluded?.(occluded);
  }, [settingsOpen, commandOpen, modelPickerOpen, workspaceMenuOpen, appDialog, firstRun, bootState.status, backendReady]);

  useEffect(() => {
    let lastEscapeAt = 0;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!backendReady) return;
      if (event.key !== 'Escape' || event.repeat) return;
      const ui = useUiStore.getState();
      if (ui.appDialog || ui.commandOpen || ui.settingsOpen || ui.modelPickerOpen) {
        lastEscapeAt = 0;
        return;
      }
      const now = Date.now();
      if (now - lastEscapeAt <= 650) {
        lastEscapeAt = 0;
        event.preventDefault();
        void useChatStore.getState().rewindLatest();
        return;
      }
      lastEscapeAt = now;
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [backendReady]);

  const mainContent = activeSection === 'chat' ? <MetisThread /> : activeSection === 'cron' ? <CronPanel /> : <SectionMain section={activeSection} />;
  const main = (
    <div className="main-panel-stage" key={activeSection} data-section={activeSection}>
      {mainContent}
    </div>
  );
  const showBootOverlay = bootState.status !== 'ready' || !backendReady;

  return (
    <AppShell
      backendReady={backendReady}
      reconnect={bootState.reconnect ?? healthReconnect}
      model={settings?.model ?? ''}
      pythonPath={settings?.pythonPath}
      sidebar={<Sidebar model={settings?.model ?? ''} />}
      main={main}
      sideChat={<SideChatPanel defaultModel={settings?.model ?? ''} onClose={() => setSideChatOpen(false)} />}
      rightRail={<RightRail backendReady={backendReady} />}
      overlays={
        <>
          <CommandPalette settings={settings} settingsChanged={refresh} />
          <ModelPickerOverlay currentModel={settings?.model ?? ''} settingsChanged={refresh} />
          <SettingsDialog onSaved={refresh} />
          <AppDialog />
          <ToastViewport />
          {firstRun && (
            <SetupWizard
              onDone={() => {
                firstRunDismissed.current = true;
                setFirstRun(false);
                void refresh();
              }}
            />
          )}
          {showBootOverlay && (
            <BootOverlay
              state={bootState}
              onRetry={() => {
                loadedPort.current = null;
                setBackendReady(false);
                void window.metis.retryBackend();
              }}
              onOpenLog={() => void window.metis.openLog()}
            />
          )}
        </>
      }
    />
  );
}
