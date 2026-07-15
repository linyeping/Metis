import { useEffect } from 'react';
import { useDesignStore } from '../../store/designStore';
import { useUiStore } from '../../store/uiStore';
import { DesignStudio } from './DesignStudio';

export function DesignSurface() {
  const productSurface = useUiStore(state => state.productSurface);
  const language = useUiStore(state => state.language);
  const initialize = useDesignStore(state => state.initialize);
  const setRuntime = useDesignStore(state => state.setRuntime);
  const setView = useDesignStore(state => state.setView);

  useEffect(() => {
    if (productSurface !== 'design') return;
    void initialize(language);
  }, [initialize, language, productSurface]);

  useEffect(() => {
    if (!window.metis) return undefined;
    const offRuntime = window.metis.onDesignRuntimeState(setRuntime);
    const offView = window.metis.onDesignViewState(setView);
    return () => {
      offRuntime();
      offView();
    };
  }, [setRuntime, setView]);

  useEffect(() => {
    if (productSurface === 'design') return;
    void window.metis?.designViewSetLayout?.({ visible: false });
  }, [productSurface]);

  return (
    <section
      className="design-surface"
      data-active={productSurface === 'design'}
      aria-hidden={productSurface !== 'design'}
    >
      <DesignStudio />
    </section>
  );
}
