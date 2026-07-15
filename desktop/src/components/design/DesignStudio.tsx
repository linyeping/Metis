import { LoaderCircle } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { useDesignStore } from '../../store/designStore';
import { useUiStore } from '../../store/uiStore';

export function DesignStudio() {
  const productSurface = useUiStore(state => state.productSurface);
  const language = useUiStore(state => state.language);
  const zh = language === 'zh';
  const activePagePath = useDesignStore(state => state.activePagePath);
  const studioTitle = useDesignStore(state => state.studioTitle);
  const view = useDesignStore(state => state.view);
  const error = useDesignStore(state => state.error);
  const openPage = useDesignStore(state => state.openPage);
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || productSurface !== 'design' || !activePagePath) return undefined;
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
  }, [activePagePath, productSurface]);

  const retry = () => openPage(activePagePath || '/projects', studioTitle || (zh ? '项目' : 'Projects'));

  return (
    <div className="design-studio">
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
