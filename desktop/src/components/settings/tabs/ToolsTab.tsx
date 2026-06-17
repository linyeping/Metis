import { memo } from 'react';
import type { ModelCapabilities, PermissionStatePayload } from '../../../lib/types';
import { PermissionPanel } from '../PermissionPanel';
import type { PermissionRuleDraft } from '../settingsShared';
import { useT } from '../../../hooks/useT';

interface ToolsTabProps {
  capabilities: ModelCapabilities | null;
  onCreate: (payload: PermissionRuleDraft) => void | Promise<void>;
  onCreateWritableRoot: (path: string) => void | Promise<void>;
  onDelete: (ruleId: string, tool: string) => void;
  onDeleteMany: (ruleIds: string[]) => void | Promise<void>;
  onDeleteWritableRoot: (rootId: string, path: string) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  permissions: PermissionStatePayload | null;
}

export const ToolsTab = memo(function ToolsTab({
  capabilities,
  onCreate,
  onCreateWritableRoot,
  onDelete,
  onDeleteMany,
  onDeleteWritableRoot,
  onRefresh,
  permissions,
}: ToolsTabProps) {
  const t = useT();
  return (
    <div className="settings-card-grid">
      {capabilities && (
        <div className="tool-tier-bar">
          <span className="tier-label">Tier {capabilities.tier}</span>
          <span className="tier-count">
            {capabilities.toolCount} / {capabilities.totalToolCount} {t('工具已启用')}
          </span>
        </div>
      )}
      <PermissionPanel
        permissions={permissions}
        onRefresh={onRefresh}
        onCreate={onCreate}
        onCreateWritableRoot={onCreateWritableRoot}
        onDeleteMany={onDeleteMany}
        onDelete={onDelete}
        onDeleteWritableRoot={onDeleteWritableRoot}
      />
    </div>
  );
});
