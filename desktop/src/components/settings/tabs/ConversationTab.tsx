import { memo } from 'react';
import { Brain } from 'lucide-react';
import type { MemoryPayload, RuntimeSettings } from '../../../lib/types';
import { useT } from '../../../hooks/useT';

interface ConversationTabProps {
  memory: MemoryPayload | null;
  onMemoryChange: (value: MemoryPayload) => void;
  onSettingsChange: (value: RuntimeSettings) => void;
  settings: RuntimeSettings;
}

export const ConversationTab = memo(function ConversationTab({
  memory,
  onMemoryChange,
  onSettingsChange,
  settings,
}: ConversationTabProps) {
  const t = useT();
  return (
    <div className="settings-card-grid">
      <section className="settings-section memory-panel">
        <div className="settings-section-header">
          <Brain size={16} className="section-icon" />
          <h3>{t('对话设置')}</h3>
        </div>
        <label className="toggle-row capsule-toggle-row">
          <span>{t('自动记忆')}</span>
          <input
            type="checkbox"
            checked={settings.autoMemory}
            onChange={event => onSettingsChange({ ...settings, autoMemory: event.target.checked })}
          />
          <i aria-hidden="true" />
        </label>
        <label className="toggle-row capsule-toggle-row">
          <span>{t('自动创建技能')}</span>
          <input
            type="checkbox"
            checked={settings.autoSkills}
            onChange={event => onSettingsChange({ ...settings, autoSkills: event.target.checked })}
          />
          <i aria-hidden="true" />
        </label>
        {memory && (
          <>
            <label>
              <span>{t('全局记忆')}</span>
              <small>{memory.globalPath}</small>
              <textarea
                value={memory.globalContent}
                onChange={event => onMemoryChange({ ...memory, globalContent: event.target.value })}
              />
            </label>
            <label>
              <span>{t('项目记忆')}</span>
              <small>{memory.projectPath}</small>
              <textarea
                value={memory.projectContent}
                onChange={event => onMemoryChange({ ...memory, projectContent: event.target.value })}
              />
            </label>
          </>
        )}
      </section>
    </div>
  );
});
