/**
 * MetisThread — 主对话线程组件。
 *
 * 只负责滚动容器、消息窗口虚拟化和顶层布局。
 * 消息渲染、工具卡片、文件变更等拆分到同目录下的子模块。
 */
import {
  AssistantRuntimeProvider,
  ThreadPrimitive,
  useAuiState,
} from '@assistant-ui/react';
import { ChevronDown, Code2, Handshake, MessageCircleMore } from 'lucide-react';
import { createElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ComponentProps, RefObject } from 'react';
import { themes } from '../../lib/themes';
import type { AppMode, RuntimeStatus } from '../../lib/types';
import { useMetisRuntime } from '../../runtime/metisRuntime';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { Composer } from './Composer';
import { AssistantMessage, SystemMessage, UserMessage } from './MessageBubble';
import { AwaySummaryNotice, ContextOrganizingNotice, LearningNotice, RunRecoveryNotice, RuntimeStatusBar, TodoNotice } from './NoticeCards';
import { SubagentGroup } from './SubagentGroup';

const coworkBackdropUrl = new URL('../../assets/cowork-dotwave-b.html', import.meta.url).href;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const INITIAL_MESSAGE_WINDOW = 80;
const MESSAGE_WINDOW_STEP = 40;
const TOP_LOAD_THRESHOLD_PX = 48;
const BOTTOM_STICKY_THRESHOLD_PX = 72;
const POST_RUN_BOTTOM_LOCK_MS = 800;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type ThreadMessageComponents = ComponentProps<typeof ThreadPrimitive.MessageByIndex>['components'];

interface MessageMeta {
  id: string;
  index: number;
  role: string;
}

// ---------------------------------------------------------------------------
// MetisThread — 主入口
// ---------------------------------------------------------------------------

