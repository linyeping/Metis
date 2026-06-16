export type Language = 'en' | 'zh';

export type FontFamily = 'official-sans' | 'system' | 'microsoft-yahei' | 'inter';

export type ProxyMode = 'system' | 'custom' | 'off';

export type TerminalShell = 'powershell' | 'cmd' | 'bash' | 'sh' | 'shell';

export type ThemeName =
  | 'templar-silver'
  | 'paladin-ivory'
  | 'crusader-parchment'
  | 'cathedral-obsidian'
  | 'midnight-forge'
  | 'void-chapel'
  | 'rose-gold'
  | 'rose-gold-dark'
  | 'gold-slate'
  | 'gold-plum'
  | 'gold-azure'
  | 'gold-graphite'
  | 'gold-jade'
  | 'gold-clay'
  | 'gold-wisteria'
  | 'gold-pine';

export type SectionId = 'chat' | 'skills' | 'mcp' | 'computer' | 'cron';

export type SettingsSection = 'appearance' | 'conversation' | 'model' | 'usage' | 'network' | 'terminal' | 'tools' | 'connectors' | 'desktop' | 'about';

export type BootPhase = 'idle' | 'detecting' | 'preflight' | 'starting' | 'log' | 'ready' | 'error' | 'exit' | 'restarting';

export interface BootEvent {
  phase: BootPhase;
  timestamp?: string;
  title?: string;
  detail?: string;
  line?: string;
  port?: number;
  attempt?: number;
  limit?: number;
  logPath?: string;
  logTail?: string;
}

export interface BootState {
  status: 'idle' | 'starting' | 'ready' | 'error';
  port: number | null;
  error: {
    title: string;
    detail: string;
    logTail?: string;
  } | null;
  reconnect: { attempt: number; limit: number } | null;
  events: BootEvent[];
  logPath: string;
}

export interface StoragePayload {
  dataRoot: string;
  metisHome: string;
  electronUserData: string;
  source: string;
  configPath: string;
  portable: boolean;
  legacyMetisHome: string;
}

export interface DiagnosticsPayload {
  generatedAt: string;
  app: {
    name: string;
    version: string;
    packaged: boolean;
    fakeBackend: boolean;
  };
  platform: {
    platform: string;
    arch: string;
    release: string;
    versions: Record<string, string>;
  };
  backend: {
    status: BootState['status'];
    port: number | null;
    logPath: string;
    logTail: string;
  };
  storage: StoragePayload;
  boot: {
    error: BootState['error'];
    events: BootEvent[];
  };
  terminal: {
    activeSessions: number;
    backend: 'pty' | 'shell';
  };
}

export interface DiagnosticsBundleResult {
  canceled: boolean;
  path?: string;
  diagnostics?: DiagnosticsPayload;
}

export type DevServerState = 'idle' | 'detected' | 'starting' | 'running' | 'error' | 'exited';

export interface DevServerDetectResult {
  ok: boolean;
  cwd: string;
  packagePath: string;
  packageManager: 'npm' | 'pnpm' | 'yarn';
  scriptName: string;
  command: string;
  scriptCommand?: string;
  stack: string;
  reason: string;
  scripts: string[];
}

export interface DevServerStatus {
  state: DevServerState;
  cwd: string;
  packagePath: string;
  packageManager: 'npm' | 'pnpm' | 'yarn';
  scriptName: string;
  command: string;
  stack: string;
  url: string;
  logs: string[];
  reason: string;
  startedAt: number;
  updatedAt: number;
  exitCode?: number | null;
  previewPort?: number;
}

export interface DevServerStartPayload {
  cwd?: string;
  port?: number;
}

export interface DevServerEventPayload {
  type: 'status' | 'log' | 'url' | 'exit' | 'error';
  status: DevServerStatus;
}

export interface PreviewAuditInput {
  url: string;
  title: string;
  loading: boolean;
  error: string;
  zoom: number;
  screenshotDataUrl?: string;
}

export interface PreviewAuditResult {
  ok: boolean;
  status: 'ok' | 'warning' | 'error';
  reason: string;
  url: string;
  title: string;
  savedPath: string;
  screenshotPath?: string;
  capturedAt: string;
  screenshotAvailable: boolean;
}

