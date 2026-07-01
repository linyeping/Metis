import {
  Check,
  ChevronDown,
  Columns3,
  FileCode,
  Folder,
  Globe,
  List,
  Maximize2,
  Minus,
  Network,
  PanelLeft,
  Square,
  SquareTerminal,
  StickyNote,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import {
  SHORTCUTTABLE_CARDS,
  useUiStore,
  type WorkspaceCardId,
  type WorkspaceCardShortcut,
} from '../../store/uiStore';
import { useT } from '../../hooks/useT';

const titlebarWorkspaceCardOptions: Array<{ id: WorkspaceCardId; label: string; icon: typeof Globe }> = [
  { id: 'web', label: 'Preview', icon: Globe },
  { id: 'diff', label: 'Diff', icon: FileCode },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal },
  { id: 'files', label: 'Files', icon: Folder },
  { id: 'activity', label: 'Background tasks', icon: Network },
  { id: 'plan', label: 'Plan', icon: StickyNote },
];

export const isMacPlatform =
  typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent || '');

// Render the platform-correct hint: ⇧⌘P on macOS, Ctrl+Shift+P on Windows/Linux.
export function shortcutLabel(keys: WorkspaceCardShortcut): string {
  const k = keys.key === '`' ? '`' : keys.key.toUpperCase();
  if (isMacPlatform) return `${keys.shift ? '⇧' : ''}⌘${k}`;
  return `Ctrl+${keys.shift ? 'Shift+' : ''}${k}`;
}

export function Titlebar() {
  const appMode = useUiStore(state => state.appMode);
  const sidebarOpen = useUiStore(state => state.sidebarOpen);
  const setSidebarOpen = useUiStore(state => state.setSidebarOpen);
  const rightRailOpen = useUiStore(state => state.rightRailOpen);
  const setRightRailOpen = useUiStore(state => state.setRightRailOpen);
  const workspaceCardVisibility = useUiStore(state => state.workspaceCardVisibility);
  const workspaceCardShortcuts = useUiStore(state => state.workspaceCardShortcuts);
  const setWorkspaceCardVisible = useUiStore(state => state.setWorkspaceCardVisible);
  const toggleWorkspaceCard = useUiStore(state => state.toggleWorkspaceCard);
  const setWorkspaceMenuOpen = useUiStore(state => state.setWorkspaceMenuOpen);
  const t = useT();
  const [cardMenuOpen, setCardMenuOpen] = useState(false);
  const cardMenuRef = useRef<HTMLDivElement | null>(null);
  const showWorkspaceCards = appMode !== 'chat';
  const researchSourceOpen = appMode === 'chat' && rightRailOpen && workspaceCardVisibility.research;

  // 原生 preview 视图没有 z-index，永远盖在 DOM 之上。此下拉菜单浮在 preview 区域上方，
  // 打开时必须通知主进程藏掉 preview，否则 webview 会盖住菜单下半部分（尤其末尾两项）。
  useEffect(() => {
    if (!showWorkspaceCards) {
      setWorkspaceMenuOpen(false);
      return undefined;
    }
    setWorkspaceMenuOpen(cardMenuOpen);
    return () => setWorkspaceMenuOpen(false);
  }, [cardMenuOpen, setWorkspaceMenuOpen, showWorkspaceCards]);
  const visibleCardCount = titlebarWorkspaceCardOptions.filter(option => workspaceCardVisibility[option.id]).length;

  useEffect(() => {
    if (!showWorkspaceCards && cardMenuOpen) setCardMenuOpen(false);
  }, [cardMenuOpen, showWorkspaceCards]);

  useEffect(() => {
    if (!cardMenuOpen) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (cardMenuRef.current?.contains(event.target as Node)) return;
      setCardMenuOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCardMenuOpen(false);
    };
    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [cardMenuOpen]);

  const toggleCardFromMenu = (cardId: WorkspaceCardId) => {
    if (!rightRailOpen && workspaceCardVisibility[cardId]) {
      setRightRailOpen(true);
      return;
    }
    toggleWorkspaceCard(cardId);
  };

  const toggleResearchSources = () => {
    if (researchSourceOpen) {
      setWorkspaceCardVisible('research', false);
      setRightRailOpen(false);
      return;
    }
    setWorkspaceCardVisible('research', true);
  };

  // Wire the (user-customizable) card shortcuts: Ctrl/⌘ [+Shift] + key. Read
  // state via getState to avoid stale closures.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = isMacPlatform ? event.metaKey : event.ctrlKey;
      if (!mod || event.altKey) return;
      const key = event.key.toLowerCase();
      const ui = useUiStore.getState();
      if (ui.appMode === 'chat') return;
      const matchId = SHORTCUTTABLE_CARDS.find(id => {
        const sc = ui.workspaceCardShortcuts[id];
        return sc && key === sc.key.toLowerCase() && Boolean(sc.shift) === event.shiftKey;
      });
      if (!matchId) return;
      event.preventDefault();
      if (!ui.rightRailOpen && ui.workspaceCardVisibility[matchId]) {
        ui.setRightRailOpen(true);
        return;
      }
      ui.toggleWorkspaceCard(matchId);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <header className="titlebar">
      <div className="titlebar-brand" aria-hidden="true" />
      <div className="titlebar-center" aria-hidden="true" />
      <div className="titlebar-actions">
        <button type="button" title={t('左栏')} data-active={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}>
          <PanelLeft size={15} />
        </button>
        {showWorkspaceCards && <div className="titlebar-cards-menu-wrap" ref={cardMenuRef}>
          <button
            type="button"
            className="titlebar-cards-menu-button"
            title="Cards"
            aria-haspopup="menu"
            aria-expanded={cardMenuOpen}
            data-active={rightRailOpen || visibleCardCount > 0}
            data-open={cardMenuOpen}
            onClick={() => setCardMenuOpen(value => !value)}
          >
            <Columns3 size={15} />
            <ChevronDown size={12} />
          </button>
          <div className="titlebar-cards-menu workspace-card-menu" data-open={cardMenuOpen} role="menu" aria-label="Workspace cards">
            {titlebarWorkspaceCardOptions.map(option => {
              const Icon = option.icon;
              const visible = workspaceCardVisibility[option.id];
              return (
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={visible}
                  data-active={visible}
                  key={option.id}
                  onClick={() => toggleCardFromMenu(option.id)}
                >
                  <Icon size={13} />
                  <span>{option.label}</span>
                  {workspaceCardShortcuts[option.id] && <em>{shortcutLabel(workspaceCardShortcuts[option.id]!)}</em>}
                  <Check size={12} />
                </button>
              );
            })}
          </div>
        </div>}
        {appMode === 'chat' && (
          <button
            type="button"
            className="titlebar-source-button"
            title={t('来源')}
            data-active={researchSourceOpen}
            onClick={toggleResearchSources}
          >
            <List size={15} />
          </button>
        )}
        <button type="button" title={t('最小化')} onClick={() => void window.metis.window('minimize')}>
          <Minus size={15} />
        </button>
        <button type="button" title={t('最大化或还原')} onClick={() => void window.metis.window('toggle-maximize')}>
          {false ? <Square size={13} /> : <Maximize2 size={14} />}
        </button>
        <button type="button" title={t('隐藏到托盘')} onClick={() => void window.metis.window('hide')}>
          <X size={15} />
        </button>
      </div>
    </header>
  );
}
