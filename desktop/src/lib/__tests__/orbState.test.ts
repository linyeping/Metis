import { describe, expect, it } from 'vitest';
import type { RuntimeStatus } from '../types';
import { assistantRuntimeStatus, orbSpeedForRepositoryDemo, orbStateForRuntime } from '../orbState';

function status(input: Partial<RuntimeStatus>): RuntimeStatus {
  return {
    phase: 'tool_running',
    message: '',
    display: '',
    severity: 'working',
    toolName: '',
    hint: '',
    recoverable: false,
    ...input,
  };
}

describe('orbStateForRuntime', () => {
  it('maps model preparation and context work to solving', () => {
    expect(orbStateForRuntime(status({ phase: 'llm_request' }))).toBe('solving');
    expect(orbStateForRuntime(status({ phase: 'compact_started' }))).toBe('solving');
  });

  it('maps streamed answers to composing', () => {
    expect(orbStateForRuntime(status({ phase: 'streaming' }))).toBe('composing');
  });

  it('lets search activity override the generic tool state', () => {
    expect(orbStateForRuntime(status({ toolName: 'web_search' }))).toBe('searching');
    expect(orbStateForRuntime(status({ display: '正在运行 deep_research_run' }))).toBe('searching');
  });

  it('uses working for ordinary tools', () => {
    expect(orbStateForRuntime(status({ toolName: 'read_file' }))).toBe('working');
  });

  it('uses transparent, user-facing copy inside the assistant turn', () => {
    const result = assistantRuntimeStatus(status({ phase: 'llm_request', display: '连接模型中...' }));
    expect(result.display).toBe('正在理解你的要求');
    expect(result.display).not.toContain('连接模型');
  });

  it('matches the repository 64px demo speed while rendering inline at 20px', () => {
    expect(orbSpeedForRepositoryDemo('working', 20)).toBeCloseTo(1.885 / 3.9);
    expect(orbSpeedForRepositoryDemo('composing', 20)).toBeCloseTo(2.34 / 3.12);
    expect(orbSpeedForRepositoryDemo('working', 64)).toBe(1);
  });
});
