import type {
  AgentEventContract,
  ActiveChatRunPayload,
  ChatRunPayload,
  ChatRunsPayload,
  ChatStreamEvent,
  CompactStatusPayload,
  CronTask,
  DeskGoalLogEntry,
  DeskStatusPayload,
  FileChangeRevertResult,
  FirstRunStatus,
  MemoryPayload,
  McpConfigSource,
  McpServerStatus,
  McpStatusPayload,
  ModelCapabilities,
  ParsedFile,
  PermissionAccessMode,
  PermissionAuditEntry,
  PermissionRule,
  PermissionStatePayload,
  ProviderModelCatalog,
  ProviderProfile,
  ProviderRegistryEntry,
  ProviderRegistryInput,
  ProviderRegistryProbeResult,
  ProviderStatusPayload,
  ProviderUsagePayload,
  ProviderValidation,
  RewindResult,
  RuntimeSettings,
  SearchResult,
  Session,
  SessionCheckpoint,
  SessionsPayload,
  SkillDetail,
  SkillSummary,
  WorkspaceFile,
  WorkspacesPayload,
  WorkspaceTreeNode,
} from './types';
import type { FileChangeSummary } from './diffPreview';

let cachedBase: string | null = null;
const MAX_JSON_RESPONSE_CHARS = 50 * 1024 * 1024;

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => stringValue(item)).filter(Boolean) : [];
}

function providerProfileFromRecord(row: Record<string, unknown>): ProviderProfile {
  const capabilities = recordValue(row.capabilities);
  const modelContextWindows = recordValue(row.model_context_windows ?? row.modelContextWindows);
  const modelNotes = recordValue(row.model_notes);
  return {
    providerId: stringValue(row.provider_id),
    displayName: stringValue(row.display_name),
    backendType: stringValue(row.backend_type),
    aliases: stringArray(row.aliases),
    baseUrl: stringValue(row.base_url),
    chatCompletionsPath: stringValue(row.chat_completions_path),
    defaultModel: stringValue(row.default_model),
    fallbackModels: stringArray(row.fallback_models),
    apiKeyRequired: Boolean(row.api_key_required),
    openaiCompatible: Boolean(row.openai_compatible),
    capabilities: {
      stream: Boolean(capabilities.stream),
      tools: Boolean(capabilities.tools),
      vision: Boolean(capabilities.vision),
      parallelToolCalls: Boolean(capabilities.parallel_tool_calls ?? capabilities.parallelToolCalls),
      requiresReasoningPassback: Boolean(
        capabilities.requires_reasoning_passback ?? capabilities.requiresReasoningPassback,
      ),
    },
    modelContextWindows: Object.entries(modelContextWindows).reduce<Record<string, number>>((acc, [key, value]) => {
      const count = numberValue(value);
      if (count > 0) acc[key] = count;
      return acc;
    }, {}),
    modelNotes: Object.fromEntries(Object.entries(modelNotes).map(([key, value]) => [key, stringValue(value)])),
  };
}

function nullableBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

function providerConformanceFromRecord(row: Record<string, unknown>) {
  return {
    ok: Boolean(row.ok),
    providerId: stringValue(row.provider_id),
    baseUrl: stringValue(row.base_url),
    model: stringValue(row.model),
    path: stringValue(row.path),
    requiresReasoningPassback: nullableBoolean(row.requires_reasoning_passback),
    parallelToolCalls: nullableBoolean(row.parallel_tool_calls),
    reasoningMode: stringValue(row.reasoning_mode),
    cacheFields: stringValue(row.cache_fields),
    toolSchemaStrictness: stringValue(row.tool_schema_strictness),
    multiRoundContinuation: stringValue(row.multi_round_continuation),
    error: stringValue(row.error),
    notes: stringArray(row.notes),
  };
}

function providerValidationFromRecord(row: Record<string, unknown>): ProviderValidation {
  const provider = recordValue(row.provider);
  const conformance = recordValue(row.conformance);
  return {
    ok: Boolean(row.ok),
    code: stringValue(row.code),
    title: stringValue(row.title),
    message: stringValue(row.message),
    hint: stringValue(row.hint),
    recoverable: Boolean(row.recoverable),
    providerId: stringValue(row.provider_id),
    displayName: stringValue(row.display_name),
    backend: stringValue(row.backend),
    baseUrl: stringValue(row.base_url),
    chatUrl: stringValue(row.chat_url),
    model: stringValue(row.model),
    apiKeyRequired: Boolean(row.api_key_required),
    hasApiKey: Boolean(row.has_api_key),
    warnings: stringArray(row.warnings),
    provider: Object.keys(provider).length ? providerProfileFromRecord(provider) : undefined,
    conformance: Object.keys(conformance).length ? providerConformanceFromRecord(conformance) : undefined,
  };
}

function providerModelCatalogFromRecord(row: Record<string, unknown>): ProviderModelCatalog {
  const models = Array.isArray(row.models) ? row.models : [];
  return {
    ok: Boolean(row.ok),
    kind: 'models',
    status: stringValue(row.status),
    providerId: stringValue(row.provider_id ?? row.providerId),
    displayName: stringValue(row.display_name ?? row.displayName),
    baseUrl: stringValue(row.base_url ?? row.baseUrl),
    apiBaseUrl: stringValue(row.api_base_url ?? row.apiBaseUrl),
    model: stringValue(row.model),
    modelsUrl: stringValue(row.models_url ?? row.modelsUrl),
    message: stringValue(row.message),
    hint: stringValue(row.hint),
    models: models.map(item => {
      const model = recordValue(item);
      return {
        id: stringValue(model.id),
        displayName: stringValue(model.display_name ?? model.displayName) || stringValue(model.id),
        ownedBy: stringValue(model.owned_by ?? model.ownedBy),
        type: stringValue(model.type) || 'chat',
        created: numberValue(model.created),
        contextLimit: numberValue(model.context_limit ?? model.contextLimit),
        chatCapable: model.chat_capable === undefined && model.chatCapable === undefined ? true : Boolean(model.chat_capable ?? model.chatCapable),
      };
    }),
  };
}

function providerUsageFromRecord(row: Record<string, unknown>): ProviderUsagePayload {
  const today = recordValue(row.today);
  const total = recordValue(row.total);
  const quota = recordValue(row.quota);
  return {
    ok: Boolean(row.ok),
    kind: 'usage',
    status: stringValue(row.status),
    providerId: stringValue(row.provider_id ?? row.providerId),
    displayName: stringValue(row.display_name ?? row.displayName),
    baseUrl: stringValue(row.base_url ?? row.baseUrl),
    apiBaseUrl: stringValue(row.api_base_url ?? row.apiBaseUrl),
    model: stringValue(row.model),
    usageUrl: stringValue(row.usage_url ?? row.usageUrl),
    mode: stringValue(row.mode),
    isValid: row.is_valid === undefined && row.isValid === undefined ? true : Boolean(row.is_valid ?? row.isValid),
    planName: stringValue(row.plan_name ?? row.planName),
    remaining: numberValue(row.remaining),
    balance: numberValue(row.balance),
    unit: stringValue(row.unit),
    today: {
      requests: numberValue(today.requests),
      totalTokens: numberValue(today.total_tokens ?? today.totalTokens),
      cost: numberValue(today.cost),
    },
    total: {
      requests: numberValue(total.requests),
      totalTokens: numberValue(total.total_tokens ?? total.totalTokens),
      cost: numberValue(total.cost),
    },
    quota: {
      limit: numberValue(quota.limit),
      used: numberValue(quota.used),
      remaining: numberValue(quota.remaining),
    },
    message: stringValue(row.message),
    hint: stringValue(row.hint),
  };
}

