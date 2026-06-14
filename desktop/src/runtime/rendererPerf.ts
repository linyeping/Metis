import type { ChatMessage } from '../lib/types';
import { useChatStore } from '../store/chatStore';
import { useUiStore } from '../store/uiStore';
import { TRANSCRIPT_REPLAY_FIXTURE } from './fixtures/transcriptReplayFixture';
import {
  DESKTOP_PERF_BUDGETS,
  evaluateDesktopPerfBudgets,
  type DesktopPerfBudgetResult,
  type DesktopPerfMetrics,
} from './perfBudgets';

interface PerfCheck {
  name: string;
  ok: boolean;
  detail?: string;
}

interface FrameStats {
  frames: number;
  averageFrameMs: number;
  p95FrameMs: number;
  maxFrameMs: number;
  droppedFramesOver32Ms: number;
}

interface PerfReport {
  ok: boolean;
  checks: PerfCheck[];
  metrics?: DesktopPerfMetrics;
  budgets?: {
    limits: typeof DESKTOP_PERF_BUDGETS;
    results: DesktopPerfBudgetResult[];
  };
  error?: string;
}

const PERF_TIMEOUT_MS = 12000;

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function nextFrame(): Promise<void> {
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

function asErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.stack || error.message;
  if (typeof error === 'string') return error;
  return String(error);
}

function record(checks: PerfCheck[], name: string, ok: boolean, detail?: string): void {
  checks.push({ name, ok, detail });
  if (!ok) {
    throw new Error(`${name}${detail ? `: ${detail}` : ''}`);
  }
}

async function waitForCondition(predicate: () => boolean, detail: string): Promise<void> {
  const deadline = Date.now() + PERF_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await delay(20);
  }
  throw new Error(`timed out waiting for ${detail}`);
}

async function waitForBoot(checks: PerfCheck[]): Promise<void> {
  if (!window.metis) {
    record(checks, 'perf-boot-ready', false, 'window.metis is missing');
    return;
  }

  const deadline = Date.now() + PERF_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const state = await window.metis.bootState();
    if (state.status === 'ready' && state.port) {
      record(checks, 'perf-boot-ready', true, `127.0.0.1:${state.port}`);
      return;
    }
    if (state.status === 'error') {
      record(checks, 'perf-boot-ready', false, state.error?.detail || state.error?.title || 'boot error');
      return;
    }
    await delay(80);
  }

  record(checks, 'perf-boot-ready', false, 'timed out waiting for fake backend');
}

function syntheticMessages(count: number): ChatMessage[] {
  const now = Date.now();
  return Array.from({ length: count }, (_, index) => ({
    id: `perf-message-${index}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    content:
      index % 2 === 0
        ? `Performance user turn ${index}`
        : `Performance assistant response ${index}\n\n- item one\n- item two\n\n\`\`\`ts\nconst sample${index} = ${index};\n\`\`\``,
    createdAt: now + index,
  }));
}

function markdownHeavyContent(): string {
  const tableRows = Array.from({ length: 80 }, (_, index) => `| ${index} | perf-${index} | ${'value '.repeat(8)} |`).join('\n');
  const codeBlocks = Array.from(
    { length: 8 },
    (_, index) => `\`\`\`ts\nexport const markdownPerf${index} = ${JSON.stringify({ index, ok: true })};\n\`\`\``,
  ).join('\n\n');
  const list = Array.from({ length: 80 }, (_, index) => `- Markdown perf bullet ${index} with repeated content ${'x'.repeat(48)}`).join('\n');
  return [
    '# Markdown Performance Sample',
    'This synthetic answer stresses tables, lists, and code blocks without contacting a model.',
    '| Index | Name | Value |',
    '| ---: | --- | --- |',
    tableRows,
    '',
    list,
    '',
    codeBlocks,
  ].join('\n');
}

function largeToolResult(seed: number, lines = 120): string {
  return JSON.stringify(
    {
      seed,
      cwd: 'D:/pycharm/py.project/Miro',
      rows: Array.from({ length: lines }, (_, index) => ({
        index,
        path: `src/generated/perf-${seed}-${index}.ts`,
        status: index % 5 === 0 ? 'changed' : 'ok',
        detail: `Synthetic tool output row ${index} ${'x'.repeat(72)}`,
      })),
    },
    null,
    2,
  );
}

