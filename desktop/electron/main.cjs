const { app, BrowserWindow, dialog, ipcMain, Menu, nativeTheme, safeStorage, screen, shell, Tray, WebContentsView } = require('electron')
const { spawn } = require('node:child_process')
const fsSync = require('node:fs')
const fs = require('node:fs/promises')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { TextDecoder } = require('node:util')
const { pathToFileURL } = require('node:url')
const {
  configFilePath,
  legacyMetisHome,
  resolveDataRootInfo
} = require('./data-root.cjs')
const { getBackendLogPath, startBackend, stopBackend, tailBackendLog } = require('./backend.cjs')
const { registerConnectorIpc } = require('./oauth.cjs')
const {
  HARDENED_WEB_PREFERENCES,
  isAllowedAppNavigation,
  isSafeExternalUrl
} = require('./security.cjs')
const {
  previewBoundsIntent,
  previewOcclusionRestoreIntent
} = require('./preview-state.cjs')

let autoUpdater = null
try {
  autoUpdater = require('electron-updater').autoUpdater
} catch {}

registerConnectorIpc({ app, ipcMain, safeStorage, shell })

// GitHub 项目主页 / 更新检查源（#8）
const GITHUB_OWNER = 'linyeping'
const GITHUB_REPO = 'Metis'
const GITHUB_HOME = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}`

// 简单 semver 比较：a>b 返回 1，a<b 返回 -1，相等 0。
function compareVersions(a, b) {
  const pa = String(a).split('.').map(n => parseInt(n, 10) || 0)
  const pb = String(b).split('.').map(n => parseInt(n, 10) || 0)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] || 0) - (pb[i] || 0)
    if (d !== 0) return d > 0 ? 1 : -1
  }
  return 0
}

let mainWindow = null
let previewView = null
let previewTabId = ''
let lastPreviewBoundsKey = ''
let pendingUpdateInfo = null
let updateCheckTimer = null
let lastPreviewBounds = null
// 原生 WebContentsView 永远盖在 DOM 之上（Electron 无 z-index）。任意 DOM 浮层（设置/命令面板/弹窗）打开时
// 必须把它藏掉，否则就会像截图那样压在弹窗上面。这是 VS Code / 各家稳定方案的核心做法。
let previewOccluded = false
const previewLoadedUrls = new Map()
const previewElementCache = new Map()
let previewBridgeLoopId = 0
let tray = null
let backendPort = null
let bootRunning = false
let bootStatus = 'idle'
let bootError = null
let backendRestartAttempts = 0
let backendRestartTimer = null
const bootEvents = []
const TERMINAL_OUTPUT_LIMIT = 64000
const TERMINAL_TIMEOUT_MS = 120000
const TERMINAL_LIVE_OUTPUT_LIMIT = 120000
const DEV_SERVER_LOG_LIMIT = 80
const DEV_SERVER_HOST = '127.0.0.1'
const PREVIEW_LOCAL_PORT_CANDIDATES = [5173, 5174, 3000, 4200, 8000, 8080]
const PREVIEW_DIAGNOSTIC_LIMIT = 80
const BACKEND_RESTART_LIMIT = 5
let terminalCounter = 1
const terminalSessions = new Map()
const devServers = new Map()
let nodePty = null

const PREVIEW_WEB_PREFERENCES = Object.freeze({
  contextIsolation: true,
  nodeIntegration: false,
  sandbox: true,
  webSecurity: true,
  allowRunningInsecureContent: false
})

const PREVIEW_RISK_PATTERN = /(\blog\s*in\b|\bsign\s*in\b|\bsign\s*up\b|\boauth\b|\bauthori[sz]e\b|\bgrant\b|\ballow\b|\bconsent\b|\bpassword\b|\bpasscode\b|\botp\b|\bsubmit\b|\bsend\b|\bpost\b|\bpublish\b|\bshare\b|\bupload\b|\bdelete\b|\bremove\b|\bpurchase\b|\bbuy\b|\bcheckout\b|\bpay\b|\bpayment\b|\bsubscribe\b|登录|登陆|注册|授权|允许|同意|密码|验证码|提交|发送|发布|分享|上传|删除|移除|购买|支付|付款|结账|订阅|确认)/i
const PREVIEW_AUTH_URL_PATTERN = /(accounts\.google\.com|github\.com\/login|\/oauth|\/authorize|\/auth\/|login|signin|sign-in|signup|sign-up)/i
const PREVIEW_SENSITIVE_INPUT_TYPES = new Set(['password', 'file'])
const PREVIEW_SUBMIT_INPUT_TYPES = new Set(['submit', 'image'])
const PREVIEW_CONSOLE_LEVELS = ['verbose', 'info', 'warning', 'error']

try {
  nodePty = require('node-pty')
} catch {}

app.disableHardwareAcceleration()
app.commandLine.appendSwitch('in-process-gpu')
app.commandLine.appendSwitch('disable-gpu')
app.commandLine.appendSwitch('disable-gpu-compositing')
app.commandLine.appendSwitch('use-gl', 'swiftshader')
app.commandLine.appendSwitch('enable-unsafe-swiftshader')
app.commandLine.appendSwitch('disable-features', 'VizDisplayCompositor')
// P0 修复：本机/目标机用 swiftshader 软件渲染，沙箱开启时渲染器会崩(0x80000003→黑屏)。
// dev 脚本一直带 --no-sandbox 所以 dev 正常；打包版必须同样关闭沙箱，否则双击黑屏。
app.commandLine.appendSwitch('no-sandbox')
app.commandLine.appendSwitch('disable-gpu-sandbox')

const storageInfo = resolveDataRootInfo()
try {
  fsSync.mkdirSync(storageInfo.electronUserData, { recursive: true })
  app.setPath('userData', storageInfo.electronUserData)
} catch (error) {
  process.stderr.write(`[storage] failed to set Electron userData: ${error?.message || error}\n`)
}
if (!process.env.METIS_HOME) process.env.METIS_HOME = storageInfo.metisHome
if (!process.env.METIS_DATA_ROOT) process.env.METIS_DATA_ROOT = storageInfo.dataRoot

const isSmokeMode = process.env.METIS_DESKTOP_SMOKE === '1'
const gotSingleInstanceLock = isSmokeMode || app.requestSingleInstanceLock()

if (!gotSingleInstanceLock) {
  app.quit()
} else if (!isSmokeMode) {
  app.on('second-instance', () => {
    showWindow()
  })
}

function log(message) {
  process.stdout.write(`${String(message).trimEnd()}\n`)
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, Math.max(0, Number(ms) || 0)))
}

function trimUrlInput(value) {
  return String(value || '').trim().replace(/[.,;!?，。；！？]+$/g, '')
}

function isPreviewCurrentAlias(value) {
  const raw = trimUrlInput(value).toLowerCase()
  if (!raw) return true
  return [
    'current',
    'current page',
    'current-page',
    'active',
    'active page',
    '当前',
    '当前页',
    '当前页面',
    '右栏当前页',
    '右栏当前页面',
    'preview current',
    '__current__'
  ].includes(raw)
}

function isLoopbackHostname(hostname) {
  const host = String(hostname || '').toLowerCase()
  return host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0' || host === '[::1]' || host === '::1'
}

function hostForLocalProbe(hostname) {
  const host = String(hostname || '').toLowerCase()
  if (host === '[::1]' || host === '::1') return '::1'
  return DEV_SERVER_HOST
}

function normalizePreviewNavigationInput(value) {
  let raw = trimUrlInput(value)
  if (isPreviewCurrentAlias(raw)) {
    return { ok: true, useCurrent: true, requestedUrl: raw }
  }
  if (/^:\d{2,5}(?:[/?#].*)?$/i.test(raw)) {
    raw = `${DEV_SERVER_HOST}${raw}`
  }
  if (/^(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\]|::1)(?::\d{1,5})?(?:[/?#].*)?$/i.test(raw)) {
    raw = `http://${raw}`
  }
  try {
    const parsed = new URL(raw)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return { ok: false, requestedUrl: raw, error: 'Preview 只允许 http(s) 地址' }
    }
    if (parsed.hostname === '0.0.0.0') {
      parsed.hostname = DEV_SERVER_HOST
    }
    return {
      ok: true,
      url: parsed.toString(),
      parsed,
      requestedUrl: raw,
      local: isLoopbackHostname(parsed.hostname),
      explicitPort: Boolean(parsed.port)
    }
  } catch {
    return { ok: false, requestedUrl: raw, error: 'invalid preview url' }
  }
}

function localPortOpen(hostname, port, timeoutMs = 260) {
  const targetPort = normalizePort(port, 0)
  if (!targetPort) return Promise.resolve(false)
  return new Promise(resolve => {
    const socket = net.createConnection({ host: hostForLocalProbe(hostname), port: targetPort })
    let settled = false
    const done = value => {
      if (settled) return
      settled = true
      try { socket.destroy() } catch {}
      resolve(value)
    }
    socket.once('connect', () => done(true))
    socket.once('error', () => done(false))
    socket.setTimeout(timeoutMs, () => done(false))
  })
}

function localUrlPort(url) {
  try {
    const parsed = new URL(String(url || ''))
    if (!isLoopbackHostname(parsed.hostname)) return 0
    return normalizePort(parsed.port, parsed.protocol === 'https:' ? 443 : 80)
  } catch {
    return 0
  }
}

function mergeLocalPreviewUrl(candidateUrl, requestedUrl) {
  try {
    const candidate = new URL(String(candidateUrl || ''))
    const requested = new URL(String(requestedUrl || candidateUrl || ''))
    if (requested.pathname && requested.pathname !== '/') candidate.pathname = requested.pathname
    if (requested.search) candidate.search = requested.search
    if (requested.hash) candidate.hash = requested.hash
    return candidate.toString()
  } catch {
    return candidateUrl
  }
}

function addPreviewCandidate(candidates, source, url) {
  const normalized = normalizePreviewNavigationInput(url)
  if (!normalized.ok || normalized.useCurrent || !normalized.url || !normalized.local) return
  if (candidates.some(item => item.url === normalized.url)) return
  candidates.push({ source, url: normalized.url, port: localUrlPort(normalized.url), parsed: normalized.parsed })
}

function commonPreviewPortCandidates(protocol = 'http:', skipPort = 0) {
  const candidates = []
  for (const port of PREVIEW_LOCAL_PORT_CANDIDATES) {
    if (port === skipPort) continue
    addPreviewCandidate(candidates, 'common-port-scan', `${protocol}//${DEV_SERVER_HOST}:${port}/`)
  }
  return candidates
}

function activePreviewDevServerCandidates(options = {}) {
  const includeCurrent = options.includeCurrent !== false
  const candidates = []
  addPreviewCandidate(candidates, 'METIS_DESKTOP_DEV_SERVER', process.env.METIS_DESKTOP_DEV_SERVER || '')
  for (const session of devServers.values()) {
    const status = session?.status || {}
    if (!['running', 'starting', 'detected'].includes(String(status.state || ''))) continue
    addPreviewCandidate(candidates, 'dev-server-status', status.url || '')
    if (status.previewPort) {
      addPreviewCandidate(candidates, 'dev-server-preview-port', `http://${DEV_SERVER_HOST}:${status.previewPort}/`)
    }
  }
  if (includeCurrent) {
    addPreviewCandidate(candidates, 'current-preview-url', previewWebContents()?.getURL() || '')
    addPreviewCandidate(candidates, 'current-preview-loaded-url', previewLoadedUrls.get(previewTabId) || '')
  }
  return candidates
}

async function firstReachablePreviewCandidate(candidates, checkedPorts) {
  for (const candidate of candidates) {
    if (!candidate.port) continue
    checkedPorts.push({ source: candidate.source, port: candidate.port, url: candidate.url })
    if (await localPortOpen(candidate.parsed.hostname, candidate.port)) return candidate
  }
  return null
}

async function resolvePreviewNavigationUrl(input) {
  const normalized = normalizePreviewNavigationInput(input)
  const checkedPorts = []
  if (!normalized.ok) {
    return { ok: false, requestedUrl: normalized.requestedUrl || String(input || ''), error: normalized.error || 'invalid preview url' }
  }

  if (normalized.useCurrent) {
    const currentUrl = previewWebContents()?.getURL() || ''
    if (currentUrl && isSafeExternalUrl(currentUrl)) {
      return {
        ok: true,
        url: currentUrl,
        requestedUrl: normalized.requestedUrl || '',
        resolution: { mode: 'current-preview-url', reason: 'Using the current right-rail Preview page.' }
      }
    }
    const fallback = await firstReachablePreviewCandidate(activePreviewDevServerCandidates(), checkedPorts)
    if (fallback) {
      return {
        ok: true,
        url: fallback.url,
        requestedUrl: normalized.requestedUrl || '',
        resolution: {
          mode: 'fallback-current-empty',
          source: fallback.source,
          checkedPorts,
          reason: 'Current Preview page is empty; using the active local dev server.'
        }
      }
    }
    const scanned = await firstReachablePreviewCandidate(commonPreviewPortCandidates('http:', 0), checkedPorts)
    if (scanned) {
      return {
        ok: true,
        url: scanned.url,
        requestedUrl: normalized.requestedUrl || '',
        resolution: {
          mode: 'fallback-current-empty-common-port',
          source: scanned.source,
          checkedPorts,
          reason: `Current Preview page is empty; using scanned local port ${scanned.port}.`
        }
      }
    }
    return {
      ok: false,
      requestedUrl: normalized.requestedUrl || '',
      error: 'current preview page is empty and no local dev server was detected',
      resolution: { mode: 'current-preview-url', checkedPorts }
    }
  }

  if (!normalized.local) {
    return {
      ok: true,
      url: normalized.url,
      requestedUrl: normalized.requestedUrl,
      resolution: { mode: 'external-url', reason: 'External URL is used as requested.' }
    }
  }

  const requested = normalized.parsed
  const requestedPort = localUrlPort(normalized.url)
  if (normalized.explicitPort && requestedPort) {
    checkedPorts.push({ source: 'requested-url', port: requestedPort, url: normalized.url })
    if (await localPortOpen(requested.hostname, requestedPort)) {
      return {
        ok: true,
        url: normalized.url,
        requestedUrl: normalized.requestedUrl,
        resolution: { mode: 'requested-local-port', checkedPorts, reason: `Requested local port ${requestedPort} is reachable.` }
      }
    }
  }

  const candidates = activePreviewDevServerCandidates()
  const fallback = await firstReachablePreviewCandidate(candidates, checkedPorts)
  if (fallback) {
    const resolvedUrl = mergeLocalPreviewUrl(fallback.url, normalized.url)
    return {
      ok: true,
      url: resolvedUrl,
      requestedUrl: normalized.requestedUrl,
      resolution: {
        mode: normalized.explicitPort ? 'fallback-dead-local-port' : 'fallback-missing-local-port',
        source: fallback.source,
        fromPort: requestedPort || 0,
        toPort: fallback.port,
        checkedPorts,
        reason: normalized.explicitPort
          ? `Requested local port ${requestedPort} is not reachable; using ${fallback.source} on port ${fallback.port}.`
          : `No local port was provided; using ${fallback.source} on port ${fallback.port}.`
      }
    }
  }

  const scanned = await firstReachablePreviewCandidate(commonPreviewPortCandidates(requested.protocol, requestedPort), checkedPorts)
  if (scanned) {
    const resolvedUrl = mergeLocalPreviewUrl(scanned.url, normalized.url)
    return {
      ok: true,
      url: resolvedUrl,
      requestedUrl: normalized.requestedUrl,
      resolution: {
        mode: normalized.explicitPort ? 'fallback-dead-local-port' : 'fallback-missing-local-port',
        source: scanned.source,
        fromPort: requestedPort || 0,
        toPort: scanned.port,
        checkedPorts,
        reason: normalized.explicitPort
          ? `Requested local port ${requestedPort} is not reachable; using scanned port ${scanned.port}.`
          : `No local port was provided; using scanned port ${scanned.port}.`
      }
    }
  }

  return {
    ok: true,
    url: normalized.url,
    requestedUrl: normalized.requestedUrl,
    resolution: {
      mode: 'no-local-fallback',
      checkedPorts,
      reason: 'No reachable local fallback port was detected; trying the requested URL.'
    }
  }
}

