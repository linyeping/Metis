import { memo, useMemo } from 'react';
import { ChevronDown, ChevronRight, Cpu, RefreshCw, Server, SlidersHorizontal } from 'lucide-react';
import type {
  Language,
  ModelCapabilities,
  ProviderModelCatalog,
  ProviderProfile,
  ProviderValidation,
  RuntimeSettings,
} from '../../../lib/types';
import { tr } from '../../../lib/i18n';
import { formatSettingsTokenCount } from '../settingsShared';
import { ProviderRegistryManager } from '../ProviderRegistryManager';
import { useT } from '../../../hooks/useT';

interface ModelTabProps {
  apiKey: string;
  capabilities: ModelCapabilities | null;
  capabilitiesError: string;
  checkingProvider: boolean;
  language: Language;
  loadingModels: boolean;
  modelCatalog: ProviderModelCatalog | null;
  modelCatalogOpen: boolean;
  onApiKeyChange: (value: string) => void;
  onCheckProvider: (deepProbe?: boolean) => void | Promise<void>;
  onModelCatalogOpenChange: (value: boolean | ((current: boolean) => boolean)) => void;
  onRefreshModelCatalog: () => void | Promise<void>;
  onRepairProviderSettings: () => void;
  onSelectProvider: (providerId: string) => void;
  onSettingsChange: (value: RuntimeSettings) => void;
  providerCheck: ProviderValidation | null;
  providers: ProviderProfile[];
  settings: RuntimeSettings;
}

