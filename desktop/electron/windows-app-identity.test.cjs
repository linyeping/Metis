const assert = require('node:assert/strict')
const path = require('node:path')
const test = require('node:test')

const {
  DEV_APP_USER_MODEL_ID,
  PROD_APP_USER_MODEL_ID,
  appUserModelId,
  cleanupConflictingElectronShortcut,
  conflictingShortcutPath,
  isConflictingElectronShortcut
} = require('./windows-app-identity.cjs')

test('development and packaged builds use separate Windows identities', () => {
  assert.equal(appUserModelId(false), DEV_APP_USER_MODEL_ID)
  assert.equal(appUserModelId(true), PROD_APP_USER_MODEL_ID)
  assert.notEqual(DEV_APP_USER_MODEL_ID, PROD_APP_USER_MODEL_ID)
})

test('only the historical Metis development Electron shortcut is conflicting', () => {
  assert.equal(isConflictingElectronShortcut({
    target: 'D:\\repo\\desktop\\node_modules\\electron\\dist\\electron.exe',
    appUserModelId: PROD_APP_USER_MODEL_ID
  }), true)
  assert.equal(isConflictingElectronShortcut({
    target: 'D:\\Apps\\Electron\\electron.exe',
    appUserModelId: PROD_APP_USER_MODEL_ID
  }), false)
  assert.equal(isConflictingElectronShortcut({
    target: 'D:\\repo\\desktop\\node_modules\\electron\\dist\\electron.exe',
    appUserModelId: DEV_APP_USER_MODEL_ID
  }), false)
  assert.equal(isConflictingElectronShortcut({
    target: 'D:\\repo\\desktop\\node_modules\\electron\\dist\\electron.exe',
    appUserModelId: 'com.some-other.app'
  }), false)
})

test('packaged startup removes only a verified conflicting Electron shortcut', () => {
  const appDataPath = 'C:\\Users\\tester\\AppData\\Roaming'
  const expectedPath = conflictingShortcutPath(appDataPath)
  const deleted = []
  const result = cleanupConflictingElectronShortcut({
    platform: 'win32',
    isPackaged: true,
    appDataPath,
    electronShell: {
      readShortcutLink(shortcutPath) {
        assert.equal(shortcutPath, expectedPath)
        return {
          target: 'D:\\repo\\desktop\\node_modules\\electron\\dist\\electron.exe',
          appUserModelId: PROD_APP_USER_MODEL_ID
        }
      }
    },
    fileSystem: {
      existsSync: shortcutPath => shortcutPath === expectedPath,
      unlinkSync: shortcutPath => deleted.push(shortcutPath)
    }
  })

  assert.deepEqual(deleted, [expectedPath])
  assert.equal(result.removed, true)
  assert.equal(path.win32.basename(result.shortcutPath), 'Electron.lnk')
})

test('development startup and unrelated shortcuts are never removed', () => {
  let deleted = false
  const common = {
    platform: 'win32',
    appDataPath: 'C:\\Users\\tester\\AppData\\Roaming',
    electronShell: {
      readShortcutLink: () => ({
        target: 'D:\\Apps\\AnotherElectronApp\\electron.exe',
        appUserModelId: PROD_APP_USER_MODEL_ID
      })
    },
    fileSystem: {
      existsSync: () => true,
      unlinkSync: () => { deleted = true }
    }
  }

  assert.equal(cleanupConflictingElectronShortcut({ ...common, isPackaged: false }).checked, false)
  assert.equal(cleanupConflictingElectronShortcut({ ...common, isPackaged: true }).reason, 'not-conflicting')
  assert.equal(deleted, false)
})