function openExternalSafe(url) {
  if (!isSafeExternalUrl(url)) {
    log(`[security] denied external url ${String(url || '').slice(0, 120)}`)
    return false
  }
  shell.openExternal(String(url))
  return true
}

function routePreviewWindowOpen(url, source = 'window-open') {
  const value = String(url || '').trim()
  if (!isSafeExternalUrl(value)) {
    log(`[security] denied preview popup url ${value.slice(0, 120)}`)
    emitPreviewState({ error: 'Preview 拦截了不安全的新窗口地址。', loading: false })
    return false
  }
  log(`[preview] ${source} -> ${value}`)
  loadPreviewUrl(value, previewTabId)
  return true
}

function emitPreviewState(patch = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const webContents = previewView?.webContents
  const canRead = webContents && !webContents.isDestroyed()
  const payload = {
    tabId: previewTabId,
    canGoBack: canRead ? webContents.canGoBack() : false,
    canGoForward: canRead ? webContents.canGoForward() : false,
    title: canRead ? webContents.getTitle() : '',
    url: canRead ? webContents.getURL() : '',
    ...patch
  }
  mainWindow.webContents.send('metis:preview-state', payload)
}

function emitUpdateEvent(payload = {}) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  mainWindow.webContents.send('metis:update-event', payload)
}

function hidePreviewView() {
  if (!previewView) return
  lastPreviewBoundsKey = 'hidden'
  try {
    previewView.setVisible?.(false)
  } catch {}
  try {
    previewView.setBounds({ x: 0, y: 0, width: 0, height: 0 })
  } catch {}
}

// DOM 浮层打开/关闭时由渲染端调用：遮挡时藏掉原生视图并忽略后续定位，关闭时按最后已知位置恢复。
function setPreviewOccluded(value) {
  const next = Boolean(value)
  if (next === previewOccluded) return
  previewOccluded = next
  if (!previewView || previewView.webContents.isDestroyed()) return
  if (previewOccluded) {
    hidePreviewView()
  } else {
    const restore = previewOcclusionRestoreIntent(lastPreviewBounds)
    if (restore.visible) {
      lastPreviewBoundsKey = restore.key
      try { previewView.setBounds(restore.bounds) } catch {}
      try { previewView.setVisible?.(true) } catch {}
    } else {
      // 渲染端最新意图是隐藏（遮挡期间关掉了预览）——保持隐藏，别用旧位置把网页恢复出来。
      hidePreviewView()
    }
  }
}

function disposePreviewView() {
  if (!previewView) return
  try {
    mainWindow?.contentView?.removeChildView?.(previewView)
  } catch {}
  try {
    if (!previewView.webContents.isDestroyed()) {
      previewView.webContents.close()
    }
  } catch {}
  previewView = null
  previewTabId = ''
  lastPreviewBoundsKey = ''
  previewLoadedUrls.clear()
}

function ensurePreviewView() {
  if (!mainWindow || mainWindow.isDestroyed()) return null
  if (previewView && !previewView.webContents.isDestroyed()) return previewView
  if (!WebContentsView) return null

  previewView = new WebContentsView({ webPreferences: PREVIEW_WEB_PREFERENCES })
  try { previewView.setBackgroundColor('#ffffff') } catch {}
  mainWindow.contentView.addChildView(previewView)
  hidePreviewView()

  const webContents = previewView.webContents
  webContents.setWindowOpenHandler(details => {
    routePreviewWindowOpen(details.url, 'preview-window-open')
    return { action: 'deny' }
  })
  webContents.on('will-navigate', (event, url) => {
    if (isSafeExternalUrl(url)) return
    event.preventDefault()
    log(`[security] denied preview navigation ${String(url || '').slice(0, 120)}`)
  })
  webContents.on('will-redirect', (event, url) => {
    if (isSafeExternalUrl(url)) return
    event.preventDefault()
    log(`[security] denied preview redirect ${String(url || '').slice(0, 120)}`)
  })
  webContents.on('console-message', recordPreviewConsoleMessage)
  webContents.on('render-process-gone', (_event, details = {}) => {
    addPreviewDiagnostic('page_failures', {
      kind: 'render-process-gone',
      reason: details.reason || '',
      exitCode: details.exitCode ?? null
    })
  })
  webContents.on('unresponsive', () => {
    addPreviewDiagnostic('page_failures', { kind: 'unresponsive' })
  })
  try {
    webContents.session.webRequest.onErrorOccurred({ urls: ['http://*/*', 'https://*/*'] }, details => {
      if (details.webContentsId && details.webContentsId !== webContents.id) return
      const error = String(details.error || '')
      if (error === 'net::ERR_ABORTED') return
      addPreviewDiagnostic('network_failed', {
        method: details.method || '',
        url: compactPreviewText(details.url || '', 1000),
        resourceType: details.resourceType || '',
        error
      })
    })
  } catch {}
  webContents.on('did-start-loading', () => emitPreviewState({ error: '', loading: true }))
  webContents.on('did-stop-loading', () => emitPreviewState({ loading: false }))
  webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (errorCode === -3 || isMainFrame === false) {
      emitPreviewState({ loading: false })
      return
    }
    addPreviewDiagnostic('page_failures', {
      kind: 'did-fail-load',
      errorCode,
      errorDescription: errorDescription || '',
      validatedURL: validatedURL || '',
      isMainFrame: Boolean(isMainFrame)
    })
    emitPreviewState({
      error: errorDescription || '网页加载失败',
      loading: false,
      url: validatedURL || webContents.getURL()
    })
  })
  webContents.on('did-navigate', (_event, url) => emitPreviewState({ error: '', loading: false, url }))
  webContents.on('did-navigate-in-page', (_event, url) => emitPreviewState({ error: '', loading: false, url }))
  webContents.on('did-finish-load', () => {
    void installPreviewPageDiagnosticsHooks(webContents)
  })
  webContents.on('page-title-updated', (_event, title) => emitPreviewState({ title }))

  return previewView
}

async function loadPreviewUrl(url, tabId = '') {
  const requestedValue = trimUrlInput(url)
  const nextTabId = String(tabId || '')
  const resolved = await resolvePreviewNavigationUrl(requestedValue)
  if (!resolved.ok) {
    emitPreviewState({ error: resolved.error || 'Preview URL 解析失败', loading: false, tabId: nextTabId, url: resolved.url || '' })
    recordPreviewAction({
      event: 'navigate',
      action: 'navigate',
      ok: false,
      requestedUrl: requestedValue,
      resolvedUrl: resolved.url || '',
      error: resolved.error || 'Preview URL 解析失败',
      navigation_resolution: resolved.resolution || {}
    })
    return {
      ok: false,
      requestedUrl: requestedValue,
      error: resolved.error || 'Preview URL 解析失败',
      navigation_resolution: resolved.resolution || {},
      browser_activity: previewActivityPayload()
    }
  }
  const value = resolved.url
  if (!isSafeExternalUrl(value)) {
    emitPreviewState({ error: 'Preview 只允许 http(s) 地址', loading: false, tabId: nextTabId })
    recordPreviewAction({
      event: 'navigate',
      action: 'navigate',
      ok: false,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      error: 'unsafe url',
      navigation_resolution: resolved.resolution || {}
    })
    return { ok: false, requestedUrl: requestedValue, resolvedUrl: value, error: 'unsafe url', navigation_resolution: resolved.resolution || {}, browser_activity: previewActivityPayload() }
  }
  const view = ensurePreviewView()
  if (!view) {
    recordPreviewAction({
      event: 'navigate',
      action: 'navigate',
      ok: false,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      error: 'preview view unavailable',
      navigation_resolution: resolved.resolution || {}
    })
    return { ok: false, requestedUrl: requestedValue, resolvedUrl: value, error: 'preview view unavailable', navigation_resolution: resolved.resolution || {}, browser_activity: previewActivityPayload() }
  }
  const currentUrl = view.webContents.getURL()
  if (previewTabId === nextTabId && (currentUrl === value || previewLoadedUrls.get(nextTabId) === value)) {
    emitPreviewState({ error: '', loading: false, tabId: nextTabId, url: value })
    recordPreviewAction({
      event: 'navigate',
      action: 'navigate',
      ok: true,
      skipped: true,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      navigation_resolution: resolved.resolution || {}
    })
    return {
      ...previewStatePayload({ url: value }),
      ok: true,
      skipped: true,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      navigation_resolution: resolved.resolution || {},
      browser_activity: previewActivityPayload()
    }
  }
  previewTabId = nextTabId
  resetPreviewDiagnostics(`navigate:${value}`)
  emitPreviewState({ error: '', loading: true, tabId: previewTabId, url: value })
  try {
    await view.webContents.loadURL(value)
    previewLoadedUrls.set(previewTabId, value)
    emitPreviewState({ error: '', loading: false, tabId: previewTabId, url: view.webContents.getURL() || value })
    recordPreviewAction({
      event: 'navigate',
      action: 'navigate',
      ok: true,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      navigation_resolution: resolved.resolution || {}
    })
    return previewStatePayload({
      ok: true,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      navigation_resolution: resolved.resolution || {},
      browser_activity: previewActivityPayload()
    })
  } catch (error) {
    const message = error?.message || String(error)
    previewLoadedUrls.delete(previewTabId)
    emitPreviewState({ error: message, loading: false, tabId: previewTabId, url: value })
    recordPreviewAction({
      event: 'navigate',
      action: 'navigate',
      ok: false,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      error: message,
      navigation_resolution: resolved.resolution || {}
    })
    return previewStatePayload({
      ok: false,
      requestedUrl: requestedValue,
      resolvedUrl: value,
      url: view.webContents.getURL() || value,
      error: message,
      navigation_resolution: resolved.resolution || {},
      browser_activity: previewActivityPayload()
    })
  }
}

function previewWebContents() {
  const webContents = previewView?.webContents
  if (!webContents || webContents.isDestroyed()) return null
  return webContents
}

function previewStatePayload(extra = {}) {
  const webContents = previewWebContents()
  return {
    ok: Boolean(webContents),
    tabId: previewTabId,
    url: webContents ? webContents.getURL() : '',
    title: webContents ? webContents.getTitle() : '',
    canGoBack: webContents ? webContents.canGoBack() : false,
    canGoForward: webContents ? webContents.canGoForward() : false,
    loading: webContents ? webContents.isLoading() : false,
    ...extra
  }
}

function clampPreviewNumber(value, min, max, fallback = 0) {
  const number = Number(value)
  if (!Number.isFinite(number)) return fallback
  return Math.min(Math.max(number, min), max)
}

function compactPreviewText(value, max = 240) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, max)
}

function previewActionLog() {
  if (!globalThis.__metisPreviewActionLog) {
    globalThis.__metisPreviewActionLog = []
  }
  return globalThis.__metisPreviewActionLog
}

function recordPreviewAction(entry = {}) {
  const logItems = previewActionLog()
  logItems.push({
    at: new Date().toISOString(),
    url: previewWebContents()?.getURL() || '',
    title: previewWebContents()?.getTitle() || '',
    ...entry
  })
  while (logItems.length > 80) logItems.shift()
}

function previewActivityLabel(item = {}) {
  const event = String(item.event || item.action || '').toLowerCase()
  if (event === 'navigate') {
    return item.ok === false
      ? `Navigation failed: ${compactPreviewText(item.resolvedUrl || item.requestedUrl || item.url || '', 120)}`
      : `Navigated: ${compactPreviewText(item.resolvedUrl || item.requestedUrl || item.url || '', 120)}`
  }
  if (event === 'observe') {
    return `Observed ${Number(item.element_count || 0)} elements`
  }
  if (event === 'screenshot') {
    return item.ok === false
      ? `Screenshot failed: ${compactPreviewText(item.error || '', 120)}`
      : `Screenshot captured ${item.width || 0}x${item.height || 0}`
  }
  if (item.blocked || item.requires_confirmation) {
    return `Blocked ${compactPreviewText(item.action || 'action', 40)}: ${compactPreviewText(item.target || item.error || '', 120)}`
  }
  if (event || item.action) {
    return `${compactPreviewText(item.action || event, 40)} ${item.ok === false ? 'failed' : 'completed'}: ${compactPreviewText(item.target || item.error || '', 120)}`
  }
  return compactPreviewText(item.summary || item.error || 'Preview activity', 160)
}

function previewActivityPayload(limit = 24) {
  const items = previewActionLog().slice(-Math.max(1, Math.min(Number(limit) || 24, 80))).map(item => {
    const event = String(item.event || item.action || '').toLowerCase()
    return {
      at: item.at || '',
      url: item.url || '',
      title: item.title || '',
      event: event || 'activity',
      action: item.action || '',
      ok: item.ok !== false,
      blocked: Boolean(item.blocked || item.requires_confirmation),
      confirmed: Boolean(item.confirmed),
      target: compactPreviewText(item.target || '', 240),
      point: item.point || item.action_point || null,
      risk: item.risk || null,
      element_count: Number(item.element_count || 0) || 0,
      text_length: Number(item.text_length || 0) || 0,
      width: Number(item.width || 0) || 0,
      height: Number(item.height || 0) || 0,
      saved_path: item.saved_path || item.path || '',
      error: compactPreviewText(item.error || '', 300),
      navigation_resolution: item.navigation_resolution || null,
      diagnostics_counts: item.diagnostics_counts || null,
      page_health: item.page_health || null,
      screenshot_health: item.screenshot_health || null,
      summary: previewActivityLabel(item)
    }
  })
  const counts = {
    total: items.length,
    navigate: items.filter(item => item.event === 'navigate').length,
    observe: items.filter(item => item.event === 'observe').length,
    action: items.filter(item => !['navigate', 'observe', 'screenshot'].includes(item.event)).length,
    screenshot: items.filter(item => item.event === 'screenshot').length,
    blocked: items.filter(item => item.blocked).length,
    errors: items.filter(item => item.ok === false).length
  }
  const diagnostics = previewDiagnosticsPayload()
  return {
    ...previewStatePayload(),
    ok: true,
    items,
    counts,
    diagnostics_counts: diagnostics.counts || {}
  }
}

function previewDiagnosticsStore() {
  if (!globalThis.__metisPreviewDiagnostics) {
    globalThis.__metisPreviewDiagnostics = {
      console: [],
      exceptions: [],
      network_failed: [],
      page_failures: [],
      lifecycle: [],
      reset_at: new Date().toISOString(),
      reset_reason: 'initial'
    }
  }
  return globalThis.__metisPreviewDiagnostics
}

function boundedDiagnosticItems(items, limit = PREVIEW_DIAGNOSTIC_LIMIT) {
  while (items.length > limit) items.shift()
}

function resetPreviewDiagnostics(reason = 'navigation') {
  globalThis.__metisPreviewDiagnostics = {
    console: [],
    exceptions: [],
    network_failed: [],
    page_failures: [],
    lifecycle: [{
      at: new Date().toISOString(),
      reason,
      url: previewWebContents()?.getURL() || ''
    }],
    reset_at: new Date().toISOString(),
    reset_reason: reason
  }
}

