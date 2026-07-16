const assert = require('node:assert/strict')
const test = require('node:test')

const {
  mergePetConfig,
  normalizePetConfig,
  normalizePetState,
  petWindowSize
} = require('./pet-runtime.cjs')

test('pet config rejects unknown ids, sizes, and invalid coordinates', () => {
  assert.deepEqual(normalizePetConfig({
    enabled: true,
    petId: '../../escape',
    size: 'huge',
    alwaysOnTop: false,
    statusDriven: false,
    position: { x: Number.NaN, y: 20 }
  }), {
    enabled: true,
    petId: 'tux',
    size: 'medium',
    animationSpeed: 'normal',
    alwaysOnTop: false,
    statusDriven: false,
    position: null
  })
})

test('pet config patches preserve fields not present in the update', () => {
  assert.deepEqual(
    mergePetConfig(
      { enabled: true, petId: 'dentist', size: 'small', position: { x: 12, y: 30 } },
      { size: 'large' }
    ),
    {
      enabled: true,
      petId: 'dentist',
      size: 'large',
      animationSpeed: 'normal',
      alwaysOnTop: true,
      statusDriven: true,
      position: { x: 12, y: 30 }
    }
  )
})

test('pet config accepts managed custom ids and animation speeds only', () => {
  assert.equal(normalizePetConfig({ petId: 'custom:studio-pet' }).petId, 'custom:studio-pet')
  assert.equal(normalizePetConfig({ petId: 'custom:../escape' }).petId, 'tux')
  assert.equal(normalizePetConfig({ animationSpeed: 'slow' }).animationSpeed, 'slow')
  assert.equal(normalizePetConfig({ animationSpeed: 99 }).animationSpeed, 'normal')
})

test('pet state and window size are normalized', () => {
  assert.equal(normalizePetState('review'), 'review')
  assert.equal(normalizePetState('thinking'), 'idle')
  assert.deepEqual(petWindowSize('small'), { width: 150, height: 184 })
  assert.deepEqual(petWindowSize('unknown'), { width: 190, height: 228 })
})
