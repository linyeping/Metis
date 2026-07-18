const fs = require('node:fs')
const path = require('node:path')

const PROD_APP_USER_MODEL_ID = 'com.metis.app'
const DEV_APP_USER_MODEL_ID = 'com.metis.app.dev'
const CONFLICTING_SHORTCUT_NAME = 'Electron.lnk'

function appUserModelId(isPackaged) {
  return isPackaged ? PROD_APP_USER_MODEL_ID : DEV_APP_USER_MODEL_ID
}

function conflictingShortcutPath(appDataPath) {
  return path.win32.join(
    String(appDataPath || ''),
    'Microsoft',
    'Windows',
    'Start Menu',
    'Programs',
    CONFLICTING_SHORTCUT_NAME
  )
}

function isConflictingElectronShortcut(details = {}) {
  if (details.appUserModelId !== PROD_APP_USER_MODEL_ID) return false
  const target = path.win32.normalize(String(details.target || '')).toLowerCase()
  if (!path.win32.isAbsolute(target)) return false
  return target.endsWith('\\node_modules\\electron\\dist\\electron.exe')
}

function cleanupConflictingElectronShortcut(options = {}) {
  if (options.platform !== 'win32' || options.isPackaged !== true) {
    return { checked: false, removed: false, reason: 'not-packaged-windows' }
  }

  const shortcutPath = conflictingShortcutPath(options.appDataPath)
  const fileSystem = options.fileSystem || fs
  const electronShell = options.electronShell
  if (!fileSystem.existsSync(shortcutPath)) {
    return { checked: true, removed: false, reason: 'not-found', shortcutPath }
  }
  if (!electronShell?.readShortcutLink) {
    return { checked: true, removed: false, reason: 'shortcut-reader-unavailable', shortcutPath }
  }

  let details
  try {
    details = electronShell.readShortcutLink(shortcutPath)
  } catch (error) {
    return {
      checked: true,
      removed: false,
      reason: 'read-failed',
      shortcutPath,
      error: error?.message || String(error)
    }
  }

  if (!isConflictingElectronShortcut(details)) {
    return { checked: true, removed: false, reason: 'not-conflicting', shortcutPath }
  }

  try {
    fileSystem.unlinkSync(shortcutPath)
    return { checked: true, removed: true, reason: 'removed', shortcutPath }
  } catch (error) {
    return {
      checked: true,
      removed: false,
      reason: 'remove-failed',
      shortcutPath,
      error: error?.message || String(error)
    }
  }
}

module.exports = {
  CONFLICTING_SHORTCUT_NAME,
  DEV_APP_USER_MODEL_ID,
  PROD_APP_USER_MODEL_ID,
  appUserModelId,
  cleanupConflictingElectronShortcut,
  conflictingShortcutPath,
  isConflictingElectronShortcut
}