export interface BrowserActivityItem {
  at: string;
  url: string;
  title: string;
  event: string;
  action: string;
  ok: boolean;
  blocked: boolean;
  confirmed: boolean;
  target: string;
  point?: { x?: number; y?: number; element_id?: string } | null;
  risk?: { risk_level?: string; summary?: string; reasons?: string[] } | null;
  element_count?: number;
  text_length?: number;
  width?: number;
  height?: number;
  saved_path?: string;
  error?: string;
  summary: string;
  navigation_resolution?: Record<string, unknown> | null;
  diagnostics_counts?: Record<string, number> | null;
  page_health?: Record<string, unknown> | null;
  screenshot_health?: Record<string, unknown> | null;
}

export interface BrowserActivityPayload {
  ok: boolean;
  tabId?: string;
  url: string;
  title: string;
  loading: boolean;
  canGoBack?: boolean;
  canGoForward?: boolean;
  counts: {
    total: number;
    navigate: number;
    observe: number;
    action: number;
    screenshot: number;
    blocked: number;
    errors: number;
  };
  diagnostics_counts: Record<string, number>;
  items: BrowserActivityItem[];
}

export interface Workspace {
  id: string;
  name: string;
  path: string;
  createdAt: number;
  updatedAt: number;
}

export interface SessionMeta {
  id: string;
  title: string;
  workspaceId: string;
  messageCount: number;
  createdAt: number;
  updatedAt: number;
}

export type MessageRole = 'user' | 'assistant' | 'tool' | 'system';

export interface SessionToolRecord {
  call_id?: string;
  name?: string;
  arguments?: unknown;
  result?: unknown;
  status?: 'running' | 'success' | 'error' | 'waiting_approval';
}

export interface SessionMessage {
  id?: string;
  role: MessageRole;
  content: unknown;
  name?: string;
  // FABLEADV-16: transcript-only tool record for rebuilding tool cards on reload.
  metis_kind?: string;
  metis_tool?: SessionToolRecord;
}

export interface CompactState {
  summary: string;
  boundaryMessageId: string;
  boundaryIndex: number;
  compactedAt: number;
  compactCount: number;
}

export interface Session {
  id: string;
  title: string;
  workspaceId: string;
  mode: string;
  history: SessionMessage[];
  compactState: CompactState | null;
  createdAt: number;
  updatedAt: number;
}

export interface SessionsPayload {
  sessions: SessionMeta[];
  activeSessionId: string | null;
  activeWorkspaceId: string;
}

export interface WorkspacesPayload {
  workspaces: Workspace[];
  activeWorkspaceId: string;
}

export interface RuntimeSettings {
  backend: string;
  providerId: string;
  baseUrl: string;
  model: string;
  temperature: number;
  reasoningEffort: string;
  maxTokens: number;
  apiKey: string;
  hasApiKey: boolean;
  autoMemory: boolean;
  autoSkills: boolean;
  proxyMode: ProxyMode;
  proxyScheme: string;
  proxyHost: string;
  proxyPort: string;
  proxyBypass: string;
  terminalShell: TerminalShell;
  pythonPath: string;
  providerValidation?: ProviderValidation;
}

export interface ModelCapabilities {
  tier: number;
  tierLabel: string;
  family: string;
  model: string;
  detectionMethod: string;
  effectiveContext: number;
  supportsVision: boolean;
  visionProtocol: string;
  supportsToolCalling: boolean;
  supportsStructuredOutput: boolean;
  instructionAdherence: string;
  toolCount: number;
  totalToolCount: number;
}

export interface TerminalRunPayload {
  command: string;
  cwd?: string;
  shell?: TerminalShell;
}

export interface TerminalRunResult {
  ok: boolean;
  command: string;
  cwd: string;
  shell: TerminalShell;
  stdout: string;
  stderr: string;
  exitCode: number | null;
  timedOut: boolean;
  durationMs: number;
  error?: string;
}

export interface TerminalSessionPayload {
  id: string;
  cwd: string;
  shell: TerminalShell;
  backend: 'pty' | 'shell';
  startedAt: number;
}

