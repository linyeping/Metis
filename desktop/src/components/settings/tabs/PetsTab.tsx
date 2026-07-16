import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Eye, Pin, Sparkles } from 'lucide-react';
import type { Language, PetAnimationState, PetConfig, PetId, PetSize } from '../../../lib/types';
import { petById, petCatalog } from '../../../pets/catalog';
import { PetSprite } from '../../../pets/PetSprite';

type PetsTabProps = {
  language: Language;
};

const fallbackConfig: PetConfig = {
  enabled: false,
  petId: 'tux',
  size: 'medium',
  alwaysOnTop: true,
  statusDriven: true,
  position: null,
};

const previewStates: Array<{ id: PetAnimationState; zh: string; en: string }> = [
  { id: 'idle', zh: '待命', en: 'Idle' },
  { id: 'running', zh: '工作', en: 'Working' },
  { id: 'waiting', zh: '等待', en: 'Waiting' },
  { id: 'review', zh: '检查', en: 'Review' },
  { id: 'jumping', zh: '完成', en: 'Done' },
  { id: 'failed', zh: '失败', en: 'Failed' },
];

export function PetsTab({ language }: PetsTabProps) {
  const [config, setConfig] = useState<PetConfig>(fallbackConfig);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [previewState, setPreviewState] = useState<PetAnimationState>('idle');
  const selectedPet = useMemo(() => petById(config.petId), [config.petId]);
  const text = useCallback((zh: string, en: string) => (language === 'zh' ? zh : en), [language]);

  useEffect(() => {
    let canceled = false;
    void window.metis.petGetConfig().then(value => {
      if (!canceled) setConfig(value);
    });
    const unsubscribe = window.metis.onPetConfig(value => {
      if (!canceled) setConfig(value);
    });
    return () => {
      canceled = true;
      unsubscribe();
    };
  }, []);

  const update = useCallback(async (patch: Partial<PetConfig>) => {
    setBusy(true);
    setMessage('');
    try {
      const result = await window.metis.petUpdateConfig(patch);
      if (!result.ok || !result.config) throw new Error(result.error || 'Pet settings could not be saved.');
      setConfig(result.config);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, []);

  const preview = useCallback(async (state: PetAnimationState) => {
    setPreviewState(state);
    if (config.enabled) await window.metis.petSetState(state);
  }, [config.enabled]);

  return (
    <div className="settings-card-grid pet-settings-grid">
      <section className="settings-section pet-current-section">
        <div className="pet-current-preview">
          <PetSprite animate spriteUrl={selectedPet.spriteUrl} state={previewState} />
        </div>
        <div className="pet-current-copy">
          <span>{text('当前宠物', 'Current pet')}</span>
          <strong>{selectedPet.name}</strong>
          <p>{selectedPet.description[language]}</p>
        </div>
        <button
          type="button"
          className="pet-enable-button"
          data-active={config.enabled}
          disabled={busy}
          onClick={() => void update({ enabled: !config.enabled })}
        >
          <Eye size={15} />
          {config.enabled ? text('隐藏宠物', 'Hide pet') : text('显示宠物', 'Show pet')}
        </button>
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>{text('内置宠物', 'Built-in pets')}</h3>
            <p className="section-desc">{text('选择一个随 Metis 安装的宠物。切换会立即应用，不需要启动 Design。', 'Choose a companion bundled with Metis. Changes apply immediately and do not require Design.')}</p>
          </div>
        </div>
        <div className="pet-catalog-grid">
          {petCatalog.map(pet => (
            <button
              type="button"
              className="pet-catalog-card"
              data-active={config.petId === pet.id}
              disabled={busy}
              key={pet.id}
              onClick={() => void update({ petId: pet.id as PetId })}
            >
              <PetSprite animate={false} spriteUrl={pet.spriteUrl} />
              <span>
                <strong>{pet.name}</strong>
                <small>{pet.description[language]}</small>
              </span>
              {config.petId === pet.id && <Check size={15} aria-hidden="true" />}
            </button>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>{text('状态与行为', 'State and behavior')}</h3>
            <p className="section-desc">{text('Metis 会把运行、等待确认、检查、完成和失败映射到 Codex 标准动画行。', 'Metis maps work, approvals, review, completion, and failures to the Codex animation rows.')}</p>
          </div>
        </div>
        <div className="pet-setting-rows">
          <div className="pet-setting-row">
            <span>
              <Sparkles size={16} />
              <span>
                <strong>{text('跟随任务状态', 'Follow task state')}</strong>
                <small>{text('直接使用 Metis 运行事件，不扫描其他应用的日志。', 'Uses Metis runtime events directly without scanning other app logs.')}</small>
              </span>
            </span>
            <button className="pet-switch" type="button" role="switch" aria-checked={config.statusDriven} data-on={config.statusDriven} disabled={busy} onClick={() => void update({ statusDriven: !config.statusDriven })}>
              <span />
            </button>
          </div>
          <div className="pet-setting-row">
            <span>
              <Pin size={16} />
              <span>
                <strong>{text('保持置顶', 'Always on top')}</strong>
                <small>{text('宠物保持在其他窗口上方，但不会出现在任务栏。', 'Keeps the pet above other windows without adding a taskbar item.')}</small>
              </span>
            </span>
            <button className="pet-switch" type="button" role="switch" aria-checked={config.alwaysOnTop} data-on={config.alwaysOnTop} disabled={busy} onClick={() => void update({ alwaysOnTop: !config.alwaysOnTop })}>
              <span />
            </button>
          </div>
          <div className="pet-setting-row pet-setting-row-controls">
            <span>
              <span>
                <strong>{text('宠物大小', 'Pet size')}</strong>
                <small>{text('位置会自动限制在当前显示器工作区内。', 'Position stays inside the current display work area.')}</small>
              </span>
            </span>
            <div className="pet-segmented-control" role="group" aria-label={text('宠物大小', 'Pet size')}>
              {(['small', 'medium', 'large'] as PetSize[]).map(size => (
                <button type="button" data-active={config.size === size} disabled={busy} key={size} onClick={() => void update({ size })}>
                  {size === 'small' ? text('小', 'S') : size === 'medium' ? text('中', 'M') : text('大', 'L')}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="pet-state-preview" role="group" aria-label={text('动画状态预览', 'Animation state preview')}>
          {previewStates.map(item => (
            <button type="button" data-active={previewState === item.id} key={item.id} onClick={() => void preview(item.id)}>
              {language === 'zh' ? item.zh : item.en}
            </button>
          ))}
        </div>
        {message && <p className="pet-settings-error">{message}</p>}
      </section>
    </div>
  );
}
