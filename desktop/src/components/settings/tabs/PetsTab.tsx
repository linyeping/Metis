import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Download, Eye, FolderInput, FolderOpen, Gauge, PackageOpen, Pin, RefreshCw, Search, Sparkles, Trash2 } from 'lucide-react';
import type {
  CommunityPet,
  Language,
  PetAnimationSpeed,
  PetAnimationState,
  PetConfig,
  PetId,
} from '../../../lib/types';
import { petById, petCatalog } from '../../../pets/catalog';
import { PetSprite } from '../../../pets/PetSprite';
import { useUiStore } from '../../../store/uiStore';

type PetsTabProps = {
  language: Language;
};

const fallbackConfig: PetConfig = {
  enabled: false,
  petId: 'tux',
  size: 'medium',
  sizeScale: 100,
  animationSpeed: 'normal',
  alwaysOnTop: true,
  statusDriven: true,
  position: null,
  customPets: [],
};

const previewStates: Array<{ id: PetAnimationState; zh: string; en: string }> = [
  { id: 'idle', zh: '待命', en: 'Idle' },
  { id: 'running', zh: '工作', en: 'Working' },
  { id: 'waiting', zh: '等待', en: 'Waiting' },
  { id: 'review', zh: '检查', en: 'Review' },
  { id: 'jumping', zh: '完成', en: 'Done' },
  { id: 'failed', zh: '失败', en: 'Failed' },
];

const speedMultipliers: Record<PetAnimationSpeed, number> = {
  slow: 0.5,
  normal: 0.7,
  fast: 1,
};