function mountedMessageRows(): number {
  return document.querySelectorAll('.message-row').length;
}

function threadWindowValue(): string {
  return document.querySelector<HTMLElement>('.thread-window')?.getAttribute('data-message-window') || '';
}

function hasThreadWindowFor(totalMessages: number, minRows = 1): boolean {
  return mountedMessageRows() >= minRows && threadWindowValue().endsWith(`/${totalMessages}`);
}

async function measureLongThread(checks: PerfCheck[]): Promise<DesktopPerfMetrics['longThread']> {
  const totalMessages = 1000;
  useUiStore.getState().setActiveSection('chat');
  useUiStore.getState().setSidebarOpen(true);
  useUiStore.getState().setRightRailOpen(true);
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: syntheticMessages(totalMessages),
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });

  await waitForCondition(() => hasThreadWindowFor(totalMessages), 'long thread rows');
  await nextFrame();
  await waitForCondition(() => hasThreadWindowFor(totalMessages), 'stable long thread rows');

  const initialMountedRows = mountedMessageRows();
  const initialWindow = threadWindowValue();
  record(
    checks,
    'perf-long-thread-window-capped',
    initialMountedRows > 0 && initialMountedRows <= 90 && initialWindow.endsWith(`/${totalMessages}`),
    `${initialWindow} rows=${initialMountedRows}`,
  );

  const loader = document.querySelector<HTMLButtonElement>('.thread-history-loader');
  record(checks, 'perf-long-thread-loader-visible', Boolean(loader), initialWindow || 'missing window');
  const expandStart = performance.now();
  loader?.click();
  await waitForCondition(() => mountedMessageRows() > initialMountedRows, 'older history expansion');
  await nextFrame();

  const expandedMountedRows = mountedMessageRows();
  const expandedWindow = threadWindowValue();
  const expandMs = Math.round(performance.now() - expandStart);
  record(
    checks,
    'perf-long-thread-expands-history',
    expandedMountedRows > initialMountedRows && expandedMountedRows <= 130,
    `${expandedWindow} before=${initialMountedRows} after=${expandedMountedRows} expandMs=${expandMs}`,
  );

  return {
    totalMessages,
    initialMountedRows,
    expandedMountedRows,
    initialWindow,
    expandedWindow,
    expandMs,
  };
}

async function measureStreamBurst(checks: PerfCheck[]): Promise<DesktopPerfMetrics['streamBurst']> {
  const now = Date.now();
  const assistantId = 'perf-stream-assistant';
  const chunks = 180;
  useUiStore.getState().setActiveSection('chat');
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: [
      {
        id: 'perf-stream-user',
        role: 'user',
        content: 'Measure synthetic stream burst.',
        createdAt: now,
      },
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        createdAt: now + 1,
        pending: true,
      },
    ],
    runtimeStatus: null,
    streaming: true,
    subagents: [],
  });
  await waitForCondition(() => mountedMessageRows() === 2, 'stream burst initial rows');

  const start = performance.now();
  for (let index = 0; index < chunks; index += 1) {
    const chunk = ` chunk-${index}`;
    useChatStore.setState(state => ({
      messages: state.messages.map(message =>
        message.id === assistantId ? { ...message, content: `${message.content}${chunk}` } : message,
      ),
    }));
    if (index % 8 === 0) {
      await nextFrame();
    }
  }
  useChatStore.setState(state => ({
    messages: state.messages.map(message => (message.id === assistantId ? { ...message, pending: false } : message)),
    streaming: false,
  }));
  await nextFrame();

  const assistant = useChatStore.getState().messages.find(message => message.id === assistantId);
  const durationMs = Math.round(performance.now() - start);
  const finalChars = assistant?.content.length || 0;
  const rows = mountedMessageRows();
  record(
    checks,
    'perf-stream-burst-completes',
    finalChars > chunks * 6 && rows === 2,
    `chunks=${chunks} chars=${finalChars} rows=${rows} durationMs=${durationMs}`,
  );

  return {
    chunks,
    finalChars,
    durationMs,
    mountedRows: rows,
  };
}

