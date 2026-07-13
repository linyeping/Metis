function normalizeGraphicsMode(value, platform = process.platform) {
  const mode = String(value || '').trim().toLowerCase()
  if (mode === 'software' || mode === 'hardware') return mode
  return platform === 'win32' ? 'software' : 'hardware'
}

function applyGraphicsMode(app, value, log = () => {}, platform = process.platform) {
  const mode = normalizeGraphicsMode(value, platform)
  if (mode === 'software') {
    app.commandLine.appendSwitch('use-angle', 'swiftshader')
    app.commandLine.appendSwitch('enable-unsafe-swiftshader')
    app.commandLine.appendSwitch('disable-direct-composition')
    // Required on affected Windows machines where the sandboxed GPU/renderer
    // process exits with 0x80000003 even when ANGLE is using SwiftShader.
    app.commandLine.appendSwitch('no-sandbox')
    app.commandLine.appendSwitch('disable-gpu-sandbox')
    log('[graphics] Windows compatibility rendering enabled')
  } else {
    log('[graphics] hardware acceleration enabled')
  }
  return mode
}

module.exports = {
  applyGraphicsMode,
  normalizeGraphicsMode
}
