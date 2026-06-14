import { describe, expect, it } from 'vitest';
import { buildFileChangePreview, summarizeFileChanges } from '../diffPreview';

describe('diffPreview', () => {
  it('ignores mutation tool results that do not include a path', () => {
    const preview = buildFileChangePreview('write_file', { content: 'hello' }, 'Wrote file successfully');
    expect(preview).toBeNull();
    expect(summarizeFileChanges([])).toBeNull();
  });

  it('keeps valid file changes visible', () => {
    const preview = buildFileChangePreview('write_file', { path: 'index.html', content: '<h1>Hello</h1>' }, 'ok');
    expect(preview).not.toBeNull();
    if (!preview) return;
    expect(preview?.path).toBe('index.html');
    expect(summarizeFileChanges([preview])).toMatchObject({
      fileCount: 1,
    });
  });
});
