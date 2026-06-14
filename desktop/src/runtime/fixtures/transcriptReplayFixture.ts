import type { ChatSubagentEvent, ChatToolEvent } from '../../lib/types';

export interface TranscriptReplayFixture {
  id: string;
  title: string;
  ordinaryQuestion: string;
  ordinaryAnswer: string;
  markdownAnswer: string;
  tools: ChatToolEvent[];
  permissionTools: ChatToolEvent[];
  subagents: ChatSubagentEvent[];
  rightRailPreview: {
    title: string;
    content: string;
  };
}

function markdownAnswer(): string {
  const sections = Array.from({ length: 12 }, (_, section) => {
    const bullets = Array.from(
      { length: 8 },
      (_, index) => `- Replay section ${section}.${index}: stable no-secret transcript content for perf replay.`,
    ).join('\n');
    return `## Replay Section ${section}\n\n${bullets}\n\n\`\`\`ts\nexport const replay${section} = ${section};\n\`\`\``;
  }).join('\n\n');
  return `# Replay Plan\n\nThis fixture imitates a mixed Metis desktop session without secrets.\n\n${sections}`;
}

function toolResult(seed: number, rows = 40): string {
  return JSON.stringify(
    {
      seed,
      files: Array.from({ length: rows }, (_, index) => ({
        index,
        path: `D:/Metis/Replay/src/file-${seed}-${index}.ts`,
        status: index % 7 === 0 ? 'review' : 'ok',
        summary: `Replay fixture row ${index} ${'x'.repeat(48)}`,
      })),
    },
    null,
    2,
  );
}

export const TRANSCRIPT_REPLAY_FIXTURE: TranscriptReplayFixture = {
  id: 'metis-replay-001',
  title: 'Metis Mixed Transcript Replay',
  ordinaryQuestion: 'Summarize the current workspace and propose the next safe step.',
  ordinaryAnswer: 'Metis inspected the workspace, found the desktop shell and backend boundary, and proposed a narrow next step.',
  markdownAnswer: markdownAnswer(),
  tools: Array.from({ length: 8 }, (_, index) => ({
    id: `replay-tool-${index}`,
    callId: `replay-tool-call-${index}`,
    toolName: index % 2 === 0 ? 'read_file' : 'list_directory',
    args: { path: `D:/Metis/Replay/${index}` },
    result: toolResult(index),
    status: 'success',
    startedAt: 1000 + index * 10,
    finishedAt: 1010 + index * 10,
    summary: `Replay tool ${index} completed`,
  })),
  permissionTools: [
    {
      id: 'replay-permission-waiting',
      callId: 'replay-permission-waiting',
      requestId: 'replay-permission-request',
      toolName: 'write_file',
      args: { path: 'D:/Metis/Replay/notes.md', content: 'No secret replay content.' },
      status: 'waiting_approval',
      startedAt: 1200,
      summary: 'Waiting for permission in replay fixture',
    },
    {
      id: 'replay-permission-allow',
      callId: 'replay-permission-allow',
      requestId: 'replay-permission-allow-request',
      toolName: 'write_file',
      args: { path: 'D:/Metis/Replay/allowed.md', content: 'Allowed replay content.' },
      result: 'Replay permission allowed; no real file was written.',
      status: 'success',
      startedAt: 1210,
      finishedAt: 1230,
      summary: 'Permission allowed in replay fixture',
    },
    {
      id: 'replay-permission-deny',
      callId: 'replay-permission-deny',
      requestId: 'replay-permission-deny-request',
      toolName: 'delete_file',
      args: { path: 'D:/Metis/Replay/protected.md' },
      result: '[Permission denied] Replay denied delete_file.',
      status: 'error',
      startedAt: 1240,
      finishedAt: 1260,
      summary: 'Permission denied in replay fixture',
      errorHint: 'Replay denied a destructive action.',
    },
  ],
  subagents: [
    {
      taskId: 'replay-explore',
      name: 'delegate_explore',
      status: 'done',
      progress: 100,
      summary: 'Replay explored workspace structure.',
      result: 'Found desktop, backend, perf harness, and New-Build docs.',
      startedAt: 1300,
      updatedAt: 1400,
      finishedAt: 1400,
    },
    {
      taskId: 'replay-verify',
      name: 'delegate_verify',
      status: 'done',
      progress: 100,
      summary: 'Replay verified no-secret fixture rules.',
      result: 'No API keys or private paths beyond synthetic Metis replay paths.',
      startedAt: 1310,
      updatedAt: 1410,
      finishedAt: 1410,
    },
  ],
  rightRailPreview: {
    title: 'Replay Tool Preview',
    content: toolResult(999, 220),
  },
};