export interface TerminalCreatePayload {
  cwd?: string;
  shell?: TerminalShell;
  cols?: number;
  rows?: number;
}

export interface TerminalEventPayload {
  id: string;
  type: 'data' | 'exit' | 'error' | 'ready';
  data?: string;
  code?: number | null;
  signal?: string | null;
  cwd?: string;
  shell?: TerminalShell;
  backend?: 'pty' | 'shell';
}

export type ChatRunStatus = 'queued' | 'running' | 'canceling' | 'done' | 'failed' | 'canceled' | string;

export interface ChatRunPayload {
  ok?: boolean;
  runId: string;
  id: string;
  sessionId: string;
  assistantId: string;
  status: ChatRunStatus;
  phase: string;
  cancelRequested: boolean;
  createdAt: number;
  updatedAt: number;
  startedAt: number;
  finishedAt: number;
  eventCount: number;
  lastSeq: number;
  error: string;
}

export interface ChatRunsPayload {
  runs: ChatRunPayload[];
}

export interface ActiveChatRunPayload {
  ok: boolean;
  run: ChatRunPayload | null;
}

export interface ProviderProfile {
  providerId: string;
  displayName: string;
  backendType: string;
  aliases: string[];
  baseUrl: string;
  chatCompletionsPath: string;
  defaultModel: string;
  fallbackModels: string[];
  apiKeyRequired: boolean;
  openaiCompatible: boolean;
  capabilities: {
    stream: boolean;
    tools: boolean;
    vision: boolean;
    parallelToolCalls: boolean;
    requiresReasoningPassback: boolean;
  };
  modelContextWindows: Record<string, number>;
  modelNotes: Record<string, string>;
}

// FABLEADV-15: config-driven provider registry entry (builtin + user providers).
export interface ProviderRegistryEntry extends ProviderProfile {
  source: 'builtin' | 'user' | 'project';
  apiKeyEnv: string;
  deletable: boolean;
}

// Input for creating/updating a user provider via providers.json.
export interface ProviderRegistryInput {
  id: string;
  display_name?: string;
  backend_type?: string;
  base_url?: string;
  api_key_env?: string;
  default_model?: string;
  models?: string[];
  supports_vision?: boolean;
  parallel_tool_calls?: boolean;
  requires_reasoning_passback?: boolean;
}

export interface ProviderRegistryProbeResult {
  ok: boolean;
  providerId: string;
  error: string;
  models: string[];
  modelsResult?: ProviderModelCatalog;
  conformance?: ProviderConformance;
  supportsVision: boolean;
  parallelToolCalls: boolean;
  requiresReasoningPassback: boolean;
  visionDetection: string;
}

export interface ProviderConformance {
  ok: boolean;
  providerId: string;
  baseUrl: string;
  model: string;
  path: string;
  requiresReasoningPassback: boolean | null;
  parallelToolCalls: boolean | null;
  reasoningMode: string;
  cacheFields: string;
  toolSchemaStrictness: string;
  multiRoundContinuation: string;
  error: string;
  notes: string[];
}

export interface SessionCheckpointFile {
  relativePath: string;
  existed: boolean;
  skipped: string;
}

export interface SessionCheckpoint {
  checkpointId: string;
  sessionId: string;
  anchorIndex: number;
  userMessageId: string;
  reason: string;
  createdAt: number;
  completedAt: number;
  status: string;
  fileCount: number;
  files: SessionCheckpointFile[];
}

export interface RewindResult {
  ok: boolean;
  error: string;
  mode: 'conversation' | 'files' | 'both';
  checkpointId: string;
  safetyCheckpointId: string;
  historyLength: number;
  restored: string[];
  skipped: Array<{ path?: string; relativePath?: string; reason?: string }>;
}

export interface ProviderValidation {
  ok: boolean;
  code: string;
  title: string;
  message: string;
  hint: string;
  recoverable: boolean;
  providerId: string;
  displayName: string;
  backend: string;
  baseUrl: string;
  chatUrl: string;
  model: string;
  apiKeyRequired: boolean;
  hasApiKey: boolean;
  warnings: string[];
  provider?: ProviderProfile;
  conformance?: ProviderConformance;
}

