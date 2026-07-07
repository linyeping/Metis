import { useEffect, useMemo, useState } from 'react';
import { Check, Cpu } from 'lucide-react';
import { getProviderModels, getSettings, updateSettings } from '../../lib/api';
import type { ProviderModel, RuntimeSettings } from '../../lib/types';
import { useUiStore } from '../../store/uiStore';

interface ModelPickerOverlayProps {
  currentModel: string;
  settingsChanged: () => Promise<void>;
}

export function ModelPickerOverlay({ currentModel, settingsChanged }: ModelPickerOverlayProps) {
  const open = useUiStore(state => state.modelPickerOpen);
  const setOpen = useUiStore(state => state.setModelPickerOpen);
  const language = useUiStore(state => state.language);
  const [settings, setSettings] = useState<RuntimeSettings | null>(null);
  const [models, setModels] = useState<ProviderModel[]>([]);
  const [loading, setLoading] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [savingId, setSavingId] = useState('');

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setCatalogError('');
    void getSettings()
      .then(async nextSettings => {
        setSettings(nextSettings);
        const catalog = await getProviderModels({
          backend: nextSettings.providerId || nextSettings.backend,
          baseUrl: nextSettings.baseUrl,
          model: nextSettings.model,
          apiKey: nextSettings.apiKey,
          remoteOnly: true,
        });
        setModels(catalog.models.filter(model => model.chatCapable));
        setCatalogError(catalog.ok || catalog.models.length > 0 ? '' : catalog.message || catalog.hint);
      })
      .catch(error => {
        setModels([]);
        setCatalogError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open]);

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

  const entries = useMemo(
    () => models.map(model => ({
      id: model.id,
      model: model.id,
      note: providerModelNote(model),
    })),
    [models],
  );

  if (!open) return null;

  const activeModel = settings?.model || currentModel;
  const zh = language === 'zh';

  return (
    <div className="command-layer">
      <section className="model-picker">
        <header>
          <span>
            <Cpu size={18} />
            {zh ? '快速切模型' : 'Switch model'}
          </span>
          <button type="button" onClick={() => setOpen(false)}>
            Esc
          </button>
        </header>
        <div className="model-groups">
          {loading && <p className="model-picker-empty">{zh ? '正在读取当前 API 的模型目录...' : 'Loading models from the current API...'}</p>}
          {!loading && catalogError && <p className="model-picker-empty">{catalogError}</p>}
          {!loading && !catalogError && entries.length === 0 && (
            <p className="model-picker-empty">{zh ? '当前 API 没有返回可切换的聊天模型。' : 'The current API returned no switchable chat models.'}</p>
          )}
          {!loading && entries.length > 0 && (
            <section className="model-group">
              <h3>{settings?.providerId || settings?.backend || (zh ? '当前 API' : 'Current API')}</h3>
              <div>
                {entries.map(preset => {
                  const active = activeModel === preset.model;
                  return (
                    <button
                      key={preset.id}
                      type="button"
                      data-active={active}
                      disabled={Boolean(savingId)}
                      onClick={async () => {
                        setSavingId(preset.id);
                        try {
                          await updateSettings({
                            backend: settings?.providerId || settings?.backend,
                            providerId: settings?.providerId || settings?.backend,
                            baseUrl: settings?.baseUrl,
                            model: preset.model,
                          });
                          setSettings(await getSettings());
                          await settingsChanged();
                          setOpen(false);
                        } finally {
                          setSavingId('');
                        }
                      }}
                    >
                      <span>
                        <strong>{preset.model}</strong>
                        <em>{preset.note}</em>
                      </span>
                      {active && <Check size={16} />}
                      {savingId === preset.id && <small>{zh ? '保存中' : 'Saving'}</small>}
                    </button>
                  );
                })}
              </div>
            </section>
          )}
        </div>
      </section>
    </div>
  );
}

function providerModelNote(model: ProviderModel): string {
  const limit = model.contextLimit;
  const parts: string[] = [];
  if (limit > 0) {
    parts.push(formatContextLimit(limit));
  }
  if (model.type) {
    parts.push(model.type);
  }
  if (parts.length > 0) {
    return parts.join(' · ');
  }
  return model.ownedBy || 'remote';
}

function formatContextLimit(limit: number): string {
  if (limit >= 1_000_000) {
    const value = limit / 1_000_000;
    return `${Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1)}M context`;
  }
  if (limit >= 1_000) {
    return `${Math.round(limit / 1_000)}K context`;
  }
  return `${limit} context`;
}
