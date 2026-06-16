import { AlertTriangle, CheckCircle2, Clock3, Edit3, Play, Plus, Power, RotateCcw, Trash2, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  createCronTask,
  deleteCronTask,
  getCronTasks,
  runCronTask,
  toggleCronTask,
  updateCronTask,
} from '../../lib/api';
import type { CronTask } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';

const scheduleOptions = [
  { value: 'every 1 minute', label: '每分钟' },
  { value: 'every 30 minutes', label: '每 30 分钟' },
  { value: '09:00', label: '每天 09:00' },
  { value: '18:00', label: '每天 18:00' },
];

export function CronPanel() {
  const t = useT();
  const [tasks, setTasks] = useState<CronTask[]>([]);
  const [name, setName] = useState('');
  const [schedule, setSchedule] = useState('every 1 minute');
  const [prompt, setPrompt] = useState('');
  const [editingId, setEditingId] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [validationMessage, setValidationMessage] = useState('');
  const promptInputRef = useRef<HTMLTextAreaElement | null>(null);
  const workspaces = useSessionStore(state => state.workspaces);
  const activeWorkspaceId = useSessionStore(state => state.activeWorkspaceId);
  const selectSession = useSessionStore(state => state.selectSession);
  const loadSessions = useSessionStore(state => state.load);
  const loadChatSession = useChatStore(state => state.loadSession);
  const setActiveSection = useUiStore(state => state.setActiveSection);
  const requestConfirm = useUiStore(state => state.requestConfirm);

  const load = async () => {
    setError('');
    try {
      setTasks(await getCronTasks());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const stats = useMemo(() => {
    const enabled = tasks.filter(task => task.enabled).length;
    const lastRun = Math.max(0, ...tasks.map(task => task.lastRun || 0));
    const nextRun = Math.min(...tasks.filter(task => task.enabled && task.nextRun).map(task => task.nextRun));
    return {
      total: tasks.length,
      enabled,
      lastRun,
      nextRun: Number.isFinite(nextRun) ? nextRun : 0,
    };
  }, [tasks]);

  const resetForm = () => {
    setName('');
    setSchedule('every 1 minute');
    setPrompt('');
    setEditingId('');
    setValidationMessage('');
  };

  const submit = async () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt) {
      setValidationMessage(t('先填写要定时执行的任务 prompt。'));
      promptInputRef.current?.focus();
      return;
    }
    setBusy('submit');
    setError('');
    setValidationMessage('');
    try {
      if (editingId) {
        await updateCronTask(editingId, {
          name: name.trim() || 'Scheduled task',
          schedule,
          prompt: trimmedPrompt,
          workspaceId: activeWorkspaceId,
        });
      } else {
        await createCronTask({
          name: name.trim() || 'Scheduled task',
          schedule,
          prompt: trimmedPrompt,
          workspaceId: activeWorkspaceId,
        });
      }
      resetForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const editTask = (task: CronTask) => {
    setEditingId(task.id);
    setName(task.name);
    setSchedule(task.schedule || 'every 1 minute');
    setPrompt(task.prompt);
    setError('');
    setValidationMessage('');
  };

  const runTask = async (task: CronTask) => {
    setBusy(`run:${task.id}`);
    setError('');
    try {
      const result = await runCronTask(task.id);
      await loadSessions();
      await load();
      if (result.sessionId) {
        await selectSession(result.sessionId);
        await loadChatSession(result.sessionId);
        setActiveSection('chat');
      } else if (result.error) {
        setError(result.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const jumpToSession = async (sessionId: string) => {
    await selectSession(sessionId);
    await loadChatSession(sessionId);
    setActiveSection('chat');
  };

  const removeTask = async (task: CronTask) => {
    const confirmed = await requestConfirm({
      title: t('删除定时任务？'),
      message: task.name,
      details: `${t('计划: ')}${t(scheduleLabel(task.schedule))}\n${t('提示词: ')}${task.prompt}\n\n${t('删除后不会再按计划运行。')}`,
      confirmLabel: t('删除'),
      cancelLabel: t('取消'),
      tone: 'danger',
      icon: 'trash',
    });
    if (!confirmed) return;
    setBusy(`delete:${task.id}`);
    setError('');
    try {
      await deleteCronTask(task.id);
      if (editingId === task.id) resetForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  const toggleTask = async (task: CronTask) => {
    setBusy(`toggle:${task.id}`);
    setError('');
    try {
      await toggleCronTask(task.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="cron-panel" data-zone="cron">
      <header>
        <div>
          <h2>{t('自动化')}</h2>
          <p>{t('定时运行 prompt，结果会保存成会话。')}</p>
        </div>
        <button type="button" onClick={() => void load()}>
          <RotateCcw size={14} />
          {t('刷新')}
        </button>
      </header>
      {error && (
        <div className="cron-error">
          <AlertTriangle size={14} />
          <span>{error}</span>
        </div>
      )}
      <section className="cron-metrics">
        <Metric label={t('任务')} value={String(stats.total)} />
        <Metric label={t('启用')} value={String(stats.enabled)} />
        <Metric label={t('最近运行')} value={t(formatTime(stats.lastRun))} />
        <Metric label={t('下次运行')} value={t(formatTime(stats.nextRun))} />
      </section>
      <section className="cron-form">
        <input className="cron-name-input" value={name} placeholder={t('任务名称')} onChange={event => setName(event.target.value)} />
        <select className="cron-schedule-select" value={schedule} onChange={event => setSchedule(event.target.value)}>
          {scheduleOptions.map(option => (
            <option value={option.value} key={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <textarea
          ref={promptInputRef}
          className="cron-prompt-input"
          value={prompt}
          placeholder={t('要定时执行的任务 prompt')}
          aria-invalid={Boolean(validationMessage)}
          onChange={event => {
            setPrompt(event.target.value);
            if (event.target.value.trim()) setValidationMessage('');
          }}
        />
        <button
          className="cron-submit-button"
          type="button"
          disabled={busy === 'submit'}
          aria-disabled={!prompt.trim()}
          title={!prompt.trim() ? t('填写任务 prompt 后即可新建任务') : undefined}
          onClick={() => void submit()}
        >
          {editingId ? <CheckCircle2 size={15} /> : <Plus size={15} />}
          {busy === 'submit' ? t('保存中') : editingId ? t('保存修改') : t('新建任务')}
        </button>
        {editingId && (
          <button className="cron-cancel-edit-button" type="button" onClick={resetForm}>
            <X size={15} />
            {t('取消编辑')}
          </button>
        )}
        <p className="cron-form-hint" data-tone={validationMessage ? 'danger' : 'muted'}>
          {validationMessage || t('填写 prompt 后，Metis 会按所选计划运行并把结果保存成新会话。')}
        </p>
      </section>
      <section className="cron-list">
        {tasks.length === 0 && (
          <article className="zone-empty">
            <Clock3 size={18} />
            <span>{t('暂无定时任务')}</span>
            <small>{t('创建一个任务后，Metis 会按计划运行并保存结果会话。')}</small>
          </article>
        )}
        {tasks.map(task => (
          <article className="cron-row" data-active={editingId === task.id} key={task.id}>
            <div>
              <header>
                <strong>{task.name}</strong>
                <StatusPill ok={task.enabled} text={task.enabled ? t('启用') : t('停用')} />
              </header>
              <span>
                {t(scheduleLabel(task.schedule))} · {workspaceName(workspaces, task.workspaceId) || t('当前工作区')}
              </span>
              <p>{task.prompt}</p>
              <em>
                {t('下次 ')}{t(formatTime(task.nextRun))}
                {task.lastRun ? `${t(' · 上次 ')}${t(formatTime(task.lastRun))} · ${task.lastStatus || 'ok'}` : ''}
              </em>
            </div>
            <div className="cron-actions">
              <button className="cron-edit-button" type="button" onClick={() => editTask(task)}>
                <Edit3 size={14} />
                {t('编辑')}
              </button>
              <button
                className="cron-toggle-button"
                type="button"
                disabled={busy === `toggle:${task.id}`}
                onClick={() => void toggleTask(task)}
              >
                <Power size={14} />
                {task.enabled ? t('停用') : t('启用')}
              </button>
              <button
                className="cron-run-button"
                type="button"
                disabled={busy === `run:${task.id}`}
                onClick={() => void runTask(task)}
              >
                <Play size={14} />
                {busy === `run:${task.id}` ? t('运行中') : t('运行')}
              </button>
              {task.lastSessionId && (
                <button type="button" onClick={() => void jumpToSession(task.lastSessionId)}>
                  {t('上次结果')}
                </button>
              )}
              <button
                className="danger-action cron-delete-button"
                type="button"
                disabled={busy === `delete:${task.id}`}
                onClick={() => void removeTask(task)}
              >
                <Trash2 size={14} />
                {t('删除')}
              </button>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function StatusPill({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span className="zone-pill" data-ok={ok}>
      {text}
    </span>
  );
}

function workspaceName(workspaces: Array<{ id: string; name: string }>, id: string): string {
  return workspaces.find(workspace => workspace.id === id)?.name || '';
}

function scheduleLabel(value: string): string {
  return scheduleOptions.find(option => option.value === value)?.label || value || '未设置';
}

function formatTime(ts: number): string {
  if (!ts) return '未安排';
  return new Date(ts * 1000).toLocaleString();
}