export interface ProviderStatusPayload {
  providers: ProviderProfile[];
  active: ProviderValidation | null;
  settings: {
    backend: string;
    providerId: string;
    baseUrl: string;
    model: string;
    hasApiKey: boolean;
  };
}

export interface ProviderModel {
  id: string;
  displayName: string;
  ownedBy: string;
  type: string;
  created: number;
  contextLimit: number;
  chatCapable: boolean;
}

export interface ProviderModelCatalog {
  ok: boolean;
  kind: 'models';
  status: 'ok' | 'unsupported' | 'error' | string;
  providerId: string;
  displayName: string;
  baseUrl: string;
  apiBaseUrl: string;
  model: string;
  modelsUrl: string;
  message: string;
  hint: string;
  models: ProviderModel[];
}

export interface ProviderUsageCounter {
  requests: number;
  totalTokens: number;
  cost: number;
}

export interface ProviderUsagePayload {
  ok: boolean;
  kind: 'usage';
  status: 'ok' | 'warning' | 'danger' | 'unsupported' | 'error' | string;
  providerId: string;
  displayName: string;
  baseUrl: string;
  apiBaseUrl: string;
  model: string;
  usageUrl: string;
  mode: string;
  isValid: boolean;
  planName: string;
  remaining: number;
  balance: number;
  unit: string;
  today: ProviderUsageCounter;
  total: ProviderUsageCounter;
  quota: {
    limit: number;
    used: number;
    remaining: number;
  };
  message: string;
  hint: string;
}

export interface CompactStatusPayload {
  running: boolean;
  ok: boolean;
  beforeCount: number;
  afterCount: number;
  beforeContextMessages?: number;
  afterContextMessages?: number;
  summaryPreview: string;
  updatedAt: number;
  error: string;
}

export interface CompactHandoffSnapshot {
  sessionId: string;
  createdAt: number;
  beforeCount: number;
  afterCount: number;
  summaryPreview: string;
  model: string;
}

export interface PermissionRule {
  id: string;
  tool: string;
  action: 'allow' | 'deny' | 'ask' | string;
  argsMatch: Record<string, string>;
  source: string;
  createdAt: number;
  updatedAt: number;
}

export interface PermissionAuditEntry {
  id: string;
  createdAt: number;
  workspaceId: string;
  sessionId: string;
  cwd: string;
  requestId: string;
  callId: string;
  tool: string;
  action: string;
  approved: boolean;
  remember: string;
  ruleId: string;
  source: string;
  arguments: unknown;
}

export interface PermissionStatePayload {
  rules: PermissionRule[];
  audit: PermissionAuditEntry[];
  path: string;
  legacyPath: string;
  auditPath: string;
}

export type PermissionAccessMode = 'ask' | 'auto' | 'full';

export interface FileChangeRevertItem {
  id: string;
  path: string;
  kind: string;
  toolName: string;
  status: 'reverted' | 'conflict' | 'blocked' | string;
  message: string;
  beforeHash: string;
  afterHash: string;
  currentHash: string;
}

export interface FileChangeRevertResult {
  ok: boolean;
  summaryId: string;
  revertedCount: number;
  conflictCount: number;
  blockedCount: number;
  auditPath: string;
  items: FileChangeRevertItem[];
}

export interface FirstRunStatus {
  firstRun: boolean;
  hasApiKey: boolean;
  hasConfig: boolean;
  configPath: string | null;
  legacyConfigPath: string | null;
}

export interface ParsedFile {
  path: string;
  name: string;
  extension: string;
  size: number;
  kind: 'document' | 'image';
  mime: string;
  text: string;
  dataUrl?: string;
  status?: 'parsing' | 'ready' | 'error';
  error?: string;
  truncated?: boolean;
}

export interface ChatAttachment {
  path: string;
  name: string;
  kind: string;
  mime: string;
  text?: string;
  dataUrl?: string;
}

export interface ChatToolEvent {
  id: string;
  callId: string;
  requestId?: string;
  toolName: string;
  args?: unknown;
  result?: unknown;
  status: 'waiting_approval' | 'running' | 'success' | 'error';
  startedAt?: number;
  finishedAt?: number;
  summary?: string;
  errorHint?: string;
}

