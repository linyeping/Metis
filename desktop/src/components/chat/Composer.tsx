import {
  AlertCircle,
  ArrowUp,
  Check,
  ChevronDown,
  ClipboardList,
  FileText,
  Image as ImageIcon,
  Loader2,
  Pencil,
  Plus,
  ShieldAlert,
  ShieldQuestion,
  Sparkles,
  Square,
  Upload,
  X,
  Zap,
} from 'lucide-react';
import { AnimatePresence, motion, useAnimationControls } from 'framer-motion';
import {
  Fragment,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  getComposerPermissionMode,
  getProviderStatus,
  getSettings,
  getSkills,
  setComposerPermissionMode,
  updateSettings,
} from '../../lib/api';
import { filterSlashWorkflowCommands, moveSlashSelection } from '../../lib/slashCommands';
import type { ParsedFile, PermissionAccessMode, ProviderProfile, RuntimeSettings, SkillSummary } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useUiStore } from '../../store/uiStore';
import { effortLevelsFor, effortLabel } from '../../lib/reasoningTiers';
import { useT } from '../../hooks/useT';

function shortModelName(model: string): string {
  if (!model) return '模型';
  const trimmed = model.split('/').pop() || model;
  return trimmed.replace(/^(gpt|claude|gemini|qwen|deepseek|glm)[-_]?/i, '') || trimmed;
}

interface ComposerModelEntry {
  id: string;
  model: string;
  providerId: string;
  baseUrl: string;
  provider: string;
  active: boolean;
}

function buildComposerModelList(providers: ProviderProfile[], settings: RuntimeSettings | null): ComposerModelEntry[] {
  const activeProviderId = settings?.providerId || settings?.backend || '';
  const activeModel = settings?.model || '';
  const out: ComposerModelEntry[] = [];
  for (const provider of providers) {
    if (provider.providerId === 'fake') continue;
    const isActiveProvider = provider.providerId === activeProviderId;
    const modelIds = Array.from(new Set([
      provider.defaultModel,
      ...provider.fallbackModels,
      isActiveProvider ? activeModel : '',
    ].filter(Boolean)));
    for (const model of modelIds) {
      out.push({
        id: `${provider.providerId}:${model}`,
        model,
        providerId: provider.providerId,
        baseUrl: provider.baseUrl || (isActiveProvider ? settings?.baseUrl || '' : ''),
        provider: provider.displayName,
        active: isActiveProvider && model === activeModel,
      });
    }
  }
  if (out.length === 0 && activeModel) {
    out.push({ id: `current:${activeModel}`, model: activeModel, providerId: activeProviderId, baseUrl: settings?.baseUrl || '', provider: 'Current', active: true });
  }
  return out;
}

const accessOptions: Array<{
  mode: PermissionAccessMode;
  label: string;
  buttonLabel: string;
  description: string;
}> = [
  {
    mode: 'ask',
    label: '询问权限',
    buttonLabel: '询问权限',
    description: '每次使用工具前都征求许可',
  },
  {
    mode: 'edit',
    label: '接受编辑',
    buttonLabel: '接受编辑',
    description: '自动应用文件编辑；运行命令、桌面或联网操作前询问',
  },
  {
    mode: 'plan',
    label: '计划模式',
    buttonLabel: '计划模式',
    description: '只读研究并制定计划，不做任何更改',
  },
  {
    mode: 'auto',
    label: '自动模式',
    buttonLabel: '自动模式',
    description: '自主运行命令与编辑，仅在危险或破坏性操作前询问',
  },
  {
    mode: 'bypass',
    label: '绕过权限',
    buttonLabel: '绕过权限',
    description: '不再询问，完全访问文件、命令与网络',
  },
];

type SlashAction = { kind: 'action'; command: string; hint: string; run: () => void | Promise<void> };
type SlashSkillAction = { kind: 'skill'; command: string; hint: string; skill: SkillSummary };
type SlashMenuItem = SlashAction | SlashSkillAction;

