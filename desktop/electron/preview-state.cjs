function normalizePreviewBounds(payload = {}) {
  return {
    x: Math.max(0, Math.round(Number(payload.x) || 0)),
    y: Math.max(0, Math.round(Number(payload.y) || 0)),
    width: Math.max(0, Math.round(Number(payload.width) || 0)),
    height: Math.max(0, Math.round(Number(payload.height) || 0))
  }
}

function previewBoundsKey(bounds) {
  if (!bounds) return ''
  return `${bounds.x},${bounds.y},${bounds.width},${bounds.height}`
}

function isValidPreviewBounds(bounds) {
  return Boolean(bounds && bounds.width > 4 && bounds.height > 4)
}

function previewBoundsIntent(payload = {}) {
  const bounds = normalizePreviewBounds(payload)
  if (!Boolean(payload.visible)) {
    return { visible: false, bounds: null, hiddenBounds: null, reason: 'hidden-intent' }
  }
  if (!isValidPreviewBounds(bounds)) {
    return { visible: false, bounds: null, hiddenBounds: bounds, reason: 'invalid-bounds' }
  }
  return { visible: true, bounds, hiddenBounds: null, key: previewBoundsKey(bounds), reason: 'visible-intent' }
}

function previewOcclusionRestoreIntent(lastPreviewBounds) {
  if (!isValidPreviewBounds(lastPreviewBounds)) {
    return { visible: false, bounds: null, reason: 'no-visible-bounds' }
  }
  return {
    visible: true,
    bounds: lastPreviewBounds,
    key: previewBoundsKey(lastPreviewBounds),
    reason: 'restore-visible-bounds'
  }
}

module.exports = {
  isValidPreviewBounds,
  normalizePreviewBounds,
  previewBoundsIntent,
  previewBoundsKey,
  previewOcclusionRestoreIntent
}
