const crypto = require('node:crypto')
const fs = require('node:fs/promises')
const http = require('node:http')
const path = require('node:path')

const CONNECTORS = {
  github: {
    service: 'github',
    displayName: 'GitHub',
    scopes: ['repo', 'read:org'],
    tokenEnv: 'GITHUB_PERSONAL_ACCESS_TOKEN',
  },
  gmail: {
    service: 'gmail',
    displayName: 'Gmail',
    scopes: [
      'https://www.googleapis.com/auth/gmail.readonly',
      'https://www.googleapis.com/auth/gmail.send',
      'https://www.googleapis.com/auth/gmail.labels',
    ],
    tokenEnv: 'GOOGLE_OAUTH_ACCESS_TOKEN',
  },
}

function registerConnectorIpc({ app, ipcMain, safeStorage, shell }) {
  ipcMain.handle('metis:connector-status', async () => connectorStatus({ app, safeStorage }))
  ipcMain.handle('metis:connector-disconnect', async (_event, service) => disconnectConnector({ app, service }))
  ipcMain.handle('metis:connector-authorize', async (_event, service, options = {}) =>
    authorizeConnector({ app, safeStorage, shell, service, options })
  )
}

async function connectorStatus({ app, safeStorage }) {
  const encryptionAvailable = isEncryptionAvailable(safeStorage)
  const services = []
  for (const connector of Object.values(CONNECTORS)) {
    services.push({
      ...connector,
      connected: await hasStoredSecret(app, connector.service),
      encryptionAvailable,
    })
  }
  return { ok: true, encryptionAvailable, services }
}

async function disconnectConnector({ app, service }) {
  const connector = connectorFor(service)
  if (!connector) return { ok: false, error: 'unknown connector' }
  await fs.rm(secretPath(app, connector.service), { force: true })
  return { ok: true, service: connector.service }
}

async function authorizeConnector({ app, safeStorage, shell, service, options }) {
  const connector = connectorFor(service)
  if (!connector) return { ok: false, error: 'unknown connector' }
  if (!isEncryptionAvailable(safeStorage)) {
    return { ok: false, service: connector.service, error: 'safeStorage unavailable' }
  }
  const manualToken = String(options.token || options.personalAccessToken || '').trim()
  if (manualToken) {
    await storeSecret(app, safeStorage, connector.service, manualToken)
    return { ok: true, service: connector.service, method: 'manual_token' }
  }
  if (connector.service === 'github') {
    return authorizeGitHubDeviceFlow({ app, safeStorage, shell, options })
  }
  if (connector.service === 'gmail') {
    return authorizeGmailPkce({ app, safeStorage, shell, options })
  }
  return { ok: false, service: connector.service, error: 'unsupported connector' }
}

async function authorizeGitHubDeviceFlow({ app, safeStorage, shell, options }) {
  const clientId = String(options.clientId || process.env.METIS_GITHUB_CLIENT_ID || process.env.GITHUB_OAUTH_CLIENT_ID || '').trim()
  if (!clientId) {
    return {
      ok: false,
      service: 'github',
      code: 'missing_client_id',
      error: 'GitHub Device Flow needs METIS_GITHUB_CLIENT_ID or GITHUB_OAUTH_CLIENT_ID.',
    }
  }
  const scope = String(options.scope || CONNECTORS.github.scopes.join(' ')).trim()
  const device = await postForm('https://github.com/login/device/code', { client_id: clientId, scope })
  if (!device.device_code || !device.verification_uri) {
    return { ok: false, service: 'github', error: 'GitHub did not return a device code.' }
  }
  await shell.openExternal(device.verification_uri)
  const token = await pollGitHubDeviceToken(clientId, device)
  await storeSecret(app, safeStorage, 'github', token)
  return {
    ok: true,
    service: 'github',
    method: 'device_flow',
    userCode: device.user_code,
    verificationUri: device.verification_uri,
  }
}

async function pollGitHubDeviceToken(clientId, device) {
  const intervalMs = Math.max(1, Number(device.interval || 5)) * 1000
  const expiresAt = Date.now() + Math.max(60, Number(device.expires_in || 900)) * 1000
  while (Date.now() < expiresAt) {
    await delay(intervalMs)
    const response = await postForm('https://github.com/login/oauth/access_token', {
      client_id: clientId,
      device_code: device.device_code,
      grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
    })
    if (response.access_token) return String(response.access_token)
    if (response.error === 'authorization_pending') continue
    if (response.error === 'slow_down') {
      await delay(5000)
      continue
    }
    throw new Error(String(response.error_description || response.error || 'GitHub authorization failed'))
  }
  throw new Error('GitHub authorization timed out')
}

