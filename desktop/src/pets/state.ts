import type { ChatSubagentEvent, PetAnimationState, RuntimeStatus } from '../lib/types';

export type PetStateInput = {
  compacting: boolean;
  error: string | null;
  runtimeStatus: RuntimeStatus | null;
  streaming: boolean;
  subagents: ChatSubagentEvent[];
};

const WAITING_PATTERN = /(permission|approval|waiting|confirm|授权|审批|确认|等待)/i;
const REVIEW_PATTERN = /(review|verify|validation|test|check|audit|审查|验证|测试|检查)/i;

export function derivePetState(input: PetStateInput): PetAnimationState {
  const statusText = [
    input.runtimeStatus?.phase,
    input.runtimeStatus?.display,
    input.runtimeStatus?.message,
    input.runtimeStatus?.toolName,
  ].filter(Boolean).join(' ');

  if (input.runtimeStatus?.severity === 'error' || input.error) return 'failed';
  if (WAITING_PATTERN.test(statusText)) return 'waiting';
  if (input.compacting || REVIEW_PATTERN.test(statusText)) return 'review';
  if (input.subagents.some(agent => agent.status === 'waiting_permission')) return 'waiting';
  if (input.subagents.some(agent => agent.status === 'running')) return 'review';
  if (input.streaming || input.runtimeStatus?.severity === 'working') return 'running';
  if (input.runtimeStatus?.severity === 'done') return 'jumping';
  return 'idle';
}
