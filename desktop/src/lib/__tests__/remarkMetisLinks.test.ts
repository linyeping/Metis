import { describe, expect, it } from 'vitest';
import { chatLinkActionFromHref, decodeMetisFileHref, metisFileHref } from '../metisLinks';
import { linkifyMetisText, remarkMetisLinks } from '../remarkMetisLinks';

describe('remarkMetisLinks', () => {
  it('links workspace file paths and bare localhost urls', () => {
    const segments = linkifyMetisText('See backend/core/paths.py and localhost:3000.');

    expect(segments).toEqual([
      { kind: 'text', value: 'See ' },
      { kind: 'file', value: 'backend/core/paths.py', href: metisFileHref('backend/core/paths.py') },
      { kind: 'text', value: ' and ' },
      { kind: 'web', value: 'localhost:3000', href: 'http://localhost:3000' },
      { kind: 'text', value: '.' },
    ]);
  });

  it('keeps ordinary filenames conservative but links html preview entries', () => {
    expect(linkifyMetisText('Install from requirements.txt.')).toEqual([{ kind: 'text', value: 'Install from requirements.txt.' }]);
    expect(linkifyMetisText('Open index.html')).toEqual([
      { kind: 'text', value: 'Open ' },
      { kind: 'file', value: 'index.html', href: metisFileHref('index.html') },
    ]);
  });

  it('marks existing http links and leaves fenced code blocks alone', () => {
    const tree: any = {
      type: 'root',
      children: [
        {
          type: 'paragraph',
          children: [
            { type: 'text', value: 'Read src/app.tsx then ' },
            { type: 'link', url: 'https://example.com', children: [{ type: 'text', value: 'site' }] },
          ],
        },
        { type: 'code', lang: 'ts', value: 'const path = "src/app.tsx";' },
      ],
    };

    remarkMetisLinks()(tree);

    expect(tree.children[0].children[1]).toMatchObject({
      type: 'link',
      url: metisFileHref('src/app.tsx'),
      data: { hProperties: { 'data-link-kind': 'file' } },
    });
    expect(tree.children[0].children[3]).toMatchObject({
      type: 'link',
      url: 'https://example.com',
      data: { hProperties: { 'data-link-kind': 'web' } },
    });
    expect(tree.children[1].value).toBe('const path = "src/app.tsx";');
  });
});

describe('chatLinkActionFromHref', () => {
  it('dispatches metis file links and web links', () => {
    const href = metisFileHref('D:\\project\\public\\index.html');

    expect(decodeMetisFileHref(href)).toBe('D:\\project\\public\\index.html');
    expect(chatLinkActionFromHref(href, 'file')).toEqual({ kind: 'file', path: 'D:\\project\\public\\index.html' });
    expect(chatLinkActionFromHref('http://localhost:3000/', 'web')).toEqual({ kind: 'web', url: 'http://localhost:3000/' });
    expect(chatLinkActionFromHref('mailto:test@example.com')).toBeNull();
  });
});
