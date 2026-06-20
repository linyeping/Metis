// Per-model reasoning-effort tiers (mirror of backend/runtime/reasoning_tiers.py).
// Keep in sync with the backend registry. The composer shows only the tiers a
// model supports; an "off" switch is prepended by the UI.

const TIERS: Array<[RegExp, string[]]> = [
  [/gpt-?5\.(5|4)/i, ['low', 'medium', 'high', 'xhigh']],
  [/gpt-?5\.2/i, ['minimal', 'low', 'medium', 'high', 'xhigh']],
  [/gpt-?5|(\b|[-_])o[1345](\b|[-_])/i, ['minimal', 'low', 'medium', 'high']],
  [/claude|opus|sonnet|haiku|fable/i, ['low', 'medium', 'high', 'xhigh', 'max']],
  [/gemini/i, ['low', 'medium', 'high']],
  // DeepSeek reasoners only (v4+, r1/reasoner) — not deepseek-chat.
  [/deepseek[-_]?(v[4-9]|r\d|reason)/i, ['low', 'high', 'max']],
  [/\b(qwq|glm-?z|grok.*reason)/i, ['low', 'medium', 'high']],
];

// Unknown / non-reasoning models => no tiers (don't offer reasoning effort).
const DEFAULT_TIERS: string[] = [];

export function effortLevelsFor(model: string): string[] {
  const name = (model || '').trim();
  if (!name) return [...DEFAULT_TIERS];
  for (const [pattern, levels] of TIERS) {
    if (pattern.test(name)) return [...levels];
  }
  return [...DEFAULT_TIERS];
}

export const EFFORT_LABELS: Record<string, { zh: string; en: string }> = {
  off: { zh: '关', en: 'Off' },
  minimal: { zh: '极简', en: 'Minimal' },
  low: { zh: '低', en: 'Low' },
  medium: { zh: '中', en: 'Medium' },
  high: { zh: '高', en: 'High' },
  xhigh: { zh: '超高', en: 'X-High' },
  max: { zh: '极致', en: 'Max' },
};

export function effortLabel(level: string, zh: boolean): string {
  const meta = EFFORT_LABELS[level];
  if (meta) return zh ? meta.zh : meta.en;
  return level;
}
