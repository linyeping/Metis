function normalizeGraphicsMode(value, platform = process.platform) {
  const mode = String(value || '').trim().toLowerCase()
  if (mode === 'software' || mode === 'hardware') return mode
  return 'hardware'
}

const GRAPHICS_FALLBACK_TTL_MS = 30 * 24 * 60 * 60 * 1000

function resolveGraphicsMode(value, fallbackRecord, platform = process.platform, runtimeVersion = '', now = Date.now()) {
  const requested = String(value || '').trim().toLowerCase()
  if (requested === 'software' || requested === 'hardware') {
    return { mode: requested, source: 'explicit' }
  }
  const updatedAt = Number(fallbackRecord?.updatedAt)
  const currentFallback = platform === 'win32'
    && fallbackRecord?.mode === 'software'
    && String(fallbackRecord?.runtimeVersion || '') === String(runtimeVersion || '')
    && Number.isFinite(updatedAt)
    && updatedAt >= now - GRAPHICS_FALLBACK_TTL_MS
    && updatedAt <= now
  return currentFallback
    ? { mode: 'software', source: 'cached-fallback' }
    : { mode: normalizeGraphicsMode('', platform), source: 'default' }
}

function createGraphicsFallbackRecord(runtimeVersion, now = Date.now()) {
  return {
    mode: 'software',
    reason: 'gpu-process-crashed',
    runtimeVersion: String(runtimeVersion || ''),
    updatedAt: now
  }
}

function applyGraphicsMode(app, value, log = () => {}, platform = process.platform) {
  const mode = normalizeGraphicsMode(value, platform)
  if (mode === 'software') {
    app.commandLine.appendSwitch('use-angle', 'swiftshader')
    app.commandLine.appendSwitch('enable-unsafe-swiftshader')
    // DirectComposition is required for transparent auxiliary windows. Turning
    // it off makes the pet window render as an opaque black rectangle.
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
  createGraphicsFallbackRecord,
  GRAPHICS_FALLBACK_TTL_MS,
  resolveGraphicsMode,
  normalizeGraphicsMode
}