function addPreviewDiagnostic(kind, entry = {}, limit = PREVIEW_DIAGNOSTIC_LIMIT) {
  const store = previewDiagnosticsStore()
  const target = Array.isArray(store[kind]) ? store[kind] : (store[kind] = [])
  target.push({
    at: new Date().toISOString(),
    url: previewWebContents()?.getURL() || '',
    title: previewWebContents()?.getTitle() || '',
    ...entry
  })
  boundedDiagnosticItems(target, limit)
}

function normalizePreviewConsoleLevel(level) {
  if (typeof level === 'number') return PREVIEW_CONSOLE_LEVELS[level] || `level-${level}`
  return String(level || 'info').toLowerCase()
}

function recordPreviewConsoleMessage(_event, levelOrDetails, message, line, sourceId) {
  const details = levelOrDetails && typeof levelOrDetails === 'object'
    ? levelOrDetails
    : { level: levelOrDetails, message, line, sourceId }
  const level = normalizePreviewConsoleLevel(details.level)
  const text = compactPreviewText(details.message || message || '', 1200)
  if (!text) return
  addPreviewDiagnostic('console', {
    level,
    message: text,
    line: Number(details.line || line || 0) || 0,
    source: compactPreviewText(details.sourceId || details.source || sourceId || '', 700)
  })
  if (level === 'error' || /\b(uncaught|exception|unhandled|syntaxerror|typeerror|referenceerror)\b/i.test(text)) {
    addPreviewDiagnostic('exceptions', {
      kind: 'console-error',
      message: text,
      line: Number(details.line || line || 0) || 0,
      source: compactPreviewText(details.sourceId || details.source || sourceId || '', 700)
    }, 40)
  }
}

function previewDiagnosticsPayload(extra = {}) {
  const store = previewDiagnosticsStore()
  const consoleItems = store.console.slice(-30)
  const exceptions = store.exceptions.slice(-20)
  const networkFailed = store.network_failed.slice(-20)
  const pageFailures = store.page_failures.slice(-12)
  const lifecycle = store.lifecycle.slice(-12)
  const consoleErrorCount = store.console.filter(item => item.level === 'error').length
  const consoleWarningCount = store.console.filter(item => item.level === 'warning' || item.level === 'warn').length
  return {
    reset_at: store.reset_at,
    reset_reason: store.reset_reason,
    counts: {
      console: store.console.length,
      console_errors: consoleErrorCount,
      console_warnings: consoleWarningCount,
      exceptions: store.exceptions.length,
      network_failed: store.network_failed.length,
      page_failures: store.page_failures.length
    },
    recent_console: consoleItems,
    exceptions,
    network_failed: networkFailed,
    page_failures: pageFailures,
    lifecycle,
    ...extra
  }
}

function buildPreviewPageHealth(observed = {}, diagnostics = previewDiagnosticsPayload()) {
  const url = String(observed.url || previewWebContents()?.getURL() || '')
  const title = String(observed.title || previewWebContents()?.getTitle() || '')
  const text = String(observed.text || '')
  const domSummary = observed.dom_summary || {}
  const elementCount = Array.isArray(observed.elements) ? observed.elements.length : 0
  const bodyTextLength = Number(domSummary.bodyTextLength ?? text.length) || 0
  const bodyChildCount = Number(domSummary.bodyChildCount || 0) || 0
  const reasons = []
  if (url.startsWith('chrome-error://')) reasons.push('chrome_error_page')
  if (diagnostics.counts?.page_failures) reasons.push('page_load_failure')
  if (diagnostics.counts?.network_failed) reasons.push('network_failure')
  if (diagnostics.counts?.exceptions) reasons.push('javascript_exception')
  if (bodyTextLength < 20 && elementCount === 0 && bodyChildCount <= 2) reasons.push('little_or_no_visible_dom')
  if (!title && bodyTextLength < 20 && elementCount === 0) reasons.push('empty_title_and_body')
  const blank = reasons.includes('chrome_error_page') || reasons.includes('little_or_no_visible_dom') || reasons.includes('empty_title_and_body')
  let status = 'ok'
  if (blank || reasons.includes('page_load_failure')) status = 'error'
  else if (reasons.length) status = 'warning'
  return {
    status,
    blank,
    reasons,
    url,
    title,
    bodyTextLength,
    elementCount,
    bodyChildCount
  }
}

async function collectPreviewPageErrors(webContents) {
  if (!webContents || webContents.isDestroyed()) return []
  const script = `
(() => {
  const errors = Array.isArray(window.__metisPreviewErrors) ? window.__metisPreviewErrors.slice(-20) : [];
  if (Array.isArray(window.__metisPreviewErrors)) window.__metisPreviewErrors = [];
  return errors.map(item => ({
    at: item.at || '',
    kind: item.kind || 'page-error',
    message: String(item.message || '').slice(0, 1200),
    source: String(item.source || '').slice(0, 700),
    line: Number(item.line || 0) || 0,
    column: Number(item.column || 0) || 0
  }));
})()
`
  try {
    const errors = await webContents.executeJavaScript(script, true)
    return Array.isArray(errors) ? errors : []
  } catch {
    return []
  }
}

async function installPreviewPageDiagnosticsHooks(webContents) {
  if (!webContents || webContents.isDestroyed()) return
  const script = `
(() => {
  if (window.__metisPreviewDiagnosticsHooked) return true;
  window.__metisPreviewDiagnosticsHooked = true;
  window.__metisPreviewErrors = Array.isArray(window.__metisPreviewErrors) ? window.__metisPreviewErrors : [];
  const push = item => {
    window.__metisPreviewErrors.push({ at: new Date().toISOString(), ...item });
    if (window.__metisPreviewErrors.length > 80) window.__metisPreviewErrors.shift();
  };
  window.addEventListener('error', event => {
    push({
      kind: 'error',
      message: event.message || String(event.error || 'Script error'),
      source: event.filename || '',
      line: event.lineno || 0,
      column: event.colno || 0
    });
  });
  window.addEventListener('unhandledrejection', event => {
    const reason = event.reason;
    push({
      kind: 'unhandledrejection',
      message: reason && reason.stack ? String(reason.stack) : String(reason && reason.message ? reason.message : reason),
      source: '',
      line: 0,
      column: 0
    });
  });
  return true;
})()
`
  try {
    await webContents.executeJavaScript(script, true)
  } catch {}
}

function previewElementSearchText(meta = {}, payload = {}) {
  return compactPreviewText([
    meta.text,
    meta.href,
    meta.selector,
    meta.role,
    meta.type,
    meta.name,
    meta.title,
    meta.ariaLabel,
    meta.placeholder,
    meta.formAction,
    payload.text,
    payload.key
  ].filter(Boolean).join(' '), 1600)
}

function classifyPreviewActionRisk(action = '', payload = {}, meta = {}) {
  const normalizedAction = String(action || '').trim().toLowerCase()
  const tag = String(meta.tag || '').toLowerCase()
  const type = String(meta.type || '').toLowerCase()
  const role = String(meta.role || '').toLowerCase()
  const href = String(meta.href || meta.formAction || '')
  const searchText = previewElementSearchText(meta, payload)
  const reasons = []

  if (PREVIEW_SENSITIVE_INPUT_TYPES.has(type)) {
    reasons.push(type === 'file' ? 'file_upload_control' : 'password_or_secret_input')
  }
  if (PREVIEW_SUBMIT_INPUT_TYPES.has(type)) {
    reasons.push('submit_control')
  }
  if (tag === 'button' && String(meta.buttonType || '').toLowerCase() === 'submit') {
    reasons.push('submit_button')
  }
  if (PREVIEW_AUTH_URL_PATTERN.test(href)) {
    reasons.push('auth_or_oauth_navigation')
  }
  if (PREVIEW_RISK_PATTERN.test(searchText)) {
    reasons.push('risk_keyword')
  }
  if (normalizedAction === 'type' && PREVIEW_RISK_PATTERN.test(String(meta.labelText || searchText))) {
    reasons.push('sensitive_text_entry_target')
  }
  if (
    normalizedAction === 'key' &&
    String(payload.key || '').toLowerCase() === 'enter' &&
    (meta.withinForm || PREVIEW_RISK_PATTERN.test(searchText))
  ) {
    reasons.push('enter_may_submit_form')
  }

  const riskLevel = reasons.length ? 'high' : 'none'
  return {
    risk_level: riskLevel,
    requires_confirmation: riskLevel !== 'none',
    reasons: [...new Set(reasons)],
    summary: riskLevel === 'none'
      ? ''
      : compactPreviewText(`${normalizedAction || 'action'} on ${searchText || tag || 'page element'}`, 300)
  }
}

async function inspectPreviewTarget(webContents, payload = {}, point = {}, action = '') {
  const fallback = point.cached || {}
  const useActive = !point.elementId && !payload.x && !payload.y && ['key', 'type'].includes(String(action || '').toLowerCase())
  const script = `
(() => {
  const input = ${JSON.stringify({ x: point.x || 0, y: point.y || 0, useActive })};
  const clampText = (value, max = 500) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, max);
  const rectPayload = rect => ({
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    centerX: Math.round(rect.x + rect.width / 2),
    centerY: Math.round(rect.y + rect.height / 2)
  });
  const base = input.useActive ? document.activeElement : document.elementFromPoint(input.x, input.y);
  const el = base && base.closest
    ? (base.closest('a[href],button,input,textarea,select,summary,label,[role="button"],[role="link"],[role="menuitem"],[contenteditable=""],[contenteditable="true"],[onclick]') || base)
    : base;
  if (!el || !el.getBoundingClientRect) return null;
  const form = el.closest ? el.closest('form') : null;
  const label =
    el.getAttribute('aria-label') ||
    el.getAttribute('title') ||
    el.getAttribute('placeholder') ||
    el.innerText ||
    el.value ||
    el.name ||
    el.href ||
    '';
  const rect = el.getBoundingClientRect();
  return {
    tag: el.tagName ? el.tagName.toLowerCase() : '',
    role: el.getAttribute ? (el.getAttribute('role') || '') : '',
    type: el.getAttribute ? (el.getAttribute('type') || '') : '',
    buttonType: el.tagName && el.tagName.toLowerCase() === 'button'
      ? ((el.getAttribute && el.getAttribute('type')) || (form ? 'submit' : 'button'))
      : (el.getAttribute ? (el.getAttribute('type') || '') : ''),
    text: clampText(label, 300),
    href: el.href ? String(el.href).slice(0, 700) : '',
    name: el.getAttribute ? (el.getAttribute('name') || '') : '',
    title: el.getAttribute ? (el.getAttribute('title') || '') : '',
    ariaLabel: el.getAttribute ? (el.getAttribute('aria-label') || '') : '',
    placeholder: el.getAttribute ? (el.getAttribute('placeholder') || '') : '',
    labelText: clampText((el.labels && el.labels.length ? Array.from(el.labels).map(item => item.innerText || '').join(' ') : '') || '', 300),
    withinForm: Boolean(form),
    formAction: form ? String(form.action || '').slice(0, 700) : '',
    formMethod: form ? String(form.method || '').toLowerCase() : '',
    isContentEditable: Boolean(el.isContentEditable),
    rect: rectPayload(rect)
  };
})()
`
  try {
    const fresh = await webContents.executeJavaScript(script, true)
    return { ...fallback, ...(fresh || {}) }
  } catch {
    return fallback
  }
}

async function confirmPreviewRisk(action, payload, meta, risk) {
  if (!risk?.requires_confirmation) return true
  if (!mainWindow || mainWindow.isDestroyed()) return false
  try {
    showWindow()
  } catch {}
  const response = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['允许一次', '取消'],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
    title: 'Preview Browser 需要确认',
    message: 'Metis 即将在 Preview 网页中执行一个可能产生外部影响的动作。',
    detail: [
      `动作: ${action}`,
      `页面: ${previewWebContents()?.getURL() || ''}`,
      `目标: ${compactPreviewText(meta.text || meta.href || meta.selector || '', 220) || '页面元素'}`,
      `原因: ${(risk.reasons || []).join(', ') || 'risk_keyword'}`
    ].join('\n')
  })
  return response.response === 0
}

