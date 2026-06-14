import { describe, expect, it } from 'vitest';
import type { Session } from '../../lib/types';
import { messagesFromSession } from '../messageOps';

describe('messagesFromSession compact boundary', () => {
  it('keeps every history message and inserts a compact divider at the boundary', () => {
    const session: Session = {
      id: 'session-compact',
      title: 'Compact',
      workspaceId: 'workspace-1',
      mode: 'auto',
      history: [
        { id: 'm1', role: 'user', content: 'old user' },
        { id: 'm2', role: 'assistant', content: 'old assistant' },
        { id: 'm3', role: 'user', content: 'retained user' },
        { id: 'm4', role: 'assistant', content: 'retained assistant' },
      ],
      compactState: {
        summary: '[Context Summary]\nold user facts',
        boundaryMessageId: 'm3',
        boundaryIndex: 2,
        compactedAt: 10,
        compactCount: 1,
      },
      createdAt: 1,
      updatedAt: 2,
    };

    const messages = messagesFromSession(session);

    expect(messages.map(message => message.content)).toEqual([
      'old user',
      'old assistant',
      '[Metis Compact Boundary]\n[Context Summary]\nold user facts',
      'retained user',
      'retained assistant',
    ]);
    expect(messages.filter(message => message.role !== 'system')).toHaveLength(4);
  });
});
