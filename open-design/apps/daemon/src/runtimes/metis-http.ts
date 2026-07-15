import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { PassThrough } from 'node:stream';

type JsonRecord = Record<string, any>;

export type MetisHttpTransportOptions = {
  backendUrl: string;
  token: string;
  designRoot: string;
  stateRoot: string;
  projectId: string;
  projectDir: string;
  conversationId: string;
  prompt: string;
};

function loopbackOrigin(value: string): string {
  const parsed = new URL(value);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)) {
    throw new Error('Metis Design transport only accepts a loopback backend.');
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error('Metis Design transport requires HTTP or HTTPS.');
  }
  if (parsed.username || parsed.password) throw new Error('Metis Design backend URL must not contain credentials.');
  return parsed.origin;
}

function normalizedPath(value: string): string {
  return path.resolve(value).replace(/[\\/]+$/, '').toLowerCase();
}

function assertManagedProject(options: MetisHttpTransportOptions): void {
  const root = normalizedPath(options.designRoot);
  const project = normalizedPath(options.projectDir);
  const expected = normalizedPath(path.join(options.designRoot, options.projectId));
  if (!options.projectId || project !== expected || !project.startsWith(`${root}${path.sep}`)) {
    throw new Error('Metis Design project is outside the managed Design root.');
  }
}