async function observePreviewPage(payload = {}) {
  const webContents = previewWebContents()
  if (!webContents) return { ok: false, error: 'preview view unavailable' }
  const options = {
    maxElements: clampPreviewNumber(payload.maxElements ?? payload.max_elements, 1, 200, 80),
    includeText: payload.includeText !== false && payload.include_text !== false
  }
  const script = `
(() => {
  const options = ${JSON.stringify(options)};
  const clampText = (value, max = 500) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, max);
  const rectPayload = rect => ({
    x: Math.round(rect.x),
    y: Math.round(rect.y),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    centerX: Math.round(rect.x + rect.width / 2),
    centerY: Math.round(rect.y + rect.height / 2)
  });
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity || '1') === 0) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    return rect.bottom >= 0 && rect.right >= 0 && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
  };
  const selectorFor = el => {
    if (!el || !el.tagName) return '';
    const cssEscape = value => window.CSS && CSS.escape ? CSS.escape(value) : String(value || '').replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
    if (el.id) return '#' + cssEscape(el.id);
    const testId = el.getAttribute('data-testid') || el.getAttribute('data-test-id');
    if (testId) return el.tagName.toLowerCase() + '[data-testid="' + testId.replace(/"/g, '\\\\"') + '"]';
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 4) {
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(child => child.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  const nodes = Array.from(document.querySelectorAll([
    'a[href]',
    'button',
    'input',
    'textarea',
    'select',
    'summary',
    'label',
    '[role="button"]',
    '[role="link"]',
    '[role="menuitem"]',
    '[tabindex]:not([tabindex="-1"])',
    '[contenteditable=""]',
    '[contenteditable="true"]',
    '[onclick]'
  ].join(',')));
  const seen = new Set();
  const elements = [];
  for (const el of nodes) {
    if (elements.length >= options.maxElements) break;
    if (seen.has(el) || !visible(el)) continue;
    seen.add(el);
    const rect = el.getBoundingClientRect();
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const type = el.getAttribute('type') || '';
    const form = el.closest ? el.closest('form') : null;
    const label =
      el.getAttribute('aria-label') ||
      el.getAttribute('title') ||
      el.getAttribute('placeholder') ||
      el.innerText ||
      el.value ||
      el.name ||
      el.href ||
      '';
    elements.push({
      tag,
      role,
      type,
      buttonType: tag === 'button' ? (type || (form ? 'submit' : 'button')) : type,
      text: clampText(label, 220),
      href: tag === 'a' ? String(el.href || '').slice(0, 500) : '',
      name: el.getAttribute('name') || '',
      title: el.getAttribute('title') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      placeholder: el.getAttribute('placeholder') || '',
      labelText: clampText((el.labels && el.labels.length ? Array.from(el.labels).map(item => item.innerText || '').join(' ') : '') || '', 220),
      withinForm: Boolean(form),
      formAction: form ? String(form.action || '').slice(0, 500) : '',
      selector: selectorFor(el),
      rect: rectPayload(rect),
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
      readOnly: Boolean(el.readOnly || el.getAttribute('aria-readonly') === 'true'),
      isContentEditable: Boolean(el.isContentEditable)
    });
  }
  const bodyText = options.includeText && document.body ? clampText(document.body.innerText || document.body.textContent || '', 6000) : '';
  const headings = Array.from(document.querySelectorAll('h1,h2,h3')).slice(0, 12).map(item => ({
    tag: item.tagName.toLowerCase(),
    text: clampText(item.innerText || item.textContent || '', 180)
  })).filter(item => item.text);
  const domSummary = {
    bodyTextLength: document.body ? String(document.body.innerText || document.body.textContent || '').trim().length : 0,
    bodyChildCount: document.body ? document.body.children.length : 0,
    rootChildCount: document.documentElement ? document.documentElement.children.length : 0,
    buttons: document.querySelectorAll('button,[role="button"]').length,
    inputs: document.querySelectorAll('input,textarea,select,[contenteditable=""],[contenteditable="true"]').length,
    links: document.querySelectorAll('a[href]').length,
    forms: document.querySelectorAll('form').length,
    images: document.querySelectorAll('img,svg,canvas,video').length,
    scripts: document.scripts ? document.scripts.length : 0,
    stylesheets: document.styleSheets ? document.styleSheets.length : 0,
    headings,
    bodyClass: document.body ? clampText(document.body.className || '', 240) : '',
    appRoots: Array.from(document.querySelectorAll('#root, #app, [data-reactroot], [data-nextjs-root]')).map(item => ({
      selector: item.id ? '#' + item.id : (item.getAttribute('data-reactroot') !== null ? '[data-reactroot]' : item.tagName.toLowerCase()),
      childCount: item.children ? item.children.length : 0,
      textLength: String(item.innerText || item.textContent || '').trim().length
    }))
  };
  return {
    ok: true,
    url: location.href,
    title: document.title,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scrollX: Math.round(window.scrollX || 0),
      scrollY: Math.round(window.scrollY || 0),
      devicePixelRatio: window.devicePixelRatio || 1
    },
    text: bodyText,
    elements,
    dom_summary: domSummary
  };
})()
`
  try {
    const observed = await webContents.executeJavaScript(script, true)
    const pageErrors = await collectPreviewPageErrors(webContents)
    for (const errorItem of pageErrors) {
      addPreviewDiagnostic('exceptions', errorItem, 40)
    }
    previewElementCache.clear()
    const stamp = Date.now().toString(36)
    const elements = Array.isArray(observed?.elements) ? observed.elements.map((element, index) => {
      const elementId = `preview-${stamp}-${index + 1}`
      const rect = element.rect || {}
      const risk = classifyPreviewActionRisk('click', {}, element)
      previewElementCache.set(elementId, {
        x: Number(rect.centerX) || 0,
        y: Number(rect.centerY) || 0,
        rect,
        selector: element.selector || '',
        text: element.text || '',
        href: element.href || '',
        tag: element.tag || '',
        role: element.role || '',
        type: element.type || '',
        buttonType: element.buttonType || '',
        name: element.name || '',
        title: element.title || '',
        ariaLabel: element.ariaLabel || '',
        placeholder: element.placeholder || '',
        labelText: element.labelText || '',
        withinForm: Boolean(element.withinForm),
        formAction: element.formAction || '',
        disabled: Boolean(element.disabled),
        readOnly: Boolean(element.readOnly),
        isContentEditable: Boolean(element.isContentEditable),
        risk,
        observedAt: Date.now()
      })
      return {
        element_id: elementId,
        risk,
        ...element
      }
    }) : []
    const diagnostics = previewDiagnosticsPayload()
    const pageHealth = buildPreviewPageHealth(observed, diagnostics)
    recordPreviewAction({
      event: 'observe',
      action: 'observe',
      ok: true,
      element_count: elements.length,
      text_length: String(observed?.text || '').length,
      page_health: pageHealth,
      diagnostics_counts: diagnostics.counts || {}
    })
    const browserActivity = previewActivityPayload()
    return {
      ...previewStatePayload(),
      ...observed,
      elements,
      diagnostics,
      page_health: pageHealth,
      action_log: browserActivity.items.slice(-12),
      browser_activity: browserActivity
    }
  } catch (error) {
    const diagnostics = previewDiagnosticsPayload()
    recordPreviewAction({
      event: 'observe',
      action: 'observe',
      ok: false,
      error: error?.message || String(error),
      diagnostics_counts: diagnostics.counts || {}
    })
    return {
      ...previewStatePayload(),
      ok: false,
      error: error?.message || String(error),
      diagnostics,
      page_health: buildPreviewPageHealth({}, diagnostics),
      browser_activity: previewActivityPayload()
    }
  }
}

function previewPointForPayload(payload = {}) {
  const elementId = String(payload.elementId || payload.element_id || '')
  const cached = elementId ? previewElementCache.get(elementId) : null
  if (cached) {
    return { x: cached.x, y: cached.y, elementId, cached }
  }
  const bounds = lastPreviewBounds || { width: 1200, height: 800 }
  const x = clampPreviewNumber(payload.x, 0, Math.max(0, bounds.width || 0), Math.round((bounds.width || 0) / 2))
  const y = clampPreviewNumber(payload.y, 0, Math.max(0, bounds.height || 0), Math.round((bounds.height || 0) / 2))
  return { x, y, elementId, cached: null }
}

function sendPreviewClick(webContents, x, y, clickCount = 1) {
  webContents.sendInputEvent({ type: 'mouseMove', x, y })
  webContents.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount })
  webContents.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount })
}

async function performPreviewAction(payload = {}) {
  const action = String(payload.action || '').trim().toLowerCase()
  const point = previewPointForPayload(payload)
  const webContents = previewWebContents()
  if (!webContents) {
    recordPreviewAction({
      event: 'action',
      action,
      ok: false,
      error: 'preview view unavailable',
      point: { x: point.x, y: point.y, element_id: point.elementId || '' }
    })
    return { ok: false, action, error: 'preview view unavailable', browser_activity: previewActivityPayload() }
  }
  try {
    const targetMeta = await inspectPreviewTarget(webContents, payload, point, action)
    const risk = ['click', 'double_click', 'type', 'key'].includes(action)
      ? classifyPreviewActionRisk(action, payload, targetMeta)
      : { risk_level: 'none', requires_confirmation: false, reasons: [], summary: '' }
    if (risk.requires_confirmation) {
      const confirmed = await confirmPreviewRisk(action, payload, targetMeta, risk)
      if (!confirmed) {
        recordPreviewAction({
          event: 'action',
          action,
          blocked: true,
          risk,
          target: compactPreviewText(targetMeta.text || targetMeta.href || targetMeta.selector || '', 300),
          point: { x: point.x, y: point.y, element_id: point.elementId || '' }
        })
        return {
          ...previewStatePayload(),
          ok: false,
          blocked: true,
          requires_confirmation: true,
          risk,
          action,
          action_point: { x: point.x, y: point.y, element_id: point.elementId || '' },
          target: targetMeta,
          browser_activity: previewActivityPayload(),
          message: 'Preview Browser blocked this high-risk action until the user confirms it.'
        }
      }
      recordPreviewAction({
        event: 'action',
        action,
        confirmed: true,
        risk,
        target: compactPreviewText(targetMeta.text || targetMeta.href || targetMeta.selector || '', 300),
        point: { x: point.x, y: point.y, element_id: point.elementId || '' }
      })
    }
    try { webContents.focus() } catch {}
    if (action === 'click') {
      sendPreviewClick(webContents, point.x, point.y, 1)
    } else if (action === 'double_click') {
      sendPreviewClick(webContents, point.x, point.y, 2)
    } else if (action === 'type') {
      if (point.elementId || payload.x || payload.y) {
        sendPreviewClick(webContents, point.x, point.y, 1)
        await delay(80)
      }
      await webContents.insertText(String(payload.text || ''))
    } else if (action === 'key') {
      const keyCode = String(payload.key || 'Enter')
      webContents.sendInputEvent({ type: 'keyDown', keyCode })
      webContents.sendInputEvent({ type: 'keyUp', keyCode })
    } else if (action === 'scroll') {
      const scrollY = clampPreviewNumber(payload.scrollY ?? payload.scroll_y, -5000, 5000, 600)
      const scrollScript = `window.scrollBy({ left: 0, top: ${JSON.stringify(scrollY)}, behavior: 'auto' }); true;`
      await webContents.executeJavaScript(scrollScript, true).catch(() => {})
      webContents.sendInputEvent({ type: 'mouseWheel', x: point.x, y: point.y, deltaY: -scrollY })
    } else if (action === 'wait') {
      await delay(clampPreviewNumber(payload.waitMs ?? payload.wait_ms, 100, 10000, 800))
    } else {
      recordPreviewAction({
        event: 'action',
        action,
        ok: false,
        error: `unknown preview action: ${action}`,
        point: { x: point.x, y: point.y, element_id: point.elementId || '' }
      })
      return { ...previewStatePayload(), ok: false, action, error: `unknown preview action: ${action}`, browser_activity: previewActivityPayload() }
    }
    await delay(action === 'wait' ? 0 : 180)
    const observed = await observePreviewPage({ maxElements: 25, includeText: true })
    recordPreviewAction({
      event: 'action',
      action,
      ok: Boolean(observed.ok),
      risk,
      target: compactPreviewText(targetMeta.text || targetMeta.href || targetMeta.selector || '', 300),
      point: { x: point.x, y: point.y, element_id: point.elementId || '' }
    })
    return {
      ...observed,
      action,
      risk,
      target: targetMeta,
      action_point: { x: point.x, y: point.y, element_id: point.elementId || '' },
      browser_activity: previewActivityPayload()
    }
  } catch (error) {
    recordPreviewAction({
      event: 'action',
      action,
      ok: false,
      error: error?.message || String(error),
      point: { x: point.x, y: point.y, element_id: point.elementId || '' }
    })
    return { ...previewStatePayload(), ok: false, action, error: error?.message || String(error), browser_activity: previewActivityPayload() }
  }
}

function analyzePreviewImageHealth(image) {
  const size = image?.getSize ? image.getSize() : { width: 0, height: 0 }
  const width = Number(size.width || 0)
  const height = Number(size.height || 0)
  const bitmap = image?.getBitmap ? image.getBitmap() : null
  if (!bitmap || !bitmap.length || width <= 0 || height <= 0) {
    return {
      ok: false,
      width,
      height,
      sampled_pixels: 0,
      appears_blank: true,
      reasons: ['empty_capture']
    }
  }

  const pixelCount = Math.floor(bitmap.length / 4)
  const step = Math.max(1, Math.floor(pixelCount / 12000))
  let sampled = 0
  let nearWhite = 0
  let nearBlack = 0
  let transparent = 0
  let minBrightness = 255
  let maxBrightness = 0
  let brightnessTotal = 0

  for (let pixel = 0; pixel < pixelCount; pixel += step) {
    const offset = pixel * 4
    const c1 = bitmap[offset] ?? 0
    const c2 = bitmap[offset + 1] ?? 0
    const c3 = bitmap[offset + 2] ?? 0
    const alpha = bitmap[offset + 3] ?? 255
    sampled += 1
    if (alpha === 0) {
      transparent += 1
      continue
    }
    const brightness = Math.round((c1 + c2 + c3) / 3)
    minBrightness = Math.min(minBrightness, brightness)
    maxBrightness = Math.max(maxBrightness, brightness)
    brightnessTotal += brightness
    if (c1 >= 248 && c2 >= 248 && c3 >= 248) nearWhite += 1
    if (c1 <= 7 && c2 <= 7 && c3 <= 7) nearBlack += 1
  }

  const nonTransparent = Math.max(1, sampled - transparent)
  const nearWhiteRatio = nearWhite / nonTransparent
  const nearBlackRatio = nearBlack / nonTransparent
  const transparentRatio = transparent / Math.max(1, sampled)
  const brightnessRange = maxBrightness - minBrightness
  const reasons = []
  if (nearWhiteRatio >= 0.985) reasons.push('mostly_white')
  if (nearBlackRatio >= 0.985) reasons.push('mostly_black')
  if (transparentRatio >= 0.985) reasons.push('mostly_transparent')
  if (brightnessRange <= 2) reasons.push('flat_brightness')

  return {
    ok: true,
    width,
    height,
    sampled_pixels: sampled,
    near_white_ratio: Number(nearWhiteRatio.toFixed(4)),
    near_black_ratio: Number(nearBlackRatio.toFixed(4)),
    transparent_ratio: Number(transparentRatio.toFixed(4)),
    brightness_min: minBrightness,
    brightness_max: maxBrightness,
    brightness_average: Number((brightnessTotal / nonTransparent).toFixed(1)),
    appears_blank: reasons.length > 0,
    reasons
  }
}

async function capturePreviewPage() {
  const webContents = previewWebContents()
  if (!webContents) {
    recordPreviewAction({
      event: 'screenshot',
      action: 'screenshot',
      ok: false,
      error: 'preview view unavailable'
    })
    return { ...previewStatePayload(), ok: false, dataUrl: '', error: 'preview view unavailable', browser_activity: previewActivityPayload() }
  }
  try {
    const image = await webContents.capturePage()
    const size = image.getSize()
    const screenshotHealth = analyzePreviewImageHealth(image)
    const diagnostics = previewDiagnosticsPayload()
    let domSummary = {}
    try {
      domSummary = await webContents.executeJavaScript(`
(() => ({
  bodyTextLength: document.body ? String(document.body.innerText || document.body.textContent || '').trim().length : 0,
  bodyChildCount: document.body ? document.body.children.length : 0,
  buttons: document.querySelectorAll('button,[role="button"]').length,
  inputs: document.querySelectorAll('input,textarea,select,[contenteditable=""],[contenteditable="true"]').length,
  links: document.querySelectorAll('a[href]').length,
  forms: document.querySelectorAll('form').length
}))()
`, true)
    } catch {}
    const state = previewStatePayload()
    const pageHealth = buildPreviewPageHealth({ ...state, dom_summary: domSummary }, diagnostics)
    recordPreviewAction({
      event: 'screenshot',
      action: 'screenshot',
      ok: true,
      width: size.width,
      height: size.height,
      page_health: pageHealth,
      screenshot_health: screenshotHealth,
      diagnostics_counts: diagnostics.counts || {}
    })
    return {
      ...state,
      ok: true,
      width: size.width,
      height: size.height,
      viewport: lastPreviewBounds || null,
      diagnostics,
      page_health: pageHealth,
      screenshot_health: screenshotHealth,
      browser_activity: previewActivityPayload(),
      dataUrl: image.toDataURL()
    }
  } catch (error) {
    const diagnostics = previewDiagnosticsPayload()
    recordPreviewAction({
      event: 'screenshot',
      action: 'screenshot',
      ok: false,
      error: error?.message || String(error),
      diagnostics_counts: diagnostics.counts || {}
    })
    return {
      ...previewStatePayload(),
      ok: false,
      dataUrl: '',
      error: error?.message || String(error),
      diagnostics,
      page_health: buildPreviewPageHealth({}, diagnostics),
      browser_activity: previewActivityPayload()
    }
  }
}

async function handlePreviewBridgeRequest(request = {}) {
  const kind = String(request.kind || '').trim()
  const payload = request.payload || {}
  if (kind === 'navigate') {
    return loadPreviewUrl(payload.url, payload.tabId || payload.tab_id || '')
  }
  if (kind === 'observe') {
    return observePreviewPage(payload)
  }
  if (kind === 'action') {
    return performPreviewAction(payload)
  }
  if (kind === 'screenshot') {
    return capturePreviewPage()
  }
  if (kind === 'status') {
    return previewStatePayload()
  }
  if (kind === 'activity') {
    return previewActivityPayload(payload.limit)
  }
  return { ok: false, error: `unknown preview bridge command: ${kind}` }
}

async function postPreviewBridgeResult(requestId, result) {
  if (!backendPort || !requestId) return
  await fetch(`http://127.0.0.1:${backendPort}/api/preview-browser/result`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: requestId, result })
  })
}

