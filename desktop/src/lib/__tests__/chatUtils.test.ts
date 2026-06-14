import { describe, it, expect } from 'vitest';
import { contentToText, buildUserContent, formatBytes, compactPythonPath } from '../chatUtils';
import type { ParsedFile } from '../types';

function makeParsedFile(overrides: Partial<ParsedFile> = {}): ParsedFile {
  return {
    path: '/tmp/test.txt',
    name: 'test.txt',
    extension: '.txt',
    size: 100,
    kind: 'document',
    mime: 'text/plain',
    text: 'file content here',
    status: 'ready',
    truncated: false,
    ...overrides,
  };
}

describe('contentToText', () => {
  it('returns string content as-is', () => {
    expect(contentToText('hello world')).toBe('hello world');
  });

  it('returns empty string for null/undefined', () => {
    expect(contentToText(null)).toBe('');
    expect(contentToText(undefined)).toBe('');
  });

  it('extracts text from multimodal array with text blocks', () => {
    const content = [
      { type: 'text', text: 'Hello' },
      { type: 'text', text: 'World' },
    ];
    expect(contentToText(content)).toBe('Hello\nWorld');
  });

  it('replaces image_url blocks with placeholder', () => {
    const content = [
      { type: 'text', text: 'Check this image' },
      { type: 'image_url', image_url: { url: 'data:image/png;base64,abc' } },
    ];
    expect(contentToText(content)).toBe('Check this image\n[图片附件]');
  });

  it('handles mixed string and object blocks', () => {
    const content = ['raw string', { type: 'text', text: 'typed text' }];
    expect(contentToText(content)).toBe('raw string\ntyped text');
  });

  it('handles objects with content field', () => {
    const content = [{ content: 'via content field' }];
    expect(contentToText(content)).toBe('via content field');
  });

  it('handles objects with text field but no type', () => {
    const content = [{ text: 'untyped text' }];
    expect(contentToText(content)).toBe('untyped text');
  });

  it('falls back to JSON.stringify for non-array objects', () => {
    const content = { custom: 'data' };
    expect(contentToText(content)).toBe(JSON.stringify(content, null, 2));
  });

  it('returns empty array as JSON', () => {
    expect(contentToText([])).toBe('[]');
  });
});

describe('buildUserContent', () => {
  it('returns plain text when no attachments', () => {
    expect(buildUserContent('hello', [])).toBe('hello');
  });

  it('builds multimodal blocks for image attachments', () => {
    const img = makeParsedFile({
      kind: 'image',
      name: 'photo.png',
      mime: 'image/png',
      dataUrl: 'data:image/png;base64,abc123',
    });
    const result = buildUserContent('Analyze this', [img]);
    expect(Array.isArray(result)).toBe(true);
    const blocks = result as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toEqual({ type: 'text', text: 'Analyze this' });
    expect(blocks[1]).toEqual({ type: 'image_url', image_url: { url: 'data:image/png;base64,abc123' } });
  });

  it('builds multimodal blocks with mixed image and document attachments', () => {
    const img = makeParsedFile({ kind: 'image', name: 'img.png', dataUrl: 'data:image/png;base64,x' });
    const doc = makeParsedFile({ kind: 'document', name: 'readme.md', text: 'doc content' });
    const result = buildUserContent('', [img, doc]);
    expect(Array.isArray(result)).toBe(true);
    const blocks = result as Array<Record<string, unknown>>;
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toEqual({ type: 'image_url', image_url: { url: 'data:image/png;base64,x' } });
    expect(blocks[1]).toEqual({ type: 'text', text: '[Attachment: readme.md]\ndoc content' });
  });

  it('concatenates text-only attachments as string', () => {
    const doc = makeParsedFile({ name: 'file.py', text: 'print("hi")' });
    const result = buildUserContent('Run this', [doc]);
    expect(typeof result).toBe('string');
    expect(result).toContain('Run this');
    expect(result).toContain('[Attachment: file.py]');
    expect(result).toContain('print("hi")');
  });

  it('uses fallback text when no user text and text-only attachments', () => {
    const doc = makeParsedFile({ name: 'data.csv', text: '' });
    const result = buildUserContent('', [doc]);
    expect(typeof result).toBe('string');
    expect(result).toContain('Please use the attached files.');
  });

  it('handles attachment with no extractable text', () => {
    const doc = makeParsedFile({ name: 'binary.bin', text: '' });
    const result = buildUserContent('Check this', [doc]);
    expect(typeof result).toBe('string');
    expect(result).toContain('(No extractable text)');
  });
});

describe('formatBytes', () => {
  it('formats zero and negative values', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(-5)).toBe('0 B');
  });

  it('formats bytes', () => {
    expect(formatBytes(512)).toBe('512 B');
  });

  it('formats kilobytes', () => {
    expect(formatBytes(1024)).toBe('1.0 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  it('formats megabytes', () => {
    expect(formatBytes(1048576)).toBe('1.0 MB');
    expect(formatBytes(5242880)).toBe('5.0 MB');
  });

  it('handles NaN and Infinity', () => {
    expect(formatBytes(NaN)).toBe('0 B');
    expect(formatBytes(Infinity)).toBe('0 B');
  });
});

describe('compactPythonPath', () => {
  it('returns short paths as-is', () => {
    expect(compactPythonPath('python.exe')).toBe('python.exe');
    expect(compactPythonPath('bin/python')).toBe('bin/python');
  });

  it('takes last 2 segments of long paths', () => {
    expect(compactPythonPath('C:\\Anaconda3\\python.exe')).toBe('Anaconda3/python.exe');
    expect(compactPythonPath('D:\\envs\\myenv\\Scripts\\python.exe')).toBe('Scripts/python.exe');
  });

  it('handles forward slashes', () => {
    expect(compactPythonPath('/usr/local/bin/python3')).toBe('bin/python3');
  });
});
