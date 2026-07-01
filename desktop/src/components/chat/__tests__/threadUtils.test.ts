import { isValidElement } from 'react';
import { describe, expect, it } from 'vitest';
import { toolKindGlyph, toolProgressText, toolStatusIcon } from '../threadUtils';

describe('threadUtils tool progress', () => {
  it('uses the deep research running copy and atom icon class', () => {
    const icon = toolStatusIcon('web_research', 'running');

    expect(toolProgressText('web_research', 'running')).toBe('正在深度研究...');
    expect(isValidElement(icon)).toBe(true);
    expect((icon as { props: { className?: string } }).props.className).toBe('atom-orbit-spin');
  });

  it('maps common tools to the expected glyphs', () => {
    const fileIcon = toolKindGlyph('read_file');
    const terminalIcon = toolKindGlyph('execute_bash_command');
    const docxIcon = toolKindGlyph('docx_create');

    expect(isValidElement(fileIcon)).toBe(true);
    expect((fileIcon as { props: { className?: string } }).props.className).toBe('tool-kind-mark');
    expect(isValidElement(terminalIcon)).toBe(true);
    expect((terminalIcon as { props: { className?: string } }).props.className).toBe('tool-kind-mark');
    expect(isValidElement(docxIcon)).toBe(true);
    expect((docxIcon as { props: { className?: string; 'data-badge'?: string } }).props.className).toBe('tool-kind-mark');
    expect((docxIcon as { props: { 'data-badge'?: string } }).props['data-badge']).toBe('DOC');
  });
});