function startPreviewBridgeLoop() {
  if (!backendPort) return
  const loopId = ++previewBridgeLoopId
  void (async () => {
    log('[preview-browser] bridge loop started')
    while (loopId === previewBridgeLoopId && backendPort) {
      try {
        const response = await fetch(`http://127.0.0.1:${backendPort}/api/preview-browser/next?timeout=25`)
        if (!response.ok) {
          await delay(1000)
          continue
        }
        const data = await response.json()
        const request = data?.request
        if (!request?.id) {
          await delay(150)
          continue
        }
        let result
        try {
          result = await handlePreviewBridgeRequest(request)
        } catch (error) {
          result = { ok: false, error: error?.message || String(error) }
        }
        try {
          await postPreviewBridgeResult(request.id, result)
        } catch (error) {
          log(`[preview-browser] failed to post result: ${error?.message || error}`)
        }
      } catch (error) {
        if (loopId === previewBridgeLoopId && backendPort) {
          await delay(1000)
        }
      }
    }
    log('[preview-browser] bridge loop stopped')
  })()
}

function stopPreviewBridgeLoop() {
  previewBridgeLoopId += 1
}

function emitBootEvent(event) {
  const payload = {
    timestamp: new Date().toISOString(),
    logPath: getBackendLogPath(),
    ...event
  }

  if (payload.phase === 'ready' && payload.port) {
    backendPort = payload.port
    bootStatus = 'ready'
    bootError = null
  } else if (payload.phase === 'error' || payload.phase === 'exit') {
    backendPort = null
    bootStatus = 'error'
    stopPreviewBridgeLoop()
    bootError = {
      title: payload.title || '后端启动失败',
      detail: payload.detail || payload.logTail || '',
      logTail: payload.logTail || tailBackendLog()
    }
  } else if (payload.phase === 'stopped') {
    backendPort = null
    stopPreviewBridgeLoop()
  } else if (payload.phase !== 'log') {
    bootStatus = 'starting'
  }

  bootEvents.push(payload)
  while (bootEvents.length > 160) {
    bootEvents.shift()
  }

  if (payload.phase === 'log') {
    log(payload.line || '')
  } else {
    log(`[boot] ${payload.title || payload.line || payload.phase}`)
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('metis:boot-event', payload)
    if (payload.phase === 'exit') {
      mainWindow.webContents.send('metis:backend-exit', payload)
    }
  }

  if (payload.phase === 'exit') {
    scheduleBackendRestart(payload)
  }
}

function clearBackendRestartTimer() {
  if (!backendRestartTimer) {
    return
  }
  clearTimeout(backendRestartTimer)
  backendRestartTimer = null
}

function scheduleBackendRestart(exitPayload) {
  if (app.isQuitting || bootRunning || backendRestartTimer || process.env.METIS_FAKE_BACKEND === '1') {
    return
  }

  if (backendRestartAttempts >= BACKEND_RESTART_LIMIT) {
    emitBootEvent({
      phase: 'error',
      title: `重新连接 ${BACKEND_RESTART_LIMIT} 次仍失败，已放弃`,
      detail: `Metis 已尝试重新连接后端 ${BACKEND_RESTART_LIMIT} 次仍无法连接。\n\n${exitPayload?.detail || tailBackendLog()}`,
      attempt: backendRestartAttempts,
      limit: BACKEND_RESTART_LIMIT,
      logTail: tailBackendLog()
    })
    return
  }

  backendRestartAttempts += 1
  const delayMs = Math.min(1000 * 2 ** (backendRestartAttempts - 1), 8000)
  emitBootEvent({
    phase: 'restarting',
    title: `正在重新连接 (${backendRestartAttempts}/${BACKEND_RESTART_LIMIT})`,
    detail: `将在 ${Math.round(delayMs / 1000)} 秒后重试。`,
    attempt: backendRestartAttempts,
    limit: BACKEND_RESTART_LIMIT,
    logTail: tailBackendLog()
  })
  backendRestartTimer = setTimeout(() => {
    backendRestartTimer = null
    void startBackendWithEvents({ reset: false })
  }, delayMs)
}

function bootState() {
  return {
    status: bootStatus,
    port: backendPort,
    error: bootError,
    events: bootEvents.slice(-160),
    logPath: getBackendLogPath()
  }
}

