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
const {
  HARDENED_WEB_PREFERENCES,
  isAllowedAppNavigation,
  isSafeExternalUrl
} = require('./security.cjs')

let autoUpdater = null
try {
  autoUpdater = require('electron-updater').autoUpdater
} catch {}

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
let lastPreviewBounds = null
// 原生 WebContentsView 永远盖在 DOM 之上（Electron 无 z-index）。任意 DOM 浮层（设置/命令面板/弹窗）打开时
// 必须把它藏掉，否则就会像截图那样压在弹窗上面。这是 VS Code / 各家稳定方案的核心做法。
let previewOccluded = false
const previewLoadedUrls = new Map()
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

function hidePreviewView() {
  if (!previewView) return
  if (lastPreviewBoundsKey === 'hidden') return
  lastPreviewBoundsKey = 'hidden'
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
    try { previewView.setVisible?.(false) } catch {}
    hidePreviewView()
  } else {
    try { previewView.setVisible?.(true) } catch {}
    if (lastPreviewBounds && lastPreviewBounds.width > 4 && lastPreviewBounds.height > 4) {
      lastPreviewBoundsKey = `${lastPreviewBounds.x},${lastPreviewBounds.y},${lastPreviewBounds.width},${lastPreviewBounds.height}`
      try { previewView.setBounds(lastPreviewBounds) } catch {}
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
  webContents.on('did-start-loading', () => emitPreviewState({ error: '', loading: true }))
  webContents.on('did-stop-loading', () => emitPreviewState({ loading: false }))
  webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    if (errorCode === -3 || isMainFrame === false) {
      emitPreviewState({ loading: false })
      return
    }
    emitPreviewState({
      error: errorDescription || '网页加载失败',
      loading: false,
      url: validatedURL || webContents.getURL()
    })
  })
  webContents.on('did-navigate', (_event, url) => emitPreviewState({ error: '', loading: false, url }))
  webContents.on('did-navigate-in-page', (_event, url) => emitPreviewState({ error: '', loading: false, url }))
  webContents.on('page-title-updated', (_event, title) => emitPreviewState({ title }))

  return previewView
}

function loadPreviewUrl(url, tabId = '') {
  const value = String(url || '').trim()
  const nextTabId = String(tabId || '')
  if (!isSafeExternalUrl(value)) {
    emitPreviewState({ error: 'Preview 只允许 http(s) 地址', loading: false, tabId })
    return { ok: false, error: 'unsafe url' }
  }
  const view = ensurePreviewView()
  if (!view) return { ok: false, error: 'preview view unavailable' }
  const currentUrl = view.webContents.getURL()
  if (previewTabId === nextTabId && (currentUrl === value || previewLoadedUrls.get(nextTabId) === value)) {
    emitPreviewState({ error: '', loading: false, tabId: nextTabId, url: value })
    return { ok: true, skipped: true }
  }
  previewTabId = nextTabId
  previewLoadedUrls.set(previewTabId, value)
  emitPreviewState({ error: '', loading: true, tabId: previewTabId, url: value })
  view.webContents.loadURL(value).catch(error => {
    emitPreviewState({ error: error?.message || String(error), loading: false, url: value })
  })
  return { ok: true }
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
    bootError = {
      title: payload.title || '后端启动失败',
      detail: payload.detail || payload.logTail || '',
      logTail: payload.logTail || tailBackendLog()
    }
  } else if (payload.phase === 'stopped') {
    backendPort = null
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
    .replace(/-----BEGIN [^-]+PRIVATE KEY-----[\s\S]*?-----END [^-]+PRIVATE KEY-----/g, '[redacted private key]')
    .replace(/\b[\w.-]+\.(?:pem|pfx|key)\b/gi, '[redacted secret file]')
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

function diagnosticsPayload() {
  const state = bootState()
  const storage = resolveDataRootInfo()
  return {
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
    }
  }
}

function diagnosticsBundleContent(payload = diagnosticsPayload()) {
  return JSON.stringify(
    {
      schema: 'metis.diagnostics.bundle.v1',
      diagnostics: payload
    },
    null,
    2
  )
}

