const PET_STATES = new Set([
  'idle',
  'running-right',
  'running-left',
  'waving',
  'jumping',
  'failed',
  'waiting',
  'running',
  'review'
])

const PET_IDS = new Set(['tux', 'dentist', 'nyako-shigure', 'yorha-sit-2b'])
const PET_SIZES = new Set(['small', 'medium', 'large'])
const PET_ANIMATION_SPEEDS = new Set(['slow', 'normal', 'fast'])
const CUSTOM_PET_ID_PATTERN = /^custom:[a-z0-9][a-z0-9-]{0,63}$/

const DEFAULT_PET_CONFIG = Object.freeze({
  enabled: false,
  petId: 'tux',
  size: 'medium',
  sizeScale: 100,
  animationSpeed: 'normal',
  alwaysOnTop: true,
  statusDriven: true,
  position: null
})

const PET_WINDOW_SIZES = Object.freeze({
  small: Object.freeze({ width: 150, height: 184 }),
  medium: Object.freeze({ width: 190, height: 228 }),
  large: Object.freeze({ width: 240, height: 282 })
})

function finiteCoordinate(value) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.round(value) : null
}

function normalizeSizeScale(value, legacySize = DEFAULT_PET_CONFIG.size) {
  const parsed = Number(value)
  if (Number.isFinite(parsed)) return Math.max(65, Math.min(160, Math.round(parsed)))
  if (legacySize === 'small') return 79
  if (legacySize === 'large') return 126
  return 100
}

function normalizePetConfig(value = {}) {
  const source = value && typeof value === 'object' ? value : {}
  const x = finiteCoordinate(source.position?.x)
  const y = finiteCoordinate(source.position?.y)
  return {
    enabled: source.enabled === true,
    petId: PET_IDS.has(source.petId) || CUSTOM_PET_ID_PATTERN.test(String(source.petId || ''))
      ? source.petId
      : DEFAULT_PET_CONFIG.petId,
    size: PET_SIZES.has(source.size) ? source.size : DEFAULT_PET_CONFIG.size,
    sizeScale: normalizeSizeScale(source.sizeScale, source.size),
    animationSpeed: PET_ANIMATION_SPEEDS.has(source.animationSpeed)
      ? source.animationSpeed
      : DEFAULT_PET_CONFIG.animationSpeed,
    alwaysOnTop: source.alwaysOnTop !== false,
    statusDriven: source.statusDriven !== false,
    position: x === null || y === null ? null : { x, y }
  }
}

function mergePetConfig(current, patch) {
  const base = normalizePetConfig(current)
  const delta = patch && typeof patch === 'object' ? patch : {}
  return normalizePetConfig({
    ...base,
    ...delta,
    position: Object.prototype.hasOwnProperty.call(delta, 'position') ? delta.position : base.position
  })
}

function normalizePetState(value) {
  return PET_STATES.has(value) ? value : 'idle'
}

function petWindowSize(size, sizeScale) {
  if (!Number.isFinite(Number(sizeScale))) return PET_WINDOW_SIZES[PET_SIZES.has(size) ? size : DEFAULT_PET_CONFIG.size]
  const scale = normalizeSizeScale(sizeScale) / 100
  return { width: Math.round(190 * scale), height: Math.round(228 * scale) }
}

module.exports = {
  DEFAULT_PET_CONFIG,
  CUSTOM_PET_ID_PATTERN,
  PET_ANIMATION_SPEEDS,
  PET_IDS,
  PET_SIZES,
  PET_STATES,
  PET_WINDOW_SIZES,
  mergePetConfig,
  normalizePetConfig,
  normalizePetState,
  normalizeSizeScale,
  petWindowSize
}
