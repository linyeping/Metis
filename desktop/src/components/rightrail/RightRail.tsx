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
import { apiBase, cancelChatRun, getChatRuns, getProviderStatus, getResearchJob, getResearchJobs, getWorkspaceFile, getWorkspaceTree, pingHealth } from '../../lib/api';
import { DOCUMENT_LIBRARY_EVENT, listDocumentLibraryItems, syncDocumentLibraryFromArtifacts, upsertDocumentLibraryItem, type DocumentLibraryItem } from '../../lib/documentLibrary';
import type { FileChangeFileSummary, FileChangePreview } from '../../lib/diffPreview';
import type { BrowserActivityItem, BrowserActivityPayload, ChatMessage, ChatRunPayload, ChatSubagentEvent, ChatTodoItem, CoworkPlanSnapshot, CoworkPlanSubrun, DevServerStatus, ParsedFile, PreviewAuditResult, ProviderStatusPayload, ResearchJob, ResearchJobPhase, ResearchJobSource, RuntimeStatus, SessionMeta, Workspace, WorkspaceFile, WorkspaceTreeNode } from '../../lib/types';
import type { FileChangeRevertItem } from '../../lib/types';
import { isPreviewableWebFilePath, localFilePreviewUrl } from '../../lib/webPreview';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore, type WebPreviewTab, type WorkspaceCardColumnId, type WorkspaceCardId } from '../../store/uiStore';
import { CoworkActivityPanel } from '../chat/CoworkActivityPanel';
import { MarkdownText } from '../chat/threadUtils';
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
  { id: 'research', label: 'Research', icon: ScanSearch },
  { id: 'session', label: '会话文件', icon: Folder },
  { id: 'tool', label: 'Tool output', icon: Wrench },
];

const workspaceCardColumns: Array<{ id: WorkspaceCardColumnId; cards: WorkspaceCardId[] }> = [
  { id: 'left', cards: ['web', 'terminal'] },
  { id: 'middle', cards: ['files', 'diff'] },
  { id: 'right', cards: ['activity', 'plan', 'research', 'session'] },
];

type PlanTodoStatus = 'done' | 'active' | 'pending' | 'blocked' | 'failed' | 'canceled';
type PlanAgentTaskStatus = 'planned' | 'running' | 'done' | 'error';

interface PlanAgentTask {
  id: string;
  title: string;
  status: PlanAgentTaskStatus;
  progress: number;
  summary: string;
  prompt: string;
  resultText: string;
  startedAt?: number;
  finishedAt?: number;
}

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

function planOverviewText(
  total: number,
  doneCount: number,
  activeCount: number,
  issueCount: number,
  runtimeStatus: RuntimeStatus | null,
  t: (text: string) => string,
): string {
  if (runtimeStatus?.phase === 'todo_progress' && runtimeStatus.display) return runtimeStatus.display;
  if (issueCount > 0) return t('有步骤失败或受阻。');
  if (activeCount > 0) return t('智能体正在推进当前步骤。');
  if (total > 0 && doneCount >= total) return t('任务清单已完成。');
  if (total > 0) return t('等待智能体继续执行下一步。');
  return t('等待任务清单。');
}

function buildPlanAgentTasks(subagents: ChatSubagentEvent[], plan: CoworkPlanSnapshot | null): PlanAgentTask[] {
  const planSubruns = Array.isArray(plan?.subruns) ? plan.subruns : [];
  const itemById = new Map(subagents.map(item => [item.taskId, item]));
  const used = new Set<string>();
  const tasks: PlanAgentTask[] = [];

  for (const subrun of planSubruns) {
    const id = planAgentSubrunId(subrun, tasks.length + 1);
    const title = textFromUnknown(subrun.title) || textFromUnknown(subrun.name) || id;
    const item = itemById.get(id) || subagents.find(candidate => candidate.name === title);
    if (item) used.add(item.taskId);
    tasks.push(planAgentTaskFrom(item, subrun, tasks.length + 1));
  }

  for (const item of subagents) {
    if (used.has(item.taskId)) continue;
    tasks.push(planAgentTaskFrom(item, null, tasks.length + 1));
  }

  return tasks;
}

function planAgentSubrunId(subrun: CoworkPlanSubrun, index: number): string {
  return (
    textFromUnknown(subrun.subrun_id) ||
    textFromUnknown(subrun.task_id) ||
    textFromUnknown(subrun.run_id) ||
    textFromUnknown(subrun.title) ||
    `subrun-${index}`
  );
}

function planAgentTaskFrom(item: ChatSubagentEvent | undefined, subrun: CoworkPlanSubrun | null, index: number): PlanAgentTask {
  const result = recordFromUnknown(item?.result);
  const worktree = recordFromUnknown(result.worktree);
  const id = item?.taskId || (subrun ? planAgentSubrunId(subrun, index) : `subrun-${index}`);
  const title = item?.name || textFromUnknown(subrun?.title) || textFromUnknown(subrun?.name) || `${index}. Subrun`;
  const summary =
    item?.summary ||
    textFromUnknown(result.summary) ||
    textFromUnknown(result.message) ||
    textFromUnknown(subrun?.status);
  const prompt = textFromUnknown(subrun?.prompt);
  const resultText = compactPlanAgentResult(item?.result);
  const worktreeId = textFromUnknown(result.worktree_id) || textFromUnknown(worktree.worktree_id) || textFromUnknown(subrun?.worktree_id);
  const worktreeRoot =
    textFromUnknown(result.worktree_workspace_root) ||
    textFromUnknown(worktree.worktree_workspace_root) ||
    textFromUnknown(worktree.path) ||
    textFromUnknown(subrun?.worktree_workspace_root);
  const details = [
    summary,
    prompt ? `Prompt: ${prompt}` : '',
    worktreeId ? `Worktree: ${worktreeId}` : '',
    worktreeRoot ? `Path: ${compactPath(worktreeRoot)}` : '',
  ].filter(Boolean);

  return {
    id,
    title,
    status: planAgentStatus(item?.status, textFromUnknown(subrun?.status)),
    progress: clampPercent(item?.progress ?? planAgentProgressFromStatus(textFromUnknown(subrun?.status))),
    summary: details[0] || '',
    prompt,
    resultText: [details.slice(1).join('\n'), resultText].filter(Boolean).join('\n\n'),
    startedAt: item?.startedAt,
    finishedAt: item?.finishedAt || item?.updatedAt,
  };
}

function planAgentStatus(itemStatus: ChatSubagentEvent['status'] | undefined, planStatus: string): PlanAgentTaskStatus {
  if (itemStatus === 'running') return 'running';
  if (itemStatus === 'done') return 'done';
  if (itemStatus === 'error') return 'error';
  const value = planStatus.toLowerCase();
  if (['running', 'active', 'in_progress', 'in-progress'].includes(value)) return 'running';
  if (['done', 'complete', 'completed', 'finished'].includes(value)) return 'done';
  if (['failed', 'failure', 'error', 'blocked'].includes(value)) return 'error';
  return 'planned';
}

function planAgentProgressFromStatus(status: string): number {
  const value = status.toLowerCase();
  if (['done', 'complete', 'completed', 'finished'].includes(value)) return 100;
  if (['running', 'active', 'in_progress', 'in-progress'].includes(value)) return 35;
  if (['failed', 'failure', 'error', 'blocked'].includes(value)) return 100;
  return 0;
}

function planAgentStatusLabel(status: PlanAgentTaskStatus): string {
  if (status === 'running') return '正在运行';
  if (status === 'done') return '已完成';
  if (status === 'error') return '失败';
  return '待开始';
}

