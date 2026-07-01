/**
 * PermissionWarningDialog
 * 高风险权限模式确认弹窗（Cowork Act / Code 自动模式 / Code 绕过权限）
 * 深色浅色双模式适配，通过 CSS 变量自动跟随主题。
 */
import { AlertTriangle, Shield, Zap } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { PermissionAccessMode } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';

export interface PermissionWarningConfig {
  mode: PermissionAccessMode;
  /** 'cowork' | 'code' */
  surface: string;
}

interface Props {
  config: PermissionWarningConfig;
  onConfirm: () => void;
  onCancel: () => void;
}

// ── 文案定义 ────────────────────────────────────────────────────────────────

interface WarningContent {
  icon: typeof AlertTriangle;
  tone: 'warning' | 'danger';
  title: string;
  titleEn: string;
  lead: string;
  leadEn: string;
  bullets: string[];
  bulletsEn: string[];
  confirmZh: string;
  confirmEn: string;
}

const WARNINGS: Partial<Record<PermissionAccessMode, WarningContent>> = {
  bypass: {
    icon: Shield,
    tone: 'danger',
    title: '绕过权限模式',
    titleEn: 'Bypass Permissions',
    lead: '这是权限最高的模式。开启后，Metis 将对文件、命令和网络不做任何访问拦截，所有操作直接执行。',
    leadEn: 'This is the highest-permission mode. Metis will execute all file, command, and network operations without any access checks.',
    bullets: [
      '文件读写、删除操作将直接执行，不再询问',
      '终端命令无限制运行，包括系统级命令',
      '网络请求和外部服务直接访问',
      '建议仅在完全隔离的测试环境中使用',
    ],
    bulletsEn: [
      'File operations including deletion execute without confirmation',
      'Terminal commands run unrestricted, including system-level commands',
      'Network requests and external services accessed directly',
      'Recommended only in fully isolated test environments',
    ],
    confirmZh: '我了解风险，继续',
    confirmEn: 'I understand the risks',
  },
  auto: {
    icon: Zap,
    tone: 'warning',
    title: '自动模式',
    titleEn: 'Auto Mode',
    lead: 'Metis 将自主运行命令和编辑文件，仅在检测到危险或不可逆操作时才会暂停。',
    leadEn: 'Metis will autonomously run commands and edit files, only pausing for dangerous or irreversible operations.',
    bullets: [
      '文件编辑、创建和构建命令自动执行',
      '终端命令无需逐步确认',
      '破坏性操作（如删除）前仍会询问',
      '建议在有版本控制保护的项目中使用',
    ],
    bulletsEn: [
      'File edits, creation, and build commands execute automatically',
      'Terminal commands run without step-by-step confirmation',
      'Destructive operations (e.g. delete) will still prompt',
      'Recommended for projects with version control protection',
    ],
    confirmZh: '明白了，开启自动模式',
    confirmEn: 'Enable Auto Mode',
  },
};

// Cowork 的 bypass/act 模式复用独立文案
const COWORK_ACT_WARNING: WarningContent = {
  icon: Zap,
  tone: 'warning',
  title: '直接执行模式',
  titleEn: 'Act Mode',
  lead: '开启后，Metis 将连续执行每一步操作，不再逐步询问你的许可。',
  leadEn: 'In Act mode, Metis executes each step continuously without pausing to ask for your approval.',
  bullets: [
    '文件修改、运行命令、网络请求直接执行',
    '多步骤任务将连续推进直到完成',
    '你随时可以点击"停止"中断运行',
    '建议任务目标明确时使用，避免模糊指令',
  ],
  bulletsEn: [
    'File changes, commands, and network requests execute directly',
    'Multi-step tasks run continuously until completion',
    'You can click Stop at any time to interrupt',
    'Best used when the goal is clear and well-defined',
  ],
  confirmZh: '我了解风险，开始执行',
  confirmEn: 'Start executing',
};

// ── 组件 ────────────────────────────────────────────────────────────────────

export function PermissionWarningDialog({ config, onConfirm, onCancel }: Props) {
  const language = useUiStore(state => state.language);
  const confirmRef = useRef<HTMLButtonElement>(null);

  // 取文案
  const isCoworkAct = config.surface === 'cowork' && config.mode === 'bypass';
  const content: WarningContent | undefined =
    isCoworkAct ? COWORK_ACT_WARNING : WARNINGS[config.mode];

  useEffect(() => {
    const timer = window.setTimeout(() => confirmRef.current?.focus(), 30);
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', handleKey);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('keydown', handleKey);
    };
  }, [onCancel]);

  if (!content) return null;

  const Icon = content.icon;
  const isZh = language !== 'en';
  const title   = isZh ? content.title   : content.titleEn;
  const lead    = isZh ? content.lead    : content.leadEn;
  const bullets = isZh ? content.bullets : content.bulletsEn;
  const confirmLabel = isZh ? content.confirmZh : content.confirmEn;
  const cancelLabel  = isZh ? '取消'             : 'Cancel';

  return (
    <div
      className="perm-warning-overlay"
      role="presentation"
      onClick={e => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        className="perm-warning-dialog"
        data-tone={content.tone}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="perm-warn-title"
        aria-describedby="perm-warn-body"
      >
        {/* 头部 */}
        <div className="perm-warn-header">
          <span className="perm-warn-icon-wrap" data-tone={content.tone}>
            <Icon size={20} strokeWidth={1.8} />
          </span>
          <h2 id="perm-warn-title">{title}</h2>
        </div>

        {/* 正文 */}
        <div className="perm-warn-body" id="perm-warn-body">
          <p className="perm-warn-lead">{lead}</p>
          <ul className="perm-warn-bullets">
            {bullets.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        </div>

        {/* 底部按钮 */}
        <div className="perm-warn-footer">
          <button
            className="perm-warn-btn-cancel"
            type="button"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            className="perm-warn-btn-confirm"
            data-tone={content.tone}
            type="button"
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 判断给定模式是否需要警告 */
export function modeNeedsWarning(mode: PermissionAccessMode, surface: string): boolean {
  if (surface === 'cowork' && mode === 'bypass') return true;
  if (surface === 'code'   && (mode === 'auto' || mode === 'bypass')) return true;
  return false;
}
