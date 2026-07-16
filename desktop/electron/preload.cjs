const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('metis', {
  backendPort: () => ipcRenderer.invoke('metis:backend-port'),
  window: action => ipcRenderer.invoke('metis:window', action),
  getWindowCloseBehavior: () => ipcRenderer.invoke('metis:window-close-behavior'),
  setWindowCloseBehavior: behavior => ipcRenderer.invoke('metis:set-window-close-behavior', behavior),
  pickFolder: () => ipcRenderer.invoke('metis:pick-folder'),
  pickPythonExe: () => ipcRenderer.invoke('metis:pick-python-exe'),
  saveFile: payload => ipcRenderer.invoke('metis:save-file', payload),
  saveBinaryFile: payload => ipcRenderer.invoke('metis:save-binary-file', payload),
  openExternal: url => ipcRenderer.invoke('metis:open-external', url),
  openPath: path => ipcRenderer.invoke('metis:open-path', path),
  bootState: () => ipcRenderer.invoke('metis:boot-state'),
  retryBackend: () => ipcRenderer.invoke('metis:retry-backend'),
  openLog: () => ipcRenderer.invoke('metis:open-log'),
  appInfo: () => ipcRenderer.invoke('metis:app-info'),
  petGetConfig: () => ipcRenderer.invoke('metis:pet-config'),
  petUpdateConfig: patch => ipcRenderer.invoke('metis:pet-update-config', patch),
  petShow: () => ipcRenderer.invoke('metis:pet-show'),
  petSetState: state => ipcRenderer.invoke('metis:pet-set-state', state),
  petImport: () => ipcRenderer.invoke('metis:pet-import'),
  petDelete: id => ipcRenderer.invoke('metis:pet-delete', id),
  petOpenFolder: () => ipcRenderer.invoke('metis:pet-open-folder'),
  designRuntimeStatus: () => ipcRenderer.invoke('metis:design-runtime-status'),
  designRuntimeStart: locale => ipcRenderer.invoke('metis:design-runtime-start', locale),
  designProjectsList: () => ipcRenderer.invoke('metis:design-projects-list'),
  designSystemsList: () => ipcRenderer.invoke('metis:design-systems-list'),
  designProjectCreate: payload => ipcRenderer.invoke('metis:design-project-create', payload),
  designViewLoad: projectId => ipcRenderer.invoke('metis:design-view-load', projectId),
  designViewLoadPage: pagePath => ipcRenderer.invoke('metis:design-view-load-page', pagePath),
  designViewSetLayout: payload => ipcRenderer.invoke('metis:design-view-set-layout', payload),
  designViewSetOccluded: value => ipcRenderer.invoke('metis:design-view-set-occluded', value),
  designViewReload: () => ipcRenderer.invoke('metis:design-view-reload'),
  diagnostics: () => ipcRenderer.invoke('metis:diagnostics'),
  setNativeTheme: mode => ipcRenderer.invoke('metis:set-native-theme', mode),
  saveDiagnosticsBundle: () => ipcRenderer.invoke('metis:save-diagnostics-bundle'),
  checkUpdates: () => ipcRenderer.invoke('metis:check-updates'),
  installUpdate: () => ipcRenderer.invoke('metis:install-update'),
  devServerDetect: payload => ipcRenderer.invoke('metis:dev-server-detect', payload),
  devServerStart: payload => ipcRenderer.invoke('metis:dev-server-start', payload),
  devServerStop: payload => ipcRenderer.invoke('metis:dev-server-stop', payload),
  devServerStatus: payload => ipcRenderer.invoke('metis:dev-server-status', payload),
  savePreviewEvidence: payload => ipcRenderer.invoke('metis:save-preview-evidence', payload),
  previewSetLayoutIntent: payload => ipcRenderer.invoke('metis:preview-set-layout-intent', payload),
  previewSetBounds: payload => ipcRenderer.invoke('metis:preview-set-bounds', payload),
  previewSetOccluded: value => ipcRenderer.invoke('metis:preview-set-occluded', value),
  previewLoad: payload => ipcRenderer.invoke('metis:preview-load', payload),
  previewCommand: command => ipcRenderer.invoke('metis:preview-command', command),
  previewSetZoom: zoom => ipcRenderer.invoke('metis:preview-set-zoom', zoom),
  previewCapture: () => ipcRenderer.invoke('metis:preview-capture'),
  previewObserve: payload => ipcRenderer.invoke('metis:preview-observe', payload),
  previewAction: payload => ipcRenderer.invoke('metis:preview-action', payload),
  previewActivity: payload => ipcRenderer.invoke('metis:preview-activity', payload),
  terminalRun: payload => ipcRenderer.invoke('metis:terminal-run', payload),
  terminalCreate: payload => ipcRenderer.invoke('metis:terminal-create', payload),
  terminalInput: (sessionId, data) => ipcRenderer.invoke('metis:terminal-input', sessionId, data),
  terminalResize: (sessionId, cols, rows) => ipcRenderer.invoke('metis:terminal-resize', sessionId, cols, rows),
  terminalKill: sessionId => ipcRenderer.invoke('metis:terminal-kill', sessionId),
  reportSmokeResult: payload => ipcRenderer.invoke('metis:smoke-result', payload),
  reportPerfResult: payload => ipcRenderer.invoke('metis:perf-result', payload),
  overlaySetActive: active => ipcRenderer.invoke('metis:overlay-set-active', active),
  overlayStop: () => ipcRenderer.invoke('metis:overlay-stop'),
  onTakeoverStop: callback => {
    const listener = () => callback()
    ipcRenderer.on('metis:takeover-stop', listener)
    return () => ipcRenderer.removeListener('metis:takeover-stop', listener)
  },
  safeStorageMigrate: () => ipcRenderer.invoke('metis:safe-storage-migrate'),
  safeStorageAvailable: () => ipcRenderer.invoke('metis:safe-storage-available'),
  safeStorageEncrypt: plaintext => ipcRenderer.invoke('metis:safe-storage-encrypt', plaintext),
  safeStorageDecrypt: encrypted => ipcRenderer.invoke('metis:safe-storage-decrypt', encrypted),
  connectorAuthorize: (service, options) => ipcRenderer.invoke('metis:connector-authorize', service, options),
  connectorStatus: () => ipcRenderer.invoke('metis:connector-status'),
  connectorDisconnect: service => ipcRenderer.invoke('metis:connector-disconnect', service),
  extensionSecretsSave: (extensionId, values) => ipcRenderer.invoke('metis:extension-secrets-save', extensionId, values),
  extensionSecretsStatus: extensionId => ipcRenderer.invoke('metis:extension-secrets-status', extensionId),
  extensionSecretsDelete: extensionId => ipcRenderer.invoke('metis:extension-secrets-delete', extensionId),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:backend-exit', listener)
    return () => ipcRenderer.removeListener('metis:backend-exit', listener)
  },
  onBootEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:boot-event', listener)
    return () => ipcRenderer.removeListener('metis:boot-event', listener)
  },
  onDevServerEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:dev-server-event', listener)
    return () => ipcRenderer.removeListener('metis:dev-server-event', listener)
  },
  onTerminalEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:terminal-event', listener)
    return () => ipcRenderer.removeListener('metis:terminal-event', listener)
  },
  onPreviewState: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:preview-state', listener)
    return () => ipcRenderer.removeListener('metis:preview-state', listener)
  },
  onWindowState: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:window-state', listener)
    return () => ipcRenderer.removeListener('metis:window-state', listener)
  },
  onUpdateEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:update-event', listener)
    return () => ipcRenderer.removeListener('metis:update-event', listener)
  },
  onDesignRuntimeState: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:design-runtime-state', listener)
    return () => ipcRenderer.removeListener('metis:design-runtime-state', listener)
  },
  onDesignViewState: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:design-view-state', listener)
    return () => ipcRenderer.removeListener('metis:design-view-state', listener)
  },
  onPetConfig: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:pet-config', listener)
    return () => ipcRenderer.removeListener('metis:pet-config', listener)
  },
  onPetState: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('metis:pet-state', listener)
    return () => ipcRenderer.removeListener('metis:pet-state', listener)
  }
})
