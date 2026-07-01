/**
 * commands.ts 单元测试 —— 验证命令面板构建和模糊搜索评分。
 */
import { describe, it, expect, vi } from 'vitest';
import { buildCommands, commandScore, modelPresets } from '../commands';
import type { CommandContext, CommandItem } from '../commands';

// ---------------------------------------------------------------------------
// Helper: 最小化 CommandContext
// ---------------------------------------------------------------------------

function makeContext(overrides: Partial<CommandContext> = {}): CommandContext {
  const noop = vi.fn();
  return {
    language: 'zh',
    sessions: [],
    workspaces: [],
    activeSessionId: null,
    activeWorkspaceId: 'w1',
    theme: 'cathedral-obsidian',
    sidebarOpen: true,
    rightRailOpen: false,
    settings: null,
    actions: {
      createSession: noop,
      switchSession: noop,
      switchWorkspace: noop,
      openFolder: noop,
      setTheme: noop,
      openModelPicker: noop,
      openSettings: noop,
      clearConversation: noop,
      rewindConversation: noop,
      exportChat: noop,
      toggleLanguage: noop,
      setSidebarOpen: noop,
      setRightRailOpen: noop,
      setActiveSection: noop,
    },
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// buildCommands
// ---------------------------------------------------------------------------

describe('buildCommands', () => {
  it('returns base commands in Chinese', () => {
    const commands = buildCommands(makeContext());
    const ids = commands.map(c => c.id);
    expect(ids).toContain('session.new');
    expect(ids).toContain('workspace.open');
    expect(ids).toContain('model.open');
    expect(ids).toContain('settings.open');
  });

  it('includes theme commands for all theme names', () => {
    const commands = buildCommands(makeContext());
    const themeCommands = commands.filter(c => c.id.startsWith('theme.'));
    expect(themeCommands.length).toBeGreaterThanOrEqual(2);
  });

  it('includes section navigation commands', () => {
    const commands = buildCommands(makeContext());
    const sectionIds = commands.filter(c => c.id.startsWith('section.')).map(c => c.id);
    expect(sectionIds).toContain('section.skills');
    expect(sectionIds).toContain('section.mcp');
    expect(sectionIds).toContain('section.computer');
    expect(sectionIds).toContain('section.cron');
  });

  it('includes workspace quick-switch commands', () => {
    const commands = buildCommands(makeContext({
      workspaces: [
        { id: 'w1', name: 'Alpha', path: '/alpha', createdAt: 1, updatedAt: 2 },
        { id: 'w2', name: 'Beta', path: '/beta', createdAt: 1, updatedAt: 2 },
      ],
    }));
    const wsCommands = commands.filter(c => c.id.startsWith('workspace.w'));
    expect(wsCommands).toHaveLength(2);
    expect(wsCommands[0].title).toContain('Alpha');
  });

  it('includes session quick-switch commands', () => {
    const commands = buildCommands(makeContext({
      sessions: [
        { id: 's1', title: 'Chat 1', workspaceId: 'w1', mode: 'chat', messageCount: 3, createdAt: 1, updatedAt: 2 },
      ],
    }));
    const sessionCmd = commands.find(c => c.id === 'session.s1');
    expect(sessionCmd).toBeDefined();
    expect(sessionCmd!.title).toBe('Chat 1');
  });

  it('uses English labels when language is en', () => {
    const commands = buildCommands(makeContext({ language: 'en' }));
    const newCmd = commands.find(c => c.id === 'session.new');
    expect(newCmd?.title).toBe('New chat');
  });

  it('sidebar toggle says hide when open', () => {
    const commands = buildCommands(makeContext({ sidebarOpen: true }));
    const cmd = commands.find(c => c.id === 'layout.sidebar.toggle');
    expect(cmd?.title).toContain('隐藏');
  });

  it('sidebar toggle says show when closed', () => {
    const commands = buildCommands(makeContext({ sidebarOpen: false }));
    const cmd = commands.find(c => c.id === 'layout.sidebar.toggle');
    expect(cmd?.title).toContain('显示');
  });

  it('caps sessions to 12', () => {
    const sessions = Array.from({ length: 20 }, (_, i) => ({
      id: `s${i}`,
      title: `Session ${i}`,
      workspaceId: 'w1',
      mode: 'chat',
      messageCount: i,
      createdAt: i,
      updatedAt: i,
    }));
    const commands = buildCommands(makeContext({ sessions }));
    const sessionCmds = commands.filter(c => c.id.startsWith('session.s'));
    expect(sessionCmds.length).toBeLessThanOrEqual(12);
  });
});

// ---------------------------------------------------------------------------
// commandScore
// ---------------------------------------------------------------------------

describe('commandScore', () => {
  const cmd: CommandItem = {
    id: 'test',
    title: '新建会话',
    subtitle: '创建新对话',
    keywords: ['new', 'chat', 'session', '新建', '会话'],
    group: '会话',
    run: vi.fn(),
  };

  it('returns 1 for empty query', () => {
    expect(commandScore(cmd, '')).toBe(1);
    expect(commandScore(cmd, '   ')).toBe(1);
  });

  it('scores exact substring match high', () => {
    const score = commandScore(cmd, '新建');
    expect(score).toBeGreaterThan(50);
  });

  it('scores keyword match high', () => {
    const score = commandScore(cmd, 'new');
    expect(score).toBeGreaterThan(50);
  });

  it('returns negative for completely unrelated query', () => {
    const score = commandScore(cmd, 'zzzzqqqq');
    expect(score).toBeLessThan(0);
  });

  it('fuzzy matches partial characters', () => {
    const score = commandScore(cmd, 'nw');
    // 'n' and 'w' are both in "new" — should get a positive fuzzy score
    expect(score).toBeGreaterThan(0);
  });

  it('prefers exact substring over fuzzy', () => {
    const exactScore = commandScore(cmd, 'session');
    const fuzzyScore = commandScore(cmd, 'sessn');
    expect(exactScore).toBeGreaterThan(fuzzyScore);
  });
});

// ---------------------------------------------------------------------------
// modelPresets
// ---------------------------------------------------------------------------

describe('modelPresets', () => {
  it('contains expected providers', () => {
    const providers = [...new Set(modelPresets.map(p => p.provider))];
    expect(providers).toContain('DeepSeek');
    expect(providers).toContain('OpenAI');
    expect(providers).toContain('Anthropic');
  });

  it('all presets have required fields', () => {
    for (const preset of modelPresets) {
      expect(preset.id).toBeTruthy();
      expect(preset.provider).toBeTruthy();
      expect(preset.backend).toBeTruthy();
      expect(preset.model).toBeTruthy();
      expect(preset.note).toBeTruthy();
    }
  });

  it('has unique ids', () => {
    const ids = modelPresets.map(p => p.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
