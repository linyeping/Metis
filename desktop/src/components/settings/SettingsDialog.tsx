import { useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { BarChart3, Cpu, Globe, Info, MessageSquare, Monitor, Palette, Plug, Terminal, Wrench, X } from 'lucide-react';
import {
  createPermissionRule,
  deletePermissionRule,
  getMemory,
  getModelCapabilities,
  getPermissions,
  getProviderModels,
  getProviderStatus,
  getProviderUsage,
  getSettings,
  saveMemory,
  updateSettings,
  verifyProviderConfig,
} from '../../lib/api';
import type {
  DiagnosticsPayload,
  FontFamily,
  Language,
  MemoryPayload,
  ModelCapabilities,
  PermissionStatePayload,
  ProviderModelCatalog,
  ProviderProfile,
  ProviderStatusPayload,
  ProviderUsagePayload,
  ProviderValidation,
  RuntimeSettings,
  SettingsSection,
  StoragePayload,
  ThemeName,
} from '../../lib/types';
import { tr } from '../../lib/i18n';
import { useUiStore } from '../../store/uiStore';
import { useT } from '../../hooks/useT';
import { sections, type PermissionRuleDraft } from './settingsShared';
import { AppearanceTab } from './tabs/AppearanceTab';
import { ConversationTab } from './tabs/ConversationTab';
import { ModelTab } from './tabs/ModelTab';
import { UsageTab } from './tabs/UsageTab';
import { NetworkTab } from './tabs/NetworkTab';
import { TerminalTab } from './tabs/TerminalTab';
import { ToolsTab } from './tabs/ToolsTab';
import { ConnectorsTab } from './tabs/ConnectorsTab';
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
  appearance: Palette,
  conversation: MessageSquare,
  model: Cpu,
  usage: BarChart3,
  network: Globe,
  terminal: Terminal,
  tools: Wrench,
  connectors: Plug,
  desktop: Monitor,
  about: Info,
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
  const theme = useUiStore(state => state.theme);
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
  const setLanguage = useUiStore(state => state.setLanguage);
  const uiFontSize = useUiStore(state => state.uiFontSize);
  const setUiFontSize = useUiStore(state => state.setUiFontSize);
  const requestConfirm = useUiStore(state => state.requestConfirm);

  const [active, setActive] = useState<SettingsSection>(settingsSection);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [providers, setProviders] = useState<ProviderProfile[]>([]);
  const [providerCheck, setProviderCheck] = useState<ProviderValidation | null>(null);
  const [modelCatalog, setModelCatalog] = useState<ProviderModelCatalog | null>(null);
  const [modelCatalogOpen, setModelCatalogOpen] = useState(false);
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
  const [savingDiagnostics, setSavingDiagnostics] = useState(false);
  const [diagnosticsMessage, setDiagnosticsMessage] = useState('');
  const [checkingUpdates, setCheckingUpdates] = useState(false);
  const [updateMessage, setUpdateMessage] = useState('');

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
    setModelCatalogOpen(false);
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
    if (!open || !settings) {
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
  }, [open, settings?.backend, settings?.baseUrl, settings?.model, settings?.providerId]);

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
    setProviders(providerStatus.providers);
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
    if (!open || active !== 'about') return;
    if (!appInfo) void window.metis.appInfo().then(setAppInfo);
    if (!diagnostics) void refreshDiagnostics();
  }, [active, appInfo, diagnostics, open, refreshDiagnostics]);

  const save = useCallback(async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const trimmedApiKey = apiKey.trim();
      const providerId = settings.providerId || settings.backend;
      await updateSettings({
        backend: providerId,
        providerId,
        baseUrl: settings.baseUrl,
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
        ...(trimmedApiKey ? { apiKey: trimmedApiKey } : {}),
      });
      if (memory) {
        await saveMemory(memory);
      }
      await onSaved?.();
      setOpen(false);
    } finally {
      setSaving(false);
    }
  }, [apiKey, memory, onSaved, setOpen, settings]);

  const selectProvider = useCallback(
    (providerId: string) => {
      if (!settings) return;
      const provider = providers.find(item => item.providerId === providerId);
      setProviderCheck(null);
      setModelCatalog(null);
      setModelCatalogOpen(false);
      setProviderUsage(null);
      setSettings({
        ...settings,
        apiKey: '',
        backend: providerId,
        providerId,
        baseUrl: provider?.baseUrl ?? settings.baseUrl,
        model: provider?.defaultModel ?? settings.model,
      });
      setApiKey('');
    },
    [providers, settings],
  );

  const repairProviderSettings = useCallback(() => {
    if (!settings) return;
    const provider = providers.find(item => item.providerId === (settings.providerId || settings.backend));
    if (!provider) return;
    setProviderCheck(null);
    setModelCatalog(null);
    setModelCatalogOpen(false);
    setProviderUsage(null);
    setSettings({
      ...settings,
      backend: provider.providerId,
      providerId: provider.providerId,
      baseUrl: provider.baseUrl || settings.baseUrl,
      model: provider.defaultModel || settings.model,
    });
  }, [providers, settings]);

  const checkProvider = useCallback(async (deepProbe = false) => {
    if (!settings) return;
    setCheckingProvider(true);
    try {
      setProviderCheck(
        await verifyProviderConfig({
          backend: settings.providerId || settings.backend,
          baseUrl: settings.baseUrl,
          model: settings.model,
          apiKey: apiKey.trim() || settings.apiKey,
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
        baseUrl: settings.baseUrl,
        model: settings.model,
        apiKey: apiKey.trim() || settings.apiKey,
      });
      setModelCatalog(catalog);
      setModelCatalogOpen(true);
    } finally {
      setLoadingModels(false);
    }
  }, [apiKey, settings]);

  const refreshProviderUsage = useCallback(async () => {
    if (!settings) return;
    setLoadingUsage(true);
    try {
      setProviderUsage(
        await getProviderUsage({
          backend: settings.providerId || settings.backend,
          baseUrl: settings.baseUrl,
          model: settings.model,
          apiKey: apiKey.trim() || settings.apiKey,
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
      const url = (result as { url?: string }).url;
      if (url) void window.metis.openExternal?.(url);
    } finally {
      setCheckingUpdates(false);
    }
  }, []);

  const renderSettingsLoading = () => (
    <div className="settings-placeholder">
      <h3>{tr(language, active)}</h3>
      <p>设置正在读取中...</p>
    </div>
  );

  const renderActiveTab = () => {
    switch (active) {
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
            theme={theme}
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
          renderSettingsLoading()
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
            modelCatalogOpen={modelCatalogOpen}
            onApiKeyChange={setApiKey}
            onCheckProvider={checkProvider}
            onModelCatalogOpenChange={setModelCatalogOpen}
            onRefreshModelCatalog={refreshModelCatalog}
            onRepairProviderSettings={repairProviderSettings}
            onSelectProvider={selectProvider}
            onSettingsChange={value => setSettings(value)}
            providerCheck={providerCheck}
            providers={providers}
            settings={settings}
          />
        ) : (
          renderSettingsLoading()
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
          renderSettingsLoading()
        );
      case 'network':
        return settings ? <NetworkTab onSettingsChange={value => setSettings(value)} settings={settings} /> : renderSettingsLoading();
      case 'terminal':
        return settings ? <TerminalTab onSettingsChange={value => setSettings(value)} settings={settings} /> : renderSettingsLoading();
      case 'tools':
        return (
          <ToolsTab
            capabilities={modelCapabilities}
            permissions={permissions}
            onRefresh={() => refreshPermissions(true)}
            onCreate={createPermission}
            onDeleteMany={deletePermissions}
            onDelete={deletePermission}
          />
        );
      case 'connectors':
        return <ConnectorsTab />;
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
            onRefreshDiagnostics={refreshDiagnostics}
            onSaveDiagnosticsBundle={saveDiagnosticsBundle}
            savingDiagnostics={savingDiagnostics}
            updateMessage={updateMessage}
          />
        );
      default:
        return (
          <div className="settings-placeholder">
            <h3>{tr(language, active)}</h3>
            <p>{tr(language, 'comingSoon')}</p>
          </div>
        );
    }
  };

  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          className="modal-layer"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.16 } }}
          transition={{ duration: 0.18 }}
        >
          <motion.section
            className="settings-dialog"
            data-active-section={active}
            initial={{ scale: 0.96, y: 8, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.97, y: 6, opacity: 0, transition: { duration: 0.14, ease: [0.16, 1, 0.3, 1] } }}
            transition={{ type: 'spring', stiffness: 380, damping: 28 }}
          >
            <header>
              <div>
                <h2>{tr(language, 'settingsTitle')}</h2>
                <p>Metis Desktop</p>
              </div>
              <button type="button" onClick={() => setOpen(false)}>
                <X size={18} />
              </button>
            </header>
            <div className="settings-body">
              <nav>
                {sections.map(section => {
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
                      <Icon size={15} />
                      <span>{tr(language, section)}</span>
                    </button>
                  );
                })}
              </nav>
              <div className="settings-panel">{renderActiveTab()}</div>
            </div>
            <footer>
              <button type="button" onClick={() => setOpen(false)}>
                {t('取消')}
              </button>
              <button type="button" className="primary" disabled={saving || !settings} onClick={() => void save()}>
                {saving ? t('保存中...') : tr(language, 'saveSettings')}
              </button>
            </footer>
          </motion.section>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
