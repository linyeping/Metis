import {
  AlertTriangle,
  Bot,
  Calendar,
  ChevronDown,
  ChevronRight,
  Cpu,
  Database,
  ExternalLink,
  FileText,
  FolderOpen,
  GitBranch,
  Globe2,
  KeyRound,
  Network,
  PackagePlus,
  PauseCircle,
  PencilLine,
  PlayCircle,
  Plus,
  PlugZap,
  Power,
  RefreshCw,
  Save,
  Search,
  Shield,
  ShieldCheck,
  Store,
  Trash2,
  Unplug,
  UploadCloud,
  Wrench,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  addMarketplaceSource,
  connectConnector,
  deleteMarketplaceSource,
  deleteSkill,
  configureMarketplaceItem,
  disconnectConnector,
  getDeskGoalLog,
  getModelCapabilities,
  getDeskStatus,
  getSettings,
  getSkill,
  getSkills,
  getMarketplaceCatalog,
  getMarketplaceSources,
  importSkill,
  installMarketplaceItem,
  installMarketplaceSource,
  listBackendConnectors,
  openSkillFolder,
  pauseDeskAutomation,
  resumeDeskAutomation,
  refreshMarketplaceSource,
  saveSkill,
  setDeskEnabled,
  setSkillEnabled,
  setMarketplaceItemEnabled,
  searchMarketplaceMcp,
  testConnector,
  uninstallMarketplaceItem,
} from '../../lib/api';
import type { BackendConnector } from '../../lib/api';
import type { DeskGoalLogEntry, DeskStatusPayload, MarketplaceEnvironmentVariable, MarketplaceItem, MarketplaceItemKind, MarketplaceSource, ModelCapabilities, SectionId, SkillDetail, SkillFileEntry, SkillSummary } from '../../lib/types';
import { marketplaceItemDescription, marketplaceRegistrySearchQuery } from '../../lib/marketplaceDescriptions';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import { MarkdownText } from '../chat/threadUtils';
import { ConnectorLogo } from '../connectors/ConnectorLogo';
import metisMark from '../../assets/metis-M-128.png';

import { ChatListPanel } from './ChatListPanel';

type ZoneSection = Exclude<SectionId, 'chat' | 'cron'>;

export function SectionMain({ section }: { section: ZoneSection }) {
  if (section === 'chat-list') return <ChatListPanel />;
  if (section === 'skills') return <SkillsPanel />;
  if (section === 'mcp') return <McpPanel />;
  if (section === 'store') return <StorePanel />;
  return <ComputerPanel />;
}

