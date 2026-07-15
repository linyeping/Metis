import { ArrowLeft, LoaderCircle, Paintbrush, RefreshCw, RotateCcw } from 'lucide-react';
import { useEffect, useMemo, useRef } from 'react';
import { useDesignStore } from '../../store/designStore';
import { useUiStore } from '../../store/uiStore';

export function DesignStudio() {
  const productSurface = useUiStore(state => state.productSurface);
  const language = useUiStore(state => state.language);
  const zh = language === 'zh';
  const activeProjectId = useDesignStore(state => state.activeProjectId);
  const activePagePath = useDesignStore(state => state.activePagePath);
  const studioTitle = useDesignStore(state => state.studioTitle);
  const projects = useDesignStore(state => state.projects);
  const runtime = useDesignStore(state => state.runtime);
  const view = useDesignStore(state => state.view);
  const error = useDesignStore(state => state.error);
  const showHome = useDesignStore(state => state.showHome);
  const openProject = useDesignStore(state => state.openProject);
  const openPage = useDesignStore(state => state.openPage);
  const hostRef = useRef<HTMLDivElement>(null);
  const project = useMemo(() => projects.find(item => item.id === activeProjectId), [activeProjectId, projects]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || productSurface !== 'design' || (!activeProjectId && !activePagePath)) return undefined;
    let frame = 0;
    const sync = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const rect = host.getBoundingClientRect();
        void window.metis?.designViewSetLayout?.({
          visible: true,
          x: rect.left,
          y: rect.top,
          width: rect.width,
          height: rect.height,
        });
      });
    };
    const observer = new ResizeObserver(sync);
    observer.observe(host);
    window.addEventListener('resize', sync);
    sync();
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', sync);
      void window.metis?.designViewSetLayout?.({ visible: false });
    };
  }, [activePagePath, activeProjectId, productSurface]);

  const retry = () => activeProjectId
    ? openProject(activeProjectId)
    : openPage(activePagePath, studioTitle);

  return (
    <div className="design-studio">
      <header className="design-studio-header">
        <div className="design-studio-project">
          <button className="design-icon-command" type="button" title={zh ? '返回设计项目' : 'Back to designs'} aria-label={zh ? '返回设计项目' : 'Back to designs'} onClick={showHome}>
            <ArrowLeft size={17} />
          </button>
          <div className="design-brand-mark"><Paintbrush size={17} /></div>
          <div>
            <strong>{project?.name || studioTitle || 'Design Studio'}</strong>
            <span>{project ? (project.kind === 'deck' ? (zh ? '演示文稿' : 'Slide deck') : project.fidelity === 'wireframe' ? (zh ? '线框图' : 'Wireframe') : (zh ? '高保真' : 'High fidelity')) : (zh ? 'Open Design 功能区' : 'Open Design workspace')}</span>
          </div>
        </div>
        <div className="design-studio-status" data-state={view.state === 'error' ? 'error' : runtime.state}>
          {(runtime.state === 'starting' || view.loading) && <LoaderCircle className="spin" size={14} />}
          <span>{view.state === 'error' ? (zh ? '工作台错误' : 'Studio error') : runtime.state === 'ready' ? 'Open Design 0.15.1' : runtime.state}</span>
        </div>
        <div className="design-studio-actions">
          <button className="design-icon-command" type="button" title={zh ? '重新加载设计工作台' : 'Reload Design Studio'} aria-label={zh ? '重新加载设计工作台' : 'Reload Design Studio'} onClick={() => void window.metis?.designViewReload?.()}>
            <RefreshCw size={16} />
          </button>
          <button className="design-icon-command" type="button" title={zh ? '重新打开当前页面' : 'Reopen current page'} aria-label={zh ? '重新打开当前页面' : 'Reopen current page'} onClick={() => void retry()}>
            <RotateCcw size={16} />
          </button>
        </div>
      </header>
      <div className="design-studio-host" ref={hostRef}>
        <div className="design-studio-placeholder" data-error={Boolean(error || view.error)}>
          {error || view.error ? (
            <>
              <strong>{zh ? 'Design Studio 无法加载' : 'Design Studio could not load'}</strong>
              <span>{error || view.error}</span>
              <button type="button" onClick={() => void retry()}>{zh ? '重试' : 'Retry'}</button>
            </>
          ) : (
            <>
              <LoaderCircle className="spin" size={22} />
              <strong>{zh ? '正在打开 Design Studio' : 'Opening Design Studio'}</strong>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