export function PetsTab({ language }: PetsTabProps) {
  const [config, setConfig] = useState<PetConfig>(fallbackConfig);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [previewState, setPreviewState] = useState<PetAnimationState>('idle');
  const [communityPets, setCommunityPets] = useState<CommunityPet[]>([]);
  const [communityLoading, setCommunityLoading] = useState(true);
  const [communityQuery, setCommunityQuery] = useState('');
  const [installingId, setInstallingId] = useState('');
  const requestConfirm = useUiStore(state => state.requestConfirm);
  const selectedPet = useMemo(
    () => petById(config.petId, config.customPets),
    [config.customPets, config.petId],
  );
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

  const loadCommunity = useCallback(async () => {
    setCommunityLoading(true);
    setMessage('');
    try {
      const result = await window.metis.petCommunityList();
      if (!result.ok) throw new Error(result.error || text('社区宠物暂时无法加载。', 'Community pets are temporarily unavailable.'));
      setCommunityPets(result.pets);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setCommunityLoading(false);
    }
  }, [text]);

  useEffect(() => { void loadCommunity(); }, [loadCommunity]);

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

  const importPetFolder = useCallback(async () => {
    setBusy(true);
    setMessage('');
    try {
      const result = await window.metis.petImportFolder();
      if (!result.ok) throw new Error(result.error || text('无法导入宠物。', 'The pet could not be imported.'));
      if (result.config) setConfig(result.config);
      if (result.warnings?.length) setMessage(result.warnings.join('\n'));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [text]);

  const importPetZip = useCallback(async () => {
    setBusy(true);
    setMessage('');
    try {
      const result = await window.metis.petImportZip();
      if (!result.ok) throw new Error(result.error || text('无法导入宠物 ZIP。', 'The pet ZIP could not be imported.'));
      if (result.config) setConfig(result.config);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [text]);

  const importCodexPets = useCallback(async () => {
    setBusy(true);
    setMessage('');
    try {
      const result = await window.metis.petImportCodex();
      if (!result.ok) throw new Error(result.error || text('没有找到可导入的 Codex 宠物。', 'No Codex pets were found to import.'));
      if (result.config) setConfig(result.config);
      setMessage(text(`已同步 ${result.imported || 0} 个 Codex 宠物。`, `Synced ${result.imported || 0} Codex pets.`));
      if (result.warnings?.length) setMessage(current => `${current}\n${result.warnings!.join('\n')}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [text]);

  const installCommunityPet = useCallback(async (pet: CommunityPet) => {
    setInstallingId(pet.id);
    setMessage('');
    try {
      const result = await window.metis.petCommunityInstall(pet.id);
      if (!result.ok || !result.config) throw new Error(result.error || text('无法安装社区宠物。', 'The community pet could not be installed.'));
      setConfig(result.config);
      setCommunityPets(current => current.map(item => item.id === pet.id ? { ...item, installed: true } : item));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setInstallingId('');
    }
  }, [text]);

  const deletePet = useCallback(async (id: PetId) => {
    const pet = config.customPets.find(item => item.id === id);
    const confirmed = await requestConfirm({
      title: text('删除自定义宠物', 'Delete custom pet'),
      message: text(`“${pet?.name || id}”将从 Metis 宠物库中删除。`, `“${pet?.name || id}” will be removed from the Metis pet library.`),
      confirmLabel: text('删除', 'Delete'),
      tone: 'danger',
      icon: 'trash',
    });
    if (!confirmed) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await window.metis.petDelete(id);
      if (!result.ok || !result.config) throw new Error(result.error || text('无法删除宠物。', 'The pet could not be deleted.'));
      setConfig(result.config);
      if (pet?.sourceCommunityId) {
        setCommunityPets(current => current.map(item => item.id === pet.sourceCommunityId ? { ...item, installed: false } : item));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [config.customPets, requestConfirm, text]);

  const filteredCommunityPets = useMemo(() => {
    const query = communityQuery.trim().toLowerCase();
    const filtered = query
      ? communityPets.filter(pet => `${pet.displayName} ${pet.description} ${pet.tags.join(' ')}`.toLowerCase().includes(query))
      : communityPets;
    return filtered.slice(0, 40);
  }, [communityPets, communityQuery]);

  const updateSizeScale = useCallback((sizeScale: number) => {
    setConfig(current => ({ ...current, sizeScale }));
    void window.metis.petUpdateConfig({ sizeScale }).then(result => {
      if (result.ok && result.config) setConfig(result.config);
    });
  }, []);

  return (
    <div className="settings-card-grid pet-settings-grid">
      <section className="settings-section pet-current-section">
        <div className="pet-current-preview">
          <PetSprite
            animate
            speedMultiplier={speedMultipliers[config.animationSpeed]}
            spriteUrl={selectedPet.spriteUrl}
            state={previewState}
          />
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

      <section className="settings-section pet-preview-section">
        <div className="pet-preview-heading">
          <strong>{text('动画预览', 'Animation preview')}</strong>
          <span>{text('切换状态会同步到已显示的桌面宠物。', 'State changes are mirrored to the visible desktop pet.')}</span>
        </div>
        <div className="pet-state-preview" role="group" aria-label={text('动画状态预览', 'Animation state preview')}>
          {previewStates.map(item => (
            <button type="button" data-active={previewState === item.id} key={item.id} onClick={() => void preview(item.id)}>
              {language === 'zh' ? item.zh : item.en}
            </button>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-header">
          <div>
            <h3>{text('内置宠物', 'Built-in pets')}</h3>
            <p className="section-desc">{text('选择一个随 Metis 安装的宠物，切换后立即应用。', 'Choose a companion bundled with Metis. Changes apply immediately.')}</p>
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
              onClick={() => void update({ petId: pet.id })}
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
            <p className="section-desc">{text('Metis 会把运行、等待确认、检查、完成和失败映射到标准动画行。', 'Metis maps work, approvals, review, completion, and failures to standard animation rows.')}</p>
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
                <small>{text('拖动滑杆连续调整，位置会自动限制在当前显示器工作区内。', 'Use the slider for continuous sizing; position stays inside the current display work area.')}</small>
              </span>
            </span>
            <div className="pet-size-control">
              <input type="range" min="65" max="160" step="1" value={config.sizeScale} aria-label={text('宠物大小', 'Pet size')} onChange={event => updateSizeScale(Number(event.target.value))} />
              <output>{config.sizeScale}%</output>
            </div>
          </div>
          <div className="pet-setting-row pet-setting-row-controls">
            <span>
              <Gauge size={16} />
              <span>
                <strong>{text('动画速度', 'Animation speed')}</strong>
                <small>{text('默认速度已降低，减少桌面干扰。', 'The default is intentionally calmer and less distracting.')}</small>
              </span>
            </span>
            <div className="pet-segmented-control" role="group" aria-label={text('动画速度', 'Animation speed')}>
              {(['slow', 'normal', 'fast'] as PetAnimationSpeed[]).map(speed => (
                <button type="button" data-active={config.animationSpeed === speed} disabled={busy} key={speed} onClick={() => void update({ animationSpeed: speed })}>
                  {speed === 'slow' ? text('慢', 'Slow') : speed === 'normal' ? text('标准', 'Normal') : text('快', 'Fast')}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="settings-section pet-custom-section">
        <div className="settings-section-header pet-custom-header">
          <div>
            <h3>{text('自定义宠物', 'Custom pets')}</h3>
            <p className="section-desc">{text('导入包含 pet.json 和 spritesheet.webp/png 的 Codex 兼容宠物文件夹。', 'Import a Codex-compatible folder containing pet.json and spritesheet.webp/png.')}</p>
          </div>
          <div className="pet-custom-actions">
            <button type="button" disabled={busy} title={text('打开宠物文件夹', 'Open pets folder')} onClick={() => void window.metis.petOpenFolder()}>
              <FolderOpen size={15} />
              {text('打开文件夹', 'Open folder')}
            </button>
            <button type="button" disabled={busy} onClick={() => void importPetZip()}>
              <PackageOpen size={15} />
              {text('导入 ZIP', 'Import ZIP')}
            </button>
            <button type="button" disabled={busy} onClick={() => void importCodexPets()}>
              <Sparkles size={15} />
              {text('导入 Codex', 'Import Codex')}
            </button>
            <button type="button" className="primary" disabled={busy} onClick={() => void importPetFolder()}>
              <FolderInput size={15} />
              {text('导入文件夹', 'Import folder')}
            </button>
          </div>
        </div>
        {config.customPets.length > 0 ? (
          <div className="pet-catalog-grid pet-custom-grid">
            {config.customPets.map(pet => (
              <div className="pet-custom-card" data-active={config.petId === pet.id} key={pet.id}>
                <button type="button" className="pet-custom-select" disabled={busy} onClick={() => void update({ petId: pet.id })}>
                  <PetSprite animate={false} spriteUrl={pet.spriteUrl} />
                  <span>
                    <strong>{pet.name}</strong>
                    <small>{pet.description[language]}</small>
                  </span>
                  {config.petId === pet.id && <Check size={15} aria-hidden="true" />}
                </button>
                <button type="button" className="pet-custom-delete" disabled={busy} aria-label={text(`删除 ${pet.name}`, `Delete ${pet.name}`)} title={text('删除宠物', 'Delete pet')} onClick={() => void deletePet(pet.id)}>
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="pet-custom-empty">{text('尚未导入自定义宠物。', 'No custom pets imported yet.')}</div>
        )}
        {message && <p className="pet-settings-error">{message}</p>}
      </section>

      <section className="settings-section pet-community-section">
        <div className="settings-section-header pet-custom-header">
          <div>
            <h3>{text('社区宠物', 'Community pets')}</h3>
            <p className="section-desc">{text('浏览并安装经过社区审核的 Codex 兼容宠物，安装后可继续使用大小、速度和动作预览。', 'Browse approved Codex-compatible pets. Installed pets retain Metis sizing, speed, and animation previews.')}</p>
          </div>
          <button type="button" className="pet-community-refresh" disabled={communityLoading} title={text('刷新社区', 'Refresh community')} onClick={() => void loadCommunity()}>
            <RefreshCw size={15} className={communityLoading ? 'spin' : ''} />
          </button>
        </div>
        <label className="pet-community-search">
          <Search size={15} />
          <input value={communityQuery} onChange={event => setCommunityQuery(event.target.value)} placeholder={text('搜索宠物名称或标签', 'Search pets or tags')} />
        </label>
        {communityLoading ? (
          <div className="pet-custom-empty">{text('正在加载社区宠物…', 'Loading community pets…')}</div>
        ) : filteredCommunityPets.length > 0 ? (
          <div className="pet-community-list">
            {filteredCommunityPets.map(pet => (
              <article key={pet.id} className="pet-community-row">
                <span className="pet-community-icon"><PackageOpen size={17} /></span>
                <span>
                  <strong>{pet.displayName}</strong>
                  <small>{pet.description || pet.tags.join(' · ') || text('社区宠物', 'Community pet')}</small>
                </span>
                <button type="button" disabled={Boolean(installingId)} data-installed={pet.installed} onClick={() => void installCommunityPet(pet)}>
                  {pet.installed ? <Check size={15} /> : <Download size={15} />}
                  {installingId === pet.id
                    ? text('安装中…', 'Installing…')
                    : pet.installed
                      ? text('重新安装', 'Reinstall')
                      : text('安装', 'Install')}
                </button>
              </article>
            ))}
          </div>
        ) : (
          <div className="pet-custom-empty">{text('没有找到匹配的社区宠物。', 'No matching community pets found.')}</div>
        )}
      </section>
    </div>
  );
}