function SkillsPanel() {
  const t = useT();
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [learning, setLearning] = useState<{ autoMemory: boolean; autoSkills: boolean } | null>(null);
  const [selectedSkillId, setSelectedSkillId] = useState('');
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [draft, setDraft] = useState('');
  const [importPath, setImportPath] = useState('');
  const [skillQuery, setSkillQuery] = useState('');
  const [expandedSkillIds, setExpandedSkillIds] = useState<string[]>([]);
  const [skillViewMode, setSkillViewMode] = useState<'preview' | 'edit'>('preview');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [deletingSkill, setDeletingSkill] = useState('');
  const [busy, setBusy] = useState('');
  const requestConfirm = useUiStore(state => state.requestConfirm);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const [nextSkills, settings] = await Promise.all([getSkills(), getSettings()]);
      setSkills(nextSkills);
      setLearning({ autoMemory: settings.autoMemory, autoSkills: settings.autoSkills });
      if (selectedSkillId && !nextSkills.some(skill => skill.id === selectedSkillId)) {
        setSelectedSkillId('');
        setDetail(null);
        setDraft('');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const openSkill = async (skillId: string) => {
    if (!skillId) return;
    setBusy(`detail:${skillId}`);
    setError('');
    try {
      const next = await getSkill(skillId);
      setSelectedSkillId(next.id);
      setDetail(next);
      setDraft(next.content);
      setSkillViewMode('preview');
      setExpandedSkillIds(current => (current.includes(skillId) ? current : [...current, skillId]));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const saveCurrentSkill = async () => {
    if (!detail || draft === detail.content) return;
    setBusy('save');
    setError('');
    try {
      const next = await saveSkill(detail.id, draft);
      setDetail(next);
      setDraft(next.content);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const toggleCurrentSkill = async () => {
    if (!detail) return;
    setBusy('toggle');
    setError('');
    try {
      const next = await setSkillEnabled(detail.id, !detail.enabled);
      setDetail(next);
      setDraft(next.content);
      setSkills(current => current.map(skill => (skill.id === next.id ? { ...skill, enabled: next.enabled } : skill)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const importFromPath = async (path: string) => {
    const value = path.trim();
    if (!value) return;
    setBusy('import');
    setError('');
    try {
      const next = await importSkill(value);
      setImportPath('');
      setSelectedSkillId(next.id);
      setDetail(next);
      setDraft(next.content);
      setSkillViewMode('preview');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const pickAndImport = async () => {
    const path = await window.metis.pickFolder();
    if (!path) return;
    setImportPath(path);
    await importFromPath(path);
  };

  const openCurrentFolder = async () => {
    if (!detail) return;
    setBusy('open-folder');
    setError('');
    try {
      const result = await openSkillFolder(detail.id);
      if (!result.ok) setError(`${t('无法打开目录: ')}${result.path || detail.path}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const removeSkill = async (skill: SkillSummary) => {
    if (!skill.id || deletingSkill) return;
    const confirmed = await requestConfirm({
      title: t('删除本地技能？'),
      message: skill.name || skill.id,
      details: `${t('会删除对应的 SKILL.md 目录。')}\n${skill.path}\n\n${t('此操作不能撤销。')}`,
      confirmLabel: t('删除'),
      cancelLabel: t('取消'),
      tone: 'danger',
      icon: 'trash',
    });
    if (!confirmed) return;

    setDeletingSkill(skill.id);
    setError('');
    try {
      await deleteSkill(skill.id);
      setSkills(current => current.filter(item => item.id !== skill.id));
      if (selectedSkillId === skill.id) {
        setSelectedSkillId('');
        setDetail(null);
        setDraft('');
        setSkillViewMode('preview');
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingSkill('');
    }
  };

  const toggleSkillExpanded = (skillId: string) => {
    setExpandedSkillIds(current => (current.includes(skillId) ? current.filter(id => id !== skillId) : [...current, skillId]));
  };

  const enabledCount = skills.filter(skill => skill.enabled).length;
  const hasUnsavedChanges = Boolean(detail && draft !== detail.content);
  const normalizedSkillQuery = skillQuery.trim().toLowerCase();
  const visibleSkills = useMemo(() => {
    if (!normalizedSkillQuery) return skills;
    return skills.filter(skill =>
      [
        skill.name,
        skill.skillName,
        skill.description,
        skill.whenToUse,
        skill.preview,
        skill.path,
        skill.source,
        ...skill.paths,
      ]
        .join(' ')
        .toLowerCase()
        .includes(normalizedSkillQuery),
    );
  }, [normalizedSkillQuery, skills]);
  const groupedSkills = useMemo(
    () => [
      { source: 'personal', title: t('个人技能'), items: visibleSkills.filter(skill => skill.source !== 'builtin') },
      { source: 'builtin', title: t('内置技能'), items: visibleSkills.filter(skill => skill.source === 'builtin') },
    ],
    [visibleSkills, t],
  );
  const skillsDirectory = useMemo(() => {
    const samplePath = detail?.path || skills[0]?.path || '';
    if (!samplePath) return 'METIS_HOME/skills';
    const parts = samplePath.split(/[\\/]/);
    let skillsIndex = -1;
    parts.forEach((part, index) => {
      if (part.toLowerCase() === 'skills') skillsIndex = index;
    });
    if (skillsIndex >= 0) {
      const separator = samplePath.includes('\\') ? '\\' : '/';
      return parts.slice(0, skillsIndex + 1).join(separator);
    }
    return samplePath.replace(/[\\/]SKILL\.md$/i, '');
  }, [detail?.path, skills]);
  const sourceLabel = (source: string) => {
    if (source === 'project') return t('项目技能');
    if (source === 'builtin') return t('内置技能');
    if (source === 'global') return t('全局技能');
    return source || t('个人技能');
  };

  return (
    <section className="zone-panel skills-panel" data-zone="skills">
      <ZoneHeader
        icon={Wrench}
        title={t('技能')}
        eyebrow="Customize"
        status={loading ? t('加载中') : error ? t('读取失败') : `${skills.length} ${t('个技能')}`}
        ok={!error}
        onRefresh={load}
      />
      {error && <InlineError message={error} onRetry={load} />}
      <div className="skills-manager">
        <div className="skills-workbench">
          <section className="skills-browser" aria-label={t('技能列表')}>
            <header className="skills-browser-header">
              <div>
                <strong>{t('技能')}</strong>
                <span>
                  {enabledCount}/{skills.length} {t('已启用')}
                  {' · '}
                  {t('自学习')}: {learning?.autoSkills ? t('自动技能开') : t('自动技能关')}
                </span>
              </div>
              <button type="button" disabled={busy === 'import'} onClick={() => void pickAndImport()}>
                <UploadCloud size={14} />
                <span>{t('导入')}</span>
              </button>
            </header>
            <label className="skills-search">
              <span>{t('搜索')}</span>
              <input value={skillQuery} placeholder={t('搜索技能、路径或触发方式')} onChange={event => setSkillQuery(event.target.value)} />
            </label>
            <div className="skill-import-row">
              <input
                className="skill-import-input"
                value={importPath}
                placeholder={t('粘贴包含 SKILL.md 的目录路径')}
                onChange={event => setImportPath(event.target.value)}
              />
              <button
                className="skill-import-button"
                type="button"
                disabled={busy === 'import' || !importPath.trim()}
                onClick={() => void importFromPath(importPath)}
              >
                {busy === 'import' ? t('导入中') : t('添加')}
              </button>
            </div>
            <div className="skill-directory-note">{skillsDirectory}</div>
            <div className="zone-list skill-list-live">
              {!loading && visibleSkills.length === 0 && (
                <article className="zone-empty">
                  <FileText size={18} />
                  <span>{skills.length === 0 ? t('暂无本地技能') : t('没有匹配的技能')}</span>
                  <small>{skills.length === 0 ? t('完成复杂任务后可沉淀为 SKILL.md。') : t('换个关键词再试。')}</small>
                </article>
              )}
              {groupedSkills.map(group => (
                group.items.length > 0 && (
                  <div className="skill-group" data-source={group.source} key={group.source}>
                    <span className="skill-group-label">{group.title}</span>
                    {group.items.map(skill => (
                      <article className="skill-tree-item" data-active={selectedSkillId === skill.id} data-source={skill.source} key={skill.id || skill.path || skill.name}>
                        <div className="zone-row skill-row">
                          <SkillLogo skill={skill} />
                          <button
                            className="skill-detail-button skill-row-main"
                            type="button"
                            disabled={!skill.id || busy === `detail:${skill.id}`}
                            onClick={() => void openSkill(skill.id)}
                          >
                            <strong>{skill.name || 'Unnamed skill'}</strong>
                            <small>
                              {sourceLabel(skill.source)}
                              {' · '}
                              {skill.userInvocable ? `/${skill.skillName || skill.id}` : t('后台技能')}
                              {' · '}
                              {skill.disableModelInvocation ? t('仅手动') : t('自动触发')}
                            </small>
                          </button>
                          <div className="row-actions skill-actions">
                            <span className="skill-state-dot" data-ok={skill.enabled} title={skill.enabled ? t('启用') : t('停用')} />
                            <button
                              className="danger-action skill-delete-button"
                              type="button"
                              disabled={!skill.id || deletingSkill === skill.id}
                              onClick={() => void removeSkill(skill)}
                            >
                              <Trash2 size={13} />
                              <span>{deletingSkill === skill.id ? t('删除中') : t('删除')}</span>
                            </button>
                          </div>
                          <button
                            className="skill-expand-button"
                            type="button"
                            aria-expanded={expandedSkillIds.includes(skill.id)}
                            onClick={() => toggleSkillExpanded(skill.id)}
                          >
                            <ChevronRight className="disclosure-chevron" data-open={expandedSkillIds.includes(skill.id)} size={14} />
                          </button>
                        </div>
                        {expandedSkillIds.includes(skill.id) && (
                          <SkillFileTree files={skill.files} skillId={skill.id} onOpenSkill={openSkill} />
                        )}
                      </article>
                    ))}
                  </div>
                )
              ))}
            </div>
          </section>

          <aside className="skill-detail-panel">
            {!detail ? (
              <div className="zone-empty">
                <FileText size={18} />
                <span>{t('选择一个技能')}</span>
                <small>{t('查看、编辑或停用自动沉淀的 SKILL.md。')}</small>
              </div>
            ) : (
              <>
                <header>
                  <div>
                    <strong>{detail.name || detail.id}</strong>
                    <span>{detail.path}</span>
                  </div>
                  <button
                    className="skill-toggle-switch skill-toggle-button"
                    type="button"
                    data-on={detail.enabled}
                    disabled={busy === 'toggle'}
                    onClick={() => void toggleCurrentSkill()}
                  >
                    <span>{detail.enabled ? t('已启用') : t('已停用')}</span>
                  </button>
                </header>
                <div className="skill-detail-meta">
                  <p>
                    <span>{t('来源')}</span>
                    <strong>{sourceLabel(detail.source)}</strong>
                  </p>
                  <p>
                    <span>{t('触发')}</span>
                    <strong>
                      {detail.userInvocable ? `/${detail.skillName || detail.id}` : t('后台技能')}
                      {detail.disableModelInvocation ? ` · ${t('仅手动')}` : ` · ${t('自动')}`}
                    </strong>
                  </p>
                </div>
                <div className="skill-detail-toolbar">
                  <div className="skill-mode-switch" role="tablist" aria-label={t('SKILL.md 视图')}>
                    <button type="button" role="tab" aria-selected={skillViewMode === 'preview'} data-active={skillViewMode === 'preview'} onClick={() => setSkillViewMode('preview')}>
                      <FileText size={13} />
                      <span>{t('预览')}</span>
                    </button>
                    <button type="button" role="tab" aria-selected={skillViewMode === 'edit'} data-active={skillViewMode === 'edit'} onClick={() => setSkillViewMode('edit')}>
                      <PencilLine size={13} />
                      <span>{t('编辑')}</span>
                    </button>
                  </div>
                  <div className="skill-detail-actions">
                    <button className="skill-open-folder-button" type="button" disabled={busy === 'open-folder'} onClick={() => void openCurrentFolder()}>
                      <FolderOpen size={13} />
                      {t('打开目录')}
                    </button>
                    <button className="skill-save-button" type="button" disabled={busy === 'save' || !hasUnsavedChanges} onClick={() => void saveCurrentSkill()}>
                      <Save size={13} />
                      {busy === 'save' ? t('保存中') : t('保存')}
                    </button>
                  </div>
                </div>
                <div className="skill-detail-body" data-mode={skillViewMode}>
                  {skillViewMode === 'preview' ? (
                    <div className="skill-rendered-preview markdown-body">
                      <MarkdownText text={draft} />
                    </div>
                  ) : (
                    <div className="skill-editor-shell">
                      <div className="skill-file-tab">
                        <span className="skill-file-tab-label">
                          <FileText size={13} />
                          <span>SKILL.md</span>
                        </span>
                        {hasUnsavedChanges && <em>{t('未保存')}</em>}
                      </div>
                      <textarea
                        className="skill-editor"
                        value={draft}
                        spellCheck={false}
                        onChange={event => setDraft(event.target.value)}
                      />
                    </div>
                  )}
                </div>
              </>
            )}
          </aside>
        </div>
      </div>
    </section>
  );
}

function SkillLogo({ skill }: { skill: SkillSummary }) {
  const Icon = skillIconFor(skill);
  const style = skill.brandColor ? ({ '--skill-brand': skill.brandColor } as CSSProperties) : undefined;
  return (
    <span className="skill-logo" data-source={skill.source} data-skill={skill.skillName || skill.id} data-color={Boolean(skill.iconDataUrl || skill.brandColor)} style={style}>
      {skill.iconDataUrl ? <img src={skill.iconDataUrl} alt="" /> : <Icon size={15} />}
    </span>
  );
}

function skillIconFor(skill: SkillSummary): typeof Bot {
  const key = `${skill.skillName} ${skill.name} ${skill.id}`.toLowerCase();
  if (key.includes('browser') || key.includes('web')) return Globe2;
  if (key.includes('review') || key.includes('checklist')) return Shield;
  if (key.includes('coding') || key.includes('frontend') || key.includes('app')) return GitBranch;
  if (key.includes('computer') || key.includes('desktop')) return Cpu;
  if (key.includes('debug')) return Wrench;
  if (key.includes('document') || key.includes('docx') || key.includes('pdf')) return FileText;
  if (key.includes('git')) return GitBranch;
  if (key.includes('schedule') || key.includes('cron')) return Calendar;
  if (key.includes('data')) return Database;
  if (skill.source === 'builtin') return Store;
  return FileText;
}

function SkillFileTree({
  files,
  skillId,
  onOpenSkill,
}: {
  files: SkillFileEntry[];
  skillId: string;
  onOpenSkill: (skillId: string) => Promise<void>;
}) {
  const t = useT();
  const rows = files.length > 0 ? files : [{ name: 'SKILL.md', path: 'SKILL.md', kind: 'file' as const, children: [] }];
  return (
    <div className="skill-file-tree">
      {rows.map(file => (
        <SkillFileNode key={file.path || file.name} file={file} skillId={skillId} depth={0} onOpenSkill={onOpenSkill} />
      ))}
      {rows.length === 0 && <span>{t('暂无文件')}</span>}
    </div>
  );
}

function SkillFileNode({
  file,
  skillId,
  depth,
  onOpenSkill,
}: {
  file: SkillFileEntry;
  skillId: string;
  depth: number;
  onOpenSkill: (skillId: string) => Promise<void>;
}) {
  const isSkillFile = file.name.toLowerCase() === 'skill.md';
  return (
    <div className="skill-file-node" data-kind={file.kind} style={{ '--skill-file-depth': String(depth) } as CSSProperties}>
      <button type="button" disabled={!isSkillFile && file.kind !== 'directory'} onClick={() => (isSkillFile ? void onOpenSkill(skillId) : undefined)}>
        {file.kind === 'directory' ? <FolderOpen size={13} /> : <FileText size={13} />}
        <span>{file.name}</span>
      </button>
      {file.children.length > 0 && (
        <div>
          {file.children.map(child => (
            <SkillFileNode key={child.path || child.name} file={child} skillId={skillId} depth={depth + 1} onOpenSkill={onOpenSkill} />
          ))}
        </div>
      )}
    </div>
  );
}

function McpPanel() {
  const t = useT();
  const [connectors, setConnectors] = useState<BackendConnector[]>([]);
  const [selectedConnectorId, setSelectedConnectorId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionNotice, setActionNotice] = useState('');
  const [busyConnector, setBusyConnector] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const next = await listBackendConnectors();
      setConnectors(next);
      setSelectedConnectorId(current => (current && next.some(connector => connector.serviceId === current) ? current : next[0]?.serviceId || ''));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const runConnectorAction = async (connector: BackendConnector, action: 'connect' | 'disconnect' | 'test') => {
    setBusyConnector(`${action}:${connector.serviceId}`);
    setError('');
    setActionNotice(`${connector.displayName} · ${action === 'connect' ? t('正在连接') : action === 'disconnect' ? t('正在断开') : t('正在测试')}`);
    try {
      const result =
        action === 'connect'
          ? await connectConnector(connector.serviceId)
          : action === 'disconnect'
            ? await disconnectConnector(connector.serviceId)
            : await testConnector(connector.serviceId);
      if (result.error) {
        setError(result.error);
        setActionNotice(`${connector.displayName} · ${t('操作失败')}`);
      } else {
        const resultTools = (result as { tools?: unknown }).tools;
        const resultToolsCount = (result as { toolsCount?: unknown }).toolsCount;
        const detail =
          action === 'connect' && Array.isArray(resultTools)
            ? `${resultTools.length} ${t('个工具')}`
            : action === 'test' && typeof resultToolsCount === 'number'
              ? `${resultToolsCount} ${t('个工具')}`
              : '';
        setActionNotice(`${connector.displayName} · ${action === 'disconnect' ? t('已断开') : action === 'connect' ? t('已连接') : t('连通正常')}${detail ? ` · ${detail}` : ''}`);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setActionNotice(`${connector.displayName} · ${t('操作失败')}`);
    } finally {
      setBusyConnector('');
    }
  };

  const selected = connectors.find(connector => connector.serviceId === selectedConnectorId) || connectors[0] || null;
  const activeCount = connectors.filter(connector => connector.active).length;
  const grouped = connectorGroups(connectors, t);
  const selectedTools = selected?.tools.length ? selected.tools : connectorFallbackTools(selected);

  return (
    <section className="zone-panel connectors-panel" data-zone="mcp">
      <ZoneHeader
        icon={Network}
        title={t('连接器')}
        eyebrow="Customize"
        status={loading ? t('加载中') : error ? t('读取失败') : `${activeCount}/${connectors.length} ${t('已连接')}`}
        ok={!error}
        onRefresh={load}
      />
      {error && <InlineError message={error} onRetry={load} />}
      {actionNotice && <p className="connector-action-notice">{actionNotice}</p>}
      <div className="connectors-manager">
        <section className="connectors-browser" aria-label={t('连接器列表')}>
          <header className="connectors-browser-header">
            <div>
              <strong>{t('连接器')}</strong>
              <span>{t('按服务类型管理 MCP 连接')}</span>
            </div>
            <Search size={15} />
          </header>
          <div className="connector-group-list">
            {!loading && connectors.length === 0 && (
              <article className="zone-empty">
                <PlugZap size={18} />
                <span>{t('暂无连接器')}</span>
              </article>
            )}
            {grouped.map(group => (
              group.items.length > 0 && (
                <div className="connector-group" key={group.id}>
                  <span className="connector-group-label">{group.title}</span>
                  {group.items.map(connector => (
                    <button
                      type="button"
                      className="connector-catalog-row"
                      data-active={selected?.serviceId === connector.serviceId}
                      key={connector.serviceId}
                      onClick={() => setSelectedConnectorId(connector.serviceId)}
                    >
                      <ConnectorLogo serviceId={connector.serviceId} active={connector.active} />
                      <span>
                        <strong>{connector.displayName}</strong>
                        <small>{connector.active ? t('已连接') : connector.hasToken || connector.authKind === 'none' ? t('可连接') : t('待授权')}</small>
                      </span>
                      <em>{connector.active ? t('断开') : t('连接')}</em>
                    </button>
                  ))}
                </div>
              )
            ))}
          </div>
        </section>

        <aside className="connector-detail-panel">
          {!selected ? (
            <article className="zone-empty">
              <PlugZap size={18} />
              <span>{t('选择一个连接器')}</span>
            </article>
          ) : (
            <>
              <header>
                <div>
                  <ConnectorLogo serviceId={selected.serviceId} active={selected.active} />
                  <span>
                    <strong>{selected.displayName}</strong>
                    <em>{selected.authKind === 'none' ? t('无需授权') : selected.hasToken ? t('已授权') : t('待授权')}</em>
                  </span>
                </div>
                <div className="connector-detail-actions">
                  <button
                    type="button"
                    disabled={busyConnector === `connect:${selected.serviceId}` || selected.active}
                    onClick={() => void runConnectorAction(selected, 'connect')}
                  >
                    {busyConnector === `connect:${selected.serviceId}` ? t('连接中') : t('连接')}
                  </button>
                  <button
                    type="button"
                    disabled={busyConnector === `disconnect:${selected.serviceId}` || !selected.active}
                    onClick={() => void runConnectorAction(selected, 'disconnect')}
                  >
                    {busyConnector === `disconnect:${selected.serviceId}` ? t('断开中') : t('断开')}
                  </button>
                </div>
              </header>
              <p className="connector-detail-copy">{connectorSummary(selected)}</p>
              <div className="connector-detail-meta">
                <p>
                  <span>{t('认证')}</span>
                  <strong>{selected.authKind === 'none' ? t('无需授权') : selected.tokenEnv || selected.credentialsEnvs.join(', ') || selected.secretEnvs.join(', ') || t('环境变量')}</strong>
                </p>
                <p>
                  <span>{t('工具')}</span>
                  <strong>{selected.active ? `${selected.toolsCount} ${t('个')}` : t('未连接')}</strong>
                </p>
              </div>
              <div className="connector-notes">
                {selected.notes.slice(0, 3).map(note => (
                  <p key={note}>{note}</p>
                ))}
              </div>
              <section className="connector-usage">
                <span>{t('怎么使用')}</span>
                <p>{connectorUsageHint(selected, t)}</p>
              </section>
              <section className="connector-permissions">
                <header>
                  <span>{t('工具权限')}</span>
                  <button type="button" disabled={busyConnector === `test:${selected.serviceId}`} onClick={() => void runConnectorAction(selected, 'test')}>
                    <RefreshCw size={13} />
                    {busyConnector === `test:${selected.serviceId}` ? t('测试中') : t('测试')}
                  </button>
                </header>
                <div>
                  {selectedTools.map(tool => (
                    <p key={tool.name}>
                      <span>{tool.name}</span>
                      <em>{tool.description || t('按需审批')}</em>
                    </p>
                  ))}
                </div>
              </section>
              <section className="connector-command">
                <span>{t('启动命令')}</span>
                <code>{[selected.command, ...selected.args].filter(Boolean).join(' ') || selected.url || selected.serviceId}</code>
              </section>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

function connectorGroups(connectors: BackendConnector[], t: (value: string) => string) {
  return [
    { id: 'popular', title: t('常用'), items: connectors.filter(connector => ['slack', 'google_calendar', 'notion'].includes(connector.serviceId)) },
    { id: 'web', title: 'Web', items: connectors.filter(connector => ['github', 'gmail', 'google_drive', 'x_docs', 'x_api'].includes(connector.serviceId)) },
    { id: 'desktop', title: t('桌面'), items: connectors.filter(connector => connector.serviceId === 'filesystem') },
    { id: 'data', title: t('数据'), items: connectors.filter(connector => connector.serviceId === 'postgres') },
  ];
}

function connectorFallbackTools(connector: BackendConnector | null): Array<{ name: string; description: string }> {
  if (!connector) return [];
  const base = connector.scopes.length > 0 ? connector.scopes : connector.notes.slice(0, 4);
  return base.slice(0, 12).map((item, index) => ({
    name: item.replace(/^https?:\/\/[^/]+\//, '').replace(/[^\w:.-]+/g, ' ').trim() || `${connector.serviceId}_${index + 1}`,
    description: connector.active ? '' : 'Connect to inspect live tools',
  }));
}

function connectorSummary(connector: BackendConnector): string {
  if (connector.notes[0]) return connector.notes[0];
  if (connector.scopes.length > 0) return connector.scopes.join(', ');
  return `${connector.displayName} MCP connector`;
}

function connectorUsageHint(connector: BackendConnector, t: (value: string) => string): string {
  if (connector.serviceId === 'x_docs') {
    return t('连接后直接在 Chat / Research 里问 X 官方文档相关问题，模型会自动调用 X Docs 的搜索和文档读取工具。');
  }
  if (connector.serviceId === 'x_api') {
    return t('先到设置 → 连接器保存 X Developer App 的 CLIENT_ID / CLIENT_SECRET，重启后端后再连接；连接成功后，模型可按需使用 X API MCP 工具。');
  }
  if (connector.authKind === 'none') {
    return t('点击连接后，该连接器的 MCP 工具会加入当前工具池，后续任务需要时模型会自动调用。');
  }
  return t('先完成授权或保存配置，再点击连接；连接成功后，该服务的 MCP 工具会加入当前工具池，后续任务需要时模型会自动调用。');
}

function StorePanel() {
  const t = useT();
  const language = useUiStore(state => state.language);
  const [items, setItems] = useState<MarketplaceItem[]>([]);
  const [sources, setSources] = useState<MarketplaceSource[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<MarketplaceItemKind | 'all'>('all');
  const [sourceMode, setSourceMode] = useState('metis-official');
  const [sourceInput, setSourceInput] = useState('');
  const [sourceName, setSourceName] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [sourceManagerOpen, setSourceManagerOpen] = useState(false);
  const [manualInstallOpen, setManualInstallOpen] = useState(false);
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const requestConfirm = useUiStore(state => state.requestConfirm);

  const loadSources = async () => {
    const next = await getMarketplaceSources();
    setSources(next);
    return next;
  };

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const catalog = sourceMode === 'mcp-registry'
        ? await searchMarketplaceMcp(marketplaceRegistrySearchQuery(query, language))
        : await getMarketplaceCatalog({ query, kind, source: sourceMode });
      setItems(catalog.items);
      setSelectedId(current => (current && catalog.items.some(item => item.id === current) ? current : ''));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadSources().catch(err => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 220);
    return () => window.clearTimeout(timer);
  }, [sourceMode, kind, query, catalogRevision]);

  const selected = items.find(item => item.id === selectedId) || null;
  const variables = useMemo(() => marketplaceVariables(selected), [selected]);
  const installed = items.filter(item => item.installed);
  const groupedItems = useMemo(() => {
    const groups = new Map<string, MarketplaceItem[]>();
    for (const item of items) {
      const category = marketplaceCategoryLabel(item.category || marketplaceKindLabel(item.kind, language), language);
      groups.set(category, [...(groups.get(category) || []), item]);
    }
    return [...groups.entries()];
  }, [items, language]);

  const refreshItem = async (next: MarketplaceItem) => {
    setItems(current => current.map(item => (item.id === next.id ? next : item)));
    setSelectedId(next.id);
    await load();
  };

  const runAction = async (action: 'install' | 'enable' | 'disable' | 'uninstall') => {
    if (!selected) return;
    if (action === 'uninstall') {
      const confirmed = await requestConfirm({
        title: t('卸载扩展？'),
        message: selected.name,
        details: selected.kind === 'plugin' ? t('会同时清理由该 Plugin 独占安装的 Skills 和 MCP。') : t('会删除本机安装内容和状态。'),
        confirmLabel: t('卸载'),
        cancelLabel: t('取消'),
        tone: 'danger',
        icon: 'trash',
      });
      if (!confirmed) return;
    }
    setBusy(action);
    setError('');
    setNotice('');
    try {
      const next = action === 'install'
        ? await installMarketplaceItem(selected.id)
        : action === 'uninstall'
          ? await uninstallMarketplaceItem(selected.id)
          : await setMarketplaceItemEnabled(selected.id, action === 'enable');
      if (action === 'uninstall') await window.metis.extensionSecretsDelete(selected.id);
      setNotice(action === 'install' ? t('已安装，默认保持停用。') : action === 'enable' ? t('扩展已启用。') : action === 'disable' ? t('扩展已停用。') : t('扩展已卸载。'));
      await refreshItem(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const saveConfiguration = async () => {
    if (!selected) return;
    setBusy('configure');
    setError('');
    setNotice('');
    try {
      const publicValues: Record<string, unknown> = {};
      const secrets: Record<string, string> = {};
      for (const row of variables) {
        const value = values[marketplaceVariableKey(row.componentId, row.variable.name)] ?? row.variable.default;
        if (row.variable.secret) {
          if (value) secrets[row.variable.name] = value;
        } else if (selected.kind === 'plugin') {
          const componentValues = (publicValues[row.componentId] as Record<string, string> | undefined) || {};
          componentValues[row.variable.name] = value || '';
          publicValues[row.componentId] = componentValues;
        } else {
          publicValues[row.variable.name] = value || '';
        }
      }
      if (Object.keys(secrets).length > 0) {
        const result = await window.metis.extensionSecretsSave(selected.id, secrets);
        if (!result.ok) throw new Error(result.error || t('无法加密保存 MCP 密钥'));
      }
      const next = await configureMarketplaceItem(selected.id, publicValues, Object.keys(secrets));
      await refreshItem(next);
      setValues({});
      if (Object.keys(secrets).length > 0) {
        setNotice(t('配置已加密保存。后端将重启以注入密钥，扩展仍保持停用。'));
        void window.metis.retryBackend();
      } else {
        setNotice(t('配置已保存，扩展仍保持停用。'));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const installSource = async () => {
    const source = sourceInput.trim();
    if (!source) return;
    setBusy('source');
    setError('');
    try {
      const next = await installMarketplaceSource(source);
      setSourceInput('');
      setSourceMode('metis-official');
      setNotice(t('自定义扩展已安装并保持停用。'));
      await refreshItem(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const selectSource = async (nextMode: string) => {
    setNotice('');
    setError('');
    setSelectedId('');
    setSourceMode(nextMode);
    if (nextMode === 'mcp-registry') {
      setKind('mcp');
      return;
    }
    const source = sources.find(row => row.id === nextMode);
    if (source && source.itemCount === 0 && !busy) {
      setBusy(`refresh:${source.id}`);
      try {
        await refreshMarketplaceSource(source.id);
        await loadSources();
        setCatalogRevision(value => value + 1);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy('');
      }
    }
  };

  const refreshCurrent = async () => {
    const source = sources.find(row => row.id === sourceMode);
    if (source) {
      setBusy(`refresh:${source.id}`);
      setError('');
      try {
        await refreshMarketplaceSource(source.id);
        await loadSources();
        setCatalogRevision(value => value + 1);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy('');
      }
    }
    await load();
  };

  const addSource = async () => {
    if (!sourceUrl.trim()) return;
    setBusy('add-source');
    setError('');
    try {
      const next = await addMarketplaceSource(sourceName.trim(), sourceUrl.trim());
      setSourceName('');
      setSourceUrl('');
      await refreshMarketplaceSource(next.id);
      await loadSources();
      await selectSource(next.id);
      setNotice(t('市场来源已添加。'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const removeSource = async (source: MarketplaceSource) => {
    const confirmed = await requestConfirm({
      title: t('移除市场来源？'),
      message: source.name,
      details: t('已安装的扩展不会被删除，只会移除该在线目录。'),
      confirmLabel: t('移除'),
      cancelLabel: t('取消'),
      tone: 'danger',
      icon: 'trash',
    });
    if (!confirmed) return;
    setBusy(`delete-source:${source.id}`);
    try {
      await deleteMarketplaceSource(source.id);
      if (sourceMode === source.id) setSourceMode('metis-official');
      await loadSources();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  return (
    <section className="zone-panel store-panel" data-zone="store">
      <ZoneHeader icon={Store} title={t('扩展市场')} eyebrow={t('扩展')} status={loading ? t('加载中') : `${items.length} ${t('项')}`} ok={!error} onRefresh={refreshCurrent} />
      {error && <InlineError message={error} onRetry={load} />}
      {notice && <p className="store-notice">{notice}</p>}
      <div className="store-workbench">
        <section className="store-browser">
          <header className="store-toolbar">
            <div className="store-source-bar">
              <div className="store-source-switch">
                <button type="button" data-active={sourceMode === 'metis-official'} onClick={() => void selectSource('metis-official')}>
                  <MarketplaceSourceLogo kind="metis" name="Metis" color="#79A5EE" />
                  <span>{t('Metis 官方')}</span>
                </button>
                {sources.map(source => (
                  <button type="button" data-active={sourceMode === source.id} key={source.id} onClick={() => void selectSource(source.id)}>
                    <MarketplaceSourceLogo kind={marketplaceSourceLogoKind(source)} name={source.name} color={source.brandColor} />
                    <span>{source.name}</span>
                    {busy === `refresh:${source.id}` && <RefreshCw className="spin" size={11} />}
                  </button>
                ))}
                <button type="button" data-active={sourceMode === 'mcp-registry'} onClick={() => void selectSource('mcp-registry')}>
                  <MarketplaceSourceLogo kind="mcp" name="MCP" color="#7C3AED" />
                  <span>{t('MCP 注册中心')}</span>
                </button>
              </div>
              <button className="store-source-manage-button" type="button" data-active={sourceManagerOpen} onClick={() => setSourceManagerOpen(value => !value)}>
                <Plus size={13} />{t('来源')}
              </button>
            </div>
            <label className="store-search">
              <Search size={14} />
              <input value={query} placeholder={sourceMode === 'mcp-registry' ? t('搜索 MCP 注册中心') : t('搜索技能、MCP、插件')} onChange={event => setQuery(event.target.value)} />
            </label>
            {sourceMode !== 'mcp-registry' && (
              <div className="store-kind-switch">
                {(['all', 'skill', 'mcp', 'plugin'] as const).map(value => (
                  <button type="button" data-active={kind === value} key={value} onClick={() => setKind(value)}>{value === 'all' ? t('全部') : value === 'skill' ? t('技能') : value === 'plugin' ? t('插件') : 'MCP'}</button>
                ))}
              </div>
            )}
          </header>
          {sourceManagerOpen && (
            <section className="store-source-manager">
              <header>
                <div><ShieldCheck size={16} /><span><strong>{t('市场来源')}</strong><em>{t('官方来源经过标记；社区来源安装后默认停用。')}</em></span></div>
                <button type="button" onClick={() => setSourceManagerOpen(false)} aria-label={t('关闭')}><X size={14} /></button>
              </header>
              <div className="store-source-list">
                {sources.map(source => (
                  <article key={source.id}>
                    <MarketplaceSourceLogo kind={marketplaceSourceLogoKind(source)} name={source.name} color={source.brandColor} large />
                    <span><strong>{source.name}</strong><em>{source.itemCount} {t('项')} · {source.revision ? source.revision.slice(0, 7) : t('尚未刷新')}</em></span>
                    <i data-trust={source.trust}>{source.trust === 'official' ? t('官方') : t('社区')}</i>
                    <button type="button" disabled={Boolean(busy)} onClick={() => void (async () => { setBusy(`refresh:${source.id}`); try { await refreshMarketplaceSource(source.id); await loadSources(); if (sourceMode === source.id) await load(); } catch (err) { setError(err instanceof Error ? err.message : String(err)); } finally { setBusy(''); } })()}><RefreshCw size={13} /></button>
                    {!source.builtin && <button className="danger" type="button" disabled={Boolean(busy)} onClick={() => void removeSource(source)}><Trash2 size={13} /></button>}
                    {source.error && <small>{source.error}</small>}
                  </article>
                ))}
              </div>
              <div className="store-source-add">
                <input value={sourceName} placeholder={t('来源名称（可选）')} onChange={event => setSourceName(event.target.value)} />
                <input value={sourceUrl} placeholder="https://github.com/org/repo 或 marketplace.json" onChange={event => setSourceUrl(event.target.value)} />
                <button type="button" disabled={!sourceUrl.trim() || busy === 'add-source'} onClick={() => void addSource()}>{busy === 'add-source' ? t('添加中') : t('添加')}</button>
              </div>
            </section>
          )}
          {installed.length > 0 && (
            <div className="store-installed-strip">
              <strong>{t('已安装')}</strong>
              {installed.map(item => <button type="button" title={item.name} key={item.id} onClick={() => setSelectedId(item.id)}><MarketplaceLogo item={item} /></button>)}
            </div>
          )}
          <div className="store-catalog-scroll">
            {!loading && items.length === 0 && (
              <div className="store-empty">
                <Store size={20} />
                <strong>{t('这里还没有扩展')}</strong>
                <span>{sources.find(source => source.id === sourceMode)?.itemCount === 0 ? t('点击刷新获取这个来源的最新目录。') : t('换个关键词或筛选条件。')}</span>
              </div>
            )}
            {groupedItems.map(([category, categoryItems]) => (
              <section className="store-category" key={category}>
                <header><strong>{category}</strong><span>{categoryItems.length}</span></header>
                <div className="store-item-grid">
                  {categoryItems.map(item => (
                    <button className="store-item-card" type="button" data-active={selected?.id === item.id} key={item.id} onClick={() => setSelectedId(item.id)}>
                      <MarketplaceLogo item={item} />
                      <span className="store-item-copy">
                        <span><strong>{marketplaceItemName(item, language)}</strong><i>{marketplaceKindLabel(item.kind, language)}</i></span>
                        <small>{item.publisher}{item.marketplaceName ? ` · ${item.marketplaceName}` : ''}</small>
                        <em>{marketplaceItemDescription(item, language)}</em>
                      </span>
                      <span className="store-item-status" data-state={item.enabled ? 'enabled' : item.installed ? item.needsSetup ? 'setup' : 'installed' : 'available'}>
                        {item.enabled ? t('已启用') : item.installed ? item.needsSetup ? t('待配置') : t('已安装') : t('查看')}
                        <ChevronRight size={13} />
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
          <section className="store-install-panel" data-open={manualInstallOpen}>
            <button type="button" onClick={() => setManualInstallOpen(value => !value)}>
              <PackagePlus size={15} /><span><strong>{t('从 URL 或本地文件安装')}</strong><em>{t('支持目录、ZIP、Git 仓库和 Codex Plugin 包')}</em></span><ChevronDown size={14} />
            </button>
            {manualInstallOpen && (
              <div className="store-url-row">
                <input value={sourceInput} placeholder="https://github.com/org/repo.git or D:\\plugins\\my-plugin" onChange={event => setSourceInput(event.target.value)} />
                <button type="button" disabled={!sourceInput.trim() || busy === 'source'} onClick={() => void installSource()}>{busy === 'source' ? t('安装中') : t('安装')}</button>
              </div>
            )}
          </section>
        </section>
        {selected && (
          <div className="store-detail-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSelectedId(''); }}>
            <aside className="store-detail-panel" aria-label={selected.name}>
              <div className="store-detail-scroll">
                <header className="store-detail-header">
                  <MarketplaceLogo item={selected} large />
                  <div><strong>{marketplaceItemName(selected, language)}</strong><span>{selected.publisher} · v{selected.version || '0.0.0'}</span></div>
                  <button type="button" onClick={() => setSelectedId('')} aria-label={t('关闭')}><X size={16} /></button>
                </header>
                <div className="store-detail-badges">
                  <i>{marketplaceKindLabel(selected.kind, language)}</i>
                  <i data-trust={selected.trust}>{selected.trust === 'official' ? t('官方来源') : selected.marketplaceName || t('自定义来源')}</i>
                </div>
                <p className="store-detail-description">{marketplaceItemDescription(selected, language)}</p>
                <dl className="store-detail-meta">
                  <div><dt>{t('状态')}</dt><dd>{selected.enabled ? t('已启用') : selected.installed ? selected.needsSetup ? t('待配置') : t('已安装 · 已停用') : t('未安装')}</dd></div>
                  <div><dt>{t('来源')}</dt><dd>{marketplaceItemSourceLabel(selected, language)}</dd></div>
                  {selected.license && <div><dt>{t('许可证')}</dt><dd>{selected.license}</dd></div>}
                  {selected.revision && <div><dt>{t('提交版本')}</dt><dd title={selected.revision}>{selected.revision.slice(0, 12)}</dd></div>}
                </dl>
                {selected.sourceUrl && <a className="store-source-link" href={selected.sourceUrl} target="_blank" rel="noreferrer"><ExternalLink size={13} />{t('查看源仓库')}</a>}
                <section className="store-detail-content">
                  <header><strong>{t('介绍')}</strong><span>{selected.kind === 'plugin' ? t('包含的 Skills 与使用说明') : t('Skill 使用说明')}</span></header>
                  {language === 'zh' ? (
                    <>
                      <p className="store-detail-localized-summary">{marketplaceItemDescription(selected, language)}</p>
                      {selected.content && selected.content !== selected.description && (
                        <details className="store-detail-original">
                          <summary>{t('查看英文原始说明')}</summary>
                          <MarkdownText text={selected.content} />
                        </details>
                      )}
                    </>
                  ) : <MarkdownText text={selected.content || selected.description} />}
                </section>
                {selected.components.length > 0 && (
                  <section className="store-components"><span>{t('包含')}</span>{selected.components.map(component => <p key={component.id}><MarketplaceLogo item={component} /><strong>{marketplaceItemName(component, language)}</strong><em>{marketplaceKindLabel(component.kind, language)}</em></p>)}</section>
                )}
                {selected.installed && variables.length > 0 && (
                  <section className="store-config-form">
                    <header><KeyRound size={14} /><strong>{t('配置')}</strong><span>{t('密钥由系统加密存储，不写入 mcp.json')}</span></header>
                    {variables.map(row => {
                      const key = marketplaceVariableKey(row.componentId, row.variable.name);
                      const configured = selected.configuredEnv.includes(row.variable.name) || selected.components.some(component => component.configuredEnv.includes(row.variable.name));
                      return (
                        <label key={key}>
                          <span>{row.variable.name}{row.variable.required ? ' *' : ''}</span>
                          <input type={row.variable.secret ? 'password' : 'text'} value={values[key] || ''} placeholder={configured ? t('已配置，留空保持不变') : row.variable.default || row.variable.description} onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))} />
                        </label>
                      );
                    })}
                    <button type="button" disabled={busy === 'configure'} onClick={() => void saveConfiguration()}>{busy === 'configure' ? t('保存中') : t('保存配置')}</button>
                  </section>
                )}
                {selected.error && <p className="store-item-error">{selected.error}</p>}
              </div>
              <footer className="store-detail-actions">
                <button className="secondary" type="button" disabled={Boolean(busy)} onClick={() => setSelectedId('')}>{t('取消')}</button>
                {!selected.installed ? <button className="primary" type="button" disabled={Boolean(busy)} onClick={() => void runAction('install')}>{busy === 'install' ? t('安装中') : t('安装')}</button> : (
                  <>
                    <button className="primary" type="button" disabled={Boolean(busy) || selected.needsSetup} onClick={() => void runAction(selected.enabled ? 'disable' : 'enable')}>{selected.enabled ? t('停用') : t('启用')}</button>
                    <button className="danger" type="button" disabled={Boolean(busy)} onClick={() => void runAction('uninstall')}><Trash2 size={13} />{t('卸载')}</button>
                  </>
                )}
              </footer>
            </aside>
          </div>
        )}
      </div>
    </section>
  );
}

function MarketplaceLogo({ item, large = false }: { item: MarketplaceItem; large?: boolean }) {
  const initials = marketplaceInitials(item.name);
  const sourceLogo = marketplaceItemSourceLogoKind(item);
  return (
    <span className="marketplace-logo" data-large={large} data-generated={!item.iconDataUrl && !sourceLogo} data-source-logo={Boolean(sourceLogo)} style={{ '--marketplace-brand': item.brandColor } as CSSProperties}>
      {item.iconDataUrl ? <img src={item.iconDataUrl} alt="" /> : sourceLogo ? <MarketplaceSourceLogo kind={sourceLogo} name={item.publisher || item.name} color={item.brandColor} large /> : <strong>{initials}</strong>}
    </span>
  );
}

type MarketplaceSourceLogoKind = 'metis' | 'openai' | 'anthropic' | 'mcp' | 'custom';

function MarketplaceSourceLogo({ kind, name, color, large = false }: { kind: MarketplaceSourceLogoKind; name: string; color: string; large?: boolean }) {
  return (
    <span className="marketplace-source-logo" data-source={kind} data-large={large} style={{ '--marketplace-brand': color } as CSSProperties}>
      {kind === 'metis' && <img src={metisMark} alt="" />}
      {kind === 'openai' && <svg role="img" viewBox="0 0 24 24" aria-label="OpenAI"><path d="M9.205 8.658v-2.26c0-.19.072-.333.238-.428l4.543-2.616c.619-.357 1.356-.523 2.117-.523 2.854 0 4.662 2.212 4.662 4.566 0 .167 0 .357-.024.547l-4.71-2.759a.797.797 0 0 0-.856 0l-5.97 3.473Zm10.609 8.8V12.06c0-.333-.143-.57-.429-.737l-5.97-3.473 1.95-1.118a.433.433 0 0 1 .476 0l4.543 2.617c1.309.76 2.189 2.378 2.189 3.948 0 1.808-1.07 3.473-2.76 4.163ZM7.802 12.703l-1.95-1.142c-.167-.095-.239-.238-.239-.428V5.899c0-2.545 1.95-4.472 4.591-4.472 1 0 1.927.333 2.712.928L8.23 5.067c-.285.166-.428.404-.428.737v6.898ZM12 15.128l-2.795-1.57v-3.33L12 8.658l2.795 1.57v3.33L12 15.128Zm1.796 7.23c-1 0-1.927-.332-2.712-.927l4.686-2.712c.285-.166.428-.404.428-.737v-6.898l1.974 1.142c.167.095.238.238.238.428v5.233c0 2.545-1.974 4.472-4.614 4.472Zm-5.637-5.303-4.544-2.617c-1.308-.761-2.188-2.378-2.188-3.948A4.482 4.482 0 0 1 4.21 6.327v5.423c0 .333.143.571.428.738l5.947 3.449-1.95 1.118a.432.432 0 0 1-.476 0Zm-.262 3.9c-2.688 0-4.662-2.021-4.662-4.519 0-.19.024-.38.047-.57l4.686 2.71c.286.167.571.167.856 0l5.97-3.448v2.26c0 .19-.07.333-.237.428l-4.543 2.616c-.619.357-1.356.523-2.117.523Zm5.899 2.83a5.947 5.947 0 0 0 5.827-4.756C22.287 18.339 24 15.84 24 13.296c0-1.665-.713-3.282-1.998-4.448.119-.5.19-.999.19-1.498 0-3.401-2.759-5.947-5.946-5.947-.642 0-1.26.095-1.88.31A5.962 5.962 0 0 0 10.205 0a5.947 5.947 0 0 0-5.827 4.757C1.713 5.447 0 7.945 0 10.49c0 1.666.713 3.283 1.998 4.448-.119.5-.19 1-.19 1.499 0 3.401 2.759 5.946 5.946 5.946.642 0 1.26-.095 1.88-.309a5.96 5.96 0 0 0 4.162 1.713Z" /></svg>}
      {kind === 'anthropic' && <svg role="img" viewBox="0 0 24 24" aria-label="Anthropic"><path d="M13.827 3.52h3.603L24 20h-3.603l-6.57-16.48Zm-7.258 0h3.767L16.906 20h-3.674l-1.343-3.461H5.017l-1.344 3.46H0L6.57 3.522Zm4.132 9.959L8.453 7.687 6.205 13.48H10.7Z" /></svg>}
      {kind === 'mcp' && <svg role="img" viewBox="0 0 24 24" aria-label="Model Context Protocol"><path d="M13.85 0a4.16 4.16 0 0 0-2.95 1.217L1.456 10.66a.835.835 0 0 0 1.18 1.18l9.442-9.442a2.49 2.49 0 0 1 3.541 3.541L8.59 12.97l-.1.1a.835.835 0 0 0 1.18 1.18l.1-.098 7.03-7.034a2.49 2.49 0 0 1 3.542 0l.049.05a2.49 2.49 0 0 1 0 3.54l-8.54 8.54a1.96 1.96 0 0 0 0 2.755l1.753 1.753a.835.835 0 0 0 1.18-1.18l-1.753-1.753a.266.266 0 0 1 0-.394l8.54-8.54a4.185 4.185 0 0 0 0-5.9l-.05-.05a4.16 4.16 0 0 0-3.55-1.17 4.17 4.17 0 0 0-1.17-3.552A4.16 4.16 0 0 0 13.85 0Zm0 3.333a.84.84 0 0 0-.59.245L6.275 10.56a4.186 4.186 0 0 0 5.902 5.902L19.16 9.48a.835.835 0 0 0-1.18-1.18l-6.985 6.984a2.49 2.49 0 0 1-3.54-3.54l6.983-6.985a.835.835 0 0 0-.59-1.425Z" /></svg>}
      {kind === 'custom' && <strong>{marketplaceInitials(name)}</strong>}
    </span>
  );
}

function marketplaceSourceLogoKind(source: MarketplaceSource): MarketplaceSourceLogoKind {
  if (source.id === 'openai-plugins') return 'openai';
  if (source.id === 'anthropic-skills') return 'anthropic';
  return 'custom';
}

function marketplaceItemSourceLogoKind(item: MarketplaceItem): MarketplaceSourceLogoKind | null {
  if (item.marketplaceSource === 'openai-plugins') return 'openai';
  if (item.marketplaceSource === 'anthropic-skills') return 'anthropic';
  if (item.sourceType === 'registry' || item.category === 'MCP Registry') return 'mcp';
  return null;
}

function marketplaceInitials(value: string): string {
  const words = String(value || '?').trim().split(/[\s._/-]+/).filter(Boolean);
  if (words.length > 1) return `${words[0][0]}${words[1][0]}`.toUpperCase();
  return (words[0] || '?').slice(0, 2).toUpperCase();
}

function marketplaceKindLabel(kind: MarketplaceItemKind, language: string): string {
  if (language !== 'zh') return kind === 'skill' ? 'Skill' : kind === 'mcp' ? 'MCP' : 'Plugin';
  return kind === 'skill' ? '技能' : kind === 'mcp' ? 'MCP' : '插件';
}

const MARKETPLACE_CATEGORY_ZH: Record<string, string> = {
  'Productivity': '生产力',
  'Developer Tools': '开发工具',
  'Business & Operations': '商务与运营',
  'Creativity': '创意',
  'Education & Research': '教育与研究',
  'Other': '其他',
  'Security': '安全',
  'Travel': '旅行',
  'Example Skills': '示例技能',
  'Document Skills': '文档技能',
  'Agent Skills': '智能体技能',
  'MCP Registry': 'MCP 注册中心',
  'Plugins': '插件',
  'Plugin': '插件',
  'Claude Api': 'Claude API',
  'Claude API': 'Claude API',
  'Business': '商务',
  'Communication': '沟通协作',
  'Data & Analytics': '数据与分析',
  'Design': '设计',
  'Education': '教育',
  'Finance': '金融',
  'Lifestyle': '生活方式',
  'Research': '研究',
  'Sales': '销售',
};

const ANTHROPIC_ITEM_ZH: Record<string, { name: string; description: string }> = {
  'anthropic:xlsx': { name: '电子表格', description: '创建、读取、编辑和修复 XLSX、XLSM、CSV、TSV 等电子表格，支持公式、格式、图表和数据清洗。' },
  'anthropic:docx': { name: 'Word 文档', description: '创建、读取和编辑 DOCX 文档，支持目录、页码、页眉页脚、图片、批注和修订。' },
  'anthropic:pptx': { name: '演示文稿', description: '创建、读取和修改 PowerPoint 演示文稿，支持模板、版式、演讲者备注以及幻灯片合并拆分。' },
  'anthropic:pdf': { name: 'PDF 文档', description: '读取、创建、合并、拆分和旋转 PDF，支持表单、水印、图片提取以及扫描件 OCR。' },
  'anthropic:algorithmic-art': { name: '算法艺术', description: '使用 p5.js、可复现随机数和交互参数创作原创生成式艺术、流场与粒子效果。' },
  'anthropic:brand-guidelines': { name: '品牌规范', description: '将 Anthropic 官方品牌颜色、字体和视觉规范应用到文档、页面与其他设计产物。' },
  'anthropic:canvas-design': { name: '画布设计', description: '依据设计原则创作海报、艺术作品和其他 PNG、PDF 静态视觉内容。' },
  'anthropic:doc-coauthoring': { name: '文档协作', description: '通过结构化流程共同编写文档、提案、技术规范和决策记录，并迭代验证可读性。' },
  'anthropic:frontend-design': { name: '前端设计', description: '为新界面或现有界面重构提供明确的视觉方向、字体体系和非模板化设计指导。' },
  'anthropic:internal-comms': { name: '内部沟通', description: '编写状态报告、管理层更新、公司简报、FAQ、事故报告和项目进展等内部沟通材料。' },
  'anthropic:mcp-builder': { name: 'MCP 构建器', description: '使用 Python FastMCP 或 Node/TypeScript MCP SDK 构建高质量 MCP 服务并连接外部 API。' },
  'anthropic:skill-creator': { name: 'Skill 创建器', description: '创建、修改和优化 Skill，并通过评测、基准测试和方差分析衡量触发与执行效果。' },
  'anthropic:slack-gif-creator': { name: 'Slack GIF 制作', description: '按照 Slack 的尺寸和性能约束创建、验证和优化动画 GIF。' },
  'anthropic:theme-factory': { name: '主题工厂', description: '为幻灯片、文档、报告和网页应用预设或即时生成配色、字体与视觉主题。' },
  'anthropic:web-artifacts-builder': { name: 'Web 作品构建器', description: '使用 React、Tailwind CSS 和 shadcn/ui 创建包含状态管理与路由的复杂 Web 交互作品。' },
  'anthropic:webapp-testing': { name: 'Web 应用测试', description: '使用 Playwright 测试本地 Web 应用，验证功能、调试界面、截取页面并查看浏览器日志。' },
  'anthropic:claude-api': { name: 'Claude API', description: '提供 Claude API、模型选择、缓存、工具调用、流式输出和智能体开发方面的官方指导。' },
};

function marketplaceCategoryLabel(value: string, language: string): string {
  if (language !== 'zh') return value;
  return MARKETPLACE_CATEGORY_ZH[value] || value;
}

function marketplaceItemName(item: MarketplaceItem, language: string): string {
  if (language !== 'zh') return item.name;
  return ANTHROPIC_ITEM_ZH[item.id]?.name || item.name;
}

function marketplaceItemSourceLabel(item: MarketplaceItem, language: string): string {
  if (item.marketplaceName) return item.marketplaceName;
  if (language === 'zh' && item.sourceType === 'registry') return 'MCP 注册中心';
  if (language === 'zh' && item.sourceType === 'builtin') return 'Metis 官方';
  return item.sourceType || 'Metis';
}

function marketplaceVariables(item: MarketplaceItem | null): Array<{ componentId: string; variable: MarketplaceEnvironmentVariable }> {
  if (!item) return [];
  if (item.mcp) return item.mcp.environmentVariables.map(variable => ({ componentId: item.id, variable }));
  return item.components.flatMap(component => component.mcp?.environmentVariables.map(variable => ({ componentId: component.id, variable })) || []);
}

function marketplaceVariableKey(componentId: string, name: string): string {
  return `${componentId}:${name}`;
}

function ComputerPanel() {
  const t = useT();
  const [status, setStatus] = useState<DeskStatusPayload | null>(null);
  const [capabilities, setCapabilities] = useState<ModelCapabilities | null>(null);
  const [log, setLog] = useState<DeskGoalLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const [nextStatus, nextLog, settings] = await Promise.all([getDeskStatus(), getDeskGoalLog(12), getSettings()]);
      setStatus(nextStatus);
      setLog(nextLog);
      try {
        setCapabilities(await getModelCapabilities(settings));
      } catch (capabilityError) {
        setCapabilities(null);
        setError(capabilityError instanceof Error ? capabilityError.message : String(capabilityError));
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const runAction = async (name: 'enable' | 'pause' | 'resume') => {
    if (!status) return;
    setBusy(name);
    setError('');
    try {
      if (name === 'enable') {
        if (!status.enabled) {
          const settings = await getSettings();
          const nextCapabilities = await getModelCapabilities(settings);
          setCapabilities(nextCapabilities);
          if (!nextCapabilities.supportsVision) {
            setError(`${nextCapabilities.model || t('当前模型')}${t(' 不支持视觉功能，无法启用桌面操控。')}`);
            return;
          }
        }
        await setDeskEnabled(!status.enabled);
      }
      if (name === 'pause') await pauseDeskAutomation();
      if (name === 'resume') await resumeDeskAutomation();
      await load();
    } finally {
      setBusy('');
    }
  };

  const statusText = loading ? t('加载中') : status?.available ? (status.enabled ? t('已启用') : t('已关闭')) : t('不可用');
  const progress = useMemo(() => {
    if (!status?.visionMaxSteps) return '0/0';
    return `${status.visionStep}/${status.visionMaxSteps}`;
  }, [status]);
  const meaningfulLog = useMemo(
    () =>
      log.filter(entry => {
        const action = String(entry.action || '').trim();
        const statusValue = String(entry.status || '').trim();
        const detail = String(entry.detail || '').trim();
        if (detail && detail !== '-') return true;
        if (statusValue && statusValue !== '-' && statusValue !== 'event') return true;
        return Boolean(action && action !== '-' && action !== 'event');
      }),
    [log],
  );

  return (
    <section className="zone-panel" data-zone="computer">
      <ZoneHeader icon={Cpu} title={t('操控')} eyebrow="Computer" status={statusText} ok={Boolean(status?.available)} onRefresh={load} />
      {status && !status.available && <InlineError message={status.error || t('桌面自动化模块不可用')} onRetry={load} />}
      {error && <InlineError message={error} onRetry={load} />}
      <div className="zone-metrics">
        <Metric label={t('安全总开关')} value={status?.enabled ? t('开') : t('关')} />
        <Metric label={t('执行模式')} value={status?.execMode || '-'} />
        <Metric label={t('视觉步骤')} value={progress} />
        <Metric label={t('视觉模型')} value={capabilities?.supportsVision ? t('支持') : t('不支持')} />
      </div>
      <div className="computer-grid">
        <article className="control-panel">
          <header>
            <Shield size={16} />
            <strong>{t('安全控制')}</strong>
          </header>
          <div className="control-actions">
            <button type="button" disabled={busy === 'enable' || !status?.available} onClick={() => void runAction('enable')}>
              <Power size={14} />
              {status?.enabled ? t('关闭操控') : t('启用操控')}
            </button>
            <button type="button" disabled={busy === 'pause' || !status?.available || status?.paused} onClick={() => void runAction('pause')}>
              <PauseCircle size={14} />
              {t('暂停')}
            </button>
            <button type="button" disabled={busy === 'resume' || !status?.available || !status?.paused} onClick={() => void runAction('resume')}>
              <PlayCircle size={14} />
              {t('恢复')}
            </button>
          </div>
          <dl>
            <dt>Human core</dt>
            <dd>{status?.humanCore || '-'}</dd>
            <dt>Goal</dt>
            <dd>{status?.goal || 'idle'}</dd>
            <dt>Vision</dt>
            <dd>{status?.visionStatus || 'idle'}</dd>
            <dt>Model</dt>
            <dd>{capabilities?.model || '-'}</dd>
          </dl>
        </article>
        <article className="control-panel">
          <header>
            <Wrench size={16} />
            <strong>{t('运行状态')}</strong>
          </header>
          <div className="state-stack">
            <StateLine label="Goal runner" active={Boolean(status?.goalRunning)} value={status?.goalStatus || 'idle'} />
            <StateLine label="Vision loop" active={Boolean(status?.visionRunning)} value={status?.visionGoal || status?.visionStatus || 'idle'} />
            <StateLine label="Paused" active={Boolean(status?.paused)} value={status?.paused ? 'true' : 'false'} />
          </div>
        </article>
        {meaningfulLog.length > 0 && (
          <article className="control-panel log-panel">
            <header>
              <FileText size={16} />
              <strong>{t('最近日志')}</strong>
            </header>
            {meaningfulLog.map((entry, index) => (
              <p key={`${entry.ts}-${entry.action}-${index}`}>
                <span>{entry.action || entry.status || 'event'}</span>
                <small>{entry.detail || entry.status || '-'}</small>
              </p>
            ))}
          </article>
        )}
      </div>
    </section>
  );
}

function ZoneHeader({
  icon: Icon,
  title,
  eyebrow,
  status,
  ok = true,
  onRefresh,
}: {
  icon: typeof Bot;
  title: string;
  eyebrow: string;
  status: string;
  ok?: boolean;
  onRefresh: () => Promise<void>;
}) {
  const t = useT();
  return (
    <header className="zone-header">
      <div>
        <Icon size={22} />
        <span>
          <em>{eyebrow}</em>
          <strong>{title}</strong>
        </span>
      </div>
      <div className="zone-header-actions">
        <StatusPill ok={ok} text={status} />
        <button className="zone-icon-button" type="button" onClick={() => void onRefresh()}>
          <RefreshCw size={14} />
          <span>{t('刷新')}</span>
        </button>
      </div>
    </header>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="zone-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function InlineError({ message, onRetry }: { message: string; onRetry: () => Promise<void> }) {
  const t = useT();
  return (
    <div className="zone-error">
      <AlertTriangle size={15} />
      <span>{message}</span>
      <button type="button" onClick={() => void onRetry()}>
        {t('重试')}
      </button>
    </div>
  );
}

function StatusPill({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span className="zone-pill" data-ok={ok}>
      {!ok && <AlertTriangle size={12} />}
      {text}
    </span>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className="status-dot" data-ok={ok} />;
}

function StateLine({ label, active, value }: { label: string; active: boolean; value: string }) {
  return (
    <p className="state-line">
      <StatusDot ok={active} />
      <span>{label}</span>
      <strong>{value}</strong>
    </p>
  );
}
