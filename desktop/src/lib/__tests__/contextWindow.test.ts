import { describe, it, expect } from 'vitest';
import {
  contextLimitForModel,
  contextWindowLevel,
  contextWindowPercent,
  formatTokenCount,
} from '../contextWindow';

describe('contextLimitForModel', () => {
  it('returns exact match for known models', () => {
    expect(contextLimitForModel('deepseek-v4-flash')).toBe(1_000_000);
    expect(contextLimitForModel('gpt-4o')).toBe(128_000);
    expect(contextLimitForModel('claude-sonnet-4-20250514')).toBe(200_000);
  });

  it('returns default for unknown models', () => {
    expect(contextLimitForModel('unknown-model')).toBe(128_000);
    expect(contextLimitForModel('')).toBe(128_000);
  });

  it('matches by substring for model families', () => {
    expect(contextLimitForModel('deepseek-v4-something')).toBe(1_000_000);
    expect(contextLimitForModel('claude-3-opus')).toBe(200_000);
    expect(contextLimitForModel('gpt-4.1-nano')).toBe(1_047_576);
  });

  it('handles case insensitive input', () => {
    expect(contextLimitForModel('GPT-4O')).toBe(128_000);
    expect(contextLimitForModel('DEEPSEEK-V4-FLASH')).toBe(1_000_000);
  });
});

describe('contextWindowLevel', () => {
  it('returns normal below 70%', () => {
    expect(contextWindowLevel(0)).toBe('normal');
    expect(contextWindowLevel(50)).toBe('normal');
    expect(contextWindowLevel(69)).toBe('normal');
  });

  it('returns warning between 70-90%', () => {
    expect(contextWindowLevel(70)).toBe('warning');
    expect(contextWindowLevel(85)).toBe('warning');
    expect(contextWindowLevel(89)).toBe('warning');
  });

  it('returns danger at 90%+', () => {
    expect(contextWindowLevel(90)).toBe('danger');
    expect(contextWindowLevel(100)).toBe('danger');
  });
});

describe('contextWindowPercent', () => {
  it('calculates percentage correctly', () => {
    expect(contextWindowPercent(50_000, 100_000)).toBe(50);
    expect(contextWindowPercent(128_000, 128_000)).toBe(100);
  });

  it('clamps to 0-100 range', () => {
    expect(contextWindowPercent(0, 100_000)).toBe(0);
    expect(contextWindowPercent(200_000, 100_000)).toBe(100);
  });

  it('handles edge cases', () => {
    expect(contextWindowPercent(0, 0)).toBe(0);
    expect(contextWindowPercent(NaN, 100_000)).toBe(0);
    expect(contextWindowPercent(100, NaN)).toBe(0);
  });
});

describe('formatTokenCount', () => {
  it('formats small numbers as-is', () => {
    expect(formatTokenCount(42)).toBe('42');
    expect(formatTokenCount(999)).toBe('999');
  });

  it('formats thousands with k suffix', () => {
    expect(formatTokenCount(1_000)).toBe('1.0k');
    expect(formatTokenCount(128_000)).toBe('128.0k');
  });

  it('formats millions with M suffix', () => {
    expect(formatTokenCount(1_000_000)).toBe('1.0M');
    expect(formatTokenCount(1_500_000)).toBe('1.5M');
  });

  it('handles zero and invalid values', () => {
    expect(formatTokenCount(0)).toBe('0');
    expect(formatTokenCount(-5)).toBe('0');
    expect(formatTokenCount(NaN)).toBe('0');
  });
});