async function saveDiagnosticsBundle() {
  const diagnostics = diagnosticsPayload()
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
  } catch (error) {
    bootStatus = 'error'
    backendPort = null
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

function configureAutoUpdates() {
  if (!app.isPackaged || !autoUpdater) {
    return
  }

  const updateUrl = process.env.METIS_UPDATE_URL
  if (updateUrl) {
    autoUpdater.setFeedURL({ provider: 'generic', url: updateUrl })
  }

  autoUpdater.autoDownload = true
  autoUpdater.on('checking-for-update', () => log('[update] checking'))
  autoUpdater.on('update-available', info => log(`[update] available ${info?.version || ''}`))
  autoUpdater.on('update-not-available', info => log(`[update] not available ${info?.version || ''}`))
  autoUpdater.on('error', error => log(`[update] error ${error?.message || error}`))
  autoUpdater.on('update-downloaded', info => log(`[update] downloaded ${info?.version || ''}`))

  setTimeout(() => {
    autoUpdater.checkForUpdatesAndNotify().catch(error => {
      log(`[update] skipped ${error?.message || error}`)
    })
  }, 5000)
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
// 两个窗口：glow 全屏点击穿透（纯视觉），pill 小窗可点击（急停）。
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
      width: 248,
      height: 60,
      show: false,
      transparent: true,
      frame: false,
      resizable: false,
      movable: false,
      skipTaskbar: true,
      hasShadow: false,
      fullscreenable: false,
      webPreferences: {
        ...HARDENED_WEB_PREFERENCES,
        preload: path.join(__dirname, 'preload.cjs')
      }
    })
    overlayPillWindow.setAlwaysOnTop(true, 'screen-saver')
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
      width: 248,
      height: 60,
      x: work.x + work.width - 268,
      y: work.y + work.height - 80
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
ipcMain.handle('metis:save-diagnostics-bundle', () => saveDiagnosticsBundle())
ipcMain.handle('metis:dev-server-detect', (_event, payload = {}) => detectFrontendProject(payload))
ipcMain.handle('metis:dev-server-start', (_event, payload = {}) => startDevServer(payload))
ipcMain.handle('metis:dev-server-stop', (_event, payload = {}) => stopDevServer(payload))
ipcMain.handle('metis:dev-server-status', (_event, payload = {}) => devServerStatus(payload))
ipcMain.handle('metis:save-preview-evidence', (_event, payload = {}) => savePreviewEvidence(payload))
ipcMain.handle('metis:preview-set-bounds', (_event, payload = {}) => {
  // 有 DOM 浮层挡着时，无视一切定位请求，保持隐藏（否则 ResizeObserver 会把它又显示到弹窗上面）。
  if (previewOccluded) return { ok: true, occluded: true }
  const visible = Boolean(payload.visible)
  if (!visible) {
    hidePreviewView()
    return { ok: true }
  }
  const tabId = String(payload.tabId || '')
  if (tabId && tabId !== previewTabId) {
    return { ok: true, skipped: true, tabId, activeTabId: previewTabId }
  }
  const view = ensurePreviewView()
  if (!view) return { ok: false, error: 'preview view unavailable' }
  const bounds = {
    x: Math.max(0, Math.round(Number(payload.x) || 0)),
    y: Math.max(0, Math.round(Number(payload.y) || 0)),
    width: Math.max(0, Math.round(Number(payload.width) || 0)),
    height: Math.max(0, Math.round(Number(payload.height) || 0))
  }
  if (bounds.width <= 4 || bounds.height <= 4) {
    hidePreviewView()
    return { ok: true, bounds, hidden: true }
  }
  // 去重：渲染端一次同步会连发多帧/定时器调用，位置没变就别重定位原生视图（消除闪烁）。
  const key = `${bounds.x},${bounds.y},${bounds.width},${bounds.height}`
  if (key === lastPreviewBoundsKey) {
    return { ok: true, bounds, deduped: true }
  }
  lastPreviewBoundsKey = key
  lastPreviewBounds = bounds
  view.setBounds(bounds)
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
ipcMain.handle('metis:preview-capture', async () => {
  const webContents = previewView?.webContents
  if (!webContents || webContents.isDestroyed()) return { ok: false, dataUrl: '' }
  const image = await webContents.capturePage()
  return { ok: true, dataUrl: image.toDataURL() }
})
ipcMain.handle('metis:check-updates', async () => {
  const current = app.getVersion()

  // 1) 若配置了私有更新源，走 electron-updater 自动下载。
  const updateUrl = process.env.METIS_UPDATE_URL
  if (app.isPackaged && autoUpdater && updateUrl) {
    try {
      autoUpdater.setFeedURL({ provider: 'generic', url: updateUrl })
      const result = await autoUpdater.checkForUpdatesAndNotify()
      const v = result?.updateInfo?.version
      return v && compareVersions(v, current) > 0
        ? { ok: true, status: 'available', message: `发现新版本 v${v}，正在后台下载。` }
        : { ok: true, status: 'latest', message: `当前已是最新版本 (v${current})。` }
    } catch (error) {
      return { ok: false, status: 'error', message: error?.message || String(error) }
    }
  }

  // 2) 默认查 GitHub Releases：无发布/网络异常时优雅降级。
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
  stopAllDevServers()
  killAllTerminalSessions()
  stopBackend()
})
app.on('window-all-closed', () => {})
