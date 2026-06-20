import { useCallback, useEffect, useState } from 'react';
import { Check, Plus, Power, RefreshCw, Server, Trash2 } from 'lucide-react';
import {
  deleteProviderRegistry,
  getProviderRegistry,
  getSettings,
  probeProviderRegistry,
  saveProviderRegistry,
  updateSettings,
} from '../../lib/api';
import type { ProviderRegistryProbeResult } from '../../lib/types';
import type { ProviderRegistryEntry, ProviderRegistryInput } from '../../lib/types';
import { useT } from '../../hooks/useT';

// FABLEADV-15: config-driven provider management. Lets the user add/remove
// custom providers (internal relays, new models) without editing code.
// Self-contained: owns its own fetch/save/delete state.

const EMPTY_FORM = {
  id: '',
  displayName: '',
  baseUrl: '',
  apiKeyEnv: '',
  apiKey: '',
  defaultModel: '',
  models: '',
  supportsVision: false,
  parallelToolCalls: false,
};

export function ProviderRegistryManager() {
  const t = useT();
  const [entries, setEntries] = useState<ProviderRegistryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState('');
  const [activating, setActivating] = useState('');
  const [activeProviderId, setActiveProviderId] = useState('');
  const [form, setForm] = useState({ ...EMPTY_FORM });

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [list, settings] = await Promise.all([getProviderRegistry(), getSettings().catch(() => null)]);
      setEntries(list);
      if (settings) setActiveProviderId(settings.providerId || settings.backend || '');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const onActivate = useCallback(async (entry: ProviderRegistryEntry) => {
    setActivating(entry.providerId);
    setError('');
    try {
      await updateSettings({
        backend: entry.providerId,
        providerId: entry.providerId,
        baseUrl: entry.baseUrl,
        model: entry.defaultModel || entry.fallbackModels[0] || '',
      });
      setActiveProviderId(entry.providerId);
      window.dispatchEvent(new Event('metis:settings-refresh'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setActivating('');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSave = async () => {
    if (!form.id.trim() || !form.baseUrl.trim()) {
      setError(t('供应商 ID 和 Base URL 必填'));
      return;
    }
    setSaving(true);
    setError('');
    const payload: ProviderRegistryInput = {
      id: form.id.trim(),
      display_name: form.displayName.trim() || form.id.trim(),
      backend_type: 'openai',
      base_url: form.baseUrl.trim(),
      api_key_env: form.apiKeyEnv.trim() || undefined,
      default_model: form.defaultModel.trim() || undefined,
      models: form.models
        .split(/[,\s]+/)
        .map(m => m.trim())
        .filter(Boolean),
      supports_vision: form.supportsVision,
      parallel_tool_calls: form.parallelToolCalls,
    };
    try {
      const result = await saveProviderRegistry(payload);
      if (!result.ok) {
        setError(result.error || t('保存失败'));
        return;
      }
      setForm({ ...EMPTY_FORM });
      setShowForm(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (providerId: string) => {
    setError('');
    try {
      const result = await deleteProviderRegistry(providerId);
      if (!result.ok) {
        setError(result.error || t('删除失败'));
        return;
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const applyProbeToForm = (result: ProviderRegistryProbeResult) => {
    setForm(current => ({
      ...current,
      defaultModel: result.models.includes(current.defaultModel) ? current.defaultModel : result.models[0] || current.defaultModel,
      models: result.models.length ? result.models.join(', ') : current.models,
      supportsVision: result.supportsVision,
      parallelToolCalls: result.parallelToolCalls,
    }));
  };

  const onProbeEntry = async (entry: ProviderRegistryEntry) => {
    setError('');
    setProbing(entry.providerId);
    try {
      const result = await probeProviderRegistry(entry.providerId, {
        baseUrl: entry.baseUrl,
        model: entry.defaultModel || entry.fallbackModels[0],
      });
      if (!result.ok) {
        setError(result.error || result.modelsResult?.message || t('探测失败'));
        return;
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setProbing('');
    }
  };

  const onProbeForm = async () => {
    if (!form.id.trim() || !form.baseUrl.trim()) {
      setError(t('供应商 ID 和 Base URL 必填'));
      return;
    }
    setError('');
    setProbing(form.id.trim());
    try {
      const saveResult = await saveProviderRegistry({
        id: form.id.trim(),
        display_name: form.displayName.trim() || form.id.trim(),
        backend_type: 'openai',
        base_url: form.baseUrl.trim(),
        api_key_env: form.apiKeyEnv.trim() || undefined,
        default_model: form.defaultModel.trim() || undefined,
        models: form.models
          .split(/[,\s]+/)
          .map(m => m.trim())
          .filter(Boolean),
        supports_vision: form.supportsVision,
        parallel_tool_calls: form.parallelToolCalls,
      });
      if (!saveResult.ok) {
        setError(saveResult.error || t('保存失败，无法探测'));
        return;
      }
      const result = await probeProviderRegistry(form.id.trim(), {
        baseUrl: form.baseUrl.trim(),
        model: form.defaultModel.trim(),
        apiKey: form.apiKey.trim(),
      });
      if (!result.ok) {
        setError(result.error || result.modelsResult?.message || t('探测失败'));
        return;
      }
      applyProbeToForm(result);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setProbing('');
    }
  };

  return (
    <section className="settings-section provider-registry">
      <div className="settings-section-header">
        <Server size={16} className="section-icon" />
        <h3>{t('供应商管理')}</h3>
      </div>
      <p className="settings-hint">
        {t('添加自定义供应商（内网中转、新模型）无需改代码。密钥只填环境变量名（不存明文）。')}
      </p>

      {error && <p className="settings-error" role="alert">{error}</p>}

      <div className="provider-registry-list">
        {loading && <p className="settings-hint">{t('加载中…')}</p>}
        {!loading && entries.map(entry => (
          <div className="provider-registry-row" key={entry.providerId}>
            <div className="provider-registry-info">
              <span className="provider-registry-name">{entry.displayName}</span>
              <span className="provider-registry-badge" data-source={entry.source}>
                {entry.source === 'builtin' ? t('内置') : entry.source === 'project' ? t('项目') : t('自定义')}
              </span>
              {entry.capabilities.vision && <span className="provider-registry-cap">{t('视觉')}</span>}
              {entry.capabilities.parallelToolCalls && <span className="provider-registry-cap">{t('并行')}</span>}
            </div>
            <code className="provider-registry-url">{entry.baseUrl || entry.backendType}</code>
            {activeProviderId === entry.providerId ? (
              <span className="provider-registry-active" title={t('当前启用')}>
                <Check size={13} />
                {t('已启用')}
              </span>
            ) : (
              <button
                type="button"
                className="provider-registry-activate"
                title={t('启用此供应商')}
                disabled={Boolean(activating)}
                onClick={() => void onActivate(entry)}
              >
                <Power className={activating === entry.providerId ? 'spin' : undefined} size={13} />
                {t('启用')}
              </button>
            )}
            <button
              type="button"
              className="provider-registry-probe"
              title={t('探测模型与能力')}
              disabled={Boolean(probing)}
              onClick={() => void onProbeEntry(entry)}
            >
              <RefreshCw className={probing === entry.providerId ? 'spin' : undefined} size={13} />
            </button>
            {entry.deletable && (
              <button
                type="button"
                className="provider-registry-delete"
                title={t('删除供应商')}
                onClick={() => void onDelete(entry.providerId)}
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
        ))}
      </div>

      {showForm ? (
        <div className="provider-registry-form">
          <label><span>{t('供应商 ID')}</span>
            <input value={form.id} onChange={e => setForm({ ...form, id: e.target.value })} placeholder="my-relay" />
          </label>
          <label><span>{t('显示名')}</span>
            <input value={form.displayName} onChange={e => setForm({ ...form, displayName: e.target.value })} placeholder="my-relay" />
          </label>
          <label><span>Base URL</span>
            <input value={form.baseUrl} onChange={e => setForm({ ...form, baseUrl: e.target.value })} placeholder="https://llm.internal.corp/v1" />
          </label>
          <label><span>{t('API Key 环境变量名')}</span>
            <input value={form.apiKeyEnv} onChange={e => setForm({ ...form, apiKeyEnv: e.target.value })} placeholder="CORP_LLM_KEY" />
          </label>
          <label><span>{t('临时 API Key（仅探测）')}</span>
            <input value={form.apiKey} onChange={e => setForm({ ...form, apiKey: e.target.value })} placeholder="sk-..." type="password" />
          </label>
          <label><span>{t('默认模型')}</span>
            <input value={form.defaultModel} onChange={e => setForm({ ...form, defaultModel: e.target.value })} placeholder="gpt-5.5" />
          </label>
          <label><span>{t('模型列表（逗号分隔）')}</span>
            <input value={form.models} onChange={e => setForm({ ...form, models: e.target.value })} placeholder="gpt-5.5, deepseek-v4-pro" />
          </label>
          <label className="provider-registry-toggle">
            <input type="checkbox" checked={form.supportsVision} onChange={e => setForm({ ...form, supportsVision: e.target.checked })} />
            <span>{t('支持视觉 / computer use')}</span>
          </label>
          <label className="provider-registry-toggle">
            <input type="checkbox" checked={form.parallelToolCalls} onChange={e => setForm({ ...form, parallelToolCalls: e.target.checked })} />
            <span>{t('支持并行工具调用')}</span>
          </label>
          <div className="settings-action-row">
            <button type="button" disabled={saving || Boolean(probing)} onClick={() => void onProbeForm()}>
              <RefreshCw className={probing === form.id.trim() ? 'spin' : undefined} size={13} />
              {probing ? t('探测中…') : t('探测')}
            </button>
            <button type="button" disabled={saving} onClick={() => void onSave()}>
              {saving ? t('保存中…') : t('保存供应商')}
            </button>
            <button type="button" onClick={() => { setShowForm(false); setForm({ ...EMPTY_FORM }); setError(''); }}>
              {t('取消')}
            </button>
          </div>
        </div>
      ) : (
        <div className="settings-action-row">
          <button type="button" onClick={() => setShowForm(true)}>
            <Plus size={13} />
            {t('新增供应商')}
          </button>
        </div>
      )}
    </section>
  );
}