export function Composer() {
  const t = useT();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const slashMenuRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const heightAnimationReadyRef = useRef(false);
  const heightFrameRef = useRef<number | null>(null);
  const sendReadyRef = useRef(false);
  const sendControls = useAnimationControls();
  const text = useChatStore(state => state.composerText);
  const attachments = useChatStore(state => state.attachments);
  const streaming = useChatStore(state => state.streaming);
  const setText = useChatStore(state => state.setComposerText);
  const send = useChatStore(state => state.send);
  const stop = useChatStore(state => state.stop);
  const addFiles = useChatStore(state => state.addFiles);
  const removeAttachment = useChatStore(state => state.removeAttachment);
  const compactContext = useChatStore(state => state.compactContext);
  const rewindLatest = useChatStore(state => state.rewindLatest);
  const promptSuggestions = useChatStore(state => state.promptSuggestions);
  const applyPromptSuggestion = useChatStore(state => state.applyPromptSuggestion);
  const newSession = useSessionStore(state => state.newSession);
  const selectSession = useSessionStore(state => state.selectSession);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashSkills, setSlashSkills] = useState<SkillSummary[]>([]);
  const [slashActiveIndex, setSlashActiveIndex] = useState(0);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const pendingAttachment = attachments.some(file => file.status === 'parsing');
  const readyAttachmentCount = attachments.filter(file => !file.status || file.status === 'ready').length;
  const sendDisabled = !streaming && (pendingAttachment || (!text.trim() && readyAttachmentCount === 0));
  const sendReady = streaming || !sendDisabled;
  const showPromptSuggestions = promptSuggestions.length > 0 && !text.trim() && !streaming;

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    if (heightFrameRef.current !== null) {
      cancelAnimationFrame(heightFrameRef.current);
      heightFrameRef.current = null;
    }
    const maxHeight = Math.max(156, Math.min(window.innerHeight * 0.38, 320));
    const previousHeight = Math.max(40, textarea.getBoundingClientRect().height || 40);
    textarea.style.transition = heightAnimationReadyRef.current ? 'height 120ms cubic-bezier(0.16, 1, 0.3, 1)' : 'none';
    textarea.style.height = 'auto';
    const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
    const resolvedHeight = Math.max(40, nextHeight);
    const applyOverflow = () => {
      textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden';
    };
    if (heightAnimationReadyRef.current) {
      textarea.style.height = `${previousHeight}px`;
      void textarea.offsetHeight;
      heightFrameRef.current = requestAnimationFrame(() => {
        textarea.style.height = `${resolvedHeight}px`;
        applyOverflow();
        heightFrameRef.current = null;
      });
    } else {
      textarea.style.height = `${resolvedHeight}px`;
      applyOverflow();
    }
    return () => {
      if (heightFrameRef.current !== null) {
        cancelAnimationFrame(heightFrameRef.current);
        heightFrameRef.current = null;
      }
    };
  }, [text]);

  useEffect(() => {
    const becameReady = sendReady && !sendReadyRef.current;
    sendReadyRef.current = sendReady;
    void sendControls.start(
      becameReady
        ? {
            opacity: 1,
            scale: [1, 1.06, 1],
            transition: { opacity: { duration: 0.16 }, scale: { duration: 0.24, ease: [0.16, 1, 0.3, 1] } },
          }
        : {
            opacity: sendReady ? 1 : 0.42,
            scale: 1,
            transition: { duration: 0.16 },
          },
    );
  }, [sendControls, sendReady]);

  useEffect(() => {
    if (!slashOpen || slashSkills.length > 0) return;
    let alive = true;
    void getSkills()
      .then(skills => {
        if (!alive) return;
        setSlashSkills(skills.filter(skill => skill.enabled && skill.userInvocable));
      })
      .catch(() => {
        if (alive) setSlashSkills([]);
      });
    return () => {
      alive = false;
    };
  }, [slashOpen, slashSkills.length]);

  const slashQuery = text.startsWith('/') ? text.slice(1).trim().toLowerCase() : '';
  // 立即执行的内置指令（真接 chatStore/sessionStore 能力，不再是发出去没人理的假文字）。
  const immediateSlashActions: SlashAction[] = [
    {
      kind: 'action',
      command: '/new',
      hint: '开新对话',
      run: async () => {
        const sessionId = await newSession();
        if (sessionId) await selectSession(sessionId);
      },
    },
    { kind: 'action', command: '/compact', hint: '压缩上下文，释放空间', run: () => compactContext() },
    { kind: 'action', command: '/rewind', hint: '撤销上一轮对话', run: () => rewindLatest() },
  ];
  const workflowSlashActions: SlashAction[] = filterSlashWorkflowCommands(slashQuery).map<SlashAction>(workflow => ({
    kind: 'action',
    command: workflow.command,
    hint: workflow.hint,
    run: () => send(workflow.prompt),
  }));
  const matchedSlashActions = [
    ...immediateSlashActions.filter(action => !slashQuery || action.command.slice(1).includes(slashQuery)),
    ...workflowSlashActions,
  ];
  const slashSkillOptions = slashSkills
    .filter(skill => {
      const key = (skill.skillName || skill.id || skill.name).toLowerCase();
      const haystack = `${key} ${skill.name} ${skill.description} ${skill.whenToUse}`.toLowerCase();
      return !slashQuery || haystack.includes(slashQuery);
    })
    .sort((a, b) => slashSkillRank(a) - slashSkillRank(b))
    .slice(0, 8);
  const slashMenuItems = useMemo<SlashMenuItem[]>(
    () => [
      ...matchedSlashActions,
      ...slashSkillOptions.map<SlashSkillAction>(skill => ({
        kind: 'skill',
        command: `/${skill.skillName || skill.id}`,
        hint: skill.description || skill.whenToUse || skill.name,
        skill,
      })),
    ],
    [matchedSlashActions, slashSkillOptions],
  );
  const hasSlashResults = slashMenuItems.length > 0;
  const firstSkillIndex = slashMenuItems.findIndex(item => item.kind === 'skill');

  useEffect(() => {
    setSlashActiveIndex(0);
  }, [slashQuery]);

  useEffect(() => {
    setSlashActiveIndex(current => {
      if (!slashOpen || slashMenuItems.length === 0) return 0;
      return Math.min(Math.max(current, 0), slashMenuItems.length - 1);
    });
  }, [slashMenuItems.length, slashOpen]);

  useEffect(() => {
    if (!slashOpen) return;
    const active = slashMenuRef.current?.querySelector<HTMLElement>(`[data-slash-index="${slashActiveIndex}"]`);
    active?.scrollIntoView({ block: 'nearest' });
  }, [slashActiveIndex, slashOpen]);

  const insertSlashSkill = (command: string) => {
    setText(`${command} `);
    setSlashOpen(false);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.focus();
      const end = el.value.length;
      el.setSelectionRange(end, end); // 光标落在指令后面，方便接着输入任务
    });
  };

  const runSlashAction = (action: { run: () => void | Promise<void> }) => {
    setText('');
    setSlashOpen(false);
    void action.run();
  };

  const applySlashMenuItem = (item: SlashMenuItem | undefined) => {
    if (!item) return;
    if (item.kind === 'skill') {
      insertSlashSkill(item.command);
      return;
    }
    runSlashAction(item);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === '/' && !text.trim()) {
      setSlashOpen(true);
    }
    if (slashOpen) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        setSlashActiveIndex(index => moveSlashSelection(index, slashMenuItems.length, event.key === 'ArrowDown' ? 1 : -1));
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setSlashOpen(false);
        return;
      }
      if ((event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey && slashMenuItems.length > 0) {
        event.preventDefault();
        applySlashMenuItem(slashMenuItems[slashActiveIndex] || slashMenuItems[0]);
        return;
      }
    }
    if (event.key !== 'Enter') return;
    if (event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void send();
  };

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.currentTarget.files;
    if (files?.length) void addFiles(files);
    event.currentTarget.value = '';
  };

  const hasDraggedFiles = (event: DragEvent<HTMLElement>) => Array.from(event.dataTransfer.types).includes('Files');

  const handleDragEnter = (event: DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    setDraggingFiles(true);
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDraggingFiles(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    const related = event.relatedTarget;
    if (related instanceof Node && event.currentTarget.contains(related)) return;
    setDraggingFiles(false);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    setDraggingFiles(false);
    if (event.dataTransfer.files.length) void addFiles(event.dataTransfer.files);
  };

  return (
    <div
      className="composer-wrap"
      data-dragging-files={draggingFiles}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <AnimatePresence>
        {draggingFiles && (
          <motion.div
            className="composer-drop-zone"
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.14 } }}
            transition={{ type: 'spring', stiffness: 360, damping: 28 }}
          >
            <Upload size={18} />
            <span>{t('松开以添加到本次消息')}</span>
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {slashOpen && (
          <motion.div
            id="composer-slash-menu"
            className="slash-menu"
            ref={slashMenuRef}
            role="listbox"
            aria-label={t('斜杠指令')}
            initial={{ y: 8, opacity: 0, scale: 0.96 }}
            animate={{ y: 0, opacity: 1, scale: 1 }}
            exit={{ y: 6, opacity: 0, scale: 0.97, transition: { duration: 0.12 } }}
            transition={{ type: 'spring', stiffness: 400, damping: 28 }}
          >
            {slashMenuItems.map((item, index) => (
              <Fragment key={slashMenuItemKey(item)}>
                {index === firstSkillIndex && <small className="slash-menu-section">{t('技能')}</small>}
                <button
                  id={`slash-option-${index}`}
                  className="slash-skill-option"
                  type="button"
                  role="option"
                  aria-selected={index === slashActiveIndex}
                  data-active={index === slashActiveIndex}
                  data-kind={item.kind}
                  data-slash-index={index}
                  onMouseDown={event => event.preventDefault()}
                  onMouseEnter={() => setSlashActiveIndex(index)}
                  onClick={() => applySlashMenuItem(item)}
                >
                  <span>{item.command}</span>
                  <small>{item.kind === 'action' ? t(item.hint) : item.hint}</small>
                </button>
              </Fragment>
            ))}
            {!hasSlashResults && <small className="slash-menu-empty">{t('无匹配指令')}</small>}
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {showPromptSuggestions && (
          <motion.div
            className="prompt-suggestion-row"
            initial={{ y: 6, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 4, opacity: 0, transition: { duration: 0.12 } }}
            transition={{ type: 'spring', stiffness: 420, damping: 30 }}
          >
            <Sparkles size={13} />
            {promptSuggestions.map(suggestion => (
              <button key={suggestion} type="button" onClick={() => applyPromptSuggestion(suggestion)}>
                {t(suggestion)}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
      {attachments.length > 0 && (
        <div className="attachment-row" aria-live="polite">
          {attachments.map(file => (
            <AttachmentCard key={file.path} file={file} onRemove={removeAttachment} />
          ))}
        </div>
      )}
      <div className="composer">
        {/* 命令模式（斜杠开头、还没空格）时给真实文字染蓝——原生光标，永不错位；进入任务参数即转普通色。 */}
        <textarea
          ref={textareaRef}
          aria-label="Message input"
          aria-controls={slashOpen ? 'composer-slash-menu' : undefined}
          aria-expanded={slashOpen}
          aria-activedescendant={slashOpen && hasSlashResults ? `slash-option-${slashActiveIndex}` : undefined}
          rows={1}
          value={text}
          data-command={/^\/\S*$/.test(text)}
          placeholder={t('让 Metis 在这个项目里开始工作...')}
          onChange={event => {
            setText(event.target.value);
            // 只在「还在敲命令本身」（斜杠开头、且还没空格）时显示菜单；一旦开始输入任务参数就收起。
            setSlashOpen(/^\/\S*$/.test(event.target.value));
          }}
          onFocus={() => {
            heightAnimationReadyRef.current = true;
          }}
          onKeyDown={handleKeyDown}
        />
        <div className="composer-toolbar">
          <div className="composer-toolbar-left">
            <button className="icon-button composer-attach-button" type="button" title={t('添加附件')} onClick={() => fileInputRef.current?.click()}>
              <Plus size={22} />
            </button>
            <ComposerAccessMenu />
          </div>
          <div className="composer-toolbar-right">
            <ComposerModelMenu />
            <motion.button
              className="send-button"
              type="button"
              data-streaming={streaming}
              aria-label={streaming ? t('停止生成') : t('发送消息')}
              title={streaming ? t('停止生成') : t('发送消息')}
              disabled={sendDisabled}
              animate={sendControls}
              whileTap={!sendDisabled ? { scale: 0.9 } : undefined}
              transition={{ type: 'spring', stiffness: 420, damping: 24 }}
              onClick={() => (streaming ? stop() : void send())}
            >
              {streaming ? <Square size={15} /> : <ArrowUp size={20} />}
            </motion.button>
          </div>
        </div>
        <input ref={fileInputRef} type="file" multiple hidden onChange={handleFiles} />
      </div>
    </div>
  );
}

function AttachmentCard({ file, onRemove }: { file: ParsedFile; onRemove: (path: string) => void }) {
  const t = useT();
  const status = file.status || 'ready';
  return (
    <article className="attachment-card" data-status={status}>
      <span className="attachment-thumb" data-kind={file.kind}>
        {file.kind === 'image' && file.dataUrl ? (
          <img src={file.dataUrl} alt="" />
        ) : status === 'error' ? (
          <AlertCircle size={16} />
        ) : file.kind === 'image' ? (
          <ImageIcon size={16} />
        ) : (
          <FileText size={16} />
        )}
      </span>
      <span className="attachment-meta">
        <strong title={file.name}>{file.name}</strong>
        <small>{attachmentStatusText(file, t)}</small>
      </span>
      {status === 'parsing' && <Loader2 className="spin attachment-spinner" size={14} />}
      <button type="button" className="attachment-remove" aria-label={`${t('移除')} ${file.name}`} onClick={() => onRemove(file.path)}>
        <X size={13} />
      </button>
    </article>
  );
}

function slashSkillRank(skill: SkillSummary): number {
  const key = (skill.skillName || skill.id || skill.name).toLowerCase();
  if (key === 'browser') return 0;
  if (key === 'computer') return 1;
  return 2;
}

function slashMenuItemKey(item: SlashMenuItem): string {
  if (item.kind === 'skill') return `skill:${item.skill.id}`;
  return `action:${item.command}`;
}

function attachmentStatusText(file: ParsedFile, t: (zh: string) => string): string {
  if (file.status === 'parsing') return `${formatBytes(file.size)} · ${t('正在解析')}`;
  if (file.status === 'error') return file.error || t('解析失败，可移除后重试');
  const type = file.extension || file.mime || file.kind;
  return `${type} · ${formatBytes(file.size)}${file.truncated ? ` · ${t('已截断')}` : ''}`;
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function ComposerModelMenu() {
  const t = useT();
  const language = useUiStore(state => state.language);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [providers, setProviders] = useState<ProviderProfile[]>([]);

  const load = async () => {
    try {
      const [next, status] = await Promise.all([getSettings(), getProviderStatus()]);
      setSettings(next);
      setProviders(status.providers);
    } catch {
      try { setSettings(await getSettings()); } catch { /* ignore */ }
    }
  };

  useEffect(() => { void load(); }, []);
  useEffect(() => { if (open) void load(); }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: Event) => { if ((event as globalThis.KeyboardEvent).key === 'Escape') setOpen(false); };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey); };
  }, [open]);

  const zh = language === 'zh';
  const effort = settings?.reasoningEffort && settings.reasoningEffort !== 'off' ? settings.reasoningEffort : '';
  // Only the tiers the *current* model actually supports, plus an off switch.
  const effortChoices = useMemo(() => ['off', ...effortLevelsFor(settings?.model || '')], [settings?.model]);
  const effortBadge = effort ? effortLabel(effort, zh) : '';
  const models = useMemo(() => buildComposerModelList(providers, settings), [providers, settings]);

  const applyEffort = async (value: string) => {
    setSaving(true);
    try {
      await updateSettings({ reasoningEffort: value });
      await load();
      window.dispatchEvent(new Event('metis:settings-refresh'));
    } finally { setSaving(false); }
  };
  const applyModel = async (entry: ComposerModelEntry) => {
    setSaving(true);
    try {
      await updateSettings({ backend: entry.providerId, providerId: entry.providerId, baseUrl: entry.baseUrl, model: entry.model });
      await load();
      window.dispatchEvent(new Event('metis:settings-refresh'));
      setOpen(false);
    } finally { setSaving(false); }
  };

  return (
    <div ref={rootRef} className="composer-model-wrap" onPointerDown={event => event.stopPropagation()}>
      <button
        type="button"
        className="composer-model-button"
        onClick={() => setOpen(value => !value)}
        title={settings?.model || t('选择模型')}
      >
        <span className="composer-model-name">{shortModelName(settings?.model || '')}</span>
        {effortBadge && <em className="composer-model-effort">{effortBadge}</em>}
        <ChevronDown size={13} />
      </button>
      {open && (
        <div className="composer-model-menu" role="menu">
          {effortChoices.length > 1 && (
            <>
              <div className="composer-model-section">{t('推理强度')}</div>
              <div className="composer-effort-row">
                {effortChoices.map(level => (
                  <button
                    key={level}
                    type="button"
                    data-active={level === 'off' ? !effort : effort === level}
                    disabled={saving}
                    onClick={() => void applyEffort(level)}
                  >
                    {effortLabel(level, zh)}
                  </button>
                ))}
              </div>
              <div className="composer-model-divider" />
            </>
          )}
          <div className="composer-model-section">{t('模型')}</div>
          <div className="composer-model-list">
            {models.map(entry => (
              <button
                key={entry.id}
                type="button"
                data-active={entry.active}
                disabled={saving}
                onClick={() => void applyModel(entry)}
              >
                <span>{entry.model}</span>
                {entry.active && <Check size={14} />}
              </button>
            ))}
            {models.length === 0 && <p className="composer-model-empty">{t('暂无可用模型')}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function ComposerAccessMenu() {
  const t = useT();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [mode, setMode] = useState<PermissionAccessMode>('auto');
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const active = accessOptions.find(option => option.mode === mode) ?? accessOptions[1];

  useEffect(() => {
    let alive = true;
    void getComposerPermissionMode()
      .then(nextMode => {
        if (alive) setMode(nextMode);
      })
      .catch(() => {
        if (alive) setError(t('权限状态暂不可用'));
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener('pointerdown', handlePointerDown);
    return () => window.removeEventListener('pointerdown', handlePointerDown);
  }, [open]);

  const chooseMode = async (nextMode: PermissionAccessMode) => {
    setSaving(true);
    setError('');
    try {
      const persisted = await setComposerPermissionMode(nextMode);
      setMode(persisted);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const preventComposerFocus = (event: ReactPointerEvent) => {
    event.stopPropagation();
  };

  return (
    <div ref={rootRef} className="composer-access-wrap" onPointerDown={preventComposerFocus}>
      <button
        className="composer-access-button"
        type="button"
        data-mode={mode}
        aria-haspopup="menu"
        aria-expanded={open}
        title={t(active.description)}
        onClick={() => setOpen(value => !value)}
      >
        {saving ? <Loader2 className="spin" size={15} /> : accessIcon(mode)}
        <span>{t(active.buttonLabel)}</span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="composer-access-menu" role="menu">
          {accessOptions.map(option => (
            <button
              key={option.mode}
              type="button"
              role="menuitemradio"
              aria-checked={mode === option.mode}
              data-active={mode === option.mode}
              className="composer-access-option"
              disabled={saving}
              onClick={() => void chooseMode(option.mode)}
            >
              <span className="composer-access-check">{mode === option.mode && <Check size={15} />}</span>
              <span>
                <strong>{t(option.label)}</strong>
                <small>{t(option.description)}</small>
              </span>
            </button>
          ))}
          {error && <p className="composer-access-error">{error}</p>}
        </div>
      )}
    </div>
  );
}

function accessIcon(mode: PermissionAccessMode) {
  if (mode === 'ask') return <ShieldQuestion size={15} />;
  if (mode === 'edit') return <Pencil size={15} />;
  if (mode === 'plan') return <ClipboardList size={15} />;
  if (mode === 'bypass') return <ShieldAlert size={15} />;
  return <Zap size={15} />;
}