async function measureFrames(durationMs: number, action: () => void): Promise<FrameStats> {
  const deltas: number[] = [];
  let previous = performance.now();
  let started = false;
  const end = previous + durationMs;

  return new Promise(resolve => {
    const tick = (now: number) => {
      deltas.push(now - previous);
      previous = now;
      if (!started) {
        started = true;
        action();
      }
      if (now < end) {
        requestAnimationFrame(tick);
        return;
      }
      resolve(frameStats(deltas.slice(1)));
    };
    requestAnimationFrame(tick);
  });
}

function frameStats(deltas: number[]): FrameStats {
  const sorted = deltas.slice().sort((a, b) => a - b);
  const p95Index = Math.min(sorted.length - 1, Math.max(0, Math.floor(sorted.length * 0.95)));
  const total = deltas.reduce((sum, value) => sum + value, 0);
  return {
    frames: deltas.length,
    averageFrameMs: round(total / Math.max(1, deltas.length)),
    p95FrameMs: round(sorted[p95Index] || 0),
    maxFrameMs: round(Math.max(0, ...deltas)),
    droppedFramesOver32Ms: deltas.filter(value => value > 32).length,
  };
}

async function measureFramesDuring(action: () => Promise<void>): Promise<FrameStats> {
  const deltas: number[] = [];
  let previous = performance.now();
  let running = true;
  let resolveStats: (stats: FrameStats) => void = () => {};

  const statsPromise = new Promise<FrameStats>(resolve => {
    resolveStats = resolve;
  });

  const tick = (now: number) => {
    deltas.push(now - previous);
    previous = now;
    if (running) {
      requestAnimationFrame(tick);
      return;
    }
    resolveStats(frameStats(deltas.slice(1)));
  };

  requestAnimationFrame(tick);
  await action();
  running = false;
  return await statsPromise;
}

async function measurePanelMotion(checks: PerfCheck[]): Promise<DesktopPerfMetrics['panelMotion']> {
  const ui = useUiStore.getState();
  ui.setActiveSection('chat');
  ui.setSidebarOpen(true);
  ui.setRightRailOpen(true);
  let toggles = 0;

  const stats = await measureFrames(900, () => {
    const timer = window.setInterval(() => {
      const state = useUiStore.getState();
      state.setSidebarOpen(!state.sidebarOpen);
      state.setRightRailOpen(!state.rightRailOpen);
      toggles += 1;
      if (toggles >= 8) {
        window.clearInterval(timer);
      }
    }, 90);
  });

  ui.setSidebarOpen(true);
  ui.setRightRailOpen(true);
  record(
    checks,
    'perf-panel-motion-sampled',
    stats.frames >= 20 && toggles >= 8,
    `frames=${stats.frames} p95=${stats.p95FrameMs} max=${stats.maxFrameMs} toggles=${toggles}`,
  );

  return {
    ...stats,
    durationMs: 900,
    toggles,
  };
}

async function measureComposerInput(checks: PerfCheck[]): Promise<DesktopPerfMetrics['composerInput']> {
  useUiStore.getState().setActiveSection('chat');
  const updates = 24;
  const chars = 6000;
  const seed = 'Metis composer performance input ';
  const base = seed.repeat(Math.ceil(chars / seed.length) + 1).slice(0, chars);
  const start = performance.now();

  for (let index = 1; index <= updates; index += 1) {
    const nextLength = Math.floor((base.length * index) / updates);
    useChatStore.getState().setComposerText(base.slice(0, nextLength));
    if (index % 4 === 0) {
      await nextFrame();
    }
  }
  await nextFrame();

  const durationMs = Math.round(performance.now() - start);
  const valueLength = document.querySelector<HTMLTextAreaElement>('.composer textarea')?.value.length || 0;
  record(
    checks,
    'perf-composer-long-input-updates',
    valueLength === chars,
    `chars=${valueLength} updates=${updates} durationMs=${durationMs}`,
  );

  return {
    chars,
    updates,
    durationMs,
  };
}

