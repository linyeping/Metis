import {
  Cable,
  CalendarCheck2,
  FilePlus2,
  Folder,
  MousePointer2,
  NotebookPen,
  Settings,
  SquarePen,
  Store,
  Terminal,
  Wrench,
} from 'lucide-react';
import type { ComponentType } from 'react';
import { useState } from 'react';
import type { AppMode, SectionId } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';

type NavItem =
  | { kind: 'section'; id: SectionId; icon: ComponentType<{ size?: number }>; zh: string; en: string; disabled?: boolean; beta?: boolean }
  | { kind: 'terminal'; icon: ComponentType<{ size?: number }>; zh: string; en: string; disabled?: boolean; beta?: boolean }
  | { kind: 'settings'; icon: ComponentType<{ size?: number }>; zh: string; en: string; disabled?: boolean; beta?: boolean };

const SETTINGS_ITEM: NavItem = { kind: 'settings', icon: Settings, zh: '设置', en: 'Customize' };

// Per-mode sidebar menu, replacing the old global vertical icon rail. Each mode
// surfaces only the tools that belong to it (Metis Desktop style).
const NAV_BY_MODE: Record<AppMode, NavItem[]> = {
  chat: [
    { kind: 'section', id: 'projects' as SectionId, icon: Folder, zh: '项目', en: 'Projects', disabled: true },
    { kind: 'section', id: 'artifacts' as SectionId, icon: NotebookPen, zh: '文档库', en: 'Artifacts', disabled: true },
  ],
  cowork: [
    { kind: 'section', id: 'cron', icon: CalendarCheck2, zh: '定时任务', en: 'Scheduled' },
    { kind: 'section', id: 'skills', icon: Wrench, zh: '技能', en: 'Skills' },
    { kind: 'section', id: 'mcp', icon: Cable, zh: '连接器', en: 'Connectors' },
    { kind: 'section', id: 'store', icon: Store, zh: 'Store', en: 'Store', beta: true },
  ],
  code: [
    { kind: 'section', id: 'computer', icon: MousePointer2, zh: '电脑操控', en: 'Computer' },
    { kind: 'section', id: 'mcp', icon: Cable, zh: 'MCP', en: 'MCP' },
    { kind: 'section', id: 'store', icon: Store, zh: 'Store', en: 'Store', beta: true },
    { kind: 'terminal', icon: Terminal, zh: '终端', en: 'Terminal' },
  ],
};

export function SidebarNav() {
  const appMode = useUiStore(state => state.appMode);
  const activeSection = useUiStore(state => state.activeSection);
  const language = useUiStore(state => state.language);
  const setActiveSection = useUiStore(state => state.setActiveSection);
  const setSettingsOpen = useUiStore(state => state.setSettingsOpen);
  const terminalOpen = useUiStore(state => state.terminalOpen);
  const setTerminalOpen = useUiStore(state => state.setTerminalOpen);
  const startDraftSession = useSessionStore(state => state.startDraftSession);
  const clearChat = useChatStore(state => state.clearLocal);
  const [creating, setCreating] = useState(false);

  const items = NAV_BY_MODE[appMode];
  const showCreateAction = true;
  const CreateIcon = appMode === 'cowork' ? FilePlus2 : SquarePen;
  const createLabel = appMode === 'cowork'
    ? language === 'zh' ? '新任务' : 'New task'
    : appMode === 'chat'
      ? language === 'zh' ? '新对话' : 'New chat'
      : language === 'zh' ? '新会话' : 'New session';
  const createTitle = appMode === 'cowork'
    ? language === 'zh' ? '新建协作任务' : 'Start a new cowork task'
    : appMode === 'chat'
      ? language === 'zh' ? '新建对话' : 'Start a new chat'
      : language === 'zh' ? '新建编码会话' : 'Start a new code session';

  const createModeSession = async () => {
    if (!showCreateAction || creating) return;
    setCreating(true);
    try {
      startDraftSession();
      clearChat();
      setActiveSection('chat');
    } finally {
      setCreating(false);
    }
  };

  return (
    <nav className="sidebar-nav">
      {showCreateAction && (
        <button
          type="button"
          className="sidebar-nav-create"
          title={createTitle}
          disabled={creating}
          onClick={() => void createModeSession()}
        >
          <CreateIcon size={15} />
          <span>{createLabel}</span>
        </button>
      )}
      {items.map(item => {
        const Icon = item.icon;
        const label = language === 'zh' ? item.zh : item.en;
        const statusBadge = item.disabled
          ? language === 'zh' ? '未开放' : 'Not open'
          : item.beta ? 'Beta' : '';
        const active =
          (item.kind === 'section' && activeSection === item.id) ||
          (item.kind === 'terminal' && terminalOpen);
        const onClick = () => {
          if (item.kind === 'section') setActiveSection(item.id);
          else if (item.kind === 'terminal') setTerminalOpen(!terminalOpen);
          else setSettingsOpen(true);
        };
        return (
          <button
            key={item.kind === 'section' ? item.id : item.kind}
            type="button"
            className="sidebar-nav-item"
            data-active={active}
            disabled={item.disabled}
            onClick={onClick}
          >
            <Icon size={16} />
            <span>{label}</span>
            {statusBadge && (
              <em className="sidebar-nav-beta" data-state={item.disabled ? 'unavailable' : 'beta'}>
                {statusBadge}
              </em>
            )}
          </button>
        );
      })}
    </nav>
  );
}
