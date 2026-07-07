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

  return (
    <div className="settings-card-grid">
      <section className="settings-section">
        <div className="settings-section-header">
          <Server size={16} className="section-icon" />
          <h3>{t('API 连接')}</h3>
        </div>
        <div className="provider-profile-panel" data-mismatch="false">
          <div className="provider-profile-head">
            <span>
              <strong>{t('OpenAI-compatible 中转站')}</strong>
              <em>{t('根据 Base URL 和 API Key 自动读取 /models。')}</em>
            </span>
          </div>
          <div className="provider-profile-grid">
            <span>
              <small>Base URL</small>
              <strong>{settings.baseUrl ? t('已填写') : t('未填写')}</strong>
            </span>
            <span>
              <small>API Key</small>
              <strong>{apiKey || settings.apiKey ? t('已配置') : t('未填写')}</strong>
            </span>
            <span>
              <small>{t('模型来源')}</small>
              <strong>{chatModels.length > 0 ? `${chatModels.length} ${t('个')}` : '/models'}</strong>
            </span>
            <span>
              <small>{t('识别方式')}</small>
              <strong>{t('自动识别')}</strong>
            </span>
          </div>
          {endpointPreview && <code>{endpointPreview}</code>}
        </div>
        <label>
          <span>Base URL</span>
          <input
            className="settings-base-url-input"
            value={settings.baseUrl}
            spellCheck={false}
            onChange={event => onSettingsChange(endpointSettings(settings, event.target.value))}
          />
        </label>
        <label>
          <span>{tr(language, 'apiKey')}</span>
          <input
            className="settings-api-key-input"
            value={apiKey}
            placeholder={settings.apiKey || 'sk-...'}
            spellCheck={false}
            onChange={event => onApiKeyChange(stripConfigWhitespace(event.target.value))}
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
        <div className="provider-catalog-panel" data-status={modelCatalog?.status || 'idle'}>
          <div className="settings-action-row">
            <button type="button" disabled={loadingModels} onClick={() => void onRefreshModelCatalog()}>
              <RefreshCw size={14} />
              {loadingModels ? t('读取中...') : t('读取模型列表')}
            </button>
            {endpointPreview && <code>{endpointPreview}</code>}
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
              {chatModels.length === 0 && (
                <p className="provider-model-empty">{t('当前 API 没有返回可切换的聊天模型，仍可手动填写模型名。')}</p>
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
