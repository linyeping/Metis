import {
  ArrowLeft,
  Boxes,
  FileArchive,
  LayoutTemplate,
  LoaderCircle,
  Monitor,
  Paintbrush,
  Plus,
  Presentation,
  RefreshCw,
  Search,
  Settings2,
  Shapes,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import type { DesignFidelity, DesignProjectKind } from '../../lib/design';
import { useDesignStore } from '../../store/designStore';
import { useUiStore } from '../../store/uiStore';

const projectKinds: Array<{ id: DesignProjectKind; icon: typeof Monitor }> = [
  { id: 'prototype', icon: Monitor },
  { id: 'deck', icon: Presentation },
  { id: 'template', icon: LayoutTemplate },
  { id: 'other', icon: Shapes },
];

const kindLabels: Record<DesignProjectKind, { zh: string; en: string }> = {
  prototype: { zh: '原型', en: 'Prototype' },
  deck: { zh: '演示文稿', en: 'Slide deck' },
  template: { zh: '从模板创建', en: 'From template' },
  other: { zh: '其他设计', en: 'Other' },
};

export function DesignHome() {
  const setProductSurface = useUiStore(state => state.setProductSurface);
  const language = useUiStore(state => state.language);
  const zh = language === 'zh';
  const projects = useDesignStore(state => state.projects);
  const designSystems = useDesignStore(state => state.designSystems);
  const runtime = useDesignStore(state => state.runtime);
  const loadingProjects = useDesignStore(state => state.loadingProjects);
  const creatingProject = useDesignStore(state => state.creatingProject);
  const error = useDesignStore(state => state.error);
  const createProject = useDesignStore(state => state.createProject);
  const openProject = useDesignStore(state => state.openProject);
  const openPage = useDesignStore(state => state.openPage);
  const refreshProjects = useDesignStore(state => state.refreshProjects);
  const initialize = useDesignStore(state => state.initialize);
  const [kind, setKind] = useState<DesignProjectKind>('prototype');
  const [fidelity, setFidelity] = useState<DesignFidelity>('high-fidelity');
  const [name, setName] = useState('');
  const [query, setQuery] = useState('');
  const [designSystemId, setDesignSystemId] = useState('');
  const [projectFilter, setProjectFilter] = useState<'recent' | 'all'>('recent');

  const filteredProjects = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const matches = normalized
      ? projects.filter(project => project.name.toLowerCase().includes(normalized))
      : projects;
    return projectFilter === 'recent' ? matches.slice(0, 12) : matches;
  }, [projectFilter, projects, query]);

  const submit = async () => {
    const projectName = name.trim();
    if (!projectName || creatingProject || runtime.state !== 'ready') return;
    const created = await createProject({ name: projectName, kind, fidelity, designSystemId: designSystemId || null });
    if (created) setName('');
  };

  return (
    <div className="design-home">
      <aside className="design-home-create-pane">
        <header className="design-brand-row">
          <button
            className="design-icon-command"
            type="button"
            title={zh ? '返回工作区' : 'Back to workspace'}
            aria-label={zh ? '返回工作区' : 'Back to workspace'}
            onClick={() => setProductSurface('assistant')}
          >
            <ArrowLeft size={17} />
          </button>
          <div className="design-brand-mark"><Paintbrush size={18} /></div>
          <div>
            <strong>Metis Design</strong>
            <span>{zh ? '设计工作台' : 'Design workspace'}</span>
          </div>
        </header>

        <div className="design-kind-tabs" role="tablist" aria-label={zh ? '项目类型' : 'Project type'}>
          {projectKinds.map(item => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={kind === item.id}
                data-active={kind === item.id}
                onClick={() => item.id === 'template'
                  ? void openPage('/plugins', zh ? '模板与示例' : 'Templates and examples')
                  : setKind(item.id)}
              >
                <Icon size={14} />
                <span>{kindLabels[item.id][language]}</span>
              </button>
            );
          })}
        </div>

        <form
          className="design-create-form"
          onSubmit={event => {
            event.preventDefault();
            void submit();
          }}
        >
          <div className="design-create-heading">
            <span>{zh ? `新建${kindLabels[kind].zh}` : `New ${kindLabels[kind].en.toLowerCase()}`}</span>
            <em data-state={runtime.state}>{runtime.state === 'ready' ? (zh ? '运行时就绪' : 'Runtime ready') : runtime.state}</em>
          </div>
          <label className="design-name-field">
            <span>{zh ? '项目名称' : 'Project name'}</span>
            <input
              autoComplete="off"
              maxLength={120}
              placeholder={zh ? '未命名设计' : 'Untitled design'}
              value={name}
              onChange={event => setName(event.target.value)}
            />
          </label>

          {kind === 'prototype' && (
            <div className="design-fidelity-grid" role="radiogroup" aria-label="设计精度">
              <button type="button" role="radio" aria-checked={fidelity === 'wireframe'} data-active={fidelity === 'wireframe'} onClick={() => setFidelity('wireframe')}>
                <span className="design-fidelity-preview design-fidelity-wireframe" aria-hidden="true">
                  <i /><i /><i /><i />
                </span>
                <strong>{zh ? '线框图' : 'Wireframe'}</strong>
              </button>
              <button type="button" role="radio" aria-checked={fidelity === 'high-fidelity'} data-active={fidelity === 'high-fidelity'} onClick={() => setFidelity('high-fidelity')}>
                <span className="design-fidelity-preview design-fidelity-polished" aria-hidden="true">
                  <i /><i /><i /><i />
                </span>
                <strong>{zh ? '高保真' : 'High fidelity'}</strong>
              </button>
            </div>
          )}

          <label className="design-system-select">
            <span>{zh ? '设计系统' : 'Design system'}</span>
            <select value={designSystemId} onChange={event => setDesignSystemId(event.target.value)}>
              <option value="">{zh ? '不使用' : 'None'}</option>
              {designSystems.map(system => <option key={system.id} value={system.id}>{system.title}</option>)}
            </select>
          </label>

          <button className="design-create-button" type="submit" disabled={!name.trim() || creatingProject || runtime.state !== 'ready'}>
            {creatingProject ? <LoaderCircle className="spin" size={16} /> : <Plus size={16} />}
            <span>{zh ? '创建' : 'Create'}</span>
          </button>
        </form>

        <div className="design-system-entry">
          <div><Boxes size={18} /><strong>{zh ? '设计系统' : 'Design system'}</strong></div>
          <button type="button" onClick={() => void openPage('/design-systems/create', zh ? '创建设计系统' : 'Create design system')}><Settings2 size={15} /><span>{zh ? '新建' : 'Create'}</span></button>
        </div>
      </aside>

      <section className="design-home-library">
        <header className="design-library-header">
          <nav aria-label={zh ? 'Design 导航' : 'Design navigation'}>
            <button type="button" data-active="true">{zh ? '设计项目' : 'Designs'}</button>
            <button type="button" onClick={() => void openPage('/plugins', zh ? '模板与示例' : 'Templates and examples')}>{zh ? '模板与示例' : 'Examples'}</button>
            <button type="button" onClick={() => void openPage('/design-systems', zh ? '设计系统' : 'Design system')}>{zh ? '设计系统' : 'Design system'}</button>
          </nav>
          <div className="design-library-actions">
            <label className="design-search">
              <Search size={16} />
              <input aria-label={zh ? '搜索 Design 项目' : 'Search Design projects'} placeholder={zh ? '搜索项目' : 'Search projects'} value={query} onChange={event => setQuery(event.target.value)} />
            </label>
            <button className="design-icon-command" type="button" title={zh ? '刷新项目' : 'Refresh projects'} aria-label={zh ? '刷新项目' : 'Refresh projects'} onClick={() => void refreshProjects()}>
              <RefreshCw className={loadingProjects ? 'spin' : ''} size={16} />
            </button>
          </div>
        </header>

        <div className="design-library-content">
          <div className="design-project-filters" role="tablist" aria-label={zh ? '项目范围' : 'Project scope'}>
            <button type="button" role="tab" aria-selected={projectFilter === 'recent'} data-active={projectFilter === 'recent'} onClick={() => setProjectFilter('recent')}>{zh ? '最近' : 'Recent'}</button>
            <button type="button" role="tab" aria-selected={projectFilter === 'all'} data-active={projectFilter === 'all'} onClick={() => setProjectFilter('all')}>{zh ? '全部设计' : 'All designs'}</button>
          </div>

          {error && (
            <div className="design-runtime-error" role="alert">
              <div><FileArchive size={18} /><span>{error}</span></div>
              <button type="button" onClick={() => void initialize(language)}>{zh ? '重试' : 'Retry'}</button>
            </div>
          )}

          {!loadingProjects && filteredProjects.length === 0 ? (
            <div className="design-empty-state">
              <Paintbrush size={24} />
              <strong>{query ? (zh ? '没有匹配的项目' : 'No matching projects') : (zh ? '还没有设计项目' : 'No designs yet')}</strong>
              <span>{runtime.state === 'ready' ? (zh ? '从左侧创建第一个项目。' : 'Create the first project from the panel.') : (zh ? '启动 Design 运行时后加载项目。' : 'Start the Design runtime to load projects.')}</span>
            </div>
          ) : (
            <div className="design-project-grid">
              {filteredProjects.map((project, index) => (
                <button key={project.id} className="design-project-card" type="button" onClick={() => void openProject(project.id)}>
                  <span className="design-project-thumbnail" data-tone={index % 4} aria-hidden="true">
                    <i /><i /><i /><i /><i />
                  </span>
                  <span className="design-project-meta">
                    <strong>{project.name}</strong>
                    <em>{kindLabels[project.kind]?.[language] || (zh ? '设计' : 'Design')} · {formatUpdatedAt(project.updatedAt, language)}</em>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function formatUpdatedAt(timestamp: number, language: 'zh' | 'en'): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return language === 'zh' ? '最近更新' : 'Recently updated';
  return new Intl.DateTimeFormat(language === 'zh' ? 'zh-CN' : 'en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}
