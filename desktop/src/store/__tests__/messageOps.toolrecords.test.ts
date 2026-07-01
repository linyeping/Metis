import { describe, expect, it } from 'vitest';
import type { Session } from '../../lib/types';
import { messagesFromSession, toolResultStatus } from '../messageOps';

// FABLEADV-16: transcript-only tool records (metis_kind=tool) must rebuild into
// tool cards on the following assistant message, not render as empty bubbles.
describe('messagesFromSession tool records', () => {
  const baseSession = (history: Session['history']): Session => ({
    id: 'sess-tools',
    title: 'Tools',
    workspaceId: 'ws',
    mode: 'auto',
    history,
    compactState: null,
    createdAt: 1,
    updatedAt: 2,
  });

  it('attaches accumulated tool records to the next assistant turn', () => {
    const session = baseSession([
      { id: 'u1', role: 'user', content: '读取 paths.py' },
      {
        id: 't1',
        role: 'assistant',
        content: '',
        metis_kind: 'tool',
        metis_tool: { call_id: 'c1', name: 'read_file', arguments: { path: 'paths.py' }, result: '=== paths.py ===', status: 'success' },
      },
      {
        id: 't2',
        role: 'assistant',
        content: '',
        metis_kind: 'tool',
        metis_tool: { call_id: 'c2', name: 'grep_search', arguments: { pattern: 'metis_home' }, result: '3 matches', status: 'success' },
      },
      { id: 'a1', role: 'assistant', content: 'metis_home 的解析顺序是…' },
    ]);

    const messages = messagesFromSession(session);

    // No empty assistant bubble for the tool records themselves.
    expect(messages).toHaveLength(2);
    const [userMsg, assistantMsg] = messages;
    expect(userMsg.role).toBe('user');
    expect(assistantMsg.role).toBe('assistant');
    expect(assistantMsg.content).toContain('metis_home');
    expect(assistantMsg.tools).toHaveLength(2);
    expect(assistantMsg.tools?.[0]).toMatchObject({ callId: 'c1', toolName: 'read_file', status: 'success' });
    expect(assistantMsg.tools?.[1]).toMatchObject({ callId: 'c2', toolName: 'grep_search' });
  });

  it('flushes trailing tool records into a standalone tool message when no assistant text follows', () => {
    const session = baseSession([
      { id: 'u1', role: 'user', content: 'do it' },
      {
        id: 't1',
        role: 'assistant',
        content: '',
        metis_kind: 'tool',
        metis_tool: { call_id: 'c1', name: 'write_file', result: 'ok', status: 'success' },
      },
    ]);

    const messages = messagesFromSession(session);

    expect(messages).toHaveLength(2);
    expect(messages[1].tools).toHaveLength(1);
    expect(messages[1].tools?.[0].toolName).toBe('write_file');
  });

  it('marks error tool records with error status', () => {
    const session = baseSession([
      { id: 'u1', role: 'user', content: 'x' },
      {
        id: 't1',
        role: 'assistant',
        content: '',
        metis_kind: 'tool',
        metis_tool: { call_id: 'c1', name: 'run_tests', result: 'Error: boom', status: 'error' },
      },
      { id: 'a1', role: 'assistant', content: 'failed' },
    ]);

    const messages = messagesFromSession(session);
    expect(messages[1].tools?.[0].status).toBe('error');
  });

  it('keeps partial research payloads successful when only individual sources failed', () => {
    const result = [
      '<!-- METIS_RESEARCH_JSON {"schema":"metis.research_activity.v1","kind":"research","job_id":"research_1","job_status":"partial","sources":[{"status":"failed","error":"HTTP 403"}]} -->',
      '=== Web Research ===',
    ].join('\n');

    expect(toolResultStatus(result)).toBe('success');
  });
});
