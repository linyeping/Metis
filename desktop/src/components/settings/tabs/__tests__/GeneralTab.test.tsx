import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useUiStore } from '../../../../store/uiStore';
import { GeneralTab } from '../GeneralTab';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('GeneralTab window close behavior', () => {
  let container: HTMLDivElement;
  let root: Root;
  const getWindowCloseBehavior = vi.fn(async () => ({ behavior: 'ask' as const }));
  const setWindowCloseBehavior = vi.fn(async (behavior: 'ask' | 'tray' | 'quit') => ({ ok: true, behavior }));

  beforeEach(() => {
    vi.clearAllMocks();
    useUiStore.setState({ language: 'zh' });
    Object.defineProperty(window, 'metis', {
      configurable: true,
      value: { getWindowCloseBehavior, setWindowCloseBehavior } as Partial<Window['metis']>,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('loads the persisted choice and saves select changes immediately', async () => {
    await act(async () => {
      root.render(<GeneralTab />);
      await Promise.resolve();
    });

    const select = container.querySelector<HTMLSelectElement>('select[aria-label="窗口关闭行为"]');
    expect(select?.value).toBe('ask');

    await act(async () => {
      if (!select) return;
      select.value = 'quit';
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(setWindowCloseBehavior).toHaveBeenCalledWith('quit');
    expect(select?.value).toBe('quit');
  });
});
