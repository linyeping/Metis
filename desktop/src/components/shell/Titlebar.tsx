import {
  Check,
  ChevronDown,
  Columns3,
  FileCode,
  Folder,
  Globe,
  Maximize2,
  MessageCircle,
  Minus,
  Network,
  PanelLeft,
  Square,
  SquareTerminal,
  StickyNote,
  X,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useSideChatStore } from '../../store/sideChatStore';
import { useUiStore, type WorkspaceCardId } from '../../store/uiStore';
import { useT } from '../../hooks/useT';

interface TitlebarProps {
  model: string;
}

type CardShortcut = { key: string; shift?: boolean };

const titlebarWorkspaceCardOptions: Array<{ id: WorkspaceCardId; label: string; icon: typeof Globe; keys?: CardShortcut }> = [
  { id: 'web', label: 'Preview', icon: Globe, keys: { key: 'p', shift: true } },
  { id: 'diff', label: 'Diff', icon: FileCode, keys: { key: 'd', shift: true } },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminal, keys: { key: '`' } },
  { id: 'files', label: 'Files', icon: Folder, keys: { key: 'f', shift: true } },
  { id: 'activity', label: 'Background tasks', icon: Network },
  { id: 'plan', label: 'Plan', icon: StickyNote },
];

const isMacPlatform =
  typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent || '');

// Render the platform-correct hint: ⇧⌘P on macOS, Ctrl+Shift+P on Windows/Linux.
function shortcutLabel(keys: CardShortcut): string {
  const k = keys.key === '`' ? '`' : keys.key.toUpperCase();
  if (isMacPlatform) return `${keys.shift ? '⇧' : ''}⌘${k}`;
  return `Ctrl+${keys.shift ? 'Shift+' : ''}${k}`;
}

export function Titlebar({ model }: TitlebarProps) {
  const sidebarOpen = useUiStore(state => state.sidebarOpen);
  const setSidebarOpen = useUiStore(state => state.setSidebarOpen);
  const rightRailOpen = useUiStore(state => state.rightRailOpen);
  const setRightRailOpen = useUiStore(state => state.setRightRailOpen);
  const sideChatOpen = useUiStore(state => state.sideChatOpen);
  const setSideChatOpen = useUiStore(state => state.setSideChatOpen);
  const workspaceCardVisibility = useUiStore(state => state.workspaceCardVisibility);
  const toggleWorkspaceCard = useUiStore(state => state.toggleWorkspaceCard);
  const sideChatStreaming = useSideChatStore(state => state.streaming);
  const setWorkspaceMenuOpen = useUiStore(state => state.setWorkspaceMenuOpen);
  const t = useT();
  const [cardMenuOpen, setCardMenuOpen] = useState(false);
  const cardMenuRef = useRef<HTMLDivElement | null>(null);

  // 原生 preview 视图没有 z-index，永远盖在 DOM 之上。此下拉菜单浮在 preview 区域上方，
  // 打开时必须通知主进程藏掉 preview，否则 webview 会盖住菜单下半部分（尤其末尾两项）。
  useEffect(() => {
    setWorkspaceMenuOpen(cardMenuOpen);
    return () => setWorkspaceMenuOpen(false);
  }, [cardMenuOpen, setWorkspaceMenuOpen]);
  const visibleCardCount = titlebarWorkspaceCardOptions.filter(option => workspaceCardVisibility[option.id]).length;

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

  // Wire the card shortcuts (Ctrl/⌘ [+Shift] + key) — they used to be decorative
  // labels only. Read state via getState to avoid stale closures.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = isMacPlatform ? event.metaKey : event.ctrlKey;
      if (!mod || event.altKey) return;
      const key = event.key.toLowerCase();
      const option = titlebarWorkspaceCardOptions.find(
        item => item.keys && key === item.keys.key && Boolean(item.keys.shift) === event.shiftKey,
      );
      if (!option) return;
      event.preventDefault();
      const ui = useUiStore.getState();
      if (!ui.rightRailOpen && ui.workspaceCardVisibility[option.id]) {
        ui.setRightRailOpen(true);
        return;
      }
      ui.toggleWorkspaceCard(option.id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <header className="titlebar">
      <div className="titlebar-brand" aria-hidden="true" />
      <div className="titlebar-center">
        <span>Metis Desktop</span>
        {model && <em>{model}</em>}
      </div>
      <div className="titlebar-actions">
        <button
          type="button"
          className="titlebar-chat-toggle"
          data-active={sideChatOpen}
          data-streaming={sideChatStreaming}
          title={t('独立 Chat')}
          onClick={() => setSideChatOpen(!sideChatOpen)}
        >
          <MessageCircle size={15} />
        </button>
        <button type="button" title={t('左栏')} data-active={sidebarOpen} onClick={() => setSidebarOpen(!sidebarOpen)}>
          <PanelLeft size={15} />
        </button>
        <div className="titlebar-cards-menu-wrap" ref={cardMenuRef}>
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
                  {option.keys && <em>{shortcutLabel(option.keys)}</em>}
                  <Check size={12} />
                </button>
              );
            })}
          </div>
        </div>
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