function compactStatusFromRecord(row: Record<string, unknown>): CompactStatusPayload {
  const beforeContextMessages = numberValue(row.before_context_messages ?? row.beforeContextMessages);
  const afterContextMessages = numberValue(row.after_context_messages ?? row.afterContextMessages);
  return {
    running: Boolean(row.running),
    ok: Boolean(row.ok),
    beforeCount: beforeContextMessages || numberValue(row.before_count ?? row.beforeCount),
    afterCount: afterContextMessages || numberValue(row.after_count ?? row.afterCount),
    beforeContextMessages,
    afterContextMessages,
    summaryPreview: stringValue(row.summary_preview ?? row.summaryPreview),
    updatedAt: numberValue(row.updated_at ?? row.updatedAt),
    error: stringValue(row.error),
  };
}

function compactStateFromRecord(value: unknown): Session['compactState'] {
  const row = recordValue(value);
  const summary = stringValue(row.summary);
  if (!summary) return null;
  return {
    summary,
    boundaryMessageId: stringValue(row.boundary_message_id ?? row.boundaryMessageId),
    boundaryIndex: numberValue(row.boundary_index ?? row.boundaryIndex),
    compactedAt: numberValue(row.compacted_at ?? row.compactedAt),
    compactCount: numberValue(row.compact_count ?? row.compactCount),
  };
}

function modelCapabilitiesFromRecord(row: Record<string, unknown>): ModelCapabilities {
  return {
    tier: numberValue(row.tier) || 2,
    tierLabel: stringValue(row.tier_label ?? row.tierLabel) || '中',
    family: stringValue(row.family) || 'unknown',
    model: stringValue(row.model),
    detectionMethod: stringValue(row.detection_method ?? row.detectionMethod),
    effectiveContext: numberValue(row.effective_context ?? row.effectiveContext),
    supportsVision: Boolean(row.supports_vision ?? row.supportsVision),
    visionProtocol: stringValue(row.vision_protocol ?? row.visionProtocol) || 'legacy',
    supportsToolCalling: Boolean(row.supports_tool_calling ?? row.supportsToolCalling),
    supportsStructuredOutput: Boolean(row.supports_structured_output ?? row.supportsStructuredOutput),
    instructionAdherence: stringValue(row.instruction_adherence ?? row.instructionAdherence) || 'medium',
    toolCount: numberValue(row.tool_count ?? row.toolCount),
    totalToolCount: numberValue(row.total_tool_count ?? row.totalToolCount),
  };
}

function permissionRuleFromRecord(row: Record<string, unknown>): PermissionRule {
  const argsMatch = recordValue(row.args_match ?? row.argsMatch);
  return {
    id: stringValue(row.id),
    tool: stringValue(row.tool) || '*',
    action: stringValue(row.action) || 'ask',
    argsMatch: Object.fromEntries(Object.entries(argsMatch).map(([key, value]) => [key, stringValue(value)])),
    source: stringValue(row.source),
    createdAt: numberValue(row.created_at ?? row.createdAt),
    updatedAt: numberValue(row.updated_at ?? row.updatedAt),
    dangerousAllow: Boolean(row.dangerous_allow ?? row.dangerousAllow),
  };
}

function permissionAuditFromRecord(row: Record<string, unknown>): PermissionAuditEntry {
  return {
    id: stringValue(row.id),
    createdAt: numberValue(row.created_at ?? row.createdAt),
    workspaceId: stringValue(row.workspace_id ?? row.workspaceId),
    sessionId: stringValue(row.session_id ?? row.sessionId),
    cwd: stringValue(row.cwd),
    requestId: stringValue(row.request_id ?? row.requestId),
    callId: stringValue(row.call_id ?? row.callId),
    tool: stringValue(row.tool),
    action: stringValue(row.action),
    approved: Boolean(row.approved),
    remember: stringValue(row.remember),
    ruleId: stringValue(row.rule_id ?? row.ruleId),
    source: stringValue(row.source),
    arguments: row.arguments,
    decisionSource: stringValue(row.decision_source ?? row.decisionSource),
    decisionReason: stringValue(row.decision_reason ?? row.decisionReason),
    riskLevel: stringValue(row.risk_level ?? row.riskLevel),
    mode: stringValue(row.mode),
  };
}

function permissionControlPlaneFromRecord(row: Record<string, unknown>) {
  const dangerousRaw = row.dangerous_allow_rules ?? row.dangerousAllowRules;
  const dangerous = Array.isArray(dangerousRaw) ? dangerousRaw : [];
  return {
    version: stringValue(row.version),
    mode: stringValue(row.mode),
    decisionOrder: stringArray(row.decision_order ?? row.decisionOrder),
    availableModes: stringArray(row.available_modes ?? row.availableModes),
    dangerousAllowRules: dangerous.map(item => permissionRuleFromRecord(recordValue(item))),
    dangerousAllowCount: numberValue(row.dangerous_allow_count ?? row.dangerousAllowCount),
    notes: stringArray(row.notes),
  };
}

const COMPOSER_PERMISSION_SOURCE = 'composer_access';

function isComposerAccessRule(rule: PermissionRule): boolean {
  return rule.source === COMPOSER_PERMISSION_SOURCE && rule.tool === '*' && Object.keys(rule.argsMatch || {}).length === 0;
}

export async function apiBase(): Promise<string> {
  const port = window.metis ? await window.metis.backendPort() : null;
  if (!port) {
    throw new Error('Metis backend is not ready yet. Please wait for initialization.');
  }
  cachedBase = `http://127.0.0.1:${port}`;
  return cachedBase;
}

// FABLEADV-34: 心跳探测——检测"进程活着但 API 假死"（进程崩溃由 boot 事件覆盖）。
export async function pingHealth(timeoutMs = 4000): Promise<boolean> {
  try {
    const base = await apiBase();
    const response = await fetch(`${base}/health`, { signal: AbortSignal.timeout(timeoutMs) });
    return response.ok;
  } catch {
    return false;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await apiBase();
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  });
  const text = await response.text();
  if (text.length > MAX_JSON_RESPONSE_CHARS) {
    throw new Error('Response too large (>50MB)');
  }
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const row = recordValue(data);
    throw new Error(stringValue(row.message) || stringValue(row.error) || `HTTP ${response.status}`);
  }
  return data as T;
}

