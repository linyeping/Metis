export type SlashWorkflowId = 'simplify' | 'skillify' | 'stuck' | 'remember' | 'update-config';

export interface SlashWorkflowCommand {
  id: SlashWorkflowId;
  command: `/${SlashWorkflowId}`;
  hint: string;
  keywords: string[];
  prompt: string;
}

export const slashWorkflowCommands: SlashWorkflowCommand[] = [
  {
    id: 'simplify',
    command: '/simplify',
    hint: '清理最近 diff，删掉重复和临时痕迹',
    keywords: ['diff', 'cleanup', 'clean', '简化', '清理'],
    prompt: [
      '执行 /simplify 工作流。',
      '',
      '目标：检查最近的代码 diff，自动清理不必要的复杂度、重复逻辑、调试残留、临时命名和明显可以收束的实现，但不要改变用户明确要求的行为。',
      '',
      '要求：',
      '1. 先查看 git status 和相关 diff。',
      '2. 只修改与最近 diff 直接相关的文件。',
      '3. 保留用户已有改动，不回退无关文件。',
      '4. 清理后运行最小必要验证。',
      '5. 最后总结清理了什么、保留了什么、验证结果是什么。',
    ].join('\n'),
  },
  {
    id: 'skillify',
    command: '/skillify',
    hint: '把最近流程沉淀成可复用技能',
    keywords: ['skill', 'workflow', '沉淀', '技能'],
    prompt: [
      '执行 /skillify 工作流。',
      '',
      '目标：把当前会话或最近一次成功流程沉淀成 Metis 技能，方便以后复用。',
      '',
      '要求：',
      '1. 先从最近上下文提炼任务触发条件、步骤、工具边界和验收方式。',
      '2. 如果已有相近技能，优先更新而不是重复创建。',
      '3. 生成或更新 `.metis/skills/<skill-name>/SKILL.md`，使用 Metis 自己的工具名和约束。',
      '4. 技能内容要简洁、可执行、避免写入一次性路径或临时日志。',
      '5. 最后说明技能路径、触发场景和使用方式。',
    ].join('\n'),
  },
  {
    id: 'stuck',
    command: '/stuck',
    hint: '诊断卡死、循环、工具失败和后台状态',
    keywords: ['diagnose', 'stuck', 'loop', '卡死', '诊断'],
    prompt: [
      '执行 /stuck 工作流。',
      '',
      '目标：一键诊断当前任务为什么卡住、慢、循环、工具活动不结束或看起来没有进展。',
      '',
      '要求：',
      '1. 检查最近工具调用、运行状态、后台日志、错误事件和当前 todo/plan。',
      '2. 判断是模型没规划好、工具失败、权限阻塞、后台进程未结束、端口/环境问题，还是 UI 状态未刷新。',
      '3. 不要先大改代码；先给出诊断结论和最小修复路径。',
      '4. 如果能安全修复，就修复并验证；如果需要用户确认，明确说明原因。',
      '5. 输出“原因 / 证据 / 修复 / 后续预防”。',
    ].join('\n'),
  },
  {
    id: 'remember',
    command: '/remember',
    hint: '整理项目记忆和偏好',
    keywords: ['memory', 'project profile', 'remember', '记忆', '偏好'],
    prompt: [
      '执行 /remember 工作流。',
      '',
      '目标：整理并更新当前项目记忆，让 Metis 下次进入项目时知道关键上下文。',
      '',
      '要求：',
      '1. 总结项目结构、启动命令、测试命令、常见端口、权限偏好、devlog 习惯和 release 规则。',
      '2. 只记录长期有用的信息，不记录一次性聊天碎片。',
      '3. 更新合适的项目记忆文件或 Project Profile；如果已有内容，追加/整合，不粗暴覆盖。',
      '4. 不提交本地运行缓存、日志或临时文件。',
      '5. 最后列出新增/更新的记忆条目和文件路径。',
    ].join('\n'),
  },
  {
    id: 'update-config',
    command: '/update-config',
    hint: '检查权限、hook、工具和设置',
    keywords: ['config', 'permission', 'hook', 'settings', '配置', '权限'],
    prompt: [
      '执行 /update-config 工作流。',
      '',
      '目标：检查并整理当前项目的 Metis 配置，包括权限、hook、工具配置、Project Profile 和常用设置。',
      '',
      '要求：',
      '1. 先读取现有配置和权限状态，说明当前设计。',
      '2. 找出过宽、过窄、重复、过期或容易误触发的配置。',
      '3. 提出最小修改方案；涉及危险权限、外部账号、删除或全局配置时先说明风险。',
      '4. 可安全修改的项目级配置可以直接更新，并保留可回滚说明。',
      '5. 最后输出变更摘要、风险等级和验证方式。',
    ].join('\n'),
  },
];

export function filterSlashWorkflowCommands(query: string): SlashWorkflowCommand[] {
  const needle = normalizeSlashQuery(query);
  if (!needle) return slashWorkflowCommands;
  return slashWorkflowCommands.filter(command => {
    const haystack = [command.command.slice(1), command.hint, command.id, ...command.keywords].join(' ').toLowerCase();
    return haystack.includes(needle);
  });
}

export function normalizeSlashQuery(value: string): string {
  return String(value || '').trim().replace(/^\//, '').toLowerCase();
}

export function moveSlashSelection(current: number, count: number, delta: 1 | -1): number {
  if (count <= 0) return -1;
  if (current < 0 || current >= count) return delta > 0 ? 0 : count - 1;
  return (current + delta + count) % count;
}