function compactPlanAgentResult(value: unknown): string {
  if (value === undefined || value === null) return '';
  let text = '';
  if (typeof value === 'string') {
    text = value;
  } else {
    try {
      text = JSON.stringify(value, null, 2) || '';
    } catch {
      text = String(value);
    }
  }
  return text.length > 1600 ? `${text.slice(0, 1600).trimEnd()}...` : text;
}

function planAgentElapsed(startedAt?: number, finishedAt?: number): string {
  if (!startedAt || !finishedAt || finishedAt < startedAt) return '';
  const ms = finishedAt - startedAt;
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function clampPercent(value: number): number {
  return Math.min(Math.max(Math.round(value || 0), 0), 100);
}

function recordFromUnknown(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function textFromUnknown(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
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
  const appMode = useUiStore(state => state.appMode);
  const t = useT();
  const previewPath = useUiStore(state => state.previewPath);
  const previewFrozenSrc = useUiStore(state => state.previewFrozenSrc);
  const setPreviewFrozenSrc = useUiStore(state => state.setPreviewFrozenSrc);
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
  const coworkPlan = useChatStore(state => state.coworkPlan);
  const chatMessages = useChatStore(state => state.messages);
  const pendingAttachments = useChatStore(state => state.attachments);
  const planTodos = useChatStore(state => state.planTodos);
  const streaming = useChatStore(state => state.streaming);
  const runtimeStatus = useChatStore(state => state.runtimeStatus);
  const stopChatRun = useChatStore(state => state.stop);
  const clearSubagents = useChatStore(state => state.clearSubagents);
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
  const activeResearchJobId = useUiStore(state => state.activeResearchJobId);
  const workspaceCardColumnWidths = useUiStore(state => state.workspaceCardColumnWidths);
  const workspaceCardRowSplits = useUiStore(state => state.workspaceCardRowSplits);
  const setWorkspaceCardVisible = useUiStore(state => state.setWorkspaceCardVisible);
  const setWorkspaceCardColumnWidths = useUiStore(state => state.setWorkspaceCardColumnWidths);
  const setWorkspaceCardRowSplit = useUiStore(state => state.setWorkspaceCardRowSplit);
  const setResearchReportView = useUiStore(state => state.setResearchReportView);
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
  const [previewState, setPreviewState] = useState<PreviewStatePayload | null>(null);
  const [browserActivity, setBrowserActivity] = useState<BrowserActivityPayload | null>(null);
  const [auditBusy, setAuditBusy] = useState(false);
  const [devDetailsOpen, setDevDetailsOpen] = useState(false);
  const [workspaceSettling, setWorkspaceSettling] = useState(false);
  const [openPlanAgentTasks, setOpenPlanAgentTasks] = useState<Record<string, boolean>>({});
  const [researchJobs, setResearchJobs] = useState<ResearchJob[]>([]);
  const [researchJob, setResearchJob] = useState<ResearchJob | null>(null);
  const [researchError, setResearchError] = useState('');
  const [researchSourceFocusId, setResearchSourceFocusId] = useState('');
  const previewHostRef = useRef<HTMLDivElement | null>(null);
  const zoomFrameRef = useRef<number | null>(null);
  const workspaceDeckRef = useRef<HTMLDivElement | null>(null);
  const researchSourceListRef = useRef<HTMLDivElement | null>(null);
  const workspaceFilesScopeRef = useRef(activeWorkspaceId);
  const activeWorkspacePath = workspaces.find(workspace => workspace.id === activeWorkspaceId)?.path || '';
  const activeWebTab = useMemo(() => webPreviewTabs.find(tab => tab.id === activeWebPreviewId) || null, [activeWebPreviewId, webPreviewTabs]);
  const activeWebZoom = activeWebTab?.zoom || 1;
  const activeWebZoomPercent = Math.round(activeWebZoom * 100);
  const webCardVisible = workspaceCardVisibility.web;
  const canShowWebPreview = appMode !== 'chat' && rightRailOpen && webCardVisible;
  const showFrozenPreview = Boolean(previewFrozenSrc && canShowWebPreview && webPreviewUrl);
  const researchCardVisible = workspaceCardVisibility.research;
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
  const togglePlanAgentTask = useCallback((taskId: string) => {
    setOpenPlanAgentTasks(current => ({ ...current, [taskId]: !current[taskId] }));
  }, []);

  const loadTree = async () => {
    const requestWorkspaceId = activeWorkspaceId;
    if (!backendReady) {
      setTree([]);
      setError(null);
      return;
    }
    try {
      setError(null);
      const nextTree = await getWorkspaceTree();
      if (workspaceFilesScopeRef.current !== requestWorkspaceId) return;
      setTree(nextTree);
    } catch (err) {
      if (workspaceFilesScopeRef.current !== requestWorkspaceId) return;
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    if (workspaceFilesScopeRef.current === activeWorkspaceId) return;
    workspaceFilesScopeRef.current = activeWorkspaceId;
    setTree([]);
    setFile(null);
    setError(null);
    if (previewPath) useUiStore.setState({ previewPath: null });
  }, [activeWorkspaceId, previewPath]);

  useEffect(() => {
    void loadTree();
  }, [activeWorkspaceId, backendReady, workspaceRefreshNonce]);

  useEffect(() => {
    if (!backendReady || !previewPath) {
      setFile(null);
      return;
    }
    const requestWorkspaceId = activeWorkspaceId;
    const requestPath = previewPath;
    let cancelled = false;
    setError(null);
    void getWorkspaceFile(previewPath)
      .then(nextFile => {
        if (
          cancelled ||
          workspaceFilesScopeRef.current !== requestWorkspaceId ||
          useUiStore.getState().previewPath !== requestPath
        ) {
          return;
        }
        setFile(nextFile);
      })
      .catch(err => {
        if (
          cancelled ||
          workspaceFilesScopeRef.current !== requestWorkspaceId ||
          useUiStore.getState().previewPath !== requestPath
        ) {
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [activeWorkspaceId, backendReady, previewPath, workspaceRefreshNonce]);

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
      const tabId = payload.tab_id || payload.tabId || useUiStore.getState().activeWebPreviewId;
      if (!tabId || tabId !== useUiStore.getState().activeWebPreviewId) return;
      setPreviewState(payload);
      const patch: Partial<WebPreviewTab> = {};
      if (payload.error !== undefined) patch.error = payload.error;
      if (payload.loading !== undefined || payload.state) {
        patch.loading = payload.state === 'loading' || payload.state === 'mounting' || Boolean(payload.loading);
      }
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

  useEffect(() => {
    if (!rightRailOpen || !webCardVisible || !previewState?.activity_seq) return;
    void refreshBrowserActivity();
  }, [previewState?.activity_seq, refreshBrowserActivity, rightRailOpen, webCardVisible]);

  const refreshResearchJobs = useCallback(async () => {
    if (!backendReady) {
      setResearchJobs([]);
      setResearchJob(null);
      setResearchError('');
      return;
    }
    try {
      const payload = await getResearchJobs(40);
      const railJobs = payload.jobs.filter(job => !isDeepResearchJob(job));
      setResearchJobs(railJobs);
      const activeRailJob = activeResearchJobId && railJobs.some(job => job.id === activeResearchJobId) ? activeResearchJobId : '';
      const jobId = activeRailJob || railJobs[0]?.id || '';
      if (jobId) {
        const nextJob = await getResearchJob(jobId);
        setResearchJob(isDeepResearchJob(nextJob) ? null : nextJob);
      } else {
        setResearchJob(null);
      }
      setResearchError('');
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (/failed to fetch|networkerror|load failed/i.test(message)) {
        const backendAlive = await pingHealth(1200);
        setResearchError(
          backendAlive
            ? `${t('Research 接口没有响应。通常是当前窗口还连着旧后端，重启 Metis 后端/桌面端后再测。')} (${message})`
            : `${t('Metis 后端暂时没有连上，Research 面板拿不到任务列表。等后端启动完成或重启桌面端。')} (${message})`,
        );
      } else {
        setResearchError(message);
      }
    }
  }, [activeResearchJobId, backendReady, t]);

  useEffect(() => {
    if (!rightRailOpen || !researchCardVisible) return;
    void refreshResearchJobs();
    const timer = window.setInterval(() => void refreshResearchJobs(), streaming ? 2200 : 6000);
    return () => window.clearInterval(timer);
  }, [refreshResearchJobs, researchCardVisible, rightRailOpen, streaming]);

  useEffect(() => {
    if (!researchJob?.id || !researchSourceFocusId) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(researchSourceDomId(researchJob.id, researchSourceFocusId))?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [researchJob?.id, researchSourceFocusId]);

  const sendPreviewLayoutIntent = useCallback((payload: PreviewLayoutIntentPayload) => {
    const api = window.metis;
    if (api?.previewSetLayoutIntent) return api.previewSetLayoutIntent(payload);
    if (api?.previewSetBounds) return api.previewSetBounds(payload);
    return Promise.resolve({ ok: false, error: 'preview ipc unavailable' });
  }, []);

  const hidePreviewView = useCallback((reason = 'right-rail-hidden') => {
    void sendPreviewLayoutIntent({ visible: false, reason });
  }, [sendPreviewLayoutIntent]);

  const showPreviewAtHost = useCallback((node: HTMLDivElement, tabId: string, reason: string, visibleOverride?: boolean) => {
    const rect = node.getBoundingClientRect();
    const visible = visibleOverride ?? (rect.width > 4 && rect.height > 4 && !workspaceSettling);
    void sendPreviewLayoutIntent({
      bounds: {
        height: rect.height,
        width: rect.width,
        x: rect.left,
        y: rect.top,
      },
      reason,
      tabId,
      visible,
    });
  }, [sendPreviewLayoutIntent, workspaceSettling]);

  const syncPreviewBounds = useCallback(() => {
    const node = previewHostRef.current;
    const canShowPreview = canShowWebPreview && Boolean(webPreviewUrl && activeWebPreviewId);
    if (!node || !canShowPreview) {
      hidePreviewView('right-rail-sync-hidden');
      return;
    }
    showPreviewAtHost(node, activeWebPreviewId, 'right-rail-sync');
  }, [activeWebPreviewId, canShowWebPreview, hidePreviewView, showPreviewAtHost, webPreviewUrl]);

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
    if (!canShowWebPreview || !webPreviewUrl || !activeWebPreviewId) {
      hidePreviewView('right-rail-no-preview');
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
      if (!node) return;
      const state = useUiStore.getState();
      const rect = node.getBoundingClientRect();
      const visible =
        state.appMode !== 'chat' &&
        state.rightRailOpen &&
        state.workspaceCardVisibility.web &&
        state.activeWebPreviewId === tabId &&
        state.webPreviewUrl === webPreviewUrl &&
        rect.width > 4 &&
        rect.height > 4;
      showPreviewAtHost(node, tabId, 'right-rail-load-sync', visible);
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
  }, [activeWebPreviewId, canShowWebPreview, hidePreviewView, showPreviewAtHost, t, webPreviewUrl]);

  useEffect(() => {
    if (!canShowWebPreview) hidePreviewView();
  }, [canShowWebPreview, hidePreviewView]);

  useEffect(() => {
    if (showFrozenPreview) return;
    if (previewFrozenSrc && (!canShowWebPreview || !webPreviewUrl)) setPreviewFrozenSrc(null);
  }, [canShowWebPreview, previewFrozenSrc, setPreviewFrozenSrc, showFrozenPreview, webPreviewUrl]);

  useEffect(() => {
    const node = previewHostRef.current;
    if (!node || !canShowWebPreview) {
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
  }, [canShowWebPreview, hidePreviewView, schedulePreviewBoundsSync]);

  useEffect(() => {
    if (!activeWebPreviewId || !webPreviewUrl || !canShowWebPreview) {
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
  }, [activeWebPreviewId, activeWebZoom, canShowWebPreview, hidePreviewView, schedulePreviewBoundsSync, webPreviewUrl]);

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

  const scrollResearchSources = (direction: -1 | 1) => {
    researchSourceListRef.current?.scrollBy({
      top: direction * 132,
      behavior: 'smooth',
    });
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
        sessionId: activeSessionId || '',
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
      visibleCards: column.cards.filter(cardId => {
        if ((cardId === 'research' || cardId === 'session') && appMode !== 'chat') return false;
        if (appMode === 'chat' && cardId !== 'research' && cardId !== 'session') return false;
        return workspaceCardVisibility[cardId] && (cardId !== 'tool' || Boolean(toolPreview));
      }),
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
      <div className="preview-pane">
        {error && <p className="error-text">{error}</p>}
        {!backendReady && !error && <p className="empty-preview">{t('后端连接后会自动加载工作区文件。')}</p>}
        {backendReady && !file && !error && <p className="empty-preview">{t('选择文件后在这里预览。')}</p>}
        {file && <FileMeta file={file} workspacePath={activeWorkspacePath} />}
        {file?.type === 'image' && <ImagePreview file={file} />}
        {file?.type === 'pdf' && <PdfPreview file={file} />}
        {file?.type === 'office' && <OfficePreview file={file} />}
        {(file?.type === 'text' || file?.type === 'markdown') && (
          <>
            {file.truncated && <p className="rail-warning">{t('文件较大，已显示前半部分。')}</p>}
            {file.type === 'markdown' && file.content ? (
              <div className="file-content file-content-markdown markdown-body">
                <MarkdownText text={file.content} />
              </div>
            ) : (
              <CodePreview file={file} />
            )}
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
      <div className="file-tree">
        {tree.length === 0 && backendReady && !error && <p className="empty-preview">{t('当前工作区没有可预览文件。')}</p>}
        {tree.map(node => createElement(TreeNode, { key: node.path || node.name, node, onPick: setPreviewPath, activePath: previewPath || '' }))}
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
      {appMode === 'cowork' ? (
        <CoworkActivityPanel items={subagents} plan={coworkPlan} runtimeStatus={runtimeStatus} />
      ) : (
        <SubagentActivityPanel items={subagents} />
      )}
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
            {showFrozenPreview && (
              <img className="web-preview-frozen" src={previewFrozenSrc || undefined} alt="" draggable={false} />
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
    const progress = total > 0 ? Math.round((doneCount / total) * 100) : 0;
    const agentTasks = buildPlanAgentTasks(subagents, coworkPlan);
    const agentDone = agentTasks.filter(task => task.status === 'done').length;
    const agentRunning = agentTasks.filter(task => task.status === 'running').length;
    const agentFailed = agentTasks.filter(task => task.status === 'error').length;
    const agentProgress = agentTasks.length
      ? Math.round(agentTasks.reduce((sum, task) => sum + task.progress, 0) / agentTasks.length)
      : 0;
    const activeAgentTasks = agentTasks.filter(task => task.status === 'running' || task.status === 'planned');
    const finishedAgentTasks = agentTasks.filter(task => task.status === 'done' || task.status === 'error');
    const canClearAgentTasks = agentTasks.length > 0 && activeAgentTasks.length === 0;
    const renderAgentTask = (task: PlanAgentTask) => {
      const open = openPlanAgentTasks[task.id] ?? task.status === 'error';
      const elapsed = planAgentElapsed(task.startedAt, task.finishedAt);
      const detailText = task.resultText || task.prompt || task.summary;
      return (
        <article className="plan-agent-task" data-status={task.status} data-open={open} key={task.id}>
          <div className="plan-agent-task-row">
            <button className="plan-agent-task-main" type="button" onClick={() => togglePlanAgentTask(task.id)}>
              <ChevronRight className="disclosure-chevron" data-open={open} size={13} />
              <span className="plan-agent-dot" data-status={task.status} />
              <span>
                <strong>{task.title}</strong>
                <small>{task.summary || t(planAgentStatusLabel(task.status))}</small>
              </span>
              <em>{t(planAgentStatusLabel(task.status))}</em>
            </button>
            {task.status === 'running' && (
              <button
                className="plan-agent-stop"
                type="button"
                disabled={!streaming}
                title={t('停止当前运行')}
                onClick={event => {
                  event.stopPropagation();
                  stopChatRun();
                }}
              >
                <Square size={12} />
                <span>{t('停止')}</span>
              </button>
            )}
          </div>
          {open && (
            <div className="plan-agent-task-detail">
              <div className="plan-agent-progress" aria-label={`${task.title} ${task.progress}%`}>
                <span style={{ width: `${task.progress}%` }} />
              </div>
              <div className="plan-agent-meta">
                <span>{task.progress}%</span>
                {elapsed && <span>{elapsed}</span>}
              </div>
              {detailText ? <pre>{detailText}</pre> : <p>{t('等待智能体输出详情。')}</p>}
            </div>
          )}
        </article>
      );
    };
    return (
    <div className="plan-card-pane">
      {total > 0 ? (
        <div className="plan-card-todos">
          <div className="plan-card-todos-head">
            <div>
              <strong>{t('任务进度')}</strong>
              <span>{planOverviewText(total, doneCount, activeCount, issueCount + canceledCount, runtimeStatus, t)}</span>
            </div>
            <em>{doneCount}/{total} {t('完成')}</em>
          </div>
          <div className="plan-progress-track" aria-label={t('任务进度')}>
            <span style={{ width: `${progress}%` }} />
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
          <span>{t('智能体规划任务后，这里显示整体进度和每个步骤状态。')}</span>
        </div>
      )}
      <section className="plan-agent-panel" aria-label={t('智能体任务')}>
        <div className="plan-agent-head">
          <div>
            <strong>{t('智能体任务')}</strong>
            <span>
              {agentTasks.length
                ? `${agentDone}/${agentTasks.length} ${t('已完成')}${agentRunning ? ` · ${agentRunning} ${t('正在运行')}` : ''}${agentFailed ? ` · ${agentFailed} ${t('失败')}` : ''}`
                : t('等待 subrun 启动')}
            </span>
          </div>
          {canClearAgentTasks ? (
            <button
              className="plan-agent-clear"
              type="button"
              onClick={() => {
                clearSubagents();
                setOpenPlanAgentTasks({});
              }}
            >
              {t('清理')}
            </button>
          ) : (
            agentTasks.length > 0 && <em>{agentProgress}%</em>
          )}
        </div>
        {agentTasks.length > 0 ? (
          <div className="plan-agent-list">
            {activeAgentTasks.length > 0 && (
              <section className="plan-agent-section" data-layer="active" aria-label={t('进行中或待开始的智能体任务')}>
                <div className="plan-agent-section-title">
                  <strong>{t('进行中 / 待开始')}</strong>
                  <span>{activeAgentTasks.length}</span>
                </div>
                {activeAgentTasks.map(renderAgentTask)}
              </section>
            )}
            {finishedAgentTasks.length > 0 && (
              <section className="plan-agent-section" data-layer="finished" aria-label={t('已完成或失败的智能体任务')}>
                <div className="plan-agent-section-title">
                  <strong>{t('已结束')}</strong>
                  <span>{finishedAgentTasks.length}</span>
                </div>
                {finishedAgentTasks.map(renderAgentTask)}
              </section>
            )}
          </div>
        ) : (
          <div className="plan-agent-empty">
            <Network size={16} />
            <span>{t('启动 Cowork 或并行 subrun 后，这里显示每个智能体任务。')}</span>
          </div>
        )}
      </section>
    </div>
    );
  };

  const renderResearchPanel = () => {
    const job = researchJob;
    const stats = job?.stats || {};
    const sources = job?.sources || [];
    const opened = sources.filter(source => source.status === 'opened').length || stats.opened || 0;
    const failures = sources.filter(source => source.status === 'failed').length || stats.failures || 0;
    const sourceCount = stats.sources || sources.length;
    return (
      <div className="research-pane">
        {researchError && (
          <p className="rail-warning">
            <AlertTriangle size={13} />
            {researchError}
          </p>
        )}
        {!job && researchJobs.length === 0 && !researchError && (
          <div className="rail-empty-card">
            <Globe size={18} />
            <strong>{t('暂无来源')}</strong>
            <span>{t('运行 web_search、web_research 或 fetch_content 后会显示来源。')}</span>
          </div>
        )}
        {job && (
          <div className="research-sources-card">
            <div className="research-source-card-head">
              <div>
                <strong>{t('来源')}</strong>
                <span>{job.title || job.query || researchKindLabel(job.kind, t)}</span>
              </div>
              <div className="research-source-card-head-actions">
                <em data-status={job.status}>
                  {job.status === 'running' && <LoaderCircle className="spin" size={11} />}
                  {sourceCount} {t('个')}
                </em>
              </div>
            </div>
            <div className="research-source-card-meta">
              {stats.search_results ? <span>{stats.search_results} {t('搜索结果')}</span> : null}
              <span>{opened} {t('已读取')}</span>
              {failures > 0 && <span data-warn="true">{failures} {t('失败')}</span>}
            </div>
            <ResearchSourcesView
              focusSourceId={researchSourceFocusId}
              job={job}
              listRef={researchSourceListRef}
              onOpenSource={source => {
                const url = researchSourceUrl(source);
                if (url) void window.metis?.openExternal?.(url);
              }}
              t={t}
            />
            <div className="research-source-card-nav">
              <span>{sourceCount ? `${Math.min(sourceCount, sources.length || sourceCount)} / ${sourceCount}` : t('暂无来源')}</span>
              <div>
                <button type="button" title={t('上一个来源')} onClick={() => scrollResearchSources(-1)}>
                  <ChevronDown size={12} />
                </button>
                <button type="button" title={t('下一个来源')} onClick={() => scrollResearchSources(1)}>
                  <ChevronDown size={12} />
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderSessionPanel = () => (
    <SessionWorkspacePanel
      backendReady={backendReady}
      messages={chatMessages}
      pendingAttachments={pendingAttachments}
      setResearchReportView={setResearchReportView}
      t={t}
    />
  );

  const renderCardContent = (cardId: WorkspaceCardId) => {
    if (cardId === 'web') return renderWebPanel();
    if (cardId === 'terminal') return <TerminalPanel embedded onRequestClose={() => setWorkspaceCardVisible('terminal', false)} />;
    if (cardId === 'files') return renderFilesPanel();
    if (cardId === 'diff') return renderDiffPanel();
    if (cardId === 'activity') return renderActivityPanel();
    if (cardId === 'research') return renderResearchPanel();
    if (cardId === 'session') return renderSessionPanel();
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
        <span className="browser-activity-caret">
          <ChevronRight className="disclosure-chevron" data-open={open} size={13} />
        </span>
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

function ResearchProcessView({ job, t }: { job: ResearchJob; t: (text: string) => string }) {
  const phases = job.plan || [];
  const queries = job.queries || [];
  const attempts = job.attempts || [];
  return (
    <div className="research-process-view">
      <div className="research-phase-list">
        {phases.length > 0 ? phases.map((phase, index) => (
          <ResearchPhaseRow key={`${phase.id || phase.label || 'phase'}-${index}`} phase={phase} t={t} />
        )) : (
          <p className="research-muted">{t('暂无过程记录')}</p>
        )}
      </div>
      {queries.length > 0 && (
        <div className="research-query-list">
          <strong>{t('查询')}</strong>
          {queries.slice(-6).reverse().map((query, index) => (
            <code key={`${index}-${String(query.query || '')}`}>{String(query.query || '')}</code>
          ))}
        </div>
      )}
      {attempts.length > 0 && (
        <div className="research-attempt-list">
          <strong>{t('读取尝试')}</strong>
          {attempts.map((attempt, index) => (
            <span data-ok={attempt.ok !== false} key={`${index}-${String(attempt.provider || '')}`}>
              {String(attempt.provider || 'provider')} · {String(attempt.status || '')}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ResearchPhaseRow({ phase, t }: { phase: ResearchJobPhase; t: (text: string) => string }) {
  const status = String(phase.status || 'complete');
  return (
    <div className="research-phase-row" data-status={status}>
      <span>{researchPhaseIcon(status)}</span>
      <div>
        <strong>{phase.label || phase.id || t('阶段')}</strong>
        <small>
          {[phase.summary, typeof phase.count === 'number' ? `${phase.count} ${t('项')}` : '', typeof phase.failed === 'number' && phase.failed > 0 ? `${phase.failed} ${t('失败')}` : '']
            .filter(Boolean)
            .join(' · ')}
        </small>
      </div>
    </div>
  );
}

function ResearchSourcesView({
  focusSourceId,
  job,
  listRef,
  onOpenSource,
  t,
}: {
  focusSourceId: string;
  job: ResearchJob;
  listRef: { current: HTMLDivElement | null };
  onOpenSource: (source: ResearchJobSource) => void;
  t: (text: string) => string;
}) {
  const sources = job.sources || [];
  return (
    <div className="research-source-list" ref={listRef}>
      {sources.length > 0 ? sources.map((source, index) => {
        const sourceId = researchSourceId(source, index);
        const title = researchSourceTitle(source, t);
        const url = researchSourceUrl(source);
        const linkLabel = researchSourceLinkLabel(source, t);
        const statusLabel = researchSourceStatus(source, t);
        return (
          <button
            type="button"
            className="research-source-row"
            data-highlight={focusSourceId === sourceId}
            data-status={source.status || 'search_result'}
            disabled={!url}
            id={researchSourceDomId(job.id, sourceId)}
            key={`${sourceId}-${source.url || source.title || ''}`}
            onClick={() => onOpenSource(source)}
          >
            <ResearchSourceLogo source={source} />
            <span className="research-source-link" title={url || title}>{linkLabel}</span>
            {statusLabel && <em>{statusLabel}</em>}
          </button>
        );
      }) : (
        <p className="research-muted">{t('暂无来源')}</p>
      )}
    </div>
  );
}

function SessionWorkspacePanel({
  backendReady,
  messages,
  pendingAttachments,
  setResearchReportView,
  t,
}: {
  backendReady: boolean;
  messages: ChatMessage[];
  pendingAttachments: ParsedFile[];
  setResearchReportView: (jobId?: string) => void;
  t: (text: string) => string;
}) {
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const [reports, setReports] = useState<ResearchJob[]>([]);
  const [libraryItems, setLibraryItems] = useState<DocumentLibraryItem[]>([]);
  const attachments = useMemo(() => {
    const rows = new Map<string, ParsedFile>();
    for (const attachment of pendingAttachments || []) {
      if (attachment.path) rows.set(attachment.path, attachment);
    }
    for (const message of messages || []) {
      for (const attachment of message.attachments || []) {
        if (attachment.path) rows.set(attachment.path, attachment);
      }
    }
    return Array.from(rows.values()).slice(-10).reverse();
  }, [messages, pendingAttachments]);

  useEffect(() => {
    const refreshLibrary = () => setLibraryItems(listDocumentLibraryItems().slice(0, 12));
    refreshLibrary();
    window.addEventListener(DOCUMENT_LIBRARY_EVENT, refreshLibrary);
    window.addEventListener('storage', refreshLibrary);
    return () => {
      window.removeEventListener(DOCUMENT_LIBRARY_EVENT, refreshLibrary);
      window.removeEventListener('storage', refreshLibrary);
    };
  }, []);

  useEffect(() => {
    if (!backendReady) {
      setReports([]);
      return undefined;
    }
    let disposed = false;
    const refresh = async () => {
      let artifactSynced = false;
      try {
        const synced = await syncDocumentLibraryFromArtifacts({ sessionId: activeSessionId || '', includeUnscoped: true });
        artifactSynced = true;
        if (!disposed) setLibraryItems(synced.slice(0, 12));
      } catch {
        artifactSynced = false;
      }
      try {
        const payload = await getResearchJobs(24);
        if (disposed) return;
        const reportJobs = payload.jobs.filter(job => isReportDocumentJob(job)).slice(0, 10);
        if (!artifactSynced) for (const job of reportJobs) {
          upsertDocumentLibraryItem(documentItemFromResearchJob(job, t));
        }
        setReports(reportJobs);
      } catch {
        if (!disposed) setReports([]);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 6000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [activeSessionId, backendReady, t]);

  const generatedItems = useMemo(
    () => libraryItems.filter(item => item.artifactId || ['research_report', 'report', 'document', 'diff', 'file_change', 'preview_evidence', 'download', 'workspace_file'].includes(item.kind)),
    [libraryItems],
  );
  const libraryReportIds = useMemo(() => new Set(generatedItems.map(item => item.jobId).filter(Boolean)), [generatedItems]);
  const looseReports = useMemo(() => reports.filter(report => !libraryReportIds.has(report.id)), [libraryReportIds, reports]);
  const hasContent = attachments.length > 0 || reports.length > 0 || libraryItems.length > 0;
  const openGeneratedFile = (item: DocumentLibraryItem) => {
    if (item.jobId && (item.kind === 'research_report' || item.kind === 'report')) {
      setResearchReportView(item.jobId);
      return;
    }
    if (item.path) {
      void window.metis?.openPath?.(item.path);
      return;
    }
    if (item.url) {
      void window.metis?.openExternal?.(item.url);
      return;
    }
    if (item.jobId) setResearchReportView(item.jobId);
  };
  const openGeneratedReport = (report: ResearchJob) => {
    if (report.report_path) {
      void window.metis?.openPath?.(report.report_path);
      return;
    }
    setResearchReportView(report.id);
  };
  return (
    <div className="session-workspace-pane">
      <div className="session-workspace-head">
        <div>
          <strong>{t('会话文件')}</strong>
          <span>{t('生成与上传')}</span>
        </div>
        <em>{t('本次会话')}</em>
      </div>
      {!hasContent && (
        <div className="session-workspace-empty">
          <Folder size={20} />
          <strong>{t('会话文件')}</strong>
          <span>{t('上传的文件、图片和生成的文件会出现在这里。')}</span>
        </div>
      )}
      {attachments.length > 0 && (
        <section className="session-workspace-section">
          <header>{t('上传文件')}</header>
          {attachments.map(file => (
            <button type="button" key={file.path} onClick={() => file.path ? void window.metis?.openPath?.(file.path) : undefined}>
              <Folder size={13} />
              <span>
                <strong>{attachmentName(file)}</strong>
                <small>{file.path}</small>
              </span>
            </button>
          ))}
        </section>
      )}
      {generatedItems.length > 0 && (
        <section className="session-workspace-section">
          <header>{t('生成文件')}</header>
          {generatedItems.map(item => (
            <button type="button" key={item.id} onClick={() => openGeneratedFile(item)}>
              <FileText size={13} />
              <span>
                <strong>{generatedFileName(item.title || item.path || item.jobId || t('生成文件'))}</strong>
                <small>{item.path || item.subtitle || t('已生成')}</small>
              </span>
            </button>
          ))}
        </section>
      )}
      {looseReports.length > 0 && (
        <section className="session-workspace-section">
          <header>{t('生成文件')}</header>
          {looseReports.map(report => (
            <button type="button" key={report.id} onClick={() => openGeneratedReport(report)}>
              <FileText size={13} />
              <span>
                <strong>{report.report_filename || generatedFileName(report.title || report.query || t('生成文件'))}</strong>
                <small>{report.report_path || researchJobEntryMeta(report, t)}</small>
              </span>
            </button>
          ))}
        </section>
      )}
    </div>
  );
}

function isReportDocumentJob(job: ResearchJob): boolean {
  return Boolean(job.report_filename || job.report_path || String(job.report || '').trim());
}

function documentItemFromResearchJob(job: ResearchJob, t: (text: string) => string): DocumentLibraryItem {
  return {
    id: `research:${job.id}`,
    jobId: job.id,
    kind: 'research_report',
    path: job.report_path || '',
    source: 'research',
    subtitle: researchJobEntryMeta(job, t),
    title: job.title || job.query || job.report_filename || t('研究报告'),
    createdAt: Number(job.created_at || Date.now()),
    updatedAt: Number(job.updated_at || Date.now()),
  };
}

function attachmentName(file: ParsedFile): string {
  const path = String(file.path || '').replace(/\\/g, '/');
  return path.split('/').pop() || path || 'file';
}

function generatedFileName(value: string): string {
  const normalized = String(value || '').replace(/\\/g, '/').trim();
  const filename = normalized.split('/').pop() || normalized || 'generated-file.md';
  return /\.[A-Za-z0-9]{1,8}$/.test(filename) ? filename : `${filename}.md`;
}

function researchJobEntryMeta(job: ResearchJob, t: (text: string) => string): string {
  const count = job.stats?.sources || job.sources?.length || 0;
  const time = Number(job.updated_at || job.created_at || 0);
  const when = Number.isFinite(time) && time > 0 ? new Date(time).toLocaleString([], { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  return [when, count ? `${count} ${t('个来源')}` : ''].filter(Boolean).join(' · ') || t('已生成');
}

function ResearchSourceLogo({ source }: { source: ResearchJobSource }) {
  const [iconIndex, setIconIndex] = useState(0);
  const [failed, setFailed] = useState(false);
  const host = researchHost(researchSourceUrl(source)) || source.domain || '';
  const candidates = sourceFaviconCandidates(host);
  const faviconUrl = !failed && candidates.length > 0 ? candidates[Math.min(iconIndex, candidates.length - 1)] : '';
  const brand = sourceLogoBrand(host);

  return (
    <span className="research-source-logo" aria-hidden="true" style={{ '--source-logo-hue': sourceLogoHue(host) } as CSSProperties}>
      {faviconUrl && !failed ? (
        <img
          src={faviconUrl}
          alt=""
          loading="lazy"
          onError={() => {
            if (iconIndex < candidates.length - 1) setIconIndex(value => value + 1);
            else setFailed(true);
          }}
        />
      ) : (
        <span className="research-source-logo-fallback" data-brand={brand} />
      )}
    </span>
  );
}

function sourceFaviconCandidates(host: string): string[] {
  const value = String(host || '').replace(/^www\./i, '').trim();
  if (!value) return [];
  const encoded = encodeURIComponent(value);
  return [
    `https://icons.duckduckgo.com/ip3/${value}.ico`,
    `https://www.google.com/s2/favicons?domain=${encoded}&sz=64`,
    `https://${value}/favicon.ico`,
  ];
}

function sourceLogoBrand(host: string): string {
  const value = String(host || '').toLowerCase();
  if (/google|gemini|deepmind/.test(value)) return 'google';
  if (/github/.test(value)) return 'github';
  if (/youtube/.test(value)) return 'youtube';
  return 'generic';
}

function sourceLogoHue(host: string): number {
  const value = String(host || 'source');
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) hash = (hash * 31 + value.charCodeAt(index)) % 360;
  return hash;
}

function ResearchReportView({
  busy,
  copied,
  job,
  onCopy,
  onDownload,
  onJumpSource,
  t,
}: {
  busy: boolean;
  copied: boolean;
  job: ResearchJob;
  onCopy: () => void;
  onDownload: () => void;
  onJumpSource: (sourceId: string) => void;
  t: (text: string) => string;
}) {
  const report = job.report || fallbackResearchReport(job, t);
  const citations = researchCitationSources(job, report);
  return (
    <div className="research-report-view">
      <div className="research-report-actions">
        <button type="button" disabled={busy} onClick={onCopy}>
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? t('已复制') : t('复制')}
        </button>
        <button type="button" disabled={busy} onClick={onDownload}>
          <FileText size={13} />
          {t('导出')}
        </button>
      </div>
      {citations.length > 0 && (
        <div className="research-citation-strip" aria-label={t('来源引用')}>
          <span>{t('引用')}</span>
          {citations.map(row => (
            <button
              type="button"
              data-status={row.source.status || 'search_result'}
              key={`${row.id}-${row.source.url || row.source.title || ''}`}
              onClick={() => onJumpSource(row.id)}
              title={row.source.title || row.source.url || ''}
            >
              [{row.source.rank || row.index + 1}] {row.source.domain || researchHost(row.source.url || '') || row.source.title || t('来源')}
            </button>
          ))}
        </div>
      )}
      <div className="research-report-markdown markdown-body">
        <MarkdownText text={report} />
      </div>
    </div>
  );
}

function researchPhaseIcon(status: string) {
  if (status === 'running') return <LoaderCircle className="spin" size={13} />;
  if (status === 'error' || status === 'failed') return <AlertTriangle size={13} />;
  if (status === 'partial') return <Circle size={13} />;
  if (status === 'queued') return <Circle size={13} />;
  if (status === 'skipped') return <ChevronRight size={13} />;
  return <CircleCheck size={13} />;
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

function researchKindLabel(kind: string, t: (text: string) => string): string {
  if (kind === 'fetch_content') return t('读取');
  if (kind === 'search') return t('搜索');
  return t('研究');
}

function isDeepResearchJob(job: Pick<ResearchJob, 'kind'> | null | undefined): boolean {
  return String(job?.kind || '').toLowerCase() === 'deep_research';
}

function researchSourceStatus(source: ResearchJobSource, t: (text: string) => string): string {
  const status = String(source.status || '');
  if (status === 'opened') return source.evidence_status === 'partial' ? t('部分') : '';
  if (status === 'failed') return t('失败');
  return '';
}

function researchSourceTitle(source: ResearchJobSource, t: (text: string) => string): string {
  const title = String(source.title || '').trim();
  if (title && !/^\(?untitled\)?$/i.test(title) && !/r\.jina\.ai/i.test(title)) return title;
  return researchHost(researchSourceUrl(source)) || source.domain || researchSourceUrl(source) || t('来源');
}

function researchSourceUrl(source: ResearchJobSource): string {
  return unwrapReaderUrl(String(source.url || '').trim());
}

function researchSourceLinkLabel(source: ResearchJobSource, t: (text: string) => string): string {
  const url = researchSourceUrl(source);
  if (!url) return source.domain || researchSourceTitle(source, t);
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./i, '');
    const path = `${parsed.pathname || ''}${parsed.search || ''}`.replace(/\/$/, '');
    return `${host}${path || ''}` || url;
  } catch {
    return url.replace(/^https?:\/\//i, '').replace(/^www\./i, '') || researchSourceTitle(source, t);
  }
}

function unwrapReaderUrl(value: string): string {
  let current = String(value || '').trim();
  for (let index = 0; index < 4; index += 1) {
    let parsed: URL;
    try {
      parsed = new URL(current);
    } catch {
      break;
    }
    if (parsed.hostname !== 'r.jina.ai') break;
    let next = decodeURIComponent(parsed.pathname.replace(/^\/+/, ''));
    next = next.replace(/^https?:\/\/(https?:\/\/)/i, '$1');
    if (!/^https?:\/\//i.test(next)) next = next.replace(/^(https?:)\/+/i, '$1//');
    if (!/^https?:\/\//i.test(next) || next === current) break;
    current = next;
  }
  return current;
}

function researchHost(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

function researchSourceId(source: ResearchJobSource, index: number): string {
  return String(source.id || `s${index + 1}`);
}

function researchSourceDomId(jobId: string, sourceId: string): string {
  return `research-source-${safeDomFragment(jobId)}-${safeDomFragment(sourceId)}`;
}

function safeDomFragment(value: string): string {
  return String(value || '').replace(/[^A-Za-z0-9_-]+/g, '_');
}

function researchCitationSources(job: ResearchJob, report: string): Array<{ id: string; index: number; source: ResearchJobSource }> {
  const rows = (job.sources || []).map((source, index) => ({ id: researchSourceId(source, index), index, source }));
  const text = String(report || '');
  const cited = rows.filter(row => {
    const url = row.source.url || '';
    const title = row.source.title || '';
    return Boolean((url && text.includes(url)) || (title && text.includes(title)));
  });
  return (cited.length > 0 ? cited : rows.filter(row => Boolean(row.source.url))).slice(0, 12);
}

function fallbackResearchReport(job: ResearchJob, t: (text: string) => string): string {
  const lines = [`# ${job.title || job.query || t('研究报告')}`, ''];
  if (job.query) lines.push(`Query: ${job.query}`, '');
  if (job.sources?.length) {
    lines.push('## Sources', '');
    for (const source of job.sources.slice(0, 20)) {
      const label = source.title || source.domain || source.url || 'Source';
      lines.push(source.url ? `- [${label}](${source.url})` : `- ${label}`);
    }
    lines.push('');
  }
  if (job.evidence?.length) {
    lines.push('## Evidence', '');
    for (const item of job.evidence.slice(0, 8)) {
      lines.push(`### ${item.title || item.url || 'Evidence'}`);
      const text = item.text || item.snippet || '';
      if (text) lines.push('', text);
      lines.push('');
    }
  }
  return lines.join('\n').trim() || t('暂无报告内容');
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
          <ChevronRight className="disclosure-chevron" data-open={open} size={13} />
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

type CodeHighlighter = {
  codeToHtml: (code: string, options: { lang: string; theme: string }) => string;
};

let codeHighlighterPromise: Promise<CodeHighlighter> | null = null;

function getCodeHighlighter(): Promise<CodeHighlighter> {
  if (!codeHighlighterPromise) {
    codeHighlighterPromise = Promise.all([
      import('shiki/core'),
      import('shiki/engine/javascript'),
      import('@shikijs/themes/github-dark'),
      import('@shikijs/themes/github-light'),
      import('@shikijs/langs/bash'),
      import('@shikijs/langs/batch'),
      import('@shikijs/langs/c'),
      import('@shikijs/langs/cpp'),
      import('@shikijs/langs/csharp'),
      import('@shikijs/langs/css'),
      import('@shikijs/langs/csv'),
      import('@shikijs/langs/dart'),
      import('@shikijs/langs/dockerfile'),
      import('@shikijs/langs/go'),
      import('@shikijs/langs/html'),
      import('@shikijs/langs/ini'),
      import('@shikijs/langs/java'),
      import('@shikijs/langs/javascript'),
      import('@shikijs/langs/json'),
      import('@shikijs/langs/jsx'),
      import('@shikijs/langs/kotlin'),
      import('@shikijs/langs/less'),
      import('@shikijs/langs/log'),
      import('@shikijs/langs/lua'),
      import('@shikijs/langs/markdown'),
      import('@shikijs/langs/php'),
      import('@shikijs/langs/powershell'),
      import('@shikijs/langs/python'),
      import('@shikijs/langs/r'),
      import('@shikijs/langs/ruby'),
      import('@shikijs/langs/rust'),
      import('@shikijs/langs/scss'),
      import('@shikijs/langs/shellscript'),
      import('@shikijs/langs/sql'),
      import('@shikijs/langs/svelte'),
      import('@shikijs/langs/swift'),
      import('@shikijs/langs/toml'),
      import('@shikijs/langs/tsx'),
      import('@shikijs/langs/typescript'),
      import('@shikijs/langs/vue'),
      import('@shikijs/langs/xml'),
      import('@shikijs/langs/yaml'),
    ]).then(async ([core, engine, dark, light, ...langs]) => {
      const highlighter = await core.createHighlighterCore({
        themes: [dark.default, light.default],
        langs: langs.map(lang => lang.default),
        engine: engine.createJavaScriptRegexEngine(),
      });
      return highlighter as CodeHighlighter;
    });
  }
  return codeHighlighterPromise;
}

function CodePreview({ file }: { file: WorkspaceFile }) {
  const t = useT();
  const appearanceMode = useUiStore(state => state.appearanceMode);
  const content = file.content || (file.truncated ? t('文件过大，已省略内容。') : '');
  const language = useMemo(() => codePreviewLanguage(file), [file]);
  const [highlightHtml, setHighlightHtml] = useState('');
  const [highlightError, setHighlightError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setHighlightHtml('');
    setHighlightError('');
    if (!content) return () => {
      cancelled = true;
    };
    const theme = appearanceMode === 'light' ? 'github-light' : 'github-dark';
    void getCodeHighlighter()
      .then(highlighter => highlighter.codeToHtml(content, { lang: language, theme }))
      .then(html => {
        if (!cancelled) setHighlightHtml(html);
      })
      .catch(err => {
        if (!cancelled) {
          setHighlightError(err instanceof Error ? err.message : String(err));
          setHighlightHtml('');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [appearanceMode, content, language]);

  return (
    <div className="code-preview">
      <div className="code-preview-header">
        <div className="code-preview-language">
          <strong>{languageLabel(language)}</strong>
        </div>
      </div>
      {highlightError && <p className="rail-warning">{t('代码高亮失败，已显示纯文本。')}</p>}
      {highlightHtml ? (
        <div className="code-preview-html" dangerouslySetInnerHTML={{ __html: highlightHtml }} />
      ) : (
        <pre className="code-preview-plain">{content}</pre>
      )}
    </div>
  );
}

function codePreviewLanguage(file: WorkspaceFile): string {
  const ext = fileExtension(file.name || file.path);
  const raw = String(file.language || '').toLowerCase();
  const byLanguage: Record<string, string> = {
    c: 'c',
    cpp: 'cpp',
    cplusplus: 'cpp',
    csharp: 'csharp',
    css: 'css',
    dart: 'dart',
    go: 'go',
    html: 'html',
    java: 'java',
    javascript: 'javascript',
    json: 'json',
    jsx: 'jsx',
    kotlin: 'kotlin',
    less: 'less',
    log: 'log',
    lua: 'lua',
    markdown: 'markdown',
    php: 'php',
    powershell: 'powershell',
    python: 'python',
    r: 'r',
    ruby: 'ruby',
    rust: 'rust',
    scss: 'scss',
    shell: 'shellscript',
    bash: 'bash',
    sql: 'sql',
    svelte: 'svelte',
    toml: 'toml',
    tsx: 'tsx',
    typescript: 'typescript',
    vue: 'vue',
    xml: 'xml',
    yaml: 'yaml',
  };
  if (byLanguage[raw]) return byLanguage[raw];
  const byExt: Record<string, string> = {
    '.bat': 'batch',
    '.c': 'c',
    '.conf': 'ini',
    '.cpp': 'cpp',
    '.cs': 'csharp',
    '.css': 'css',
    '.csv': 'csv',
    '.dart': 'dart',
    '.dockerfile': 'dockerfile',
    '.go': 'go',
    '.h': 'c',
    '.hpp': 'cpp',
    '.htm': 'html',
    '.html': 'html',
    '.ini': 'ini',
    '.java': 'java',
    '.js': 'javascript',
    '.json': 'json',
    '.jsx': 'jsx',
    '.kt': 'kotlin',
    '.less': 'less',
    '.log': 'log',
    '.lua': 'lua',
    '.mjs': 'javascript',
    '.php': 'php',
    '.ps1': 'powershell',
    '.py': 'python',
    '.r': 'r',
    '.rb': 'ruby',
    '.rs': 'rust',
    '.scss': 'scss',
    '.sh': 'bash',
    '.sql': 'sql',
    '.svelte': 'svelte',
    '.swift': 'swift',
    '.toml': 'toml',
    '.ts': 'typescript',
    '.tsx': 'tsx',
    '.vue': 'vue',
    '.xml': 'xml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
  };
  return byExt[ext] || 'log';
}

function fileExtension(value: string): string {
  const clean = String(value || '').split(/[?#]/, 1)[0] || '';
  const dot = clean.lastIndexOf('.');
  return dot >= 0 ? clean.slice(dot).toLowerCase() : '';
}

function languageLabel(language: string): string {
  const labels: Record<string, string> = {
    bash: 'Shell',
    csharp: 'C#',
    cpp: 'C++',
    javascript: 'JavaScript',
    powershell: 'PowerShell',
    python: 'Python',
    shellscript: 'Shell',
    tsx: 'TSX',
    typescript: 'TypeScript',
  };
  return labels[language] || language.toUpperCase();
}

function FileMeta({ file, workspacePath }: { file: WorkspaceFile; workspacePath: string }) {
  const t = useT();
  const Icon = file.type === 'image' ? ImageIcon : file.type === 'binary' ? Binary : file.type === 'pdf' || file.type === 'office' ? FileText : FileCode;
  return (
    <div className="file-meta">
      <Icon size={15} />
      <div>
        <strong>{file.name}</strong>
        <span>{file.path}</span>
      </div>
      <em>{file.type}</em>
      <em>{formatBytes(file.size)}</em>
      <button type="button" className="file-open-button" title={t('用默认浏览器或系统应用打开')} onClick={() => void openWorkspaceFile(file, workspacePath)}>
        <ExternalLink size={12} />
        <span>{t('打开')}</span>
      </button>
    </div>
  );
}

async function openWorkspaceFile(file: WorkspaceFile, workspacePath: string): Promise<void> {
  if (file.previewUrl) {
    const base = await apiBase();
    const result = await window.metis?.openExternal?.(`${base}${file.previewUrl}`);
    if (result?.ok) return;
  }
  const targetPath = resolveWorkspaceFilePath(file.path, workspacePath);
  const result = await window.metis?.openPath?.(targetPath);
  if (!result?.ok && file.previewUrl) {
    const base = await apiBase();
    await window.metis?.openExternal?.(`${base}${file.previewUrl}`);
  }
}

function resolveWorkspaceFilePath(filePath: string, workspacePath: string): string {
  const value = String(filePath || '').trim();
  if (!value) return '';
  if (/^[A-Za-z]:[\\/]/.test(value) || value.startsWith('\\\\') || value.startsWith('/')) return value;
  const root = String(workspacePath || '').replace(/[\\/]+$/, '');
  if (!root) return value;
  const separator = root.includes('\\') || /^[A-Za-z]:/.test(root) ? '\\' : '/';
  return `${root}${separator}${value.replace(/^[\\/]+/, '').replace(/[\\/]/g, separator)}`;
}

function ImagePreview({ file }: { file: WorkspaceFile }) {
  const [src, setSrc] = useState('');
  useEffect(() => {
    if (!file.previewUrl) return;
    void apiBase().then(base => setSrc(`${base}${file.previewUrl}`));
  }, [file.previewUrl]);
  return src ? <img className="image-preview" src={src} alt={file.name} /> : null;
}

function PdfPreview({ file }: { file: WorkspaceFile }) {
  const appearanceMode = useUiStore(state => state.appearanceMode);
  const [src, setSrc] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    setSrc('');
    setError('');
    if (!file.previewUrl) {
      setError('暂无 PDF 预览地址');
      return () => {
        cancelled = true;
      };
    }
    void apiBase().then(async base => {
      const nextSrc = `${base}${file.previewUrl}`;
      try {
        const response = await fetch(nextSrc, { method: 'HEAD' });
        if (cancelled) return;
        const type = response.headers.get('content-type') || '';
        if (!response.ok || /json/i.test(type)) {
          const detail = await fetch(nextSrc).then(item => item.text()).catch(() => '');
          if (!cancelled) setError(previewErrorText(detail || `HTTP ${response.status}`));
          return;
        }
        setSrc(nextSrc);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [appearanceMode, file.previewUrl]);
  if (error) {
    return (
      <div className="rail-empty-card pdf-preview-error">
        <FileText size={18} />
        <strong>PDF 预览暂不可用</strong>
        <span>{error}</span>
      </div>
    );
  }
  return src ? <iframe key={`${file.previewUrl}:${appearanceMode}`} className="pdf-preview-frame" src={src} title={file.name} /> : null;
}

function previewErrorText(value: string): string {
  try {
    const parsed = JSON.parse(value);
    return String(parsed.error || parsed.detail || value);
  } catch {
    return value.trim().slice(0, 160) || '预览服务暂未返回 PDF 内容';
  }
}

function OfficePreview({ file }: { file: WorkspaceFile }) {
  const t = useT();
  const content = file.content || '';
  return (
    <div className="office-preview">
      {file.previewNote && <p className="rail-warning">{t(file.previewNote)}</p>}
      {file.truncated && <p className="rail-warning">{t('文件较大，已显示前半部分。')}</p>}
      {file.previewUrl ? (
        <PdfPreview file={file} />
      ) : content ? (
        <pre className="file-content office-preview-content">{content}</pre>
      ) : (
        <div className="rail-empty-card">
          <FileText size={18} />
          <strong>{t('无法生成内置预览')}</strong>
          <span>{t('可以用系统默认应用打开，或安装/配置 LibreOffice 启用 PDF 预览。')}</span>
        </div>
      )}
    </div>
  );
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
