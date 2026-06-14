import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { initTakeoverOverlay } from './runtime/takeoverOverlay';
import './index.css';

createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

initTakeoverOverlay();

const startupParams = new URLSearchParams(window.location.search);

if (startupParams.has('metisSmoke')) {
  void import('./runtime/rendererSmoke').then(module => module.runRendererSmoke());
}

if (startupParams.has('metisPerf')) {
  void import('./runtime/rendererPerf').then(module => module.runRendererPerf());
}
