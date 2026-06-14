const { URL } = require('node:url')

const HARDENED_WEB_PREFERENCES = Object.freeze({
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  allowRunningInsecureContent: false,
  webviewTag: false
})

function parseUrl(value) {
  try {
    return new URL(String(value || ''))
  } catch {
    return null
  }
}

function isSafeExternalUrl(value) {
  const parsed = parseUrl(value)
  if (!parsed) {
    return false
  }
  if (parsed.username || parsed.password) {
    return false
  }
  return (parsed.protocol === 'https:' || parsed.protocol === 'http:') && Boolean(parsed.hostname)
}

function sameOrigin(left, right) {
  const a = parseUrl(left)
  const b = parseUrl(right)
  return Boolean(a && b && a.origin === b.origin)
}

function isAllowedAppNavigation(value, devServerUrl = '') {
  const parsed = parseUrl(value)
  if (!parsed) {
    return false
  }
  if (devServerUrl && sameOrigin(value, devServerUrl)) {
    return true
  }
  return parsed.protocol === 'file:'
}

module.exports = {
  HARDENED_WEB_PREFERENCES,
  isAllowedAppNavigation,
  isSafeExternalUrl
}