export const ModelTab = memo(function ModelTab({
  apiKey,
  capabilities,
  capabilitiesError,
  checkingProvider,
  language,
  loadingModels,
  modelCatalog,
  modelCatalogOpen,
  onApiKeyChange,
  onCheckProvider,
  onModelCatalogOpenChange,
  onRefreshModelCatalog,
  onRepairProviderSettings,
  onSelectProvider,
  onSettingsChange,
  providerCheck,
  providers,
  settings,
}: ModelTabProps) {
  const t = useT();
  const currentProvider = useMemo(
    () => providers.find(item => item.providerId === (settings.providerId || settings.backend)) ?? null,
    [providers, settings.backend, settings.providerId],
  );
  const providerPresetModels = useMemo(
    () => (currentProvider ? Array.from(new Set([currentProvider.defaultModel, ...currentProvider.fallbackModels].filter(Boolean))) : []),
    [currentProvider],
  );
  const providerApiFamily = currentProvider
    ? currentProvider.openaiCompatible
      ? 'OpenAI-compatible Chat Completions'
      : currentProvider.backendType
    : '';
  const providerEndpointPreview =
    currentProvider?.openaiCompatible && settings.baseUrl
      ? `${settings.baseUrl.replace(/\/+$/, '')}${currentProvider.chatCompletionsPath || '/chat/completions'}`
      : '';
  const providerModelMismatch = Boolean(
    currentProvider &&
      settings.model &&
      providerPresetModels.length > 0 &&
      !providerPresetModels.includes(settings.model) &&
      /^(gpt-|o\d|claude|gemini|kimi|glm|qwen|deepseek)/i.test(settings.model),
  );
  const tierVariant = capabilities ? (capabilities.tier <= 1 ? 'success' : capabilities.tier === 2 ? 'warning' : 'danger') : 'neutral';

  return (
    <div className="settings-card-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <Server size={16} className="section-icon" />
          <h3>{t('Provider 配置')}</h3>
        </div>
        <label>
          <span>{tr(language, 'provider')}</span>
          <select value={settings.providerId || settings.backend} onChange={event => onSelectProvider(event.target.value)}>
            {providers.map(provider => (
              <option key={provider.providerId} value={provider.providerId}>
                {provider.displayName}
              </option>
            ))}
          </select>
        </label>
        {currentProvider && (
          <div className="provider-profile-panel" data-mismatch={providerModelMismatch}>
            <div className="provider-profile-head">
              <span>
                <strong>{currentProvider.displayName}</strong>
                <em>{providerApiFamily}</em>
              </span>
              <button type="button" onClick={onRepairProviderSettings}>
                {t('修复当前配置')}
              </button>
            </div>
            <div className="provider-profile-grid">
              <span>
                <small>Provider ID</small>
                <strong>{currentProvider.providerId}</strong>
              </span>
              <span>
                <small>{t('默认模型')}</small>
                <strong>{currentProvider.defaultModel || t('手动填写')}</strong>
              </span>
              <span>
                <small>{t('本地预设')}</small>
                <strong>{providerPresetModels.length || 0} {t('个')}</strong>
              </span>
              <span>
                <small>{t('工具调用')}</small>
                <strong>{currentProvider.capabilities.tools ? t('支持') : t('未知/不支持')}</strong>
              </span>
            </div>
            {providerEndpointPreview && <code>{providerEndpointPreview}</code>}
            {providerPresetModels.length > 0 && (
              <div className="provider-preset-strip">
                {providerPresetModels.map(modelId => (
                  <button
                    type="button"
                    key={modelId}
                    data-active={settings.model === modelId}
                    onClick={() => onSettingsChange({ ...settings, model: modelId })}
                  >
                    <span>{modelId}</span>
                    <small>{formatSettingsTokenCount(currentProvider.modelContextWindows[modelId] || 0)}</small>
                  </button>
                ))}
              </div>
            )}
            {providerModelMismatch && (
              <p className="provider-profile-warning">
                {t('当前模型看起来不属于 ')}{currentProvider.displayName}{t('。建议修复配置，或选择本地预设模型。')}
              </p>
            )}
          </div>
        )}
        <label>
          <span>Base URL</span>
          <input
            className="settings-base-url-input"
            value={settings.baseUrl}
            spellCheck={false}
            onChange={event => onSettingsChange({ ...settings, baseUrl: event.target.value })}
          />
        </label>
        <label>
          <span>{tr(language, 'apiKey')}</span>
          <input
            className="settings-api-key-input"
            value={apiKey}
            placeholder={settings.apiKey || (settings.providerId === 'custom-openai' ? '' : 'sk-...')}
            spellCheck={false}
            onChange={event => onApiKeyChange(event.target.value)}
          />
        </label>
        <div className="provider-check-panel" data-ok={providerCheck?.ok ?? false}>
          <div className="settings-action-row">
            <button type="button" disabled={checkingProvider} onClick={() => void onCheckProvider(false)}>
              {checkingProvider ? t('检查中...') : t('本地检查配置')}
            </button>
            <button type="button" disabled={checkingProvider} onClick={() => void onCheckProvider(true)}>
              {checkingProvider ? t('探测中...') : t('深度探测')}
            </button>
          </div>
          {providerCheck && (
            <div>
              <strong>{t(providerCheck.title)}</strong>
              <span>{t(providerCheck.message)}</span>
              {providerCheck.chatUrl && <small>{providerCheck.chatUrl}</small>}
              {providerCheck.hint && <em>{t(providerCheck.hint)}</em>}
              {providerCheck.warnings.map(warning => (
                <em key={warning}>{t(warning)}</em>
              ))}
              {providerCheck.conformance && (
                <small>
                  Conformance: {providerCheck.conformance.multiRoundContinuation || 'unknown'} · reasoning{' '}
                  {providerCheck.conformance.requiresReasoningPassback === null
                    ? 'unknown'
                    : providerCheck.conformance.requiresReasoningPassback
                      ? 'passback'
                      : 'not required'}
                </small>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <SlidersHorizontal size={16} className="section-icon" />
          <h3>{t('模型选择')}</h3>
        </div>
        <label>
          <span>{tr(language, 'model')}</span>
          <input
            className="settings-model-input"
            value={settings.model}
            spellCheck={false}
            onChange={event => onSettingsChange({ ...settings, model: event.target.value })}
          />
        </label>
        <div className="provider-catalog-panel" data-status={modelCatalog?.status || 'idle'}>
          <div className="settings-action-row">
            <button type="button" disabled={loadingModels} onClick={() => void onRefreshModelCatalog()}>
              <RefreshCw size={14} />
              {loadingModels ? t('读取中...') : t('刷新模型目录')}
            </button>
            {modelCatalog?.modelsUrl && <code>{modelCatalog.modelsUrl}</code>}
          </div>
          {modelCatalog && (
            <div className="provider-catalog-result">
              <button
                type="button"
                className="provider-model-disclosure"
                aria-expanded={modelCatalogOpen}
                onClick={() => onModelCatalogOpenChange(value => !value)}
              >
                {modelCatalogOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span>
                  <strong>{t(modelCatalog.message || '模型目录')}</strong>
                  {modelCatalog.hint && <em>{t(modelCatalog.hint)}</em>}
                </span>
                <small>{modelCatalog.models.length > 0 ? `${modelCatalog.models.length} ${t('个模型')}` : modelCatalog.status}</small>
              </button>
              {modelCatalogOpen && modelCatalog.models.length > 0 && (
                <div className="provider-model-list">
                  {modelCatalog.models.map(item => (
                    <button
                      type="button"
                      key={item.id}
                      data-active={settings.model === item.id}
                      data-disabled={!item.chatCapable}
                      onClick={() => {
                        if (!item.chatCapable) return;
                        onSettingsChange({ ...settings, model: item.id });
                      }}
                    >
                      <span>
                        <strong>{item.displayName || item.id}</strong>
                        <em>{item.chatCapable ? `${item.type} · ${formatSettingsTokenCount(item.contextLimit)}` : `${item.type} · ${t('非聊天模型')}`}</em>
                      </span>
                    </button>
                  ))}
                </div>
              )}
              {modelCatalogOpen && modelCatalog.models.length === 0 && (
                <p className="provider-model-empty">{t('这个供应商没有返回可切换的聊天模型，仍可手动填写模型名。')}</p>
              )}
            </div>
          )}
        </div>
        <div className="settings-inline-grid">
          <label>
            <span>{tr(language, 'temperature')}</span>
            <input
              type="number"
              step="0.1"
              value={settings.temperature}
              onChange={event => onSettingsChange({ ...settings, temperature: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>{tr(language, 'maxTokens')}</span>
            <input
              type="number"
              value={settings.maxTokens}
              onChange={event => onSettingsChange({ ...settings, maxTokens: Number(event.target.value) })}
            />
          </label>
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <Cpu size={16} className="section-icon" />
          <h3>{t('模型能力')}</h3>
          {capabilities && (
            <span className="settings-badge" data-variant={tierVariant}>
              Tier {capabilities.tier} · {capabilities.tierLabel}
            </span>
          )}
        </div>
        {capabilities ? (
          <>
            <div className="capability-matrix">
              <span className="cap-label">{t('模型族')}</span>
              <span className="cap-value">{capabilities.family}</span>
              <span className="cap-label">{t('视觉')}</span>
              <span className="cap-value">{capabilities.supportsVision ? t('支持') : t('不支持')}</span>
              <span className="cap-label">{t('工具调用')}</span>
              <span className="cap-value">{capabilities.supportsToolCalling ? t('支持') : t('不支持')}</span>
              <span className="cap-label">{t('结构化输出')}</span>
              <span className="cap-value">{capabilities.supportsStructuredOutput ? t('支持') : t('不支持')}</span>
              <span className="cap-label">{t('可用工具数')}</span>
              <span className="cap-value">{capabilities.toolCount} / {capabilities.totalToolCount}</span>
              <span className="cap-label">{t('上下文窗口')}</span>
              <span className="cap-value">{formatSettingsTokenCount(capabilities.effectiveContext)} tokens</span>
              <span className="cap-label">{t('指令遵循')}</span>
              <span className="cap-value">{capabilities.instructionAdherence}</span>
            </div>
            {!capabilities.supportsVision && (
              <p className="section-desc section-desc-warning">
                {t('当前模型不支持视觉，桌面操控需要切换到 Claude、GPT-4o、Gemini 或视觉模型。')}
              </p>
            )}
            {capabilities.tier >= 3 && (
              <p className="section-desc section-desc-warning">
                {t('基础 tier 已自动裁剪工具集至 ')}{capabilities.toolCount}{t(' 个核心工具。')}
              </p>
            )}
          </>
        ) : (
          <p className="section-desc">{capabilitiesError || t('正在读取模型能力...')}</p>
        )}
      </section>

      <ProviderRegistryManager />
    </div>
  );
});
