import { memo, useMemo } from 'react';
import { Cpu, RefreshCw, Server, SlidersHorizontal } from 'lucide-react';
import type {
  Language,
  ModelCapabilities,
  ProviderModelCatalog,
  ProviderValidation,
  RuntimeSettings,
} from '../../../lib/types';
import { tr } from '../../../lib/i18n';
import { formatSettingsTokenCount, stripConfigWhitespace } from '../settingsShared';
import { useT } from '../../../hooks/useT';

interface ModelTabProps {
  apiKey: string;
  capabilities: ModelCapabilities | null;
  capabilitiesError: string;
  checkingProvider: boolean;
  language: Language;
  loadingModels: boolean;
  modelCatalog: ProviderModelCatalog | null;
  onApiKeyChange: (value: string) => void;
  onCheckProvider: (deepProbe?: boolean) => void | Promise<void>;
  onRefreshModelCatalog: () => void | Promise<void>;
  onSettingsChange: (value: RuntimeSettings) => void;
  providerCheck: ProviderValidation | null;
  settings: RuntimeSettings;
}

function modelsEndpointPreview(baseUrl: string): string {
  const base = stripConfigWhitespace(baseUrl).replace(/\/+$/, '');
  if (!base) return '';
  return `${base.replace(/\/(?:chat\/completions|models|usage)$/i, '')}/models`;
}

function endpointSettings(settings: RuntimeSettings, baseUrl: string): RuntimeSettings {
  const cleanedBaseUrl = stripConfigWhitespace(baseUrl);
  const providerId = /api\.openai\.com/i.test(cleanedBaseUrl) ? 'openai' : 'custom-openai';
  return {
    ...settings,
    backend: providerId,
    providerId,
    baseUrl: cleanedBaseUrl,
  };
}

function isLikelyChatModel(modelId: string): boolean {
  const value = modelId.toLowerCase();
  return !/(^gpt-image|image|dall-e|embedding|rerank|moderation|whisper|tts|audio)/.test(value);
}

