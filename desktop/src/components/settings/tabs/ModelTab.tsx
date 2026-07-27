import { memo, useEffect, useMemo, useState } from 'react';
import { Cpu, RefreshCw, RotateCcw, Save, Server, SlidersHorizontal } from 'lucide-react';
import { resetModelProfile, saveModelProfile } from '../../../lib/api';
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
  onRefreshCapabilities: () => void | Promise<void>;
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

function catalogStatusText(status: string | undefined, t: (value: string) => string): string {
  if (status === 'ok') return t('已读取');
  if (status === 'error') return t('读取失败');
  if (status === 'unsupported') return t('不支持');
  return t('未读取');
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
  onRefreshCapabilities,
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
  const catalogFailed = modelCatalog?.status === 'error';
  const providerTechnicalDetails = providerCheck
    ? [
        providerCheck.chatUrl,
        ...providerCheck.warnings,
        providerCheck.conformance
          ? `Conformance: ${providerCheck.conformance.multiRoundContinuation || 'unknown'} · reasoning ${
              providerCheck.conformance.requiresReasoningPassback === null
                ? 'unknown'
                : providerCheck.conformance.requiresReasoningPassback
                  ? 'passback'
                  : 'not required'
            }`
          : '',
      ].filter(Boolean)
    : [];
  const serviceName = settings.providerId || settings.backend || t('模型服务');
  const [profileDraft, setProfileDraft] = useState({
    contextWindow: 128_000,
    maxOutputTokens: 32_768,
    stage1: 60,
    stage2: 80,
    stage3: 92,
  });
  const [profileBusy, setProfileBusy] = useState('');
  const [profileMessage, setProfileMessage] = useState('');

  useEffect(() => {
    if (!capabilities) return;
    setProfileDraft({
      contextWindow: capabilities.effectiveContext,
      maxOutputTokens: capabilities.maxOutputTokens,
      stage1: Math.round(capabilities.compactThresholds[0] * 100),
      stage2: Math.round(capabilities.compactThresholds[1] * 100),
      stage3: Math.round(capabilities.compactThresholds[2] * 100),
    });
    setProfileMessage('');
  }, [capabilities, settings.model]);

  const persistProfile = async () => {
    if (!settings.model.trim()) return;
    setProfileBusy('save');
    setProfileMessage('');
    try {
      await saveModelProfile({
        model: settings.model,
        contextWindow: profileDraft.contextWindow,
        maxOutputTokens: profileDraft.maxOutputTokens,
        compactThresholds: [profileDraft.stage1 / 100, profileDraft.stage2 / 100, profileDraft.stage3 / 100],
      });
      await onRefreshCapabilities();
      window.dispatchEvent(new CustomEvent('metis:settings-refresh'));
      setProfileMessage(t('已保存到 models.toml，运行时立即生效。'));
    } catch (error) {
      setProfileMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setProfileBusy('');
    }
  };

  const restoreProfile = async () => {
    if (!settings.model.trim()) return;
    setProfileBusy('reset');
    setProfileMessage('');
    try {
      await resetModelProfile(capabilities?.contextSource === 'user' ? capabilities.contextMatchedModel || settings.model : settings.model);
      await onRefreshCapabilities();
      window.dispatchEvent(new CustomEvent('metis:settings-refresh'));
      setProfileMessage(t('已恢复 Metis 内置资料。'));
    } catch (error) {
      setProfileMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setProfileBusy('');
    }
  };

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
            <span className="provider-check-summary">
              <strong>{t(providerCheck.title)}</strong>
              <em>{t(providerCheck.message)}</em>
            </span>
            {providerCheck.hint && <p className="provider-check-hint">{t(providerCheck.hint)}</p>}
            {providerTechnicalDetails.length > 0 && (
              <details className="provider-technical-details">
                <summary>{t('查看技术详情')}</summary>
                <div>
                  {providerTechnicalDetails.map((detail, index) => <code key={`${detail}-${index}`}>{t(detail)}</code>)}
                </div>
              </details>
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
              <strong>
                {chatModels.length > 0
                  ? `${chatModels.length} ${t('个聊天模型')}`
                  : catalogFailed
                    ? t('模型列表读取失败')
                    : t('等待模型发现')}
              </strong>
              <small>{endpointPreview || t('填写 Base URL 后可读取模型列表')}</small>
            </span>
            <em>{catalogStatusText(modelCatalog?.status, t)}</em>
          </div>
          {modelCatalog && catalogFailed && (
            <div className="model-discovery-error" role="alert">
              <p>{modelCatalog.hint ? t(modelCatalog.hint) : t('请检查 Base URL、API Key、代理和模型平台分组。')}</p>
              <details className="provider-technical-details">
                <summary>{t('查看技术详情')}</summary>
                <div>
                  {modelCatalog.message && <code>{t(modelCatalog.message)}</code>}
                  {modelCatalog.modelsUrl && <code>{modelCatalog.modelsUrl}</code>}
                </div>
              </details>
            </div>
          )}
          {modelCatalog && !catalogFailed && (
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
        ) : modelCatalog?.ok ? (
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

      <section className="settings-section model-profile-section">
        <div className="settings-section-header settings-section-header-with-action">
          <span className="settings-section-title">
            <Cpu size={16} className="section-icon" />
            <h3>{t('上下文与压缩')}</h3>
          </span>
          {capabilities && (
            <span className="settings-badge" data-variant={capabilities.contextIsEstimate ? 'warning' : capabilities.contextSource === 'user' ? 'success' : 'neutral'}>
              {capabilities.contextSource === 'user'
                ? t('用户配置')
                : capabilities.contextSource === 'builtin'
                  ? t('内置资料')
                  : capabilities.contextSource === 'builtin_estimate'
                    ? t('家族估算')
                    : t('未确认默认值')}
            </span>
          )}
        </div>
        <p className="section-desc">
          {t('这组值同时控制运行时 token 预算、最大输出和自动压缩触发点；用户配置优先于内置资料。')}
        </p>
        {capabilities?.contextIsEstimate && (
          <p className="section-desc section-desc-warning">
            {t('当前模型没有可确认的精确资料。请按供应商文档填写，不会把自动探测结果冒充准确值。')}
          </p>
        )}
        <div className="settings-inline-grid model-profile-grid">
          <label>
            <span>{t('上下文上限')}</span>
            <input type="number" min={4096} step={1024} value={profileDraft.contextWindow} onChange={event => setProfileDraft(value => ({ ...value, contextWindow: Number(event.target.value) }))} />
          </label>
          <label>
            <span>{t('最大输出 tokens')}</span>
            <input type="number" min={256} step={256} value={profileDraft.maxOutputTokens} onChange={event => setProfileDraft(value => ({ ...value, maxOutputTokens: Number(event.target.value) }))} />
          </label>
        </div>
        <div className="settings-inline-grid model-profile-threshold-grid">
          <label>
            <span>{t('提醒阈值 %')}</span>
            <input type="number" min={10} max={99} value={profileDraft.stage1} onChange={event => setProfileDraft(value => ({ ...value, stage1: Number(event.target.value) }))} />
          </label>
          <label>
            <span>{t('自动压缩 %')}</span>
            <input type="number" min={10} max={99} value={profileDraft.stage2} onChange={event => setProfileDraft(value => ({ ...value, stage2: Number(event.target.value) }))} />
          </label>
          <label>
            <span>{t('紧急阈值 %')}</span>
            <input type="number" min={10} max={99} value={profileDraft.stage3} onChange={event => setProfileDraft(value => ({ ...value, stage3: Number(event.target.value) }))} />
          </label>
        </div>
        <div className="model-profile-actions">
          <button type="button" className="settings-inline-button" disabled={Boolean(profileBusy) || !settings.model.trim()} onClick={() => void persistProfile()}>
            <Save size={14} /> {profileBusy === 'save' ? t('保存中...') : t('保存模型配置')}
          </button>
          <button type="button" className="settings-inline-button" disabled={Boolean(profileBusy) || capabilities?.contextSource !== 'user'} onClick={() => void restoreProfile()}>
            <RotateCcw size={14} /> {profileBusy === 'reset' ? t('恢复中...') : t('恢复内置')}
          </button>
          {capabilities?.contextSourcePath && <code title={capabilities.contextSourcePath}>{capabilities.contextSourcePath}</code>}
        </div>
        {profileMessage && <p className="section-desc">{profileMessage}</p>}
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
              <span className="cap-label">{t('最大输出')}</span>
              <span className="cap-value">{formatSettingsTokenCount(capabilities.maxOutputTokens)} tokens</span>
              <span className="cap-label">{t('压缩阈值')}</span>
              <span className="cap-value">{capabilities.compactThresholds.map(value => `${Math.round(value * 100)}%`).join(' / ')}</span>
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