async function measureMarkdownHeavy(checks: PerfCheck[]): Promise<DesktopPerfMetrics['markdownHeavy']> {
  const now = Date.now();
  const content = markdownHeavyContent();
  useUiStore.getState().setActiveSection('chat');
  const start = performance.now();
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: [
      {
        id: 'perf-markdown-user',
        role: 'user',
        content: 'Render markdown-heavy response.',
        createdAt: now,
      },
      {
        id: 'perf-markdown-assistant',
        role: 'assistant',
        content,
        createdAt: now + 1,
      },
    ],
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });
  await waitForCondition(() => Boolean(document.querySelector('.markdown-body table')), 'markdown table render');
  await nextFrame();

  const renderMs = Math.round(performance.now() - start);
  const mountedRows = mountedMessageRows();
  const tables = document.querySelectorAll('.markdown-body table').length;
  const codeBlocks = document.querySelectorAll('.markdown-body pre').length;
  record(
    checks,
    'perf-markdown-heavy-renders',
    mountedRows <= 4 && tables >= 1 && codeBlocks >= 8,
    `chars=${content.length} rows=${mountedRows} tables=${tables} codeBlocks=${codeBlocks} renderMs=${renderMs}`,
  );

  return {
    chars: content.length,
    tables,
    codeBlocks,
    renderMs,
    mountedRows,
  };
}

async function measureToolOutputHeavy(checks: PerfCheck[]): Promise<DesktopPerfMetrics['toolOutputHeavy']> {
  const now = Date.now();
  const toolCount = 24;
  const tools = Array.from({ length: toolCount }, (_, index) => {
    const result = largeToolResult(index, 80);
    return {
      id: `perf-tool-${index}`,
      callId: `perf-tool-call-${index}`,
      toolName: index % 2 === 0 ? 'read_file' : 'list_directory',
      args: { path: `D:/Metis/perf/${index}` },
      result,
      status: 'success' as const,
      startedAt: now + index,
      finishedAt: now + index + 10,
      summary: `Synthetic tool result ${index}`,
    };
  });
  const resultChars = tools.reduce((sum, tool) => sum + String(tool.result).length, 0);
  useUiStore.getState().setActiveSection('chat');
  const start = performance.now();
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: [
      {
        id: 'perf-tool-assistant',
        role: 'assistant',
        content: 'Tool output performance sample.',
        createdAt: now,
        tools,
      },
    ],
    runtimeStatus: null,
    streaming: false,
    subagents: [],
  });
  await waitForCondition(() => document.querySelectorAll('.tool-card').length >= toolCount, 'tool cards render');
  await nextFrame();

  const renderMs = Math.round(performance.now() - start);
  const toolCards = document.querySelectorAll('.tool-card').length;
  record(
    checks,
    'perf-tool-output-heavy-renders',
    toolCards >= toolCount,
    `cards=${toolCards} resultChars=${resultChars} renderMs=${renderMs}`,
  );

  const firstToolHead = document.querySelector<HTMLButtonElement>('.tool-card-head');
  record(checks, 'perf-tool-card-head-visible', Boolean(firstToolHead), `cards=${toolCards}`);
  const expandStart = performance.now();
  firstToolHead?.click();
  await waitForCondition(() => Boolean(document.querySelector('.tool-card pre')), 'tool card expanded output');
  await nextFrame();
  const expandMs = Math.round(performance.now() - expandStart);

  record(checks, 'perf-tool-card-large-expand', expandMs <= 300, `expandMs=${expandMs}`);

  return {
    toolCards,
    resultChars,
    renderMs,
    expandMs,
  };
}