async function authorizeGmailPkce({ app, safeStorage, shell, options }) {
  const clientId = String(options.clientId || process.env.METIS_GOOGLE_CLIENT_ID || process.env.GOOGLE_OAUTH_CLIENT_ID || '').trim()
  if (!clientId) {
    return {
      ok: false,
      service: 'gmail',
      code: 'missing_client_id',
      error: 'Gmail PKCE needs METIS_GOOGLE_CLIENT_ID or GOOGLE_OAUTH_CLIENT_ID. Google sensitive scopes may require test-mode users until verification.',
    }
  }
  const verifier = base64Url(crypto.randomBytes(32))
  const challenge = base64Url(crypto.createHash('sha256').update(verifier).digest())
  const callback = await waitForLoopbackCode()
  const scope = String(options.scope || CONNECTORS.gmail.scopes.join(' ')).trim()
  const authUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth')
  authUrl.searchParams.set('client_id', clientId)
  authUrl.searchParams.set('redirect_uri', callback.redirectUri)
  authUrl.searchParams.set('response_type', 'code')
  authUrl.searchParams.set('scope', scope)
  authUrl.searchParams.set('code_challenge', challenge)
  authUrl.searchParams.set('code_challenge_method', 'S256')
  authUrl.searchParams.set('access_type', 'offline')
  authUrl.searchParams.set('prompt', 'consent')
  await shell.openExternal(authUrl.toString())
  const code = await callback.code
  const token = await postForm('https://oauth2.googleapis.com/token', {
    client_id: clientId,
    code,
    code_verifier: verifier,
    grant_type: 'authorization_code',
    redirect_uri: callback.redirectUri,
  })
  await storeSecret(app, safeStorage, 'gmail', JSON.stringify(token))
  return { ok: true, service: 'gmail', method: 'pkce_loopback', testModeNote: 'Gmail sensitive scopes require Google OAuth test users or app verification.' }
}

async function waitForLoopbackCode() {
  let server
  const codePromise = new Promise((resolve, reject) => {
    server = http.createServer((req, res) => {
      const url = new URL(req.url || '/', 'http://127.0.0.1')
      if (url.pathname !== '/callback') {
        res.writeHead(404)
        res.end('Not found')
        return
      }
      const code = url.searchParams.get('code')
      const error = url.searchParams.get('error')
      res.writeHead(code ? 200 : 400, { 'content-type': 'text/html; charset=utf-8' })
      res.end(code ? '<h1>Metis connector authorized. You can close this tab.</h1>' : '<h1>Metis connector authorization failed.</h1>')
      server.close()
      if (code) resolve(code)
      else reject(new Error(error || 'OAuth callback did not include code'))
    })
    server.listen(0, '127.0.0.1')
    setTimeout(() => {
      try { server.close() } catch {}
      reject(new Error('OAuth loopback timed out'))
    }, 10 * 60 * 1000).unref()
  })
  const portPromise = new Promise(resolve => {
    server.on('listening', () => resolve(server.address().port))
  })
  const port = await portPromise
  return {
    redirectUri: `http://127.0.0.1:${port}/callback`,
    code: portPromise.then(() => codePromise),
  }
}

async function postForm(url, fields) {
  const body = new URLSearchParams()
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) body.set(key, String(value))
  }
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      accept: 'application/json',
      'content-type': 'application/x-www-form-urlencoded',
    },
    body,
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(String(data.error_description || data.error || `HTTP ${response.status}`))
  }
  return data
}

async function storeSecret(app, safeStorage, service, plaintext) {
  const encrypted = safeStorage.encryptString(String(plaintext)).toString('base64')
  const target = secretPath(app, service)
  await fs.mkdir(path.dirname(target), { recursive: true })
  await fs.writeFile(target, encrypted, { encoding: 'utf8', mode: 0o600 })
}

async function hasStoredSecret(app, service) {
  try {
    const stat = await fs.stat(secretPath(app, service))
    return stat.isFile()
  } catch {
    return false
  }
}

function secretPath(app, service) {
  return path.join(app.getPath('userData'), 'connectors', `${connectorFor(service).service}.enc`)
}

function connectorFor(service) {
  return CONNECTORS[String(service || '').trim().toLowerCase()] || null
}

function isEncryptionAvailable(safeStorage) {
  try {
    return safeStorage.isEncryptionAvailable()
  } catch {
    return false
  }
}

function base64Url(buffer) {
  return Buffer.from(buffer).toString('base64').replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_')
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

module.exports = {
  CONNECTORS,
  authorizeConnector,
  connectorStatus,
  disconnectConnector,
  registerConnectorIpc,
}