function redactDiagnosticsText(value) {
  return String(value || '')
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, 'sk-***')
    .replace(/(api[_-]?key\s*["':=]\s*)[^\s"',;}]+/gi, '$1***')
    .replace(/(authorization\s*["':=]\s*bearer\s+)[^\s"',;}]+/gi, '$1***')
    .replace(/([?&](?:access_token|refresh_token|id_token|token|api_key|apikey|key|code|secret|password)=)[^&\s"'`]+/gi, '$1***')
    .replace(/-----BEGIN [^-]+PRIVATE KEY-----[\s\S]*?-----END [^-]+PRIVATE KEY-----/g, '[redacted private key]')
    .replace(/\b[\w.-]+\.(?:pem|pfx|key)\b/gi, '[redacted secret file]')
}

function redactDiagnosticsValue(value, depth = 0) {
  if (depth > 8) return '[redacted deep object]'
  if (typeof value === 'string') return redactDiagnosticsText(value)
  if (value === null || value === undefined) return value
  if (typeof value !== 'object') return value
  if (Array.isArray(value)) return value.slice(0, 200).map(item => redactDiagnosticsValue(item, depth + 1))
  const output = {}
  for (const [key, field] of Object.entries(value)) {
    const lower = key.toLowerCase()
    if (/(api[_-]?key|apikey|token|secret|password|authorization|access[_-]?token|refresh[_-]?token|id[_-]?token|oauth[_-]?code)/i.test(lower)) {
      output[key] = '***'
      continue
    }
    output[key] = redactDiagnosticsValue(field, depth + 1)
  }
  return output
}

function sanitizedBootEvent(event) {
  return {
    ...event,
    title: redactDiagnosticsText(event.title),
    detail: redactDiagnosticsText(event.detail),
    line: redactDiagnosticsText(event.line),
    logTail: redactDiagnosticsText(event.logTail)
  }
}

function sanitizedBootError(error) {
  if (!error) return null
  return {
    title: redactDiagnosticsText(error.title),
    detail: redactDiagnosticsText(error.detail),
    logTail: redactDiagnosticsText(error.logTail)
  }
}

async function fetchBackendDiagnostics(pathname) {
  if (!backendPort) {
    return { ok: false, skipped: true, error: 'backend unavailable' }
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 1800)
  try {
    const response = await fetch(`http://127.0.0.1:${backendPort}${pathname}`, { signal: controller.signal })
    const text = await response.text()
    let data = null
    try {
      data = text ? JSON.parse(text) : null
    } catch {
      data = text
    }
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        error: redactDiagnosticsText(typeof data === 'string' ? data.slice(0, 1000) : JSON.stringify(data).slice(0, 1000))
      }
    }
    return { ok: true, data: redactDiagnosticsValue(data) }
  } catch (error) {
    return {
      ok: false,
      error: error?.name === 'AbortError' ? 'backend diagnostics request timed out' : redactDiagnosticsText(error?.message || String(error))
    }
  } finally {
    clearTimeout(timer)
  }
}

async function diagnosticsPayload() {
  const state = bootState()
  const storage = resolveDataRootInfo()
  const [runs, permissions, toolAudit, deskStatus, deskGoalLog] = await Promise.all([
    fetchBackendDiagnostics('/runs'),
    fetchBackendDiagnostics('/permissions'),
    fetchBackendDiagnostics('/diagnostics/tool-audit?limit=80'),
    fetchBackendDiagnostics('/api/status'),
    fetchBackendDiagnostics('/api/goal/log?n=80')
  ])
  return redactDiagnosticsValue({
    generatedAt: new Date().toISOString(),
    app: {
      name: app.getName(),
      version: app.getVersion(),
      packaged: app.isPackaged,
      fakeBackend: process.env.METIS_FAKE_BACKEND === '1'
    },
    platform: {
      platform: process.platform,
      arch: process.arch,
      release: os.release(),
      versions: {
        electron: process.versions.electron || '',
        chrome: process.versions.chrome || '',
        node: process.versions.node || ''
      }
    },
    backend: {
      status: state.status,
      port: state.port,
      logPath: getBackendLogPath(),
      logTail: redactDiagnosticsText(tailBackendLog(120))
    },
    storage,
    boot: {
      error: sanitizedBootError(state.error),
      events: state.events.slice(-80).map(sanitizedBootEvent)
    },
    terminal: {
      activeSessions: terminalSessions.size,
      backend: nodePty ? 'pty' : 'shell'
    },
    preview: {
      state: previewStatePayload(),
      activity: previewActivityPayload(48),
      diagnostics: previewDiagnosticsPayload(),
      evidence: {
        directory: path.join(app.getPath('userData'), 'preview-evidence')
      }
    },
    backendRuntime: {
      runs,
      permissions,
      toolAudit,
      deskStatus,
      deskGoalLog
    }
  })
}

function diagnosticsBundleContent(payload = diagnosticsPayload()) {
  return JSON.stringify(
    {
      schema: 'metis.diagnostics.bundle.v1',
      diagnostics: redactDiagnosticsValue(payload)
    },
    null,
    2
  )
}

async function saveDiagnosticsBundle() {
  const diagnostics = await diagnosticsPayload()
  const content = diagnosticsBundleContent(diagnostics)

  if (process.env.METIS_DESKTOP_SMOKE === '1') {
    const outputDir = path.join(app.getPath('userData'), 'diagnostics')
    await fs.mkdir(outputDir, { recursive: true })
    const outputPath = path.join(outputDir, 'metis-diagnostics-smoke.json')
    await fs.writeFile(outputPath, content, 'utf8')
    return { canceled: false, path: outputPath, diagnostics }
  }

  if (!mainWindow) {
    return { canceled: true, diagnostics }
  }
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: `metis-diagnostics-${new Date().toISOString().replace(/[:.]/g, '-')}.json`,
    filters: [{ name: 'Metis Diagnostics', extensions: ['json'] }]
  })
  if (result.canceled || !result.filePath) {
    return { canceled: true, diagnostics }
  }
  await fs.writeFile(result.filePath, content, 'utf8')
  return { canceled: false, path: result.filePath, diagnostics }
}

function boundedLines(lines, limit = DEV_SERVER_LOG_LIMIT) {
  return lines.slice(-limit)
}

function devServerCwd(value) {
  const candidate = String(value || '').trim()
  return candidate || app.getPath('home')
}

function devServerStatusFromDetection(detected, state = 'detected') {
  return {
    state,
    cwd: detected.cwd || '',
    packagePath: detected.packagePath || '',
    packageManager: detected.packageManager || 'npm',
    scriptName: detected.scriptName || '',
    command: detected.command || '',
    stack: detected.stack || '',
    url: '',
    logs: [],
    reason: detected.reason || '',
    startedAt: 0,
    updatedAt: Date.now()
  }
}

function devServerIdleStatus(cwd, reason = '') {
  return {
    state: 'idle',
    cwd,
    packagePath: path.join(cwd, 'package.json'),
    packageManager: 'npm',
    scriptName: '',
    command: '',
    stack: '',
    url: '',
    logs: reason ? [reason] : [],
    reason,
    startedAt: 0,
    updatedAt: Date.now()
  }
}

function emitDevServerEvent(type, status) {
  const payload = {
    type,
    status: {
      ...status,
      logs: boundedLines(status.logs || [])
    }
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('metis:dev-server-event', payload)
  }
}

function packageManagerFor(cwd) {
  if (fsSync.existsSync(path.join(cwd, 'pnpm-lock.yaml'))) return 'pnpm'
  if (fsSync.existsSync(path.join(cwd, 'yarn.lock'))) return 'yarn'
  return 'npm'
}

function commandLabel(manager, scriptName) {
  if (!scriptName) return ''
  if (manager === 'npm') return `npm run ${scriptName}`
  return `${manager} ${scriptName}`
}

function commandSpawnParts(manager, scriptName) {
  const exe = process.platform === 'win32' ? `${manager}.cmd` : manager
  if (manager === 'npm') return { exe, args: ['run', scriptName] }
  return { exe, args: [scriptName] }
}

function appendDevServerArgs(manager, baseArgs, scriptArgs) {
  if (!scriptArgs.length) return baseArgs
  if (manager === 'npm') return [...baseArgs, '--', ...scriptArgs]
  return [...baseArgs, ...scriptArgs]
}

function normalizePort(value, fallback) {
  const port = Number.parseInt(String(value || ''), 10)
  return Number.isInteger(port) && port > 0 && port < 65536 ? port : fallback
}

function preferredDevServerPort(detected, payload = {}) {
  const explicit = normalizePort(payload.port || process.env.METIS_PREVIEW_PORT, 0)
  if (explicit) return explicit
  const stack = String(detected.stack || '').toLowerCase()
  const command = String(detected.scriptCommand || '').toLowerCase()
  if (stack.includes('angular') || /\bng\s+serve\b/.test(command)) return 4200
  if (stack.includes('next') || stack.includes('create react app') || /\bnext\s+dev\b/.test(command)) return 3000
  return 5173
}

function isDevPortAvailable(port) {
  return new Promise(resolve => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.listen(port, DEV_SERVER_HOST, () => {
      server.close(() => resolve(true))
    })
  })
}

async function findAvailableDevPort(preferredPort, maxAttempts = 80) {
  const start = normalizePort(preferredPort, 5173)
  for (let offset = 0; offset < maxAttempts; offset += 1) {
    const port = start + offset
    if (port >= 65536) break
    if (await isDevPortAvailable(port)) return port
  }
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, DEV_SERVER_HOST, () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}

function devServerScriptArgs(detected, port) {
  const stack = String(detected.stack || '').toLowerCase()
  const command = String(detected.scriptCommand || '').toLowerCase()
  const portText = String(port)
  if (stack.includes('vite') || /\bvite\b/.test(command)) {
    return ['--host', DEV_SERVER_HOST, '--port', portText, '--strictPort']
  }
  if (stack.includes('next') || /\bnext\s+dev\b/.test(command)) {
    return ['--hostname', DEV_SERVER_HOST, '--port', portText]
  }
  if (stack.includes('angular') || /\bng\s+serve\b/.test(command)) {
    return ['--host', DEV_SERVER_HOST, '--port', portText]
  }
  if (/vue-cli-service\s+serve/.test(command)) {
    return ['--host', DEV_SERVER_HOST, '--port', portText]
  }
  return []
}

function devServerEnv(port) {
  const portText = String(port)
  return {
    ...process.env,
    HOST: DEV_SERVER_HOST,
    PORT: portText,
    VITE_PORT: portText,
    METIS_PREVIEW_PORT: portText,
    BROWSER: 'none',
  }
}

function detectFrontendStack(pkg) {
  const deps = {
    ...(pkg.dependencies && typeof pkg.dependencies === 'object' ? pkg.dependencies : {}),
    ...(pkg.devDependencies && typeof pkg.devDependencies === 'object' ? pkg.devDependencies : {})
  }
  if (deps.vite) return 'Vite'
  if (deps.next) return 'Next.js'
  if (deps['react-scripts']) return 'Create React App'
  if (deps['@angular/cli']) return 'Angular'
  if (deps.vue || deps['@vitejs/plugin-vue']) return 'Vue'
  if (deps.react || deps['@vitejs/plugin-react']) return 'React'
  return 'Frontend'
}

function detectFrontendProject(payload = {}) {
  const cwd = devServerCwd(payload.cwd)

  if (process.env.METIS_DESKTOP_SMOKE === '1') {
    return {
      ok: true,
      cwd,
      packagePath: path.join(cwd, 'package.json'),
      packageManager: 'npm',
      scriptName: 'dev',
      command: 'npm run dev',
      stack: 'Vite',
      reason: 'Smoke frontend project detected.',
      scripts: ['dev', 'start']
    }
  }

  const packagePath = path.join(cwd, 'package.json')
  if (!fsSync.existsSync(packagePath)) {
    return {
      ok: false,
      cwd,
      packagePath,
      packageManager: packageManagerFor(cwd),
      scriptName: '',
      command: '',
      stack: '',
      reason: '当前工作区没有 package.json，未识别到前端项目。',
      scripts: []
    }
  }

  let pkg = null
  try {
    pkg = JSON.parse(fsSync.readFileSync(packagePath, 'utf8'))
  } catch (error) {
    return {
      ok: false,
      cwd,
      packagePath,
      packageManager: packageManagerFor(cwd),
      scriptName: '',
      command: '',
      stack: '',
      reason: `package.json 解析失败: ${error?.message || String(error)}`,
      scripts: []
    }
  }

  const scriptsObject = pkg.scripts && typeof pkg.scripts === 'object' ? pkg.scripts : {}
  const scripts = Object.keys(scriptsObject)
  const scriptName = scripts.includes('dev') ? 'dev' : scripts.includes('start') ? 'start' : ''
  const packageManager = packageManagerFor(cwd)
  const stack = detectFrontendStack(pkg)
  const command = commandLabel(packageManager, scriptName)
  return {
    ok: Boolean(scriptName),
    cwd,
    packagePath,
    packageManager,
    scriptName,
    command,
    scriptCommand: scriptName ? String(scriptsObject[scriptName] || '') : '',
    stack,
    reason: scriptName ? `${stack} 项目已识别。` : `package.json 缺少 dev/start 脚本。可用 scripts: ${scripts.join(', ') || '无'}`,
    scripts
  }
}

const DEV_SERVER_URL_RE =
  /https?:\/\/(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\])(?::\d{1,5})?(?:\/[^\s<>"'`)\]}]*)?/i
const DEV_SERVER_HOST_PORT_RE =
  /(?:^|[\s([>])((?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[::1\]):\d{2,5}(?:\/[^\s<>"'`)\]}]*)?)/i

function normalizeDevServerUrl(value) {
  const raw = String(value || '').replace(/[.,;:!?]+$/g, '')
  try {
    const parsed = new URL(raw)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return ''
    const host = parsed.hostname.toLowerCase()
    if (!['localhost', '127.0.0.1', '0.0.0.0', '[::1]', '::1'].includes(host)) return ''
    if (host === '0.0.0.0') parsed.hostname = '127.0.0.1'
    return parsed.toString()
  } catch {
    return ''
  }
}

function findDevServerUrl(text) {
  const cleaned = String(text || '').replace(/\x1b\[[0-9;]*m/g, '')
  const full = cleaned.match(DEV_SERVER_URL_RE)?.[0]
  if (full) return normalizeDevServerUrl(full)
  const hostPort = cleaned.match(DEV_SERVER_HOST_PORT_RE)?.[1]
  if (hostPort) return normalizeDevServerUrl(`http://${hostPort}`)
  return ''
}

function appendDevServerLog(session, source, chunk) {
  const text = redactDiagnosticsText(String(chunk || ''))
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trimEnd()
    if (!line) continue
    session.status.logs.push(`[${source}] ${line}`)
    session.status.logs = boundedLines(session.status.logs)
    session.status.updatedAt = Date.now()
    emitDevServerEvent('log', session.status)
    const url = findDevServerUrl(line)
    if (url) {
      session.status.state = 'running'
      session.status.url = url
      session.status.reason = '已解析到本地预览地址。'
      session.status.updatedAt = Date.now()
      emitDevServerEvent('url', session.status)
    }
  }
}

async function startDevServer(payload = {}) {
  const detected = detectFrontendProject(payload)
  const cwd = detected.cwd
  const existing = devServers.get(cwd)
  if (existing && existing.proc && !existing.proc.killed && ['starting', 'running'].includes(existing.status.state)) {
    emitDevServerEvent('status', existing.status)
    return {
      ...existing.status,
      logs: boundedLines(existing.status.logs || [])
    }
  }

  if (!detected.ok) {
    const status = devServerStatusFromDetection(detected, 'error')
    status.logs = [detected.reason]
    emitDevServerEvent('error', status)
    return status
  }

  const status = devServerStatusFromDetection(detected, 'starting')
  status.startedAt = Date.now()
  status.logs = [`启动 ${detected.command} (cwd=${detected.cwd})`]

  if (process.env.METIS_DESKTOP_SMOKE === '1') {
    status.state = 'running'
    status.url = 'http://127.0.0.1:5173/'
    status.reason = 'Smoke dev server ready.'
    status.logs.push('Local: http://127.0.0.1:5173/')
    devServers.set(cwd, {
      status,
      proc: {
        killed: false,
        kill() {
          this.killed = true
        }
      }
    })
    emitDevServerEvent('url', status)
    return status
  }

  const preferredPort = preferredDevServerPort(detected, payload)
  const port = await findAvailableDevPort(preferredPort)
  const scriptArgs = devServerScriptArgs(detected, port)
  const { exe, args: baseArgs } = commandSpawnParts(detected.packageManager, detected.scriptName)
  const args = appendDevServerArgs(detected.packageManager, baseArgs, scriptArgs)
  status.previewPort = port
  status.reason = port === preferredPort ? `使用端口 ${port}。` : `端口 ${preferredPort} 被占用，已切换到 ${port}。`
  status.logs.push(`Preview port: ${port}${port === preferredPort ? '' : ` (preferred ${preferredPort} busy)`}`)
  if (scriptArgs.length) {
    status.logs.push(`Script args: ${scriptArgs.join(' ')}`)
  }
  const child = spawn(exe, args, {
    cwd: detected.cwd,
    env: devServerEnv(port),
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe']
  })

  const session = { proc: child, status }
  devServers.set(cwd, session)
  emitDevServerEvent('status', status)

  child.stdout.on('data', chunk => appendDevServerLog(session, 'dev', chunk.toString('utf8')))
  child.stderr.on('data', chunk => appendDevServerLog(session, 'dev:err', chunk.toString('utf8')))
  child.on('error', error => {
    status.state = 'error'
    status.reason = error?.message || String(error)
    status.logs.push(`进程启动失败: ${status.reason}`)
    status.updatedAt = Date.now()
    emitDevServerEvent('error', status)
  })
  child.on('exit', (code, signal) => {
    status.exitCode = code
    status.state = status.url ? 'exited' : 'error'
    status.reason = `dev server 已退出 code=${code ?? 'null'} signal=${signal ?? 'null'}`
    status.logs.push(status.reason)
    status.updatedAt = Date.now()
    emitDevServerEvent(status.state === 'error' ? 'error' : 'exit', status)
  })

  return {
    ...status,
    logs: boundedLines(status.logs || [])
  }
}

function devServerStatus(payload = {}) {
  const cwd = devServerCwd(payload.cwd)
  const existing = devServers.get(cwd)
  if (existing) {
    return {
      ...existing.status,
      logs: boundedLines(existing.status.logs || [])
    }
  }
  const detected = detectFrontendProject({ cwd })
  return detected.ok ? devServerStatusFromDetection(detected, 'detected') : devServerIdleStatus(cwd, detected.reason)
}

function stopDevServer(payload = {}) {
  const cwd = devServerCwd(payload.cwd)
  const existing = devServers.get(cwd)
  if (!existing) {
    return devServerStatus(payload)
  }
  try {
    existing.proc?.kill?.('SIGTERM')
  } catch {}
  existing.status.state = 'exited'
  existing.status.reason = '已停止 dev server。'
  existing.status.updatedAt = Date.now()
  existing.status.logs.push(existing.status.reason)
  emitDevServerEvent('exit', existing.status)
  return {
    ...existing.status,
    logs: boundedLines(existing.status.logs || [])
  }
}

function stopAllDevServers() {
  for (const session of devServers.values()) {
    try {
      session.proc?.kill?.('SIGTERM')
    } catch {}
  }
  devServers.clear()
}

function redactAuditUrl(value) {
  try {
    const parsed = new URL(String(value || ''))
    for (const [key] of parsed.searchParams) {
      if (/token|key|secret|auth|code/i.test(key)) {
        parsed.searchParams.set(key, '***')
      }
    }
    return parsed.toString()
  } catch {
    return redactDiagnosticsText(value)
  }
}

async function savePreviewEvidence(payload = {}) {
  const capturedAt = new Date().toISOString()
  const url = redactAuditUrl(payload.url || '')
  const error = redactDiagnosticsText(payload.error || '')
  const loading = Boolean(payload.loading)
  const screenshotDataUrl = String(payload.screenshotDataUrl || '')
  const screenshotBase64 = screenshotDataUrl.replace(/^data:image\/png;base64,/i, '')
  const screenshotAvailable = /^data:image\/png;base64,/i.test(screenshotDataUrl) && screenshotBase64.length > 0
  let status = 'ok'
  let reason = '预览页已加载，未发现明显错误。'
  if (!url) {
    status = 'error'
    reason = '当前没有可验收的网页 URL。'
  } else if (error) {
    status = 'error'
    reason = error
  } else if (loading) {
    status = 'warning'
    reason = '网页仍在加载，建议加载完成后重新验收。'
  }

  const outputDir = path.join(app.getPath('userData'), 'preview-evidence')
  await fs.mkdir(outputDir, { recursive: true })
  const filename = process.env.METIS_DESKTOP_SMOKE === '1'
    ? 'preview-evidence-smoke'
    : `preview-evidence-${capturedAt.replace(/[:.]/g, '-')}`
  let screenshotPath = ''
  if (screenshotAvailable) {
    screenshotPath = path.join(outputDir, `${filename}.png`)
    await fs.writeFile(screenshotPath, Buffer.from(screenshotBase64, 'base64'))
  }
  const result = {
    ok: status === 'ok',
    status,
    reason,
    url,
    title: redactDiagnosticsText(payload.title || ''),
    savedPath: path.join(outputDir, `${filename}.json`),
    screenshotPath,
    capturedAt,
    screenshotAvailable
  }
  const evidence = {
    schema: 'metis.preview_evidence.v1',
    result,
    tab: {
      url,
      title: result.title,
      loading,
      error,
      zoom: Number.isFinite(payload.zoom) ? payload.zoom : 1
    }
  }
  await fs.writeFile(result.savedPath, JSON.stringify(evidence, null, 2), 'utf8')
  return result
}

async function startBackendWithEvents({ reset = false } = {}) {
  if (bootRunning) {
    return bootState()
  }

  if (reset) {
    clearBackendRestartTimer()
    backendRestartAttempts = 0
    bootEvents.length = 0
    bootError = null
    backendPort = null
  }

  bootRunning = true
  bootStatus = 'starting'
  emitBootEvent({ phase: 'starting', title: '正在启动 Metis 后端' })

  try {
    const port = await startBackend(emitBootEvent)
    backendPort = port
    bootStatus = 'ready'
    startPreviewBridgeLoop()
  } catch (error) {
    bootStatus = 'error'
    backendPort = null
    stopPreviewBridgeLoop()
    if (!error?.title) {
      emitBootEvent({
        phase: 'error',
        title: '后端启动失败',
        detail: error?.message || String(error),
        logTail: tailBackendLog()
      })
    }
  } finally {
    bootRunning = false
  }

  return bootState()
}

function iconPath(filename = 'logo.png') {
  const devPath = path.join(__dirname, '..', 'resources', 'icons', filename)
  if (process.env.METIS_DESKTOP_DEV_SERVER) {
    return devPath
  }
  return path.join(process.resourcesPath, 'icons', filename)
}

function broadcastWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }

  mainWindow.webContents.send('metis:window-state', {
    isMaximized: mainWindow.isMaximized(),
    isFullScreen: mainWindow.isFullScreen()
  })
}

async function createWindow() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    showWindow()
    return
  }

  mainWindow = new BrowserWindow({
    width: 1240,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    title: 'Metis',
    frame: false,
    show: false,
    icon: iconPath('logo.ico'),
    backgroundColor: '#0A0A0E',
    webPreferences: {
      ...HARDENED_WEB_PREFERENCES,
      preload: path.join(__dirname, 'preload.cjs'),
    }
  })

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show()
    broadcastWindowState()
  })

  // ── Lock main-window zoom ──────────────────────────────────────
  // The app UI should never zoom — only the hosted preview view has
  // its own zoom via setZoomFactor.  Block Ctrl+/-/0
  // and the default Electron View menu from affecting the main window.
  mainWindow.webContents.on('before-input-event', (_event, input) => {
    if ((input.control || input.meta) && !input.alt && !input.shift) {
      if (input.key === '+' || input.key === '=' || input.key === '-' || input.key === '0') {
        _event.preventDefault()
      }
    }
  })
  // Also reset if something else changes it (e.g. pinch gesture)
  mainWindow.webContents.on('did-finish-load', () => {
    try { mainWindow?.webContents.setZoomFactor(1) } catch {}
  })
  // Remove default Electron menu (has View→Zoom In/Out items).
  // We don't need a menu bar — the app is frameless (frame: false).
  Menu.setApplicationMenu(null)

  mainWindow.on('maximize', broadcastWindowState)
  mainWindow.on('unmaximize', broadcastWindowState)
  mainWindow.on('enter-full-screen', broadcastWindowState)
  mainWindow.on('leave-full-screen', broadcastWindowState)
  mainWindow.on('restore', broadcastWindowState)

  mainWindow.on('close', event => {
    if (!app.isQuitting) {
      event.preventDefault()
      hidePreviewView()
      mainWindow.hide()
    }
  })

  mainWindow.on('closed', () => {
    disposePreviewView()
    mainWindow = null
  })

  mainWindow.webContents.setWindowOpenHandler(details => {
    openExternalSafe(details.url)
    return { action: 'deny' }
  })

  mainWindow.webContents.on('will-navigate', (event, url) => {
    const devUrl = process.env.METIS_DESKTOP_DEV_SERVER
    if (isAllowedAppNavigation(url, devUrl || '')) {
      return
    }
    event.preventDefault()
    openExternalSafe(url)
  })

  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log(`[renderer] gone reason=${details.reason} exitCode=${details.exitCode}`)
  })
  mainWindow.webContents.on('unresponsive', () => log('[renderer] unresponsive'))
  mainWindow.webContents.on('did-finish-load', () => {
    for (const event of bootEvents) {
      mainWindow?.webContents.send('metis:boot-event', event)
    }
    if (bootStatus === 'idle') {
      void startBackendWithEvents()
    }
  })

  if (process.env.METIS_DESKTOP_DEV_SERVER) {
    await mainWindow.loadURL(process.env.METIS_DESKTOP_DEV_SERVER)
  } else {
    await mainWindow.loadURL(pathToFileURL(path.join(__dirname, '..', 'dist', 'index.html')).toString())
  }
}

function createTray() {
  if (tray) {
    return
  }

  tray = new Tray(iconPath('logo.png'))
  tray.setToolTip('Metis')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '显示 Metis', click: () => showWindow() },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          app.isQuitting = true
          app.quit()
        }
      }
    ])
  )
  tray.on('double-click', showWindow)
}

function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore()
  }
  mainWindow.show()
  mainWindow.focus()
}

// 重新检查间隔：app 常驻托盘运行数天是常态（closeBehavior=tray），只在启动时查一次
// 等于几乎永远不会发现新版本——所以这里要周期性重新检查。
const UPDATE_RECHECK_INTERVAL_MS = 4 * 60 * 60 * 1000

function triggerUpdateCheck() {
  if (!autoUpdater) return
  autoUpdater.checkForUpdatesAndNotify().catch(error => {
    log(`[update] check skipped: ${error?.message || error}`)
    emitUpdateEvent({ status: 'error', message: error?.message || String(error) })
  })
}

