import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Cpu,
  FileText,
  FolderOpen,
  Network,
  PauseCircle,
  PlayCircle,
  PlugZap,
  Power,
  RefreshCw,
  Save,
  Shield,
  Trash2,
  Unplug,
  UploadCloud,
  Wrench,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import {
  deleteSkill,
  disconnectMcpServer,
  getDeskGoalLog,
  getModelCapabilities,
  getDeskStatus,
  getMcpStatus,
  getSettings,
  getSkill,
  getSkills,
  importSkill,
  openSkillFolder,
  pauseDeskAutomation,
  reconnectMcpServer,
  reloadMcpServers,
  resumeDeskAutomation,
  saveSkill,
  setDeskEnabled,
  setSkillEnabled,
} from '../../lib/api';
import type { DeskGoalLogEntry, DeskStatusPayload, McpStatusPayload, ModelCapabilities, SectionId, SkillDetail, SkillSummary } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';

type ZoneSection = Exclude<SectionId, 'chat' | 'cron'>;

export function SectionMain({ section }: { section: ZoneSection }) {
  if (section === 'skills') return <SkillsPanel />;
  if (section === 'mcp') return <McpPanel />;
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
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingSkill('');
    }
  };

  const enabledCount = skills.filter(skill => skill.enabled).length;
  const groupedSkills = useMemo(
    () => [
      { source: 'builtin', title: t('内置技能'), items: skills.filter(skill => skill.source === 'builtin') },
      { source: 'global', title: t('全局技能'), items: skills.filter(skill => skill.source === 'global') },
      { source: 'project', title: t('项目技能'), items: skills.filter(skill => skill.source === 'project') },
    ],
    [skills, t],
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

  return (
    <section className="zone-panel" data-zone="skills">
      <ZoneHeader
        icon={Bot}
        title={t('技能')}
        eyebrow="Skills"
        status={loading ? t('加载中') : error ? t('读取失败') : `${skills.length} ${t('个技能')}`}
        ok={!error}
        onRefresh={load}
      />
      {error && <InlineError message={error} onRetry={load} />}
      <div className="learning-strip">
        <div>
          <strong>{t('自学习')}</strong>
          <span>{t('复杂任务完成后，Metis 会把可复用经验沉淀到本地记忆和技能。')}</span>
        </div>
        <div className="learning-strip-pills">
          <StatusPill ok={Boolean(learning?.autoMemory)} text={learning?.autoMemory ? t('自动记忆开') : t('自动记忆关')} />
          <StatusPill ok={Boolean(learning?.autoSkills)} text={learning?.autoSkills ? t('自动技能开') : t('自动技能关')} />
        </div>
      </div>
      <div className="zone-metrics">
        <Metric label={t('内置')} value={String(groupedSkills[0].items.length)} />
        <Metric label={t('全局')} value={String(groupedSkills[1].items.length)} />
        <Metric label={t('项目')} value={String(groupedSkills[2].items.length)} />
        <Metric label={t('已启用')} value={String(enabledCount)} />
      </div>
      <div className="skill-directory-note">{skillsDirectory}</div>
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
          <UploadCloud size={13} />
          {t('导入')}
        </button>
        <button type="button" disabled={busy === 'import'} onClick={() => void pickAndImport()}>
          <FolderOpen size={13} />
          {t('选择目录')}
        </button>
      </div>
      <div className="skills-workbench">
        <div className="zone-list skill-list-live">
          {!loading && skills.length === 0 && (
            <article className="zone-empty">
              <FileText size={18} />
              <span>{t('暂无本地技能')}</span>
              <small>{t('完成复杂任务后可沉淀为 SKILL.md。')}</small>
            </article>
          )}
          {groupedSkills.map(group => (
            group.items.length > 0 && (
              <div className="skill-group" data-source={group.source} key={group.source}>
                <span className="skill-group-label">{group.title}</span>
                {group.items.map(skill => (
                  <article className="zone-row skill-row" data-active={selectedSkillId === skill.id} data-source={skill.source} key={skill.id || skill.path || skill.name}>
                    <div>
                      <strong>{skill.name || 'Unnamed skill'}</strong>
                      <span>{skill.path}</span>
                      <div className="skill-badges">
                        <em>{skill.source === 'project' ? t('项目覆盖') : skill.source === 'builtin' ? t('内置') : t('全局')}</em>
                        <em>{skill.disableModelInvocation ? t('仅手动') : t('模型可触发')}</em>
                        <em>{skill.userInvocable ? `/${skill.skillName || skill.id}` : t('后台技能')}</em>
                        {skill.paths.length > 0 && <em>{skill.paths.slice(0, 2).join(', ')}</em>}
                      </div>
                      {(skill.description || skill.preview) && <p>{skill.description || skill.preview}</p>}
                    </div>
                    <div className="row-actions skill-actions">
                      <button
                        className="skill-detail-button"
                        type="button"
                        disabled={!skill.id || busy === `detail:${skill.id}`}
                        onClick={() => void openSkill(skill.id)}
                      >
                        {t('查看')}
                      </button>
                      <StatusPill ok={skill.enabled} text={skill.enabled ? t('启用') : t('停用')} />
                      <button
                        className="danger-action skill-delete-button"
                        type="button"
                        disabled={!skill.id || deletingSkill === skill.id}
                        onClick={() => void removeSkill(skill)}
                      >
                        <Trash2 size={13} />
                        {deletingSkill === skill.id ? t('删除中') : t('删除')}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )
          ))}
        </div>
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
                  <small>
                    {detail.source === 'project' ? t('项目技能') : detail.source === 'builtin' ? t('内置技能') : t('全局技能')}
                    {' · '}
                    {detail.disableModelInvocation ? t('仅手动触发') : t('模型可自动触发')}
                    {detail.userInvocable ? ` · /${detail.skillName || detail.id}` : ` · ${t('后台技能')}`}
                  </small>
                </div>
                <StatusPill ok={detail.enabled} text={detail.enabled ? t('启用') : t('停用')} />
              </header>
              <div className="skill-detail-actions">
                <button className="skill-toggle-button" type="button" disabled={busy === 'toggle'} onClick={() => void toggleCurrentSkill()}>
                  {detail.enabled ? t('停用') : t('启用')}
                </button>
                <button className="skill-open-folder-button" type="button" disabled={busy === 'open-folder'} onClick={() => void openCurrentFolder()}>
                  <FolderOpen size={13} />
                  {t('打开目录')}
                </button>
                <button className="skill-save-button" type="button" disabled={busy === 'save' || draft === detail.content} onClick={() => void saveCurrentSkill()}>
                  <Save size={13} />
                  {busy === 'save' ? t('保存中') : t('保存')}
                </button>
              </div>
              <textarea
                className="skill-editor"
                value={draft}
                spellCheck={false}
                onChange={event => setDraft(event.target.value)}
              />
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

function McpPanel() {
  const t = useT();
  const [status, setStatus] = useState<McpStatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyServer, setBusyServer] = useState('');
  const [reloadingMcp, setReloadingMcp] = useState(false);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      setStatus(await getMcpStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const connected = status?.servers.filter(server => server.connected).length ?? 0;
  const toolCount = status?.servers.reduce((sum, server) => sum + server.toolsCount, 0) ?? 0;

  const runServerAction = async (serverName: string, action: 'reconnect' | 'disconnect') => {
    setBusyServer(serverName);
    setError('');
    try {
      const result = action === 'reconnect' ? await reconnectMcpServer(serverName) : await disconnectMcpServer(serverName);
      if (result.error) setError(result.error);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyServer('');
    }
  };

  const reloadMcp = async () => {
    setReloadingMcp(true);
    setError('');
    try {
      const result = await reloadMcpServers();
      if (!result.ok) setError(result.error || 'MCP reload failed');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setReloadingMcp(false);
    }
  };

  return (
    <section className="zone-panel" data-zone="mcp">
      <ZoneHeader
        icon={Network}
        title={t('连接器')}
        eyebrow="MCP"
        status={loading ? t('加载中') : error ? t('读取失败') : status?.enabled ? `${connected}/${status.servers.length} ${t('已连接')}` : t('已禁用')}
        ok={!error && Boolean(status?.enabled)}
        onRefresh={load}
      />
      {error && <InlineError message={error} onRetry={load} />}
      <div className="zone-metrics">
        <Metric label={t('MCP 可用')} value={status?.available ? t('是') : t('否')} />
        <Metric label={t('已连接')} value={`${connected}/${status?.servers.length ?? 0}`} />
        <Metric label={t('工具')} value={String(toolCount)} />
      </div>
      <div className="settings-action-row">
        <button type="button" disabled={reloadingMcp} onClick={() => void reloadMcp()}>
          <RefreshCw size={13} />
          {reloadingMcp ? t('重载中...') : t('热重载 MCP')}
        </button>
      </div>
      <div className="zone-split">
        <div className="zone-list">
          {!loading && status?.servers.length === 0 && (
            <article className="zone-empty">
              <PlugZap size={18} />
              <span>{t('暂无 MCP server')}</span>
              <small>{t('检测到配置后会显示连接状态。')}</small>
            </article>
          )}
          {status?.servers.map(server => (
            <article className="zone-row connector-row" key={server.name}>
              <div>
                <strong>{server.name}</strong>
                <span>{server.url || [server.command, ...server.args].filter(Boolean).join(' ') || t('未配置启动方式')}</span>
                {server.tools.length > 0 && <p>{server.tools.slice(0, 4).map(tool => tool.name).join(' · ')}</p>}
                <small>
                  {server.transport || 'stdio'} · resources {server.resourcesCount}
                  {server.lastError ? ` · ${server.lastError}` : ''}
                </small>
              </div>
              <div className="row-actions">
                <StatusPill ok={server.connected && server.healthy} text={server.connected ? (server.healthy ? t('健康') : t('异常')) : t('未连接')} />
                <button type="button" disabled={busyServer === server.name} onClick={() => void runServerAction(server.name, 'reconnect')}>
                  <RefreshCw size={13} />
                  {t('重连')}
                </button>
                <button type="button" disabled={busyServer === server.name || !server.connected} onClick={() => void runServerAction(server.name, 'disconnect')}>
                  <Unplug size={13} />
                  {t('断开')}
                </button>
              </div>
            </article>
          ))}
        </div>
        <aside className="zone-side">
          <strong>{t('配置来源')}</strong>
          {(status?.configSources ?? []).length === 0 && <span>{t('未发现配置文件')}</span>}
          {status?.configSources.map(source => (
            <p key={`${source.label}-${source.path}`}>
              <StatusDot ok={source.exists} />
              <span>{source.label || 'config'}</span>
              <small>{source.path}</small>
            </p>
          ))}
        </aside>
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
