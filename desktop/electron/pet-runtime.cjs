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

const DEFAULT_PET_CONFIG = Object.freeze({
  enabled: false,
  petId: 'tux',
  size: 'medium',
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

function normalizePetConfig(value = {}) {
  const source = value && typeof value === 'object' ? value : {}
  const x = finiteCoordinate(source.position?.x)
  const y = finiteCoordinate(source.position?.y)
  return {
    enabled: source.enabled === true,
    petId: PET_IDS.has(source.petId) ? source.petId : DEFAULT_PET_CONFIG.petId,
    size: PET_SIZES.has(source.size) ? source.size : DEFAULT_PET_CONFIG.size,
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

function petWindowSize(size) {
  return PET_WINDOW_SIZES[PET_SIZES.has(size) ? size : DEFAULT_PET_CONFIG.size]
}

module.exports = {
  DEFAULT_PET_CONFIG,
  PET_IDS,
  PET_SIZES,
  PET_STATES,
  PET_WINDOW_SIZES,
  mergePetConfig,
  normalizePetConfig,
  normalizePetState,
  petWindowSize
}
