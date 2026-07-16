import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { PetStateBridge } from './pets/PetStateBridge';
import { PetWindow } from './pets/PetWindow';
import { initTakeoverOverlay } from './runtime/takeoverOverlay';
import './index.css';

const startupParams = new URLSearchParams(window.location.search);
const isPetWindow = startupParams.has('metisPet');

createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    {isPetWindow ? (
      <PetWindow />
    ) : (
      <>
        <App />
        <PetStateBridge />
      </>
    )}
  </React.StrictMode>,
);

if (!isPetWindow) initTakeoverOverlay();

if (startupParams.has('metisSmoke')) {
  void import('./runtime/rendererSmoke').then(module => module.runRendererSmoke());
}

if (startupParams.has('metisPerf')) {
  void import('./runtime/rendererPerf').then(module => module.runRendererPerf());
}
