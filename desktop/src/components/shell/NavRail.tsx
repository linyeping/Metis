import { Bot, CalendarClock, Cpu, MessageSquare, Network, Settings, SquareTerminal } from 'lucide-react';
import { createElement } from 'react';
import type { SectionId } from '../../lib/types';
import { tr } from '../../lib/i18n';
import { useUiStore } from '../../store/uiStore';

const items: Array<{ id: SectionId; icon: typeof MessageSquare; label: 'navChat' | 'navSkills' | 'navMcp' | 'navComputer' | 'navCron' }> = [
  { id: 'chat', icon: MessageSquare, label: 'navChat' },
  { id: 'skills', icon: Bot, label: 'navSkills' },
  { id: 'mcp', icon: Network, label: 'navMcp' },
  { id: 'computer', icon: Cpu, label: 'navComputer' },
  { id: 'cron', icon: CalendarClock, label: 'navCron' },
];

export function NavRail() {
  const active = useUiStore(state => state.activeSection);
  const language = useUiStore(state => state.language);
  const setActive = useUiStore(state => state.setActiveSection);
  const setSettingsOpen = useUiStore(state => state.setSettingsOpen);
  const terminalOpen = useUiStore(state => state.terminalOpen);
  const setTerminalOpen = useUiStore(state => state.setTerminalOpen);

  return (
    <nav className="nav-rail">
      <div className="nav-rail-top">
        {items.map(item => {
          const Icon = item.icon;
          return createElement(
            'button',
            {
              key: item.id,
              type: 'button',
              className: 'nav-button',
              'data-active': active === item.id,
              title: tr(language, item.label),
              onClick: () => setActive(item.id),
            },
            <Icon size={20} />,
          );
        })}
      </div>
      <div className="nav-rail-bottom">
        <button
          className="nav-button nav-terminal-button"
          type="button"
          data-active={terminalOpen}
          title={language === 'zh' ? '终端' : 'Terminal'}
          aria-label={language === 'zh' ? '打开或收起终端' : 'Toggle terminal'}
          onClick={() => setTerminalOpen(!terminalOpen)}
        >
          <SquareTerminal size={20} />
        </button>
        <button className="nav-button" type="button" title={tr(language, 'settings')} onClick={() => setSettingsOpen(true)}>
          <Settings size={20} />
        </button>
      </div>
    </nav>
  );
}
