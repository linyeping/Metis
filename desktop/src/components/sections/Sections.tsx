import {
  AlertTriangle,
  Bot,
  Calendar,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  GitBranch,
  Globe2,
  HardDrive,
  KeyRound,
  Mail,
  Network,
  PackagePlus,
  PauseCircle,
  PencilLine,
  PlayCircle,
  PlugZap,
  Power,
  RefreshCw,
  Save,
  Search,
  Shield,
  Store,
  Trash2,
  Unplug,
  UploadCloud,
  Wrench,
} from 'lucide-react';
import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  connectConnector,
  deleteSkill,
  disconnectConnector,
  getDeskGoalLog,
  getModelCapabilities,
  getDeskStatus,
  getSettings,
  getSkill,
  getSkills,
  importSkill,
  listBackendConnectors,
  openSkillFolder,
  pauseDeskAutomation,
  resumeDeskAutomation,
  saveSkill,
  setDeskEnabled,
  setSkillEnabled,
  testConnector,
} from '../../lib/api';
import type { BackendConnector } from '../../lib/api';
import type { DeskGoalLogEntry, DeskStatusPayload, ModelCapabilities, SectionId, SkillDetail, SkillFileEntry, SkillSummary } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import { MarkdownText } from '../chat/threadUtils';

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
                            {expandedSkillIds.includes(skill.id) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
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
  return (
    <span className="skill-logo" data-source={skill.source} data-skill={skill.skillName || skill.id}>
      <Icon size={15} />
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
                      <ConnectorGlyph connector={connector} />
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
                  <ConnectorGlyph connector={selected} />
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

function ConnectorGlyph({ connector }: { connector: BackendConnector }) {
  const Icon = connectorIcon(connector.serviceId);
  return (
    <span className="connector-glyph" data-active={connector.active}>
      <Icon size={15} />
    </span>
  );
}

function connectorIcon(serviceId: string) {
  if (serviceId.startsWith('x_')) return Globe2;
  if (serviceId.includes('github')) return GitBranch;
  if (serviceId.includes('gmail')) return Mail;
  if (serviceId.includes('calendar')) return Calendar;
  if (serviceId.includes('drive')) return HardDrive;
  if (serviceId.includes('postgres')) return Database;
  if (serviceId.includes('filesystem')) return FolderOpen;
  if (serviceId.includes('slack') || serviceId.includes('notion')) return Globe2;
  return PlugZap;
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
  return (
    <section className="zone-panel store-panel" data-zone="store">
      <ZoneHeader icon={Store} title="Store" eyebrow="Beta" status="Beta" ok onRefresh={async () => undefined} />
      <div className="store-workbench">
        <section className="store-install-panel">
          <header>
            <PackagePlus size={18} />
            <span>
              <strong>{t('安装扩展')}</strong>
              <em>{t('暂支持 GitHub 链接，官方市场后续接入。')}</em>
            </span>
          </header>
          <div className="store-url-row">
            <input placeholder="https://github.com/org/repo/tree/main/skill-or-mcp" />
            <button type="button" disabled>{t('安装')}</button>
          </div>
        </section>
        <section className="store-category-grid">
          <article>
            <FileText size={18} />
            <strong>Skills</strong>
            <span>{t('从官方市场或 GitHub 安装可复用工作流。')}</span>
            <em>Beta</em>
          </article>
          <article>
            <Network size={18} />
            <strong>MCP</strong>
            <span>{t('安装 MCP server 并接入连接器权限。')}</span>
            <em>Beta</em>
          </article>
          <article>
            <KeyRound size={18} />
            <strong>{t('授权')}</strong>
            <span>{t('安装后在连接器页面完成本机授权。')}</span>
            <em>Beta</em>
          </article>
        </section>
      </div>
    </section>
  );
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
      {ok ? <CheckCircle2 size={12} /> : <AlertTriangle size={12} />}
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
