import type {
  BootEvent,
  BootState,
  BrowserActivityPayload,
  ConnectorAuthorizeResult,
  ConnectorStatusPayload,
  DevServerDetectResult,
  DevServerEventPayload,
  DevServerStartPayload,
  DevServerStatus,
  DiagnosticsBundleResult,
  DiagnosticsPayload,
  PreviewAuditInput,
  StoragePayload,
  PreviewAuditResult,
  TerminalCreatePayload,
  TerminalEventPayload,
  TerminalRunPayload,
  TerminalRunResult,
  TerminalSessionPayload,
  WindowCloseBehavior,
} from './lib/types';

export {};

declare global {
  type PreviewRuntimeStateName = 'hidden' | 'mounting' | 'loading' | 'ready' | 'occluded' | 'error';

  interface PreviewBoundsPayload {
    x: number;
    y: number;
    width: number;
    height: number;
  }

  interface PreviewStatePayload {
    ok?: boolean;
    schema: 'metis.preview_state.v1';
    version: 1;
    state: PreviewRuntimeStateName;
    tab_id?: string;
    tabId?: string;
    url?: string;
    title?: string;
    bounds?: PreviewBoundsPayload | null;
    visible?: boolean;
    loading?: boolean;
    occluded?: boolean;
    error?: string;
    last_command_id?: string;
    lastCommandId?: string;
    activity_seq?: number;
    activitySeq?: number;
    updated_at?: string;
    updatedAt?: string;
    canGoBack?: boolean;
    canGoForward?: boolean;
  }

  interface PreviewLayoutIntentPayload {
    visible: boolean;
    tabId?: string;
    tab_id?: string;
    bounds?: Partial<PreviewBoundsPayload>;
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    reason?: string;
  }

  interface PreviewCommandResultPayload {
    ok: boolean;
    bounds?: PreviewBoundsPayload;
    hidden?: boolean;
    skipped?: boolean;
    occluded?: boolean;
    preview_state?: PreviewStatePayload;
    error?: string;
  }

  interface Window {
    metis: {
      backendPort: () => Promise<number | null>;
      bootState: () => Promise<BootState>;
      retryBackend: () => Promise<{ ok: boolean }>;
      openLog: () => Promise<{ ok: boolean; path: string; error?: string }>;
      appInfo: () => Promise<{ name: string; version: string; packaged: boolean; updateUrl: string; githubHome?: string; fakeBackend?: boolean; storage?: StoragePayload }>;
      diagnostics: () => Promise<DiagnosticsPayload>;
      setNativeTheme: (mode: 'light' | 'dark' | 'system') => Promise<{ ok: boolean; themeSource?: string; shouldUseDarkColors?: boolean }>;
      saveDiagnosticsBundle: () => Promise<DiagnosticsBundleResult>;
      checkUpdates: () => Promise<{ ok: boolean; status: string; message: string; url?: string }>;
      installUpdate: () => Promise<{ ok: boolean; message?: string }>;
      devServerDetect: (payload?: DevServerStartPayload) => Promise<DevServerDetectResult>;
      devServerStart: (payload?: DevServerStartPayload) => Promise<DevServerStatus>;
      devServerStop: (payload?: DevServerStartPayload) => Promise<DevServerStatus>;
      devServerStatus: (payload?: DevServerStartPayload) => Promise<DevServerStatus>;
      savePreviewEvidence: (payload: PreviewAuditInput) => Promise<PreviewAuditResult>;
      previewSetLayoutIntent: (payload: PreviewLayoutIntentPayload) => Promise<PreviewCommandResultPayload>;
      previewSetBounds: (payload: PreviewLayoutIntentPayload) => Promise<PreviewCommandResultPayload>;
      previewSetOccluded: (value: boolean) => Promise<{ ok: boolean; occluded?: boolean; preview_state?: PreviewStatePayload }>;
      previewLoad: (payload: { url: string; tabId?: string }) => Promise<{ ok: boolean; error?: string; preview_state?: PreviewStatePayload } & Partial<PreviewStatePayload>>;
      previewCommand: (command: 'back' | 'forward' | 'reload' | 'stop') => Promise<{ ok: boolean; preview_state?: PreviewStatePayload }>;
      previewSetZoom: (zoom: number) => Promise<{ ok: boolean; zoom?: number }>;
      previewCapture: () => Promise<{ ok: boolean; dataUrl: string; width?: number; height?: number; url?: string; title?: string; error?: string }>;
      previewObserve: (payload?: { maxElements?: number; includeText?: boolean }) => Promise<Record<string, unknown>>;
      previewAction: (payload: {
        action: 'click' | 'double_click' | 'type' | 'key' | 'scroll' | 'wait';
        elementId?: string;
        x?: number;
        y?: number;
        text?: string;
        key?: string;
        scrollY?: number;
        waitMs?: number;
      }) => Promise<Record<string, unknown>>;
      previewActivity: (payload?: { limit?: number }) => Promise<BrowserActivityPayload>;
      terminalRun: (payload: TerminalRunPayload) => Promise<TerminalRunResult>;
      terminalCreate: (payload: TerminalCreatePayload) => Promise<TerminalSessionPayload>;
      terminalInput: (sessionId: string, data: string) => Promise<{ ok: boolean }>;
      terminalResize: (sessionId: string, cols: number, rows: number) => Promise<{ ok: boolean }>;
      terminalKill: (sessionId: string) => Promise<{ ok: boolean }>;
      reportSmokeResult: (payload: unknown) => Promise<{ ok: boolean }>;
      reportPerfResult: (payload: unknown) => Promise<{ ok: boolean }>;
      safeStorageMigrate: () => Promise<{ ok: boolean }>;
      safeStorageAvailable: () => Promise<boolean>;
      safeStorageEncrypt: (plaintext: string) => Promise<string | null>;
      safeStorageDecrypt: (encrypted: string) => Promise<string | null>;
      connectorAuthorize: (service: string, options?: { token?: string; clientId?: string; scope?: string; secrets?: Record<string, string> }) => Promise<ConnectorAuthorizeResult>;
      connectorStatus: () => Promise<ConnectorStatusPayload>;
      connectorDisconnect: (service: string) => Promise<{ ok: boolean; service?: string; error?: string }>;
      extensionSecretsSave: (extensionId: string, values: Record<string, string>) => Promise<{ ok: boolean; extensionId?: string; envNames?: string[]; error?: string }>;
      extensionSecretsStatus: (extensionId: string) => Promise<{ ok: boolean; configured: boolean; envNames: string[] }>;
      extensionSecretsDelete: (extensionId: string) => Promise<{ ok: boolean; extensionId?: string; error?: string }>;
      window: (action: 'minimize' | 'toggle-maximize' | 'hide' | 'close' | 'quit') => Promise<{ ok: boolean }>;
      getWindowCloseBehavior: () => Promise<{ behavior: WindowCloseBehavior }>;
      setWindowCloseBehavior: (behavior: WindowCloseBehavior) => Promise<{ ok: boolean; behavior: WindowCloseBehavior; error?: string }>;
      pickFolder: () => Promise<string | null>;
      pickPythonExe: () => Promise<string | null>;
      saveFile: (payload: {
        content: string;
        defaultPath?: string;
        filters?: Array<{ name: string; extensions: string[] }>;
      }) => Promise<{ canceled: boolean; path?: string }>;
      saveBinaryFile: (payload: {
        dataUrl: string;
        defaultPath?: string;
        filters?: Array<{ name: string; extensions: string[] }>;
      }) => Promise<{ canceled: boolean; path?: string; error?: string }>;
      openExternal: (url: string) => Promise<{ ok: boolean }>;
      openPath: (path: string) => Promise<{ ok: boolean; path?: string; error?: string }>;
      getPathForFile: (file: File) => string;
      onBackendExit: (callback: (payload: unknown) => void) => () => void;
      onBootEvent: (callback: (payload: BootEvent) => void) => () => void;
      onDevServerEvent: (callback: (payload: DevServerEventPayload) => void) => () => void;
      onTerminalEvent: (callback: (payload: TerminalEventPayload) => void) => () => void;
      onPreviewState: (callback: (payload: PreviewStatePayload) => void) => () => void;
      onWindowState: (callback: (payload: { isMaximized: boolean; isFullScreen: boolean }) => void) => () => void;
      onUpdateEvent: (callback: (payload: {
        status: 'checking' | 'available' | 'not-available' | 'downloading' | 'downloaded' | 'error';
        version?: string;
        percent?: number;
        message?: string;
      }) => void) => () => void;
    };
  }
}
