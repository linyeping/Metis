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
