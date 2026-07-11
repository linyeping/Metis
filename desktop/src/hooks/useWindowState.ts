import { useEffect, useState } from 'react';

interface WindowState {
  isMaximized: boolean;
  isFullScreen: boolean;
}

const initialWindowState: WindowState = {
  isMaximized: false,
  isFullScreen: false,
};

export function useWindowState(): WindowState {
  const [windowState, setWindowState] = useState(initialWindowState);

  useEffect(() => window.metis?.onWindowState?.(setWindowState), []);

  return windowState;
}
