import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, BarChart3, Copy, Cpu, Egg, Globe, HardDrive, Info, MessageSquare, Minus, Monitor, Palette, Plug, Search, Settings2, Square, Terminal, Wrench, X } from 'lucide-react';
import {
  createPermissionWritableRoot,
  createPermissionRule,
  deletePermissionWritableRoot,
  deletePermissionRule,
  getDocumentConverters,
  getMemory,
  getMetisRuntimeStatus,
  getModelCapabilities,
  getPermissions,
  getProviderModels,
  getProviderStatus,
  getProviderUsage,
  getRuntimeManagerStatus,
  getSettings,
  runtimeManagerBuildVmAssets,
  runtimeManagerBuildPlan,
  runtimeManagerDiagnostics,
  runtimeManagerImport,
  runtimeManagerImportPlan,
  runtimeManagerPackageBundle,
  runtimeManagerPackageVmBundle,
  runtimeManagerPrepareBundle,
  runtimeManagerRepair,
  runtimeManagerSmoke,
  runtimeManagerStartupTest,
  runtimeManagerValidateRelease,
  repairMetisRuntime,
  saveMemory,
  updateSettings,
  verifyProviderConfig,
} from '../../lib/api';
import type {
  DiagnosticsPayload,
  DocumentConverterStatus,
  FontFamily,
  Language,
  MemoryPayload,
  ModelCapabilities,
  MetisRuntimeStatus,
  PermissionStatePayload,
  ProviderModelCatalog,
  ProviderStatusPayload,
  ProviderUsagePayload,
  ProviderValidation,
  RuntimeManagerCommandResult,
  RuntimeManagerStatus,
  RuntimeSettings,
  SettingsSection,
  StoragePayload,
  ThemeName,
} from '../../lib/types';
import { tr } from '../../lib/i18n';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import { useWindowState } from '../../hooks/useWindowState';
import { settingsNavGroups, stripConfigWhitespace, type PermissionRuleDraft } from './settingsShared';
import { GeneralTab } from './tabs/GeneralTab';
import { AppearanceTab } from './tabs/AppearanceTab';
import { ConversationTab } from './tabs/ConversationTab';
import { ModelTab } from './tabs/ModelTab';
import { UsageTab } from './tabs/UsageTab';
import { NetworkTab } from './tabs/NetworkTab';
import { TerminalTab } from './tabs/TerminalTab';
import { RuntimeTab } from './tabs/RuntimeTab';
import { ToolsTab } from './tabs/ToolsTab';
import { ConnectorsTab } from './tabs/ConnectorsTab';
import { PetsTab } from './tabs/PetsTab';
import { DesktopTab } from './tabs/DesktopTab';
import { AboutTab } from './tabs/AboutTab';

const SETTINGS_API_CACHE_MS = 30_000;

type AppInfo = {
  name: string;
  version: string;
  packaged: boolean;
  updateUrl: string;
  fakeBackend?: boolean;
  storage?: StoragePayload;
};

type CacheEntry<T> = {
  data: T;
  expiresAt: number;
};

let providerStatusCache: CacheEntry<ProviderStatusPayload> | null = null;
let permissionsCache: CacheEntry<PermissionStatePayload> | null = null;

const sectionIcons: Record<SettingsSection, typeof Palette> = {
  general: Settings2,
  appearance: Palette,
  conversation: MessageSquare,
  model: Cpu,
  usage: BarChart3,
  network: Globe,
  terminal: Terminal,
  runtime: HardDrive,
  tools: Wrench,
  connectors: Plug,
  pets: Egg,
  desktop: Monitor,
  about: Info,
};

