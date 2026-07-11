const fs = require('node:fs')
const path = require('node:path')

const DEFAULT_CLOSE_BEHAVIOR = 'tray'
const CLOSE_BEHAVIORS = new Set(['ask', 'tray', 'quit'])

function normalizeCloseBehavior(value) {
  return CLOSE_BEHAVIORS.has(value) ? value : DEFAULT_CLOSE_BEHAVIOR
}

function windowPreferencesPath(userDataPath) {
  return path.join(userDataPath, 'window-preferences.json')
}

function loadWindowPreferences(filePath) {
  try {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'))
    return { closeBehavior: normalizeCloseBehavior(parsed?.closeBehavior) }
  } catch {
    return { closeBehavior: DEFAULT_CLOSE_BEHAVIOR }
  }
}

function saveWindowPreferences(filePath, preferences = {}) {
  const normalized = {
    closeBehavior: normalizeCloseBehavior(preferences.closeBehavior)
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  fs.writeFileSync(filePath, `${JSON.stringify(normalized, null, 2)}\n`, 'utf8')
  return normalized
}

module.exports = {
  CLOSE_BEHAVIORS,
  DEFAULT_CLOSE_BEHAVIOR,
  loadWindowPreferences,
  normalizeCloseBehavior,
  saveWindowPreferences,
  windowPreferencesPath
}
