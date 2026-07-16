const assert = require('node:assert/strict')
const test = require('node:test')
const {
  applyGraphicsMode,
  createGraphicsFallbackRecord,
  GRAPHICS_FALLBACK_TTL_MS,
  normalizeGraphicsMode,
  resolveGraphicsMode
} = require('./graphics-mode.cjs')

test('graphics mode defaults to hardware and keeps software as an explicit fallback', () => {
  assert.equal(normalizeGraphicsMode('', 'win32'), 'hardware')
  assert.equal(normalizeGraphicsMode('auto', 'win32'), 'hardware')
  assert.equal(normalizeGraphicsMode('', 'darwin'), 'hardware')
  assert.equal(normalizeGraphicsMode('hardware', 'win32'), 'hardware')
  assert.equal(normalizeGraphicsMode('software', 'win32'), 'software')
})

test('Windows compatibility mode applies the switches required by the affected runtime', () => {
  const switches = []
  const app = {
    commandLine: {
      appendSwitch(name, value) {
        switches.push([name, value])
      }
    }
  }

  assert.equal(applyGraphicsMode(app, 'software', () => {}, 'win32'), 'software')
  assert.deepEqual(switches, [
    ['disable-gpu', undefined],
    ['no-sandbox', undefined],
    ['disable-gpu-sandbox', undefined]
  ])
})

test('a current GPU crash record selects the cached Windows fallback', () => {
  const now = 10_000_000
  const record = createGraphicsFallbackRecord('40.9.3', now)
  assert.deepEqual(resolveGraphicsMode('', record, 'win32', '40.9.3', now), {
    mode: 'software',
    source: 'cached-fallback'
  })
  assert.equal(resolveGraphicsMode('hardware', record, 'win32', '40.9.3', now).mode, 'hardware')
  assert.equal(resolveGraphicsMode('', record, 'darwin', '40.9.3', now).mode, 'hardware')
  assert.equal(resolveGraphicsMode('', record, 'win32', '41.0.0', now).mode, 'hardware')
  assert.equal(resolveGraphicsMode('', record, 'win32', '40.9.3', now + GRAPHICS_FALLBACK_TTL_MS + 1).mode, 'hardware')
})
