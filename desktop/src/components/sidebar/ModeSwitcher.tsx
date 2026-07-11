import { Code2 } from 'lucide-react';
import { ChatBubbleIcon, CoworkTaskIcon } from '../icons/MetisIcons';
import { navigateAppMode } from '../../lib/modeNavigation';
import type { AppMode } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';

const MODES: Array<{ id: AppMode; icon: React.ComponentType<{ size?: number; strokeWidth?: number; className?: string }>; zh: string; en: string }> = [
  { id: 'chat',   icon: ChatBubbleIcon,  zh: '对话', en: 'Chat'   },
  { id: 'cowork', icon: CoworkTaskIcon,  zh: '协作', en: 'Cowork' },
  { id: 'code',   icon: Code2,           zh: '编码', en: 'Code'   },
];

/**
 * Top-level Chat / Cowork / Code switcher (Claude Desktop style): three equal
 * segments spanning the sidebar width, each showing icon + label, active one
 * highlighted. Switching modes returns to that mode's home thread.
 */
export function ModeSwitcher() {
  const appMode = useUiStore(state => state.appMode);
  const language = useUiStore(state => state.language);
  const activeIndex = MODES.findIndex(mode => mode.id === appMode);

  return (
    <div
      className="mode-switcher"
      role="tablist"
      aria-label={language === 'zh' ? '模式' : 'Mode'}
      style={{ '--mode-index': activeIndex } as React.CSSProperties}
    >
      <span className="mode-switcher-pill" aria-hidden="true" />
      {MODES.map(mode => {
        const Icon = mode.icon;
        const active = appMode === mode.id;
        const label = language === 'zh' ? mode.zh : mode.en;
        return (
          <button
            key={mode.id}
            type="button"
            role="tab"
            aria-selected={active}
            aria-label={label}
            title={label}
            className="mode-switcher-tab"
            data-active={active}
            onClick={() => {
              if (active) return;
              navigateAppMode(mode.id);
            }}
          >
            <span className="mode-switcher-tab-content">
              <Icon size={14} />
              <span className="mode-switcher-label">{label}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
