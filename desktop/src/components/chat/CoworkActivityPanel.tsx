import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FileText,
  LoaderCircle,
  Network,
  PackageCheck,
  ScrollText,
  SquareTerminal,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useT } from '../../hooks/useT';
import type { ChatSubagentEvent, CoworkPlanSnapshot, CoworkPlanSubrun, RuntimeStatus } from '../../lib/types';

type UnknownRecord = Record<string, unknown>;
type CoworkRowStatus = 'planned' | 'running' | 'done' | 'error';

interface CoworkActivityPanelProps {
  items: ChatSubagentEvent[];
  onClear?: () => void;
  plan: CoworkPlanSnapshot | null;
  runtimeStatus: RuntimeStatus | null;
}

interface CoworkArtifactRow {
  id: string;
  title: string;
  kind: string;
  path: string;
  mime: string;
  validation: string;
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
  evidence: UnknownRecord;
  localVm: UnknownRecord;
  resumeAction: '' | 'reused' | 'rerun';
  startedAt?: number;
  finishedAt?: number;
}

interface CoworkResumeSummary {
  enabled: boolean;
  reused: number;
  rerun: number;
  total: number;
  sourceRunId: string;
}

export function CoworkActivityPanel({ items, onClear, plan, runtimeStatus }: CoworkActivityPanelProps) {
  const t = useT();
  const rows = useMemo(() => buildCoworkRows(items, plan), [items, plan]);
  const stats = useMemo(() => coworkStats(rows), [rows]);
  const resume = useMemo(() => coworkResumeSummary(plan, rows), [plan, rows]);
  const activeRows = rows.filter(row => row.status === 'running' || row.status === 'planned');
  const settledRows = rows.filter(row => row.status === 'done' || row.status === 'error');
  const canClear = Boolean(onClear && rows.length > 0 && stats.running === 0 && stats.planned === 0);
  const goal = stringValue(plan?.goal);
  const runtimeLine = runtimeStatus?.display || runtimeStatus?.message || '';
  const titleLine = goal || runtimeLine || t('Cowork 任务');
  const showOverallProgress = stats.running > 0 || stats.planned > 0;

  if (rows.length === 0 && !plan) {
    return (
      <div className="cowork-activity-empty">
        <Network size={18} />
        <strong>{t('暂无 Cowork 任务')}</strong>
        <span>{t('启动 Cowork 后，这里会展示任务进度。')}</span>
      </div>
    );
  }

  return (
    <section className="cowork-activity-panel" aria-label={t('Cowork 任务详情')}>
      <header className="cowork-activity-header">
        <div className="cowork-activity-title">
          <strong>{t('Cowork')}</strong>
          <span>{titleLine}</span>
        </div>
        <div className="cowork-header-actions">
          {resume.enabled && (
            <div className="cowork-resume-chip" title={resume.sourceRunId ? `${t('来源 run')} ${resume.sourceRunId}` : undefined}>
              <span>{t('Resume')}</span>
              <b>{t('复用')} {resume.reused}</b>
              <b>{t('重跑')} {resume.rerun}</b>
            </div>
          )}
          <div className="cowork-activity-stats" aria-label={t('Cowork 状态统计')}>
            <b>{showOverallProgress ? `${stats.progress}%` : `${stats.done}/${stats.total}`}</b>
            <span>{showOverallProgress ? `${stats.done}/${stats.total} ${t('完成')}` : stats.error ? `${stats.error} ${t('失败')}` : t('完成')}</span>
          </div>
          {canClear && (
            <button className="cowork-clear-button" type="button" onClick={onClear}>
              {t('清理')}
            </button>
          )}
        </div>
      </header>

      {showOverallProgress && (
        <div className="cowork-progress" aria-label={`${t('整体进度')} ${stats.progress}%`}>
          <span style={{ width: `${stats.progress}%` }} />
        </div>
      )}

      <section className="cowork-subruns-block" aria-label={t('Subruns')}>
        <div className="cowork-section-head">
          <div>
            <strong>{t('任务')}</strong>
            <span>
              {stats.running ? `${stats.running} ${t('运行中')} · ` : ''}
              {stats.done}/{stats.total} {t('完成')}
              {stats.error ? ` · ${stats.error} ${t('失败')}` : ''}
            </span>
          </div>
        </div>
        {activeRows.length > 0 && (
          <CoworkSubrunGroup rows={activeRows} title={t('进行中 / 待开始')} />
        )}
        {settledRows.length > 0 && (
          <CoworkSubrunGroup rows={settledRows} title={t('已完成 / 失败')} />
        )}
      </section>
    </section>
  );
}