async function measureRightRailToolPreview(checks: PerfCheck[]): Promise<DesktopPerfMetrics['rightRailToolPreview']> {
  const content = largeToolResult(999, 900);
  const lines = content.split(/\r?\n/).length;
  const ui = useUiStore.getState();
  ui.setActiveSection('chat');
  ui.setRightRailOpen(true);
  const start = performance.now();
  ui.setToolPreview({
    title: 'Perf Large Tool Output',
    content,
  });
  await waitForCondition(
    () => (document.querySelector('.tool-output-pane')?.textContent || '').includes('Perf Large Tool Output'),
    'right rail large tool preview',
  );
  await nextFrame();

  const renderMs = Math.round(performance.now() - start);
  const pre = document.querySelector<HTMLElement>('.tool-output-pane pre');
  record(
    checks,
    'perf-right-rail-large-tool-preview',
    Boolean(pre && pre.textContent && pre.textContent.length >= content.length),
    `chars=${content.length} lines=${lines} renderMs=${renderMs}`,
  );

  return {
    chars: content.length,
    lines,
    renderMs,
  };
}

function replayMessages(): ChatMessage[] {
  const fixture = TRANSCRIPT_REPLAY_FIXTURE;
  const now = Date.now();
  return [
    {
      id: 'replay-user-ordinary',
      role: 'user',
      content: fixture.ordinaryQuestion,
      createdAt: now,
    },
    {
      id: 'replay-assistant-ordinary',
      role: 'assistant',
      content: fixture.ordinaryAnswer,
      createdAt: now + 1,
    },
    {
      id: 'replay-user-markdown',
      role: 'user',
      content: 'Give me the detailed replay plan.',
      createdAt: now + 2,
    },
    {
      id: 'replay-assistant-markdown',
      role: 'assistant',
      content: fixture.markdownAnswer,
      createdAt: now + 3,
    },
    {
      id: 'replay-assistant-tools',
      role: 'assistant',
      content: 'Replay collected tool results and permission decisions.',
      createdAt: now + 4,
      tools: [...fixture.tools, ...fixture.permissionTools],
    },
  ];
}

async function measureTranscriptReplay(checks: PerfCheck[]): Promise<DesktopPerfMetrics['transcriptReplay']> {
  const fixture = TRANSCRIPT_REPLAY_FIXTURE;
  const ui = useUiStore.getState();
  ui.setActiveSection('chat');
  ui.setSidebarOpen(true);
  ui.setRightRailOpen(true);
  ui.setRightRailMode('files');

  const finalMessages = replayMessages();
  const loadStart = performance.now();
  useChatStore.setState({
    attachments: [],
    composerText: '',
    error: null,
    memoryNotice: null,
    messages: finalMessages,
    runtimeStatus: null,
    streaming: false,
    subagents: fixture.subagents,
  });
  await waitForCondition(
    () => hasThreadWindowFor(finalMessages.length, finalMessages.length),
    'full replay transcript load',
  );
  await nextFrame();
  const fullLoadMs = Math.round(performance.now() - loadStart);

  const replayStart = performance.now();
  let events = 0;
  let rightRailPreviewMs = 0;
  const stats = await measureFramesDuring(async () => {
    const now = Date.now();
    const messages: ChatMessage[] = [];
    const pushMessage = async (message: ChatMessage) => {
      messages.push(message);
      useChatStore.setState({ messages: messages.slice() });
      events += 1;
      await nextFrame();
    };

    useChatStore.setState({
      attachments: [],
      composerText: '',
      error: null,
      memoryNotice: null,
      messages: [],
      runtimeStatus: null,
      streaming: true,
      subagents: [],
    });
    await nextFrame();

    await pushMessage({
      id: 'replay-step-user-ordinary',
      role: 'user',
      content: fixture.ordinaryQuestion,
      createdAt: now,
    });
    await pushMessage({
      id: 'replay-step-assistant-ordinary',
      role: 'assistant',
      content: fixture.ordinaryAnswer,
      createdAt: now + 1,
    });
    await pushMessage({
      id: 'replay-step-user-markdown',
      role: 'user',
      content: 'Give me the detailed replay plan.',
      createdAt: now + 2,
    });
    await pushMessage({
      id: 'replay-step-assistant-markdown',
      role: 'assistant',
      content: fixture.markdownAnswer,
      createdAt: now + 3,
      pending: true,
    });

    messages[messages.length - 1] = { ...messages[messages.length - 1], pending: false };
    useChatStore.setState({ messages: messages.slice(), streaming: false });
    events += 1;
    await nextFrame();

    await pushMessage({
      id: 'replay-step-assistant-tools',
      role: 'assistant',
      content: 'Replay collected tool results.',
      createdAt: now + 4,
      tools: fixture.tools,
    });
    messages[messages.length - 1] = {
      ...messages[messages.length - 1],
      content: 'Replay collected tool results and permission decisions.',
      tools: [...fixture.tools, fixture.permissionTools[0]],
    };
    useChatStore.setState({ messages: messages.slice() });
    events += 1;
    await nextFrame();

    messages[messages.length - 1] = {
      ...messages[messages.length - 1],
      tools: [...fixture.tools, ...fixture.permissionTools],
    };
    useChatStore.setState({ messages: messages.slice() });
    events += 1;
    await nextFrame();

    useChatStore.setState({ subagents: fixture.subagents });
    events += 1;
    await nextFrame();

    const previewStart = performance.now();
    ui.setToolPreview(fixture.rightRailPreview);
    await waitForCondition(
      () => (document.querySelector('.tool-output-pane')?.textContent || '').includes(fixture.rightRailPreview.title),
      'replay right rail tool preview',
    );
    await nextFrame();
    rightRailPreviewMs = Math.round(performance.now() - previewStart);
    events += 1;
  });

  const totalMs = Math.round(performance.now() - replayStart) + fullLoadMs;
  const mountedRows = mountedMessageRows();
  record(
    checks,
    'perf-transcript-replay-completes',
    events >= 9 && mountedRows <= 90 && rightRailPreviewMs > 0,
    `events=${events} messages=${finalMessages.length} rows=${mountedRows} p95=${stats.p95FrameMs} totalMs=${totalMs} railMs=${rightRailPreviewMs}`,
  );

  return {
    events,
    finalMessages: finalMessages.length,
    totalMs,
    p95FrameMs: stats.p95FrameMs,
    mountedRows,
    droppedFramesOver32Ms: stats.droppedFramesOver32Ms,
    rightRailPreviewMs,
  };
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}

