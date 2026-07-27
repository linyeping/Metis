import { resolvePreset, type OrbSize, type OrbState } from 'thinking-orbs';
import type { RuntimeStatus } from './types';

const SEARCH_ACTIVITY = /(?:^|[_./-])(search|research)(?:$|[_./-])|web_search|web_research|deep_research|fetch_content/i;

export function orbSpeedForRepositoryDemo(state: OrbState, size: OrbSize): number {
  const currentSpeed = resolvePreset(state, size).speed;
  if (currentSpeed <= 0) return 1;
  return resolvePreset(state, 64).speed / currentSpeed;
}

export function orbStateForRuntime(status: RuntimeStatus): OrbState {
  const phase = String(status.phase || '').trim().toLowerCase();
  const activity = [status.toolName, status.message, status.display, status.hint]
    .filter(Boolean)
    .join(' ');

  if (SEARCH_ACTIVITY.test(activity)) return 'searching';
  if (phase === 'streaming') return 'composing';
  if (
    phase === 'starting'
    || phase === 'llm_request'
    || phase === 'compact_started'
    || phase === 'steering_applied'
    || phase === 'queued_followup_started'
  ) {
    return 'solving';
  }
  return 'working';
}

export function assistantRuntimeStatus(status: RuntimeStatus): RuntimeStatus {
  const phase = String(status.phase || '').trim().toLowerCase();
  const orbState = orbStateForRuntime(status);
  const display =
    phase === 'starting' ? '正在准备工作' :
      phase === 'llm_request' ? '正在理解你的要求' :
        phase === 'compact_started' ? '正在整理上下文' :
          phase === 'streaming' ? '正在组织回答' :
            phase === 'steering_applied' ? '正在根据你的补充调整' :
              phase === 'queued_followup_started' ? '正在处理下一条消息' :
                orbState === 'searching' ? '正在检索并筛选相关资料' :
                  status.display || '正在工作';
  const hint =
    phase === 'streaming' ? '已收到部分结果，正在整理成完整回答' :
      orbState === 'searching' ? '会将有用的信息带回当前回复' :
        status.hint;
  return { ...status, display, hint };
}