export const ModelTab = memo(function ModelTab({
  apiKey,
  capabilities,
  capabilitiesError,
  checkingProvider,
  language,
  loadingModels,
  modelCatalog,
  onApiKeyChange,
  onCheckProvider,
  onRefreshModelCatalog,
  onSettingsChange,
  providerCheck,
  settings,
}: ModelTabProps) {
  const t = useT();
  const chatModels = useMemo(
    () => (modelCatalog?.models ?? []).filter(item => item.chatCapable && isLikelyChatModel(item.id)),
    [modelCatalog],
  );
  const hiddenModelCount = Math.max(0, (modelCatalog?.models.length ?? 0) - chatModels.length);
  const selectedModelMissing = Boolean(settings.model && chatModels.length > 0 && !chatModels.some(item => item.id === settings.model));
  const endpointPreview = modelCatalog?.modelsUrl || modelsEndpointPreview(settings.baseUrl);
  const tierVariant = capabilities ? (capabilities.tier <= 1 ? 'success' : capabilities.tier === 2 ? 'warning' : 'danger') : 'neutral';
  const connectionVariant = providerCheck?.ok ? 'success' : providerCheck ? 'warning' : 'neutral';
  const catalogVariant = modelCatalog?.status === 'ok' ? 'success' : modelCatalog?.status === 'error' ? 'danger' : modelCatalog ? 'warning' : 'neutral';
  const serviceName = settings.providerId || settings.backend || t('模型服务');

  return (
    <div className="settings-card-grid model-settings-grid">
      <section className="settings-section model-connection-section">
        <div className="settings-section-header settings-section-header-with-action">
          <span className="settings-section-title">
            <Server size={16} className="section-icon" />
            <h3>{t('连接')}</h3>
          </span>
          <button type="button" className="settings-inline-button" disabled={checkingProvider} onClick={() => void onCheckProvider(true)}>
            {checkingProvider ? t('测试中...') : t('测试连接')}
          </button>
        </div>
        <p className="section-desc">{t('配置 Metis 发送模型请求的服务地址和凭据。')}</p>
        <div className="model-service-strip" data-status={connectionVariant}>
          <Server size={16} />
          <span>
            <strong>{serviceName}</strong>
            <small>{settings.baseUrl || t('未填写 Base URL')}</small>
          </span>
          <em>{providerCheck?.ok ? t('已通过') : providerCheck ? t('需处理') : t('未测试')}</em>
        </div>
        <div className="model-credential-panel">
          <label className="model-field-row">
            <span>
              <strong>Base URL *</strong>
              <small>{t('模型服务的完整接口地址。')}</small>
            </span>
            <input
              className="settings-base-url-input"
              value={settings.baseUrl}
              spellCheck={false}
              onChange={event => onSettingsChange(endpointSettings(settings, event.target.value))}
            />
          </label>
          <label className="model-field-row">
            <span>
              <strong>{tr(language, 'apiKey')} *</strong>
              <small>{t('保存时写入本地配置，粘贴时会自动清理空格。')}</small>
            </span>
            <input
              className="settings-api-key-input"
              value={apiKey}
              placeholder={settings.apiKey || 'sk-...'}
              spellCheck={false}
              onChange={event => onApiKeyChange(stripConfigWhitespace(event.target.value))}
            />
          </label>
        </div>
        {providerCheck && (
          <div className="provider-check-panel" data-ok={providerCheck.ok}>
            <span>
              <strong>{t(providerCheck.title)}</strong>
              <em>{t(providerCheck.message)}</em>
            </span>
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
      </section>

      <section className="settings-section model-discovery-section">
        <div className="settings-section-header settings-section-header-with-action">
          <span className="settings-section-title">
            <SlidersHorizontal size={16} className="section-icon" />
            <h3>{t('模型发现')}</h3>
          </span>
          <button type="button" className="settings-inline-button" disabled={loadingModels} onClick={() => void onRefreshModelCatalog()}>
            <RefreshCw size={14} className={loadingModels ? 'spin-icon' : undefined} />
            {loadingModels ? t('读取中...') : t('读取模型列表')}
          </button>
        </div>
        <p className="section-desc">{t('从当前 API 的 /models 读取可用聊天模型，只显示这组配置实际返回的模型。')}</p>
        <div className="provider-catalog-panel model-discovery-panel" data-status={modelCatalog?.status || 'idle'}>
          <div className="model-discovery-summary" data-status={catalogVariant}>
            <span>
              <strong>{chatModels.length > 0 ? `${chatModels.length} ${t('个聊天模型')}` : t('等待模型发现')}</strong>
              <small>{endpointPreview || t('填写 Base URL 后可读取模型列表')}</small>
            </span>
            <em>{modelCatalog?.status || 'idle'}</em>
          </div>
          {modelCatalog && (
            <div className="provider-catalog-result">
              <span className="provider-model-summary">
                <strong>{t(modelCatalog.message || '模型目录')}</strong>
                {modelCatalog.hint && <em>{t(modelCatalog.hint)}</em>}
                <small>{chatModels.length > 0 ? `${chatModels.length} ${t('个聊天模型')}` : modelCatalog.status}</small>
              </span>
              {hiddenModelCount > 0 && (
                <p className="provider-model-empty">{t('已隐藏非聊天模型 ')}{hiddenModelCount}{t(' 个。')}</p>
              )}
            </div>
          )}
        </div>
        <label className="model-field-row">
          <span>
            <strong>{tr(language, 'model')}</strong>
            <small>{chatModels.length > 0 ? t('从当前 API 探测结果选择。') : t('等待模型发现，必要时可临时手动填写。')}</small>
          </span>
          {chatModels.length > 0 ? (
            <select
              className="settings-model-input"
              value={settings.model}
              onChange={event => onSettingsChange({ ...settings, model: event.target.value })}
            >
              {selectedModelMissing && <option value={settings.model}>{settings.model} · {t('当前')}</option>}
              {chatModels.map(item => (
                <option key={item.id} value={item.id}>
                  {item.displayName || item.id}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="settings-model-input"
              value={settings.model}
              spellCheck={false}
              onChange={event => onSettingsChange({ ...settings, model: event.target.value })}
            />
          )}
        </label>
        {chatModels.length > 0 ? (
          <div className="provider-model-list model-discovered-list">
            {chatModels.slice(0, 8).map(item => (
              <button
                type="button"
                key={item.id}
                data-active={settings.model === item.id}
                onClick={() => onSettingsChange({ ...settings, model: item.id })}
              >
                <span>
                  <strong>{item.displayName || item.id}</strong>
                  <em>{item.id}</em>
                </span>
              </button>
            ))}
            {chatModels.length > 8 && <p className="provider-model-empty">{t('还有 ')}{chatModels.length - 8}{t(' 个模型可在上方下拉选择。')}</p>}
          </div>
        ) : modelCatalog ? (
          <p className="provider-model-empty">{t('当前 API 没有返回可切换的聊天模型，仍可手动填写模型名。')}</p>
        ) : null}
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <SlidersHorizontal size={16} className="section-icon" />
          <h3>{t('推理与输出')}</h3>
        </div>
        <div className="settings-inline-grid model-parameter-grid">
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
          <label>
            <span>{tr(language, 'reasoningEffort')}</span>
            <select
              value={settings.reasoningEffort || 'off'}
              onChange={event => onSettingsChange({ ...settings, reasoningEffort: event.target.value })}
            >
              <option value="off">{language === 'zh' ? '关（更快/更省）' : 'Off (faster/cheaper)'}</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="max">max</option>
            </select>
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
                {t('当前模型不支持视觉，桌面操控需要切换到支持视觉的模型。')}
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
    </div>
  );
});
