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
import { ChevronDown } from 'lucide-react';
import { createElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ComponentProps, RefObject } from 'react';
import wordmark from '../../assets/metis-wordmark.png';
import { useMetisRuntime } from '../../runtime/metisRuntime';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { Composer } from './Composer';
import { AssistantMessage, SystemMessage, UserMessage } from './MessageBubble';
import { ContextOrganizingNotice, LearningNotice, RunRecoveryNotice, RuntimeStatusBar, TodoNotice } from './NoticeCards';
import { SubagentGroup } from './SubagentGroup';

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
  const memoryNotice = useChatStore(state => state.memoryNotice);
  const clearMemoryNotice = useChatStore(state => state.clearMemoryNotice);
  const todoNotice = useChatStore(state => state.todoNotice);
  const clearTodoNotice = useChatStore(state => state.clearTodoNotice);
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
  const showSubagentStrip = Boolean(subagentSignature && hiddenSubagentSignature !== subagentSignature);
  const messageComponents = useMemo<ThreadMessageComponents>(
    () => ({
      AssistantMessage,
      SystemMessage,
      UserMessage,
    }),
    [],
  );

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="thread-shell" data-compacting={compacting} ref={shellRef}>
        {memoryNotice && (
          <LearningNotice notice={memoryNotice} onDismiss={clearMemoryNotice} />
        )}
        {todoNotice && (
          <TodoNotice notice={todoNotice} onDismiss={clearTodoNotice} />
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
              <div className="welcome-panel">
                <img className="welcome-wordmark" src={wordmark} alt="Metis" />
                <p>今天我们来创造点什么？</p>
              </div>
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
          <RuntimeStatusBar status={runtimeStatus} />
          <Composer />
        </div>
      </div>
    </AssistantRuntimeProvider>
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
