import type { ChatMessage, ChatTokenUsage } from './types';

const MODEL_CONTEXT_LIMITS: Record<string, number> = {
  'deepseek-v4-flash': 1_000_000,
  'deepseek-v4-pro': 1_000_000,
  'deepseek-chat': 128_000,
  'deepseek-coder': 128_000,
  'deepseek-reasoner': 64_000,
  'gpt-4o': 128_000,
  'gpt-4o-mini': 128_000,
  'gpt-4-turbo': 128_000,
  'gpt-4.1': 1_047_576,
  'gpt-4.1-mini': 1_047_576,
  o3: 200_000,
  'o3-mini': 200_000,
  'o4-mini': 200_000,
  'claude-sonnet-4-20250514': 200_000,
  'claude-opus-4-20250514': 200_000,
  'claude-3-5-sonnet': 200_000,
  'gpt-5.5': 1_000_000,
  'gpt-5.4': 1_000_000,
  'gpt-5.4-mini': 1_000_000,
  'codex-auto-review': 1_000_000,
};

const DEFAULT_CONTEXT_LIMIT = 128_000;

export type ContextWindowLevel = 'normal' | 'warning' | 'danger';

export function contextLimitForModel(model: string): number {
  const normalized = String(model || '').trim().toLowerCase();
  if (!normalized) return DEFAULT_CONTEXT_LIMIT;
  if (MODEL_CONTEXT_LIMITS[normalized]) return MODEL_CONTEXT_LIMITS[normalized];
  if (normalized.includes('deepseek-v4')) return 1_000_000;
  if (normalized.includes('gpt-4.1')) return 1_047_576;
  if (normalized.includes('claude')) return 200_000;
  if (normalized.includes('o3') || normalized.includes('o4')) return 200_000;
  return DEFAULT_CONTEXT_LIMIT;
}

export function estimateContextTokens(
  messages: ChatMessage[],
  usage: ChatTokenUsage | null,
): number {
  const messageEstimate = messages.reduce((total, message) => total + estimateTextTokens(message.content), 0);
  return Math.max(messageEstimate, usage?.totalTokens || 0);
}

export function contextWindowLevel(percent: number): ContextWindowLevel {
  if (percent >= 90) return 'danger';
  if (percent >= 70) return 'warning';
  return 'normal';
}

export function contextWindowPercent(used: number, limit: number): number {
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((used / limit) * 100)));
}

export function formatTokenCount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

function estimateTextTokens(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}
