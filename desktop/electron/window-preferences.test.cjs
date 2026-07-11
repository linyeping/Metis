const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const {
  DEFAULT_CLOSE_BEHAVIOR,
  loadWindowPreferences,
  normalizeCloseBehavior,
  saveWindowPreferences,
  windowPreferencesPath
} = require('./window-preferences.cjs')

test('window close behavior defaults to minimize-to-tray', () => {
  assert.equal(DEFAULT_CLOSE_BEHAVIOR, 'tray')
  assert.equal(normalizeCloseBehavior('unknown'), 'tray')
  assert.equal(normalizeCloseBehavior('ask'), 'ask')
  assert.equal(normalizeCloseBehavior('quit'), 'quit')
})

test('window close behavior persists and invalid files fall back safely', t => {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-window-preferences-'))
  t.after(() => fs.rmSync(tempDir, { recursive: true, force: true }))
  const filePath = windowPreferencesPath(tempDir)

  assert.deepEqual(loadWindowPreferences(filePath), { closeBehavior: 'tray' })
  assert.deepEqual(saveWindowPreferences(filePath, { closeBehavior: 'ask' }), { closeBehavior: 'ask' })
  assert.deepEqual(loadWindowPreferences(filePath), { closeBehavior: 'ask' })

  fs.writeFileSync(filePath, '{not-json', 'utf8')
  assert.deepEqual(loadWindowPreferences(filePath), { closeBehavior: 'tray' })
})