function configureAutoUpdates() {
  if (!app.isPackaged || !autoUpdater) {
    return
  }

  // electron-builder's `publish: { provider: github, ... }` config already bakes
  // a default feed (app-update.yml) into the build, so autoUpdater works without
  // setFeedURL out of the box. METIS_UPDATE_URL stays as an optional override for
  // a private/generic update server — it is no longer required to enable
  // auto-updates at all (that was the actual P0 bug: without it, this whole
  // function used to silently do nothing and the manual "check updates" button
  // fell through to "go download it from GitHub yourself").
  const updateUrl = process.env.METIS_UPDATE_URL
  if (updateUrl) {
    autoUpdater.setFeedURL({ provider: 'generic', url: updateUrl })
  }

  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.on('checking-for-update', () => {
    log('[update] checking')
    emitUpdateEvent({ status: 'checking' })
  })
  autoUpdater.on('update-available', info => {
    log(`[update] available ${info?.version || ''}`)
    emitUpdateEvent({ status: 'available', version: info?.version || '' })
  })
  autoUpdater.on('update-not-available', info => {
    log(`[update] not available ${info?.version || ''}`)
    emitUpdateEvent({ status: 'not-available' })
  })
  autoUpdater.on('download-progress', progress => {
    emitUpdateEvent({ status: 'downloading', percent: Math.round(progress?.percent || 0) })
  })
  autoUpdater.on('error', error => {
    log(`[update] error ${error?.message || error}`)
    emitUpdateEvent({ status: 'error', message: error?.message || String(error) })
  })
  autoUpdater.on('update-downloaded', info => {
    log(`[update] downloaded ${info?.version || ''}`)
    pendingUpdateInfo = info || {}
    emitUpdateEvent({ status: 'downloaded', version: info?.version || '' })
  })

  setTimeout(triggerUpdateCheck, 5000)
  clearInterval(updateCheckTimer)
  updateCheckTimer = setInterval(triggerUpdateCheck, UPDATE_RECHECK_INTERVAL_MS)
}

function terminalCwd(value) {
  const candidate = String(value || '').trim()
  if (candidate) {
    try {
      if (
        fsSync.existsSync(candidate) &&
        fsSync.statSync(candidate).isDirectory() &&
        !isPackagedBackendTerminalCwd(candidate)
      ) {
        return candidate
      }
    } catch {}
  }
  return app.getPath('home')
}

function isPackagedBackendTerminalCwd(value) {
  const normalized = path.normalize(String(value || '')).toLowerCase()
  return (
    normalized.includes(`${path.sep}resources${path.sep}backend-dist${path.sep}metis-backend`) ||
    normalized.includes(`${path.sep}backend-dist${path.sep}metis-backend`) ||
    normalized.endsWith(`${path.sep}resources${path.sep}backend-dist`) ||
    normalized.endsWith(`${path.sep}backend-dist`)
  )
}

function powershellExe() {
  const systemRoot = process.env.SystemRoot || process.env.WINDIR || ''
  if (systemRoot) {
    const candidate = path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    if (fsSync.existsSync(candidate)) return candidate
  }
  return 'powershell.exe'
}

function normalizeTerminalShell(shellName) {
  const shellId = String(shellName || 'powershell').toLowerCase()
  return ['powershell', 'cmd', 'bash', 'sh', 'shell'].includes(shellId) ? shellId : 'powershell'
}

function cmdTerminalProfile(shell = 'cmd') {
  return {
    shell,
    exe: process.env.ComSpec || 'cmd.exe',
    args: command => ['/d', '/s', '/c', command],
    liveArgs: ['/d', '/q']
  }
}

function posixTerminalProfile(shell, exe) {
  return {
    shell,
    exe,
    args: command => ['-lc', command],
    liveArgs: ['-i']
  }
}

function terminalProfile(shellName) {
  const shellId = normalizeTerminalShell(shellName)
  if (shellId === 'cmd') {
    return cmdTerminalProfile('cmd')
  }
  if (shellId === 'bash') {
    return posixTerminalProfile('bash', 'bash')
  }
  if (shellId === 'sh') {
    return posixTerminalProfile('sh', 'sh')
  }
  if (shellId === 'shell') {
    if (process.platform === 'win32') return cmdTerminalProfile('shell')
    return posixTerminalProfile('shell', process.env.SHELL || 'sh')
  }
  return {
    shell: 'powershell',
    exe: powershellExe(),
    args: command => ['-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
    liveArgs: ['-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit']
  }
}

let terminalGbDecoder = null

function decodeTerminalChunk(chunk) {
  try {
    terminalGbDecoder = terminalGbDecoder || new TextDecoder('gb18030')
    return terminalGbDecoder.decode(chunk)
  } catch {
    return Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk || '')
  }
}

function appendBounded(buffer, chunk) {
  const next = `${buffer}${chunk}`
  return next.length > TERMINAL_OUTPUT_LIMIT ? next.slice(next.length - TERMINAL_OUTPUT_LIMIT) : next
}

function sendTerminalEvent(payload) {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return
  }
  mainWindow.webContents.send('metis:terminal-event', payload)
}

function emitTerminalExit(session, code = null, signal = null) {
  if (!session || session.exited) {
    return
  }
  session.exited = true
  terminalSessions.delete(session.id)
  sendTerminalEvent({ id: session.id, type: 'exit', code, signal })
}

function appendTerminalOutput(session, data) {
  const text = String(data || '')
  const current = session.output || ''
  const remaining = TERMINAL_LIVE_OUTPUT_LIMIT - current.length
  if (remaining <= 0) return
  const accepted = text.slice(0, remaining)
  session.output = current + accepted
  sendTerminalEvent({ id: session.id, type: 'data', data: accepted })
}

function createTerminalSession(payload = {}) {
  const cwd = terminalCwd(payload.cwd)
  const profile = terminalProfile(payload.shell)
  const cols = Number.isFinite(payload.cols) ? Math.max(20, Math.floor(payload.cols)) : 100
  const rows = Number.isFinite(payload.rows) ? Math.max(6, Math.floor(payload.rows)) : 24
  const id = `term-${Date.now()}-${terminalCounter++}`
  const startedAt = Date.now()
  const backend = nodePty ? 'pty' : 'shell'
  const session = {
    id,
    cwd,
    shell: profile.shell,
    backend,
    startedAt,
    output: '',
    exited: false,
    proc: null,
    write(data) {
      if (this.backend === 'pty') {
        this.proc?.write(String(data || ''))
        return
      }
      if (String(data || '') === '\x03') {
        appendTerminalOutput(this, '^C\r\n')
        return
      }
      this.proc?.stdin?.write(String(data || ''))
    },
    resize(nextCols, nextRows) {
      if (this.backend !== 'pty' || !this.proc?.resize) return
      this.proc.resize(Math.max(20, nextCols || cols), Math.max(6, nextRows || rows))
    },
    kill() {
      try {
        if (this.backend === 'pty') {
          this.proc?.kill()
        } else {
          this.proc?.kill()
        }
      } catch {}
    }
  }

  terminalSessions.set(id, session)

  if (nodePty) {
    const ptyProcess = nodePty.spawn(profile.exe, profile.liveArgs, {
      name: 'xterm-256color',
      cols,
      rows,
      cwd,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1'
      }
    })
    session.proc = ptyProcess
    ptyProcess.onData(data => appendTerminalOutput(session, data))
    ptyProcess.onExit(event => {
      emitTerminalExit(session, event.exitCode ?? null, event.signal ?? null)
    })
  } else {
    const child = spawn(profile.exe, profile.liveArgs, {
      cwd,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1'
      },
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe']
    })
    session.proc = child
    child.stdout.on('data', chunk => appendTerminalOutput(session, decodeTerminalChunk(chunk)))
    child.stderr.on('data', chunk => appendTerminalOutput(session, decodeTerminalChunk(chunk)))
    child.on('error', error => {
      appendTerminalOutput(session, `${error?.message || String(error)}\r\n`)
      sendTerminalEvent({ id, type: 'error', data: error?.message || String(error) })
    })
    child.on('close', (code, signal) => {
      emitTerminalExit(session, code, signal)
    })
  }

  setTimeout(() => {
    sendTerminalEvent({
      id,
      type: 'ready',
      cwd,
      shell: profile.shell,
      backend
    })
  }, 0)

  return {
    id,
    cwd,
    shell: profile.shell,
    backend,
    startedAt
  }
}

function inputTerminalSession(sessionId, data) {
  const session = terminalSessions.get(String(sessionId || ''))
  if (!session) {
    return { ok: false }
  }
  session.write(data)
  return { ok: true }
}

function resizeTerminalSession(sessionId, cols, rows) {
  const session = terminalSessions.get(String(sessionId || ''))
  if (!session) {
    return { ok: false }
  }
  session.resize(Number(cols), Number(rows))
  return { ok: true }
}

function killTerminalSession(sessionId) {
  const session = terminalSessions.get(String(sessionId || ''))
  if (!session) {
    return { ok: false }
  }
  session.kill()
  emitTerminalExit(session, null, 'killed')
  return { ok: true }
}

function killAllTerminalSessions() {
  for (const session of terminalSessions.values()) {
    session.kill()
    emitTerminalExit(session, null, 'killed')
  }
  terminalSessions.clear()
}

function runTerminalCommand(payload = {}) {
  const command = String(payload.command || '').trim()
  const cwd = terminalCwd(payload.cwd)
  const profile = terminalProfile(payload.shell)
  if (!command) {
    return Promise.resolve({
      ok: false,
      command,
      cwd,
      shell: profile.shell,
      stdout: '',
      stderr: 'Empty command',
      exitCode: null,
      timedOut: false,
      durationMs: 0,
      error: 'Empty command'
    })
  }

  return new Promise(resolve => {
    const started = Date.now()
    let stdout = ''
    let stderr = ''
    let settled = false
    const child = spawn(profile.exe, profile.args(command), {
      cwd,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1'
      },
      windowsHide: true
    })

    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      child.kill()
      resolve({
        ok: false,
        command,
        cwd,
        shell: profile.shell,
        stdout,
        stderr: appendBounded(stderr, `\nTimed out after ${TERMINAL_TIMEOUT_MS / 1000}s.`),
        exitCode: null,
        timedOut: true,
        durationMs: Date.now() - started,
        error: 'Timed out'
      })
    }, TERMINAL_TIMEOUT_MS)

    child.stdout.on('data', chunk => {
      stdout = appendBounded(stdout, decodeTerminalChunk(chunk))
    })
    child.stderr.on('data', chunk => {
      stderr = appendBounded(stderr, decodeTerminalChunk(chunk))
    })
    child.on('error', error => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({
        ok: false,
        command,
        cwd,
        shell: profile.shell,
        stdout,
        stderr,
        exitCode: null,
        timedOut: false,
        durationMs: Date.now() - started,
        error: error?.message || String(error)
      })
    })
    child.on('close', code => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve({
        ok: code === 0,
        command,
        cwd,
        shell: profile.shell,
        stdout,
        stderr,
        exitCode: code,
        timedOut: false,
        durationMs: Date.now() - started
      })
    })
  })
}

// ── Takeover overlay (FABLEADV-21) ─────────────────────────────────
// 模型接管真实键鼠时，全屏四边浮现金色光晕 + 右下角急停胶囊。
// 两个窗口：glow 全屏点击穿透（纯视觉），pill 小窗也点击穿透（纯提示）。
// 二者都 setContentProtection(true)：用户肉眼可见，但截图 API 抓不到，
// 因此对智能体的视觉判断天然隐形，不污染 desktop_screenshot。
let overlayGlowWindow = null
let overlayPillWindow = null
let overlayActive = false
const overlayOpacityTimers = new WeakMap()

function displaysUnionBounds() {
  const displays = screen.getAllDisplays()
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const d of displays) {
    const b = d.bounds
    minX = Math.min(minX, b.x)
    minY = Math.min(minY, b.y)
    maxX = Math.max(maxX, b.x + b.width)
    maxY = Math.max(maxY, b.y + b.height)
  }
  if (!Number.isFinite(minX)) {
    const primary = screen.getPrimaryDisplay().bounds
    return { x: primary.x, y: primary.y, width: primary.width, height: primary.height }
  }
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY }
}

function rampOpacity(win, from, to, ms, done) {
  if (!win || win.isDestroyed()) return
  const existing = overlayOpacityTimers.get(win)
  if (existing) clearInterval(existing)
  const steps = Math.max(1, Math.round(ms / 30))
  let step = 0
  win.setOpacity(from)
  const timer = setInterval(() => {
    step += 1
    const t = step / steps
    if (win.isDestroyed()) { clearInterval(timer); return }
    win.setOpacity(from + (to - from) * t)
    if (step >= steps) {
      clearInterval(timer)
      overlayOpacityTimers.delete(win)
      win.setOpacity(to)
      if (done) done()
    }
  }, 30)
  overlayOpacityTimers.set(win, timer)
}

function ensureOverlayWindows() {
  if (!overlayGlowWindow || overlayGlowWindow.isDestroyed()) {
    overlayGlowWindow = new BrowserWindow({
      show: false,
      transparent: true,
      frame: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      skipTaskbar: true,
      focusable: false,
      hasShadow: false,
      fullscreenable: false,
      webPreferences: { ...HARDENED_WEB_PREFERENCES }
    })
    overlayGlowWindow.setAlwaysOnTop(true, 'screen-saver')
    overlayGlowWindow.setIgnoreMouseEvents(true, { forward: true })
    try { overlayGlowWindow.setContentProtection(true) } catch {}
    try { overlayGlowWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }) } catch {}
    overlayGlowWindow.loadFile(path.join(__dirname, 'overlay.html')).catch(() => {})
  }
  if (!overlayPillWindow || overlayPillWindow.isDestroyed()) {
    overlayPillWindow = new BrowserWindow({
      width: 318,
      height: 74,
      show: false,
      transparent: true,
      frame: false,
      resizable: false,
      movable: false,
      skipTaskbar: true,
      hasShadow: false,
      fullscreenable: false,
      webPreferences: { ...HARDENED_WEB_PREFERENCES }
    })
    overlayPillWindow.setAlwaysOnTop(true, 'screen-saver')
    overlayPillWindow.setIgnoreMouseEvents(true, { forward: true })
    try { overlayPillWindow.setContentProtection(true) } catch {}
    overlayPillWindow.loadFile(path.join(__dirname, 'overlay-pill.html')).catch(() => {})
  }
}

function positionOverlayWindows() {
  const union = displaysUnionBounds()
  if (overlayGlowWindow && !overlayGlowWindow.isDestroyed()) {
    overlayGlowWindow.setBounds(union)
  }
  if (overlayPillWindow && !overlayPillWindow.isDestroyed()) {
    const work = screen.getPrimaryDisplay().workArea
    overlayPillWindow.setBounds({
      width: 318,
      height: 74,
      x: work.x + work.width - 338,
      y: work.y + work.height - 94
    })
  }
}

function showTakeoverOverlay() {
  ensureOverlayWindows()
  positionOverlayWindows()
  if (overlayActive) return
  overlayActive = true
  for (const win of [overlayGlowWindow, overlayPillWindow]) {
    if (!win || win.isDestroyed()) continue
    win.setOpacity(0)
    win.showInactive()
    rampOpacity(win, 0, 1, 500)
  }
}

function hideTakeoverOverlay() {
  if (!overlayActive) return
  overlayActive = false
  for (const win of [overlayGlowWindow, overlayPillWindow]) {
    if (!win || win.isDestroyed()) continue
    rampOpacity(win, 1, 0, 400, () => { try { if (!win.isDestroyed()) win.hide() } catch {} })
  }
}

function destroyOverlayWindows() {
  for (const win of [overlayGlowWindow, overlayPillWindow]) {
    try { if (win && !win.isDestroyed()) win.destroy() } catch {}
  }
  overlayGlowWindow = null
  overlayPillWindow = null
  overlayActive = false
}

