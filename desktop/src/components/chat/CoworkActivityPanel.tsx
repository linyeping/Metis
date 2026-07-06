import {
  AlertTriangle,
  CheckCircle2,
  FileCode,
  FileText,
  GitBranch,
  LoaderCircle,
  Network,
  PackageCheck,
  ScrollText,
  SquareTerminal,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useT } from '../../hooks/useT';
import { getWorktreeDiff, promoteWorktree } from '../../lib/api';
import type { ChatSubagentEvent, CoworkPlanSnapshot, CoworkPlanSubrun, RuntimeStatus, WorktreeDiffPayload, WorktreePromotePayload } from '../../lib/types';

type UnknownRecord = Record<string, unknown>;
type CoworkRowStatus = 'planned' | 'running' | 'done' | 'error';

interface CoworkActivityPanelProps {
  items: ChatSubagentEvent[];
  plan: CoworkPlanSnapshot | null;
  runtimeStatus: RuntimeStatus | null;
}

interface CoworkArtifactRow {
  id: string;
  title: string;
  kind: string;
  path: string;
  mime: string;
}

interface CoworkRow {
  id: string;
  title: string;
  prompt: string;
  status: CoworkRowStatus;
  progress: number;
  profile: string;
  summary: string;
  worktreeId: string;
  worktreeRoot: string;
  artifacts: CoworkArtifactRow[];
  diff: UnknownRecord;
  localVm: UnknownRecord;
  startedAt?: number;
  finishedAt?: number;
}

