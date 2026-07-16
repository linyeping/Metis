import { useEffect, useMemo, useState } from 'react';
import { useTheme } from '../hooks/useTheme';
import type { PetAnimationState, PetConfig } from '../lib/types';
import { useUiStore } from '../store/uiStore';
import { petById } from './catalog';
import { PetSprite } from './PetSprite';

const defaultConfig: PetConfig = {
  enabled: false,
  petId: 'tux',
  size: 'medium',
  alwaysOnTop: true,
  statusDriven: true,
  position: null,
};

const stateLabels: Record<PetAnimationState, { zh: string; en: string }> = {
  idle: { zh: '待命', en: 'Ready' },
  'running-right': { zh: '移动中', en: 'Moving' },
  'running-left': { zh: '移动中', en: 'Moving' },
  waving: { zh: '你好', en: 'Hello' },
  jumping: { zh: '已完成', en: 'Completed' },
  failed: { zh: '任务失败', en: 'Task failed' },
  waiting: { zh: '等待确认', en: 'Needs input' },
  running: { zh: '正在工作', en: 'Working' },
  review: { zh: '正在检查', en: 'Reviewing' },
};

export function PetWindow() {
  useTheme();
  const language = useUiStore(value => value.language);
  const [config, setConfig] = useState<PetConfig>(defaultConfig);
  const [state, setState] = useState<PetAnimationState>('idle');
  const pet = useMemo(() => petById(config.petId), [config.petId]);

  useEffect(() => {
    document.body.classList.add('metis-pet-shell');
    document.title = 'Metis Pet';
    void window.metis.petGetConfig().then(setConfig);
    const unsubscribeConfig = window.metis.onPetConfig(setConfig);
    const unsubscribeState = window.metis.onPetState(setState);
    return () => {
      document.body.classList.remove('metis-pet-shell');
      unsubscribeConfig();
      unsubscribeState();
    };
  }, []);

  return (
    <main className="metis-pet-window" data-pet-size={config.size} data-state={state}>
      <div className="metis-pet-status" role="status">{stateLabels[state][language]}</div>
      <div className="metis-pet-drag-surface" title={`${pet.name} · ${stateLabels[state][language]}`}>
        <PetSprite animate spriteUrl={pet.spriteUrl} state={state} />
      </div>
    </main>
  );
}