export function MetisThread() {
  const runtime = useMetisRuntime();
  const appMode = useUiStore(state => state.appMode);
  const memoryNotice = useChatStore(state => state.memoryNotice);
  const clearMemoryNotice = useChatStore(state => state.clearMemoryNotice);
  const todoNotice = useChatStore(state => state.todoNotice);
  const clearTodoNotice = useChatStore(state => state.clearTodoNotice);
  const awaySummary = useChatStore(state => state.awaySummary);
  const dismissAwaySummary = useChatStore(state => state.dismissAwaySummary);
  const recoveryNotice = useChatStore(state => state.recoveryNotice);
  const dismissRecoveryNotice = useChatStore(state => state.dismissRecoveryNotice);
  const markRecoveryFailed = useChatStore(state => state.markRecoveryFailed);
  const resumeInterruptedRun = useChatStore(state => state.resumeInterruptedRun);
  const clearRecoverySnapshot = useChatStore(state => state.clearRecoverySnapshot);
  const compacting = useChatStore(state => state.compacting);
  const subagents = useChatStore(state => state.subagents);
  const runtimeStatus = useChatStore(state => state.runtimeStatus);
  const activeSessionId = useSessionStore(state => state.activeSessionId);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const dockRef = useRef<HTMLDivElement | null>(null);
  // FABLEADV-33: 底部 dock(状态条 + composer)悬浮叠在消息流上方做磨砂玻璃；
  // 量出 dock 高度写成 CSS 变量，给消息区留底部内边距，保证最后一条能滚到 dock 上方不被遮。
  useEffect(() => {
    const dock = dockRef.current;
    const shell = shellRef.current;
    if (!dock || !shell) return;
    const update = () =>
      shell.style.setProperty('--thread-dock-height', `${Math.round(dock.getBoundingClientRect().height)}px`);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(dock);
    return () => observer.disconnect();
  }, []);
  const subagentSignature = useMemo(
    () => (subagents.length ? `${activeSessionId || 'local'}:${subagents.map(item => item.taskId).join('|')}` : ''),
    [activeSessionId, subagents],
  );
  const [hiddenSubagentSignature, setHiddenSubagentSignature] = useState('');
  const [coworkBackdropMounted, setCoworkBackdropMounted] = useState(appMode === 'cowork');
  const showSubagentStrip = Boolean(subagentSignature && hiddenSubagentSignature !== subagentSignature);
  const messageComponents = useMemo<ThreadMessageComponents>(
    () => ({
      AssistantMessage,
      SystemMessage,
      UserMessage,
    }),
    [],
  );
  const dockRuntimeStatus = useMemo(
    () => runtimeStatusForDock(runtimeStatus, appMode),
    [appMode, runtimeStatus],
  );

  useEffect(() => {
    if (appMode === 'cowork') {
      setCoworkBackdropMounted(true);
      return undefined;
    }
    const timer = window.setTimeout(() => setCoworkBackdropMounted(true), 900);
    return () => window.clearTimeout(timer);
  }, [appMode]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="thread-shell" data-compacting={compacting} data-mode={appMode} ref={shellRef}>
        {(appMode === 'cowork' || coworkBackdropMounted) && <CoworkBackdrop visible={appMode === 'cowork'} />}
        {memoryNotice && (
          <LearningNotice notice={memoryNotice} onDismiss={clearMemoryNotice} />
        )}
        {todoNotice && (
          <TodoNotice notice={todoNotice} onDismiss={clearTodoNotice} />
        )}
        {awaySummary && (
          <AwaySummaryNotice summary={awaySummary} onDismiss={dismissAwaySummary} />
        )}
        {recoveryNotice && (
          <RunRecoveryNotice
            notice={recoveryNotice}
            onContinue={dismissRecoveryNotice}
            onResume={resumeInterruptedRun}
            onMarkFailed={markRecoveryFailed}
            onClear={clearRecoverySnapshot}
          />
        )}
        <ThreadPrimitive.Root className="thread-root">
          <ThreadPrimitive.Viewport className="thread-viewport" ref={viewportRef}>
            <ThreadPrimitive.Empty>
              <WelcomeHome />
            </ThreadPrimitive.Empty>
            <WindowedThreadMessages
              compacting={compacting}
              components={messageComponents}
              sessionKey={activeSessionId}
              viewportRef={viewportRef}
            />
            <ThreadPrimitive.ViewportFooter>
              <div className="thread-bottom-space" />
            </ThreadPrimitive.ViewportFooter>
          </ThreadPrimitive.Viewport>
        </ThreadPrimitive.Root>
        <div className="thread-dock" ref={dockRef}>
          {showSubagentStrip && <SubagentGroup items={subagents} onDismiss={() => setHiddenSubagentSignature(subagentSignature)} />}
          {dockRuntimeStatus && <RuntimeStatusBar status={dockRuntimeStatus} />}
          <Composer />
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}

function runtimeStatusForDock(status: RuntimeStatus | null, appMode: AppMode): RuntimeStatus | null {
  if (!status) return null;
  if (appMode !== 'chat') return status;
  const phase = String(status.phase || '').toLowerCase();
  if (phase === 'llm_request' || phase === 'streaming') {
    return {
      ...status,
      callId: '',
      display: '正在整理答案',
      hint: status.message && status.message !== status.display ? status.message : '来源已返回，正在综合结论',
      toolName: '',
    };
  }
  if (['failed', 'timeout', 'timed_out', 'tool_timeout', 'retrying', 'sse_reconnecting', 'cancel_requested'].includes(phase)) {
    return status;
  }
  return null;
}

function CoworkBackdrop({ visible }: { visible: boolean }) {
  const appearanceMode = useUiStore(state => state.appearanceMode);
  const theme = useUiStore(state => state.theme);
  const src = useMemo(() => {
    const palette = themes[theme] || themes['cathedral-obsidian'];
    const params = new URLSearchParams({
      mode: appearanceMode,
      bg: palette['--bg'] || '',
      text: palette['--text'] || '',
      accent: palette['--accent-ink'] || palette['--accent'] || '',
      border: palette['--border'] || '',
    });
    return `${coworkBackdropUrl}?${params.toString()}`;
  }, [appearanceMode, theme]);

  return (
    <div className="cowork-backdrop" aria-hidden="true" data-visible={visible}>
      <iframe src={src} tabIndex={-1} title="" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// WelcomeHome — 模式感知的空状态首页（对齐 Metis Chat/Cowork/Code）
// ---------------------------------------------------------------------------

const WELCOME_COPY: Record<AppMode, { zh: [string, string]; en: [string, string] }> = {
  chat: {
    zh: ['有什么可以帮你？', '随时提问，高效解答'],
    en: ['How can I help?', 'Ask anytime, get efficient answers'],
  },
  cowork: {
    zh: ['一起完成目标', '告诉我们的需求，我来推进执行'],
    en: ["Let's complete the goal", "Tell me what you need; I'll drive execution"],
  },
  code: {
    zh: ['开始编码会话', '描述需求，我来生成与优化代码'],
    en: ['Start a coding session', "Describe what you need; I'll generate and refine code"],
  },
};

const WELCOME_ICONS = {
  chat: MessageCircleMore,
  cowork: Handshake,
  code: Code2,
} satisfies Record<AppMode, typeof MessageCircleMore>;

function WelcomeHome() {
  const appMode = useUiStore(state => state.appMode);
  const language = useUiStore(state => state.language);
  const [heading, subtitle] = WELCOME_COPY[appMode][language === 'zh' ? 'zh' : 'en'];
  const Icon = WELCOME_ICONS[appMode];
  return (
    <div className="welcome-panel" data-mode={appMode}>
      <Icon className="welcome-spark welcome-mode-icon" size={30} strokeWidth={1.65} aria-hidden />
      <h1 className="welcome-heading">{heading}</h1>
      <p className="welcome-subtitle">{subtitle}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WindowedThreadMessages — 消息窗口虚拟化
// ---------------------------------------------------------------------------

function WindowedThreadMessages({
  compacting,
  components,
  sessionKey,
  viewportRef,
}: {
  compacting: boolean;
  components: ThreadMessageComponents;
  sessionKey: string | null;
  viewportRef: RefObject<HTMLDivElement | null>;
}) {
  const messageSignature = useAuiState(state =>
    state.thread.messages.map((message, index) => `${index}\t${message.id}\t${message.role}`).join('\n'),
  );
  const isRunning = useAuiState(state => state.thread.isRunning);
  const messages = useMemo(() => parseMessageSignature(messageSignature), [messageSignature]);
  const messageCount = messages.length;
  const [windowSize, setWindowSize] = useState(INITIAL_MESSAGE_WINDOW);
  const stickyBottomRef = useRef(true);
  const restoreScrollRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null);
  const topLoadLockedRef = useRef(false);
  const previousCountRef = useRef(messageCount);
  const previousSessionRef = useRef<string | null>(sessionKey);
  const previousRunningRef = useRef(isRunning);

  const rawStartIndex = Math.max(0, messageCount - windowSize);
  const startIndex = alignStartToTurn(messages, rawStartIndex);
  const visibleMessages = messages.slice(startIndex);
  const hiddenCount = startIndex;

  const pinToBottom = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, [viewportRef]);

  const loadOlder = useCallback(() => {
    if (hiddenCount <= 0 || topLoadLockedRef.current) return;
    const viewport = viewportRef.current;
    if (viewport) {
      restoreScrollRef.current = {
        scrollHeight: viewport.scrollHeight,
        scrollTop: viewport.scrollTop,
      };
    }
    topLoadLockedRef.current = true;
    setWindowSize(size => Math.min(messageCount, size + MESSAGE_WINDOW_STEP));
  }, [hiddenCount, messageCount, viewportRef]);

  useEffect(() => {
    if (previousSessionRef.current === sessionKey) return;
    previousSessionRef.current = sessionKey;
    previousCountRef.current = messageCount;
    stickyBottomRef.current = true;
    restoreScrollRef.current = null;
    topLoadLockedRef.current = false;
    setWindowSize(INITIAL_MESSAGE_WINDOW);
    requestAnimationFrame(pinToBottom);
  }, [messageCount, pinToBottom, sessionKey]);

  useEffect(() => {
    const previousCount = previousCountRef.current;
    previousCountRef.current = messageCount;

    if (messageCount < previousCount) {
      stickyBottomRef.current = true;
      setWindowSize(INITIAL_MESSAGE_WINDOW);
      requestAnimationFrame(pinToBottom);
      return;
    }

    if (messageCount > previousCount && !stickyBottomRef.current) {
      const delta = messageCount - previousCount;
      setWindowSize(size => Math.min(messageCount, size + delta));
    }
  }, [messageCount, pinToBottom]);

  useLayoutEffect(() => {
    const snapshot = restoreScrollRef.current;
    if (!snapshot) return;
    restoreScrollRef.current = null;
    const viewport = viewportRef.current;
    if (!viewport) return;
    const heightDelta = viewport.scrollHeight - snapshot.scrollHeight;
    viewport.scrollTop = snapshot.scrollTop + heightDelta;
    requestAnimationFrame(() => {
      topLoadLockedRef.current = false;
    });
  }, [startIndex, viewportRef, windowSize]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;

    const updateStickyState = (allowTopLoad = true) => {
      const distanceFromBottom = viewport.scrollHeight - (viewport.scrollTop + viewport.clientHeight);
      stickyBottomRef.current = distanceFromBottom <= BOTTOM_STICKY_THRESHOLD_PX;

      if (allowTopLoad && viewport.scrollTop <= TOP_LOAD_THRESHOLD_PX && hiddenCount > 0) {
        loadOlder();
      }
    };

    const disarmOnWheelUp = (event: WheelEvent) => {
      if (event.deltaY < 0) stickyBottomRef.current = false;
    };
    const disarmOnTouch = () => {
      stickyBottomRef.current = false;
    };
    const handleScroll = () => {
      updateStickyState(true);
    };

    viewport.addEventListener('scroll', handleScroll, { passive: true });
    viewport.addEventListener('wheel', disarmOnWheelUp, { passive: true });
    viewport.addEventListener('touchmove', disarmOnTouch, { passive: true });

    updateStickyState(false);

    return () => {
      viewport.removeEventListener('scroll', handleScroll);
      viewport.removeEventListener('wheel', disarmOnWheelUp);
      viewport.removeEventListener('touchmove', disarmOnTouch);
    };
  }, [hiddenCount, loadOlder, viewportRef]);

  useLayoutEffect(() => {
    if (!stickyBottomRef.current) return;
    requestAnimationFrame(pinToBottom);
  }, [compacting, messageCount, pinToBottom]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || messageCount === 0) return undefined;
    const content = viewport.querySelector<HTMLElement>('.thread-window');
    if (!content) return undefined;

    let frame: number | null = null;
    const schedulePin = () => {
      if (frame !== null || !stickyBottomRef.current) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        if (stickyBottomRef.current) pinToBottom();
      });
    };

    const observer = new ResizeObserver(schedulePin);
    observer.observe(content);

    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [messageCount, pinToBottom, startIndex, viewportRef]);

  useLayoutEffect(() => {
    const finishedRun = previousRunningRef.current && !isRunning;
    previousRunningRef.current = isRunning;

    if (!finishedRun || !stickyBottomRef.current) return undefined;

    const lockUntil = performance.now() + POST_RUN_BOTTOM_LOCK_MS;
    let frame: number | null = null;
    const lockFrame = () => {
      frame = null;
      if (!stickyBottomRef.current) return;
      pinToBottom();
      if (performance.now() < lockUntil) frame = requestAnimationFrame(lockFrame);
    };

    pinToBottom();
    frame = requestAnimationFrame(lockFrame);

    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [isRunning, pinToBottom]);

  if (messageCount === 0 && !compacting) return null;

  return (
    <div
      className="thread-window"
      data-message-window={`${visibleMessages.length}/${messageCount}`}
      data-windowed={messageCount > INITIAL_MESSAGE_WINDOW}
      data-inline-compacting={compacting}
    >
      {hiddenCount > 0 && (
        <button className="thread-history-loader" type="button" onClick={loadOlder}>
          <ChevronDown size={14} />
          <span>显示更早 {hiddenCount} 条</span>
        </button>
      )}
      {visibleMessages.map(row =>
        createElement(ThreadPrimitive.MessageByIndex, {
          components,
          index: row.index,
          key: row.index,
        }),
      )}
      {compacting && <ContextOrganizingNotice />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseMessageSignature(signature: string): MessageMeta[] {
  if (!signature) return [];
  return signature.split('\n').map(row => {
    const [indexText, id, role] = row.split('\t');
    return {
      id: id || indexText,
      index: Number(indexText),
      role: role || '',
    };
  });
}

function alignStartToTurn(messages: MessageMeta[], rawStartIndex: number): number {
  let startIndex = rawStartIndex;
  while (startIndex > 0 && messages[startIndex]?.role !== 'user') {
    startIndex -= 1;
  }
  return startIndex;
}
