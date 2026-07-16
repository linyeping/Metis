import { describe, expect, it } from 'vitest';
import { derivePetState } from '../state';

const base = {
  compacting: false,
  error: null,
  runtimeStatus: null,
  streaming: false,
  subagents: [],
};

describe('derivePetState', () => {
  it('maps permissions, reviews, failures, and active work to atlas states', () => {
    expect(derivePetState(base)).toBe('idle');
    expect(derivePetState({ ...base, streaming: true })).toBe('running');
    expect(derivePetState({ ...base, streaming: true, runtimeStatus: { phase: 'waiting_permission', display: '', message: '', severity: 'warning', toolName: '', hint: '', recoverable: true } })).toBe('waiting');
    expect(derivePetState({ ...base, streaming: true, runtimeStatus: { phase: 'verify', display: '检查结果', message: '', severity: 'working', toolName: '', hint: '', recoverable: true } })).toBe('review');
    expect(derivePetState({ ...base, error: 'network failed' })).toBe('failed');
  });
});