export interface ChatSubagentEvent {
  taskId: string;
  name: string;
  status: 'running' | 'done' | 'error';
  progress: number;
  summary?: string;
  result?: unknown;
  startedAt?: number;
  updatedAt?: number;
  finishedAt?: number;
}

export interface ChatMemoryNotice {
  message: string;
  memoryCount: number;
  skillCount: number;
  memoryPath: string;
  skillPath: string;
  createdAt: number;
}

export interface ChatTodoItem {
  id?: string | number;
  content?: string;
  task?: string;
  title?: string;
  status?: string;
}

export interface ChatTodoNotice {
  todos: ChatTodoItem[];
  summary: string;
  activeCount: number;
  doneCount: number;
  createdAt: number;
}

export interface RuntimeStatus {
  phase: string;
  message: string;
  display: string;
  severity: 'info' | 'working' | 'warning' | 'error' | 'done';
  toolName: string;
  callId?: string;
  turn?: number;
  toolCalls?: number;
  startedAt?: number;
  updatedAt?: number;
  hint: string;
  recoverable: boolean;
}

export interface ChatRunRecoverySnapshot {
  sessionId: string;
  assistantId: string;
  startedAt: number;
  updatedAt: number;
  phase: string;
  display: string;
  severity: 'info' | 'working' | 'warning' | 'error' | 'done';
  toolCount: number;
  preview: string;
  canResume?: boolean;
  checkpoint?: string;
  lastUserPreview?: string;
  assistantPreview?: string;
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: number;
  pending?: boolean;
  error?: string;
  attachments?: ParsedFile[];
  tools?: ChatToolEvent[];
  subagents?: ChatSubagentEvent[];
  parts?: ChatMessagePart[];
}

export type ChatMessagePart =
  | { type: 'text'; text: string }
  | { type: 'tool'; toolId: string; callId: string };

export type AgentEventKind =
  | 'text_delta'
  | 'content_delta'
  | 'content'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'permission_request'
  | 'error'
  | 'compact'
  | 'runtime_status'
  | 'todo_update'
  | 'memory_nudge'
  | 'subagent_start'
  | 'subagent_progress'
  | 'subagent_done'
  | 'done';

export interface AgentEventEnvelope {
  schema?: 'metis.agent_event.v1' | string;
  kind?: AgentEventKind | string;
  type?: AgentEventKind | string;
  event_id?: string;
  timestamp?: number;
  payload?: Record<string, unknown>;
}

export interface AgentEventContract {
  schema: 'metis.agent_event.v1' | string;
  version: number;
  transport: 'sse' | string;
  eventKinds: string[];
  envelopeRequired: string[];
  legacyCompatFields: Record<string, string[]>;
}

export interface ChatTokenUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  promptCacheHitTokens?: number;
  promptCacheMissTokens?: number;
}

export interface ContextLedger {
  systemTokens: number;
  schemaTokens: number;
  historyTokens: number;
  estimatedTotalTokens: number;
  contextLimit: number;
  contextRatio: number;
  cacheHitTokens: number;
  cacheMissTokens: number;
  cacheHitRate: number;
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  messageCount?: number;
  toolCount?: number;
  systemBreakdown?: {
    systemPrompt: number;
    skills: number;
    memory: number;
  };
  schemaBreakdown?: {
    mcp: number;
    builtin: number;
  };
}

