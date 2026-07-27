/**
 * api.ts 单元测试 —— 验证纯函数转换逻辑和 SSE 重连策略。
 *
 * 不依赖真实后端，只测试模块内可导出的纯转换和辅助函数。
 * 对 fetch 调用的 API 函数通过 mock fetch 验证请求格式。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ---------------------------------------------------------------------------
// 重新导出 api.ts 中暴露的纯函数进行测试
// 因为 api.ts 的部分函数是 module-private，我们通过调用公开 API 来间接测试。
// ---------------------------------------------------------------------------

// Mock window.metis 供 apiBase() 使用
vi.stubGlobal('metis', { backendPort: () => Promise.resolve(9123) });

// Mock fetch
const fetchMock = vi.fn();
vi.stubGlobal('fetch', fetchMock);

function jsonResponse(data: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(data)),
  });
}

function sseResponse(chunks: string[], status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    body: new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
  });
}

// Import after mocks are set up
const api = await import('../api');

beforeEach(() => {
  fetchMock.mockReset();
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// getSessions
// ---------------------------------------------------------------------------

describe('getSessions', () => {
  it('parses backend response into typed payload', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      sessions: [
        {
          id: 's1',
          title: 'Test',
          workspace_id: 'w1',
          message_count: 5,
          created_at: 1000,
          updated_at: 2000,
        },
      ],
      active_id: 's1',
      active_workspace_id: 'w1',
    }));

    const result = await api.getSessions();
    expect(result.activeSessionId).toBe('s1');
    expect(result.activeWorkspaceId).toBe('w1');
    expect(result.sessions).toHaveLength(1);
    expect(result.sessions[0].id).toBe('s1');
    expect(result.sessions[0].title).toBe('Test');
    expect(result.sessions[0].messageCount).toBe(5);
  });

  it('handles empty sessions list', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      sessions: [],
      active_id: '',
      active_workspace_id: '',
    }));

    const result = await api.getSessions();
    expect(result.sessions).toEqual([]);
    expect(result.activeSessionId).toBeNull();
  });

  it('defaults missing fields gracefully', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      sessions: [{ id: 'x' }],
    }));

    const result = await api.getSessions();
    expect(result.sessions[0].title).toBe('Metis Chat');
    expect(result.sessions[0].messageCount).toBe(0);
    expect(result.sessions[0].workspaceId).toBe('');
  });
});

describe('getMarketplaceCatalog', () => {
  it('preserves localized marketplace descriptions from the backend', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      schema: 'metis.marketplace.v1',
      counts: { skill: 0, mcp: 0, plugin: 1 },
      items: [{
        id: 'openai:linear',
        kind: 'plugin',
        name: 'Linear',
        version: '1.0.0',
        description: 'Find and reference issues and projects.',
        descriptions: {
          en: 'Find and reference issues and projects.',
          zh: '查找并引用 Linear 中的问题、项目和相关上下文。',
        },
        source: { type: 'remote-plugin', marketplace: 'openai-plugins' },
      }],
    }));

    const result = await api.getMarketplaceCatalog({ source: 'openai-plugins' });

    expect(result.items[0].descriptions).toEqual({
      en: 'Find and reference issues and projects.',
      zh: '查找并引用 Linear 中的问题、项目和相关上下文。',
    });
  });
});

// ---------------------------------------------------------------------------
// createSession
// ---------------------------------------------------------------------------

describe('createSession', () => {
  it('sends POST and parses response', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      id: 'new-session',
      workspace_id: 'w1',
    }));

    const result = await api.createSession();
    expect(result.id).toBe('new-session');
    expect(result.workspaceId).toBe('w1');

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/sessions');
    expect(opts.method).toBe('POST');
  });

  it('can explicitly create a session without a workspace', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      id: 'unscoped-session',
      workspace_id: '',
    }));

    await api.createSession('cowork', null);

    const [, opts] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(opts.body))).toEqual({
      mode: 'cowork',
      workspace_id: '',
    });
  });
});

describe('createRun', () => {
  it('posts to /runs and parses stable run fields', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      ok: true,
      run_id: 'run-1',
      turn_id: 'turn-run-1',
      session_id: 'session-1',
      assistant_id: 'assistant-1',
      mode: 'code',
      surface_mode: 'code',
      schema_version: 1,
      status: 'queued',
      last_seq: 0,
    }));

    const result = await api.createRun({
      message: 'hello',
      session_id: 'session-1',
      assistant_id: 'assistant-1',
      surface_mode: 'code',
    });

    expect(result.runId).toBe('run-1');
    expect(result.turnId).toBe('turn-run-1');
    expect(result.surfaceMode).toBe('code');

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/runs');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(String(opts.body))).toMatchObject({ surface_mode: 'code' });
  });
});

describe('streamRunEvents', () => {
  it('requests agent_event v2 and adapts events before invoking the callback', async () => {
    fetchMock.mockReturnValueOnce(sseResponse([
      `data: ${JSON.stringify({
        schema: 'metis.agent_event.v2',
        version: 2,
        run_id: 'run-1',
        session_id: 'session-1',
        turn_id: 'turn-run-1',
        message_id: 'assistant-1',
        seq: 1,
        event_id: 'evt_run-1_000001',
        timestamp: '2026-07-06T00:00:00.000Z',
        kind: 'message_delta',
        payload: { text: 'hello' },
      })}\n\n`,
      'data: [DONE]\n\n',
    ]));

    const events: Array<{ type: string; seq?: number; payload?: Record<string, unknown> }> = [];
    await api.streamRunEvents('run-1', event => events.push(event as (typeof events)[number]));

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/runs/run-1/events?');
    expect(String(url)).toContain('schema=v2');
    expect(String(url)).toContain('after=0');
    expect(events).toHaveLength(1);
    expect(events[0].type).toBe('content_delta');
    expect(events[0].seq).toBe(1);
    expect(events[0].payload?.text).toBe('hello');
  });
});

describe('permission request protocol', () => {
  it('lists pending permission requests for desktop-wide approval', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      ok: true,
      requests: [
        {
          schema: 'metis.permission_request.v1',
          request_id: 'perm-attached',
          call_id: 'call-attached',
          run_id: 'run-attached',
          session_id: 'session-attached',
          tool_name: 'write_file',
          status: 'requested',
          arguments_preview: { path: 'notes.md' },
        },
      ],
    }));

    const requests = await api.listPendingPermissionRequests();

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('/permissions/requests');
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      requestId: 'perm-attached',
      runId: 'run-attached',
      sessionId: 'session-attached',
      toolName: 'write_file',
      status: 'requested',
    });
  });

  it('marks a permission request displayed', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      ok: true,
      request: {
        schema: 'metis.permission_request.v1',
        request_id: 'perm-1',
        call_id: 'call-1',
        status: 'displayed',
        choices: [{ value: 'once', label: '仅本次允许', approved: true }],
      },
    }));

    const request = await api.markPermissionDisplayed('perm-1', { surface: 'desktop' });

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/permissions/requests/perm-1/displayed');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(String(opts.body))).toMatchObject({ surface: 'desktop' });
    expect(request.requestId).toBe('perm-1');
    expect(request.status).toBe('displayed');
    expect(request.choices?.[0].value).toBe('once');
  });

  it('answers permission requests through the new API', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      ok: true,
      approved: true,
      request: {
        schema: 'metis.permission_request.v1',
        request_id: 'perm-2',
        call_id: 'call-2',
        status: 'audited',
      },
    }));

    const request = await api.answerPermissionRequest('perm-2', {
      approved: true,
      choice: 'once',
      tool: 'write_file',
      callId: 'call-2',
    });

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/permissions/requests/perm-2/answer');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(String(opts.body))).toMatchObject({
      approved: true,
      choice: 'once',
      tool: 'write_file',
      call_id: 'call-2',
    });
    expect(request.status).toBe('audited');
  });
});

describe('artifact registry API', () => {
  it('lists artifacts with stable v1 fields', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      ok: true,
      artifacts: [
        {
          schema: 'metis.artifact.v1',
          version: 1,
          artifact_id: 'art_1',
          run_id: 'run_1',
          session_id: 'sess_1',
          kind: 'report',
          title: 'Report',
          path: 'D:/workspace/report.md',
          mime: 'text/markdown',
          created_at: '2026-07-06T00:00:00.000Z',
          updated_at: '2026-07-06T00:01:00.000Z',
          source_tool_call_id: 'call_1',
          metadata: { job_id: 'job_1' },
        },
      ],
    }));

    const result = await api.listArtifacts({ sessionId: 'sess_1', kind: 'report', limit: 10 });

    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toContain('/artifacts?');
    expect(String(url)).toContain('session_id=sess_1');
    expect(result.artifacts[0].artifact_id).toBe('art_1');
    expect(result.artifacts[0].metadata.job_id).toBe('job_1');
  });

  it('registers artifacts through the backend registry endpoint', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      ok: true,
      artifact: {
        schema: 'metis.artifact.v1',
        version: 1,
        artifact_id: 'art_2',
        kind: 'preview_evidence',
        title: 'Preview',
        path: 'D:/data/evidence.json',
        created_at: '2026-07-06T00:00:00.000Z',
        updated_at: '2026-07-06T00:00:00.000Z',
      },
    }));

    const artifact = await api.registerArtifact({
      kind: 'preview_evidence',
      title: 'Preview',
      path: 'D:/data/evidence.json',
      sessionId: 'sess_2',
      metadata: { status: 'ok' },
    });

    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/artifacts');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(String(opts.body))).toMatchObject({
      kind: 'preview_evidence',
      session_id: 'sess_2',
      metadata: { status: 'ok' },
    });
    expect(artifact.artifact_id).toBe('art_2');
  });
});

// ---------------------------------------------------------------------------
// getSession
// ---------------------------------------------------------------------------

describe('getSession', () => {
  it('parses full session with history', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      id: 's1',
      title: 'Chat',
      workspace_id: 'w1',
      mode: 'auto',
      history: [{ role: 'user', content: 'hi' }],
      compact_state: {
        summary: '[Context Summary]\nhi',
        boundary_message_id: 'm2',
        boundary_index: 2,
        compacted_at: 123,
        compact_count: 1,
      },
      created_at: 100,
      updated_at: 200,
    }));

    const session = await api.getSession('s1');
    expect(session.id).toBe('s1');
    expect(session.mode).toBe('auto');
    expect(session.history).toHaveLength(1);
    expect(session.history[0].role).toBe('user');
    expect(session.compactState?.boundaryMessageId).toBe('m2');
    expect(session.compactState?.summary).toContain('hi');
  });

  it('defaults mode to auto when missing', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({ id: 's1' }));
    const session = await api.getSession('s1');
    expect(session.mode).toBe('auto');
  });
});

// ---------------------------------------------------------------------------
// getWorkspaces
// ---------------------------------------------------------------------------

describe('getWorkspaces', () => {
  it('parses workspace list', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      active_id: 'w1',
      workspaces: [
        { id: 'w1', name: 'Project', path: '/home/user/project', created_at: 1, updated_at: 2 },
      ],
    }));

    const result = await api.getWorkspaces();
    expect(result.activeWorkspaceId).toBe('w1');
    expect(result.workspaces).toHaveLength(1);
    expect(result.workspaces[0].name).toBe('Project');
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe('requestJson error handling', () => {
  it('throws on HTTP error with message from body', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse(
      { error: 'not found', message: 'session not found' },
      404,
    ));

    await expect(api.getSession('missing')).rejects.toThrow('session not found');
  });

  it('throws on HTTP error with error field fallback', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse(
      { error: 'workspace deleted' },
      410,
    ));

    await expect(api.getSession('gone')).rejects.toThrow('workspace deleted');
  });

  it('throws generic status on empty error body', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({}, 500));
    await expect(api.getSession('err')).rejects.toThrow('HTTP 500');
  });

  it('bounds network checks with an abort signal and friendly timeout message', async () => {
    fetchMock.mockRejectedValueOnce(new DOMException('Timed out', 'TimeoutError'));

    await expect(api.checkNetworkSettings({
      backend: 'custom-openai',
      baseUrl: 'https://relay.example/v1',
      model: 'gpt-5.5',
      apiKey: 'sk-test',
    })).rejects.toThrow('网络检查超时');

    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.signal).toBeInstanceOf(AbortSignal);
  });
});

// ---------------------------------------------------------------------------
// getSettings
// ---------------------------------------------------------------------------

describe('getSettings', () => {
  it('parses settings with snake_case to camelCase conversion', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      backend: 'deepseek',
      provider_id: 'deepseek',
      base_url: 'https://api.deepseek.com',
      model: 'deepseek-chat',
      temperature: 0.7,
      max_tokens: 4096,
      api_key: '',
      has_api_key: true,
      auto_memory: true,
      auto_skills: false,
      proxy_mode: 'system',
      proxy_scheme: 'http',
      proxy_host: '127.0.0.1',
      proxy_port: '7890',
      proxy_bypass: '',
      terminal_shell: 'powershell',
      python_path: 'python',
      provider_validation: { ok: true },
    }));

    const settings = await api.getSettings();
    expect(settings.providerId).toBe('deepseek');
    expect(settings.baseUrl).toBe('https://api.deepseek.com');
    expect(settings.hasApiKey).toBe(true);
    expect(settings.autoMemory).toBe(true);
    expect(settings.terminalShell).toBe('powershell');
  });
});

// ---------------------------------------------------------------------------
// composer deep research toggle
// ---------------------------------------------------------------------------

describe('composer deep research toggle', () => {
  it('persists the deep research preference locally', async () => {
    await expect(api.getComposerDeepResearchEnabled()).resolves.toBe(false);

    await expect(api.setComposerDeepResearchEnabled(true)).resolves.toBe(true);
    await expect(api.getComposerDeepResearchEnabled()).resolves.toBe(true);

    await expect(api.setComposerDeepResearchEnabled(false)).resolves.toBe(false);
    await expect(api.getComposerDeepResearchEnabled()).resolves.toBe(false);
  });
});

// ---------------------------------------------------------------------------
// searchSessions
// ---------------------------------------------------------------------------

describe('searchSessions', () => {
  it('encodes query and parses results', async () => {
    fetchMock.mockReturnValueOnce(jsonResponse({
      results: [
        { session_id: 's1', title: 'Chat', snippet: 'hello', ts: 100, score: 0.9 },
      ],
    }));

    const results = await api.searchSessions('hello world');
    expect(results).toHaveLength(1);
    expect(results[0].sessionId).toBe('s1');
    expect(results[0].snippet).toBe('hello');

    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain('q=hello%20world');
  });
});
