import { beforeEach, describe, expect, it, vi } from 'vitest';

const listArtifacts = vi.fn();
vi.mock('../api', () => ({ listArtifacts }));

const library = await import('../documentLibrary');

describe('documentLibrary session scoping', () => {
  beforeEach(() => {
    localStorage.clear();
    listArtifacts.mockReset();
  });

  it('keeps other sessions while returning only the requested session', async () => {
    library.upsertDocumentLibraryItem({
      id: 'artifact-old',
      artifactId: 'artifact-old',
      sessionId: 'session-b',
      kind: 'document',
      title: 'Other session',
    });
    listArtifacts.mockResolvedValue({
      artifacts: [{
        artifact_id: 'artifact-new',
        session_id: 'session-a',
        kind: 'document',
        title: 'Current session',
        path: 'current.md',
        url: '',
        mime: 'text/markdown',
        created_at: '2026-07-27T00:00:00Z',
        updated_at: '2026-07-27T00:00:00Z',
        metadata: {},
      }],
    });

    const scoped = await library.syncDocumentLibraryFromArtifacts({ sessionId: 'session-a', includeUnscoped: false });

    expect(scoped.map(item => item.id)).toEqual(['artifact-new']);
    expect(library.listDocumentLibraryItems('session-b').map(item => item.id)).toEqual(['artifact-old']);
    expect(listArtifacts).toHaveBeenCalledWith(expect.objectContaining({ sessionId: 'session-a' }));
  });

  it('does not return unscoped legacy entries for a session', () => {
    library.upsertDocumentLibraryItem({ id: 'legacy', kind: 'file', title: 'Legacy' });
    library.upsertDocumentLibraryItem({ id: 'scoped', sessionId: 'session-a', kind: 'file', title: 'Scoped' });

    expect(library.listDocumentLibraryItems('session-a').map(item => item.id)).toEqual(['scoped']);
  });
});