const sectionDescriptions: Record<SettingsSection, string> = {
  general: '窗口关闭和应用级行为。',
  appearance: '主题、语言、字体和界面密度。',
  conversation: '记忆、自动技能和对话行为。',
  model: '供应商、模型、API 地址和推理参数。',
  usage: '模型额度、用量统计和供应商账单状态。',
  network: '代理、网络访问和外部连接配置。',
  terminal: '默认 shell、Python 路径和文档转换器。',
  runtime: 'MetisRuntime、本机隔离执行和诊断修复工具。',
  tools: '工具权限、写入目录和自动审批规则。',
  connectors: '外部服务、MCP 和桌面连接能力。',
  pets: '桌面宠物、任务状态动画和显示行为。',
  desktop: '桌面接管、视觉能力和本机集成。',
  about: '版本、更新、诊断包和应用状态。',
};

async function getProviderStatusCached(force = false): Promise<ProviderStatusPayload> {
  const now = Date.now();
  if (!force && providerStatusCache && providerStatusCache.expiresAt > now) {
    return providerStatusCache.data;
  }
  const data = await getProviderStatus();
  providerStatusCache = { data, expiresAt: now + SETTINGS_API_CACHE_MS };
  return data;
}

async function getPermissionsCached(force = false): Promise<PermissionStatePayload> {
  const now = Date.now();
  if (!force && permissionsCache && permissionsCache.expiresAt > now) {
    return permissionsCache.data;
  }
  const data = await getPermissions();
  permissionsCache = { data, expiresAt: now + SETTINGS_API_CACHE_MS };
  return data;
}

interface SettingsDialogProps {
  onSaved?: () => Promise<void> | void;
}

