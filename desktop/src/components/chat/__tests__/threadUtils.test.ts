import { isValidElement } from 'react';
import { describe, expect, it } from 'vitest';
import { toolProgressText, toolStatusIcon } from '../threadUtils';

describe('threadUtils tool progress', () => {
  it('uses the deep research running copy and atom icon class', () => {
    const icon = toolStatusIcon('web_research', 'running');

    expect(toolProgressText('web_research', 'running')).toBe('正在深度研究...');
    expect(isValidElement(icon)).toBe(true);
    expect((icon as { props: { className?: string } }).props.className).toBe('atom-orbit-spin');
  });
});
