const PREVIEW_STATE_SCHEMA = 'metis.preview_state.v1'
const PREVIEW_STATE_VERSION = 1
const PREVIEW_STATES = new Set(['hidden', 'mounting', 'loading', 'ready', 'occluded', 'error'])

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
  const rawBounds = payload && typeof payload.bounds === 'object' ? payload.bounds : payload
  const bounds = normalizePreviewBounds(rawBounds)
  if (!Boolean(payload.visible)) {
    return { visible: false, bounds: null, hiddenBounds: null, reason: 'hidden-intent' }
  }
  if (!isValidPreviewBounds(bounds)) {
    return { visible: false, bounds: null, hiddenBounds: bounds, reason: 'invalid-bounds' }
  }
  return { visible: true, bounds, hiddenBounds: null, key: previewBoundsKey(bounds), reason: 'visible-intent' }
}

function previewLayoutIntent(payload = {}) {
  const base = previewBoundsIntent(payload)
  const reason = String(payload.reason || base.reason || '').trim()
  const tabId = String(payload.tabId || payload.tab_id || '').trim()
  return {
    ...base,
    tabId,
    tab_id: tabId,
    reason: reason || base.reason
  }
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

function normalizePreviewStateName(value, fallback = 'hidden') {
  const state = String(value || '').trim().toLowerCase()
  return PREVIEW_STATES.has(state) ? state : fallback
}

module.exports = {
  PREVIEW_STATE_SCHEMA,
  PREVIEW_STATE_VERSION,
  isValidPreviewBounds,
  normalizePreviewStateName,
  normalizePreviewBounds,
  previewBoundsIntent,
  previewBoundsKey,
  previewLayoutIntent,
  previewOcclusionRestoreIntent
}