export function SettingsDialog({ onSaved }: SettingsDialogProps = {}) {
  const open = useUiStore(state => state.settingsOpen);
  const setOpen = useUiStore(state => state.setSettingsOpen);
  const settingsSection = useUiStore(state => state.settingsSection);
  const setSettingsSection = useUiStore(state => state.setSettingsSection);
  const setTheme = useUiStore(state => state.setTheme);
  const appearanceMode = useUiStore(state => state.appearanceMode);
  const lightTheme = useUiStore(state => state.lightTheme);
  const darkTheme = useUiStore(state => state.darkTheme);
  const setAppearanceMode = useUiStore(state => state.setAppearanceMode);
  const codeFontSize = useUiStore(state => state.codeFontSize);
  const setCodeFontSize = useUiStore(state => state.setCodeFontSize);
  const fontFamily = useUiStore(state => state.fontFamily);
  const setFontFamily = useUiStore(state => state.setFontFamily);
  const language = useUiStore(state => state.language);
  const t = useT();
  const { isFullScreen, isMaximized } = useWindowState();
  const setLanguage = useUiStore(state => state.setLanguage);
  const uiFontSize = useUiStore(state => state.uiFontSize);
  const setUiFontSize = useUiStore(state => state.setUiFontSize);
  const requestConfirm = useUiStore(state => state.requestConfirm);

  const [active, setActive] = useState<SettingsSection>(settingsSection);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [providerCheck, setProviderCheck] = useState<ProviderValidation | null>(null);
  const [modelCatalog, setModelCatalog] = useState<ProviderModelCatalog | null>(null);
  const [providerUsage, setProviderUsage] = useState<ProviderUsagePayload | null>(null);
  const [modelCapabilities, setModelCapabilities] = useState<ModelCapabilities | null>(null);
  const [modelCapabilitiesError, setModelCapabilitiesError] = useState('');
  const [memory, setMemory] = useState<MemoryPayload | null>(null);
  const [permissions, setPermissions] = useState<PermissionStatePayload | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [checkingProvider, setCheckingProvider] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);
  const [loadingUsage, setLoadingUsage] = useState(false);
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsPayload | null>(null);
  const [documentConverters, setDocumentConverters] = useState<DocumentConverterStatus | null>(null);
  const [runtimeManager, setRuntimeManager] = useState<RuntimeManagerStatus | null>(null);
  const [metisRuntime, setMetisRuntime] = useState<MetisRuntimeStatus | null>(null);
  const [runtimeManagerBusy, setRuntimeManagerBusy] = useState('');
  const [runtimeManagerMessage, setRuntimeManagerMessage] = useState('');
  const [runtimeManagerResult, setRuntimeManagerResult] = useState<RuntimeManagerCommandResult | null>(null);
  const [savingDiagnostics, setSavingDiagnostics] = useState(false);
  const [diagnosticsMessage, setDiagnosticsMessage] = useState('');
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  const [updateMessage, setUpdateMessage] = useState('');
  const [updateReady, setUpdateReady] = useState(false);
  const [filter, setFilter] = useState('');
  const [saveComplete, setSaveComplete] = useState(false);

  useEffect(() => {
    if (!open) return;
    setActive(settingsSection);
  }, [open, settingsSection]);

  useEffect(() => {
    if (!open) return;
    let canceled = false;
    setSettings(null);
    setProviderCheck(null);
    setModelCatalog(null);
    setProviderUsage(null);
    setApiKey('');
    void getSettings().then(data => {
      if (canceled) return;
      setSettings(data);
      setProviderCheck(data.providerValidation ?? null);
    });
    return () => {
      canceled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open || active !== 'model' || !settings) {
      if (!open) {
        setModelCapabilities(null);
        setModelCapabilitiesError('');
      }
      return;
    }
    let canceled = false;
    setModelCapabilitiesError('');
    void getModelCapabilities(settings)
      .then(data => {
        if (!canceled) setModelCapabilities(data);
      })
      .catch(error => {
        if (canceled) return;
        setModelCapabilities(null);
        setModelCapabilitiesError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      canceled = true;
    };
  }, [active, open, settings?.backend, settings?.baseUrl, settings?.model, settings?.providerId]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, setOpen]);

  useEffect(() => {
    if (open) return;
    permissionsCache = null;
    setPermissions(null);
  }, [open]);

  const refreshProviderStatus = useCallback(async (force = false) => {
    const providerStatus = await getProviderStatusCached(force);
    setProviderCheck(current => current ?? providerStatus.active);
  }, []);

  const refreshMemory = useCallback(async () => {
    setMemory(await getMemory());
  }, []);

  const refreshPermissions = useCallback(async (force = false) => {
    setPermissions(await getPermissionsCached(force));
  }, []);

  const refreshDiagnostics = useCallback(async () => {
    setDiagnostics(await window.metis.diagnostics());
    setDiagnosticsMessage('');
  }, []);

  useEffect(() => {
    if (!open || active !== 'model') return;
    void refreshProviderStatus(false);
  }, [active, open, refreshProviderStatus]);

  useEffect(() => {
    if (!open || active !== 'conversation' || memory) return;
    void refreshMemory();
  }, [active, memory, open, refreshMemory]);

  useEffect(() => {
    if (!open || active !== 'tools' || permissions) return;
    void refreshPermissions(false);
  }, [active, open, permissions, refreshPermissions]);

  useEffect(() => {
    if (!open || active !== 'terminal') return;
    void getDocumentConverters().then(setDocumentConverters).catch(() => setDocumentConverters(null));
  }, [active, open]);

  const refreshRuntimeManager = useCallback(async () => {
    setRuntimeManagerBusy(current => current || 'refresh');
    try {
      const [managerStatus, metisStatus] = await Promise.all([
        getRuntimeManagerStatus(),
        getMetisRuntimeStatus(),
      ]);
      setRuntimeManager(managerStatus);
      setMetisRuntime(metisStatus);
      setRuntimeManagerMessage('');
    } catch (error) {
      setRuntimeManagerMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setRuntimeManagerBusy(current => (current === 'refresh' ? '' : current));
    }
  }, []);

  useEffect(() => {
    if (!open || active !== 'runtime') return;
    void refreshRuntimeManager();
  }, [active, open, refreshRuntimeManager]);

  useEffect(() => {
    if (!open || active !== 'about') return;
    if (!appInfo) void window.metis.appInfo().then(setAppInfo);
    if (!diagnostics) void refreshDiagnostics();
  }, [active, appInfo, diagnostics, open, refreshDiagnostics]);

  const save = useCallback(async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const cleanedApiKey = stripConfigWhitespace(apiKey);
      const cleanedBaseUrl = stripConfigWhitespace(settings.baseUrl);
      const providerId = settings.providerId || settings.backend;
      await updateSettings({
        backend: providerId,
        providerId,
        baseUrl: cleanedBaseUrl,
        model: settings.model,
        temperature: settings.temperature,
        reasoningEffort: settings.reasoningEffort,
        maxTokens: settings.maxTokens,
        autoMemory: settings.autoMemory,
        autoSkills: settings.autoSkills,
        proxyMode: settings.proxyMode,
        proxyScheme: settings.proxyScheme,
        proxyHost: settings.proxyHost,
        proxyPort: settings.proxyPort,
        proxyBypass: settings.proxyBypass,
        terminalShell: settings.terminalShell,
        pythonPath: settings.pythonPath,
        ...(cleanedApiKey ? { apiKey: cleanedApiKey } : {}),
      });
      if (memory) {
        await saveMemory(memory);
      }
      await onSaved?.();
      setSaveComplete(true);
      window.setTimeout(() => setSaveComplete(false), 1800);
    } finally {
      setSaving(false);
    }
  }, [apiKey, memory, onSaved, settings]);

  const checkProvider = useCallback(async (deepProbe = false) => {
    if (!settings) return;
    setCheckingProvider(true);
    try {
      setProviderCheck(
        await verifyProviderConfig({
          backend: settings.providerId || settings.backend,
          baseUrl: stripConfigWhitespace(settings.baseUrl),
          model: settings.model,
          apiKey: stripConfigWhitespace(apiKey) || stripConfigWhitespace(settings.apiKey),
          deepProbe,
        }),
      );
    } finally {
      setCheckingProvider(false);
    }
  }, [apiKey, settings]);

  const refreshModelCatalog = useCallback(async () => {
    if (!settings) return;
    setLoadingModels(true);
    try {
      const catalog = await getProviderModels({
        backend: settings.providerId || settings.backend,
        baseUrl: stripConfigWhitespace(settings.baseUrl),
        model: settings.model,
        apiKey: stripConfigWhitespace(apiKey) || stripConfigWhitespace(settings.apiKey),
        remoteOnly: true,
      });
      setModelCatalog(catalog);
    } finally {
      setLoadingModels(false);
    }
  }, [apiKey, settings]);

  useEffect(() => {
    if (!open || active !== 'model' || !settings) return undefined;
    const cleanedBaseUrl = stripConfigWhitespace(settings.baseUrl);
    const usableKey = stripConfigWhitespace(apiKey) || stripConfigWhitespace(settings.apiKey);
    if (!cleanedBaseUrl || !usableKey) return undefined;
    const timer = window.setTimeout(() => {
      void refreshModelCatalog();
    }, 700);
    return () => window.clearTimeout(timer);
  }, [active, apiKey, open, refreshModelCatalog, settings]);

  const refreshProviderUsage = useCallback(async () => {
    if (!settings) return;
    setLoadingUsage(true);
    try {
      setProviderUsage(
        await getProviderUsage({
          backend: settings.providerId || settings.backend,
          baseUrl: stripConfigWhitespace(settings.baseUrl),
          model: settings.model,
          apiKey: stripConfigWhitespace(apiKey) || stripConfigWhitespace(settings.apiKey),
        }),
      );
    } finally {
      setLoadingUsage(false);
    }
  }, [apiKey, settings]);

  const createPermission = useCallback(
    async (payload: PermissionRuleDraft) => {
      await createPermissionRule({
        tool: payload.tool,
        action: payload.action,
        argsMatch: payload.argsMatch,
        source: payload.source,
      });
      permissionsCache = null;
      await refreshPermissions(true);
    },
    [refreshPermissions],
  );

  const deletePermissions = useCallback(
    async (ruleIds: string[]) => {
      await Promise.all(ruleIds.map(ruleId => deletePermissionRule(ruleId)));
      permissionsCache = null;
      await refreshPermissions(true);
    },
    [refreshPermissions],
  );

  const deletePermission = useCallback(
    async (ruleId: string, tool: string) => {
      const confirmed = await requestConfirm({
        title: t('删除权限规则？'),
        message: `${t('删除后，')}${tool || t('这个工具')} ${t('下次遇到风险操作会重新询问。')}`,
        confirmLabel: t('删除'),
        cancelLabel: t('取消'),
        tone: 'danger',
        icon: 'trash',
      });
      if (!confirmed) return;
      await deletePermissionRule(ruleId);
      permissionsCache = null;
      await refreshPermissions(true);
    },
    [refreshPermissions, requestConfirm, t],
  );

  const createWritableRoot = useCallback(
    async (path: string) => {
      await createPermissionWritableRoot(path, 'settings');
      permissionsCache = null;
      await refreshPermissions(true);
    },
    [refreshPermissions],
  );

  const deleteWritableRoot = useCallback(
    async (rootId: string, path: string) => {
      const confirmed = await requestConfirm({
        title: t('删除授权目录？'),
        message: `${path || t('这个目录')} ${t('删除后，Metis 不能再写入该工作区外目录。')}`,
        confirmLabel: t('删除'),
        cancelLabel: t('取消'),
        tone: 'danger',
        icon: 'trash',
      });
      if (!confirmed) return;
      await deletePermissionWritableRoot(rootId);
      permissionsCache = null;
      await refreshPermissions(true);
    },
    [refreshPermissions, requestConfirm, t],
  );

  const saveDiagnosticsBundle = useCallback(async () => {
    setSavingDiagnostics(true);
    setDiagnosticsMessage('');
    try {
      const result = await window.metis.saveDiagnosticsBundle();
      if (result.diagnostics) setDiagnostics(result.diagnostics);
      setDiagnosticsMessage(result.canceled ? t('已取消生成诊断包。') : `${t('诊断包已保存: ')}${result.path || ''}`);
    } finally {
      setSavingDiagnostics(false);
    }
  }, [t]);

  const checkUpdates = useCallback(async () => {
    setCheckingUpdates(true);
    setUpdateMessage('');
    try {
      const result = await window.metis.checkUpdates();
      setUpdateMessage(result.message);
      setUpdateReady(result.status === 'downloaded');
      const url = (result as { url?: string }).url;
      if (url) void window.metis.openExternal?.(url);
    } finally {
      setCheckingUpdates(false);
    }
  }, []);

  const installUpdate = useCallback(async () => {
    const result = await window.metis.installUpdate();
    if (!result.ok && result.message) setUpdateMessage(result.message);
  }, []);

  useEffect(() => {
    if (!window.metis.onUpdateEvent) return undefined;
    return window.metis.onUpdateEvent(payload => {
      if (payload.status === 'downloaded') {
        setUpdateReady(true);
        setUpdateMessage(`新版本 v${payload.version || ''} 已下载完成，点击重启以更新。`);
      }
    });
  }, []);

  const runRuntimeAction = useCallback(
    async (name: string, action: () => Promise<RuntimeManagerCommandResult>) => {
      setRuntimeManagerBusy(name);
      setRuntimeManagerMessage('');
      try {
        const result = await action();
        setRuntimeManagerResult(result);
        setRuntimeManagerMessage(result.message || (result.ok ? t('操作完成。') : result.error || t('操作未完成。')));
        await refreshRuntimeManager();
      } catch (error) {
        setRuntimeManagerMessage(error instanceof Error ? error.message : String(error));
      } finally {
        setRuntimeManagerBusy('');
      }
    },
    [refreshRuntimeManager, t],
  );

  const repairRuntimeOneClick = useCallback(
    () =>
      runRuntimeAction('metis-runtime-repair', async () => {
        const result = await repairMetisRuntime({
          allowDownload: metisRuntime?.repairRequiresDownload ?? true,
        });
        setMetisRuntime(result);
        return result as unknown as RuntimeManagerCommandResult;
      }),
    [metisRuntime?.repairRequiresDownload, runRuntimeAction],
  );

  const visibleNavGroups = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return settingsNavGroups
      .map(group => ({
        ...group,
        sections: group.sections.filter(section => {
          if (!query) return true;
          const label = tr(language, section).toLowerCase();
          const description = sectionDescriptions[section].toLowerCase();
          const groupLabel = (language === 'zh' ? group.labelZh : group.labelEn).toLowerCase();
          return label.includes(query) || section.includes(query) || description.includes(query) || groupLabel.includes(query);
        }),
      }))
      .filter(group => group.sections.length > 0);
  }, [filter, language]);

  useEffect(() => {
    if (!open) return;
    const hasActiveSection = visibleNavGroups.some(group => group.sections.includes(active));
    if (!hasActiveSection && visibleNavGroups[0]?.sections[0]) {
      const next = visibleNavGroups[0].sections[0];
      setActive(next);
      setSettingsSection(next);
    }
  }, [active, open, setSettingsSection, visibleNavGroups]);

  const renderSettingsLoading = (section: SettingsSection) => (
    <div className="settings-placeholder">
      <h3>{tr(language, section)}</h3>
      <p>设置正在读取中...</p>
    </div>
  );

  const renderSettingsContent = (section: SettingsSection) => {
    switch (section) {
      case 'general':
        return <GeneralTab />;
      case 'appearance':
        return (
          <AppearanceTab
            appearanceMode={appearanceMode}
            codeFontSize={codeFontSize}
            darkTheme={darkTheme}
            fontFamily={fontFamily}
            language={language}
            lightTheme={lightTheme}
            onAppearanceModeChange={setAppearanceMode}
            onCodeFontSizeChange={setCodeFontSize}
            onFontFamilyChange={value => setFontFamily(value as FontFamily)}
            onLanguageChange={value => setLanguage(value as Language)}
            onThemeChange={value => setTheme(value as ThemeName)}
            onUiFontSizeChange={setUiFontSize}
            uiFontSize={uiFontSize}
          />
        );
      case 'conversation':
        return settings ? (
          <ConversationTab
            memory={memory}
            onMemoryChange={value => setMemory(value)}
            onSettingsChange={value => setSettings(value)}
            settings={settings}
          />
        ) : (
          renderSettingsLoading(section)
        );
      case 'model':
        return settings ? (
          <ModelTab
            apiKey={apiKey}
            capabilities={modelCapabilities}
            capabilitiesError={modelCapabilitiesError}
            checkingProvider={checkingProvider}
            language={language}
            loadingModels={loadingModels}
            modelCatalog={modelCatalog}
            onApiKeyChange={value => {
              setApiKey(value);
              setProviderCheck(null);
              setModelCatalog(null);
            }}
            onCheckProvider={checkProvider}
            onRefreshModelCatalog={refreshModelCatalog}
            onSettingsChange={value => {
              if (
                value.backend !== settings.backend ||
                value.providerId !== settings.providerId ||
                value.baseUrl !== settings.baseUrl ||
                value.model !== settings.model
              ) {
                setProviderCheck(null);
                setModelCatalog(null);
                setProviderUsage(null);
              }
              setSettings(value);
            }}
            providerCheck={providerCheck}
            settings={settings}
          />
        ) : (
          renderSettingsLoading(section)
        );
      case 'usage':
        return settings ? (
          <UsageTab
            loadingUsage={loadingUsage}
            onRefreshProviderUsage={refreshProviderUsage}
            providerUsage={providerUsage}
            settings={settings}
          />
        ) : (
          renderSettingsLoading(section)
        );
      case 'network':
        return settings ? <NetworkTab apiKey={apiKey} onSettingsChange={value => setSettings(value)} settings={settings} /> : renderSettingsLoading(section);
      case 'terminal':
        return settings ? (
          <TerminalTab
            documentConverters={documentConverters}
            onRefreshDocumentConverters={() => getDocumentConverters().then(setDocumentConverters)}
            onSettingsChange={value => setSettings(value)}
            settings={settings}
          />
        ) : renderSettingsLoading(section);
      case 'runtime':
        return (
          <RuntimeTab
            busy={runtimeManagerBusy}
            message={runtimeManagerMessage}
            onBuildVmAssets={() =>
              runRuntimeAction('build-vm-assets', () =>
                runtimeManagerBuildVmAssets({ dryRun: false, allowNetwork: true, packageBundle: true, profile: 'standard' }),
              )
            }
            onBuildVmAssetsPlan={() => runRuntimeAction('build-vm-assets-plan', () => runtimeManagerBuildVmAssets({ dryRun: true, profile: 'standard' }))}
            onBuildPlan={() => runRuntimeAction('build-plan', () => runtimeManagerBuildPlan('standard'))}
            onDiagnostics={(sessionId = '') => runRuntimeAction('diagnostics', () => runtimeManagerDiagnostics(sessionId))}
            onImport={() => runRuntimeAction('import', runtimeManagerImport)}
            onImportPlan={() => runRuntimeAction('import-plan', runtimeManagerImportPlan)}
            onPackageBundle={() => runRuntimeAction('package-bundle', () => runtimeManagerPackageBundle('', 'local'))}
            onPackageVmBundle={() => runRuntimeAction('package-vm-bundle', () => runtimeManagerPackageVmBundle('', 'direct'))}
            onPrepareBundle={() => runRuntimeAction('prepare-bundle', () => runtimeManagerPrepareBundle('', 'local'))}
            onRefresh={refreshRuntimeManager}
            onRepairMetisRuntime={repairRuntimeOneClick}
            onRepair={() =>
              runRuntimeAction('repair-runtime', () =>
                runtimeManagerRepair({ source: 'auto', allowDownload: Boolean(runtimeManager?.releaseIntegration.downloadAvailable) }),
              )
            }
            onSmoke={() => runRuntimeAction('smoke', runtimeManagerSmoke)}
            onStartupTest={() => runRuntimeAction('startup-test', runtimeManagerStartupTest)}
            onValidateRelease={() => runRuntimeAction('validate-release', () => runtimeManagerValidateRelease())}
            metisRuntime={metisRuntime}
            result={runtimeManagerResult}
            status={runtimeManager}
          />
        );
      case 'tools':
        return (
          <ToolsTab
            capabilities={modelCapabilities}
            permissions={permissions}
            onRefresh={() => refreshPermissions(true)}
            onCreate={createPermission}
            onDeleteMany={deletePermissions}
            onDelete={deletePermission}
            onCreateWritableRoot={createWritableRoot}
            onDeleteWritableRoot={deleteWritableRoot}
          />
        );
      case 'connectors':
        return <ConnectorsTab />;
      case 'pets':
        return <PetsTab language={language} />;
      case 'desktop':
        return <DesktopTab capabilities={modelCapabilities} capabilitiesError={modelCapabilitiesError} />;
      case 'about':
        return (
          <AboutTab
            appInfo={appInfo}
            checkingUpdates={checkingUpdates}
            diagnostics={diagnostics}
            diagnosticsMessage={diagnosticsMessage}
            onCheckUpdates={checkUpdates}
            onInstallUpdate={installUpdate}
            onRefreshDiagnostics={refreshDiagnostics}
            onSaveDiagnosticsBundle={saveDiagnosticsBundle}
            savingDiagnostics={savingDiagnostics}
            updateMessage={updateMessage}
            updateReady={updateReady}
          />
        );
      default:
        return (
          <div className="settings-placeholder">
            <h3>{tr(language, section)}</h3>
            <p>{tr(language, 'comingSoon')}</p>
          </div>
        );
    }
  };

  if (!open) return null;

  return (
        <div className="modal-layer settings-page-layer">
          <section
            className="settings-dialog"
            data-active-section={active}
            role="dialog"
            aria-modal="true"
            aria-label={tr(language, 'settingsTitle')}
          >
            <header className="titlebar settings-window-titlebar" aria-label={t('窗口控制')}>
              <div className="titlebar-brand" aria-hidden="true" />
              <div className="titlebar-actions settings-window-actions">
                <button type="button" title={t('最小化')} onClick={() => void window.metis.window('minimize')}>
                  <Minus size={15} />
                </button>
                <button type="button" title={t('最大化或还原')} onClick={() => void window.metis.window('toggle-maximize')}>
                  {isMaximized || isFullScreen ? <Copy size={13} /> : <Square size={13} />}
                </button>
                <button type="button" title={t('关闭')} onClick={() => void window.metis.window('close')}>
                  <X size={15} />
                </button>
              </div>
            </header>
            <aside className="settings-sidebar">
              <div className="settings-sidebar-top">
                <button type="button" className="settings-back-button" onClick={() => setOpen(false)}>
                  <ArrowLeft size={17} />
                  <span>{t('返回应用')}</span>
                </button>
                <div className="settings-brand">
                  <strong>{tr(language, 'settingsTitle')}</strong>
                  <small>Metis Desktop</small>
                </div>
              </div>
              <label className="settings-search">
                <Search size={15} />
                <input value={filter} onChange={event => setFilter(event.currentTarget.value)} placeholder={t('搜索设置')} />
              </label>
              <nav className="settings-nav" aria-label={t('设置分类')}>
                {visibleNavGroups.map(group => (
                  <section className="settings-nav-group" key={group.id}>
                    <div className="settings-nav-group-label">{language === 'zh' ? group.labelZh : group.labelEn}</div>
                    <div className="settings-nav-group-items">
                      {group.sections.map(section => {
                        const Icon = sectionIcons[section];
                        return (
                          <button
                            type="button"
                            key={section}
                            data-active={active === section}
                            onClick={() => {
                              setActive(section);
                              setSettingsSection(section);
                            }}
                          >
                            <Icon size={16} fill={section === 'pets' ? 'currentColor' : 'none'} />
                            <span>{tr(language, section)}</span>
                          </button>
                        );
                      })}
                    </div>
                  </section>
                ))}
                {visibleNavGroups.length === 0 && (
                  <div className="settings-nav-empty">{t('没有匹配的设置项')}</div>
                )}
              </nav>
            </aside>
            <div className="settings-body">
              <main className="settings-main">
                <header className="settings-main-header">
                  <div className="settings-title-block">
                    <h2>{tr(language, active)}</h2>
                    <p>{sectionDescriptions[active]}</p>
                  </div>
                  <div className="settings-main-actions">
                    <button type="button" onClick={() => setOpen(false)}>
                      {t('取消')}
                    </button>
                    <button type="button" className="primary" disabled={saving || !settings} onClick={() => void save()}>
                      {saving ? t('保存中...') : saveComplete ? t('已保存') : tr(language, 'saveSettings')}
                    </button>
                  </div>
                </header>
                <div className="settings-panel">
                  {visibleNavGroups.length > 0 ? renderSettingsContent(active) : (
                    <div className="settings-panel-empty">{t('没有匹配的设置项')}</div>
                  )}
                </div>
              </main>
            </div>
          </section>
        </div>
  );
}
