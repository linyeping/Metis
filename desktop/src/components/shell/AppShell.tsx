import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react';
import { useUiStore } from '../../store/uiStore';
import { ChatSkeleton, SidebarSkeleton } from './Skeleton';
import { Titlebar } from './Titlebar';

interface AppShellProps {
  backendReady: boolean;
  reconnect?: { attempt: number; limit: number } | null;
  pythonPath?: string;
  sidebar: ReactNode;
  main: ReactNode;
  sideChat: ReactNode;
  rightRail: ReactNode;
  overlays?: ReactNode;
}

export function AppShell({ backendReady, reconnect, pythonPath, sidebar, main, sideChat, rightRail, overlays }: AppShellProps) {
  const appMode = useUiStore(state => state.appMode);
  const rightRailOpen = useUiStore(state => state.rightRailOpen);
  const rightRailWidth = useUiStore(state => state.rightRailWidth);
  const workspaceCardVisibility = useUiStore(state => state.workspaceCardVisibility);
  const toolPreview = useUiStore(state => state.toolPreview);
  const setWorkspaceCardVisible = useUiStore(state => state.setWorkspaceCardVisible);
  const sidebarOpen = useUiStore(state => state.sidebarOpen);
  const sidebarWidth = useUiStore(state => state.sidebarWidth);
  const setSidebarWidth = useUiStore(state => state.setSidebarWidth);
  const sideChatOpen = useUiStore(state => state.sideChatOpen);
  const sideChatWidth = useUiStore(state => state.sideChatWidth);
  const setSideChatWidth = useUiStore(state => state.setSideChatWidth);
  const sidebarResizeStart = useRef<{ x: number; width: number } | null>(null);
  const sideChatResizeStart = useRef<{ x: number; width: number } | null>(null);
  const [sidebarLayoutHold, setSidebarLayoutHold] = useState(sidebarOpen);
  const sidebarLayoutOpen = sidebarOpen || sidebarLayoutHold;
  const sideChatLayoutOpen = sideChatOpen;
  const visibleWorkspaceCards = Object.entries(workspaceCardVisibility)
    .filter(([cardId, visible]) => visible && (cardId !== 'tool' || Boolean(toolPreview)))
    .map(([cardId]) => cardId);
  const effectiveVisibleWorkspaceCards = appMode === 'chat'
    ? visibleWorkspaceCards.filter(cardId => cardId === 'research')
    : visibleWorkspaceCards.filter(cardId => cardId !== 'research');
  const rightRailEffectiveOpen = rightRailOpen && effectiveVisibleWorkspaceCards.length > 0;
  const researchPopoverOpen = rightRailEffectiveOpen && effectiveVisibleWorkspaceCards.length === 1 && effectiveVisibleWorkspaceCards[0] === 'research';
  const rightRailLayoutOpen = rightRailEffectiveOpen && !researchPopoverOpen;
  const panelSpring = { type: 'spring' as const, stiffness: 320, damping: 28 };
  const panelExit = { duration: 0.18, ease: [0.16, 1, 0.3, 1] as const };

  useEffect(() => {
    if (sidebarOpen) setSidebarLayoutHold(true);
  }, [sidebarOpen]);

  useEffect(() => {
    if (appMode === 'chat' || !workspaceCardVisibility.research) return;
    setWorkspaceCardVisible('research', false);
  }, [appMode, setWorkspaceCardVisible, workspaceCardVisibility.research]);

  useEffect(() => {
    if (appMode !== 'chat') return;
    void window.metis?.previewSetBounds?.({ visible: false });
  }, [appMode]);

  const startSidebarResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!sidebarOpen) return;
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-sidebar');
    sidebarResizeStart.current = { x: event.clientX, width: sidebarWidth };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      if (!sidebarResizeStart.current) return;
      setSidebarWidth(sidebarResizeStart.current.width + (moveEvent.clientX - sidebarResizeStart.current.x));
    };
    const handleUp = () => {
      sidebarResizeStart.current = null;
      document.body.classList.remove('resizing-sidebar');
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

  const startSideChatResize = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!sideChatOpen) return;
    const resizeTarget = event.currentTarget;
    try {
      resizeTarget.setPointerCapture(event.pointerId);
    } catch {}
    document.body.classList.add('resizing-side-chat-width');
    sideChatResizeStart.current = { x: event.clientX, width: sideChatWidth };
    const preventSelection = (selectEvent: Event) => {
      selectEvent.preventDefault();
    };
    const handleMove = (moveEvent: PointerEvent) => {
      moveEvent.preventDefault();
      if (!sideChatResizeStart.current) return;
      setSideChatWidth(sideChatResizeStart.current.width - (moveEvent.clientX - sideChatResizeStart.current.x));
    };
    const handleUp = () => {
      sideChatResizeStart.current = null;
      document.body.classList.remove('resizing-side-chat-width');
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

  return (
    <div
      className="app-shell"
      data-app-mode={appMode}
      data-right-rail={rightRailEffectiveOpen}
      data-right-rail-layout={rightRailLayoutOpen}
      data-right-rail-popover={researchPopoverOpen}
      data-sidebar={sidebarOpen}
      data-sidebar-layout={sidebarLayoutOpen}
      data-side-chat={sideChatOpen}
      data-side-chat-layout={sideChatLayoutOpen}
      style={
        {
          '--right-rail-width': `${rightRailWidth}px`,
          '--sidebar-width': `${sidebarWidth}px`,
          '--side-chat-width': `${sideChatWidth}px`,
        } as CSSProperties
      }
    >
      <Titlebar />
      <div className="shell-body">
        <AnimatePresence initial={false}>
          <motion.aside
            className="secondary-panel"
            data-open={sidebarOpen}
            aria-hidden={!sidebarOpen}
            initial={false}
            animate={
              sidebarOpen
                ? { x: 0, opacity: 1, visibility: 'visible' }
                : { x: -16, opacity: 0, transition: panelExit, transitionEnd: { visibility: 'hidden' } }
            }
            transition={sidebarOpen ? panelSpring : panelExit}
            onAnimationStart={() => {
              if (!sidebarOpen) setSidebarLayoutHold(true);
            }}
            onAnimationComplete={() => {
              setSidebarLayoutHold(sidebarOpen);
            }}
          >
            {backendReady ? sidebar : <SidebarSkeleton />}
          </motion.aside>
        </AnimatePresence>
        <div
          className="sidebar-resizer"
          role="separator"
          aria-hidden={!sidebarOpen}
          aria-label="Resize sidebar"
          aria-orientation="vertical"
          data-open={sidebarOpen}
          onPointerDown={startSidebarResize}
        />
        <div className="main-workspace-column">
          <main className="main-panel">{backendReady ? main : <ChatSkeleton />}</main>
        </div>
        <AnimatePresence initial={false}>
          <motion.aside
            className="side-chat-rail"
            data-open={sideChatOpen}
            aria-hidden={!sideChatOpen}
            initial={false}
            animate={
              sideChatOpen
                ? { x: 0, opacity: 1, visibility: 'visible' }
                : { x: 12, opacity: 0, transition: panelExit, transitionEnd: { visibility: 'hidden' } }
            }
            transition={sideChatOpen ? panelSpring : panelExit}
          >
            <div
              className="side-chat-width-resizer"
              role="separator"
              aria-hidden={!sideChatOpen}
              aria-label="Resize independent chat"
              aria-orientation="vertical"
              data-open={sideChatOpen}
              onPointerDown={startSideChatResize}
            />
            {sideChat}
          </motion.aside>
        </AnimatePresence>
        <AnimatePresence initial={false}>
          <motion.aside
            className="right-rail"
            data-open={rightRailEffectiveOpen}
            aria-hidden={!rightRailEffectiveOpen}
            initial={false}
            animate={
              rightRailEffectiveOpen
                ? { x: 0, opacity: 1, visibility: 'visible' }
                : { x: 16, opacity: 0, transition: panelExit, transitionEnd: { visibility: 'hidden' } }
            }
            transition={rightRailEffectiveOpen ? panelSpring : panelExit}
          >
            {rightRail}
          </motion.aside>
        </AnimatePresence>
      </div>
      {overlays}
    </div>
  );
}