async function report(payload: PerfReport): Promise<void> {
  if (window.metis?.reportPerfResult) {
    await window.metis.reportPerfResult(payload);
    return;
  }
  console.info('METIS_PERF_RESULT:', payload);
}

export async function runRendererPerf(): Promise<void> {
  const checks: PerfCheck[] = [];

  try {
    await waitForBoot(checks);
    await waitForCondition(() => Boolean(document.querySelector('.thread-viewport')), 'thread viewport');

    const metrics: DesktopPerfMetrics = {
      longThread: await measureLongThread(checks),
      streamBurst: await measureStreamBurst(checks),
      panelMotion: await measurePanelMotion(checks),
      composerInput: await measureComposerInput(checks),
      markdownHeavy: await measureMarkdownHeavy(checks),
      toolOutputHeavy: await measureToolOutputHeavy(checks),
      rightRailToolPreview: await measureRightRailToolPreview(checks),
      transcriptReplay: await measureTranscriptReplay(checks),
    };
    const budgetResults = evaluateDesktopPerfBudgets(metrics);
    for (const budget of budgetResults) {
      checks.push({
        name: `perf-budget-${budget.id}`,
        ok: budget.ok,
        detail: budget.detail,
      });
    }

    const failedBudget = budgetResults.find(budget => !budget.ok);
    if (failedBudget) {
      await report({
        ok: false,
        checks,
        metrics,
        budgets: {
          limits: DESKTOP_PERF_BUDGETS,
          results: budgetResults,
        },
        error: `Performance budget failed: ${failedBudget.detail}`,
      });
      return;
    }

    await report({
      ok: true,
      checks,
      metrics,
      budgets: {
        limits: DESKTOP_PERF_BUDGETS,
        results: budgetResults,
      },
    });
  } catch (error) {
    checks.push({ name: 'renderer-perf-exception', ok: false, detail: asErrorMessage(error) });
    await report({ ok: false, checks, error: asErrorMessage(error) });
  }
}