export async function getSessions(): Promise<SessionsPayload> {
  const data = await requestJson<Record<string, unknown>>('/sessions');
  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  return {
    activeSessionId: stringValue(data.active_id) || null,
    activeWorkspaceId: stringValue(data.active_workspace_id),
    sessions: sessions.map(item => {
      const row = recordValue(item);
      return {
        id: stringValue(row.id),
        title: stringValue(row.title) || 'Metis Chat',
        workspaceId: stringValue(row.workspace_id),
        messageCount: numberValue(row.message_count),
        createdAt: numberValue(row.created_at),
        updatedAt: numberValue(row.updated_at),
      };
    }),
  };
}

export async function createSession(): Promise<{ id: string; workspaceId: string }> {
  const data = await requestJson<Record<string, unknown>>('/sessions', {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return { id: stringValue(data.id), workspaceId: stringValue(data.workspace_id) };
}

export async function getSession(sessionId: string): Promise<Session> {
  const data = await requestJson<Record<string, unknown>>(`/sessions/${encodeURIComponent(sessionId)}`);
  return {
    id: stringValue(data.id),
    title: stringValue(data.title) || 'Metis Chat',
    workspaceId: stringValue(data.workspace_id),
    mode: stringValue(data.mode) || 'auto',
    history: Array.isArray(data.history) ? (data.history as Session['history']) : [],
    compactState: compactStateFromRecord(data.compact_state ?? data.compactState),
    createdAt: numberValue(data.created_at),
    updatedAt: numberValue(data.updated_at),
  };
}

export async function getSessionCheckpoints(sessionId: string): Promise<SessionCheckpoint[]> {
  const data = await requestJson<Record<string, unknown>>(`/sessions/${encodeURIComponent(sessionId)}/checkpoints`);
  const checkpoints = Array.isArray(data.checkpoints) ? data.checkpoints : [];
  return checkpoints.map(item => {
    const row = recordValue(item);
    const files = Array.isArray(row.files) ? row.files : [];
    return {
      checkpointId: stringValue(row.checkpoint_id),
      sessionId: stringValue(row.session_id),
      anchorIndex: numberValue(row.anchor_index),
      userMessageId: stringValue(row.user_message_id),
      reason: stringValue(row.reason),
      createdAt: numberValue(row.created_at),
      completedAt: numberValue(row.completed_at),
      status: stringValue(row.status),
      fileCount: numberValue(row.file_count),
      files: files.map(file => {
        const fileRow = recordValue(file);
        return {
          relativePath: stringValue(fileRow.relative_path),
          existed: Boolean(fileRow.existed),
          skipped: stringValue(fileRow.skipped),
        };
      }),
    };
  });
}

export async function rewindSession(
  sessionId: string,
  payload: {
    checkpointId?: string;
    messageId?: string;
    anchorIndex?: number;
    mode: 'conversation' | 'files' | 'both';
  },
): Promise<RewindResult> {
  const data = await requestJson<Record<string, unknown>>(`/sessions/${encodeURIComponent(sessionId)}/rewind`, {
    method: 'POST',
    body: JSON.stringify({
      checkpoint_id: payload.checkpointId,
      message_id: payload.messageId,
      anchor_index: payload.anchorIndex,
      mode: payload.mode,
    }),
  });
  const skipped = Array.isArray(data.skipped) ? data.skipped : [];
  return {
    ok: Boolean(data.ok),
    error: stringValue(data.error),
    mode: (stringValue(data.mode) as RewindResult['mode']) || payload.mode,
    checkpointId: stringValue(data.checkpoint_id),
    safetyCheckpointId: stringValue(data.safety_checkpoint_id),
    historyLength: numberValue(data.history_length),
    restored: stringArray(data.restored),
    skipped: skipped.map(item => {
      const row = recordValue(item);
      return {
        path: stringValue(row.path),
        relativePath: stringValue(row.relative_path),
        reason: stringValue(row.reason),
      };
    }),
  };
}

export async function switchSession(sessionId: string): Promise<void> {
  await requestJson(`/sessions/${encodeURIComponent(sessionId)}/switch`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function deleteSession(sessionId: string): Promise<void> {
  await requestJson(`/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
}

export async function renameSessionTitle(sessionId: string, title: string): Promise<void> {
  await requestJson(`/sessions/${encodeURIComponent(sessionId)}/title`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  });
}

export async function resetConversation(): Promise<{ sessionId: string | null; workspaceId: string }> {
  const data = await requestJson<Record<string, unknown>>('/reset', {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return {
    sessionId: stringValue(data.session_id) || null,
    workspaceId: stringValue(data.workspace_id),
  };
}

export async function compactConversation(): Promise<CompactStatusPayload> {
  const data = await requestJson<Record<string, unknown>>('/compact', {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return compactStatusFromRecord(data);
}

export async function getCompactStatus(): Promise<CompactStatusPayload> {
  const data = await requestJson<Record<string, unknown>>('/compact/status');
  return compactStatusFromRecord(data);
}

export async function getWorkspaces(): Promise<WorkspacesPayload> {
  const data = await requestJson<Record<string, unknown>>('/workspaces');
  const workspaces = Array.isArray(data.workspaces) ? data.workspaces : [];
  return {
    activeWorkspaceId: stringValue(data.active_id),
    workspaces: workspaces.map(item => {
      const row = recordValue(item);
      return {
        id: stringValue(row.id),
        name: stringValue(row.name),
        path: stringValue(row.path),
        createdAt: numberValue(row.created_at),
        updatedAt: numberValue(row.updated_at),
      };
    }),
  };
}

export async function createWorkspace(path: string): Promise<{ id: string; name: string; path: string }> {
  const data = await requestJson<Record<string, unknown>>('/workspaces', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
  return { id: stringValue(data.id), name: stringValue(data.name), path: stringValue(data.path) };
}

export async function switchWorkspace(workspaceId: string): Promise<{ sessionId: string }> {
  const data = await requestJson<Record<string, unknown>>(`/workspaces/${encodeURIComponent(workspaceId)}/switch`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return { sessionId: stringValue(data.session_id) };
}

export async function clearWorkspaceSessions(workspaceId: string): Promise<void> {
  await requestJson(`/workspaces/${encodeURIComponent(workspaceId)}/sessions`, { method: 'DELETE' });
}

export async function removeWorkspace(workspaceId: string): Promise<void> {
  await requestJson(`/workspaces/${encodeURIComponent(workspaceId)}`, { method: 'DELETE' });
}

export async function getSettings(): Promise<RuntimeSettings> {
  const data = await requestJson<Record<string, unknown>>('/settings');
  const apiKey = stringValue(data.api_key);
  return {
    backend: stringValue(data.backend) || 'openai',
    providerId: stringValue(data.provider_id) || stringValue(data.backend) || 'openai',
    baseUrl: stringValue(data.base_url),
    model: stringValue(data.model),
    temperature: numberValue(data.temperature),
    reasoningEffort: stringValue(data.reasoning_effort) || 'off',
    maxTokens: numberValue(data.max_tokens),
    apiKey,
    hasApiKey: Boolean(data.has_api_key) || apiKey.length > 0,
    autoMemory: Boolean(data.auto_memory),
    autoSkills: Boolean(data.auto_skills),
    proxyMode: (stringValue(data.proxy_mode) || 'system') as RuntimeSettings['proxyMode'],
    proxyScheme: stringValue(data.proxy_scheme) || 'http',
    proxyHost: stringValue(data.proxy_host) || '127.0.0.1',
    proxyPort: stringValue(data.proxy_port) || '7890',
    proxyBypass: stringValue(data.proxy_bypass),
    terminalShell: terminalShellValue(data.terminal_shell),
    pythonPath: stringValue(data.python_path),
    providerValidation: providerValidationFromRecord(recordValue(data.provider_validation)),
  };
}

function terminalShellValue(value: unknown): RuntimeSettings['terminalShell'] {
  const shell = stringValue(value).toLowerCase();
  return shell === 'powershell' || shell === 'cmd' || shell === 'bash' || shell === 'sh' || shell === 'shell' ? shell : 'powershell';
}

export async function updateSettings(settings: Partial<RuntimeSettings> & { apiKey?: string }): Promise<void> {
  await requestJson('/settings', {
    method: 'POST',
    body: JSON.stringify({
      backend: settings.backend,
      provider_id: settings.providerId,
      base_url: settings.baseUrl,
      model: settings.model,
      temperature: settings.temperature,
      reasoning_effort: settings.reasoningEffort,
      max_tokens: settings.maxTokens,
      api_key: settings.apiKey,
      auto_memory: settings.autoMemory,
      auto_skills: settings.autoSkills,
      proxy_mode: settings.proxyMode,
      proxy_scheme: settings.proxyScheme,
      proxy_host: settings.proxyHost,
      proxy_port: settings.proxyPort,
      proxy_bypass: settings.proxyBypass,
      terminal_shell: settings.terminalShell,
      python_path: settings.pythonPath,
    }),
  });
}

// FABLEADV-15: config-driven provider registry (builtin + user providers.json).
function registryEntryFromRecord(row: Record<string, unknown>): ProviderRegistryEntry {
  const base = providerProfileFromRecord(row);
  const source = stringValue(row.source);
  return {
    ...base,
    source: source === 'user' || source === 'project' ? source : 'builtin',
    apiKeyEnv: stringValue(row.api_key_env),
    deletable: Boolean(row.deletable),
  };
}

export async function getProviderRegistry(): Promise<ProviderRegistryEntry[]> {
  const data = await requestJson<Record<string, unknown>>('/providers/registry');
  return Array.isArray(data.providers) ? data.providers.map(item => registryEntryFromRecord(recordValue(item))) : [];
}

export async function saveProviderRegistry(
  provider: ProviderRegistryInput,
): Promise<{ ok: boolean; providerId?: string; error?: string; count?: number }> {
  const data = await requestJson<Record<string, unknown>>('/providers/registry', {
    method: 'POST',
    body: JSON.stringify(provider),
  });
  return {
    ok: Boolean(data.ok),
    providerId: stringValue(data.provider_id) || undefined,
    error: stringValue(data.error) || undefined,
    count: typeof data.count === 'number' ? data.count : undefined,
  };
}

export async function deleteProviderRegistry(
  providerId: string,
): Promise<{ ok: boolean; error?: string; count?: number }> {
  const data = await requestJson<Record<string, unknown>>(`/providers/registry/${encodeURIComponent(providerId)}`, {
    method: 'DELETE',
  });
  return {
    ok: Boolean(data.ok),
    error: stringValue(data.error) || undefined,
    count: typeof data.count === 'number' ? data.count : undefined,
  };
}

export async function probeProviderRegistry(
  providerId: string,
  payload: { baseUrl?: string; model?: string; apiKey?: string } = {},
): Promise<ProviderRegistryProbeResult> {
  const data = await requestJson<Record<string, unknown>>(`/providers/registry/${encodeURIComponent(providerId)}/probe`, {
    method: 'POST',
    body: JSON.stringify({
      base_url: payload.baseUrl,
      model: payload.model,
      api_key: payload.apiKey,
    }),
  });
  const modelsResult = recordValue(data.models_result);
  const conformance = recordValue(data.conformance);
  return {
    ok: Boolean(data.ok),
    providerId: stringValue(data.provider_id) || providerId,
    error: stringValue(data.error),
    models: stringArray(data.models),
    modelsResult: Object.keys(modelsResult).length ? providerModelCatalogFromRecord(modelsResult) : undefined,
    conformance: Object.keys(conformance).length ? providerConformanceFromRecord(conformance) : undefined,
    supportsVision: Boolean(data.supports_vision),
    parallelToolCalls: Boolean(data.parallel_tool_calls),
    requiresReasoningPassback: Boolean(data.requires_reasoning_passback),
    visionDetection: stringValue(data.vision_detection),
  };
}

export async function getProviderStatus(): Promise<ProviderStatusPayload> {
  const data = await requestJson<Record<string, unknown>>('/providers');
  const providers = Array.isArray(data.providers) ? data.providers.map(item => providerProfileFromRecord(recordValue(item))) : [];
  const activeRow = recordValue(data.active);
  const settings = recordValue(data.settings);
  return {
    providers,
    active: Object.keys(activeRow).length ? providerValidationFromRecord(activeRow) : null,
    settings: {
      backend: stringValue(settings.backend),
      providerId: stringValue(settings.provider_id),
      baseUrl: stringValue(settings.base_url),
      model: stringValue(settings.model),
      hasApiKey: Boolean(settings.has_api_key),
    },
  };
}

export async function verifyProviderConfig(payload: {
  backend: string;
  baseUrl: string;
  model: string;
  apiKey?: string;
  deepProbe?: boolean;
}): Promise<ProviderValidation> {
  const data = await requestJson<Record<string, unknown>>('/providers/verify', {
    method: 'POST',
    body: JSON.stringify({
      backend: payload.backend,
      base_url: payload.baseUrl,
      model: payload.model,
      api_key: payload.apiKey,
      deep_probe: payload.deepProbe,
    }),
  });
  return providerValidationFromRecord(data);
}

export async function getProviderModels(payload: {
  backend: string;
  baseUrl: string;
  model: string;
  apiKey?: string;
}): Promise<ProviderModelCatalog> {
  const data = await requestJson<Record<string, unknown>>('/providers/models', {
    method: 'POST',
    body: JSON.stringify({
      backend: payload.backend,
      base_url: payload.baseUrl,
      model: payload.model,
      api_key: payload.apiKey,
    }),
  });
  return providerModelCatalogFromRecord(data);
}

export async function getProviderUsage(payload: {
  backend: string;
  baseUrl: string;
  model: string;
  apiKey?: string;
}): Promise<ProviderUsagePayload> {
  const data = await requestJson<Record<string, unknown>>('/providers/usage', {
    method: 'POST',
    body: JSON.stringify({
      backend: payload.backend,
      base_url: payload.baseUrl,
      model: payload.model,
      api_key: payload.apiKey,
    }),
  });
  return providerUsageFromRecord(data);
}

export async function getModelCapabilities(settings?: Pick<RuntimeSettings, 'backend' | 'providerId' | 'baseUrl' | 'model'>): Promise<ModelCapabilities> {
  const query = new URLSearchParams();
  if (settings) {
    query.set('backend', settings.providerId || settings.backend);
    query.set('base_url', settings.baseUrl);
    query.set('model', settings.model);
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : '';
  const data = await requestJson<Record<string, unknown>>(`/api/model/capabilities${suffix}`);
  return modelCapabilitiesFromRecord(data);
}

export async function getMemory(): Promise<MemoryPayload> {
  const data = await requestJson<Record<string, unknown>>('/memory');
  return {
    globalPath: stringValue(data.global_path),
    projectPath: stringValue(data.project_path),
    globalContent: stringValue(data.global_content),
    projectContent: stringValue(data.project_content),
    autoMemory: Boolean(data.auto_memory),
    autoSkills: Boolean(data.auto_skills),
  };
}

export async function saveMemory(payload: Partial<MemoryPayload>): Promise<void> {
  await requestJson('/memory', {
    method: 'POST',
    body: JSON.stringify({
      global_content: payload.globalContent,
      project_content: payload.projectContent,
    }),
  });
}

export async function getSkills(): Promise<SkillSummary[]> {
  const data = await requestJson<Record<string, unknown>>('/skills');
  const skills = Array.isArray(data.skills) ? data.skills : [];
  return skills.map(item => skillSummaryFromRecord(recordValue(item)));
}

export async function getSkill(skillId: string): Promise<SkillDetail> {
  const data = await requestJson<Record<string, unknown>>(`/skills/${encodeURIComponent(skillId)}`);
  return skillDetailFromRecord(data);
}

export async function saveSkill(skillId: string, content: string): Promise<SkillDetail> {
  const data = await requestJson<Record<string, unknown>>(`/skills/${encodeURIComponent(skillId)}`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
  return skillDetailFromResponse(data);
}

export async function setSkillEnabled(skillId: string, enabled: boolean): Promise<SkillDetail> {
  const data = await requestJson<Record<string, unknown>>(`/skills/${encodeURIComponent(skillId)}/toggle`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });
  return skillDetailFromResponse(data);
}

export async function importSkill(path: string): Promise<SkillDetail> {
  const data = await requestJson<Record<string, unknown>>('/skills/import', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
  return skillDetailFromResponse(data);
}

export async function openSkillFolder(skillId: string): Promise<{ ok: boolean; path: string }> {
  const data = await requestJson<Record<string, unknown>>(`/skills/${encodeURIComponent(skillId)}/open-folder`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return { ok: Boolean(data.ok), path: stringValue(data.path) };
}

export async function deleteSkill(skillId: string): Promise<void> {
  await requestJson(`/skills/${encodeURIComponent(skillId)}`, { method: 'DELETE' });
}

function skillSummaryFromRecord(row: Record<string, unknown>): SkillSummary {
  return {
    id: stringValue(row.id),
    name: stringValue(row.name),
    skillName: stringValue(row.skill_name) || stringValue(row.skillName) || stringValue(row.id),
    path: stringValue(row.path),
    source: stringValue(row.source) || 'global',
    enabled: Boolean(row.enabled),
    userInvocable: row.user_invocable === undefined && row.userInvocable === undefined ? true : Boolean(row.user_invocable ?? row.userInvocable),
    disableModelInvocation: Boolean(row.disable_model_invocation ?? row.disableModelInvocation),
    description: stringValue(row.description),
    whenToUse: stringValue(row.when_to_use) || stringValue(row.whenToUse),
    paths: stringArray(row.paths),
    allowedTools: stringArray(row.allowed_tools ?? row.allowedTools),
    disallowedTools: stringArray(row.disallowed_tools ?? row.disallowedTools),
    preview: stringValue(row.preview),
  };
}

function skillDetailFromRecord(row: Record<string, unknown>): SkillDetail {
  return {
    ...skillSummaryFromRecord(row),
    content: stringValue(row.content),
  };
}

function skillDetailFromResponse(row: Record<string, unknown>): SkillDetail {
  const skill = recordValue(row.skill);
  return skillDetailFromRecord(Object.keys(skill).length > 0 ? skill : row);
}

export async function getMcpStatus(): Promise<McpStatusPayload> {
  const data = await requestJson<Record<string, unknown>>('/mcp/status');
  const serversRecord = recordValue(data.servers);
  const servers: McpServerStatus[] = Object.entries(serversRecord).map(([name, value]) => {
    const row = recordValue(value);
    const config = recordValue(row.config);
    const tools = Array.isArray(row.tools) ? row.tools : [];
    const resources = Array.isArray(row.resources) ? row.resources : [];
    return {
      name,
      connected: Boolean(row.connected),
      healthy: row.healthy === undefined ? Boolean(row.connected) : Boolean(row.healthy),
      transport: stringValue(row.transport),
      toolsCount: numberValue(row.tools_count ?? row.toolsCount),
      tools: tools.map(item => {
        const tool = recordValue(item);
        return {
          name: stringValue(tool.name),
          description: stringValue(tool.description),
        };
      }),
      resourcesCount: numberValue(row.resources_count ?? row.resourcesCount),
      resources: resources.map(item => {
        const resource = recordValue(item);
        return {
          uri: stringValue(resource.uri),
          name: stringValue(resource.name) || undefined,
          description: stringValue(resource.description) || undefined,
          mimeType: stringValue(resource.mimeType ?? resource.mime_type) || undefined,
        };
      }),
      lastError: stringValue(row.last_error ?? row.lastError),
      lastConnectedAt: numberValue(row.last_connected_at ?? row.lastConnectedAt),
      lastCheckedAt: numberValue(row.last_checked_at ?? row.lastCheckedAt),
      command: stringValue(config.command),
      args: stringArray(config.args),
      url: stringValue(config.url),
    };
  });
  const rawConfigSources = data.config_sources ?? data.configSources;
  const configSourcesRaw: unknown[] = Array.isArray(rawConfigSources) ? rawConfigSources : [];
  const configSources: McpConfigSource[] = configSourcesRaw.map(item => {
    const row = recordValue(item);
    return {
      path: stringValue(row.path),
      exists: Boolean(row.exists),
      label: stringValue(row.label),
    };
  });
  return {
    available: Boolean(data.available),
    enabled: data.enabled === undefined ? true : Boolean(data.enabled),
    servers,
    configSources,
  };
}

export async function reconnectMcpServer(serverName: string): Promise<{ success: boolean; error: string; toolsCount: number }> {
  const data = await requestJson<Record<string, unknown>>('/mcp/reconnect', {
    method: 'POST',
    body: JSON.stringify({ server: serverName }),
  });
  return {
    success: Boolean(data.success),
    error: stringValue(data.error),
    toolsCount: numberValue(data.tools_count ?? data.toolsCount),
  };
}

export async function reloadMcpServers(): Promise<{ ok: boolean; error: string; removed: number; registered: number }> {
  const data = await requestJson<Record<string, unknown>>('/mcp/reload', {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return {
    ok: Boolean(data.ok),
    error: stringValue(data.error),
    removed: numberValue(data.removed),
    registered: numberValue(data.registered),
  };
}

export async function disconnectMcpServer(serverName: string): Promise<{ success: boolean; error: string }> {
  const data = await requestJson<Record<string, unknown>>('/mcp/disconnect', {
    method: 'POST',
    body: JSON.stringify({ server: serverName }),
  });
  return {
    success: Boolean(data.success),
    error: stringValue(data.error),
  };
}

export async function getDeskStatus(): Promise<DeskStatusPayload> {
  try {
    const data = await requestJson<Record<string, unknown>>('/api/status');
    return {
      available: true,
      enabled: Boolean(data.enabled),
      paused: Boolean(data.paused),
      port: numberValue(data.port),
      execMode: stringValue(data.exec_mode ?? data.execMode) || 'auto',
      humanCore: stringValue(data.human_core ?? data.humanCore) || 'som',
      goal: stringValue(data.goal),
      goalStatus: stringValue(data.goal_status ?? data.goalStatus) || 'idle',
      goalRunning: Boolean(data.goal_running ?? data.goalRunning),
      visionStatus: stringValue(data.vision_status ?? data.visionStatus) || 'idle',
      visionRunning: Boolean(data.vision_running ?? data.visionRunning),
      visionGoal: stringValue(data.vision_goal ?? data.visionGoal),
      visionStep: numberValue(data.vision_step ?? data.visionStep),
      visionMaxSteps: numberValue(data.vision_max_steps ?? data.visionMaxSteps),
      error: '',
    };
  } catch (error) {
    return {
      available: false,
      enabled: false,
      paused: false,
      port: 0,
      execMode: '',
      humanCore: '',
      goal: '',
      goalStatus: 'unavailable',
      goalRunning: false,
      visionStatus: 'unavailable',
      visionRunning: false,
      visionGoal: '',
      visionStep: 0,
      visionMaxSteps: 0,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function setDeskEnabled(enabled: boolean): Promise<void> {
  await requestJson('/api/enabled', {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });
}

export async function pauseDeskAutomation(): Promise<void> {
  await requestJson('/api/pause', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function resumeDeskAutomation(): Promise<void> {
  await requestJson('/api/resume', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export async function getDeskGoalLog(limit = 20): Promise<DeskGoalLogEntry[]> {
  try {
    const data = await requestJson<Record<string, unknown>>(`/api/goal/log?n=${encodeURIComponent(String(limit))}`);
    const rows = Array.isArray(data.log) ? data.log : [];
    return rows.map(item => {
      const row = recordValue(item);
      return {
        ts: numberValue(row.ts ?? row.time ?? row.timestamp),
        action: stringValue(row.action),
        detail: stringValue(row.detail),
        status: stringValue(row.status),
      };
    });
  } catch {
    return [];
  }
}

export async function searchSessions(query: string): Promise<SearchResult[]> {
  const data = await requestJson<Record<string, unknown>>(`/search?q=${encodeURIComponent(query)}`);
  const results = Array.isArray(data.results) ? data.results : [];
  return results.map(item => {
    const row = recordValue(item);
    return {
      sessionId: stringValue(row.session_id),
      title: stringValue(row.title),
      snippet: stringValue(row.snippet),
      ts: numberValue(row.ts),
      score: numberValue(row.score),
      workspaceId: stringValue(row.workspace_id ?? row.workspaceId) || undefined,
      workspaceName: stringValue(row.workspace_name ?? row.workspaceName) || undefined,
    };
  });
}

function cronTask(row: Record<string, unknown>): CronTask {
  return {
    id: stringValue(row.id),
    name: stringValue(row.name),
    schedule: stringValue(row.schedule),
    prompt: stringValue(row.prompt),
    workspaceId: stringValue(row.workspace_id),
    enabled: Boolean(row.enabled),
    createdAt: numberValue(row.createdAt),
    lastRun: numberValue(row.lastRun),
    nextRun: numberValue(row.nextRun),
    lastSessionId: stringValue(row.lastSessionId),
    lastStatus: stringValue(row.lastStatus),
  };
}

export async function getCronTasks(): Promise<CronTask[]> {
  const data = await requestJson<Record<string, unknown>>('/cron');
  const tasks = Array.isArray(data.tasks) ? data.tasks : [];
  return tasks.map(item => cronTask(recordValue(item)));
}

export async function createCronTask(payload: {
  name: string;
  schedule: string;
  prompt: string;
  workspaceId?: string;
}): Promise<CronTask> {
  const data = await requestJson<Record<string, unknown>>('/cron', {
    method: 'POST',
    body: JSON.stringify({
      name: payload.name,
      schedule: payload.schedule,
      prompt: payload.prompt,
      workspace_id: payload.workspaceId,
    }),
  });
  return cronTask(data);
}

export async function updateCronTask(
  taskId: string,
  payload: {
    name: string;
    schedule: string;
    prompt: string;
    workspaceId?: string;
    enabled?: boolean;
  },
): Promise<CronTask> {
  const data = await requestJson<Record<string, unknown>>(`/cron/${encodeURIComponent(taskId)}`, {
    method: 'POST',
    body: JSON.stringify({
      name: payload.name,
      schedule: payload.schedule,
      prompt: payload.prompt,
      workspace_id: payload.workspaceId,
      enabled: payload.enabled,
    }),
  });
  return cronTask(data);
}

export async function deleteCronTask(taskId: string): Promise<void> {
  await requestJson(`/cron/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
}

export async function answerToolPermission(
  requestId: string,
  approved: boolean,
  options: { remember?: 'allow' | 'deny' | ''; tool?: string; args?: unknown; callId?: string } = {},
): Promise<void> {
  await requestJson('/permission', {
    method: 'POST',
    body: JSON.stringify({
      request_id: requestId,
      approved,
      remember: options.remember || '',
      tool: options.tool,
      args: options.args,
      call_id: options.callId,
    }),
  });
}

export async function getPermissions(): Promise<PermissionStatePayload> {
  const data = await requestJson<Record<string, unknown>>('/permissions');
  const rules = Array.isArray(data.rules) ? data.rules : [];
  const audit = Array.isArray(data.audit) ? data.audit : [];
  return {
    rules: rules.map(item => permissionRuleFromRecord(recordValue(item))),
    audit: audit.map(item => permissionAuditFromRecord(recordValue(item))),
    controlPlane: Object.keys(recordValue(data.control_plane ?? data.controlPlane)).length
      ? permissionControlPlaneFromRecord(recordValue(data.control_plane ?? data.controlPlane))
      : undefined,
    path: stringValue(data.path),
    legacyPath: stringValue(data.legacy_path ?? data.legacyPath),
    auditPath: stringValue(data.audit_path ?? data.auditPath),
  };
}

export async function createPermissionRule(payload: {
  tool: string;
  action: 'allow' | 'deny' | 'ask';
  argsMatch?: Record<string, string>;
  source?: string;
}): Promise<PermissionRule> {
  const data = await requestJson<Record<string, unknown>>('/permissions', {
    method: 'POST',
    body: JSON.stringify({
      tool: payload.tool,
      action: payload.action,
      args_match: payload.argsMatch || {},
      source: payload.source || 'settings',
    }),
  });
  return permissionRuleFromRecord(recordValue(data.rule));
}

export async function deletePermissionRule(ruleId: string): Promise<void> {
  await requestJson(`/permissions/${encodeURIComponent(ruleId)}`, { method: 'DELETE' });
}

export async function getComposerPermissionMode(): Promise<PermissionAccessMode> {
  const state = await getPermissions();
  const latestRule = state.rules
    .filter(isComposerAccessRule)
    .sort((left, right) => (right.updatedAt || right.createdAt) - (left.updatedAt || left.createdAt))[0];
  if (latestRule?.action === 'allow') return 'full';
  if (latestRule?.action === 'ask') return 'ask';
  return 'auto';
}

export async function setComposerPermissionMode(mode: PermissionAccessMode): Promise<PermissionAccessMode> {
  const state = await getPermissions();
  const composerRules = state.rules.filter(isComposerAccessRule);
  await Promise.all(composerRules.map(rule => deletePermissionRule(rule.id)));
  if (mode === 'ask') {
    await createPermissionRule({ tool: '*', action: 'ask', source: COMPOSER_PERMISSION_SOURCE });
    return 'ask';
  }
  if (mode === 'full') {
    await createPermissionRule({ tool: '*', action: 'allow', source: COMPOSER_PERMISSION_SOURCE });
    return 'full';
  }
  return 'auto';
}

export async function toggleCronTask(taskId: string): Promise<CronTask> {
  const data = await requestJson<Record<string, unknown>>(`/cron/${encodeURIComponent(taskId)}/toggle`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return cronTask(data);
}

export async function runCronTask(taskId: string): Promise<{ ok: boolean; sessionId: string; error: string }> {
  const data = await requestJson<Record<string, unknown>>(`/cron/${encodeURIComponent(taskId)}/run`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
  return {
    ok: Boolean(data.ok),
    sessionId: stringValue(data.session_id),
    error: stringValue(data.error),
  };
}

export async function getFirstRun(): Promise<FirstRunStatus> {
  const data = await requestJson<Record<string, unknown>>('/first-run');
  return {
    firstRun: Boolean(data.first_run),
    hasApiKey: Boolean(data.has_api_key),
    hasConfig: Boolean(data.has_config),
    configPath: stringValue(data.config_path) || null,
    legacyConfigPath: stringValue(data.legacy_config_path) || null,
  };
}

export async function verifyFirstRun(payload: {
  backend: string;
  baseUrl: string;
  model: string;
  apiKey: string;
}): Promise<ProviderValidation> {
  const data = await requestJson<Record<string, unknown>>('/first-run/verify', {
    method: 'POST',
    body: JSON.stringify({
      backend: payload.backend,
      base_url: payload.baseUrl,
      model: payload.model,
      api_key: payload.apiKey,
    }),
  });
  return providerValidationFromRecord(data);
}

export async function completeFirstRun(payload: {
  backend: string;
  baseUrl: string;
  model: string;
  apiKey: string;
}): Promise<void> {
  await requestJson('/first-run/complete', {
    method: 'POST',
    body: JSON.stringify({
      backend: payload.backend,
      base_url: payload.baseUrl,
      model: payload.model,
      api_key: payload.apiKey,
    }),
  });
}

export async function parseUpload(file: File): Promise<ParsedFile> {
  const path = window.metis?.getPathForFile(file) || file.name;
  const ext = file.name.includes('.') ? `.${file.name.split('.').pop() || ''}`.toLowerCase() : '';

  /* ── Images: handle entirely on frontend (multimodal models use data URL) ── */
  if (file.type.startsWith('image/') || /^\.(png|jpe?g|gif|webp|bmp|svg|ico|tiff?)$/i.test(ext)) {
    const dataUrl = await readFileDataUrl(file);
    return {
      path,
      name: file.name,
      extension: ext,
      size: file.size,
      kind: 'image',
      mime: file.type || 'image/png',
      text: `[Image: ${file.name}, ${formatBytes(file.size)}]`,
      dataUrl,
      status: 'ready',
      truncated: false,
    };
  }

  /* ── Documents: send to backend for text extraction ── */
  const form = new FormData();
  form.append('file', file);
  const data = await requestJson<Record<string, unknown>>('/upload/parse', {
    method: 'POST',
    body: form,
  });
  return {
    path,
    name: stringValue(data.filename) || file.name,
    extension: stringValue(data.type),
    size: file.size,
    kind: 'document',
    mime: file.type,
    text: stringValue(data.text),
    status: 'ready',
    truncated: Boolean(data.truncated),
  };
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function readFileDataUrl(file: File): Promise<string> {
  return new Promise(resolve => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.onerror = () => resolve('');
    reader.readAsDataURL(file);
  });
}

export async function exportSession(sessionId: string, format: 'markdown' | 'json'): Promise<string> {
  const base = await apiBase();
  const response = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/export?format=${format}`);
  const text = await response.text();
  if (!response.ok) throw new Error(text || `HTTP ${response.status}`);
  return text;
}

export async function getWorkspaceTree(): Promise<WorkspaceTreeNode[]> {
  const data = await requestJson<Record<string, unknown>>('/workspace/tree?depth=3');
  const raw = data.tree ?? data.children ?? [];
  return Array.isArray(raw) ? (raw as WorkspaceTreeNode[]) : [];
}

export async function getWorkspaceFile(path: string): Promise<WorkspaceFile> {
  const data = await requestJson<Record<string, unknown>>(`/workspace/file?path=${encodeURIComponent(path)}`);
  return {
    type: (stringValue(data.type) || 'binary') as WorkspaceFile['type'],
    name: stringValue(data.name),
    path: stringValue(data.path),
    size: numberValue(data.size),
    content: stringValue(data.content),
    language: stringValue(data.language),
    previewUrl: stringValue(data.preview_url),
    truncated: Boolean(data.truncated),
  };
}

export async function revertFileChanges(summary: FileChangeSummary): Promise<FileChangeRevertResult> {
  const data = await requestJson<Record<string, unknown>>('/workspace/file-changes/revert', {
    method: 'POST',
    body: JSON.stringify({
      summary_id: summary.id,
      changes: summary.changes.map(change => ({
        id: change.id,
        path: change.path,
        kind: change.kind,
        tool_name: change.toolName,
        before: change.before,
        after: change.after,
      })),
    }),
  });
  const items = Array.isArray(data.items) ? data.items : [];
  return {
    ok: Boolean(data.ok),
    summaryId: stringValue(data.summary_id ?? data.summaryId),
    revertedCount: numberValue(data.reverted_count ?? data.revertedCount),
    conflictCount: numberValue(data.conflict_count ?? data.conflictCount),
    blockedCount: numberValue(data.blocked_count ?? data.blockedCount),
    auditPath: stringValue(data.audit_path ?? data.auditPath),
    items: items.map(item => {
      const row = recordValue(item);
      return {
        id: stringValue(row.id),
        path: stringValue(row.path),
        kind: stringValue(row.kind),
        toolName: stringValue(row.tool_name ?? row.toolName),
        status: stringValue(row.status),
        message: stringValue(row.message),
        beforeHash: stringValue(row.before_hash ?? row.beforeHash),
        afterHash: stringValue(row.after_hash ?? row.afterHash),
        currentHash: stringValue(row.current_hash ?? row.currentHash),
      };
    }),
  };
}

export async function getAgentEventContract(): Promise<AgentEventContract> {
  const data = await requestJson<Record<string, unknown>>('/contract/agent-events');
  const legacyCompatFields = recordValue(data.legacy_compat_fields ?? data.legacyCompatFields);
  return {
    schema: stringValue(data.schema),
    version: numberValue(data.version),
    transport: stringValue(data.transport),
    eventKinds: stringArray(data.event_kinds ?? data.eventKinds),
    envelopeRequired: stringArray(data.envelope_required ?? data.envelopeRequired),
    legacyCompatFields: Object.fromEntries(
      Object.entries(legacyCompatFields).map(([key, value]) => [key, stringArray(value)]),
    ),
  };
}

export async function chatStream(
  body: unknown,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const base = await apiBase();
  const response = await fetch(`${base}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const packets = buffer.split('\n\n');
      buffer = packets.pop() ?? '';

      for (const packet of packets) {
        for (const line of packet.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6);
          if (payload === '[DONE]') return;
          try {
            onEvent(JSON.parse(payload) as ChatStreamEvent);
          } catch {
            onEvent({ type: 'error', message: payload });
          }
        }
      }
    }
  } finally {
    void reader.cancel().catch(() => {});
  }
}

export async function sideChatStream(
  body: unknown,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const base = await apiBase();
  const response = await fetch(`${base}/side-chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const packets = buffer.split('\n\n');
      buffer = packets.pop() ?? '';

      for (const packet of packets) {
        for (const line of packet.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6);
          if (payload === '[DONE]') return;
          try {
            onEvent(JSON.parse(payload) as ChatStreamEvent);
          } catch {
            onEvent({ type: 'error', message: payload });
          }
        }
      }
    }
  } finally {
    void reader.cancel().catch(() => {});
  }
}

export async function startChatRun(body: unknown): Promise<ChatRunPayload> {
  const data = await requestJson<Record<string, unknown>>('/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return chatRunFromRecord(data);
}

export async function getChatRun(runId: string): Promise<ChatRunPayload> {
  const data = await requestJson<Record<string, unknown>>(`/runs/${encodeURIComponent(runId)}`);
  return chatRunFromRecord(data);
}

export async function getChatRuns(sessionId = ''): Promise<ChatRunsPayload> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const data = await requestJson<Record<string, unknown>>(`/runs${query}`);
  const runs = Array.isArray(data.runs) ? data.runs.map(item => chatRunFromRecord(recordValue(item))) : [];
  return { runs };
}

export async function getActiveSessionRun(sessionId: string): Promise<ActiveChatRunPayload> {
  if (!sessionId) return { ok: false, run: null };
  const data = await requestJson<Record<string, unknown>>(`/sessions/${encodeURIComponent(sessionId)}/runs/active`);
  const run = recordValue(data.run);
  return {
    ok: Boolean(data.ok),
    run: Object.keys(run).length ? chatRunFromRecord(run) : null,
  };
}

export async function cancelChatRun(runId: string): Promise<ChatRunPayload> {
  const data = await requestJson<Record<string, unknown>>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' });
  return chatRunFromRecord(data);
}

class StreamHttpError extends Error {
  status: number;

  constructor(status: number) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

function abortError(error: unknown, signal?: AbortSignal): boolean {
  return Boolean(signal?.aborted || (error instanceof DOMException && error.name === 'AbortError'));
}

function retryableStreamError(error: unknown): boolean {
  if (error instanceof StreamHttpError) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  return true;
}

function reconnectDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** Math.max(0, attempt - 1), 30000);
}

function waitForReconnect(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, delayMs);
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', abort, { once: true });
  });
}

function reconnectStatus(attempt: number, delayMs: number): ChatStreamEvent {
  return {
    type: 'runtime_status',
    phase: 'sse_reconnecting',
    message: `事件流断开，${Math.round(delayMs / 1000)} 秒后重连 (${attempt})`,
    recoverable: true,
  };
}

async function runEventStreamOnce(
  runId: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal: AbortSignal | undefined,
  afterSeq: number,
  onSeq: (seq: number) => void,
): Promise<void> {
  const base = await apiBase();
  const response = await fetch(`${base}/runs/${encodeURIComponent(runId)}/events?after=${Math.max(0, Math.floor(afterSeq))}`, {
    method: 'GET',
    signal,
  });
  if (!response.ok) {
    throw new StreamHttpError(response.status);
  }
  if (!response.body) {
    throw new Error('SSE response body is empty.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const packets = buffer.split('\n\n');
      buffer = packets.pop() ?? '';

      for (const packet of packets) {
        for (const line of packet.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6);
          if (payload === '[DONE]') return;
          try {
            const parsed = JSON.parse(payload) as ChatStreamEvent;
            if (typeof parsed.seq === 'number' && Number.isFinite(parsed.seq)) {
              onSeq(parsed.seq);
            }
            onEvent(parsed);
          } catch {
            onEvent({ type: 'error', message: payload });
          }
        }
      }
    }
  } finally {
    void reader.cancel().catch(() => {});
  }
}

export async function runEventStream(
  runId: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
  afterSeq = 0,
): Promise<void> {
  let nextAfterSeq = Math.max(0, Math.floor(afterSeq));
  let attempt = 0;

  while (true) {
    try {
      await runEventStreamOnce(runId, onEvent, signal, nextAfterSeq, seq => {
        nextAfterSeq = Math.max(nextAfterSeq, seq);
      });
      return;
    } catch (error) {
      if (abortError(error, signal) || !retryableStreamError(error)) {
        throw error;
      }
      attempt += 1;
      const delayMs = reconnectDelayMs(attempt);
      onEvent(reconnectStatus(attempt, delayMs));
      await waitForReconnect(delayMs, signal);
    }
  }
}

function chatRunFromRecord(data: Record<string, unknown>): ChatRunPayload {
  return {
    ok: data.ok === undefined ? undefined : Boolean(data.ok),
    runId: stringValue(data.run_id ?? data.runId ?? data.id),
    id: stringValue(data.id ?? data.run_id ?? data.runId),
    sessionId: stringValue(data.session_id ?? data.sessionId),
    assistantId: stringValue(data.assistant_id ?? data.assistantId),
    status: stringValue(data.status),
    phase: stringValue(data.phase),
    cancelRequested: Boolean(data.cancel_requested ?? data.cancelRequested),
    createdAt: numberValue(data.created_at ?? data.createdAt),
    updatedAt: numberValue(data.updated_at ?? data.updatedAt),
    startedAt: numberValue(data.started_at ?? data.startedAt),
    finishedAt: numberValue(data.finished_at ?? data.finishedAt),
    eventCount: numberValue(data.event_count ?? data.eventCount),
    lastSeq: numberValue(data.last_seq ?? data.lastSeq),
    error: stringValue(data.error),
  };
}