function CoworkSubrunGroup({ rows, title }: { rows: CoworkRow[]; title: string }) {
  return (
    <div className="cowork-subrun-group">
      <div className="cowork-subrun-group-head">
        <span>{title}</span>
        <em>{rows.length}</em>
      </div>
      <div className="cowork-subrun-list">
        {rows.map(row => (
          <CoworkSubrunCard key={row.id} row={row} />
        ))}
      </div>
    </div>
  );
}

function CoworkSubrunCard({ row }: { row: CoworkRow }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const StatusIcon = row.status === 'error' ? AlertTriangle : row.status === 'done' ? CheckCircle2 : LoaderCircle;
  const localVmBackend = stringValue(row.localVm.backend);
  const localVmStdout = stringValue(row.localVm.stdout);
  const localVmStderr = stringValue(row.localVm.stderr);
  const localVmChangedFiles = stringArray(row.localVm.changed_files);
  const localVmArtifacts = artifactRows(row.localVm.artifacts);
  const evidenceCounts = recordValue(row.evidence.counts);
  const failureReasons = failureReasonRows(row.evidence.failure_reasons);
  const hasVmOutput = Boolean(
    Object.keys(row.localVm).length
      && (localVmBackend || localVmStdout || localVmStderr || localVmChangedFiles.length || localVmArtifacts.length || row.localVm.returncode !== undefined),
  );
  const evidenceStats = [
    { label: 'Diff', value: numberValue(evidenceCounts.diff) },
    { label: 'Artifacts', value: numberValue(evidenceCounts.artifacts) },
    { label: 'Stdout/Test', value: numberValue(evidenceCounts.stdout_test ?? evidenceCounts.stdoutTest) },
    { label: 'Failures', value: numberValue(evidenceCounts.failure_reasons ?? evidenceCounts.failureReasons) },
  ].filter(item => item.value > 0 && (item.label !== 'Failures' || failureReasons.length === 0));
  const hasVisibleEvidenceCounts = evidenceStats.length > 0;
  const hasDetails = Boolean(failureReasons.length > 0 || row.artifacts.length > 0 || hasVisibleEvidenceCounts || hasVmOutput || row.worktreeRoot);
  const primaryFailure = failureReasons.find(reason => reason.message) || failureReasons[0] || null;
  const inlineText = primaryFailure?.message || inlineSubrunText(row);
  const elapsed = elapsedText(row.startedAt, row.finishedAt);
  const showProgress = row.status === 'running' || row.status === 'planned';

  return (
    <article className="cowork-subrun-card" data-status={row.status} data-open={open} data-resume={row.resumeAction || undefined}>
      <button
        aria-expanded={open}
        className="cowork-subrun-toggle"
        disabled={!hasDetails}
        type="button"
        onClick={() => setOpen(value => !value)}
      >
        <StatusIcon className={row.status === 'running' ? 'spin' : undefined} size={14} />
        <div className="cowork-subrun-text">
          <strong>{row.title}</strong>
          <span>{statusLabel(row.status, t)}{elapsed ? ` · ${elapsed}` : ''}</span>
        </div>
        <div className="cowork-subrun-meta">
          {row.resumeAction && <span>{row.resumeAction === 'reused' ? t('复用') : t('重跑')}</span>}
          <em>{profileLabel(row.profile)}</em>
          {hasDetails && <ChevronRight className="disclosure-chevron" data-open={open} size={13} />}
        </div>
      </button>

      {showProgress && (
        <div className="cowork-progress" aria-label={`${row.title} ${row.progress}%`}>
          <span style={{ width: `${row.progress}%` }} />
        </div>
      )}

      {inlineText && (
        <p className={primaryFailure ? 'cowork-reason-inline' : 'cowork-summary-line'}>
          {compactText(inlineText, 180)}
        </p>
      )}

      {open && hasDetails && (
        <div className="cowork-detail-grid">
          {failureReasons.length > 0 && (
            <div className="cowork-detail-cell cowork-detail-wide cowork-failure-cell">
              <span><ScrollText size={13} />{t('失败原因')}</span>
              <ul className="cowork-reason-list">
                {failureReasons.map((reason, index) => (
                  <li key={`${reason.code}-${index}`}>
                    <strong>{reason.code}</strong>
                    <span>{reason.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {row.artifacts.length > 0 && (
            <div className="cowork-detail-cell">
              <span><PackageCheck size={13} />{t('Artifacts')}</span>
              <ul className="cowork-artifact-list">
                {row.artifacts.map((artifact, index) => (
                  <li key={artifact.id || `${artifact.path}-${index}`}>
                    <FileText size={12} />
                    <div>
                      <strong>{artifact.title || artifact.id || artifact.path || t('Artifact')}</strong>
                      <span>{[artifact.validation, artifact.kind, artifact.mime].filter(Boolean).join(' · ') || artifact.id}</span>
                      {(artifact.path || artifact.id) && <code>{artifact.path || artifact.id}</code>}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {hasVisibleEvidenceCounts && (
            <div className="cowork-detail-cell">
              <span><ScrollText size={13} />{t('证据')}</span>
              <div className="cowork-evidence-pills">
                {evidenceStats.map(item => (
                  <b key={item.label} data-active="true">
                    {item.label} <em>{item.value}</em>
                  </b>
                ))}
              </div>
            </div>
          )}

          {row.worktreeRoot && (
            <div className="cowork-detail-cell">
              <span><Network size={13} />{t('Worktree')}</span>
              <code>{row.worktreeRoot}</code>
            </div>
          )}

          {hasVmOutput && (
            <div className="cowork-detail-cell cowork-detail-wide">
              <span><SquareTerminal size={13} />{t('Local VM')}</span>
              <div className="cowork-vm-meta">
                <b>{stringValue(row.localVm.runner) || 'local_vm'}</b>
                {localVmBackend && <span>{localVmBackend}</span>}
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
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function buildCoworkRows(items: ChatSubagentEvent[], plan: CoworkPlanSnapshot | null): CoworkRow[] {
  const planSubruns = Array.isArray(plan?.subruns) ? plan.subruns : [];
  const itemById = new Map(items.map(item => [item.taskId, item]));
  const used = new Set<string>();
  const rowKeys = new Set<string>();
  const rows: CoworkRow[] = [];

  for (const subrun of planSubruns) {
    const id = stringValue(subrun.subrun_id) || stringValue(subrun.task_id) || stringValue(subrun.run_id) || stringValue(subrun.title);
    const title = stringValue(subrun.title) || stringValue(subrun.name) || id;
    const item = itemById.get(id) || items.find(candidate => candidate.name === title);
    if (item) used.add(item.taskId);
    const row = coworkRowFrom(item, subrun, rows.length + 1);
    if (coworkRowSeen(row, rowKeys)) continue;
    rememberCoworkRow(row, rowKeys);
    rows.push(row);
  }

  for (const item of items) {
    if (used.has(item.taskId)) continue;
    const row = coworkRowFrom(item, null, rows.length + 1);
    if (coworkRowSeen(row, rowKeys)) continue;
    used.add(item.taskId);
    rememberCoworkRow(row, rowKeys);
    rows.push(row);
  }

  return rows;
}

function coworkRowSeen(row: CoworkRow, rowKeys: Set<string>): boolean {
  return coworkRowKeys(row).some(key => rowKeys.has(key));
}

function rememberCoworkRow(row: CoworkRow, rowKeys: Set<string>) {
  for (const key of coworkRowKeys(row)) rowKeys.add(key);
}

function coworkRowKeys(row: CoworkRow): string[] {
  const id = row.id.trim();
  const title = row.title.trim().toLowerCase();
  return [
    id ? `id:${id}` : '',
    title ? `title:${title}` : '',
  ].filter(Boolean);
}

function coworkRowFrom(item: ChatSubagentEvent | undefined, subrun: CoworkPlanSubrun | null, index: number): CoworkRow {
  const result = recordValue(item?.result);
  const subrunRecord = recordValue(subrun);
  const worktree = recordValue(value(result, subrunRecord, 'worktree'));
  const diff = nonEmptyRecord(value(result, subrunRecord, 'diff')) || {};
  const localVm = nonEmptyRecord(value(result, subrunRecord, 'local_vm', 'localVm')) || {};
  const artifacts = artifactRows(value(result, subrunRecord, 'artifacts'));
  const evidence = nonEmptyRecord(value(result, subrunRecord, 'evidence')) || {};
  const profile = firstString(result, subrunRecord, ['execution_profile', 'executionProfile']) || 'local_worktree';
  const worktreeId = firstString(result, subrunRecord, worktree, ['worktree_id', 'worktreeId']);
  const worktreeRoot = firstString(result, subrunRecord, worktree, ['worktree_workspace_root', 'worktreeWorkspaceRoot', 'path']);
  const id = item?.taskId || firstString(subrunRecord, ['subrun_id', 'task_id', 'run_id']) || `subrun-${index}`;
  const resumeAction = resumeActionFrom(item, result, subrunRecord);

  return {
    id,
    title: item?.name || firstString(subrunRecord, ['title', 'name']) || `Subrun ${index}`,
    prompt: firstString(subrunRecord, ['objective', 'prompt']),
    status: rowStatus(item?.status, firstString(subrunRecord, ['status'])),
    progress: clampProgress(item?.progress ?? progressFromPlanStatus(firstString(subrunRecord, ['status']))),
    profile,
    summary: item?.summary || '',
    worktreeId,
    worktreeRoot,
    artifacts,
    diff,
    evidence,
    localVm,
    resumeAction,
    startedAt: item?.startedAt,
    finishedAt: item?.finishedAt || item?.updatedAt,
  };
}

function coworkResumeSummary(plan: CoworkPlanSnapshot | null, rows: CoworkRow[]): CoworkResumeSummary {
  const resume = recordValue(plan?.resume);
  const counts = recordValue(resume.counts);
  const enabled = resume.enabled === true || rows.some(row => row.resumeAction);
  const reusedFromPlan = numberValue(counts.succeeded) + numberValue(counts.failed);
  const rerunFromPlan = numberValue(counts.unfinished);
  const reusedFromRows = rows.filter(row => row.resumeAction === 'reused').length;
  const rerunFromRows = rows.filter(row => row.resumeAction === 'rerun').length;
  return {
    enabled,
    reused: reusedFromPlan || reusedFromRows,
    rerun: rerunFromPlan || rerunFromRows,
    total: numberValue(counts.total) || rows.length,
    sourceRunId: stringValue(resume.source_run_id ?? resume.sourceRunId),
  };
}

function resumeActionFrom(item: ChatSubagentEvent | undefined, result: UnknownRecord, subrun: UnknownRecord): '' | 'reused' | 'rerun' {
  if (item?.stage === 'resume_reused' || result.resumed === true || stringValue(result.resume_action ?? result.resumeAction) === 'reused_terminal_result') {
    return 'reused';
  }
  const originalStatus = stringValue(subrun.resume_original_status ?? subrun.resumeOriginalStatus);
  if (!originalStatus) return '';
  const terminal = rowStatus(undefined, originalStatus);
  return terminal === 'done' || terminal === 'error' ? 'reused' : 'rerun';
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
  if (itemStatus === 'error' || itemStatus === 'canceled') return 'error';
  if (itemStatus === 'done' || itemStatus === 'promoted') return 'done';
  if (itemStatus === 'running' || itemStatus === 'waiting_permission') return 'running';
  if (itemStatus === 'planned') return 'planned';
  const status = planStatus.toLowerCase();
  if (['failed', 'failure', 'error', 'canceled', 'cancelled'].includes(status)) return 'error';
  if (['done', 'complete', 'completed', 'finished', 'succeeded', 'success', 'promoted'].includes(status)) return 'done';
  if (['running', 'active', 'in_progress', 'in-progress', 'waiting_permission', 'waiting-permission'].includes(status)) return 'running';
  return 'planned';
}

function progressFromPlanStatus(status: string): number {
  const normalized = status.toLowerCase();
  if (['done', 'complete', 'completed', 'finished', 'succeeded', 'success', 'promoted'].includes(normalized)) return 100;
  if (['running', 'active', 'in_progress', 'in-progress'].includes(normalized)) return 35;
  if (['waiting_permission', 'waiting-permission'].includes(normalized)) return 50;
  if (['failed', 'failure', 'error', 'canceled', 'cancelled'].includes(normalized)) return 100;
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

function inlineSubrunText(row: CoworkRow): string {
  if (row.status === 'done') return '';
  const text = (row.summary || row.prompt || '').trim();
  if (!text) return '';
  if (['finished', 'done', 'completed', 'success', 'succeeded', '完成', '已完成'].includes(text.toLowerCase())) return '';
  return text;
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
      validation: officeValidationLabel(recordValue(item.metadata)),
    }));
}

function officeValidationLabel(metadata: UnknownRecord): string {
  const validation = recordValue(metadata.office_validation ?? metadata.officeValidation);
  if (!Object.keys(validation).length) return '';
  if (validation.ok === true) return '已验收';
  return firstString(validation, ['summary', 'error']) || '验收失败';
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

function failureReasonRows(value: unknown): Array<{ code: string; message: string; source: string }> {
  if (!Array.isArray(value)) return [];
  return value
    .map(item => recordValue(item))
    .filter(item => Object.keys(item).length > 0)
    .map(item => ({
      code: stringValue(item.code) || 'SUBRUN_FAILED',
      message: stringValue(item.message),
      source: stringValue(item.source),
    }))
    .filter(item => item.code || item.message);
}

function numberValue(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
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
