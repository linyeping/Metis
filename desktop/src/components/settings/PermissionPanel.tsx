import { memo, useDeferredValue, useEffect, useMemo, useState } from 'react';
import { FolderOpen, FolderPlus, ShieldAlert, Trash2 } from 'lucide-react';
import type { PermissionAuditEntry, PermissionRule, PermissionStatePayload } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import {
  actionLabel,
  conflictCleanupRuleIds,
  formatTime,
  parsePermissionImport,
  permissionActions,
  permissionPolicyTemplates,
  permissionRuleExport,
  safeJson,
  safeJsonCompact,
  scopeLabel,
  sourceLabel,
  toolRisk,
  type PermissionActionFilter,
  type PermissionRuleAction,
  type PermissionRuleDraft,
} from './settingsShared';

interface PermissionPanelProps {
  onCreate: (payload: PermissionRuleDraft) => void | Promise<void>;
  onCreateWritableRoot: (path: string) => void | Promise<void>;
  onDelete: (ruleId: string, tool: string) => void;
  onDeleteMany: (ruleIds: string[]) => void | Promise<void>;
  onDeleteWritableRoot: (rootId: string, path: string) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  permissions: PermissionStatePayload | null;
}

export const PermissionPanel = memo(function PermissionPanel({
  permissions,
  onRefresh,
  onCreate,
  onCreateWritableRoot,
  onDeleteMany,
  onDelete,
  onDeleteWritableRoot,
}: PermissionPanelProps) {
  const t = useT();
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [actionFilter, setActionFilter] = useState<PermissionActionFilter>('all');
  const [newTool, setNewTool] = useState('');
  const [newAction, setNewAction] = useState<PermissionRuleAction>('ask');
  const [newArgKey, setNewArgKey] = useState('');
  const [newArgPattern, setNewArgPattern] = useState('');
  const [creating, setCreating] = useState(false);
  const [creatingTemplate, setCreatingTemplate] = useState('');
  const [selectedRuleIds, setSelectedRuleIds] = useState<string[]>([]);
  const [exportJson, setExportJson] = useState('');
  const [importJson, setImportJson] = useState('');
  const [importStatus, setImportStatus] = useState('');
  const [bulkBusy, setBulkBusy] = useState(false);
  const [newRootPath, setNewRootPath] = useState('');
  const [rootBusy, setRootBusy] = useState('');
  const rules = permissions?.rules ?? [];
  const audit = permissions?.audit ?? [];
  const writableRoots = permissions?.writableRoots ?? [];
  const suggestedWritableRoots = permissions?.suggestedWritableRoots ?? [];
  const needle = useMemo(() => deferredQuery.trim().toLowerCase(), [deferredQuery]);
  const ruleSearchText = useMemo(() => buildRuleSearchText(rules), [rules]);
  const auditSearchText = useMemo(() => buildAuditSearchText(audit), [audit]);
  const filteredRules = useMemo(() => {
    return rules.filter(rule => {
      const actionMatches = actionFilter === 'all' || rule.action === actionFilter;
      if (!actionMatches) return false;
      if (!needle) return true;
      return (ruleSearchText.get(rule.id) || '').includes(needle);
    });
  }, [actionFilter, needle, ruleSearchText, rules]);
  const selectedRules = useMemo(() => rules.filter(rule => selectedRuleIds.includes(rule.id)), [rules, selectedRuleIds]);
  const cleanupRuleIds = useMemo(() => conflictCleanupRuleIds(rules), [rules]);
  const writableRootSet = useMemo(
    () => new Set(writableRoots.map(root => root.path.trim().toLowerCase()).filter(Boolean)),
    [writableRoots],
  );
  const allFilteredSelected = useMemo(
    () => filteredRules.length > 0 && filteredRules.every(rule => selectedRuleIds.includes(rule.id)),
    [filteredRules, selectedRuleIds],
  );
  const filteredAudit = useMemo(() => {
    return audit.filter(entry => {
      const actionMatches = actionFilter === 'all' || entry.action === actionFilter;
      if (!actionMatches) return false;
      if (!needle) return true;
      return (auditSearchText.get(entry.id) || '').includes(needle);
    });
  }, [actionFilter, audit, auditSearchText, needle]);

  useEffect(() => {
    const validIds = new Set(rules.map(rule => rule.id));
    setSelectedRuleIds(ids => ids.filter(id => validIds.has(id)));
  }, [rules]);

  const createManualRule = async () => {
    const tool = newTool.trim();
    if (!tool || creating) return;
    const pattern = newArgPattern.trim();
    const argKey = (newArgKey.trim() || (pattern ? 'path' : '')).trim();
    setCreating(true);
    try {
      await onCreate({
        tool,
        action: newAction,
        argsMatch: argKey && pattern ? { [argKey]: pattern } : undefined,
      });
      setNewTool('');
      setNewAction('ask');
      setNewArgKey('');
      setNewArgPattern('');
    } finally {
      setCreating(false);
    }
  };

  const createTemplateRule = async (template: (typeof permissionPolicyTemplates)[number]) => {
    if (creatingTemplate) return;
    setCreatingTemplate(template.id);
    try {
      await onCreate({
        tool: template.tool,
        action: template.action,
        argsMatch: template.argsMatch,
        source: template.source,
      });
    } finally {
      setCreatingTemplate('');
    }
  };

  const createWritableRoot = async (path: string) => {
    const target = path.trim();
    if (!target || rootBusy) return;
    setRootBusy(target);
    try {
      await onCreateWritableRoot(target);
      if (target === newRootPath.trim()) setNewRootPath('');
    } finally {
      setRootBusy('');
    }
  };

  const pickWritableRoot = async () => {
    if (rootBusy) return;
    const picked = await window.metis?.pickFolder?.();
    if (!picked) return;
    setNewRootPath(picked);
    await createWritableRoot(picked);
  };

  const toggleRuleSelection = (ruleId: string, checked: boolean) => {
    setSelectedRuleIds(ids => (checked ? Array.from(new Set([...ids, ruleId])) : ids.filter(id => id !== ruleId)));
  };

  const toggleFilteredSelection = (checked: boolean) => {
    setSelectedRuleIds(ids => {
      const filteredIds = filteredRules.map(rule => rule.id);
      if (checked) return Array.from(new Set([...ids, ...filteredIds]));
      const filteredSet = new Set(filteredIds);
      return ids.filter(id => !filteredSet.has(id));
    });
  };

  const bulkDeleteSelected = async () => {
    if (selectedRules.length === 0) return;
    const confirmed = await requestConfirm({
      title: t('批量删除权限规则？'),
      message: `${t('将删除 ')}${selectedRules.length}${t(' 条已选择的权限规则。')}`,
      details: selectedRules.map(rule => `${rule.tool} · ${t(actionLabel(rule.action))} · ${t(scopeLabel(rule.argsMatch))}`).join('\n'),
      confirmLabel: t('删除所选'),
      cancelLabel: t('取消'),
      tone: 'danger',
      icon: 'trash',
    });
    if (!confirmed) return;
    setBulkBusy(true);
    try {
      await onDeleteMany(selectedRules.map(rule => rule.id));
      setSelectedRuleIds([]);
    } finally {
      setBulkBusy(false);
    }
  };

  const exportRules = () => {
    setExportJson(
      JSON.stringify(
        {
          schema: 'metis.permission.rules.v1',
          exportedAt: new Date().toISOString(),
          rules: rules.map(permissionRuleExport),
        },
        null,
        2,
      ),
    );
    setImportStatus('');
  };

  const importRules = async () => {
    if (!importJson.trim() || bulkBusy) return;
    let drafts: PermissionRuleDraft[] = [];
    try {
      drafts = parsePermissionImport(importJson);
    } catch (error) {
      setImportStatus(error instanceof Error ? error.message : String(error));
      return;
    }
    if (drafts.length === 0) {
      setImportStatus(t('没有发现可导入的权限规则。'));
      return;
    }
    const confirmed = await requestConfirm({
      title: t('导入权限规则？'),
      message: `${t('将导入 ')}${drafts.length}${t(' 条权限规则。')}`,
      details: drafts.map(rule => `${rule.tool} · ${t(actionLabel(rule.action))} · ${t(scopeLabel(rule.argsMatch || {}))}`).join('\n'),
      confirmLabel: t('导入'),
      cancelLabel: t('取消'),
      icon: 'info',
    });
    if (!confirmed) return;
    setBulkBusy(true);
    try {
      for (const draft of drafts) {
        await onCreate({ ...draft, source: draft.source || 'settings_import' });
      }
      setImportStatus(`${t('已导入 ')}${drafts.length}${t(' 条规则。')}`);
      setImportJson('');
    } finally {
      setBulkBusy(false);
    }
  };

  const cleanupConflicts = async () => {
    if (cleanupRuleIds.length === 0) return;
    const targets = rules.filter(rule => cleanupRuleIds.includes(rule.id));
    const confirmed = await requestConfirm({
      title: t('清理冲突权限规则？'),
      message: `${t('将删除 ')}${targets.length}${t(' 条重复或冲突的旧规则，保留每个工具范围里最新的一条。')}`,
      details: targets.map(rule => `${rule.tool} · ${t(actionLabel(rule.action))} · ${t(scopeLabel(rule.argsMatch))}`).join('\n'),
      confirmLabel: t('清理冲突'),
      cancelLabel: t('取消'),
      tone: 'danger',
      icon: 'warning',
    });
    if (!confirmed) return;
    setBulkBusy(true);
    try {
      await onDeleteMany(cleanupRuleIds);
      setSelectedRuleIds(ids => ids.filter(id => !cleanupRuleIds.includes(id)));
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className="permission-panel">
      <div className="permission-panel-head">
        <div>
          <h3>{t('权限中心')}</h3>
          <p>{t('管理当前工作区的工具放行、拒绝、询问规则和最近审批记录。')}</p>
        </div>
        <button type="button" onClick={onRefresh}>
          {t('刷新')}
        </button>
      </div>

      <div className="permission-summary-grid">
        <article>
          <strong>{rules.length}</strong>
          <span>{t('保存规则')}</span>
        </article>
        <article>
          <strong>{audit.length}</strong>
          <span>{t('最近审批')}</span>
        </article>
        <article>
          <strong>{rules.filter(rule => rule.action === 'deny').length}</strong>
          <span>{t('拒绝规则')}</span>
        </article>
        <article>
          <strong>{permissions?.controlPlane?.dangerousAllowCount ?? rules.filter(rule => rule.dangerousAllow).length}</strong>
          <span>{t('高风险放行')}</span>
        </article>
      </div>

      <div className="permission-paths">
        <div>
          <span>{t('控制面')}</span>
          <code>{permissions?.controlPlane ? `${permissions.controlPlane.version} · ${permissions.controlPlane.mode}` : t('等待后端返回')}</code>
        </div>
        <div>
          <span>{t('规则文件')}</span>
          <code>{permissions?.path || t('等待后端返回')}</code>
        </div>
        <div>
          <span>{t('审计日志')}</span>
          <code>{permissions?.auditPath || t('等待后端返回')}</code>
        </div>
      </div>

      <section className="permission-writable-roots">
        <div className="permission-section-head">
          <div>
            <h4>{t('授权目录')}</h4>
            <p>{t('全工具访问不等于全盘写入；桌面、文档等工作区外目录需要在这里单独授权。')}</p>
          </div>
        </div>
        <form
          className="permission-root-form"
          onSubmit={event => {
            event.preventDefault();
            void createWritableRoot(newRootPath);
          }}
        >
          <input
            value={newRootPath}
            placeholder="C:\\Users\\you\\Desktop"
            onChange={event => setNewRootPath(event.target.value)}
          />
          <button type="submit" disabled={!newRootPath.trim() || Boolean(rootBusy)}>
            <FolderPlus size={14} />
            <span>{rootBusy === newRootPath.trim() ? t('添加中...') : t('添加目录')}</span>
          </button>
          <button type="button" className="permission-root-picker" disabled={Boolean(rootBusy)} onClick={() => void pickWritableRoot()}>
            <FolderOpen size={14} />
            <span>{t('选择文件夹')}</span>
          </button>
        </form>
        {suggestedWritableRoots.length > 0 && (
          <div className="permission-root-suggestions">
            {suggestedWritableRoots.map(root => {
              const active = writableRootSet.has(root.path.trim().toLowerCase());
              return (
                <button
                  type="button"
                  key={root.key || root.path}
                  disabled={active || Boolean(rootBusy)}
                  onClick={() => void createWritableRoot(root.path)}
                >
                  <FolderPlus size={13} />
                  <span>{suggestedRootLabel(root.key, t)}</span>
                  <code>{root.path}</code>
                </button>
              );
            })}
          </div>
        )}
        {writableRoots.length === 0 ? (
          <p className="permission-empty">{t('尚未授权工作区外可写目录。')}</p>
        ) : (
          <div className="permission-root-list">
            {writableRoots.map(root => (
              <article key={root.id || root.path} className="permission-root-row">
                <div>
                  <strong>{root.path}</strong>
                  <small>{t('来源 · ')}{t(sourceLabel(root.source))}</small>
                </div>
                <button
                  type="button"
                  disabled={Boolean(rootBusy)}
                  onClick={() => void onDeleteWritableRoot(root.id, root.path)}
                  aria-label={`${t('删除')} ${root.path}`}
                >
                  <Trash2 size={14} />
                </button>
              </article>
            ))}
          </div>
        )}
      </section>

      <div className="permission-policy-templates">
        <div>
          <h4>{t('策略模板')}</h4>
          <p>{t('一键添加常见的路径级工具策略，适合真实项目开发时先收紧风险。')}</p>
        </div>
        <div className="permission-template-grid">
          {permissionPolicyTemplates.map(template => (
            <button
              type="button"
              key={template.id}
              className="permission-template-button"
              disabled={Boolean(creatingTemplate)}
              onClick={() => void createTemplateRule(template)}
            >
              <ShieldAlert size={14} />
              <span>
                <strong>{t(template.label)}</strong>
                <small>{template.hint}</small>
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="permission-toolbar">
        <label className="permission-search">
          <span>{t('搜索')}</span>
          <input
            className="permission-search-input"
            value={query}
            placeholder={t('工具、目录、会话或来源')}
            onChange={event => setQuery(event.target.value)}
          />
        </label>
        <div className="permission-filter" role="group" aria-label={t('权限动作筛选')}>
          {permissionActions.map(action => (
            <button type="button" key={action} data-active={actionFilter === action} onClick={() => setActionFilter(action)}>
              {action === 'all' ? t('全部') : t(actionLabel(action))}
            </button>
          ))}
        </div>
      </div>

      <div className="permission-bulk-toolbar">
        <label>
          <input
            type="checkbox"
            checked={allFilteredSelected}
            disabled={filteredRules.length === 0}
            onChange={event => toggleFilteredSelection(event.target.checked)}
          />
          <span>{t('选择当前列表')}</span>
        </label>
        <strong>{selectedRules.length}{t(' 已选择')}</strong>
        <button type="button" disabled={selectedRules.length === 0} onClick={() => void bulkDeleteSelected()}>
          {t('批量删除')}
        </button>
        <button type="button" disabled={cleanupRuleIds.length === 0} onClick={() => void cleanupConflicts()}>
          {t('清理冲突')} {cleanupRuleIds.length || ''}
        </button>
      </div>

      <section className="permission-import-export">
        <div>
          <h4>{t('导入 / 导出')}</h4>
          <p>{t('导出内容只包含工具、动作、参数匹配和来源，不包含 API Key、审计详情或聊天内容。')}</p>
        </div>
        <div className="permission-json-grid">
          <label>
            <span>{t('导出 JSON')}</span>
            <textarea className="permission-export-json" readOnly value={exportJson} placeholder={t('点击导出当前规则')} />
            <button type="button" onClick={exportRules}>
              {t('导出规则')}
            </button>
          </label>
          <label>
            <span>{t('导入 JSON')}</span>
            <textarea
              className="permission-import-json"
              value={importJson}
              placeholder='{"rules":[{"tool":"write_file","action":"ask","argsMatch":{"path":"src/*"}}]}'
              onChange={event => setImportJson(event.target.value)}
            />
            <button type="button" disabled={!importJson.trim() || bulkBusy} onClick={() => void importRules()}>
              {t('导入规则')}
            </button>
          </label>
        </div>
        {importStatus && <p className="permission-import-status">{importStatus}</p>}
      </section>

      <form
        className="permission-create-rule"
        onSubmit={event => {
          event.preventDefault();
          void createManualRule();
        }}
      >
        <div>
          <span>{t('手动添加规则')}</span>
          <p>{t('可选填写参数模式，例如 `path = src/*`，用于路径级放行、拒绝或询问。')}</p>
        </div>
        <input
          className="permission-new-tool-input"
          value={newTool}
          placeholder="write_file"
          onChange={event => setNewTool(event.target.value)}
        />
        <select
          className="permission-new-action-select"
          value={newAction}
          onChange={event => setNewAction(event.target.value as PermissionRuleAction)}
        >
          <option value="ask">{t('每次询问')}</option>
          <option value="allow">{t('总是允许')}</option>
          <option value="deny">{t('总是拒绝')}</option>
        </select>
        <input
          className="permission-new-arg-key-input"
          value={newArgKey}
          placeholder="path"
          onChange={event => setNewArgKey(event.target.value)}
        />
        <input
          className="permission-new-arg-pattern-input"
          value={newArgPattern}
          placeholder="src/*"
          onChange={event => setNewArgPattern(event.target.value)}
        />
        <button type="submit" className="permission-create-button" disabled={!newTool.trim() || creating}>
          {creating ? t('添加中...') : t('添加规则')}
        </button>
      </form>

      <section>
        <h4>{t('已保存规则')}</h4>
        {filteredRules.length === 0 ? (
          <p className="permission-empty">{t('没有匹配的保存规则。遇到危险工具时可以在确认弹窗里选择记住，也可以手动添加。')}</p>
        ) : (
          <div className="permission-list">
            {filteredRules.map(rule => {
              const risk = toolRisk(rule.tool);
              return (
                <article key={rule.id} className="permission-row" data-action={rule.action} data-risk={risk.level}>
                  <input
                    className="permission-rule-checkbox"
                    type="checkbox"
                    checked={selectedRuleIds.includes(rule.id)}
                    aria-label={`${t('选择 ')}${rule.tool}`}
                    onChange={event => toggleRuleSelection(rule.id, event.target.checked)}
                  />
                  <div>
                    <strong>{rule.tool}</strong>
                    <span>{t(actionLabel(rule.action))}</span>
                    <small className="permission-risk">{t(risk.label)} · {t(risk.hint)}</small>
                    {Object.keys(rule.argsMatch).length > 0 && <small className="permission-scope">{t('范围 · ')}{t(scopeLabel(rule.argsMatch))}</small>}
                    {rule.source && <small>{t('来源 · ')}{t(sourceLabel(rule.source))}</small>}
                    <em>{t(formatTime(rule.updatedAt || rule.createdAt))}</em>
                  </div>
                  <button type="button" onClick={() => onDelete(rule.id, rule.tool)}>
                    {t('删除')}
                  </button>
                </article>
              );
            })}
          </div>
        )}
      </section>

      <section>
        <h4>{t('最近审批')}</h4>
        {filteredAudit.length === 0 ? (
          <p className="permission-empty">{t('没有匹配的审批记录。')}</p>
        ) : (
          <div className="permission-list audit">
            {filteredAudit.slice(0, 12).map(entry => {
              const risk = toolRisk(entry.tool);
              return (
                <article key={entry.id} className="permission-row" data-action={entry.action} data-risk={risk.level}>
                  <div>
                    <strong>{entry.tool || 'tool'}</strong>
                    <span>
                      {entry.approved ? t('允许') : t('拒绝')}
                      {entry.remember ? `${t(' · 已记住 ')}${t(actionLabel(entry.remember))}` : ''}
                    </span>
                    <small className="permission-risk">{t(risk.label)} · {entry.cwd || entry.sessionId || entry.source}</small>
                    <small>{safeJson(entry.arguments)}</small>
                    <em>{t(formatTime(entry.createdAt))}</em>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
});

function buildRuleSearchText(rules: PermissionRule[]): Map<string, string> {
  return new Map(
    rules.map(rule => [
      rule.id,
      `${rule.tool} ${rule.action} ${rule.source} ${safeJsonCompact(rule.argsMatch)}`.toLowerCase(),
    ]),
  );
}

function suggestedRootLabel(key: string, t: (value: string) => string): string {
  if (key === 'desktop') return t('添加桌面');
  if (key === 'documents') return t('添加文档');
  if (key === 'downloads') return t('添加下载');
  return t('添加目录');
}

function buildAuditSearchText(audit: PermissionAuditEntry[]): Map<string, string> {
  return new Map(
    audit.map(entry => [
      entry.id,
      `${entry.tool} ${entry.action} ${entry.cwd} ${entry.sessionId} ${entry.source} ${safeJsonCompact(entry.arguments)}`.toLowerCase(),
    ]),
  );
}
