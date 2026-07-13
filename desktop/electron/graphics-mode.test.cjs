const assert = require('node:assert/strict')
const test = require('node:test')
const { applyGraphicsMode, normalizeGraphicsMode } = require('./graphics-mode.cjs')

test('graphics mode keeps the verified Windows fallback and hardware elsewhere', () => {
  assert.equal(normalizeGraphicsMode('', 'win32'), 'software')
  assert.equal(normalizeGraphicsMode('auto', 'win32'), 'software')
  assert.equal(normalizeGraphicsMode('', 'darwin'), 'hardware')
  assert.equal(normalizeGraphicsMode('hardware', 'win32'), 'hardware')
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

  assert.equal(applyGraphicsMode(app, '', () => {}, 'win32'), 'software')
  assert.deepEqual(switches, [
    ['use-angle', 'swiftshader'],
    ['enable-unsafe-swiftshader', undefined],
    ['disable-direct-composition', undefined],
    ['no-sandbox', undefined],
    ['disable-gpu-sandbox', undefined]
  ])
})
