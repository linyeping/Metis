import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Binary,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleCheck,
  Copy,
  ExternalLink,
  FileCode,
  FileText,
  Folder,
  Globe,
  Image as ImageIcon,
  LoaderCircle,
  MoreVertical,
  MonitorPlay,
  Network,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  SquareTerminal,
  Square,
  StickyNote,
  Wrench,
  X,
} from 'lucide-react';
import { AnimatePresence, motion } from 'framer-motion';
import { createElement, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { apiBase, cancelChatRun, getAgentRuntimeProfile, getChatRuns, getProviderStatus, getWorkspaceFile, getWorkspaceTree } from '../../lib/api';
import type { FileChangeFileSummary, FileChangePreview } from '../../lib/diffPreview';
import type { AgentRuntimeProfilePayload, BrowserActivityItem, BrowserActivityPayload, ChatRunPayload, ChatTodoItem, ContextLedger, DevServerStatus, PreviewAuditResult, ProviderStatusPayload, RuntimeStatus, SessionMeta, Workspace, WorkspaceFile, WorkspaceTreeNode } from '../../lib/types';
import type { FileChangeRevertItem } from '../../lib/types';
import { isPreviewableWebFilePath, localFilePreviewUrl } from '../../lib/webPreview';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore, type WebPreviewTab, type WorkspaceCardColumnId, type WorkspaceCardId } from '../../store/uiStore';
import { SubagentActivityPanel } from '../chat/SubagentGroup';
import { TerminalPanel } from '../terminal/TerminalPanel';
import { useT } from '../../hooks/useT';

interface RightRailProps {
  backendReady: boolean;
}

const workspaceCardOptions: Array<{ id: WorkspaceCardId; label: string; icon: typeof FileText; shortcut?: string }> = [
  { id: 'web', label: 'Preview', icon: Globe, shortcut: '⇧⌘P' },
  { id: 'diff', label: 'Diff', icon: FileCode, shortcut: '⇧⌘D' },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal, shortcut: '⌘`' },
  { id: 'files', label: 'Files', icon: Folder, shortcut: '⇧⌘F' },
  { id: 'activity', label: 'Background tasks', icon: Network },
  { id: 'plan', label: 'Plan', icon: StickyNote },
  { id: 'tool', label: 'Tool output', icon: Wrench },
];

const workspaceCardColumns: Array<{ id: WorkspaceCardColumnId; cards: WorkspaceCardId[] }> = [
  { id: 'left', cards: ['web', 'terminal'] },
  { id: 'middle', cards: ['files', 'diff'] },
  { id: 'right', cards: ['activity', 'plan'] },
];

type PlanTodoStatus = 'done' | 'active' | 'pending' | 'blocked' | 'failed' | 'canceled';
type PlanActionKind = 'retry' | 'strategy' | 'manual';

function planTodoStatus(raw?: string): PlanTodoStatus {
  const value = String(raw || '').trim().toLowerCase();
  if (['done', 'completed', 'complete', 'finished'].includes(value)) return 'done';
  if (['in_progress', 'in-progress', 'active', 'doing', 'running'].includes(value)) return 'active';
  if (['blocked', 'blocker', 'stuck', 'waiting'].includes(value)) return 'blocked';
  if (['failed', 'failure', 'error'].includes(value)) return 'failed';
  if (['cancelled', 'canceled', 'cancel'].includes(value)) return 'canceled';
  return 'pending';
}

function planTodoStatusLabel(status: PlanTodoStatus): string {
  if (status === 'done') return '完成';
  if (status === 'active') return '进行中';
  if (status === 'blocked') return '受阻';
  if (status === 'failed') return '失败';
  if (status === 'canceled') return '取消';
  return '待办';
}

function planTodoLabel(item: ChatTodoItem | null | undefined, index: number, t: (text: string) => string): string {
  if (!item) return '';
  return String(item.content || item.task || item.title || item.id || `${t('任务 ')}${index + 1}`).trim();
}