export function CoworkActivityPanel({ items, plan, runtimeStatus }: CoworkActivityPanelProps) {
  const t = useT();
  const rows = useMemo(() => buildCoworkRows(items, plan), [items, plan]);
  const stats = useMemo(() => coworkStats(rows), [rows]);
  const planRows = plan?.subruns?.length ? rows : rows.filter(row => row.title);
  const goal = stringValue(plan?.goal);
  const createdAt = createdAtText(plan?.created_at);
  const runtimeLine = runtimeStatus?.display || runtimeStatus?.message || '';

  if (rows.length === 0 && !plan) {
    return (
      <div className="cowork-activity-empty">
        <Network size={18} />
        <strong>{t('暂无 Cowork 任务')}</strong>
        <span>{t('启动 Cowork 后，这里会展示 plan、subruns、worktree、diff 和 artifact。')}</span>
      </div>
    );
  }

  return (
    <section className="cowork-activity-panel" aria-label={t('Cowork 任务详情')}>
      <header className="cowork-activity-header">
        <span className="cowork-activity-icon">
          <Network size={16} />
        </span>
        <div className="cowork-activity-title">
          <strong>{t('Cowork 任务详情')}</strong>
          <span>{goal || runtimeLine || t('本地 plan -> subruns -> diff/artifact 汇总')}</span>
        </div>
        <div className="cowork-activity-stats" aria-label={t('Cowork 状态统计')}>
          <b>{stats.progress}%</b>
          <span>{stats.done}/{stats.total} {t('完成')}</span>
        </div>
      </header>

      <div className="cowork-progress" aria-label={`${t('整体进度')} ${stats.progress}%`}>
        <span style={{ width: `${stats.progress}%` }} />
      </div>

      <div className="cowork-stat-strip">
        <Metric label={t('运行中')} value={String(stats.running)} />
        <Metric label={t('待执行')} value={String(stats.planned)} />
        <Metric label={t('错误')} value={String(stats.error)} tone={stats.error ? 'danger' : 'muted'} />
        <Metric label={t('Artifacts')} value={String(stats.artifacts)} />
      </div>

      <section className="cowork-plan-block" aria-label={t('Plan')}>
        <div className="cowork-section-head">
          <div>
            <strong>{t('Plan')}</strong>
            <span>{createdAt || t('按后端 Cowork plan 展示')}</span>
          </div>
          <em>{profileLabel(rows[0]?.profile || stringValue(plan?.merge_policy?.execution_profile))}</em>
        </div>
        {planRows.length ? (
          <ol className="cowork-plan-list">
            {planRows.map((row, index) => (
              <li data-status={row.status} key={row.id || `${row.title}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>{row.title || `${t('Subrun')} ${index + 1}`}</strong>
                  {row.prompt && <p>{compactText(row.prompt, 180)}</p>}
                </div>
                <em>{statusLabel(row.status, t)}</em>
              </li>
            ))}
          </ol>
        ) : (
          <p className="cowork-muted-line">{t('暂未收到 plan。')}</p>
        )}
      </section>

      <section className="cowork-subruns-block" aria-label={t('Subruns')}>
        <div className="cowork-section-head">
          <div>
            <strong>{t('Subruns')}</strong>
            <span>{t('本地顺序执行；每个 subrun 绑定自己的 worktree。')}</span>
          </div>
        </div>
        <div className="cowork-subrun-list">
          {rows.map(row => (
            <CoworkSubrunCard key={row.id} row={row} />
          ))}
        </div>
      </section>
    </section>
  );
}

function CoworkSubrunCard({ row }: { row: CoworkRow }) {
  const t = useT();
  const [reviewDiff, setReviewDiff] = useState<WorktreeDiffPayload | null>(null);
  const [promoteCheck, setPromoteCheck] = useState<WorktreePromotePayload | null>(null);
  const [promoteResult, setPromoteResult] = useState<WorktreePromotePayload | null>(null);
  const [actionError, setActionError] = useState('');
  const [busyAction, setBusyAction] = useState<'diff' | 'check' | 'promote' | ''>('');
  const StatusIcon = row.status === 'error' ? AlertTriangle : row.status === 'done' ? CheckCircle2 : LoaderCircle;
  const displayedDiff = reviewDiff
    ? {
        stat: reviewDiff.stat,
        status: reviewDiff.status,
        patch_preview: reviewDiff.patch,
        truncated: reviewDiff.truncated,
        error: reviewDiff.error || '',
      }
    : row.diff;
  const diffStat = stringValue(displayedDiff.stat);
  const diffStatus = stringValue(displayedDiff.status);
  const patchPreview = stringValue(displayedDiff.patch_preview);
  const diffError = stringValue(displayedDiff.error);
  const localVmBackend = stringValue(row.localVm.backend);
  const localVmStdout = stringValue(row.localVm.stdout);
  const localVmStderr = stringValue(row.localVm.stderr);
  const localVmChangedFiles = stringArray(row.localVm.changed_files);
  const localVmArtifacts = artifactRows(row.localVm.artifacts);
  const elapsed = elapsedText(row.startedAt, row.finishedAt);
  const canPromote = Boolean(row.worktreeId && promoteCheck?.ok && !promoteResult?.ok);

  const loadDiff = async () => {
    if (!row.worktreeId || busyAction) return;
    setBusyAction('diff');
    setActionError('');
    try {
      const payload = await getWorktreeDiff(row.worktreeId);
      setReviewDiff(payload);
    } catch (error) {
      setActionError(formatActionError(error));
    } finally {
      setBusyAction('');
    }
  };

  const checkPromote = async () => {
    if (!row.worktreeId || busyAction) return;
    setBusyAction('check');
    setActionError('');
    setPromoteResult(null);
    try {
      const payload = await promoteWorktree(row.worktreeId, true);
      setPromoteCheck(payload);
      if (!payload.ok) setActionError(payload.error || t('Diff 无法干净应用。'));
    } catch (error) {
      setActionError(formatActionError(error));
    } finally {
      setBusyAction('');
    }
  };

  const applyPromote = async () => {
    if (!canPromote || busyAction) return;
    setBusyAction('promote');
    setActionError('');
    try {
      const payload = await promoteWorktree(row.worktreeId, false);
      setPromoteResult(payload);
      if (!payload.ok) setActionError(payload.error || t('Promote 失败。'));
    } catch (error) {
      setActionError(formatActionError(error));
    } finally {
      setBusyAction('');
    }
  };

  return (
    <article className="cowork-subrun-card" data-status={row.status}>
      <div className="cowork-subrun-head">
        <StatusIcon className={row.status === 'running' ? 'spin' : undefined} size={15} />
        <div>
          <strong>{row.title}</strong>
          <span>
            {statusLabel(row.status, t)}
            {elapsed ? ` · ${elapsed}` : ''}
          </span>
        </div>
        <em>{profileLabel(row.profile)}</em>
      </div>

      <div className="cowork-progress" aria-label={`${row.title} ${row.progress}%`}>
        <span style={{ width: `${row.progress}%` }} />
      </div>

      {row.summary && <p className="cowork-summary-line">{row.summary}</p>}

      <div className="cowork-detail-grid">
        <div className="cowork-detail-cell">
          <span><GitBranch size={13} />{t('Worktree')}</span>
          <strong>{row.worktreeId || t('等待创建')}</strong>
          {row.worktreeRoot && <code>{row.worktreeRoot}</code>}
        </div>

        <div className="cowork-detail-cell">
          <span><PackageCheck size={13} />{t('Artifacts')}</span>
          {row.artifacts.length ? (
            <ul className="cowork-artifact-list">
              {row.artifacts.map((artifact, index) => (
                <li key={artifact.id || `${artifact.path}-${index}`}>
                  <FileText size={12} />
                  <div>
                    <strong>{artifact.title || artifact.id || artifact.path || t('Artifact')}</strong>
                    <span>{[artifact.kind, artifact.mime].filter(Boolean).join(' · ') || artifact.id}</span>
                    {(artifact.path || artifact.id) && <code>{artifact.path || artifact.id}</code>}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <small>{t('暂无 artifact')}</small>
          )}
        </div>

        <div className="cowork-detail-cell cowork-detail-wide">
          <span><FileCode size={13} />{t('Diff')}</span>
          <div className="cowork-diff-actions">
            <button type="button" disabled={!row.worktreeId || Boolean(busyAction)} onClick={() => void loadDiff()}>
              {busyAction === 'diff' ? t('加载中') : t('完整 Diff')}
            </button>
            <button type="button" disabled={!row.worktreeId || Boolean(busyAction)} onClick={() => void checkPromote()}>
              {busyAction === 'check' ? t('检查中') : t('检查可应用')}
            </button>
            <button type="button" data-danger="true" disabled={!canPromote || Boolean(busyAction)} onClick={() => void applyPromote()}>
              {busyAction === 'promote' ? t('Promote 中') : t('Promote')}
            </button>
          </div>
          {actionError && <p className="cowork-error-line">{actionError}</p>}
          {promoteCheck?.ok && !promoteResult?.ok && <small>{promoteCheck.message || t('Patch 可以干净应用。')}</small>}
          {promoteResult?.ok && <small>{promoteResult.message || t('已 promote 到主 workspace。')}</small>}
          {Object.keys(displayedDiff).length ? (
            <>
              {diffError && <p className="cowork-error-line">{diffError}</p>}
              {diffStat && <pre>{diffStat}</pre>}
              {diffStatus && <pre>{diffStatus}</pre>}
              {patchPreview && <pre>{compactText(patchPreview, 2400)}</pre>}
              {displayedDiff.truncated === true && <small>{t('Diff 已截断，完整内容在 artifact/worktree 中查看。')}</small>}
            </>
          ) : (
            <small>{t('暂无 diff')}</small>
          )}
        </div>

        <div className="cowork-detail-cell cowork-detail-wide">
          <span><SquareTerminal size={13} />{t('Local VM')}</span>
          {Object.keys(row.localVm).length ? (
            <>
              <div className="cowork-vm-meta">
                <b>{stringValue(row.localVm.runner) || 'local_vm'}</b>
                <span>{localVmBackend || t('未知 backend')}</span>
                {row.localVm.returncode !== undefined && <span>exit {String(row.localVm.returncode)}</span>}
              </div>
              {localVmStdout && <pre>{compactText(localVmStdout, 1600)}</pre>}
              {localVmStderr && <pre data-tone="danger">{compactText(localVmStderr, 1600)}</pre>}
              {localVmChangedFiles.length > 0 && (
                <ul className="cowork-file-list">
                  {localVmChangedFiles.map(path => (
                    <li key={path}><code>{path}</code></li>
                  ))}
                </ul>
              )}
              {localVmArtifacts.length > 0 && (
                <ul className="cowork-file-list">
                  {localVmArtifacts.map(artifact => (
                    <li key={artifact.path || artifact.id}><code>{artifact.path || artifact.id}</code></li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <small>{row.profile === 'local_vm' ? t('等待 local_vm 输出') : t('此 subrun 未使用 local_vm')}</small>
          )}
        </div>
      </div>
    </article>
  );
}

function Metric({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'danger' | 'muted' }) {
  return (
    <div className="cowork-stat" data-tone={tone}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function buildCoworkRows(items: ChatSubagentEvent[], plan: CoworkPlanSnapshot | null): CoworkRow[] {
  const planSubruns = Array.isArray(plan?.subruns) ? plan.subruns : [];
  const itemById = new Map(items.map(item => [item.taskId, item]));
  const used = new Set<string>();
  const rows: CoworkRow[] = [];

  for (const subrun of planSubruns) {
    const id = stringValue(subrun.subrun_id) || stringValue(subrun.task_id) || stringValue(subrun.run_id) || stringValue(subrun.title);
    const title = stringValue(subrun.title) || stringValue(subrun.name) || id;
    const item = itemById.get(id) || items.find(candidate => candidate.name === title);
    if (item) used.add(item.taskId);
    rows.push(coworkRowFrom(item, subrun, rows.length + 1));
  }

  for (const item of items) {
    if (used.has(item.taskId)) continue;
    rows.push(coworkRowFrom(item, null, rows.length + 1));
  }

  return rows;
}

function coworkRowFrom(item: ChatSubagentEvent | undefined, subrun: CoworkPlanSubrun | null, index: number): CoworkRow {
  const result = recordValue(item?.result);
  const subrunRecord = recordValue(subrun);
  const worktree = recordValue(value(result, subrunRecord, 'worktree'));
  const diff = nonEmptyRecord(value(result, subrunRecord, 'diff')) || {};
  const localVm = nonEmptyRecord(value(result, subrunRecord, 'local_vm', 'localVm')) || {};
  const artifacts = artifactRows(value(result, subrunRecord, 'artifacts'));
  const profile = firstString(result, subrunRecord, ['execution_profile', 'executionProfile']) || 'local_worktree';
  const worktreeId = firstString(result, subrunRecord, worktree, ['worktree_id', 'worktreeId']);
  const worktreeRoot = firstString(result, subrunRecord, worktree, ['worktree_workspace_root', 'worktreeWorkspaceRoot', 'path']);
  const id = item?.taskId || firstString(subrunRecord, ['subrun_id', 'task_id', 'run_id']) || `subrun-${index}`;

  return {
    id,
    title: item?.name || firstString(subrunRecord, ['title', 'name']) || `Subrun ${index}`,
    prompt: firstString(subrunRecord, ['prompt']),
    status: rowStatus(item?.status, firstString(subrunRecord, ['status'])),
    progress: clampProgress(item?.progress ?? progressFromPlanStatus(firstString(subrunRecord, ['status']))),
    profile,
    summary: item?.summary || '',
    worktreeId,
    worktreeRoot,
    artifacts,
    diff,
    localVm,
    startedAt: item?.startedAt,
    finishedAt: item?.finishedAt || item?.updatedAt,
  };
}

function coworkStats(rows: CoworkRow[]) {
  const total = rows.length;
  const done = rows.filter(row => row.status === 'done').length;
  const error = rows.filter(row => row.status === 'error').length;
  const running = rows.filter(row => row.status === 'running').length;
  const planned = rows.filter(row => row.status === 'planned').length;
  const artifacts = rows.reduce((sum, row) => sum + row.artifacts.length, 0);
  const progress = total ? Math.round(rows.reduce((sum, row) => sum + row.progress, 0) / total) : 0;
  return { total, done, error, running, planned, artifacts, progress };
}

function rowStatus(itemStatus: ChatSubagentEvent['status'] | undefined, planStatus: string): CoworkRowStatus {
  if (itemStatus === 'error') return 'error';
  if (itemStatus === 'done') return 'done';
  if (itemStatus === 'running') return 'running';
  const status = planStatus.toLowerCase();
  if (['failed', 'failure', 'error'].includes(status)) return 'error';
  if (['done', 'complete', 'completed', 'finished'].includes(status)) return 'done';
  if (['running', 'active', 'in_progress', 'in-progress'].includes(status)) return 'running';
  return 'planned';
}

function progressFromPlanStatus(status: string): number {
  const normalized = status.toLowerCase();
  if (['done', 'complete', 'completed', 'finished'].includes(normalized)) return 100;
  if (['running', 'active', 'in_progress', 'in-progress'].includes(normalized)) return 35;
  if (['failed', 'failure', 'error'].includes(normalized)) return 100;
  return 0;
}

function statusLabel(status: CoworkRowStatus, t: (text: string) => string): string {
  if (status === 'done') return t('完成');
  if (status === 'running') return t('运行中');
  if (status === 'error') return t('错误');
  return t('待执行');
}

function profileLabel(profile: string): string {
  if (profile === 'local_vm') return 'local_vm · MetisRuntime WSL';
  if (profile === 'local_worktree') return 'local_worktree';
  if (profile === 'local_direct') return 'local_direct';
  return profile || 'local_worktree';
}

function artifactRows(value: unknown): CoworkArtifactRow[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(item => recordValue(item))
    .filter(item => Object.keys(item).length > 0)
    .map((item, index) => ({
      id: firstString(item, ['artifact_id', 'artifactId', 'id']) || `artifact-${index}`,
      title: firstString(item, ['title', 'name', 'relative_path', 'relativePath']),
      kind: firstString(item, ['kind', 'type']),
      path: firstString(item, ['path', 'url', 'relative_path', 'relativePath']),
      mime: firstString(item, ['mime', 'mime_type', 'mimeType']),
    }));
}

function firstString(...args: Array<UnknownRecord | string[]>): string {
  const keys = args[args.length - 1];
  if (!Array.isArray(keys)) return '';
  for (const record of args.slice(0, -1)) {
    if (!record || Array.isArray(record)) continue;
    for (const key of keys) {
      const text = stringValue(record[key]);
      if (text) return text;
    }
  }
  return '';
}

function value(...args: Array<UnknownRecord | string>): unknown {
  const keys = args.slice(2).filter((item): item is string => typeof item === 'string');
  const records = args.slice(0, 2).filter((item): item is UnknownRecord => Boolean(item && typeof item === 'object' && !Array.isArray(item)));
  for (const record of records) {
    for (const key of keys) {
      if (record[key] !== undefined) return record[key];
    }
  }
  return undefined;
}

function recordValue(value: unknown): UnknownRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : {};
}

function nonEmptyRecord(value: unknown): UnknownRecord | null {
  const record = recordValue(value);
  return Object.keys(record).length > 0 ? record : null;
}

function stringValue(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(item => stringValue(item)).filter(Boolean);
}

function compactText(value: string, maxLength: number): string {
  const text = value.replace(/\s+$/g, '');
  return text.length > maxLength ? `${text.slice(0, maxLength).trimEnd()}...` : text;
}

function clampProgress(value: number): number {
  return Math.min(Math.max(Math.round(value || 0), 0), 100);
}

function elapsedText(startedAt?: number, finishedAt?: number): string {
  if (!startedAt || !finishedAt || finishedAt < startedAt) return '';
  const ms = finishedAt - startedAt;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function createdAtText(value: unknown): string {
  const timestamp = typeof value === 'number' ? value : Number(value || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '';
  const date = new Date(timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000);
  return date.toLocaleString();
}

function formatActionError(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error || 'Action failed');
}
