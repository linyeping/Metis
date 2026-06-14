import type { FontFamily, PermissionRule, SettingsSection, TerminalShell } from '../../lib/types';

export const sections: SettingsSection[] = ['appearance', 'conversation', 'model', 'usage', 'network', 'terminal', 'tools', 'desktop', 'about'];

export const fontOptions: Array<{ value: FontFamily; label: string; hint: string }> = [
  { value: 'official-sans', label: '官方 Sans', hint: 'Metis 默认字体栈，英文和中文都优先保证清晰。' },
  { value: 'system', label: '系统默认', hint: '跟随 Windows / 系统 UI 字体。' },
  { value: 'microsoft-yahei', label: '微软雅黑', hint: '中文界面更熟悉，适合长时间阅读。' },
  { value: 'inter', label: 'Inter', hint: '更接近现代开发工具的英文显示。' },
];

export const terminalShellOptions: Array<{ value: TerminalShell; label: string; hint: string }> = [
  { value: 'powershell', label: 'PowerShell', hint: 'Windows 默认推荐，适合 npm、Python、Git 等日常命令。' },
  { value: 'cmd', label: 'cmd', hint: '传统 Windows 命令解释器。' },
  { value: 'bash', label: 'bash', hint: 'Git Bash、WSL 或 PATH 中可用的 bash。' },
  { value: 'sh', label: 'sh', hint: 'POSIX sh，适合轻量脚本命令。' },
  { value: 'shell', label: '系统 Shell', hint: 'Windows 走 ComSpec，macOS/Linux 走 SHELL 环境变量。' },
];

export const permissionActions = ['all', 'allow', 'deny', 'ask'] as const;
export type PermissionActionFilter = (typeof permissionActions)[number];
export type PermissionRuleAction = 'allow' | 'deny' | 'ask';
export type PermissionRuleDraft = {
  tool: string;
  action: PermissionRuleAction;
  argsMatch?: Record<string, string>;
  source?: string;
};

const destructiveTools = new Set(['delete_file', 'remove_file', 'rm', 'shell', 'run_command', 'execute_command']);
const reviewTools = new Set(['write_file', 'edit_file', 'replace_in_file', 'move_file', 'copy_file', 'desktop_control']);

export const permissionPolicyTemplates: Array<PermissionRuleDraft & { id: string; label: string; hint: string }> = [
  {
    id: 'ask-write-path',
    label: '写入前确认',
    hint: 'write_file · path=*',
    tool: 'write_file',
    action: 'ask',
    argsMatch: { path: '*' },
    source: 'policy_template',
  },
  {
    id: 'ask-delete-path',
    label: '删除前确认',
    hint: 'delete_file · path=*',
    tool: 'delete_file',
    action: 'ask',
    argsMatch: { path: '*' },
    source: 'policy_template',
  },
  {
    id: 'deny-env-path',
    label: '拒绝环境密钥',
    hint: '* · path=*.env',
    tool: '*',
    action: 'deny',
    argsMatch: { path: '*.env' },
    source: 'policy_template',
  },
];

export function terminalShellLabel(shell: TerminalShell): string {
  return terminalShellOptions.find(option => option.value === shell)?.label || 'PowerShell';
}

export function actionLabel(action: string): string {
  if (action === 'allow') return '总是允许';
  if (action === 'deny') return '总是拒绝';
  if (action === 'ask') return '每次询问';
  return action || '未知';
}

export function permissionRuleExport(rule: PermissionRule): PermissionRuleDraft {
  return {
    tool: rule.tool,
    action: normalizePermissionAction(rule.action),
    argsMatch: normalizeArgsMatch(rule.argsMatch),
    source: rule.source || 'settings_export',
  };
}

export function parsePermissionImport(text: string): PermissionRuleDraft[] {
  const parsed = JSON.parse(text) as unknown;
  const rows = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === 'object' && Array.isArray((parsed as { rules?: unknown[] }).rules)
      ? (parsed as { rules: unknown[] }).rules
      : [];
  return rows.map(normalizePermissionImportRow).filter((rule): rule is PermissionRuleDraft => Boolean(rule));
}

function normalizePermissionImportRow(row: unknown): PermissionRuleDraft | null {
  if (!row || typeof row !== 'object') return null;
  const source = row as {
    action?: unknown;
    args_match?: unknown;
    argsMatch?: unknown;
    source?: unknown;
    tool?: unknown;
  };
  const tool = typeof source.tool === 'string' ? source.tool.trim() : '';
  if (!tool) return null;
  return {
    tool,
    action: normalizePermissionAction(source.action),
    argsMatch: normalizeArgsMatch(source.argsMatch ?? source.args_match),
    source: typeof source.source === 'string' ? source.source : 'settings_import',
  };
}

function normalizePermissionAction(value: unknown): PermissionRuleAction {
  return value === 'allow' || value === 'deny' || value === 'ask' ? value : 'ask';
}

function normalizeArgsMatch(value: unknown): Record<string, string> {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .map(([key, pattern]) => [key.trim(), String(pattern ?? '').trim()])
      .filter(([key, pattern]) => key && pattern),
  );
}

export function conflictCleanupRuleIds(rules: PermissionRule[]): string[] {
  const groups = new Map<string, PermissionRule[]>();
  for (const rule of rules) {
    const key = `${rule.tool}::${stableArgsMatch(rule.argsMatch)}`;
    groups.set(key, [...(groups.get(key) ?? []), rule]);
  }
  const cleanupIds: string[] = [];
  for (const group of groups.values()) {
    if (group.length <= 1) continue;
    const sorted = group
      .slice()
      .sort((left, right) => (right.updatedAt || right.createdAt || 0) - (left.updatedAt || left.createdAt || 0));
    cleanupIds.push(...sorted.slice(1).map(rule => rule.id));
  }
  return cleanupIds;
}

function stableArgsMatch(argsMatch: Record<string, string>): string {
  return Object.entries(argsMatch || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join('&');
}

export function sourceLabel(source: string): string {
  if (source === 'policy_template') return '策略模板';
  if (source === 'composer_access') return '输入框快捷权限';
  if (source === 'permission_dialog') return '审批弹窗';
  if (source === 'settings') return '手动规则';
  return source || '未知';
}

export function scopeLabel(argsMatch: Record<string, string>): string {
  const entries = Object.entries(argsMatch);
  if (entries.length === 0) return '全部参数';
  return entries.map(([key, value]) => `${key}=${value}`).join(', ');
}

export function toolRisk(tool: string): { level: 'safe' | 'review' | 'destructive'; label: string; hint: string } {
  const normalized = tool.toLowerCase();
  if (destructiveTools.has(normalized) || normalized.includes('delete') || normalized.includes('remove')) {
    return { level: 'destructive', label: '高风险', hint: '可能删除、运行命令或改变系统状态' };
  }
  if (reviewTools.has(normalized) || normalized.includes('write') || normalized.includes('edit')) {
    return { level: 'review', label: '需复核', hint: '可能修改工作区内容' };
  }
  return { level: 'safe', label: '低风险', hint: '通常是读取或查询类工具' };
}

export function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value ?? '');
  }
}

export function safeJsonCompact(value: unknown): string {
  try {
    return JSON.stringify(value ?? {});
  } catch {
    return String(value ?? '');
  }
}

export function formatInteger(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0';
  return Math.round(value).toLocaleString();
}

export function formatMoneyValue(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0';
  if (value >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function formatSettingsTokenCount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return 'unknown';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

export function formatTime(seconds: number): string {
  if (!seconds) return '刚刚';
  return new Date(seconds * 1000).toLocaleString();
}
