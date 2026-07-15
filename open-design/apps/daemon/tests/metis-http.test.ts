import { once } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { createMetisHttpTransport, metisEventToAgentEvent } from '../src/runtimes/metis-http.js';

const temporaryRoots: string[] = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

describe('Metis native Design transport', () => {
  it('maps Metis v2 text and tool events to Open Design agent events', () => {
    expect(metisEventToAgentEvent({ kind: 'message_delta', payload: { text: 'Hello' } })).toEqual({
      type: 'text_delta',
      delta: 'Hello',
    });
    expect(metisEventToAgentEvent({
      kind: 'tool_requested',
      payload: { call_id: 'c1', tool_name: 'write_file', arguments: { path: 'index.html' } },
    })).toEqual({
      type: 'tool_use',
      id: 'c1',
      name: 'write_file',
      input: { path: 'index.html' },
    });
  });

  it('preserves actionable Metis permission requests and their resolved state', () => {
    expect(metisEventToAgentEvent({
      kind: 'permission_required',
      payload: {
        request_id: 'permission-one',
        tool_name: 'write_file',
        permission: {
          status: 'requested',
          default_choice: 'once',
          permission_explainer: {
            explanation: 'Write index.html',
            reasoning: 'Needed to create the requested page.',
            risk: 'This changes a project file.',
            riskLevel: 'Medium',
          },
          choices: [{ value: 'once', label: '仅本次允许', approved: true }],
        },
      },
    })).toEqual({
      type: 'status',
      label: 'waiting_permission',
      permission: expect.objectContaining({
        requestId: 'permission-one',
        status: 'requested',
        toolName: 'write_file',
        explanation: 'Write index.html',
        defaultChoice: 'once',
        choices: [expect.objectContaining({ value: 'once', label: '仅本次允许', approved: true })],
      }),
    });
    expect(metisEventToAgentEvent({
      kind: 'permission_rejected',
      payload: {
        request_id: 'permission-one',
        permission: { status: 'rejected', approved: false, choice: 'always_deny' },
      },
    })).toEqual({
      type: 'status',
      label: 'waiting_permission',
      permission: expect.objectContaining({
        requestId: 'permission-one',
        status: 'rejected',
        approved: false,
        selectedChoice: 'always_deny',
      }),
    });
  });

  it('runs through the Metis backend without spawning an agent executable', async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-native-design-'));
    temporaryRoots.push(root);
    const designRoot = path.join(root, 'projects');
    const projectId = 'project-one';
    const projectDir = path.join(designRoot, projectId);
    fs.mkdirSync(projectDir, { recursive: true });
    const calls: Array<{
      method: string | undefined;
      url: string | undefined;
      body: any;
      token?: string;
    }> = [];

    const server = http.createServer((request, response) => {
      const chunks: Buffer[] = [];
      request.on('data', chunk => chunks.push(chunk));
      request.on('end', () => {
        const raw = Buffer.concat(chunks).toString('utf8');
        const body = raw ? JSON.parse(raw) : {};
        calls.push({ method: request.method, url: request.url, body, token: String(request.headers['x-metis-design-token'] || '') });
        response.setHeader('content-type', 'application/json');
        if (request.method === 'GET' && request.url === '/workspaces') return response.end(JSON.stringify({ workspaces: [] }));
        if (request.method === 'POST' && request.url === '/workspaces') return response.end(JSON.stringify({ id: 'workspace-one' }));
        if (request.method === 'GET' && request.url === '/mode') return response.end(JSON.stringify({ mode: 'auto_guard' }));
        if (request.method === 'POST' && request.url === '/sessions') return response.end(JSON.stringify({ id: 'session-one', active: false }));
        if (request.method === 'POST' && request.url === '/runs') return response.end(JSON.stringify({ run_id: 'run-one' }));
        if (request.method === 'GET' && request.url === '/runs/run-one/events?schema=v2&after=0') {
          response.setHeader('content-type', 'text/event-stream');
          return response.end('data: {"kind":"message_delta","payload":{"text":"Created index.html"}}\n\ndata: [DONE]\n\n');
        }
        if (request.method === 'GET' && request.url === '/runs/run-one') return response.end(JSON.stringify({ id: 'run-one', status: 'done' }));
        response.statusCode = 404;
        response.end(JSON.stringify({ error: 'not found' }));
      });
    });

    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
      const address = server.address();
      if (!address || typeof address === 'string') throw new Error('test server did not bind');
      const child = createMetisHttpTransport({
        backendUrl: `http://127.0.0.1:${address.port}`,
        token: 'design-secret',
        designRoot,
        stateRoot: root,
        projectId,
        projectDir,
        conversationId: 'conversation-one',
        prompt: 'Build it',
      });
      const events: any[] = [];
      child.on('agent', event => events.push(event));
      const [code] = await once(child, 'close');

      expect(code).toBe(0);
      expect(events).toContainEqual({ type: 'text_delta', delta: 'Created index.html' });
      const createRun = calls.find(call => call.method === 'POST' && call.url === '/runs');
      expect(createRun?.token).toBe('design-secret');
      expect(createRun?.body.surface_mode).toBe('design');
      expect(createRun?.body.metis_design.project_root).toBe(projectDir);
      expect(calls.some(call => String(call.url).includes('design-agent-bridge'))).toBe(false);
    } finally {
      await new Promise<void>(resolve => server.close(() => resolve()));
    }
  });
});
