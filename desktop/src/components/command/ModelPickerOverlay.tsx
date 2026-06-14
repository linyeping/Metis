import { useEffect, useMemo, useState } from 'react';
import { Check, Cpu } from 'lucide-react';
import { getProviderStatus, getSettings, updateSettings } from '../../lib/api';
import { modelPresets } from '../../lib/commands';
import type { ProviderProfile, RuntimeSettings } from '../../lib/types';
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
  const [providers, setProviders] = useState<ProviderProfile[]>([]);
  const [savingId, setSavingId] = useState('');

  useEffect(() => {
    if (!open) return;
    void Promise.all([getSettings(), getProviderStatus()])
      .then(([nextSettings, status]) => {
        setSettings(nextSettings);
        setProviders(status.providers);
      })
      .catch(() => {
        void getSettings().then(setSettings);
        setProviders([]);
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

  const groups = useMemo(() => {
    if (providers.length > 0) {
      const activeProviderId = settings?.providerId || settings?.backend || '';
      return providers
        .filter(provider => provider.providerId !== 'fake')
        .map(provider => {
          const activeProvider = provider.providerId === activeProviderId;
          const modelIds = Array.from(
            new Set([
              provider.defaultModel,
              ...provider.fallbackModels,
              activeProvider ? settings?.model || '' : '',
            ].filter(Boolean)),
          );
          return {
            provider: provider.displayName,
            providerId: provider.providerId,
            baseUrl: provider.baseUrl || (activeProvider ? settings?.baseUrl || '' : ''),
            models: modelIds.map(model => ({
              id: `${provider.providerId}:${model}`,
              model,
              note: providerModelNote(provider, model),
            })),
          };
        })
        .filter(group => group.models.length > 0);
    }

    const map = new Map<
      string,
      Array<{
        id: string;
        model: string;
        note: string;
        backend: string;
        baseUrl: string;
      }>
    >();
    for (const preset of modelPresets) {
      map.set(preset.provider, [...(map.get(preset.provider) || []), preset]);
    }
    return Array.from(map.entries()).map(([provider, presets]) => ({
      provider,
      providerId: presets[0]?.backend || '',
      baseUrl: presets[0]?.baseUrl || '',
      models: presets,
    }));
  }, [providers, settings]);

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
          {groups.map(group => (
            <section className="model-group" key={group.providerId || group.provider}>
              <h3>{group.provider}</h3>
              <div>
                {group.models.map(preset => {
                  const active = activeModel === preset.model && (settings?.providerId || settings?.backend || group.providerId) === group.providerId;
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
                            backend: group.providerId,
                            providerId: group.providerId,
                            baseUrl: group.baseUrl,
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
          ))}
        </div>
      </section>
    </div>
  );
}

function providerModelNote(provider: ProviderProfile, model: string): string {
  const limit = provider.modelContextWindows[model];
  const parts: string[] = [];
  if (limit > 0) {
    parts.push(formatContextLimit(limit));
  }
  if (provider.capabilities.tools) {
    parts.push('Tools');
  }
  if (provider.capabilities.vision) {
    parts.push('Vision');
  }
  if (parts.length > 0) {
    return parts.join(' · ');
  }
  return provider.openaiCompatible ? 'OpenAI-compatible' : provider.backendType;
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