function planTodoDetail(item: ChatTodoItem | null | undefined): string {
  if (!item) return '';
  const record = item as Record<string, unknown>;
  for (const key of ['note', 'error', 'reason', 'summary', 'result']) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function planFocusTodo(todos: ChatTodoItem[]): { item: ChatTodoItem | null; index: number; status: PlanTodoStatus | 'idle' } {
  const indexed = todos.map((item, index) => ({ item, index, status: planTodoStatus(item.status) }));
  return (
    indexed.find(row => row.status === 'active') ||
    indexed.find(row => row.status === 'failed' || row.status === 'blocked') ||
    indexed.find(row => row.status === 'pending') ||
    indexed[indexed.length - 1] || { item: null, index: -1, status: 'idle' }
  );
}

function planOverviewText(
  total: number,
  doneCount: number,
  activeCount: number,
  issueCount: number,
  runtimeStatus: RuntimeStatus | null,
  t: (text: string) => string,
): string {
  if (runtimeStatus?.phase === 'todo_progress' && runtimeStatus.display) return runtimeStatus.display;
  if (issueCount > 0) return t('有步骤受阻，可以重试或换策略。');
  if (activeCount > 0) return t('智能体正在推进当前步骤。');
  if (total > 0 && doneCount >= total) return t('任务清单已完成。');
  if (total > 0) return t('等待智能体继续执行下一步。');
  return t('等待任务清单。');
}

function buildPlanFollowUpPrompt(kind: PlanActionKind, stepLabel: string): string {
  const target = stepLabel ? `当前步骤：${stepLabel}` : '当前没有可见任务清单项。';
  if (kind === 'retry') {
    return `${target}\n请继续执行任务清单中的当前步骤。先简短说明刚才失败或卡住的原因，然后重试该步骤；重试后用 todo_write 更新每步状态，并给出可验证的完成证据。`;
  }
  if (kind === 'strategy') {
    return `${target}\n当前步骤受阻。请先用 todo_write 标记障碍，再换一种策略或工具继续，不要重复已经失败的路径；完成后更新任务清单，并说明新的验收证据。`;
  }
  return '我已经手动接管并完成或调整了当前界面状态。请重新观察当前状态，更新 todo_write，然后从任务清单的下一步继续；如果当前步骤已完成，请标记完成并继续后续步骤。';
}

function workspaceColumnWidth(
  columnId: WorkspaceCardColumnId,
  widths: { left: number; middle: number },
): number {
  if (columnId === 'left') return widths.left;
  if (columnId === 'middle') return widths.middle;
  return Math.max(18, 100 - widths.left - widths.middle);
}

export function RightRail({ backendReady }: RightRailProps) {
  const t = useT();
  const previewPath = useUiStore(state => state.previewPath);
  const previewFrozenSrc = useUiStore(state => state.previewFrozenSrc);
  const toolPreview = useUiStore(state => state.toolPreview);
  const diffPreview = useUiStore(state => state.diffPreview);
  const diffSummary = useUiStore(state => state.diffSummary);
  const activeDiffFileId = useUiStore(state => state.activeDiffFileId);
  const diffRevertSummaryId = useUiStore(state => state.diffRevertSummaryId);
  const diffRevertItems = useUiStore(state => state.diffRevertItems);
  const workspaceRefreshNonce = useUiStore(state => state.workspaceRefreshNonce);
  const setActiveDiffFile = useUiStore(state => state.setActiveDiffFile);
  const webPreviewTabs = useUiStore(state => state.webPreviewTabs);
  const activeWebPreviewId = useUiStore(state => state.activeWebPreviewId);
  const webPreviewUrl = useUiStore(state => state.webPreviewUrl);
  const subagents = useChatStore(state => state.subagents);
  const planTodos = useChatStore(state => state.planTodos);
  const streaming = useChatStore(state => state.streaming);
  const runtimeStatus = useChatStore(state => state.runtimeStatus);
  const contextLedger = useChatStore(state => state.contextLedger);
  const sendChat = useChatStore(state => state.send);
  const rewindLatest = useChatStore(state => state.rewindLatest);
  const loadChatSession = useChatStore(state => state.loadSession);
  const activateWebPreviewTab = useUiStore(state => state.activateWebPreviewTab);
  const closeWebPreviewTab = useUiStore(state => state.closeWebPreviewTab);
  const updateWebPreviewTab = useUiStore(state => state.updateWebPreviewTab);
  const setWebPreviewZoom = useUiStore(state => state.setWebPreviewZoom);
  const sessions = useSessionStore(state => state.sessions);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const selectSession = useSessionStore(state => state.selectSession);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const rightRailOpen = useUiStore(state => state.rightRailOpen);
  const rightRailWidth = useUiStore(state => state.rightRailWidth);
  const setRightRailWidth = useUiStore(state => state.setRightRailWidth);
  const setPreviewPath = useUiStore(state => state.setPreviewPath);
  const workspaceCardVisibility = useUiStore(state => state.workspaceCardVisibility);
  const workspaceCardColumnWidths = useUiStore(state => state.workspaceCardColumnWidths);
  const workspaceCardRowSplits = useUiStore(state => state.workspaceCardRowSplits);
  const setWorkspaceCardVisible = useUiStore(state => state.setWorkspaceCardVisible);
  const setWorkspaceCardColumnWidths = useUiStore(state => state.setWorkspaceCardColumnWidths);
  const setWorkspaceCardRowSplit = useUiStore(state => state.setWorkspaceCardRowSplit);
  const [tree, setTree] = useState<WorkspaceTreeNode[]>([]);
  const [file, setFile] = useState<WorkspaceFile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [webInput, setWebInput] = useState(webPreviewUrl);
  const [webError, setWebError] = useState('');
  const [webNav, setWebNav] = useState({ canGoBack: false, canGoForward: false });
  const [webMoreOpen, setWebMoreOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [devStatus, setDevStatus] = useState<DevServerStatus | null>(null);
  const [devBusy, setDevBusy] = useState(false);
  const [previewAudit, setPreviewAudit] = useState<PreviewAuditResult | null>(null);
  const [browserActivity, setBrowserActivity] = useState<BrowserActivityPayload | null>(null);
  const [agentRuntimeProfile, setAgentRuntimeProfile] = useState<AgentRuntimeProfilePayload | null>(null);
  const [auditBusy, setAuditBusy] = useState(false);
  const [devDetailsOpen, setDevDetailsOpen] = useState(false);
  const [workspaceSettling, setWorkspaceSettling] = useState(false);
  const [planActionBusy, setPlanActionBusy] = useState<PlanActionKind | 'rewind' | ''>('');
  const previewHostRef = useRef<HTMLDivElement | null>(null);
  const zoomFrameRef = useRef<number | null>(null);
  const workspaceDeckRef = useRef<HTMLDivElement | null>(null);
  const activeWorkspacePath = workspaces.find(workspace => workspace.id === activeWorkspaceId)?.path || '';
  const activeWebTab = useMemo(() => webPreviewTabs.find(tab => tab.id === activeWebPreviewId) || null, [activeWebPreviewId, webPreviewTabs]);
  const activeWebZoom = activeWebTab?.zoom || 1;
  const activeWebZoomPercent = Math.round(activeWebZoom * 100);
  const webCardVisible = workspaceCardVisibility.web;
  const activeDiffFile = useMemo(
    () => diffSummary?.files.find(file => file.preview.id === activeDiffFileId) || diffSummary?.files[0] || null,
    [activeDiffFileId, diffSummary],
  );
  const activeDiffPreview = activeDiffFile?.preview || diffPreview;
  const activeDiffRevertItem = useMemo(
    () =>
      diffSummary && diffRevertSummaryId === diffSummary.id
        ? diffRevertItemFor(activeDiffFile?.preview || activeDiffPreview, diffRevertItems)
        : null,
    [activeDiffFile?.preview, activeDiffPreview, diffRevertItems, diffRevertSummaryId, diffSummary],
  );

  useEffect(() => {
    if (!backendReady || !activeSessionId) {
      setAgentRuntimeProfile(null);
      return undefined;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const profile = await getAgentRuntimeProfile(activeSessionId);
        if (!cancelled) setAgentRuntimeProfile(profile);
      } catch {
        if (!cancelled) setAgentRuntimeProfile(null);
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, streaming ? 8000 : 30000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeSessionId, backendReady, planTodos.length, streaming, subagents.length]);

  const submitPlanFollowUp = useCallback(
    async (kind: PlanActionKind, stepLabel: string) => {
      if (streaming || planActionBusy) return;
      setPlanActionBusy(kind);
      try {
        await sendChat(buildPlanFollowUpPrompt(kind, stepLabel));
      } finally {
        setPlanActionBusy('');
      }
    },
    [planActionBusy, sendChat, streaming],
  );

  const rewindPlanStep = useCallback(async () => {
    if (streaming || planActionBusy) return;
    setPlanActionBusy('rewind');
    try {
      await rewindLatest();
    } finally {
      setPlanActionBusy('');
    }
  }, [planActionBusy, rewindLatest, streaming]);

  const loadTree = async () => {
    if (!backendReady) {
      setTree([]);
      setError(null);
      return;
    }
    try {
      setError(null);
      setTree(await getWorkspaceTree());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void loadTree();
  }, [backendReady, workspaceRefreshNonce]);

  useEffect(() => {
    if (!backendReady || !previewPath) {
      setFile(null);
      return;
    }
    setError(null);
    void getWorkspaceFile(previewPath)
      .then(setFile)
      .catch(err => setError(err instanceof Error ? err.message : String(err)));
  }, [backendReady, previewPath, workspaceRefreshNonce]);

  useEffect(() => {
    setWebInput(webPreviewUrl);
    if (/^https?:\/\//i.test(webPreviewUrl)) {
      setWebError('');
    }
  }, [webPreviewUrl]);

  useEffect(() => {
    setWebNav({ canGoBack: false, canGoForward: false });
  }, [activeWebPreviewId]);

  useEffect(() => {
    return window.metis?.onPreviewState?.(payload => {
      const tabId = payload.tabId || useUiStore.getState().activeWebPreviewId;
      if (!tabId || tabId !== useUiStore.getState().activeWebPreviewId) return;
      const patch: Partial<WebPreviewTab> = {};
      if (payload.error !== undefined) patch.error = payload.error;
      if (payload.loading !== undefined) patch.loading = Boolean(payload.loading);
      if (payload.title) patch.title = payload.title;
      if (payload.url && /^https?:\/\//i.test(payload.url)) patch.url = payload.url;
      setWebNav({
        canGoBack: Boolean(payload.canGoBack),
        canGoForward: Boolean(payload.canGoForward),
      });
      if (Object.keys(patch).length > 0) updateWebPreviewTab(tabId, patch);
    });
  }, [updateWebPreviewTab]);

  const refreshBrowserActivity = useCallback(async () => {
    if (!window.metis?.previewActivity || !rightRailOpen || !webCardVisible) return;
    try {
      const result = await window.metis.previewActivity({ limit: 24 });
      if (result?.ok) setBrowserActivity(result);
    } catch {
      // Activity is observational; preview itself should not be disturbed if this fails.
    }
  }, [rightRailOpen, webCardVisible]);

  useEffect(() => {
    if (!rightRailOpen || !webCardVisible) return;
    void refreshBrowserActivity();
    const timer = window.setInterval(() => void refreshBrowserActivity(), 1600);
    return () => window.clearInterval(timer);
  }, [refreshBrowserActivity, rightRailOpen, webCardVisible]);

  const hidePreviewView = useCallback(() => {
    void window.metis?.previewSetBounds?.({ visible: false });
  }, []);

  const syncPreviewBounds = useCallback(() => {
    const node = previewHostRef.current;
    const canShowPreview = rightRailOpen && webCardVisible && Boolean(webPreviewUrl && activeWebPreviewId);
    if (!window.metis?.previewSetBounds || !node || !canShowPreview) {
      hidePreviewView();
      return;
    }
    const rect = node.getBoundingClientRect();
    const visible = rect.width > 4 && rect.height > 4 && !workspaceSettling;
    void window.metis.previewSetBounds({
      height: rect.height,
      tabId: activeWebPreviewId,
      visible,
      width: rect.width,
      x: rect.left,
      y: rect.top,
    });
  }, [activeWebPreviewId, hidePreviewView, rightRailOpen, webCardVisible, webPreviewUrl, workspaceSettling]);

  const schedulePreviewBoundsSync = useCallback(() => {
    const frames: number[] = [];
    const timers: number[] = [];
    let disposed = false;
    const run = () => {
      if (!disposed) syncPreviewBounds();
    };
    run();
    const frame = requestAnimationFrame(() => {
      run();
      frames.push(requestAnimationFrame(run));
    });
    frames.push(frame);
    timers.push(window.setTimeout(run, 160));
    timers.push(window.setTimeout(run, 340));
    return () => {
      disposed = true;
      frames.forEach(cancelAnimationFrame);
      timers.forEach(window.clearTimeout);
    };
  }, [syncPreviewBounds]);

  useEffect(() => {
    if (!webPreviewUrl || !activeWebPreviewId) {
      void window.metis?.previewSetBounds?.({ visible: false });
      return;
    }
    const tabId = activeWebPreviewId;
    useUiStore.getState().updateWebPreviewTab(tabId, { error: '', loading: true });
    void window.metis?.previewLoad?.({ tabId, url: webPreviewUrl }).then(result => {
      if (!result?.ok) useUiStore.getState().updateWebPreviewTab(tabId, { error: result?.error || t('Preview 加载失败'), loading: false });
    });
    const frames: number[] = [];
    const timers: number[] = [];
    let disposed = false;
    const syncLoadedPreviewBounds = () => {
      if (disposed) return;
      const node = previewHostRef.current;
      if (!window.metis?.previewSetBounds || !node) return;
      const state = useUiStore.getState();
      const rect = node.getBoundingClientRect();
      const visible =
        state.rightRailOpen &&
        state.workspaceCardVisibility.web &&
        state.activeWebPreviewId === tabId &&
        state.webPreviewUrl === webPreviewUrl &&
        rect.width > 4 &&
        rect.height > 4;
      void window.metis.previewSetBounds({
        height: rect.height,
        tabId,
        visible,
        width: rect.width,
        x: rect.left,
        y: rect.top,
      });
    };
    syncLoadedPreviewBounds();
    const frame = requestAnimationFrame(() => {
      syncLoadedPreviewBounds();
      frames.push(requestAnimationFrame(syncLoadedPreviewBounds));
    });
    frames.push(frame);
    timers.push(window.setTimeout(syncLoadedPreviewBounds, 160));
    timers.push(window.setTimeout(syncLoadedPreviewBounds, 340));
    return () => {
      disposed = true;
      frames.forEach(cancelAnimationFrame);
      timers.forEach(window.clearTimeout);
    };
  }, [activeWebPreviewId, webPreviewUrl]);

  useEffect(() => {
    if (!rightRailOpen || !webCardVisible) hidePreviewView();
  }, [hidePreviewView, rightRailOpen, webCardVisible]);

  useEffect(() => {
    const node = previewHostRef.current;
    if (!node || !webCardVisible || !rightRailOpen) {
      hidePreviewView();
      return undefined;
    }
    let cancelScheduledSync: (() => void) | null = null;
    const schedule = () => {
      cancelScheduledSync?.();
      cancelScheduledSync = schedulePreviewBoundsSync();
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(node);
    if (workspaceDeckRef.current) observer.observe(workspaceDeckRef.current);
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, true);
    schedule();
    return () => {
      cancelScheduledSync?.();
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      window.removeEventListener('scroll', schedule, true);
      hidePreviewView();
    };
  }, [hidePreviewView, rightRailOpen, schedulePreviewBoundsSync, webCardVisible]);

  useEffect(() => {
    if (!activeWebPreviewId || !webPreviewUrl || !webCardVisible || !rightRailOpen) {
      hidePreviewView();
      return undefined;
    }
    let disposed = false;
    let cancelBoundsSync: (() => void) | null = null;
    if (zoomFrameRef.current !== null) cancelAnimationFrame(zoomFrameRef.current);
    zoomFrameRef.current = requestAnimationFrame(() => {
      zoomFrameRef.current = null;
      void window.metis?.previewSetZoom?.(activeWebZoom);
      if (!disposed) cancelBoundsSync = schedulePreviewBoundsSync();
    });
    return () => {
      disposed = true;
      if (zoomFrameRef.current !== null) {
        cancelAnimationFrame(zoomFrameRef.current);
        zoomFrameRef.current = null;
      }
      cancelBoundsSync?.();
    };
  }, [activeWebPreviewId, activeWebZoom, hidePreviewView, rightRailOpen, schedulePreviewBoundsSync, webCardVisible, webPreviewUrl]);

  useEffect(() => {
    if (!window.metis || !activeWorkspacePath) {
      setDevStatus(null);
      return undefined;
    }
    let disposed = false;
    void window.metis.devServerStatus({ cwd: activeWorkspacePath }).then(status => {
      if (!disposed) setDevStatus(status);
    });
    const unsubscribe = window.metis.onDevServerEvent(payload => {
      if (payload.status.cwd !== activeWorkspacePath) return;
      setDevStatus(payload.status);
      if (payload.status.url) {
        useUiStore.getState().setWebPreviewUrl(payload.status.url);
      }
    });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [activeWorkspacePath]);

  const dragStart = useRef<{ x: number; width: number } | null>(null);
  const columnDragStart = useRef<{
    x: number;
    width: number;
    left: number;
    middle: number;
    leftColumnId: WorkspaceCardColumnId;
    rightColumnId: WorkspaceCardColumnId;
  } | null>(null);
  const rowDragStart = useRef<{ y: number; height: number; split: number; columnId: WorkspaceCardColumnId } | null>(null);
  const settleWorkspaceCards = () => {
    setWorkspaceSettling(true);
    window.setTimeout(() => setWorkspaceSettling(false), 170);
  };

  const startResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-rail');
    dragStart.current = { x: event.clientX, width: rightRailWidth };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      if (!dragStart.current) return;
      setRightRailWidth(dragStart.current.width - (moveEvent.clientX - dragStart.current.x));
    };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleUp = () => {
      dragStart.current = null;
      document.body.classList.remove('resizing-rail');
      document.removeEventListener('selectstart', preventSelection);
      try {
        resizeTarget.releasePointerCapture(event.pointerId);
      } catch {}
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };
    document.addEventListener('selectstart', preventSelection);
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  const startColumnResize = (leftColumnId: WorkspaceCardColumnId, rightColumnId: WorkspaceCardColumnId, event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const deck = workspaceDeckRef.current;
    if (!deck) return;
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-workspace-card-column');
    columnDragStart.current = {
      leftColumnId,
      rightColumnId,
      left: workspaceCardColumnWidths.left,
      middle: workspaceCardColumnWidths.middle,
      width: Math.max(1, deck.getBoundingClientRect().width),
      x: event.clientX,
    };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      const start = columnDragStart.current;
      if (!start) return;
      const delta = ((moveEvent.clientX - start.x) / start.width) * 100;
      if (start.leftColumnId === 'left' && start.rightColumnId === 'middle') {
        setWorkspaceCardColumnWidths({
          left: start.left + delta,
          middle: start.middle - delta,
        });
        return;
      }
      if (start.leftColumnId === 'left' && start.rightColumnId === 'right') {
        setWorkspaceCardColumnWidths({
          left: start.left + delta,
          middle: start.middle,
        });
        return;
      }
      if (start.leftColumnId === 'middle' && start.rightColumnId === 'right') {
        setWorkspaceCardColumnWidths({
          left: start.left,
          middle: start.middle + delta,
        });
      }
    };
    const handleUp = () => {
      columnDragStart.current = null;
      document.body.classList.remove('resizing-workspace-card-column');
      settleWorkspaceCards();
      document.removeEventListener('selectstart', preventSelection);
      try {
        resizeTarget.releasePointerCapture(event.pointerId);
      } catch {}
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };
    document.addEventListener('selectstart', preventSelection);
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  const startRowResize = (columnId: WorkspaceCardColumnId, event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const column = event.currentTarget.closest('.workspace-card-column') as HTMLElement | null;
    if (!column) return;
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-workspace-card-row');
    rowDragStart.current = {
      columnId,
      height: Math.max(1, column.getBoundingClientRect().height),
      split: workspaceCardRowSplits[columnId],
      y: event.clientY,
    };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      const start = rowDragStart.current;
      if (!start) return;
      const delta = ((moveEvent.clientY - start.y) / start.height) * 100;
      setWorkspaceCardRowSplit(start.columnId, start.split + delta);
    };
    const handleUp = () => {
      rowDragStart.current = null;
      document.body.classList.remove('resizing-workspace-card-row');
      settleWorkspaceCards();
      document.removeEventListener('selectstart', preventSelection);
      try {
        resizeTarget.releasePointerCapture(event.pointerId);
      } catch {}
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
      window.removeEventListener('pointercancel', handleUp);
    };
    document.addEventListener('selectstart', preventSelection);
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    window.addEventListener('pointercancel', handleUp);
  };

  const toolStats = useMemo(() => previewStats(toolPreview?.content || ''), [toolPreview?.content]);

  const copyToolOutput = async () => {
    if (!toolPreview?.content) return;
    await navigator.clipboard?.writeText(toolPreview.content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 900);
  };

  const openWebInput = async () => {
    const value = webInput.trim();
    if (/^https?:\/\//i.test(value)) {
      setWebError('');
      useUiStore.getState().setWebPreviewUrl(value);
      return;
    }
    if (isPreviewableWebFilePath(value)) {
      try {
        const url = localFilePreviewUrl(await apiBase(), value);
        if (url) {
          setWebError('');
          useUiStore.getState().setWebPreviewUrl(url);
          return;
        }
      } catch (error) {
        setWebError(error instanceof Error ? error.message : String(error));
        return;
      }
    }
    setWebError(t('只支持 http://、https:// 或本工作区 HTML 文件'));
  };

  const openActiveWebExternal = async () => {
    const url = activeWebTab?.url || webPreviewUrl;
    if (!url) return;
    const result = await window.metis?.openExternal(url);
    if (!result?.ok && activeWebPreviewId) {
      updateWebPreviewTab(activeWebPreviewId, { error: t('外部打开被安全策略拦截') });
    }
  };

  const reloadActiveWeb = () => {
    setWebError('');
    if (activeWebPreviewId) {
      updateWebPreviewTab(activeWebPreviewId, { error: '', loading: true });
    }
    if (activeWebTab?.loading) {
      void window.metis?.previewCommand?.('stop');
      setTimeout(() => {
        if (activeWebPreviewId) updateWebPreviewTab(activeWebPreviewId, { loading: false });
      }, 300);
    } else {
      void window.metis?.previewCommand?.('reload');
      const reloadTimeout = setTimeout(() => {
        if (activeWebPreviewId) {
          const currentUrl = activeWebTab?.url || webPreviewUrl;
          if (currentUrl) {
            void window.metis?.previewLoad?.({ tabId: activeWebPreviewId, url: currentUrl });
          }
          updateWebPreviewTab(activeWebPreviewId, { loading: false });
        }
      }, 8000);
      window.setTimeout(() => clearTimeout(reloadTimeout), 8200);
    }
  };

  const setActiveZoom = (nextZoom: number) => {
    if (!activeWebPreviewId) return;
    setWebPreviewZoom(activeWebPreviewId, nextZoom);
  };

  const startDevPreview = async () => {
    if (!window.metis || !activeWorkspacePath || devBusy) return;
    setDevBusy(true);
    try {
      const status = await window.metis.devServerStart({ cwd: activeWorkspacePath });
      setDevStatus(status);
      if (status.url) {
        useUiStore.getState().setWebPreviewUrl(status.url);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setDevStatus({
        state: 'error',
        cwd: activeWorkspacePath,
        packagePath: '',
        packageManager: 'npm',
        scriptName: '',
        command: '',
        stack: '',
        url: '',
        logs: [message],
        reason: message,
        startedAt: 0,
        updatedAt: Date.now(),
      });
    } finally {
      setDevBusy(false);
    }
  };

  const stopDevPreview = async () => {
    if (!window.metis || !activeWorkspacePath || devBusy) return;
    setDevBusy(true);
    try {
      setDevStatus(await window.metis.devServerStop({ cwd: activeWorkspacePath }));
    } finally {
      setDevBusy(false);
    }
  };

  const auditActivePreview = async () => {
    if (!window.metis || auditBusy) return;
    setAuditBusy(true);
    try {
      const image = await window.metis?.previewCapture?.();
      const result = await window.metis.savePreviewEvidence({
        url: activeWebTab?.url || webPreviewUrl,
        title: activeWebTab?.title || t(webTabLabel(webPreviewUrl)),
        loading: Boolean(activeWebTab?.loading),
        error: activeWebTab?.error || '',
        zoom: activeWebZoom,
        screenshotDataUrl: image?.dataUrl || '',
      });
      setPreviewAudit(result);
    } catch (err) {
      setPreviewAudit({
        ok: false,
        status: 'error',
        reason: err instanceof Error ? err.message : String(err),
        url: activeWebTab?.url || webPreviewUrl,
        title: activeWebTab?.title || '',
        savedPath: '',
        capturedAt: new Date().toISOString(),
        screenshotAvailable: false,
      });
    } finally {
      setAuditBusy(false);
    }
  };

  const hasDevServerDetails = Boolean(
    previewAudit ||
      devStatus?.url ||
      devStatus?.state === 'error' ||
      (devStatus?.state === 'running' && devStatus.logs.length > 0),
  );
  const showDevServerDetails = devDetailsOpen && hasDevServerDetails;

  const visibleWorkspaceColumns = workspaceCardColumns
    .map(column => ({
      ...column,
      visibleCards: column.cards.filter(cardId => workspaceCardVisibility[cardId] && (cardId !== 'tool' || Boolean(toolPreview))),
    }))
    .filter(column => column.visibleCards.length > 0);
  const visibleColumnTotal = visibleWorkspaceColumns.reduce((total, column) => total + workspaceColumnWidth(column.id, workspaceCardColumnWidths), 0);
  const workspaceLayoutTransition = workspaceSettling
    ? { type: 'spring' as const, stiffness: 500, damping: 32, duration: 0.15 }
    : { type: 'spring' as const, stiffness: 360, damping: 26 };
  const workspaceDeckStyle = {
    gridTemplateColumns:
      visibleWorkspaceColumns.length <= 1
        ? 'minmax(0, 1fr)'
        : visibleWorkspaceColumns
            .map(column => {
              const share = (workspaceColumnWidth(column.id, workspaceCardColumnWidths) / Math.max(1, visibleColumnTotal)) * 100;
              return `minmax(0, ${Math.max(18, share).toFixed(2)}%)`;
            })
            .join(' '),
  } as CSSProperties;

  const renderFilesPanel = () => (
    <div className="workspace-files-panel">
      <div className="file-tree">
        {tree.length === 0 && backendReady && !error && <p className="empty-preview">{t('当前工作区没有可预览文件。')}</p>}
        {tree.map(node => createElement(TreeNode, { key: node.path || node.name, node, onPick: setPreviewPath, activePath: previewPath || '' }))}
      </div>
      <div className="preview-pane">
        {error && <p className="error-text">{error}</p>}
        {!backendReady && !error && <p className="empty-preview">{t('后端连接后会自动加载工作区文件。')}</p>}
        {backendReady && !file && !error && <p className="empty-preview">{t('选择文件后在这里预览。')}</p>}
        {file && <FileMeta file={file} />}
        {file?.type === 'image' && <ImagePreview file={file} />}
        {(file?.type === 'text' || file?.type === 'markdown') && (
          <>
            {file.truncated && <p className="rail-warning">{t('文件较大，已显示前半部分。')}</p>}
            <pre className="file-content">{file.content || (file.truncated ? t('文件过大，已省略内容。') : '')}</pre>
          </>
        )}
        {file?.type === 'binary' && (
          <div className="rail-empty-card">
            <Binary size={18} />
            <strong>{t('二进制文件暂不预览')}</strong>
            <span>{t('可以通过工具读取或在系统文件管理器中打开。')}</span>
          </div>
        )}
      </div>
    </div>
  );

  const renderToolPanel = () => (
    <div className="tool-output-pane">
      {toolPreview ? (
        <>
          <div className="rail-info-bar">
            <Wrench size={14} />
            <strong>{toolPreview.title || t('工具输出')}</strong>
            <span>{toolStats.lines}{t(' 行')}</span>
            <span>{toolStats.chars}{t(' 字符')}</span>
            <button type="button" className="tool-copy-button" onClick={() => void copyToolOutput()}>
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? t('已复制') : t('复制')}
            </button>
          </div>
          <pre>{toolPreview.content}</pre>
        </>
      ) : (
        <div className="rail-empty-card">
          <Wrench size={18} />
          <strong>{t('暂无工具输出')}</strong>
          <span>{t('点击聊天里的工具卡片可在这里查看完整结果。')}</span>
        </div>
      )}
    </div>
  );

  const renderActivityPanel = () => (
    <div className="activity-pane">
      <RunActivityCenter
        backendReady={backendReady}
        loadChatSession={loadChatSession}
        selectSession={selectSession}
        sessions={sessions}
        workspaces={workspaces}
      />
      {toolPreview && <div className="activity-inline-tool-output">{renderToolPanel()}</div>}
      <SubagentActivityPanel items={subagents} />
    </div>
  );

  const renderDiffPanel = () => (
    <div className="diff-preview-pane">
      {activeDiffPreview ? (
        <>
          {diffSummary && (
            <div className="diff-file-navigator" aria-label={t('文件变更列表')}>
              <div className="diff-navigator-head">
                <strong>{diffSummary.fileCount}{t(' 个文件')}</strong>
                <span>
                  +{diffSummary.additions} / -{diffSummary.removals}
                </span>
              </div>
              {diffSummary.files.map(file => {
                const item = diffRevertSummaryId === diffSummary.id ? diffRevertItemFor(file.preview, diffRevertItems) : null;
                return (
                  <button
                    className="diff-file-row"
                    data-active={file.preview.id === activeDiffPreview.id}
                    data-status={item?.status || 'active'}
                    key={file.preview.id}
                    title={file.path}
                    type="button"
                    onClick={() => setActiveDiffFile(file.preview.id)}
                  >
                    <FileCode size={13} />
                    <span>{compactPath(file.path || file.title)}</span>
                    <em>{t(diffKindLabel(file.kind))}</em>
                    <b>+{file.additions}</b>
                    <i>-{file.removals}</i>
                    {item && <small>{t(diffRevertLabel(item.status))}</small>}
                  </button>
                );
              })}
            </div>
          )}
          <div className="diff-info-bar" data-kind={activeDiffPreview.kind}>
            <FileCode size={14} />
            <div>
              <strong>{activeDiffPreview.title}</strong>
              <span>{activeDiffPreview.path}</span>
            </div>
            <em>{t(diffKindLabel(activeDiffPreview.kind))}</em>
          </div>
          {activeDiffRevertItem && activeDiffRevertItem.status !== 'reverted' && (
            <p className="diff-revert-alert" data-status={activeDiffRevertItem.status}>
              <AlertTriangle size={13} />
              {activeDiffRevertItem.message || t(diffRevertLabel(activeDiffRevertItem.status))}
            </p>
          )}
          {activeDiffRevertItem?.status === 'reverted' && (
            <p className="diff-revert-alert" data-status="reverted">
              <Check size={13} />
              {activeDiffRevertItem.message || t('已撤销')}
            </p>
          )}
          <div className="diff-summary-row">
            <span>{activeDiffPreview.toolName}</span>
            <strong>{activeDiffPreview.summary}</strong>
          </div>
          <div className="diff-table" role="table" aria-label={t('文件变更 Diff')}>
            {activeDiffPreview.diffLines.map((line, index) =>
              createElement(
                'div',
                {
                  className: 'diff-line',
                  'data-kind': line.kind,
                  key: `${index}-${line.kind}-${line.oldLine ?? ''}-${line.newLine ?? ''}`,
                  role: 'row',
                },
                createElement('span', null, line.oldLine ?? ''),
                createElement('span', null, line.newLine ?? ''),
                createElement('code', null, `${line.kind === 'add' ? '+ ' : line.kind === 'remove' ? '- ' : '  '}${line.text}`),
              ),
            )}
          </div>
        </>
      ) : (
        <div className="rail-empty-card">
          <FileCode size={18} />
          <strong>{t('暂无文件变更')}</strong>
          <span>{t('运行写入、编辑或删除文件的工具后，这里会显示 Diff。')}</span>
        </div>
      )}
    </div>
  );

  const renderWebPanel = () => (
    <div className="web-preview-pane">
      {webPreviewTabs.length > 0 && (
        <div className="web-tab-strip" role="tablist" aria-label={t('网页标签页')}>
          {webPreviewTabs.map(tab =>
            createElement(WebPreviewTabButton, {
              active: tab.id === activeWebPreviewId,
              key: tab.id,
              onActivate: activateWebPreviewTab,
              onClose: closeWebPreviewTab,
              tab,
            }),
          )}
        </div>
      )}
      <div className="dev-server-panel" data-state={devStatus?.state || 'idle'} data-compact={!showDevServerDetails}>
        <div className="dev-server-summary">
          <MonitorPlay size={14} />
          <div>
            <strong>{devStatus?.stack || t('前端预览')}</strong>
            <span>
              {devStatus?.url ||
                (devStatus?.state === 'error' ? t('当前工作区未识别到可启动的前端项目') : devStatus?.command || t('识别当前工作区并打开本地预览'))}
            </span>
          </div>
          <em>{t(devStateLabel(devStatus?.state))}</em>
        </div>
        <div className="dev-server-actions">
          <button className="dev-server-start-button" type="button" disabled={!activeWorkspacePath || devBusy} onClick={() => void startDevPreview()}>
            {devBusy ? <LoaderCircle className="spin" size={13} /> : <MonitorPlay size={13} />}
            {t('启动')}
          </button>
          <button
            className="dev-server-stop-button"
            type="button"
            disabled={!devStatus || !['starting', 'running'].includes(devStatus.state) || devBusy}
            onClick={() => void stopDevPreview()}
          >
            <Square size={12} />
            {t('停止')}
          </button>
          <button className="web-audit-button" type="button" disabled={auditBusy} onClick={() => void auditActivePreview()}>
            {auditBusy ? <LoaderCircle className="spin" size={13} /> : <ScanSearch size={13} />}
            {t('验收')}
          </button>
          <button
            className="dev-server-details-button"
            type="button"
            disabled={!hasDevServerDetails}
            aria-expanded={showDevServerDetails}
            onClick={() => setDevDetailsOpen(value => !value)}
          >
            {t('详情')}
          </button>
        </div>
        {showDevServerDetails && (devStatus?.url || devStatus?.reason || Boolean(devStatus?.logs.length)) && (
          <div className="dev-server-log">
            {devStatus?.url && <span>URL {devStatus.url}</span>}
            {devStatus?.state === 'error' && devStatus?.reason && <span>{devStatus.reason}</span>}
            {devStatus?.logs.slice(-2).map((line, index) => (
              <code key={`${index}-${line}`}>{line}</code>
            ))}
          </div>
        )}
        {previewAudit && (
          <div className="preview-audit-panel" data-status={previewAudit.status}>
            <strong>{previewAudit.status === 'ok' ? t('验收通过') : previewAudit.status === 'warning' ? t('需要复查') : t('验收失败')}</strong>
            <span>{previewAudit.reason}</span>
            {previewAudit.savedPath && <code>{previewAudit.savedPath}</code>}
          </div>
        )}
      </div>
      <div className="web-url-bar">
        <div className="web-url-nav-controls" aria-label={t('网页导航')}>
          <button
            type="button"
            className="web-toolbar-button web-back-button"
            title={t('后退')}
            disabled={!webNav.canGoBack}
            onClick={() => void window.metis?.previewCommand?.('back')}
          >
            <ArrowLeft size={13} />
          </button>
          <button
            type="button"
            className="web-toolbar-button web-forward-button"
            title={t('前进')}
            disabled={!webNav.canGoForward}
            onClick={() => void window.metis?.previewCommand?.('forward')}
          >
            <ArrowRight size={13} />
          </button>
          <button
            type="button"
            className="web-toolbar-button web-reload-button"
            title={activeWebTab?.loading ? t('停止加载') : t('刷新')}
            disabled={!webPreviewUrl}
            onClick={reloadActiveWeb}
          >
            {activeWebTab?.loading ? <X size={13} /> : <RefreshCw size={13} />}
          </button>
        </div>
        <Globe size={14} />
        <input
          className="web-url-input"
          value={webInput}
          placeholder="https://example.com"
          onChange={event => setWebInput(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter') openWebInput();
          }}
        />
        <div className="web-zoom-controls" aria-label={t('页面缩放')}>
          <button
            type="button"
            className="web-zoom-button"
            title={t('缩小页面')}
            disabled={!activeWebTab || activeWebZoom <= 0.5}
            onClick={() => setActiveZoom(activeWebZoom - 0.1)}
          >
            −
          </button>
          <button
            type="button"
            className="web-zoom-button"
            title={t('放大页面')}
            disabled={!activeWebTab || activeWebZoom >= 2}
            onClick={() => setActiveZoom(activeWebZoom + 0.1)}
          >
            +
          </button>
          <button
            type="button"
            className="web-zoom-button web-zoom-reset"
            title={`${t('当前 ')}${activeWebZoomPercent}%${t(' · 点击恢复 100%')}`}
            aria-label={`${t('当前页面缩放 ')}${activeWebZoomPercent}%${t('，点击恢复 100%')}`}
            disabled={!activeWebTab || activeWebZoom === 1}
            onClick={() => setActiveZoom(1)}
          >
            {activeWebZoomPercent}
          </button>
        </div>
        <div className="web-more-menu-wrap">
          <button
            type="button"
            className="web-more-button"
            title={t('更多网页操作')}
            aria-haspopup="menu"
            aria-expanded={webMoreOpen}
            onClick={() => setWebMoreOpen(value => !value)}
          >
            <MoreVertical size={14} />
          </button>
          {webMoreOpen && (
            <div className="web-more-menu" role="menu">
              <div className="web-more-status" data-state={devStatus?.state || 'idle'}>
                <strong>{devStatus?.stack || t('前端预览')}</strong>
                <span>
                  {devStatus?.url ||
                    (devStatus?.state === 'error' ? t('当前工作区未识别到可启动的前端项目') : devStatus?.command || t('识别当前工作区并打开本地预览'))}
                </span>
                <em>{t(devStateLabel(devStatus?.state))}</em>
              </div>
              <span className="web-more-menu-label">{t('前端预览')}</span>
              <button className="dev-server-start-button" type="button" role="menuitem" disabled={!activeWorkspacePath || devBusy} onClick={() => void startDevPreview()}>
                {devBusy ? t('启动中') : t('启动预览')}
              </button>
              <button
                className="dev-server-stop-button"
                type="button"
                role="menuitem"
                disabled={!devStatus || !['starting', 'running'].includes(devStatus.state) || devBusy}
                onClick={() => void stopDevPreview()}
              >
                {t('停止预览')}
              </button>
              <button className="web-audit-button" type="button" role="menuitem" disabled={auditBusy} onClick={() => void auditActivePreview()}>
                {auditBusy ? t('验收中') : t('视觉验收')}
              </button>
              <button
                className="dev-server-details-button"
                type="button"
                role="menuitem"
                disabled={!hasDevServerDetails}
                aria-expanded={showDevServerDetails}
                onClick={() => setDevDetailsOpen(value => !value)}
              >
                {showDevServerDetails ? t('收起详情') : t('查看详情')}
              </button>
              {showDevServerDetails && (devStatus?.url || devStatus?.reason || Boolean(devStatus?.logs.length)) && (
                <div className="dev-server-log">
                  {devStatus?.url && <span>URL {devStatus.url}</span>}
                  {devStatus?.state === 'error' && devStatus?.reason && <span>{devStatus.reason}</span>}
                  {devStatus?.logs.slice(-2).map((line, index) => createElement('code', { key: String(index) + '-' + line }, line))}
                </div>
              )}
              {previewAudit && (
                <div className="preview-audit-panel" data-status={previewAudit.status}>
                  <strong>{previewAudit.status === 'ok' ? t('验收通过') : previewAudit.status === 'warning' ? t('需要复查') : t('验收失败')}</strong>
                  <span>{previewAudit.reason}</span>
                  {previewAudit.savedPath && <code>{previewAudit.savedPath}</code>}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="web-external-wrap">
          <button
            type="button"
            className="web-external-button"
            title={t('系统浏览器打开')}
            disabled={!webPreviewUrl}
            onClick={() => {
              setWebMoreOpen(false);
              void openActiveWebExternal();
            }}
          >
            <ExternalLink size={14} />
          </button>
        </div>
      </div>
      {webPreviewUrl && (
        <div className="web-browser-toolbar" aria-label={t('网页控制栏')}>
          <button
            type="button"
            className="web-toolbar-button web-back-button"
            title={t('后退')}
            disabled={!webNav.canGoBack}
            onClick={() => void window.metis?.previewCommand?.('back')}
          >
            <ArrowLeft size={13} />
          </button>
          <button
            type="button"
            className="web-toolbar-button web-forward-button"
            title={t('前进')}
            disabled={!webNav.canGoForward}
            onClick={() => void window.metis?.previewCommand?.('forward')}
          >
            <ArrowRight size={13} />
          </button>
          <button
            type="button"
            className="web-toolbar-button web-reload-button"
            title={activeWebTab?.loading ? t('停止加载') : t('刷新')}
            onClick={reloadActiveWeb}
          >
            {activeWebTab?.loading ? <X size={13} /> : <RefreshCw size={13} />}
          </button>
        </div>
      )}
      {webError && (
        <p className="rail-warning">
          <AlertTriangle size={13} />
          {webError}
        </p>
      )}
      {activeWebTab?.loading && (
        <p className="web-status-line">
          <LoaderCircle className="spin" size={13} />
          {t('正在加载 ')}{activeWebTab.title}
        </p>
      )}
      {activeWebTab?.error && (
        <p className="rail-warning">
          <AlertTriangle size={13} />
          {activeWebTab.error}
        </p>
      )}
      {browserActivity && browserActivity.items.length > 0 && (
        <BrowserActivityPanel activity={browserActivity} t={t} />
      )}
      {webPreviewUrl ? (
        <div className="web-preview-frame" data-zoom={Math.round(activeWebZoom * 100)}>
          <div className="web-preview-host" data-preview-url={webPreviewUrl} ref={previewHostRef}>
            {previewFrozenSrc && (
              <img className="web-preview-frozen" src={previewFrozenSrc} alt="" draggable={false} />
            )}
          </div>
        </div>
      ) : (
        <div className="rail-empty-card">
          <Globe size={18} />
          <strong>{t('网页预览')}</strong>
          <span>{t('输入 URL 或点击聊天中的链接，在右栏并排查看。')}</span>
        </div>
      )}
    </div>
  );

  const renderPlanPanel = () => {
    const todos = planTodos ?? [];
    const total = todos.length;
    const statuses = todos.map(item => planTodoStatus(item.status));
    const doneCount = total > 0 ? statuses.filter(status => status === 'done').length : 0;
    const activeCount = statuses.filter(status => status === 'active').length;
    const issueCount = statuses.filter(status => status === 'failed' || status === 'blocked').length;
    const canceledCount = statuses.filter(status => status === 'canceled').length;
    const focus = planFocusTodo(todos);
    const focusLabel = planTodoLabel(focus.item, focus.index, t);
    const focusDetail = planTodoDetail(focus.item);
    const progress = total > 0 ? Math.round((doneCount / total) * 100) : 0;
    const actionsLocked = streaming || Boolean(planActionBusy);
    const canTargetStep = Boolean(focusLabel);
    return (
    <div className="plan-card-pane">
      {total > 0 ? (
        <div className="plan-card-todos">
          <div className="plan-card-todos-head">
            <div>
              <strong>{t('任务清单')}</strong>
              <span>{planOverviewText(total, doneCount, activeCount, issueCount + canceledCount, runtimeStatus, t)}</span>
            </div>
            <em>{doneCount}/{total} {t('完成')}</em>
          </div>
          <div className="plan-progress-track" aria-label={t('任务进度')}>
            <span style={{ width: `${progress}%` }} />
          </div>
          <div className="plan-step-focus" data-status={focus.status}>
            <div>
              <span>{focus.status === 'idle' ? t('当前步骤') : t(planTodoStatusLabel(focus.status))}</span>
              <strong>{focusLabel || t('等待任务清单')}</strong>
            </div>
            {focusDetail ? <p>{focusDetail}</p> : <p>{t('失败时可以重试当前步骤，或要求 Metis 换一种策略继续。')}</p>}
          </div>
          <ul className="plan-todo-list">
            {todos.map((item, index) => {
              const status = planTodoStatus(item.status);
              const label = planTodoLabel(item, index, t);
              return (
                <li key={String(item.id ?? index)} className="plan-todo-item" data-status={status}>
                  {status === 'done' ? (
                    <CircleCheck size={15} className="plan-todo-icon" />
                  ) : status === 'active' ? (
                    <LoaderCircle size={15} className="plan-todo-icon spin" />
                  ) : status === 'failed' || status === 'blocked' ? (
                    <AlertTriangle size={15} className="plan-todo-icon" />
                  ) : status === 'canceled' ? (
                    <X size={15} className="plan-todo-icon" />
                  ) : (
                    <Circle size={15} className="plan-todo-icon" />
                  )}
                  <span>{label}</span>
                  <em>{t(planTodoStatusLabel(status))}</em>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <div className="plan-card-empty">
          <StickyNote size={18} />
          <strong>Plan</strong>
          <span>{t('任务清单：智能体规划任务后，这里实时显示进度——完成打钩、进行中转圈、待办空心圆。')}</span>
        </div>
      )}
      <div className="plan-action-panel" data-streaming={streaming}>
        <div className="plan-action-head">
          <strong>{t('任务编排')}</strong>
          <span>{streaming ? t('当前运行中，完成或停止后可接管。') : t('失败后可从这里续跑。')}</span>
        </div>
        <div className="plan-action-grid">
          <button
            type="button"
            disabled={actionsLocked || !canTargetStep}
            title={t('重试当前步骤')}
            onClick={() => void submitPlanFollowUp('retry', focusLabel)}
          >
            <RefreshCw className={planActionBusy === 'retry' ? 'spin' : undefined} size={13} />
            <span>{t('重试当前')}</span>
          </button>
          <button
            type="button"
            disabled={actionsLocked || !canTargetStep}
            title={t('换一种策略继续')}
            onClick={() => void submitPlanFollowUp('strategy', focusLabel)}
          >
            <ArrowRight size={13} />
            <span>{t('换策略')}</span>
          </button>
          <button
            type="button"
            disabled={actionsLocked}
            title={t('回到上一个检查点')}
            onClick={() => void rewindPlanStep()}
          >
            <ArrowLeft size={13} />
            <span>{t('回到上步')}</span>
          </button>
          <button
            type="button"
            disabled={actionsLocked}
            title={t('手动接管后继续')}
            onClick={() => void submitPlanFollowUp('manual', focusLabel)}
          >
            <Check size={13} />
            <span>{t('接管后继续')}</span>
          </button>
        </div>
      </div>
      <AgentRuntimeProfileCard profile={agentRuntimeProfile} contextLedger={contextLedger} />
      <PlanRunMonitor
        backendReady={backendReady}
        loadChatSession={loadChatSession}
        selectSession={selectSession}
        sessions={sessions}
      />
      <div className="plan-card-metrics">
        <span>
          <b>{sessions.length}</b>
          Sessions
        </span>
        <span>
          <b>{workspaces.length}</b>
          Workspaces
        </span>
        <span>
          <b>{subagents.length}</b>
          Agents
        </span>
      </div>
    </div>
    );
  };

  const renderCardContent = (cardId: WorkspaceCardId) => {
    if (cardId === 'web') return renderWebPanel();
    if (cardId === 'terminal') return <TerminalPanel embedded onRequestClose={() => setWorkspaceCardVisible('terminal', false)} />;
    if (cardId === 'files') return renderFilesPanel();
    if (cardId === 'diff') return renderDiffPanel();
    if (cardId === 'activity') return renderActivityPanel();
    if (cardId === 'tool') return renderToolPanel();
    return renderPlanPanel();
  };

  const closeWorkspaceCard = (cardId: WorkspaceCardId) => {
    if (cardId === 'web') hidePreviewView();
    setWorkspaceCardVisible(cardId, false);
  };

  const renderWorkspaceCard = (cardId: WorkspaceCardId) => {
    const option = workspaceCardOptions.find(item => item.id === cardId) || workspaceCardOptions[0];
    const Icon = option.icon;
    return (
      <article className="workspace-card" data-card={cardId} key={cardId}>
        <header className="workspace-card-header">
          <div>
            <Icon size={14} />
            <strong>{option.label}</strong>
          </div>
          <button type="button" title={`${t('关闭 ')}${option.label}`} onClick={() => closeWorkspaceCard(cardId)}>
            <X size={13} />
          </button>
        </header>
        <div className="workspace-card-body">{renderCardContent(cardId)}</div>
      </article>
    );
  };

  return (
    <div className="right-rail-workspace">
      <div className="rail-resizer" onPointerDown={startResize} />
      <div className="right-rail-inner workspace-card-shell">
        <motion.div
          className="workspace-card-deck"
          data-empty={visibleWorkspaceColumns.length === 0}
          data-settling={workspaceSettling}
          ref={workspaceDeckRef}
          style={workspaceDeckStyle}
          layout
          transition={workspaceLayoutTransition}
        >
          <AnimatePresence initial={false} mode="popLayout">
            {visibleWorkspaceColumns.map((column, columnIndex) => {
            const visibleCards = column.visibleCards;
            const rowSplit = visibleCards.length === 2 ? workspaceCardRowSplits[column.id] : 50;
            const rowStyle = {
              '--workspace-row-split': `${rowSplit}%`,
              '--workspace-row-rest': `${100 - rowSplit}%`,
            } as CSSProperties;
            return (
              <motion.div className="workspace-card-column-wrap" key={column.id} layout transition={workspaceLayoutTransition}>
                <motion.div
                  className="workspace-card-column"
                  data-column={column.id}
                  data-count={visibleCards.length}
                  style={rowStyle}
                  layout
                  transition={workspaceLayoutTransition}
                >
                  <AnimatePresence initial={false} mode="popLayout">
                    {visibleCards.map((cardId, cardIndex) => (
                      <motion.div
                        className="workspace-card-slot"
                        key={cardId}
                        layout
                        initial={{ scale: 0.92, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0.95, opacity: 0, transition: { duration: 0.16, ease: [0.16, 1, 0.3, 1] } }}
                        transition={workspaceLayoutTransition}
                      >
                        {renderWorkspaceCard(cardId)}
                        {visibleCards.length === 2 && cardIndex === 0 && (
                          <div
                            className="workspace-row-resizer"
                            aria-label={`${column.id} column row resize`}
                            onPointerDown={event => startRowResize(column.id, event)}
                          />
                        )}
                      </motion.div>
                    ))}
                  </AnimatePresence>
                </motion.div>
                {columnIndex < visibleWorkspaceColumns.length - 1 && (
                  <div
                    className="workspace-column-resizer"
                    aria-label="Resize workspace card column"
                    data-boundary={`${column.id}-${visibleWorkspaceColumns[columnIndex + 1].id}`}
                    onPointerDown={event => startColumnResize(column.id, visibleWorkspaceColumns[columnIndex + 1].id, event)}
                  />
                )}
              </motion.div>
            );
          })}
          </AnimatePresence>
        </motion.div>
      </div>
    </div>
  );
}

function BrowserActivityPanel({ activity, t }: { activity: BrowserActivityPayload; t: (text: string) => string }) {
  const [open, setOpen] = useState(false);
  const recentItems = activity.items.slice(-8).reverse();
  const diagnostics = activity.diagnostics_counts || {};
  const hasDiagnostics = Boolean((diagnostics.console_errors || 0) + (diagnostics.exceptions || 0) + (diagnostics.network_failed || 0));

  return (
    <div className="browser-activity-panel" data-open={open} data-errors={activity.counts.errors > 0} data-blocked={activity.counts.blocked > 0}>
      <button className="browser-activity-head" type="button" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <Network size={14} />
        <div>
          <strong>{t('浏览器活动')}</strong>
          <span>
            {activity.counts.navigate} {t('导航')} · {activity.counts.observe} {t('观察')} · {activity.counts.action} {t('动作')} · {activity.counts.screenshot} {t('截图')}
          </span>
        </div>
        {(activity.counts.blocked > 0 || activity.counts.errors > 0 || hasDiagnostics) && (
          <em>
            {activity.counts.blocked > 0 ? `${activity.counts.blocked} ${t('拦截')}` : activity.counts.errors > 0 ? `${activity.counts.errors} ${t('失败')}` : t('诊断')}
          </em>
        )}
        <span className="browser-activity-caret">{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
      </button>
      {open && (
        <div className="browser-activity-list">
          {recentItems.map((item, index) => (
            <div className="browser-activity-item" data-event={item.event} data-ok={item.ok} data-blocked={item.blocked} key={`${item.at}-${index}`}>
              <span className="browser-activity-icon">{browserActivityIcon(item)}</span>
              <div>
                <strong>{item.summary || browserActivityFallbackSummary(item, t)}</strong>
                <span>{browserActivityMeta(item, t)}</span>
                {item.error && <code>{item.error}</code>}
                {item.saved_path && <code>{item.saved_path}</code>}
              </div>
              <time>{relativeActivityTime(item.at, t)}</time>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function browserActivityIcon(item: BrowserActivityItem) {
  if (!item.ok || item.blocked) return <AlertTriangle size={13} />;
  if (item.event === 'navigate') return <Globe size={13} />;
  if (item.event === 'observe') return <ScanSearch size={13} />;
  if (item.event === 'screenshot') return <ImageIcon size={13} />;
  return <CircleCheck size={13} />;
}

function browserActivityFallbackSummary(item: BrowserActivityItem, t: (text: string) => string): string {
  if (item.event === 'navigate') return item.ok ? t('导航完成') : t('导航失败');
  if (item.event === 'observe') return `${t('观察页面')} ${item.element_count || 0}`;
  if (item.event === 'screenshot') return item.ok ? t('截图完成') : t('截图失败');
  if (item.blocked) return t('动作已拦截');
  return item.ok ? t('动作完成') : t('动作失败');
}

function browserActivityMeta(item: BrowserActivityItem, t: (text: string) => string): string {
  const parts: string[] = [];
  if (item.target) parts.push(item.target);
  if (item.event === 'observe' && item.text_length) parts.push(`${item.text_length} ${t('字')}`);
  if (item.event === 'screenshot' && item.width && item.height) parts.push(`${item.width}x${item.height}`);
  if (item.risk?.summary) parts.push(item.risk.summary);
  if (item.navigation_resolution && typeof item.navigation_resolution.reason === 'string') {
    parts.push(item.navigation_resolution.reason);
  }
  return parts.join(' · ') || item.title || item.url || t('Preview');
}

function relativeActivityTime(value: string, t: (text: string) => string): string {
  const timestamp = Date.parse(value || '');
  if (!Number.isFinite(timestamp)) return '';
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 5) return t('刚刚');
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function AgentRuntimeProfileCard({ profile, contextLedger }: { profile: AgentRuntimeProfilePayload | null; contextLedger: ContextLedger | null }) {
  const t = useT();
  const cacheRate = Math.round((contextLedger?.cacheHitRate || 0) * 100);
  const cacheDetail = contextLedger
    ? `${compactMetricNumber(contextLedger.cacheHitTokens)} ${t('命中')} · ${compactMetricNumber(contextLedger.cacheMissTokens)} ${t('未命中')}`
    : t('等待下一次运行');
  if (!profile) {
    return (
      <section className="agent-runtime-card" data-empty="true">
        <header>
          <ShieldCheck size={14} />
          <div>
            <strong>{t('Agent Runtime')}</strong>
            <span>{t('当前会话暂无运行时画像。')}</span>
          </div>
        </header>
        <div className="agent-runtime-grid">
          <RuntimeMetric label={t('Cache')} value={contextLedger ? `${cacheRate}%` : '--'} detail={cacheDetail} />
        </div>
      </section>
    );
  }

  const workers = profile.coordinator.workers || [];
  const doneWorkers = workers.filter(worker => worker.status === 'done').length;
  const issueWorkers = workers.filter(worker => worker.status === 'error').length;
  const riskyContracts = profile.toolContracts.items.filter(item => item.requiresPermission).length;
  const promptLayers = profile.promptRuntime.stablePrefix.length + profile.promptRuntime.sessionSuffix.length + profile.promptRuntime.requestSuffix.length;

  return (
    <section className="agent-runtime-card" aria-label={t('Agent Runtime')}>
      <header>
        <ShieldCheck size={14} />
        <div>
          <strong>{t('Agent Runtime')}</strong>
          <span>{t(runtimeProfileSubtitle(profile.proactive.state))}</span>
        </div>
        <em>{profile.promptRuntime.version || 'v2'}</em>
      </header>
      <div className="agent-runtime-grid">
        <RuntimeMetric label={t('Prompt')} value={`${promptLayers}`} detail={profile.promptRuntime.cachePolicy} />
        <RuntimeMetric label={t('Tools')} value={`${riskyContracts}/${profile.toolContracts.items.length}`} detail={t('需权限')} />
        <RuntimeMetric label={t('Workers')} value={`${doneWorkers}/${workers.length}`} detail={issueWorkers ? t('有错误') : t('新鲜验收')} />
        <RuntimeMetric label={t('Ticks')} value={`${profile.proactive.tickSeconds || 15}s`} detail={profile.proactive.enabled ? t('后台可用') : t('需手动开启')} />
        <RuntimeMetric label={t('Cache')} value={contextLedger ? `${cacheRate}%` : '--'} detail={cacheDetail} />
      </div>
      {workers.length > 0 && (
        <div className="agent-worker-strip">
          {workers.map(worker => (
            <span key={worker.id || worker.name} data-status={worker.status}>
              {t(workerLabel(worker.name))}
              <b>{worker.progress}%</b>
            </span>
          ))}
        </div>
      )}
      <p>{profile.coordinator.nextAction || t('等待下一步任务。')}</p>
      {profile.promptRuntime.scratchpadPath && <small title={profile.promptRuntime.scratchpadPath}>{compactPath(profile.promptRuntime.scratchpadPath)}</small>}
    </section>
  );
}

function compactMetricNumber(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return `${Math.round(value)}`;
}

function RuntimeMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <span className="agent-runtime-metric">
      <b>{value}</b>
      <em>{label}</em>
      <small>{detail}</small>
    </span>
  );
}

function runtimeProfileSubtitle(state: string): string {
  if (state === 'running') return '长任务正在运行';
  if (state === 'available') return '长任务模式已授权';
  return '后台主动性保持手动开启';
}

function workerLabel(name: string): string {
  if (name === 'research') return '研究';
  if (name === 'implementation') return '实现';
  if (name === 'verification') return '验收';
  return name || 'Worker';
}

function PlanRunMonitor({
  backendReady,
  loadChatSession,
  selectSession,
  sessions,
}: {
  backendReady: boolean;
  sessions: SessionMeta[];
  selectSession: (sessionId: string) => Promise<void>;
  loadChatSession: (sessionId: string | null, options?: { force?: boolean }) => Promise<void>;
}) {
  const t = useT();
  const [runs, setRuns] = useState<ChatRunPayload[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const sessionById = useMemo(() => new Map(sessions.map(session => [session.id, session])), [sessions]);
  const activeRuns = useMemo(() => runs.filter(run => isActiveRunStatus(run.status)), [runs]);
  const latestRun = activeRuns[0] || runs[0] || null;
  const latestSession = latestRun ? sessionById.get(latestRun.sessionId) : undefined;
  const latestActive = latestRun ? isActiveRunStatus(latestRun.status) : false;
  const elapsed = latestRun
    ? formatElapsed(latestRun.startedAt || latestRun.createdAt, latestRun.finishedAt || (latestActive ? Date.now() / 1000 : latestRun.updatedAt))
    : '';

  const refresh = useCallback(async () => {
    if (!backendReady) return;
    setBusy(true);
    try {
      const payload = await getChatRuns();
      setRuns(payload.runs);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [backendReady]);

  useEffect(() => {
    if (!backendReady) {
      setRuns([]);
      return undefined;
    }
    let disposed = false;
    const refreshSafely = async () => {
      if (disposed) return;
      await refresh();
    };
    void refreshSafely();
    const timer = window.setInterval(() => void refreshSafely(), 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [backendReady, refresh]);

  const jumpToRunSession = async () => {
    if (!latestRun?.sessionId) return;
    await selectSession(latestRun.sessionId);
    await loadChatSession(latestRun.sessionId);
  };

  return (
    <section className="plan-run-monitor" data-active={latestActive}>
      <header>
        <div>
          <strong>{t('长任务模式')}</strong>
          <span>
            {latestActive
              ? `${activeRuns.length} ${t('运行中')} · ${latestRun?.phase || t('执行中')}`
              : latestRun
                ? `${t('最近任务')} · ${t(statusLabel(latestRun.status))}`
                : t('暂无后台任务')}
          </span>
        </div>
        <button type="button" title={t('刷新后台任务')} disabled={!backendReady} onClick={() => void refresh()}>
          <RefreshCw className={busy ? 'spin' : undefined} size={13} />
        </button>
      </header>
      {!backendReady ? (
        <p className="plan-run-note">
          <LoaderCircle className="spin" size={13} />
          {t('后端连接后显示可恢复任务。')}
        </p>
      ) : error ? (
        <p className="plan-run-note" data-tone="error">
          <AlertTriangle size={13} />
          {error}
        </p>
      ) : latestRun ? (
        <button className="plan-run-row" type="button" onClick={() => void jumpToRunSession()}>
          <span className="run-status-dot" data-tone={latestRun.status === 'failed' ? 'error' : latestActive ? 'running' : 'done'} />
          <span>
            <strong>{latestSession?.title || latestRun.sessionId || 'Metis run'}</strong>
            <small>
              {t(statusLabel(latestRun.status))}
              {elapsed ? ` · ${elapsed}` : ''}
              {latestRun.lastSeq || latestRun.eventCount ? ` · #${latestRun.lastSeq || latestRun.eventCount}` : ''}
            </small>
          </span>
          <ArrowRight size={13} />
        </button>
      ) : (
        <p className="plan-run-note">
          <Network size={13} />
          {t('关闭页面再回来，也会在这里接回最近任务。')}
        </p>
      )}
    </section>
  );
}

function RunActivityCenter({
  backendReady,
  loadChatSession,
  selectSession,
  sessions,
  workspaces,
}: {
  backendReady: boolean;
  sessions: SessionMeta[];
  workspaces: Workspace[];
  selectSession: (sessionId: string) => Promise<void>;
  loadChatSession: (sessionId: string | null, options?: { force?: boolean }) => Promise<void>;
}) {
  const t = useT();
  const setToolPreview = useUiStore(state => state.setToolPreview);
  const stopChatRun = useChatStore(state => state.stop);
  const [runs, setRuns] = useState<ChatRunPayload[]>([]);
  const [providerStatus, setProviderStatus] = useState<ProviderStatusPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [cancelingId, setCancelingId] = useState('');
  const sessionById = useMemo(() => new Map(sessions.map(session => [session.id, session])), [sessions]);
  const workspaceById = useMemo(() => new Map(workspaces.map(workspace => [workspace.id, workspace])), [workspaces]);
  const activeRuns = useMemo(() => runs.filter(run => isActiveRunStatus(run.status)), [runs]);
  const recentRuns = useMemo(() => runs.filter(run => !isActiveRunStatus(run.status)).slice(0, 4), [runs]);

  const refresh = useCallback(async () => {
    if (!backendReady) return;
    setBusy(true);
    try {
      const payload = await getChatRuns();
      const provider = await getProviderStatus().catch(() => null);
      setRuns(payload.runs);
      setProviderStatus(provider);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [backendReady]);

  useEffect(() => {
    if (!backendReady) {
      setRuns([]);
      return undefined;
    }
    let disposed = false;
    const refreshSafely = async () => {
      if (disposed) return;
      await refresh();
    };
    void refreshSafely();
    const timer = window.setInterval(() => void refreshSafely(), 1200);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [backendReady, refresh]);

  const jumpToRunSession = async (run: ChatRunPayload) => {
    if (!run.sessionId) return;
    await selectSession(run.sessionId);
    await loadChatSession(run.sessionId);
  };

  const cancelRun = async (run: ChatRunPayload) => {
    if (!run.runId || !isActiveRunStatus(run.status)) return;
    setCancelingId(run.runId);
    try {
      if (run.sessionId && run.sessionId === useSessionStore.getState().activeSessionId) {
        stopChatRun();
      }
      const next = await cancelChatRun(run.runId);
      setRuns(state => state.map(item => (item.runId === next.runId ? next : item)));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCancelingId('');
    }
  };

  return (
    <section className="run-activity-center" aria-label={t('后台任务中心')}>
      <header className="activity-section-head">
        <div>
          <strong>{t('后台运行')}</strong>
          <span>
            {activeRuns.length} {t('运行中')}
            {recentRuns.length ? ` · ${recentRuns.length} ${t('最近任务')}` : ''}
          </span>
        </div>
        <button type="button" title={t('刷新后台任务')} onClick={() => void refresh()}>
          <RefreshCw className={busy ? 'spin' : undefined} size={13} />
        </button>
      </header>
      {!backendReady && (
        <div className="run-activity-empty">
          <LoaderCircle className="spin" size={16} />
          <span>{t('后端连接后显示后台任务。')}</span>
        </div>
      )}
      {backendReady && error && (
        <div className="run-activity-warning">
          <AlertTriangle size={14} />
          <span>{error}</span>
        </div>
      )}
      {backendReady && !error && activeRuns.length === 0 && recentRuns.length === 0 && (
        <div className="run-activity-empty">
          <Network size={16} />
          <span>{t('暂无后台任务。')}</span>
        </div>
      )}
      {activeRuns.length > 0 && (
        <div className="run-card-list" aria-label={t('运行中任务')}>
          {activeRuns.map(run => (
            <RunActivityCard
              canceling={cancelingId === run.runId}
              key={run.runId}
              onCancel={cancelRun}
              onJump={jumpToRunSession}
              run={run}
              session={sessionById.get(run.sessionId)}
              workspace={workspaceById.get(sessionById.get(run.sessionId)?.workspaceId || '')}
            />
          ))}
        </div>
      )}
      {recentRuns.length > 0 && (
        <details className="run-recent-details">
          <summary>{t('最近任务')}</summary>
          <div className="run-card-list" aria-label={t('最近任务')}>
            {recentRuns.map(run => (
              <RunActivityCard
                canceling={false}
                key={run.runId}
                onCancel={cancelRun}
                onJump={jumpToRunSession}
                run={run}
                session={sessionById.get(run.sessionId)}
                workspace={workspaceById.get(sessionById.get(run.sessionId)?.workspaceId || '')}
              />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}


function RunActivityCard({
  canceling,
  onCancel,
  onJump,
  run,
  session,
  workspace,
}: {
  canceling: boolean;
  run: ChatRunPayload;
  session?: SessionMeta;
  workspace?: Workspace;
  onJump: (run: ChatRunPayload) => Promise<void>;
  onCancel: (run: ChatRunPayload) => Promise<void>;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const active = isActiveRunStatus(run.status);
  // 状态色点：失败=红、运行中=蓝、其余（完成）=灰
  const dotTone = run.status === 'failed' ? 'error' : active ? 'running' : 'done';
  const elapsed = formatElapsed(run.startedAt || run.createdAt, run.finishedAt || (active ? Date.now() / 1000 : run.updatedAt));

  return (
    <article className="run-activity-card" data-status={run.status} data-open={open}>
      <div className="run-card-row">
        <button
          className="run-card-caret"
          type="button"
          onClick={() => setOpen(value => !value)}
          aria-label={open ? t('收起详情') : t('展开详情')}
        >
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        <span className="run-status-dot" data-tone={dotTone} />
        <button className="run-card-open" type="button" onClick={() => void onJump(run)} title={t('跳转到会话')}>
          <strong>{session?.title || run.sessionId || 'Metis run'}</strong>
          <span>{t(workspace?.name || session?.workspaceId || '当前工作区')} · {t(statusLabel(run.status))}</span>
        </button>
        <em>{elapsed || t('刚刚')}</em>
      </div>
      {open && (
        <div className="run-card-details">
          <div className="run-card-meta">
            <span>{run.phase || 'phase unknown'}</span>
            <span>#{run.lastSeq || run.eventCount || 0}</span>
          </div>
          {run.error && (
            <p className="run-card-error">
              <AlertTriangle size={12} />
              {run.error}
            </p>
          )}
          {active && (
            <button
              className="run-cancel-button"
              type="button"
              disabled={canceling || run.status === 'canceling'}
              onClick={() => void onCancel(run)}
            >
              {canceling || run.status === 'canceling' ? t('取消中') : t('取消')}
            </button>
          )}
        </div>
      )}
    </article>
  );
}

function isActiveRunStatus(status: string): boolean {
  return status === 'queued' || status === 'running' || status === 'canceling';
}

function statusLabel(status: string): string {
  if (status === 'queued') return '排队';
  if (status === 'running') return '运行中';
  if (status === 'canceling') return '取消中';
  if (status === 'done') return '完成';
  if (status === 'failed') return '失败';
  if (status === 'canceled') return '已取消';
  return status || '未知';
}

function formatRunTime(value: number): string {
  if (!value) return '';
  return new Date(value * 1000).toLocaleString();
}

function formatElapsed(start: number, end: number): string {
  if (!start || !end || end < start) return '';
  const seconds = Math.max(0, Math.round(end - start));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

function TreeNode({ node, onPick, activePath }: { node: WorkspaceTreeNode; onPick: (path: string) => void; activePath: string }) {
  const [open, setOpen] = useState(false);
  if (node.type === 'directory') {
    return (
      <div className="tree-node">
        <button type="button" onClick={() => setOpen(value => !value)}>
          <Folder size={13} />
          <span>{node.name}</span>
        </button>
        {open && node.children && (
          <div className="tree-children">
            {node.children.map(child => createElement(TreeNode, { key: child.path || child.name, node: child, onPick, activePath }))}
          </div>
        )}
      </div>
    );
  }
  return (
    <button className="tree-file" type="button" data-active={activePath === node.path} onClick={() => onPick(node.path)}>
      <FileText size={13} />
      <span>{node.name}</span>
    </button>
  );
}

function WebPreviewTabButton({
  active,
  onActivate,
  onClose,
  tab,
}: {
  active: boolean;
  onActivate: (id: string) => void;
  onClose: (id: string) => void;
  tab: WebPreviewTab;
}) {
  const t = useT();
  return (
    <button
      className="web-preview-tab"
      type="button"
      role="tab"
      aria-selected={active}
      data-active={active}
      title={tab.url}
      onClick={() => onActivate(tab.id)}
    >
      {tab.loading ? <LoaderCircle className="spin" size={12} /> : <Globe size={12} />}
      <span>{tab.title}</span>
      <span
        className="web-tab-close"
        role="button"
        tabIndex={0}
        aria-label={`${t('关闭 ')}${tab.title}`}
        onClick={event => {
          event.stopPropagation();
          onClose(tab.id);
        }}
        onKeyDown={event => {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault();
          event.stopPropagation();
          onClose(tab.id);
        }}
      >
        <X size={12} />
      </span>
    </button>
  );
}

function webTabLabel(url: string): string {
  try {
    return new URL(url).hostname || url;
  } catch {
    return url || '网页';
  }
}

function devStateLabel(state: DevServerStatus['state'] | undefined): string {
  if (state === 'detected') return '已识别';
  if (state === 'starting') return '启动中';
  if (state === 'running') return '运行中';
  if (state === 'error') return '失败';
  if (state === 'exited') return '已停止';
  return '待识别';
}

function FileMeta({ file }: { file: WorkspaceFile }) {
  const Icon = file.type === 'image' ? ImageIcon : file.type === 'binary' ? Binary : FileCode;
  return (
    <div className="file-meta">
      <Icon size={15} />
      <div>
        <strong>{file.name}</strong>
        <span>{file.path}</span>
      </div>
      <em>{file.type}</em>
      <em>{formatBytes(file.size)}</em>
    </div>
  );
}

function ImagePreview({ file }: { file: WorkspaceFile }) {
  const [src, setSrc] = useState('');
  useEffect(() => {
    if (!file.previewUrl) return;
    void apiBase().then(base => setSrc(`${base}${file.previewUrl}`));
  }, [file.previewUrl]);
  return src ? <img className="image-preview" src={src} alt={file.name} /> : null;
}

function previewStats(content: string): { lines: number; chars: number } {
  if (!content) return { lines: 0, chars: 0 };
  return { lines: content.split(/\r?\n/).length, chars: content.length };
}

function compactPath(value: string): string {
  const parts = value.split(/[\\/]/).filter(Boolean);
  if (parts.length <= 3) return value;
  return `.../${parts.slice(-3).join('/')}`;
}

function diffKindLabel(kind: FileChangeFileSummary['kind']): string {
  if (kind === 'create') return '新增';
  if (kind === 'delete') return '删除';
  if (kind === 'modify') return '修改';
  return '变更';
}

function diffRevertLabel(status: string): string {
  if (status === 'reverted') return '已撤销';
  if (status === 'conflict') return '冲突';
  if (status === 'blocked') return '已拦截';
  return status || '待处理';
}

function diffRevertItemFor(preview: FileChangePreview | null | undefined, items: FileChangeRevertItem[]): FileChangeRevertItem | null {
  if (!preview) return null;
  return items.find(item => item.id === preview.id || item.path === preview.path) || null;
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
