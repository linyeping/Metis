const assert = require('node:assert/strict')
const test = require('node:test')
const {
  HARDENED_WEB_PREFERENCES,
  isAllowedAppNavigation,
  isSafeExternalUrl
} = require('./security.cjs')

test('external url guard allows only plain http and https URLs', () => {
  assert.equal(isSafeExternalUrl('https://example.com/docs'), true)
  assert.equal(isSafeExternalUrl('http://example.com/docs'), true)
  assert.equal(isSafeExternalUrl('javascript:alert(1)'), false)
  assert.equal(isSafeExternalUrl('file:///C:/secret.txt'), false)
  assert.equal(isSafeExternalUrl('data:text/html,hi'), false)
  assert.equal(isSafeExternalUrl('https://user:pass@example.com'), false)
})

test('app navigation allows packaged files and same-origin dev server', () => {
  assert.equal(isAllowedAppNavigation('file:///C:/Metis/dist/index.html'), true)
  assert.equal(isAllowedAppNavigation('http://127.0.0.1:5174/path', 'http://127.0.0.1:5174'), true)
  assert.equal(isAllowedAppNavigation('http://127.0.0.1:9999/path', 'http://127.0.0.1:5174'), false)
  assert.equal(isAllowedAppNavigation('https://example.com'), false)
})

test('hardened web preferences keep renderer isolation', () => {
  assert.equal(HARDENED_WEB_PREFERENCES.contextIsolation, true)
  assert.equal(HARDENED_WEB_PREFERENCES.nodeIntegration, false)
  assert.equal(HARDENED_WEB_PREFERENCES.sandbox, true)
  assert.equal(HARDENED_WEB_PREFERENCES.webSecurity, true)
  assert.equal(HARDENED_WEB_PREFERENCES.allowRunningInsecureContent, false)
})
