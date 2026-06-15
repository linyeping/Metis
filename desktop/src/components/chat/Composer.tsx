import {
  AlertCircle,
  ArrowUp,
  Check,
  ChevronDown,
  FileText,
  Image as ImageIcon,
  Loader2,
  Plus,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  Square,
  Upload,
  X,
} from 'lucide-react';
import { AnimatePresence, motion, useAnimationControls } from 'framer-motion';
import {
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { getComposerPermissionMode, getSkills, setComposerPermissionMode } from '../../lib/api';
import type { ParsedFile, PermissionAccessMode, SkillSummary } from '../../lib/types';
import { useChatStore } from '../../store/chatStore';
import { useSessionStore } from '../../store/sessionStore';
import { useT } from '../../hooks/useT';

const accessOptions: Array<{
  mode: PermissionAccessMode;
  label: string;
  buttonLabel: string;
  description: string;
}> = [
  {
    mode: 'ask',
    label: '请求批准',
    buttonLabel: '请求批准',
    description: '编辑外部文件和使用互联网时始终询问',
  },
  {
    mode: 'auto',
    label: '替我审批',
    buttonLabel: '替我审批',
    description: '仅对检测到的风险操作请求批准',
  },
  {
    mode: 'full',
    label: '完全访问权限',
    buttonLabel: '完全访问',
    description: '可不受限制地访问互联网和您电脑上的任何文件',
  },
];

export function Composer() {
  const t = useT();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
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
  const newSession = useSessionStore(state => state.newSession);
  const selectSession = useSessionStore(state => state.selectSession);
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashSkills, setSlashSkills] = useState<SkillSummary[]>([]);
  const [draggingFiles, setDraggingFiles] = useState(false);
  const pendingAttachment = attachments.some(file => file.status === 'parsing');
  const readyAttachmentCount = attachments.filter(file => !file.status || file.status === 'ready').length;
  const sendDisabled = !streaming && (pendingAttachment || (!text.trim() && readyAttachmentCount === 0));
  const sendReady = streaming || !sendDisabled;

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
  const slashActions: Array<{ command: string; hint: string; run: () => void | Promise<void> }> = [
    {
      command: '/new',
      hint: '开新对话',
      run: async () => {
        const sessionId = await newSession();
        if (sessionId) await selectSession(sessionId);
      },
    },
    { command: '/compact', hint: '压缩上下文，释放空间', run: () => compactContext() },
    { command: '/rewind', hint: '撤销上一轮对话', run: () => rewindLatest() },
  ];
  const matchedSlashActions = slashActions.filter(action => !slashQuery || action.command.slice(1).includes(slashQuery));
  const slashSkillOptions = slashSkills
    .filter(skill => {
      const key = (skill.skillName || skill.id || skill.name).toLowerCase();
      const haystack = `${key} ${skill.name} ${skill.description} ${skill.whenToUse}`.toLowerCase();
      return !slashQuery || haystack.includes(slashQuery);
    })
    .sort((a, b) => slashSkillRank(a) - slashSkillRank(b))
    .slice(0, 8);
  const hasSlashResults = matchedSlashActions.length > 0 || slashSkillOptions.length > 0;

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

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === '/' && !text.trim()) {
      setSlashOpen(true);
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
          className="slash-menu"
          initial={{ y: 8, opacity: 0, scale: 0.96 }}
          animate={{ y: 0, opacity: 1, scale: 1 }}
          exit={{ y: 6, opacity: 0, scale: 0.97, transition: { duration: 0.12 } }}
          transition={{ type: 'spring', stiffness: 400, damping: 28 }}
        >
          {matchedSlashActions.map(action => (
            <button className="slash-skill-option" key={action.command} type="button" onClick={() => runSlashAction(action)}>
              <span>{action.command}</span>
              <small>{t(action.hint)}</small>
            </button>
          ))}
          {slashSkillOptions.length > 0 && <small className="slash-menu-section">{t('技能')}</small>}
          {slashSkillOptions.map(skill => {
            const command = `/${skill.skillName || skill.id}`;
            return (
              <button className="slash-skill-option" key={skill.id} type="button" onClick={() => insertSlashSkill(command)}>
                <span>{command}</span>
                <small>{skill.description || skill.whenToUse || skill.name}</small>
              </button>
            );
          })}
          {!hasSlashResults && <small className="slash-menu-empty">{t('无匹配指令')}</small>}
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
  if (mode === 'full') return <ShieldAlert size={15} />;
  return <ShieldCheck size={15} />;
}