ipcMain.handle('metis:overlay-set-active', (_event, active) => {
  if (active) showTakeoverOverlay()
  else hideTakeoverOverlay()
  return overlayActive
})

ipcMain.handle('metis:overlay-stop', () => {
  try { mainWindow?.webContents.send('metis:takeover-stop') } catch {}
  hideTakeoverOverlay()
  return true
})

app.on('before-quit', () => { destroyOverlayWindows() })

ipcMain.handle('metis:backend-port', () => backendPort)
ipcMain.handle('metis:boot-state', () => bootState())
ipcMain.handle('metis:retry-backend', () => {
  clearBackendRestartTimer()
  backendRestartAttempts = 0
  stopBackend()
  void startBackendWithEvents({ reset: true })
  return { ok: true }
})
ipcMain.handle('metis:open-log', async () => {
  const logPath = getBackendLogPath()
  await fs.mkdir(path.dirname(logPath), { recursive: true })
  await fs.appendFile(logPath, '', 'utf8')
  const error = await shell.openPath(logPath)
  return { ok: !error, path: logPath, error }
})
ipcMain.handle('metis:app-info', () => ({
  name: app.getName(),
  version: app.getVersion(),
  packaged: app.isPackaged,
  updateUrl: process.env.METIS_UPDATE_URL || '',
  githubHome: GITHUB_HOME,
  fakeBackend: process.env.METIS_FAKE_BACKEND === '1',
  storage: resolveDataRootInfo()
}))
ipcMain.handle('metis:diagnostics', () => diagnosticsPayload())
ipcMain.handle('metis:set-native-theme', (_event, mode) => {
  const next = ['light', 'dark', 'system'].includes(mode) ? mode : 'system'
  nativeTheme.themeSource = next
  return { ok: true, themeSource: nativeTheme.themeSource, shouldUseDarkColors: nativeTheme.shouldUseDarkColors }
})
ipcMain.handle('metis:save-diagnostics-bundle', () => saveDiagnosticsBundle())
ipcMain.handle('metis:dev-server-detect', (_event, payload = {}) => detectFrontendProject(payload))
ipcMain.handle('metis:dev-server-start', (_event, payload = {}) => startDevServer(payload))
ipcMain.handle('metis:dev-server-stop', (_event, payload = {}) => stopDevServer(payload))
ipcMain.handle('metis:dev-server-status', (_event, payload = {}) => devServerStatus(payload))
ipcMain.handle('metis:save-preview-evidence', (_event, payload = {}) => savePreviewEvidence(payload))
ipcMain.handle('metis:preview-set-bounds', (_event, payload = {}) => {
  // 有 DOM 浮层挡着时，不移动原生视图（否则 ResizeObserver 会把它又显示到弹窗上面），
  // 但仍要记录渲染端的最新意图——否则遮挡期间关掉预览，解除遮挡后会用旧位置把网页又恢复出来（残留）。
  const intent = previewBoundsIntent(payload)
  if (previewOccluded) {
    lastPreviewBounds = intent.bounds
    if (!intent.visible) hidePreviewView()
    return { ok: true, occluded: true }
  }
  if (!intent.visible) {
    // Renderer explicitly closed/hidden Preview (tab/card/mode switch). Treat
    // that as the latest visibility intent, otherwise previewSetOccluded(false)
    // can resurrect the old BrowserView bounds after the app returns from the
    // background or an overlay closes.
    lastPreviewBounds = null
    hidePreviewView()
    if (intent.hiddenBounds) return { ok: true, bounds: intent.hiddenBounds, hidden: true }
    return { ok: true }
  }
  const tabId = String(payload.tabId || '')
  if (tabId && tabId !== previewTabId) {
    return { ok: true, skipped: true, tabId, activeTabId: previewTabId }
  }
  const view = ensurePreviewView()
  if (!view) return { ok: false, error: 'preview view unavailable' }
  const bounds = intent.bounds
  // 去重：渲染端一次同步会连发多帧/定时器调用，位置没变就别重定位原生视图（消除闪烁）。
  const key = intent.key
  if (key === lastPreviewBoundsKey) {
    return { ok: true, bounds, deduped: true }
  }
  lastPreviewBoundsKey = key
  lastPreviewBounds = bounds
  view.setBounds(bounds)
  try { view.setVisible?.(true) } catch {}
  return { ok: true, bounds }
})
ipcMain.handle('metis:preview-set-occluded', (_event, value) => {
  setPreviewOccluded(value)
  return { ok: true, occluded: previewOccluded }
})
ipcMain.handle('metis:preview-load', (_event, payload = {}) => loadPreviewUrl(payload.url, payload.tabId))
ipcMain.handle('metis:preview-command', (_event, command) => {
  const webContents = previewView?.webContents
  if (!webContents || webContents.isDestroyed()) return { ok: false }
  if (command === 'back' && webContents.canGoBack()) webContents.goBack()
  if (command === 'forward' && webContents.canGoForward()) webContents.goForward()
  if (command === 'reload') webContents.reload()
  if (command === 'stop') webContents.stop()
  return { ok: true }
})
ipcMain.handle('metis:preview-set-zoom', (_event, zoom) => {
  const webContents = previewView?.webContents
  if (!webContents || webContents.isDestroyed()) return { ok: false }
  const factor = Math.min(Math.max(Number(zoom) || 1, 0.5), 2)
  webContents.setZoomFactor(factor)
  return { ok: true, zoom: factor }
})
ipcMain.handle('metis:preview-capture', () => capturePreviewPage())
ipcMain.handle('metis:preview-observe', (_event, payload = {}) => observePreviewPage(payload))
ipcMain.handle('metis:preview-action', (_event, payload = {}) => performPreviewAction(payload))
ipcMain.handle('metis:preview-activity', (_event, payload = {}) => previewActivityPayload(payload.limit))
ipcMain.handle('metis:check-updates', async () => {
  const current = app.getVersion()

  if (pendingUpdateInfo) {
    return {
      ok: true,
      status: 'downloaded',
      message: `新版本 v${pendingUpdateInfo.version || ''} 已下载完成，点击重启以更新。`,
    }
  }

  // 1) 默认主路径：electron-updater，走 electron-builder 打包时生成的 GitHub
  //    发布源（app-update.yml），不需要 METIS_UPDATE_URL 才能用——这是本来
  //    的 P0 bug：之前只有配置了私有更新源这个分支才会真的自动下载，普通用
  //    户走的是下面分支 2，只会被指向"自己去 GitHub 下载"，从不会自动更新。
  //    METIS_UPDATE_URL 仍保留，作为覆盖到私有/通用更新服务器的可选项。
  if (app.isPackaged && autoUpdater) {
    const updateUrl = process.env.METIS_UPDATE_URL
    if (updateUrl) {
      autoUpdater.setFeedURL({ provider: 'generic', url: updateUrl })
    }
    try {
      const result = await autoUpdater.checkForUpdatesAndNotify()
      const v = result?.updateInfo?.version
      return v && compareVersions(v, current) > 0
        ? { ok: true, status: 'available', message: `发现新版本 v${v}，正在后台下载。` }
        : { ok: true, status: 'latest', message: `当前已是最新版本 (v${current})。` }
    } catch (error) {
      return { ok: false, status: 'error', message: error?.message || String(error) }
    }
  }

  // 2) 开发模式 / electron-updater 不可用时的兜底：直接查 GitHub Releases。
  try {
    const resp = await fetch(`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/releases/latest`, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'Metis' },
      signal: AbortSignal.timeout(8000),
    })
    if (resp.status === 404) {
      return { ok: true, status: 'latest', message: `当前已是最新版本 (v${current})。` }
    }
    if (!resp.ok) {
      return { ok: false, status: 'error', message: `检查更新失败 (HTTP ${resp.status})。` }
    }
    const data = await resp.json()
    const latest = String(data.tag_name || data.name || '').replace(/^v/i, '')
    if (latest && compareVersions(latest, current) > 0) {
      return { ok: true, status: 'available', message: `发现新版本 v${latest}，前往 GitHub 下载。`, url: data.html_url || `${GITHUB_HOME}/releases` }
    }
    return { ok: true, status: 'latest', message: `当前已是最新版本 (v${current})。` }
  } catch {
    return { ok: false, status: 'error', message: '无法连接 GitHub 检查更新，请稍后再试。' }
  }
})

ipcMain.handle('metis:install-update', () => {
  if (!pendingUpdateInfo || !autoUpdater) {
    return { ok: false, message: '没有已下载好的更新可以安装。' }
  }
  app.isQuitting = true
  // isSilent/isForceRunAfter: 不再弹 NSIS 安装界面，装完直接重新启动 Metis。
  setImmediate(() => autoUpdater.quitAndInstall(true, true))
  return { ok: true }
})

ipcMain.handle('metis:window', (_event, action) => {
  if (!mainWindow || mainWindow.isDestroyed()) {
    return { ok: false }
  }

  if (action === 'minimize') {
    mainWindow.minimize()
  } else if (action === 'toggle-maximize') {
    mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize()
  } else if (action === 'hide') {
    mainWindow.hide()
  } else if (action === 'quit') {
    app.isQuitting = true
    app.quit()
  }

  broadcastWindowState()
  return { ok: true }
})

ipcMain.handle('metis:pick-folder', async () => {
  if (!mainWindow) {
    return null
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('metis:pick-python-exe', async () => {
  if (!mainWindow) {
    return null
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择 Python 解释器',
    properties: ['openFile'],
    filters: process.platform === 'win32'
      ? [{ name: 'Python', extensions: ['exe'] }, { name: '所有文件', extensions: ['*'] }]
      : [{ name: '所有文件', extensions: ['*'] }],
    defaultPath: process.platform === 'win32'
      ? (process.env.LOCALAPPDATA || 'C:\\')
      : '/usr/bin',
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('metis:save-file', async (_event, payload = {}) => {
  if (!mainWindow) {
    return { canceled: true }
  }
  const result = await dialog.showSaveDialog(mainWindow, {
    defaultPath: payload.defaultPath || 'metis-export.md',
    filters: payload.filters || [{ name: 'Markdown', extensions: ['md'] }]
  })
  if (result.canceled || !result.filePath) {
    return { canceled: true }
  }
  await fs.writeFile(result.filePath, String(payload.content || ''), 'utf8')
  return { canceled: false, path: result.filePath }
})

ipcMain.handle('metis:open-external', (_event, url) => {
  return { ok: openExternalSafe(url) }
})

ipcMain.handle('metis:open-path', async (_event, targetPath) => {
  const raw = String(targetPath || '').trim()
  if (!raw || /^[a-z]+:\/\//i.test(raw)) {
    return { ok: false, error: 'invalid local path' }
  }
  const resolved = path.resolve(raw)
  if (!fsSync.existsSync(resolved)) {
    return { ok: false, error: 'path does not exist', path: resolved }
  }
  try {
    const stat = fsSync.statSync(resolved)
    if (stat.isDirectory()) {
      const error = await shell.openPath(resolved)
      return { ok: !error, path: resolved, error: error || undefined }
    }
    const error = await shell.openPath(resolved)
    return { ok: !error, path: resolved, error: error || undefined }
  } catch (error) {
    return { ok: false, path: resolved, error: error?.message || String(error) }
  }
})
ipcMain.handle('metis:terminal-run', (_event, payload = {}) => runTerminalCommand(payload))
ipcMain.handle('metis:terminal-create', (_event, payload = {}) => createTerminalSession(payload))
ipcMain.handle('metis:terminal-input', (_event, sessionId, data) => inputTerminalSession(sessionId, data))
ipcMain.handle('metis:terminal-resize', (_event, sessionId, cols, rows) => resizeTerminalSession(sessionId, cols, rows))
ipcMain.handle('metis:terminal-kill', (_event, sessionId) => killTerminalSession(sessionId))
ipcMain.handle('metis:smoke-result', (_event, payload = {}) => {
  const result = {
    ok: Boolean(payload.ok),
    checks: Array.isArray(payload.checks) ? payload.checks : [],
    error: payload.error || null
  }

  log(`METIS_SMOKE_RESULT:${JSON.stringify(result)}`)

  if (process.env.METIS_DESKTOP_SMOKE === '1') {
    setTimeout(() => {
      app.isQuitting = true
      app.exit(result.ok ? 0 : 1)
    }, 80)
  }

  return { ok: true }
})
ipcMain.handle('metis:perf-result', (_event, payload = {}) => {
  const result = {
    ok: Boolean(payload.ok),
    metrics: payload.metrics && typeof payload.metrics === 'object' ? payload.metrics : {},
    budgets: payload.budgets && typeof payload.budgets === 'object' ? payload.budgets : {},
    checks: Array.isArray(payload.checks) ? payload.checks : [],
    error: payload.error || null
  }

  log(`METIS_PERF_RESULT:${JSON.stringify(result)}`)

  if (process.env.METIS_DESKTOP_PERF === '1') {
    setTimeout(() => {
      app.isQuitting = true
      app.exit(result.ok ? 0 : 1)
    }, 80)
  }

  return { ok: true }
})

// --- safeStorage: encrypted key storage ---
ipcMain.handle('metis:safe-storage-migrate', () => {
  migrateApiKeyToSafeStorage()
  return { ok: true }
})
ipcMain.handle('metis:safe-storage-available', () => {
  try {
    return safeStorage.isEncryptionAvailable()
  } catch {
    return false
  }
})
ipcMain.handle('metis:safe-storage-encrypt', (_event, plaintext) => {
  try {
    if (!safeStorage.isEncryptionAvailable()) return null
    return safeStorage.encryptString(plaintext).toString('base64')
  } catch {
    return null
  }
})
ipcMain.handle('metis:safe-storage-decrypt', (_event, encrypted) => {
  try {
    if (!safeStorage.isEncryptionAvailable()) return null
    return safeStorage.decryptString(Buffer.from(encrypted, 'base64'))
  } catch {
    return null
  }
})

/**
 * Migrate plaintext api_key -> api_key_encrypted in METIS_HOME/config.json.
 * Runs once at startup; no-op if safeStorage unavailable or already migrated.
 */
function migrateApiKeyToSafeStorage() {
  try {
    if (!safeStorage.isEncryptionAvailable()) return
    const configFile = configFilePath()
    const legacyConfigFile = path.join(legacyMetisHome(), 'config.json')
    const inputConfigFile = fsSync.existsSync(configFile) ? configFile : legacyConfigFile
    if (!fsSync.existsSync(inputConfigFile)) return
    const raw = fsSync.readFileSync(inputConfigFile, 'utf-8')
    const cfg = JSON.parse(raw)
    const plainKey = (cfg.api_key || '').trim()
    if (!plainKey || cfg.api_key_encrypted) return

    const encrypted = safeStorage.encryptString(plainKey).toString('base64')
    cfg.api_key_encrypted = encrypted
    delete cfg.api_key
    fsSync.mkdirSync(path.dirname(configFile), { recursive: true })
    fsSync.writeFileSync(configFile, JSON.stringify(cfg, null, 2), 'utf-8')
  } catch {
    // best-effort migration — silently continue on failure
  }
}

app.whenReady().then(async () => {
  if (!gotSingleInstanceLock) {
    return
  }
  nativeTheme.themeSource = 'system'
  migrateApiKeyToSafeStorage()
  await createWindow()
  createTray()
  configureAutoUpdates()
})

app.on('activate', showWindow)
app.on('before-quit', () => {
  app.isQuitting = true
  clearBackendRestartTimer()
  clearInterval(updateCheckTimer)
  stopAllDevServers()
  killAllTerminalSessions()
  stopBackend()
})
app.on('window-all-closed', () => {})