export interface ChatStreamEvent {
  schema?: 'metis.agent_event.v1' | string;
  kind?: AgentEventKind | string;
  type: AgentEventKind | string;
  event_id?: string;
  timestamp?: number;
  payload?: Record<string, unknown>;
  text?: string;
  tool?: string;
  toolName?: string;
  args?: unknown;
  arguments?: unknown;
  result?: unknown;
  call_id?: string;
  callId?: string;
  request_id?: string;
  code?: string;
  title?: string;
  message?: string;
  hint?: string;
  recoverable?: boolean;
  phase?: string;
  turn?: number;
  tool_calls?: number;
  memory_count?: number;
  memoryCount?: number;
  todos?: ChatTodoItem[];
  summary?: string;
  skill_count?: number;
  skillCount?: number;
  memory_path?: string;
  memoryPath?: string;
  skill_path?: string;
  skillPath?: string;
  task_id?: string;
  taskId?: string;
  run_id?: string;
  runId?: string;
  session_id?: string;
  sessionId?: string;
  assistant_id?: string;
  assistantId?: string;
  seq?: number;
  name?: string;
  progress?: number;
  status?: string;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    prompt_cache_hit_tokens?: number;
    prompt_cache_miss_tokens?: number;
    promptTokens?: number;
    completionTokens?: number;
    totalTokens?: number;
    promptCacheHitTokens?: number;
    promptCacheMissTokens?: number;
  };
  context_ledger?: Record<string, unknown>;
  contextLedger?: Record<string, unknown>;
}

export interface ConnectorServiceStatus {
  service: 'github' | 'gmail' | string;
  displayName: string;
  scopes: string[];
  tokenEnv: string;
  connected: boolean;
  encryptionAvailable: boolean;
}

export interface ConnectorStatusPayload {
  ok: boolean;
  encryptionAvailable: boolean;
  services: ConnectorServiceStatus[];
}

export interface ConnectorAuthorizeResult {
  ok: boolean;
  service?: string;
  method?: string;
  code?: string;
  error?: string;
  userCode?: string;
  verificationUri?: string;
  testModeNote?: string;
}

export interface MemoryPayload {
  globalPath: string;
  projectPath: string;
  globalContent: string;
  projectContent: string;
  autoMemory: boolean;
  autoSkills: boolean;
}

export interface SkillSummary {
  id: string;
  name: string;
  skillName: string;
  path: string;
  source: 'builtin' | 'global' | 'project' | string;
  enabled: boolean;
  userInvocable: boolean;
  disableModelInvocation: boolean;
  description: string;
  whenToUse: string;
  paths: string[];
  allowedTools: string[];
  disallowedTools: string[];
  preview: string;
}

export interface SkillDetail extends SkillSummary {
  content: string;
}

export interface McpToolSummary {
  name: string;
  description: string;
}

export interface McpResourceSummary {
  uri: string;
  name?: string;
  description?: string;
  mimeType?: string;
}

export interface McpServerStatus {
  name: string;
  connected: boolean;
  healthy: boolean;
  transport: string;
  toolsCount: number;
  tools: McpToolSummary[];
  resourcesCount: number;
  resources: McpResourceSummary[];
  lastError: string;
  lastConnectedAt: number;
  lastCheckedAt: number;
  command: string;
  args: string[];
  url: string;
}

export interface McpConfigSource {
  path: string;
  exists: boolean;
  label: string;
}

export interface McpStatusPayload {
  available: boolean;
  enabled: boolean;
  servers: McpServerStatus[];
  configSources: McpConfigSource[];
}

export interface DeskStatusPayload {
  available: boolean;
  enabled: boolean;
  paused: boolean;
  port: number;
  execMode: string;
  humanCore: string;
  goal: string;
  goalStatus: string;
  goalRunning: boolean;
  visionStatus: string;
  visionRunning: boolean;
  visionGoal: string;
  visionStep: number;
  visionMaxSteps: number;
  error: string;
}

export interface DeskGoalLogEntry {
  ts: number;
  action: string;
  detail: string;
  status: string;
}

export interface SearchResult {
  sessionId: string;
  title: string;
  snippet: string;
  ts: number;
  score: number;
  workspaceId?: string;
  workspaceName?: string;
}

export interface CronTask {
  id: string;
  name: string;
  schedule: string;
  prompt: string;
  workspaceId: string;
  enabled: boolean;
  createdAt: number;
  lastRun: number;
  nextRun: number;
  lastSessionId: string;
  lastStatus: string;
}

export interface WorkspaceTreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: WorkspaceTreeNode[];
  size?: number;
  modified?: number;
}

export interface WorkspaceFile {
  type: 'text' | 'markdown' | 'image' | 'binary';
  name: string;
  path: string;
  size: number;
  content?: string;
  language?: string;
  previewUrl?: string;
  truncated?: boolean;
}