async function requestJson(origin: string, pathname: string, init: RequestInit = {}): Promise<JsonRecord> {
  const response = await fetch(`${origin}${pathname}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  });
  const body = await response.json().catch(() => ({})) as JsonRecord;
  if (!response.ok) throw new Error(String(body.error || body.message || `HTTP ${response.status}`));
  return body;
}

function mappingPath(stateRoot: string, projectId: string, conversationId: string): string {
  const digest = createHash('sha256').update(`${projectId}\0${conversationId}`).digest('hex');
  return path.join(stateRoot, 'metis-sessions', `${digest}.json`);
}

function readMapping(filePath: string): JsonRecord | null {
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function writeMapping(filePath: string, value: JsonRecord): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  fs.renameSync(temporary, filePath);
}

async function ensureWorkspace(origin: string, projectDir: string, projectId: string): Promise<string> {
  const listed = await requestJson(origin, '/workspaces');
  const existing = (Array.isArray(listed.workspaces) ? listed.workspaces : [])
    .find((item: JsonRecord) => normalizedPath(String(item?.path || '')) === normalizedPath(projectDir));
  if (existing?.id) return String(existing.id);
  const created = await requestJson(origin, '/workspaces', {
    method: 'POST',
    body: JSON.stringify({ path: projectDir, name: `Design ${projectId}` }),
  });
  if (!created.id) throw new Error('Metis did not return a workspace id for the Design project.');
  return String(created.id);
}

async function ensureSession(origin: string, options: MetisHttpTransportOptions): Promise<string> {
  const workspaceId = await ensureWorkspace(origin, options.projectDir, options.projectId);
  const filePath = mappingPath(options.stateRoot, options.projectId, options.conversationId);
  const mapped = readMapping(filePath);
  if (mapped?.sessionId) {
    const existing = await requestJson(origin, `/sessions/${encodeURIComponent(String(mapped.sessionId))}`).catch(() => null);
    if (existing?.id && String(existing.workspace_id || '') === workspaceId) return String(existing.id);
  }
  const modeResult = await requestJson(origin, '/mode').catch(() => ({ mode: 'auto_guard' }));
  const mode = String(modeResult.mode || 'auto_guard');
  const created = await requestJson(origin, '/sessions', {
    method: 'POST',
    body: JSON.stringify({ workspace_id: workspaceId, mode, activate: false }),
  });
  if (!created.id || created.active !== false) throw new Error('Metis could not create an isolated Design session.');
  writeMapping(filePath, {
    sessionId: String(created.id),
    workspaceId,
    projectDir: path.resolve(options.projectDir),
    updatedAt: new Date().toISOString(),
  });
  return String(created.id);
}

function eventText(event: JsonRecord): string {
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {};
  return String(payload.text || payload.delta || event.text || event.delta || '');
}

function eventError(event: JsonRecord): string {
  const kind = String(event?.kind || event?.type || '');
  if (!['error', 'run_failed'].includes(kind)) return '';
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {};
  const nested = payload.error && typeof payload.error === 'object' ? payload.error : {};
  return String(nested.message || payload.message || event.message || 'Metis Design run failed.');
}

function normalizePermission(payload: JsonRecord, status: string): JsonRecord {
  const permission = payload.permission && typeof payload.permission === 'object' ? payload.permission : {};
  const explainer = permission.permission_explainer && typeof permission.permission_explainer === 'object'
    ? permission.permission_explainer
    : permission.explainer && typeof permission.explainer === 'object'
      ? permission.explainer
      : {};
  const choices = (Array.isArray(permission.choices) ? permission.choices : [])
    .filter((choice: unknown) => choice && typeof choice === 'object')
    .map((choice: JsonRecord) => ({
      value: String(choice.value || ''),
      label: String(choice.label || choice.value || ''),
      description: String(choice.description || ''),
      approved: Boolean(choice.approved),
      requiresRootPicker: Boolean(choice.requires_root_picker || choice.requiresRootPicker),
    }))
    .filter((choice: JsonRecord) => choice.value && choice.label);
  return {
    requestId: String(payload.request_id || payload.requestId || permission.request_id || permission.requestId || ''),
    status: String(permission.status || status),
    toolName: String(payload.tool_name || payload.toolName || permission.tool_name || permission.toolName || 'tool'),
    explanation: String(explainer.explanation || ''),
    reasoning: String(explainer.reasoning || ''),
    risk: String(explainer.risk || ''),
    riskLevel: String(explainer.riskLevel || explainer.risk_level || ''),
    choices,
    defaultChoice: String(permission.default_choice || permission.defaultChoice || ''),
    suggestedWritableRoot: String(permission.suggested_writable_root || permission.suggestedWritableRoot || ''),
    ...(typeof permission.approved === 'boolean' ? { approved: permission.approved } : {}),
    selectedChoice: String(permission.choice || payload.choice || ''),
  };
}

export function metisEventToAgentEvent(event: JsonRecord): JsonRecord | null {
  const kind = String(event?.kind || event?.type || '');
  const payload = event?.payload && typeof event.payload === 'object' ? event.payload : {};
  const error = eventError(event);
  if (error) return { type: 'error', message: error };
  if (['message_delta', 'content_delta', 'text_delta', 'content'].includes(kind)) {
    const delta = eventText(event);
    return delta ? { type: 'text_delta', delta } : null;
  }
  if (kind === 'thinking') {
    const delta = eventText(event);
    return delta ? { type: 'thinking_delta', delta } : null;
  }
  if (kind === 'tool_requested') {
    return {
      type: 'tool_use',
      id: String(payload.call_id || payload.callId || ''),
      name: String(payload.tool_name || payload.toolName || 'tool'),
      input: payload.arguments && typeof payload.arguments === 'object' ? payload.arguments : {},
    };
  }
  if (kind === 'tool_succeeded' || kind === 'tool_failed') {
    return {
      type: 'tool_result',
      toolUseId: String(payload.call_id || payload.callId || ''),
      content: String(payload.result || payload.output || payload.message || ''),
      isError: kind === 'tool_failed',
    };
  }
  if (kind === 'todo_update') {
    return { type: 'todo', todos: Array.isArray(payload.todos) ? payload.todos : [] };
  }
  if (kind === 'permission_required') {
    return { type: 'status', label: 'waiting_permission', permission: normalizePermission(payload, 'requested') };
  }
  if (['permission_answered', 'permission_applied', 'permission_rejected', 'permission_expired'].includes(kind)) {
    return { type: 'status', label: 'waiting_permission', permission: normalizePermission(payload, kind.replace('permission_', '')) };
  }
  if (kind === 'runtime_status') {
    return { type: 'status', label: String(payload.phase || payload.message || 'running') };
  }
  if (kind === 'done' || kind === 'run_completed') {
    const usage = payload.usage && typeof payload.usage === 'object' ? payload.usage : {};
    return {
      type: 'usage',
      usage: {
        input_tokens: Number(usage.prompt_tokens || usage.input_tokens || 0),
        output_tokens: Number(usage.completion_tokens || usage.output_tokens || 0),
      },
    };
  }
  return null;
}

function parseSsePackets(buffer: string, accept: (payload: JsonRecord | '[DONE]') => void): string {
  const packets = buffer.split(/\r?\n\r?\n/);
  const rest = packets.pop() || '';
  for (const packet of packets) {
    for (const line of packet.split(/\r?\n/)) {
      if (!line.startsWith('data: ')) continue;
      const raw = line.slice(6);
      if (raw === '[DONE]') accept(raw);
      else {
        try { accept(JSON.parse(raw)); } catch { /* Ignore malformed transport frames. */ }
      }
    }
  }
  return rest;
}

export class MetisHttpTransport extends EventEmitter {
  stdout = new PassThrough();
  stderr = new PassThrough();
  stdin = null;
  pid = undefined;
  killed = false;
  exitCode: number | null = null;
  signalCode: NodeJS.Signals | null = null;
  private activeRunId = '';
  private closed = false;
  private readonly abortController = new AbortController();

  constructor(private readonly options: MetisHttpTransportOptions) {
    super();
    setImmediate(() => void this.start());
  }

  kill(signal: NodeJS.Signals = 'SIGTERM'): boolean {
    if (this.closed) return false;
    this.killed = true;
    this.signalCode = signal;
    this.abortController.abort();
    if (this.activeRunId) {
      const origin = loopbackOrigin(this.options.backendUrl);
      void fetch(`${origin}/runs/${encodeURIComponent(this.activeRunId)}/cancel`, { method: 'POST' }).catch(() => {});
    }
    this.finish(null, signal);
    return true;
  }

  private finish(code: number | null, signal: NodeJS.Signals | null): void {
    if (this.closed) return;
    this.closed = true;
    this.exitCode = code;
    this.signalCode = signal;
    this.stdout.end();
    this.stderr.end();
    this.emit('exit', code, signal);
    this.emit('close', code, signal);
  }

  private async start(): Promise<void> {
    try {
      assertManagedProject(this.options);
      const origin = loopbackOrigin(this.options.backendUrl);
      const sessionId = await ensureSession(origin, this.options);
      if (this.closed) return;
      const created = await requestJson(origin, '/runs', {
        method: 'POST',
        headers: { 'X-Metis-Design-Token': this.options.token },
        body: JSON.stringify({
          message: this.options.prompt,
          session_id: sessionId,
          assistant_id: `design-${Date.now()}`,
          surface_mode: 'design',
          execution_profile: 'local_direct',
          metis_design: {
            project_id: this.options.projectId,
            project_root: path.resolve(this.options.projectDir),
            conversation_id: this.options.conversationId,
          },
        }),
      });
      this.activeRunId = String(created.run_id || created.id || '');
      if (!this.activeRunId) throw new Error('Metis did not return a Design run id.');

      const response = await fetch(
        `${origin}/runs/${encodeURIComponent(this.activeRunId)}/events?schema=v2&after=0`,
        { signal: this.abortController.signal },
      );
      if (!response.ok || !response.body) throw new Error(`Metis event stream failed (HTTP ${response.status}).`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let failure = '';
      const accept = (payload: JsonRecord | '[DONE]') => {
        if (payload === '[DONE]') return;
        failure ||= eventError(payload);
        const mapped = metisEventToAgentEvent(payload);
        if (mapped) this.emit('agent', mapped);
      };
      while (!this.closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        buffer = parseSsePackets(buffer, accept);
      }
      if (buffer.trim()) parseSsePackets(`${buffer}\n\n`, accept);
      const finalRun = await requestJson(origin, `/runs/${encodeURIComponent(this.activeRunId)}`).catch(() => null);
      if (failure || finalRun?.status === 'failed') {
        throw new Error(failure || String(finalRun?.error || 'Metis Design run failed.'));
      }
      if (!this.closed) this.finish(0, null);
    } catch (error) {
      if (this.closed || (error as Error)?.name === 'AbortError') return;
      const message = error instanceof Error ? error.message : String(error);
      this.emit('agent', { type: 'error', message });
      this.stderr.write(`${message}\n`);
      this.finish(1, null);
    }
  }
}

export function createMetisHttpTransport(options: MetisHttpTransportOptions): MetisHttpTransport {
  return new MetisHttpTransport(options);
}
