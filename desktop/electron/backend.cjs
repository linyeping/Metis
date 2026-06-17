const { spawn } = require('node:child_process')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const crypto = require('node:crypto')
const { Readable } = require('node:stream')
const {
  configFilePath,
  legacyMetisHome,
  metisHome,
  resolveDataRootInfo
} = require('./data-root.cjs')

let child = null
let manualStopRequested = false

/**
 * Read METIS_HOME/config.json and decrypt any `api_key_encrypted` field
 * via Electron safeStorage.  Returns a plain API key string or empty.
 */
function decryptApiKeyFromConfig() {
  try {
    const { safeStorage } = require('electron')
    if (!safeStorage.isEncryptionAvailable()) return ''
    const configFiles = [
      configFilePath(),
      path.join(legacyMetisHome(), 'config.json')
    ]
    for (const configFile of [...new Set(configFiles)]) {
      if (!fs.existsSync(configFile)) continue
      const cfg = JSON.parse(fs.readFileSync(configFile, 'utf-8'))
      const encrypted = cfg.api_key_encrypted || ''
      if (!encrypted) continue
      return safeStorage.decryptString(Buffer.from(encrypted, 'base64'))
    }
  } catch {
    return ''
  }
  return ''
}
let fakeServer = null
let fakeServerPort = null
let fakeDeskEnabled = true
let fakeDeskPaused = false
let fakeMcpConnected = true
let fakeActiveSessionId = 'smoke-session'
let fakeCronCounter = 1
let fakeCronResultReady = false
let fakeCronTasks = []
let fakeGlobalMemory = '# METIS.md\n\n- Smoke memory: remember the desktop smoke path.\n'
let fakeProjectMemory = '# Project METIS.md\n\n- Smoke project note for self-learning UX.\n'
let fakeCompactStatus = { running: false, ok: false, before_count: 0, after_count: 0, summary_preview: '', updated_at: 0, error: '' }
const fakePermissionRequests = new Map()
let fakePermissionRuleCounter = 1
let fakePermissionRules = []
let fakePermissionAudit = []
let fakeFileChangeAudit = []
let fakeRunCounter = 1
const fakeRuns = new Map()
const fakeRunTerminalStates = new Set(['done', 'failed', 'canceled'])
const fakeRunActiveStates = new Set(['queued', 'running', 'canceling'])
const fakeBackendStartedAt = Math.floor(Date.now() / 1000)
const fakeWorkspaceRoot = 'D:\\Metis\\Smoke'
const fakeWorkspaceFiles = new Map([
  ['D:\\Metis\\Smoke\\diff-smoke.md', '# Smoke\n\nnew line\n'],
  ['D:\\Metis\\Smoke\\settings.ts', 'export const theme = "new";\nexport const compact = true;\n']
])
let fakeSkills = [
  {
    id: 'smoke-skill',
    name: 'Smoke Skill',
    path: path.join(metisHome(), 'skills', 'smoke-skill', 'SKILL.md'),
    enabled: true,
    preview: 'Use this skill to verify the Metis skills zone without touching real user skills.',
    content:
      '# Smoke Skill\n\n' +
      '## Trigger\n' +
      'Use this skill to verify the Metis skills zone without touching real user skills.\n\n' +
      '## Workflow\n' +
      '1. Open the skills zone.\n' +
      '2. Edit, save, toggle, import, and delete a local SKILL.md.\n'
  }
]

let fakeSmokeSessionHistory = []

const LOG_TAIL_LIMIT = 240
const logTail = []

function getBackendLogPath() {
  return path.join(metisHome(), 'logs', 'metis-backend.log')
}

function ensureLogDir() {
  fs.mkdirSync(path.dirname(getBackendLogPath()), { recursive: true })
}

function appendLogLine(line) {
  const text = String(line ?? '').trimEnd()
  if (!text) {
    return
  }

  const stamped = `${new Date().toISOString()} ${text}`
  logTail.push(stamped)
  while (logTail.length > LOG_TAIL_LIMIT) {
    logTail.shift()
  }

  try {
    ensureLogDir()
    fs.appendFileSync(getBackendLogPath(), `${stamped}\n`, 'utf8')
  } catch {}
}

function tailBackendLog(maxLines = 80) {
  if (logTail.length > 0) {
    return logTail.slice(-maxLines).join('\n')
  }

  try {
    const text = fs.readFileSync(getBackendLogPath(), 'utf8')
    return text.split(/\r?\n/).filter(Boolean).slice(-maxLines).join('\n')
  } catch {
    return ''
  }
}

function emitBoot(emit, event) {
  const payload = {
    timestamp: new Date().toISOString(),
    logPath: getBackendLogPath(),
    ...event
  }

  if (payload.phase === 'error') {
    appendLogLine(`[error] ${payload.title || 'backend startup failed'}`)
    if (payload.detail) {
      for (const line of String(payload.detail).split(/\r?\n/)) {
        appendLogLine(`[error] ${line}`)
      }
    }
  } else if (payload.phase !== 'log') {
    appendLogLine(`[boot] ${payload.line || payload.title || payload.phase}`)
  }

  try {
    emit(payload)
  } catch {}

  return payload
}

function makeBootError(title, detail, extra = {}) {
  const error = new Error(title)
  error.title = title
  error.detail = detail
  error.logTail = tailBackendLog()
  error.logPath = getBackendLogPath()
  Object.assign(error, extra)
  return error
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}

function backendSourceRoot() {
  if (process.env.METIS_DESKTOP_DEV_SERVER || !process.resourcesPath) {
    return path.resolve(__dirname, '..', '..', 'backend')
  }
  return path.join(process.resourcesPath, 'backend')
}

function backendCwd(root) {
  return path.dirname(root)
}

function isDevBackend() {
  return Boolean(process.env.METIS_DESKTOP_DEV_SERVER || !process.resourcesPath)
}

function packagedBackendExe() {
  const executable = process.platform === 'win32' ? 'metis-backend.exe' : 'metis-backend'
  return path.join(process.resourcesPath, 'backend-dist', 'metis-backend', executable)
}

function bundledRuntimePackDir() {
  if (process.env.METIS_BUNDLED_RUNTIME_PACK_DIR) {
    return process.env.METIS_BUNDLED_RUNTIME_PACK_DIR
  }
  if (isDevBackend()) {
    return path.resolve(__dirname, '..', 'resources', 'runtime-pack')
  }
  return path.join(process.resourcesPath, 'runtime-pack')
}

function normalizedRuntimePackAssetDir(dir) {
  const candidates = [
    path.join(dir, 'metisvm.bundle'),
    path.join(dir, 'metis-runtime-bundle-v2'),
    dir
  ]
  return candidates.find(candidate => fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) || dir
}

function hasRuntimePackAssets(dir) {
  try {
    const bundle = normalizedRuntimePackAssetDir(dir)
    const required = ['vmlinuz', 'initrd', 'metis-bin.vhdx', 'metis-vm-pack.json']
    const hasRequired = required.every(name => fs.existsSync(path.join(bundle, name)))
    const hasRootfs = fs.existsSync(path.join(bundle, 'rootfs.vhdx')) || fs.existsSync(path.join(bundle, 'rootfs.vhdx.zst'))
    return hasRequired && hasRootfs
  } catch {
    return false
  }
}

function shouldAutoEnsureRuntimePack() {
  if (process.env.METIS_RUNTIME_AUTO_ENSURE === '1' || process.env.METIS_RUNTIME_AUTO_DOWNLOAD === '1') {
    return true
  }
  return hasRuntimePackAssets(bundledRuntimePackDir())
}

function postBackendJson(port, pathname, body = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body)
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: pathname,
        method: 'POST',
        timeout: 6 * 60 * 60 * 1000,
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(payload)
        }
      },
      res => {
        let text = ''
        res.setEncoding('utf8')
        res.on('data', chunk => {
          text += chunk
        })
        res.on('end', () => {
          resolve({ statusCode: res.statusCode, text })
        })
      }
    )
    req.on('error', reject)
    req.on('timeout', () => {
      req.destroy(new Error('runtime ensure request timed out'))
    })
    req.write(payload)
    req.end()
  })
}

function maybeEnsureRuntimePack(port) {
  if (!shouldAutoEnsureRuntimePack()) {
    return
  }
  const allowDownload = process.env.METIS_RUNTIME_AUTO_DOWNLOAD === '1' || process.env.METIS_RUNTIME_AUTO_ENSURE === '1'
  appendLogLine(`[runtime-pack] auto ensure requested (allowDownload=${allowDownload})`)
  postBackendJson(port, '/settings/runtime-manager/repair', {
    source: 'auto',
    allow_download: allowDownload,
    force: false
  })
    .then(result => {
      appendLogLine(`[runtime-pack] auto ensure response ${result.statusCode}: ${String(result.text || '').slice(0, 1000)}`)
    })
    .catch(error => {
      appendLogLine(`[runtime-pack] auto ensure skipped: ${error?.message || error}`)
    })
}

function isPathLike(exe) {
  return path.isAbsolute(exe) || exe.includes('\\') || exe.includes('/')
}

function managedPythonRoot() {
  return path.join(metisHome(), 'python-backend')
}

function managedPythonVenvDir() {
  return path.join(managedPythonRoot(), 'venv')
}

function managedPythonExecutable() {
  return process.platform === 'win32'
    ? path.join(managedPythonVenvDir(), 'Scripts', 'python.exe')
    : path.join(managedPythonVenvDir(), 'bin', 'python')
}

function managedPythonStampPath() {
  return path.join(managedPythonRoot(), 'install.json')
}

function pythonCommandArgs(candidate, args = []) {
  return [...(candidate.prefixArgs || []), ...args]
}

function pythonCommandLabel(candidate) {
  return [candidate.exe, ...(candidate.prefixArgs || [])].join(' ')
}

function pythonCandidates() {
  const candidates = []
  const seen = new Set()

  function add(source, exe, prefixArgs = []) {
    if (!exe) {
      return
    }
    const key = `${String(exe).toLowerCase()}::${prefixArgs.join(' ')}`
    if (seen.has(key)) {
      return
    }
    seen.add(key)
    candidates.push({ source, exe, prefixArgs })
  }

  if (process.env.METIS_PYTHON) {
    add('METIS_PYTHON', process.env.METIS_PYTHON)
  }

  // Read python_path from METIS_HOME/config.json (set via Settings -> Terminal).
  try {
    const configFiles = [
      configFilePath(),
      path.join(legacyMetisHome(), 'config.json')
    ]
    for (const configFile of [...new Set(configFiles)]) {
      if (!fs.existsSync(configFile)) continue
      const cfg = JSON.parse(fs.readFileSync(configFile, 'utf-8'))
      const savedPython = cfg.python_path || ''
      if (savedPython && fs.existsSync(savedPython)) {
        add('settings (config.json)', savedPython)
        break
      }
    }
  } catch (_) { /* best-effort */ }

  const backendRoot = backendSourceRoot()
  const projectRoot = backendCwd(backendRoot)
  for (const venvDir of ['.venv', 'venv', '.env', 'env']) {
    add(`project venv ${venvDir}`, path.join(projectRoot, venvDir, 'Scripts', 'python.exe'))
    add(`project venv ${venvDir}`, path.join(projectRoot, venvDir, 'bin', 'python'))
  }

  if (process.platform === 'win32') {
    add('Windows py -3', 'py', ['-3'])
    add('Windows py launcher', 'py')
  }

  add('PATH python', 'python')
  add('PATH python3', 'python3')

  if (process.env.CONDA_PREFIX) {
    add('conda active env', path.join(process.env.CONDA_PREFIX, process.platform === 'win32' ? 'python.exe' : 'bin/python'))
  }
  if (process.env.CONDA_EXE) {
    const condaRoot = path.dirname(path.dirname(process.env.CONDA_EXE))
    add('conda base', path.join(condaRoot, process.platform === 'win32' ? 'python.exe' : 'bin/python'))
  }

  if (process.platform === 'win32') {
    const localAppData = process.env.LOCALAPPDATA || ''
    const appData = process.env.APPDATA || ''
    const home = os.homedir()
    const programFiles = process.env.ProgramFiles || 'C:\\Program Files'
    const programFilesX86 = process.env['ProgramFiles(x86)'] || ''
    for (const ver of ['313', '312', '311', '310', '39']) {
      if (localAppData) {
        add(`LocalAppData Python${ver}`, path.join(localAppData, 'Programs', 'Python', `Python${ver}`, 'python.exe'))
      }
      add(`ProgramFiles Python${ver}`, path.join(programFiles, 'Python', `Python${ver}`, 'python.exe'))
      if (programFilesX86) {
        add(`ProgramFiles(x86) Python${ver}`, path.join(programFilesX86, 'Python', `Python${ver}`, 'python.exe'))
      }
      add(`C drive Python${ver}`, path.join('C:\\', `Python${ver}`, 'python.exe'))
    }
    for (const base of [home, localAppData, appData]) {
      if (!base) {
        continue
      }
      for (const dir of ['Anaconda3', 'Miniconda3', 'miniforge3', 'mambaforge']) {
        add(`${dir}`, path.join(base, dir, 'python.exe'))
      }
    }
  }

  return candidates
}

function runProbe(exe, args, timeoutMs = 8000) {
  return new Promise(resolve => {
    let settled = false
    let stdout = ''
    let stderr = ''

    const probe = spawn(exe, args, {
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1'
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })

    const timer = setTimeout(() => {
      if (settled) {
        return
      }
      settled = true
      try {
        probe.kill('SIGKILL')
      } catch {}
      resolve({ ok: false, stdout, stderr, timedOut: true })
    }, timeoutMs)

    probe.stdout.on('data', data => {
      stdout += data.toString('utf8')
    })
    probe.stderr.on('data', data => {
      stderr += data.toString('utf8')
    })
    probe.on('error', error => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      resolve({ ok: false, stdout, stderr, error })
    })
    probe.on('exit', (code, signal) => {
      if (settled) {
        return
      }
      settled = true
      clearTimeout(timer)
      resolve({ ok: code === 0, code, signal, stdout, stderr })
    })
  })
}

function summarizeProbeFailure(result) {
  if (result.timedOut) {
    return '预检超时'
  }
  if (result.error) {
    return result.error.message
  }
  const stderr = String(result.stderr || '').trim()
  const stdout = String(result.stdout || '').trim()
  if (stderr) {
    return stderr.split(/\r?\n/).slice(-4).join('\n')
  }
  if (stdout) {
    return stdout.split(/\r?\n/).slice(-4).join('\n')
  }
  return `退出码 ${result.code ?? 'unknown'}`
}

async function probePythonCandidate(candidate, requireDeps = false, timeoutMs = 8000) {
  const code = requireDeps
    ? 'import sys; import flask, requests; print(sys.executable)'
    : 'import sys; print(sys.executable)'
  const result = await runProbe(candidate.exe, pythonCommandArgs(candidate, ['-c', code]), timeoutMs)
  const resolved = String(result.stdout || '').trim().split(/\r?\n/).pop() || candidate.exe
  return {
    ...result,
    resolved,
    command: pythonCommandLabel(candidate),
  }
}

function writeManagedPythonStamp(payload) {
  try {
    fs.mkdirSync(managedPythonRoot(), { recursive: true })
    fs.writeFileSync(managedPythonStampPath(), JSON.stringify(payload, null, 2), 'utf8')
  } catch {}
}

function clearManagedPythonEnv() {
  try {
    fs.rmSync(managedPythonVenvDir(), { recursive: true, force: true })
  } catch {}
  try {
    fs.rmSync(managedPythonStampPath(), { force: true })
  } catch {}
}

async function bootstrapManagedPythonEnv(candidate, backendRoot, emit = () => {}, reset = false) {
  const managedCandidate = {
    source: 'Metis managed venv',
    exe: managedPythonExecutable(),
    prefixArgs: []
  }

  fs.mkdirSync(managedPythonRoot(), { recursive: true })

  if (reset) {
    emitBoot(emit, {
      phase: 'preflight',
      line: `重建 Metis 托管 Python 环境: ${managedPythonVenvDir()}`
    })
    clearManagedPythonEnv()
    fs.mkdirSync(managedPythonRoot(), { recursive: true })
  }

  if (!fs.existsSync(managedCandidate.exe)) {
    emitBoot(emit, {
      phase: 'preflight',
      line: `创建 Metis 托管 Python 环境 (${candidate.source})`
    })
    const createResult = await runProbe(
      candidate.exe,
      pythonCommandArgs(candidate, ['-m', 'venv', managedPythonVenvDir()]),
      180000,
    )
    if (!createResult.ok) {
      return {
        ok: false,
        reason: `创建托管环境失败: ${summarizeProbeFailure(createResult)}`,
      }
    }
  }

  const pipCheck = await runProbe(managedCandidate.exe, ['-m', 'pip', '--version'], 15000)
  if (!pipCheck.ok) {
    emitBoot(emit, {
      phase: 'preflight',
      line: '托管环境缺少 pip，正在执行 ensurepip'
    })
    const ensurePip = await runProbe(managedCandidate.exe, ['-m', 'ensurepip', '--upgrade'], 120000)
    if (!ensurePip.ok) {
      return {
        ok: false,
        reason: `托管环境无法初始化 pip: ${summarizeProbeFailure(ensurePip)}`,
      }
    }
  }

  emitBoot(emit, {
    phase: 'preflight',
    line: `安装 Metis 后端依赖到托管环境: ${backendRoot}`
  })

  const upgradeResult = await runProbe(
    managedCandidate.exe,
    ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '--upgrade', 'pip', 'setuptools', 'wheel'],
    240000,
  )
  if (!upgradeResult.ok) {
    appendLogLine(`[python] pip upgrade warning: ${summarizeProbeFailure(upgradeResult)}`)
  }

  const installResult = await runProbe(
    managedCandidate.exe,
    ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-e', backendRoot],
    300000,
  )
  if (!installResult.ok) {
    if (!reset) {
      appendLogLine('[python] managed env install failed once, retrying with a clean venv')
      return bootstrapManagedPythonEnv(candidate, backendRoot, emit, true)
    }
    return {
      ok: false,
      reason: `安装托管后端依赖失败: ${summarizeProbeFailure(installResult)}`,
    }
  }

  const verifyResult = await probePythonCandidate(managedCandidate, true, 15000)
  if (!verifyResult.ok) {
    if (!reset) {
      appendLogLine('[python] managed env verification failed once, retrying with a clean venv')
      return bootstrapManagedPythonEnv(candidate, backendRoot, emit, true)
    }
    return {
      ok: false,
      reason: `托管环境校验失败: ${summarizeProbeFailure(verifyResult)}`,
    }
  }

  writeManagedPythonStamp({
    managedPython: managedCandidate.exe,
    backendRoot,
    bootstrapSource: candidate.source,
    bootstrapCommand: pythonCommandLabel(candidate),
    resolved: verifyResult.resolved,
    installedAt: new Date().toISOString(),
  })

  emitBoot(emit, {
    phase: 'preflight',
    line: `Metis 托管 Python 环境就绪: ${verifyResult.resolved}`
  })

  return {
    ok: true,
    candidate: managedCandidate,
    resolved: verifyResult.resolved,
  }
}

async function detectPython(backendRoot, emit = () => {}) {
  emitBoot(emit, { phase: 'detecting', title: '正在探测 Python 后端环境' })
  const failures = []
  const rawReady = []
  const bootstrapReady = []
  const managedCandidate = {
    source: 'Metis managed venv',
    exe: managedPythonExecutable(),
    prefixArgs: []
  }

  if (fs.existsSync(managedCandidate.exe)) {
    emitBoot(emit, {
      phase: 'preflight',
      line: `检查 ${managedCandidate.source}: ${managedCandidate.exe}`
    })
    const managedProbe = await probePythonCandidate(managedCandidate, true, 15000)
    if (managedProbe.ok) {
      emitBoot(emit, {
        phase: 'preflight',
        line: `Metis 托管环境可用: ${managedProbe.resolved}`
      })
      return { ok: true, exe: managedCandidate.exe, source: managedCandidate.source, resolved: managedProbe.resolved, prefixArgs: [] }
    }
    const reason = summarizeProbeFailure(managedProbe)
    failures.push(`${managedCandidate.source} (${managedCandidate.exe}): ${reason}`)
    appendLogLine(`[python] ${managedCandidate.source} failed: ${reason}`)
  }

  for (const candidate of pythonCandidates()) {
    emitBoot(emit, {
      phase: 'preflight',
      line: `检查 ${candidate.source}: ${pythonCommandLabel(candidate)}`
    })

    if (isPathLike(candidate.exe) && !fs.existsSync(candidate.exe)) {
      const reason = '解释器路径不存在'
      failures.push(`${candidate.source} (${pythonCommandLabel(candidate)}): ${reason}`)
      appendLogLine(`[python] ${candidate.source} skipped: ${reason}`)
      continue
    }

    const baseProbe = await probePythonCandidate(candidate, false, 12000)
    if (!baseProbe.ok) {
      const reason = summarizeProbeFailure(baseProbe)
      failures.push(`${candidate.source} (${pythonCommandLabel(candidate)}): ${reason}`)
      appendLogLine(`[python] ${candidate.source} failed: ${reason}`)
      continue
    }
    bootstrapReady.push(candidate)

    const depProbe = await probePythonCandidate(candidate, true, 15000)
    if (depProbe.ok) {
      emitBoot(emit, {
        phase: 'preflight',
        line: `Python 预检通过: ${depProbe.resolved}`
      })
      rawReady.push({
        exe: candidate.exe,
        source: candidate.source,
        resolved: depProbe.resolved,
        prefixArgs: candidate.prefixArgs || []
      })
      continue
    }

    const reason = summarizeProbeFailure(depProbe)
    failures.push(`${candidate.source} (${pythonCommandLabel(candidate)}): ${reason}`)
    appendLogLine(`[python] ${candidate.source} failed: ${reason}`)
  }

  if (bootstrapReady.length > 0) {
    const managedBootstrap = await bootstrapManagedPythonEnv(bootstrapReady[0], backendRoot, emit)
    if (managedBootstrap.ok) {
      return {
        ok: true,
        exe: managedBootstrap.candidate.exe,
        source: managedBootstrap.candidate.source,
        resolved: managedBootstrap.resolved,
        prefixArgs: managedBootstrap.candidate.prefixArgs || []
      }
    }
    failures.push(`Metis 托管环境 (${managedPythonVenvDir()}): ${managedBootstrap.reason}`)
    appendLogLine(`[python] managed bootstrap failed: ${managedBootstrap.reason}`)
  }

  if (rawReady.length > 0) {
    const fallback = rawReady[0]
    emitBoot(emit, {
      phase: 'preflight',
      line: `托管环境未就绪，回退到现有 Python: ${fallback.resolved}`
    })
    return fallback
  }

  const detail = [
    'Metis 未能找到可用的 Python 后端环境，也未能自动完成托管环境修复。',
    '',
    '已尝试:',
    ...failures.map(item => `- ${item}`),
    '',
    `托管环境位置: ${managedPythonVenvDir()}`,
    '',
    '可手动修复:',
    'python -m pip install -e backend/',
    '或设置 METIS_PYTHON 指向正确的 python.exe 后重试。'
  ].join('\n')

  const error = makeBootError('未找到可用的 Python 后端环境', detail)
  emitBoot(emit, {
    phase: 'error',
    title: error.title,
    detail: error.detail,
    logTail: error.logTail
  })
  throw error
}

function probe(port, pathname = '/health') {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: pathname, timeout: 1600 }, res => {
      res.resume()
      if (res.statusCode && res.statusCode >= 200 && res.statusCode < 500) {
        resolve(true)
      } else {
        reject(new Error(`backend probe returned HTTP ${res.statusCode}`))
      }
    })
    req.on('error', reject)
    req.on('timeout', () => {
      req.destroy(new Error('backend probe timed out'))
    })
  })
}

async function waitForReady(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  let lastError = null

  while (Date.now() < deadline) {
    try {
      await probe(port, '/health')
      return
    } catch (error) {
      lastError = error
      try {
        await probe(port, '/sessions')
        return
      } catch (fallbackError) {
        lastError = fallbackError
      }
    }
    await new Promise(resolve => setTimeout(resolve, 400))
  }

  throw lastError || new Error('backend not ready')
}

function writeFakeJson(res, status, payload) {
  res.writeHead(status, {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'content-type',
    'Access-Control-Allow-Methods': 'GET,POST,DELETE,OPTIONS',
    'Content-Type': 'application/json; charset=utf-8'
  })
  res.end(JSON.stringify(payload))
}

function readFakeBody(req) {
  return new Promise(resolve => {
    let body = ''
    req.on('data', chunk => {
      body += chunk.toString('utf8')
    })
    req.on('end', () => {
      if (!body.trim()) {
        resolve({})
        return
      }
      try {
        resolve(JSON.parse(body))
      } catch {
        resolve({})
      }
    })
  })
}

function readFakeRawBody(req) {
  return new Promise(resolve => {
    const chunks = []
    req.on('data', chunk => {
      chunks.push(Buffer.from(chunk))
    })
    req.on('end', () => resolve(Buffer.concat(chunks)))
  })
}

function fakeUploadFilename(bodyText) {
  const match = String(bodyText || '').match(/filename="([^"]+)"/i)
  return match ? path.basename(match[1]) : 'smoke-upload.txt'
}

async function fakeUploadParsePayload(req) {
  const buffer = await readFakeRawBody(req)
  const bodyText = buffer.toString('utf8')
  const filename = fakeUploadFilename(bodyText)
  const extension = path.extname(filename).toLowerCase()
  const text = [
    `Fake parsed upload: ${filename}`,
    '',
    'This smoke parser confirms the composer attachment pipeline without reading real user files.'
  ].join('\n')
  return {
    filename,
    type: extension,
    text,
    char_count: text.length,
    truncated: false
  }
}

function fakeNowSeconds() {
  return Math.floor(Date.now() / 1000)
}

function fakeCronNextRun(schedule) {
  const now = fakeNowSeconds()
  const value = String(schedule || '').trim().toLowerCase()
  if (value.startsWith('every')) {
    const minutes = value
      .split(/\s+/)
      .map(part => Number.parseInt(part, 10))
      .find(value => Number.isFinite(value) && value > 0)
    return now + (minutes || 1) * 60
  }
  if (/^\d{1,2}:\d{2}/.test(value)) {
    return now + 24 * 60 * 60
  }
  return now + 60
}

function fakeCronPayload(task) {
  return {
    id: task.id,
    name: task.name,
    schedule: task.schedule,
    prompt: task.prompt,
    workspace_id: task.workspace_id,
    enabled: task.enabled,
    createdAt: task.createdAt,
    lastRun: task.lastRun,
    nextRun: task.nextRun,
    lastSessionId: task.lastSessionId,
    lastStatus: task.lastStatus
  }
}

function makeFakeCronTask(data) {
  const now = fakeNowSeconds()
  const schedule = String(data.schedule || 'every 1 minute').trim() || 'every 1 minute'
  return {
    id: `smoke-cron-${fakeCronCounter++}`,
    name: String(data.name || 'Scheduled task').trim() || 'Scheduled task',
    schedule,
    prompt: String(data.prompt || '').trim(),
    workspace_id: String(data.workspace_id || 'smoke-workspace'),
    enabled: data.enabled === undefined ? true : Boolean(data.enabled),
    createdAt: now,
    lastRun: 0,
    nextRun: fakeCronNextRun(schedule),
    lastSessionId: '',
    lastStatus: ''
  }
}

function fakeInitialSmokeSessionHistory() {
  return [
    { role: 'user', content: 'NEW-69 smoke: start a long desktop reliability task.' },
    { role: 'assistant', content: 'I inspected the desktop app, provider settings, and context meter.' },
    { role: 'user', content: 'Keep the context summary safe and continue after compacting.' },
    { role: 'assistant', content: 'Plan: reuse /compact, add a compact control, and save a bounded local handoff.' },
    { role: 'user', content: 'Also make sure no API key appears in the handoff.' },
    { role: 'assistant', content: 'The compact handoff will redact API-key-like text and keep only summary preview.' },
    { role: 'user', content: 'Now compress the conversation.' },
    { role: 'assistant', content: 'Ready to compact while preserving the last messages.' }
  ]
}

function fakeCompactSmokeSession() {
  if (fakeSmokeSessionHistory.length < 6) {
    fakeSmokeSessionHistory = fakeInitialSmokeSessionHistory()
  }
  const before = fakeSmokeSessionHistory.length
  if (before < 6) {
    fakeCompactStatus = {
      running: false,
      ok: false,
      before_count: before,
      after_count: before,
      summary_preview: '',
      updated_at: fakeNowSeconds(),
      error: 'History too short to compact'
    }
    return fakeCompactStatus
  }
  const recent = fakeSmokeSessionHistory.slice(-4)
  const summary =
    '[Context Summary - auto-compacted from smoke history]\n\n' +
    '- NEW-69 smoke validated manual context compaction.\n' +
    '- Preserve current task: compact UI, local handoff, and reload session.\n' +
    '- No real API provider was called.'
  fakeSmokeSessionHistory = [{ role: 'system', content: summary }, ...recent]
  fakeCompactStatus = {
    running: false,
    ok: true,
    before_count: before,
    after_count: fakeSmokeSessionHistory.length,
    summary_preview: summary.slice(0, 200),
    updated_at: fakeNowSeconds(),
    error: ''
  }
  return fakeCompactStatus
}

function fakeSessionPayload() {
  const now = fakeBackendStartedAt
  const sessions = [
    {
      id: 'smoke-session',
      title: 'Smoke Session',
      workspace_id: 'smoke-workspace',
      message_count: fakeSmokeSessionHistory.length,
      created_at: now,
      updated_at: now
    },
    {
      id: 'search-hit-session',
      title: 'Smoke Search Hit',
      workspace_id: 'smoke-workspace',
      message_count: 3,
      created_at: now - 60,
      updated_at: now - 30
    }
  ]
  if (fakeCronResultReady) {
    sessions.push({
      id: 'cron-result-session',
      title: '[Cron] Smoke Cron Task Edited',
      workspace_id: 'smoke-workspace',
      message_count: 2,
      created_at: now - 15,
      updated_at: now
    })
  }
  return {
    active_id: fakeActiveSessionId,
    active_workspace_id: 'smoke-workspace',
    sessions
  }
}

function fakeWorkspacePayload() {
  const now = fakeBackendStartedAt
  return {
    active_id: 'smoke-workspace',
    workspaces: [
      {
        id: 'smoke-workspace',
        name: 'Smoke Workspace',
        path: 'D:\\Metis\\Smoke',
        created_at: now,
        updated_at: now
      }
    ]
  }
}

function fakeWorkspaceTreePayload() {
  return {
    tree: [
      {
        name: 'README.md',
        path: 'README.md',
        type: 'file',
        size: 92,
        modified: Math.floor(Date.now() / 1000)
      },
      {
        name: 'src',
        path: 'src',
        type: 'directory',
        children: [
          {
            name: 'main.ts',
            path: 'src/main.ts',
            type: 'file',
            size: 64,
            modified: Math.floor(Date.now() / 1000)
          }
        ]
      },
      {
        name: 'asset.bin',
        path: 'asset.bin',
        type: 'file',
        size: 128,
        modified: Math.floor(Date.now() / 1000)
      }
    ]
  }
}

function fakeWorkspaceFilePayload(filePath) {
  const value = String(filePath || '')
  if (value === 'README.md') {
    return {
      type: 'markdown',
      name: 'README.md',
      path: 'README.md',
      size: 92,
      language: 'markdown',
      content: '# Fake workspace README\n\nThis file verifies the Metis right rail file workbench.\n',
      truncated: false
    }
  }
  if (value === 'src/main.ts') {
    return {
      type: 'text',
      name: 'main.ts',
      path: 'src/main.ts',
      size: 64,
      language: 'typescript',
      content: 'export const smokeMessage = \"right rail workspace preview\";\n',
      truncated: false
    }
  }
  if (value === 'asset.bin') {
    return {
      type: 'binary',
      name: 'asset.bin',
      path: 'asset.bin',
      size: 128,
      content: '',
      truncated: false
    }
  }
  return null
}

function fakeHashText(value) {
  return crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex')
}

function fakeNormalizeWorkspacePath(value) {
  const raw = String(value || '').trim().replace(/\//g, '\\')
  if (!raw) {
    return ''
  }
  if (/^[A-Za-z]:\\/.test(raw)) {
    return path.win32.normalize(raw)
  }
  return path.win32.normalize(path.win32.join(fakeWorkspaceRoot, raw))
}

function fakePathInsideWorkspace(normalizedPath) {
  const target = normalizedPath.toLowerCase()
  const root = fakeWorkspaceRoot.toLowerCase()
  return target === root || target.startsWith(`${root}\\`)
}

function fakePathBlocked(normalizedPath) {
  const parts = normalizedPath.toLowerCase().split('\\')
  const name = parts.at(-1) || ''
  if (['.env', '.npmrc', 'id_rsa', 'id_ed25519'].includes(name)) {
    return 'secret-bearing filename'
  }
  if (parts.includes('.metis') || parts.includes('.miro')) {
    return 'Metis control data'
  }
  return ''
}

function fakeNormalizeFileChange(row) {
  const source = row && typeof row === 'object' ? row : {}
  return {
    id: String(source.id || ''),
    path: String(source.path || source.file_path || source.filePath || ''),
    kind: String(source.kind || 'unknown').toLowerCase(),
    toolName: String(source.tool_name || source.toolName || '').toLowerCase(),
    before: typeof source.before === 'string' ? source.before : '',
    after:
      typeof source.after === 'string'
        ? source.after
        : typeof source.content === 'string'
          ? source.content
          : ''
  }
}

function fakePreflightFileChangeRevert(change) {
  const normalizedPath = fakeNormalizeWorkspacePath(change.path)
  const base = {
    id: change.id,
    path: change.path,
    kind: change.kind,
    tool_name: change.toolName,
    before_hash: fakeHashText(change.before),
    after_hash: fakeHashText(change.after)
  }
  if (!normalizedPath) {
    return { ...base, status: 'blocked', message: 'missing path' }
  }
  if (!fakePathInsideWorkspace(normalizedPath)) {
    return { ...base, status: 'blocked', message: 'path outside workspace' }
  }
  const blockedReason = fakePathBlocked(normalizedPath)
  if (blockedReason) {
    return { ...base, status: 'blocked', message: blockedReason }
  }

  const current = fakeWorkspaceFiles.has(normalizedPath) ? fakeWorkspaceFiles.get(normalizedPath) : null
  const currentHash = current === null ? '' : fakeHashText(current)
  const item = { ...base, current_hash: currentHash, normalizedPath }
  const isDelete = change.kind === 'delete' || change.toolName.includes('delete') || change.toolName.includes('remove')
  const isCreate = change.kind === 'create'

  if (isDelete) {
    if (current !== null && current !== change.after) {
      return { ...item, status: 'conflict', message: 'file exists and no longer matches the recorded deleted state' }
    }
    return { ...item, status: 'ready', action: 'write_before', content: change.before }
  }

  if (isCreate) {
    if (current === null) {
      return { ...item, status: 'ready', action: 'noop', message: 'created file is already missing' }
    }
    if (current !== change.after) {
      return { ...item, status: 'conflict', message: 'file changed after agent edit' }
    }
    return { ...item, status: 'ready', action: 'delete_current' }
  }

  if (current === null) {
    return { ...item, status: 'conflict', message: 'file is missing' }
  }
  if (current !== change.after) {
    return { ...item, status: 'conflict', message: 'file changed after agent edit' }
  }
  return { ...item, status: 'ready', action: 'write_before', content: change.before }
}

function fakeApplyFileChangeRevert(plan) {
  const { action, content, normalizedPath, ...item } = plan
  if (action === 'write_before') {
    fakeWorkspaceFiles.set(normalizedPath, String(content || ''))
    return { ...item, status: 'reverted', message: 'restored previous content' }
  }
  if (action === 'delete_current') {
    fakeWorkspaceFiles.delete(normalizedPath)
    return { ...item, status: 'reverted', message: 'removed created file' }
  }
  if (action === 'noop') {
    return { ...item, status: 'reverted', message: item.message || 'already reverted' }
  }
  return { ...item, status: 'blocked', message: 'unknown revert action' }
}

function fakeFileChangeRevertPayload(body) {
  const changes = Array.isArray(body.changes) ? body.changes.slice(0, 50).map(fakeNormalizeFileChange) : []
  if (changes.length === 0) {
    return { statusCode: 400, payload: { ok: false, error: 'changes required' } }
  }
  const preflight = changes.map(fakePreflightFileChangeRevert)
  const blocked = preflight.some(item => item.status === 'blocked' || item.status === 'conflict')
  const items = blocked
    ? preflight.map(({ normalizedPath, content, action, ...item }) => item)
    : preflight.map(fakeApplyFileChangeRevert)
  const ok = !blocked && items.every(item => item.status === 'reverted')
  const auditPath = 'D:\\Metis\\Smoke\\.metis\\audit\\file-change-transactions.jsonl'
  fakeFileChangeAudit.unshift({
    id: `file-change-audit-${Date.now()}-${fakeFileChangeAudit.length}`,
    created_at: Math.floor(Date.now() / 1000),
    cwd: fakeWorkspaceRoot,
    summary_id: String(body.summary_id || body.summaryId || ''),
    ok,
    items
  })
  fakeFileChangeAudit = fakeFileChangeAudit.slice(0, 50)
  return {
    statusCode: 200,
    payload: {
      ok,
      summary_id: String(body.summary_id || body.summaryId || ''),
      reverted_count: items.filter(item => item.status === 'reverted').length,
      conflict_count: items.filter(item => item.status === 'conflict').length,
      blocked_count: items.filter(item => item.status === 'blocked').length,
      audit_path: auditPath,
      items
    }
  }
}

function fakeSkillsPayload() {
  return {
    skills: fakeSkills.map(({ content, ...skill }) => ({
      ...skill,
      preview: skill.preview || String(content || '').slice(0, 500)
    }))
  }
}

function fakeSkillTitle(content, fallback) {
  const line = String(content || '')
    .split(/\r?\n/)
    .find(item => item.startsWith('# '))
  return line ? line.slice(2).trim() || fallback : fallback
}

function fakeSkillDetail(skillId) {
  const skill = fakeSkills.find(item => item.id === skillId)
  if (!skill) return null
  return {
    ...skill,
    name: fakeSkillTitle(skill.content, skill.name || skill.id),
    preview: String(skill.content || '').slice(0, 500)
  }
}

function fakeMemoryPayload() {
  return {
    global_path: path.join(metisHome(), 'METIS.md'),
    project_path: 'D:\\Metis\\Smoke\\METIS.md',
    global_content: fakeGlobalMemory,
    project_content: fakeProjectMemory,
    auto_memory: true,
    auto_skills: true
  }
}

function fakeMcpStatusPayload() {
  return {
    available: true,
    enabled: true,
    servers: {
      'smoke-mcp': {
        connected: fakeMcpConnected,
        tools_count: fakeMcpConnected ? 2 : 0,
        tools: fakeMcpConnected
          ? [
              { name: 'read_resource', description: 'Read a fake resource for smoke tests.' },
              { name: 'list_resources', description: 'List fake resources.' }
            ]
          : [],
        config: {
          command: 'node',
          args: ['smoke-mcp-server.js'],
          url: ''
        }
      }
    },
    config_sources: [
      {
        label: 'Metis',
        path: path.join(metisHome(), 'mcp.json'),
        exists: true
      },
      {
        label: 'Claude Desktop',
        path: 'C:\\Users\\metis\\AppData\\Roaming\\Claude\\claude_desktop_config.json',
        exists: false
      }
    ]
  }
}

function fakeDeskStatusPayload() {
  return {
    enabled: fakeDeskEnabled,
    paused: fakeDeskPaused,
    port: 0,
    exec_mode: 'human',
    human_core: 'som',
    goal: 'Smoke desktop automation',
    goal_status: fakeDeskPaused ? 'paused' : 'idle',
    goal_running: false,
    vision_status: 'idle',
    vision_running: false,
    vision_goal: '',
    vision_step: 0,
    vision_max_steps: 0,
    vision_som_url: '/api/vision/artifacts/vision_som_latest.png',
    vision_raw_url: '/api/vision/artifacts/vision_raw_latest.png'
  }
}

function fakeProviderProfiles() {
  return [
    {
      provider_id: 'deepseek',
      display_name: 'DeepSeek',
      backend_type: 'openai',
      aliases: ['ds', 'deepseek-chat', 'deepseek-reasoner'],
      base_url: 'https://api.deepseek.com',
      chat_completions_path: '/chat/completions',
      default_model: 'deepseek-v4-flash',
      fallback_models: ['deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-chat', 'deepseek-reasoner'],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: false },
      model_context_windows: {
        'deepseek-v4-flash': 1000000,
        'deepseek-v4-pro': 1000000,
        'deepseek-chat': 128000,
        'deepseek-reasoner': 64000
      },
      model_notes: {
        'deepseek-chat': 'Deprecated at Beijing time 2026-07-24 23:59; use deepseek-v4-flash.',
        'deepseek-reasoner': 'Deprecated at Beijing time 2026-07-24 23:59; use deepseek-v4-pro.'
      }
    },
    {
      provider_id: 'openai',
      display_name: 'OpenAI',
      backend_type: 'openai',
      aliases: ['oai'],
      base_url: 'https://api.openai.com/v1',
      chat_completions_path: '/chat/completions',
      default_model: 'gpt-4o-mini',
      fallback_models: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini'],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: true },
      model_context_windows: {
        'gpt-4o-mini': 128000,
        'gpt-4o': 128000,
        'gpt-4.1-mini': 1047576
      },
      model_notes: {}
    },
    {
      provider_id: 'openai-compatible',
      display_name: 'OpenAI Compatible',
      backend_type: 'openai',
      aliases: ['openai_compat'],
      base_url: '',
      chat_completions_path: '/chat/completions',
      default_model: '',
      fallback_models: [],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: false },
      model_context_windows: {},
      model_notes: {}
    },
    {
      provider_id: 'custom-openai',
      display_name: '自定义 OpenAI 中转站',
      backend_type: 'openai',
      aliases: ['custom', 'custom-openai', 'openai-relay', 'relay-openai'],
      base_url: '',
      chat_completions_path: '/chat/completions',
      default_model: '',
      fallback_models: [],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: false },
      model_context_windows: {},
      model_notes: {}
    },
    {
      provider_id: 'kimi',
      display_name: 'Kimi',
      backend_type: 'openai',
      aliases: ['moonshot', 'moonshotai'],
      base_url: 'https://api.moonshot.cn/v1',
      chat_completions_path: '/chat/completions',
      default_model: 'kimi-k2.6',
      fallback_models: ['kimi-k2.6'],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: false },
      model_context_windows: { 'kimi-k2.6': 262144 },
      model_notes: {}
    },
    {
      provider_id: 'zhipu-glm',
      display_name: 'Zhipu GLM',
      backend_type: 'openai',
      aliases: ['glm', 'zhipu', 'bigmodel'],
      base_url: 'https://open.bigmodel.cn/api/coding/paas/v4',
      chat_completions_path: '/chat/completions',
      default_model: 'glm-5.1',
      fallback_models: ['glm-5.1'],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: false },
      model_context_windows: { 'glm-5.1': 200000 },
      model_notes: {}
    },
    {
      provider_id: 'bailian',
      display_name: 'Bailian / Qwen',
      backend_type: 'openai',
      aliases: ['qwen', 'dashscope', 'aliyun-bailian'],
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      chat_completions_path: '/chat/completions',
      default_model: 'qwen3-coder-plus',
      fallback_models: ['qwen3-coder-plus', 'qwen3-max'],
      api_key_required: true,
      openai_compatible: true,
      capabilities: { stream: true, tools: true, vision: false },
      model_context_windows: { 'qwen3-coder-plus': 1000000, 'qwen3-max': 262144 },
      model_notes: {}
    },
    {
      provider_id: 'anthropic',
      display_name: 'Anthropic',
      backend_type: 'anthropic',
      aliases: ['claude'],
      base_url: '',
      chat_completions_path: '/chat/completions',
      default_model: 'claude-sonnet-4-20250514',
      fallback_models: ['claude-sonnet-4-20250514'],
      api_key_required: true,
      openai_compatible: false,
      capabilities: { stream: true, tools: true, vision: true },
      model_context_windows: { 'claude-sonnet-4-20250514': 200000 },
      model_notes: {}
    },
    {
      provider_id: 'gemini',
      display_name: 'Gemini',
      backend_type: 'gemini',
      aliases: ['google'],
      base_url: '',
      chat_completions_path: '/chat/completions',
      default_model: 'gemini-2.0-flash',
      fallback_models: ['gemini-2.0-flash'],
      api_key_required: true,
      openai_compatible: false,
      capabilities: { stream: true, tools: true, vision: true },
      model_context_windows: { 'gemini-2.0-flash': 1000000 },
      model_notes: {}
    }
  ]
}

function fakeProviderById(providerId) {
  const key = String(providerId || '').trim().toLowerCase()
  return fakeProviderProfiles().find(provider => provider.provider_id === key || provider.aliases.includes(key)) || fakeProviderProfiles()[0]
}

function fakeNormalizeChatUrl(baseUrl, path = '/chat/completions') {
  let base = fakeNormalizeOpenAiApiBaseUrl(baseUrl)
  const suffix = `/${String(path || '/chat/completions').replace(/^\/+|\/+$/g, '')}`
  if (!base) return ''
  return base.toLowerCase().endsWith(suffix.toLowerCase()) ? base : `${base}${suffix}`
}

function fakeNormalizeOpenAiApiBaseUrl(baseUrl) {
  let base = String(baseUrl || '').trim().replace(/\/+$/, '')
  if (!base) return ''
  const lower = base.toLowerCase()
  for (const suffix of ['/chat/completions', '/models', '/usage']) {
    if (lower.endsWith(suffix)) {
      base = base.slice(0, -suffix.length).replace(/\/+$/, '')
      break
    }
  }
  let parsed
  try {
    parsed = new URL(base)
  } catch {
    return base
  }
  const host = parsed.hostname.toLowerCase()
  const pathname = parsed.pathname.replace(/\/+$/, '').toLowerCase()
  if (host.includes('api.deepseek.com')) return base
  const lastSegment = pathname.split('/').filter(Boolean).pop() || ''
  if (/^v\d+$/.test(lastSegment) || pathname.includes('/v1/')) return base
  return `${base}/v1`
}

function fakeLooksLikeForeignModel(provider, model) {
  const providerId = String(provider?.provider_id || '').trim().toLowerCase()
  const name = String(model || '').trim().toLowerCase()
  if (!name) return false
  const localModels = new Set(
    [provider.default_model, ...(provider.fallback_models || []), ...Object.keys(provider.model_context_windows || {})]
      .filter(Boolean)
      .map(item => String(item).toLowerCase())
  )
  if (localModels.has(name)) return false
  const families = {
    deepseek: ['deepseek'],
    openai: ['gpt-', 'o1', 'o3', 'o4', 'o5', 'chatgpt', 'codex'],
    'openai-compatible': [],
    'custom-openai': [],
    kimi: ['kimi', 'moonshot'],
    'zhipu-glm': ['glm', 'zhipu'],
    bailian: ['qwen', 'dashscope'],
    anthropic: ['claude'],
    gemini: ['gemini']
  }
  const ownPrefixes = families[providerId] || []
  if (ownPrefixes.length && ownPrefixes.some(prefix => name.startsWith(prefix))) return false
  if (['openai-compatible', 'custom-openai', 'fake'].includes(providerId)) return false
  return Object.entries(families)
    .filter(([key]) => key !== providerId && !['openai-compatible', 'custom-openai'].includes(key))
    .flatMap(([, prefixes]) => prefixes)
    .some(prefix => name.startsWith(prefix))
}

function fakeNormalizeProviderModel(provider, model) {
  const value = String(model || '').trim()
  if (!value) return { model: String(provider.default_model || '').trim(), warning: '' }
  if (!fakeLooksLikeForeignModel(provider, value)) return { model: value, warning: '' }
  const repaired = String(provider.default_model || '').trim()
  if (!repaired) return { model: value, warning: '' }
  return {
    model: repaired,
    warning: `模型 ${value} 与 ${provider.display_name} 不匹配，已切换为 ${repaired}。`
  }
}

function fakeValidateProviderConfig(data = {}) {
  const rawProvider = String(data.provider || data.provider_id || data.backend || 'deepseek').trim().toLowerCase()
  const baseUrl = String(data.base_url || '').trim()
  const model = String(data.model || '').trim()
  const providerId =
    rawProvider === 'openai' && (baseUrl.includes('api.deepseek.com') || model.startsWith('deepseek'))
      ? 'deepseek'
      : rawProvider || 'deepseek'
  const provider = fakeProviderById(providerId)
  const resolvedBaseUrl = baseUrl || provider.base_url
  const modelRepair = fakeNormalizeProviderModel(provider, model || provider.default_model)
  const resolvedModel = modelRepair.model
  const hasApiKey = Boolean(String(data.api_key || '').trim())
  const chatUrl = provider.openai_compatible ? fakeNormalizeChatUrl(resolvedBaseUrl, provider.chat_completions_path) : ''
  const warnings = []
  if (modelRepair.warning) warnings.push(modelRepair.warning)
  if (provider.model_notes[resolvedModel]) warnings.push(provider.model_notes[resolvedModel])
  if (provider.api_key_required && !hasApiKey) {
    return {
      ok: false,
      code: 'LLM_API_KEY_MISSING',
      title: '未配置 API Key',
      message: '当前模型供应商需要 API Key。',
      hint: '请在设置或首次引导中填入供应商提供的 API Key；本地检查不会保存空 Key。',
      recoverable: false,
      provider,
      provider_id: provider.provider_id,
      display_name: provider.display_name,
      backend: provider.backend_type,
      base_url: resolvedBaseUrl,
      chat_url: chatUrl,
      model: resolvedModel,
      api_key_required: provider.api_key_required,
      has_api_key: false,
      warnings
    }
  }
  return {
    ok: Boolean(resolvedModel && (!provider.openai_compatible || resolvedBaseUrl) && hasApiKey),
    code: 'PROVIDER_CONFIG_OK',
    title: '配置检查通过',
    message: '本次只检查本地配置，没有发起真实模型调用。',
    hint: '真实网络、余额和模型权限会在发送消息时由流式错误分类继续提示。',
    recoverable: false,
    provider,
    provider_id: provider.provider_id,
    display_name: provider.display_name,
    backend: provider.backend_type,
    base_url: resolvedBaseUrl,
    chat_url: chatUrl,
    model: resolvedModel,
    api_key_required: provider.api_key_required,
    has_api_key: hasApiKey,
    warnings
  }
}

function fakeProviderPresetModels(provider) {
  const ids = [provider.default_model, ...provider.fallback_models].filter(Boolean).filter((id, index, arr) => arr.indexOf(id) === index)
  return ids.map(id => ({
    id,
    display_name: id,
    owned_by: provider.display_name,
    type: 'chat',
    created: 0,
    context_limit: provider.model_context_windows?.[id] || 128000,
    chat_capable: true
  }))
}

function fakeProviderModelCatalog(data = {}) {
  const validation = fakeValidateProviderConfig(data)
  const apiBaseUrl = validation.provider.openai_compatible ? fakeNormalizeOpenAiApiBaseUrl(validation.base_url) : validation.base_url
  const presetModels = fakeProviderPresetModels(validation.provider)
  if (!validation.provider.openai_compatible) {
    if (presetModels.length) {
      return {
        ok: true,
        kind: 'models',
        status: 'preset',
        provider_id: validation.provider_id,
        display_name: validation.display_name,
        base_url: validation.base_url,
        api_base_url: apiBaseUrl,
        model: validation.model,
        message: '当前供应商不支持远程模型目录，已显示本地预设模型。',
        hint: '可以选择预设模型，也可以继续手动填写模型名。',
        models: presetModels
      }
    }
    return {
      ok: false,
      kind: 'models',
      status: 'unsupported',
      provider_id: validation.provider_id,
      display_name: validation.display_name,
      base_url: validation.base_url,
      api_base_url: apiBaseUrl,
      model: validation.model,
      message: '当前供应商不支持 OpenAI-compatible /models 目录查询。',
      hint: '可以继续手动填写模型名。',
      models: []
    }
  }
  if (!validation.has_api_key && presetModels.length) {
    return {
      ok: true,
      kind: 'models',
      status: 'preset',
      provider_id: validation.provider_id,
      display_name: validation.display_name,
      base_url: validation.base_url,
      api_base_url: apiBaseUrl,
      model: validation.model,
      message: '尚未填写 API Key，已显示本地预设模型。',
      hint: '填入 API Key 后可再刷新远程模型目录；未保存的 Key 不会被持久化。',
      models_url: `${apiBaseUrl}/models`,
      models: presetModels
    }
  }
  return {
    ok: true,
    kind: 'models',
    status: 'ok',
    provider_id: validation.provider_id,
    display_name: validation.display_name,
    base_url: validation.base_url,
    api_base_url: apiBaseUrl,
    model: validation.model,
    models_url: `${apiBaseUrl}/models`,
    message: '已读取 4 个模型。',
    hint: '选择一个模型会写入当前设置；也可以继续手动填写。',
    models: [
      {
        id: 'gpt-5.5',
        display_name: 'gpt-5.5',
        owned_by: 'pinai',
        type: 'chat',
        created: 0,
        context_limit: 1000000,
        chat_capable: true
      },
      {
        id: 'gpt-5.4',
        display_name: 'gpt-5.4',
        owned_by: 'pinai',
        type: 'chat',
        created: 0,
        context_limit: 1000000,
        chat_capable: true
      },
      {
        id: 'codex-auto-review',
        display_name: 'codex-auto-review',
        owned_by: 'pinai',
        type: 'chat',
        created: 0,
        context_limit: 1000000,
        chat_capable: true
      },
      {
        id: 'gpt-image-2',
        display_name: 'gpt-image-2',
        owned_by: 'pinai',
        type: 'image',
        created: 0,
        context_limit: 128000,
        chat_capable: false
      }
    ]
  }
}

function fakeProviderUsage(data = {}) {
  const validation = fakeValidateProviderConfig(data)
  const apiBaseUrl = validation.provider.openai_compatible ? fakeNormalizeOpenAiApiBaseUrl(validation.base_url) : validation.base_url
  return {
    ok: true,
    kind: 'usage',
    status: 'ok',
    provider_id: validation.provider_id,
    display_name: validation.display_name,
    base_url: validation.base_url,
    api_base_url: apiBaseUrl,
    model: validation.model,
    usage_url: validation.provider_id === 'deepseek' ? `${validation.base_url}/user/balance` : `${apiBaseUrl}/usage`,
    mode: validation.provider_id === 'deepseek' ? 'balance' : 'unrestricted',
    is_valid: true,
    plan_name: validation.provider_id === 'deepseek' ? 'DeepSeek balance' : '钱包余额',
    remaining: validation.provider_id === 'deepseek' ? 27.51 : 462.00576962,
    balance: validation.provider_id === 'deepseek' ? 27.51 : 462.00576962,
    unit: validation.provider_id === 'deepseek' ? 'CNY' : 'USD',
    today: {
      requests: 1119,
      total_tokens: 166270832,
      cost: 139.68617
    },
    total: {
      requests: 12823,
      total_tokens: 1702326130,
      cost: 1400.12
    },
    quota: {},
    message: '额度查询成功。',
    hint: '钱包余额模式不伪造百分比，只显示金额和单位。'
  }
}

function fakeGoalLogPayload() {
  const now = Date.now()
  return {
    log: [
      { ts: now - 2000, action: 'inspect', detail: 'Smoke environment loaded', status: 'done' },
      { ts: now - 1000, action: 'idle', detail: 'No automation task is running', status: 'idle' }
    ]
  }
}

function fakeSearchPayload(query = '') {
  const now = Math.floor(Date.now() / 1000)
  const value = String(query || '').trim()
  if (value.length < 2) {
    return { results: [] }
  }
  return {
    results: [
      {
        session_id: 'search-hit-session',
        title: 'Smoke Search Hit',
        snippet: `User asked about <mark>${value}</mark> inside a previous Metis session.`,
        ts: now - 30,
        score: -1.2,
        workspace_id: 'smoke-workspace',
        workspace_name: 'Smoke Workspace'
      }
    ]
  }
}

function writeFakeSseEvent(res, event) {
  res.write(`data: ${JSON.stringify(event)}\n\n`)
}

function fakeDelay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function fakeRunPublicPayload(run) {
  return {
    run_id: run.id,
    id: run.id,
    session_id: run.session_id,
    assistant_id: run.assistant_id,
    status: run.status,
    phase: run.phase,
    cancel_requested: Boolean(run.cancel_requested),
    created_at: run.created_at,
    updated_at: run.updated_at,
    started_at: run.started_at,
    finished_at: run.finished_at,
    event_count: run.events.length,
    last_seq: run.next_seq - 1,
    error: run.error || ''
  }
}

function fakeRunNotify(run) {
  const waiters = run.waiters.splice(0)
  for (const resolve of waiters) {
    resolve()
  }
}

function fakeRunSetStatus(run, status, phase = '', error = '') {
  run.status = status
  if (phase) {
    run.phase = phase
  }
  if (error) {
    run.error = error
  }
  run.updated_at = fakeNowSeconds()
  if (fakeRunTerminalStates.has(status)) {
    run.finished_at = run.updated_at
  }
  fakeRunNotify(run)
}

function fakeRunAppendEvent(run, rawEvent) {
  const event = { ...(rawEvent || {}) }
  const seq = run.next_seq++
  event.run_id = run.id
  event.runId = run.id
  event.session_id = run.session_id
  event.sessionId = run.session_id
  event.assistant_id = run.assistant_id
  event.assistantId = run.assistant_id
  event.seq = seq
  if (!event.kind && event.type) {
    event.kind = event.type
  }
  if (event.payload && typeof event.payload === 'object' && !Array.isArray(event.payload)) {
    event.payload = {
      ...event.payload,
      run_id: run.id,
      session_id: run.session_id,
      assistant_id: run.assistant_id,
      seq
    }
  }
  run.events.push(event)
  run.updated_at = fakeNowSeconds()
  const kind = event.kind || event.type || ''
  if (kind === 'runtime_status') {
    const phase = event.phase || event.payload?.phase || ''
    if (phase) {
      run.phase = phase
    }
  }
  if (kind === 'error') {
    run.error = event.message || event.payload?.message || 'Fake run failed'
  }
  fakeRunNotify(run)
  return event
}

function fakeRunWait(run, timeoutMs = 15000) {
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      const index = run.waiters.indexOf(done)
      if (index >= 0) {
        run.waiters.splice(index, 1)
      }
      resolve()
    }, timeoutMs)
    function done() {
      clearTimeout(timer)
      resolve()
    }
    run.waiters.push(done)
  })
}

function fakeRunEventText(event) {
  const kind = event.kind || event.type || ''
  if (kind !== 'text_delta' && kind !== 'content') {
    return ''
  }
  return String(event.text || event.payload?.text || '')
}

function fakeRunMessageText(message) {
  if (Array.isArray(message)) {
    const textBlock = message.find(item => item && typeof item === 'object' && item.type === 'text')
    return String(textBlock?.text || '')
  }
  return String(message || '')
}

function fakeCreateRun(body) {
  const message = body.message ?? body.prompt ?? ''
  const sessionId = String(body.session_id || body.sessionId || fakeActiveSessionId || 'smoke-session')
  const activeRun = Array.from(fakeRuns.values())
    .filter(run => run.session_id === sessionId && fakeRunActiveStates.has(run.status))
    .sort((left, right) => right.created_at - left.created_at)[0]
  if (activeRun) {
    return { blocked: true, run: activeRun }
  }

  const now = fakeNowSeconds()
  const run = {
    id: `fake-run-${fakeRunCounter++}`,
    session_id: sessionId,
    assistant_id: String(body.assistant_id || body.assistantId || `assistant-fake-run-${Date.now()}`),
    status: 'queued',
    phase: 'queued',
    cancel_requested: false,
    created_at: now,
    updated_at: now,
    started_at: 0,
    finished_at: 0,
    error: '',
    next_seq: 1,
    events: [],
    waiters: [],
    assistantText: '',
    body: { ...body, message }
  }
  fakeRuns.set(run.id, run)
  const userText = fakeRunMessageText(message)
  if (sessionId === 'smoke-session' && userText) {
    fakeSmokeSessionHistory.push({ role: 'user', content: userText })
  }
  fakeRunWorker(run).catch(error => {
    fakeRunAppendEvent(run, {
      schema: 'metis.agent_event.v1',
      kind: 'error',
      type: 'error',
      payload: {
        code: 'FAKE_RUN_ERROR',
        title: 'Fake run failed',
        message: error?.message || String(error),
        hint: 'The fake backend run registry failed during smoke.'
      }
    })
    fakeRunSetStatus(run, 'failed', 'failed', error?.message || String(error))
  })
  return { blocked: false, run }
}

async function fakeRunWorker(run) {
  run.started_at = fakeNowSeconds()
  fakeRunSetStatus(run, 'running', 'starting')
  let buffer = ''

  const req = Readable.from([JSON.stringify(run.body || {})])
  const res = {
    writeHead() {},
    write(chunk) {
      if (run.cancel_requested) {
        throw new Error('FAKE_RUN_CANCELLED')
      }
      buffer += Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk)
      const packets = buffer.split('\n\n')
      buffer = packets.pop() || ''
      for (const packet of packets) {
        for (const rawLine of packet.split(/\r?\n/)) {
          const line = rawLine.trim()
          if (!line.startsWith('data: ')) {
            continue
          }
          const payload = line.slice(6)
          if (payload === '[DONE]') {
            continue
          }
          try {
            const event = JSON.parse(payload)
            const appended = fakeRunAppendEvent(run, event)
            run.assistantText += fakeRunEventText(appended)
          } catch {
            const appended = fakeRunAppendEvent(run, { type: 'error', message: payload })
            run.assistantText += fakeRunEventText(appended)
          }
        }
      }
      return true
    },
    end() {}
  }

  try {
    await handleFakeChat(req, res)
  } catch (error) {
    if (run.cancel_requested || error?.message === 'FAKE_RUN_CANCELLED') {
      fakeRunAppendEvent(run, {
        schema: 'metis.agent_event.v1',
        kind: 'error',
        type: 'error',
        payload: {
          code: 'RUN_CANCELLED',
          title: '运行已取消',
          message: '本次后台运行已取消。',
          hint: '可以重新发送，或从会话历史继续。',
          recoverable: false
        }
      })
      fakeRunSetStatus(run, 'canceled', 'canceled')
      return
    }
    throw error
  }

  if (run.cancel_requested) {
    fakeRunSetStatus(run, 'canceled', 'canceled')
    return
  }
  if (run.error) {
    fakeRunSetStatus(run, 'failed', 'failed', run.error)
    return
  }
  if (run.session_id === 'smoke-session' && run.assistantText.trim()) {
    fakeSmokeSessionHistory.push({ role: 'assistant', content: run.assistantText.trim() })
  }
  fakeRunSetStatus(run, 'done', 'completed')
}

async function handleFakeRunEvents(req, res, run, afterSeq = 0) {
  let closed = false
  res.on('close', () => {
    closed = true
  })
  res.writeHead(200, {
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Content-Type': 'text/event-stream; charset=utf-8',
    'X-Accel-Buffering': 'no'
  })
  let lastSeq = Math.max(0, Number(afterSeq) || 0)

  while (!closed) {
    const events = run.events.filter(event => Number(event.seq || 0) > lastSeq)
    for (const event of events) {
      lastSeq = Math.max(lastSeq, Number(event.seq || 0))
      writeFakeSseEvent(res, event)
    }
    const hasMore = run.events.some(event => Number(event.seq || 0) > lastSeq)
    if (fakeRunTerminalStates.has(run.status) && !hasMore) {
      res.write('data: [DONE]\n\n')
      res.end()
      return
    }
    if (events.length === 0) {
      await fakeRunWait(run)
    }
  }
}

function fakeLatestActiveRun(sessionId) {
  return Array.from(fakeRuns.values())
    .filter(run => run.session_id === sessionId && fakeRunActiveStates.has(run.status))
    .sort((left, right) => right.created_at - left.created_at)[0]
}

function waitForFakePermission(requestId, timeoutMs = 8000) {
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      fakePermissionRequests.delete(requestId)
      resolve(false)
    }, timeoutMs)
    fakePermissionRequests.set(requestId, approved => {
      clearTimeout(timer)
      fakePermissionRequests.delete(requestId)
      resolve(Boolean(approved))
    })
  })
}

function fakePermissionRulePayload(rule) {
  return {
    id: rule.id,
    tool: rule.tool,
    action: rule.action,
    args_match: rule.args_match || {},
    source: rule.source || 'smoke',
    created_at: rule.created_at,
    updated_at: rule.updated_at
  }
}

function fakeCreatePermissionRule(tool, action, argsMatch = {}, source = 'permission_dialog') {
  const now = fakeNowSeconds()
  const existing = fakePermissionRules.find(
    rule =>
      rule.tool === tool &&
      rule.action === action &&
      rule.source === source &&
      JSON.stringify(rule.args_match || {}) === JSON.stringify(argsMatch || {})
  )
  if (existing) {
    existing.updated_at = now
    return existing
  }
  const rule = {
    id: `perm-rule-${fakePermissionRuleCounter++}`,
    tool,
    action,
    args_match: argsMatch || {},
    source,
    created_at: now,
    updated_at: now
  }
  fakePermissionRules.push(rule)
  return rule
}

function fakeAuditPermission({ requestId, callId, tool, args, approved, remember = '', ruleId = '' }) {
  fakePermissionAudit.unshift({
    id: `perm-audit-${Date.now()}-${fakePermissionAudit.length}`,
    created_at: fakeNowSeconds(),
    workspace_id: 'smoke-workspace',
    session_id: fakeActiveSessionId,
    cwd: 'D:\\Metis\\Smoke',
    request_id: requestId,
    call_id: callId,
    tool,
    action: approved ? 'allow' : 'deny',
    approved,
    remember,
    rule_id: ruleId,
    source: 'permission_dialog',
    arguments: args
  })
  fakePermissionAudit = fakePermissionAudit.slice(0, 50)
}

async function handleFakeChat(req, res) {
  const body = await readFakeBody(req)
  const message = String(body.message || '')

  res.writeHead(200, {
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Content-Type': 'text/event-stream; charset=utf-8',
    'X-Accel-Buffering': 'no'
  })

  if (message.includes('sidebar-running-smoke')) {
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'runtime_status',
      type: 'runtime_status',
      payload: { phase: 'llm_request', message: 'Sidebar running smoke' }
    })
    await fakeDelay(10000)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'text_delta',
      type: 'text_delta',
      payload: { text: 'Sidebar running smoke complete.' }
    })
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'done',
      type: 'done',
      payload: {
        usage: { prompt_tokens: 2, completion_tokens: 3, total_tokens: 5 }
      }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  if (message.includes('legacy')) {
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'runtime_status',
      type: 'runtime_status',
      payload: { phase: 'llm_request', message: 'Legacy runtime check' }
    })
    await fakeDelay(40)
    writeFakeSseEvent(res, { type: 'text_delta', text: 'Legacy ' })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      type: 'tool_call',
      tool: 'list_directory',
      args: { path: '.' },
      call_id: 'legacy-call-1'
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      type: 'tool_result',
      tool: 'list_directory',
      result: ['package.json', 'src'],
      call_id: 'legacy-call-1'
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, { type: 'text_delta', text: 'stream OK.' })
    writeFakeSseEvent(res, {
      type: 'done',
      usage: { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  if (message.includes('subagent')) {
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'runtime_status',
      type: 'runtime_status',
      payload: { phase: 'tool_running', message: 'Running parallel subagents', tool: 'run_parallel_tasks' }
    })
    await fakeDelay(120)
    for (const task of [
      { id: 'subagent-explore', name: 'delegate_explore', summary: '扫描项目结构' },
      { id: 'subagent-shell', name: 'delegate_shell', summary: '读取 package scripts' },
      { id: 'subagent-browser', name: 'delegate_browser', summary: '整理文档线索' }
    ]) {
      writeFakeSseEvent(res, {
        schema: 'metis.agent_event.v1',
        kind: 'subagent_start',
        type: 'subagent_start',
        payload: {
          task_id: task.id,
          name: task.name,
          progress: 5,
          status: 'running',
          summary: task.summary
        }
      })
    }
    await fakeDelay(320)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'subagent_progress',
      type: 'subagent_progress',
      payload: {
        task_id: 'subagent-explore',
        name: 'delegate_explore',
        progress: 45,
        status: 'running',
        summary: '已定位 desktop 与 backend 后端边界'
      }
    })
    await fakeDelay(120)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'subagent_progress',
      type: 'subagent_progress',
      payload: {
        task_id: 'subagent-shell',
        name: 'delegate_shell',
        progress: 62,
        status: 'running',
        summary: '已读取 typecheck 和 smoke 脚本'
      }
    })
    await fakeDelay(120)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'subagent_done',
      type: 'subagent_done',
      payload: {
        task_id: 'subagent-shell',
        name: 'delegate_shell',
        progress: 100,
        status: 'done',
        summary: '脚本检查完成',
        result: 'package.json scripts: typecheck, smoke:desktop, dev'
      }
    })
    await fakeDelay(120)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'subagent_progress',
      type: 'subagent_progress',
      payload: {
        task_id: 'subagent-browser',
        name: 'delegate_browser',
        progress: 70,
        status: 'running',
        summary: '已整理右栏预览验收点'
      }
    })
    await fakeDelay(120)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'subagent_done',
      type: 'subagent_done',
      payload: {
        task_id: 'subagent-explore',
        name: 'delegate_explore',
        progress: 100,
        status: 'done',
        summary: '结构扫描完成',
        result: 'Found chatStore, SubagentGroup, rendererSmoke, and fake backend event stream.'
      }
    })
    await fakeDelay(120)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'subagent_done',
      type: 'subagent_done',
      payload: {
        task_id: 'subagent-browser',
        name: 'delegate_browser',
        progress: 100,
        status: 'done',
        summary: '文档线索完成',
        result: 'NEW-34 smoke confirms the parallel subagent panel and right rail result handoff.'
      }
    })
    await fakeDelay(14)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'text_delta',
      type: 'text_delta',
      payload: { text: 'Parallel subagents finished.' }
    })
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'done',
      type: 'done',
      payload: {
        usage: { prompt_tokens: 8, completion_tokens: 9, total_tokens: 17 }
      }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  if (message.includes('tool-error')) {
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'runtime_status',
      type: 'runtime_status',
      payload: { phase: 'tool_running', message: 'Tool error smoke', tool: 'read_file' }
    })
    await fakeDelay(24)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'tool_call',
      type: 'tool_call',
      payload: {
        tool: 'read_file',
        args: { path: 'missing.txt' },
        call_id: 'error-call-1'
      }
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'tool_result',
      type: 'tool_result',
      payload: {
        tool: 'read_file',
        result: 'Error executing read_file: FileNotFoundError: missing.txt',
        call_id: 'error-call-1'
      }
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'runtime_status',
      type: 'runtime_status',
      payload: { phase: 'tool_done', message: 'Tool error smoke done', tool: 'read_file' }
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'text_delta',
      type: 'text_delta',
      payload: { text: 'Tool error smoke complete.' }
    })
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'done',
      type: 'done',
      payload: {
        usage: { prompt_tokens: 5, completion_tokens: 6, total_tokens: 11 }
      }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  if (message.includes('local-preview')) {
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'text_delta',
      type: 'text_delta',
      payload: { text: 'UI preview ready at http://127.0.0.1:5174 for interactive inspection.' }
    })
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'done',
      type: 'done',
      payload: {
        usage: { prompt_tokens: 4, completion_tokens: 5, total_tokens: 9 }
      }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  if (
    message.includes('permission-allow') ||
    message.includes('permission-deny') ||
    message.includes('permission-remember-allow') ||
    message.includes('permission-remember-deny')
  ) {
    const shouldDeny = message.includes('permission-deny') || message.includes('permission-remember-deny')
    const shouldRemember = message.includes('permission-remember')
    const requestId = shouldDeny
      ? shouldRemember
        ? 'perm-remember-deny-1'
        : 'perm-deny-1'
      : shouldRemember
      ? 'perm-remember-allow-1'
      : 'perm-allow-1'
    const callId = shouldDeny
      ? shouldRemember
        ? 'permission-remember-deny-call-1'
        : 'permission-deny-call-1'
      : shouldRemember
      ? 'permission-remember-allow-call-1'
      : 'permission-allow-call-1'
    const tool = shouldDeny ? 'delete_file' : 'write_file'
    const args = shouldDeny
      ? { path: 'D:\\Metis\\Smoke\\danger.txt' }
      : { path: 'D:\\Metis\\Smoke\\approval.txt', content: 'fake smoke content' }

    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'tool_call',
      type: 'tool_call',
      payload: { tool, args, call_id: callId }
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'permission_request',
      type: 'permission_request',
      payload: { tool, args, call_id: callId, request_id: requestId }
    })
    const approved = await waitForFakePermission(requestId)
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'tool_result',
      type: 'tool_result',
      payload: {
        tool,
        result: approved
          ? `Fake permission approved for ${tool}; no real file operation was executed.`
          : `[Permission denied] User declined execution of '${tool}'.`,
        call_id: callId
      }
    })
    await fakeDelay(12)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'text_delta',
      type: 'text_delta',
      payload: { text: approved ? 'Permission approved smoke complete.' : 'Permission denied smoke complete.' }
    })
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'done',
      type: 'done',
      payload: {
        usage: { prompt_tokens: 6, completion_tokens: 7, total_tokens: 13 }
      }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  if (message.includes('error')) {
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'runtime_status',
      type: 'runtime_status',
      payload: { phase: 'failed', message: 'Smoke runtime failed', recoverable: false }
    })
    await fakeDelay(40)
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'error',
      type: 'error',
      payload: {
        code: 'SMOKE_ERROR',
        title: 'Smoke stream error',
        message: 'Fake backend emitted a controlled stream error.',
        hint: 'This is expected during NEW-22 smoke.'
      }
    })
    writeFakeSseEvent(res, {
      schema: 'metis.agent_event.v1',
      kind: 'done',
      type: 'done',
      payload: { usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } }
    })
    res.write('data: [DONE]\n\n')
    res.end()
    return
  }

  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'runtime_status',
    type: 'runtime_status',
    payload: { phase: 'llm_request', message: 'Standard runtime check' }
  })
  await fakeDelay(40)
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'text_delta',
    type: 'text_delta',
    payload: { text: 'Hello ' }
  })
  await fakeDelay(12)
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'tool_call',
    type: 'tool_call',
    payload: {
      tool: 'read_file',
      args: { path: 'package.json' },
      call_id: 'standard-call-1'
    }
  })
  await fakeDelay(12)
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'tool_result',
    type: 'tool_result',
    payload: {
      tool: 'read_file',
      result: '{"name":"metis-desktop"}',
      call_id: 'standard-call-1'
    }
  })
  await fakeDelay(12)
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'text_delta',
    type: 'text_delta',
    payload: { text: 'from fake backend. [Smoke link](https://example.com).' }
  })
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'memory_nudge',
    type: 'memory_nudge',
    payload: {
      message: 'Smoke memory nudge',
      memory_count: 1,
      skill_count: 1,
      memory_path: path.join(metisHome(), 'METIS.md'),
      skill_path: path.join(metisHome(), 'skills', 'smoke-skill', 'SKILL.md')
    }
  })
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'done',
    type: 'done',
    payload: {
      usage: { prompt_tokens: 11, completion_tokens: 13, total_tokens: 24 }
    }
  })
  res.write('data: [DONE]\n\n')
  res.end()
}

async function handleFakeSideChat(req, res) {
  const body = await readFakeBody(req)
  const messages = Array.isArray(body.messages) ? body.messages : []
  const lastUser = messages
    .slice()
    .reverse()
    .find(message => String(message?.role || '') === 'user')
  const prompt = String(lastUser?.content || body.message || body.prompt || '').trim()

  res.writeHead(200, {
    'Access-Control-Allow-Origin': '*',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'Content-Type': 'text/event-stream; charset=utf-8',
    'X-Accel-Buffering': 'no'
  })

  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'runtime_status',
    type: 'runtime_status',
    payload: { phase: 'llm_request', message: 'Fake standalone side chat' }
  })
  await fakeDelay(16)
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'text_delta',
    type: 'text_delta',
    payload: { text: 'Side chat reply ' }
  })
  await fakeDelay(10)
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'text_delta',
    type: 'text_delta',
    payload: { text: `for "${prompt || 'empty'}" without agent context.` }
  })
  writeFakeSseEvent(res, {
    schema: 'metis.agent_event.v1',
    kind: 'done',
    type: 'done',
    payload: {
      usage: { prompt_tokens: 2, completion_tokens: 6, total_tokens: 8 }
    }
  })
  res.write('data: [DONE]\n\n')
  res.end()
}

async function handleFakeRequest(req, res) {
  if (req.method === 'OPTIONS') {
    writeFakeJson(res, 204, {})
    return
  }

  const url = new URL(req.url || '/', 'http://127.0.0.1')
  const pathname = url.pathname

  if (req.method === 'GET' && pathname === '/health') {
    writeFakeJson(res, 200, { ok: true, backend: 'fake' })
    return
  }

  if (req.method === 'POST' && pathname === '/upload/parse') {
    writeFakeJson(res, 200, await fakeUploadParsePayload(req))
    return
  }

  if (req.method === 'GET' && pathname === '/settings') {
    const validation = fakeValidateProviderConfig({
      backend: 'deepseek',
      base_url: 'https://api.deepseek.com',
      model: 'deepseek-v4-flash',
      api_key: 'fake-smoke-key'
    })
    writeFakeJson(res, 200, {
      backend: 'deepseek',
      provider_id: 'deepseek',
      provider: validation.provider,
      base_url: 'https://api.deepseek.com',
      model: 'deepseek-v4-flash',
      temperature: 0.2,
      max_tokens: 4096,
      api_key: '',
      has_api_key: true,
      auto_memory: true,
      auto_skills: true,
      proxy_mode: 'custom',
      proxy_scheme: 'http',
      proxy_host: '127.0.0.1',
      proxy_port: '7890',
      proxy_bypass: 'localhost,127.0.0.1,::1',
      terminal_shell: 'powershell',
      provider_validation: validation
    })
    return
  }

  if (req.method === 'POST' && pathname === '/settings') {
    writeFakeJson(res, 200, { ok: true, updated: ['settings'] })
    return
  }

  if (req.method === 'GET' && pathname === '/settings/runtime-manager') {
    const bundlePath = path.join(metisHome(), 'vm_bundles', 'metisvm.bundle')
    writeFakeJson(res, 200, {
      ok: true,
      schema: 'metis.runtime_manager.v1',
      generated_at: fakeNowSeconds(),
      root: fakeWorkspaceRoot,
      health: {
        preferred_backend: 'local',
        ready: true,
        metis_wsl_ready: false,
        wsl_available: false,
        docker_available: false,
        rootfs_ready: false,
        vm_pack_ready: false,
        runtime_bundle_ready: false,
        vm_runtime_installed: false,
        vm_guest_protocol_ready: false,
        vm_hcs_direct_ready: false,
        vm_assets_verified: false,
        vm_asset_bytes: 0,
        bundled_runtime_pack_available: false,
        runtime_download_available: false
      },
      paths: {
        root: fakeWorkspaceRoot,
        rootfs: '',
        wsl_install_dir: '',
        bundle_path: '',
        vm_runtime_bundle: bundlePath,
        runtime_pack_install_dir: bundlePath,
        bundled_runtime_pack: path.resolve(__dirname, '..', 'resources', 'runtime-pack'),
        runtime_bundle_manifest: '',
        artifacts_root: path.join(fakeWorkspaceRoot, '.metis', 'artifacts'),
        diagnostics_root: path.join(fakeWorkspaceRoot, '.metis', 'diagnostics'),
        runtime_jobs_root: path.join(fakeWorkspaceRoot, '.metis', 'runtime-jobs')
      },
      actions: [
        {
          id: 'repair-runtime',
          label: 'Repair or install VM runtime',
          status: 'blocked',
          description: 'Fake backend has no bundled runtime pack.'
        },
        {
          id: 'diagnostics',
          label: 'Export runtime diagnostics',
          status: 'ready',
          description: 'Collect fake runtime diagnostics.'
        }
      ],
      notes: ['Fake backend runtime manager payload.'],
      sandbox: {},
      rootfs: {},
      builder: {},
      vm_bundle: {},
      vm_runtime: {
        installed: false,
        install_dir: bundlePath,
        bundle_path: bundlePath,
        bundle_detected: false,
        metis_owned: false,
        runner_ready: false,
        guest_protocol_ready: false,
        hcs_direct_ready: false,
        runner_transport: '',
        assets_verified: false,
        asset_bytes: 0,
        missing_required: ['vmlinuz', 'initrd', 'rootfs.vhdx', 'metis-bin.vhdx'],
        asset_report: {},
        selected_bundle: {},
        candidate_count: 0,
        reason: 'fake backend has no runtime pack',
        host: {}
      },
      release_integration: {
        ok: true,
        schema: 'metis.runtime_manager.release_integration.v1',
        install_strategy: 'manual',
        installed_path: bundlePath,
        bundled_available: false,
        bundled_path: path.resolve(__dirname, '..', 'resources', 'runtime-pack'),
        download_available: false,
        download_url: '',
        auto_prepare_enabled: false,
        installed_report: {},
        bundled_report: {},
        strategies: [],
        notes: []
      },
      runtime_bundle: {},
      wsl_runtime: {},
      sessions: { sessions: [] },
      jobs: { jobs: [] }
    })
    return
  }

  if (req.method === 'POST' && pathname.startsWith('/settings/runtime-manager/')) {
    const action = pathname.split('/').pop() || 'action'
    writeFakeJson(res, 200, {
      ok: action !== 'repair',
      schema: `fake.runtime_manager.${action}.v1`,
      message: action === 'repair' ? 'Fake backend cannot repair runtime packs.' : `Fake runtime manager action completed: ${action}`,
      error: action === 'repair' ? 'Fake backend has no bundled runtime pack.' : ''
    })
    return
  }

  if (req.method === 'POST' && pathname === '/workspace/file-changes/revert') {
    const body = await readFakeBody(req)
    const result = fakeFileChangeRevertPayload(body)
    writeFakeJson(res, result.statusCode, result.payload)
    return
  }

  if (req.method === 'GET' && pathname === '/providers') {
    const active = fakeValidateProviderConfig({
      backend: 'deepseek',
      base_url: 'https://api.deepseek.com',
      model: 'deepseek-v4-flash',
      api_key: 'fake-smoke-key'
    })
    writeFakeJson(res, 200, {
      providers: fakeProviderProfiles(),
      active,
      settings: {
        backend: 'deepseek',
        provider_id: 'deepseek',
        base_url: 'https://api.deepseek.com',
        model: 'deepseek-v4-flash',
        has_api_key: true
      }
    })
    return
  }

  if (req.method === 'POST' && (pathname === '/providers/verify' || pathname === '/first-run/verify')) {
    const body = await readFakeBody(req)
    writeFakeJson(res, 200, fakeValidateProviderConfig(body))
    return
  }

  if (req.method === 'POST' && pathname === '/providers/models') {
    const body = await readFakeBody(req)
    writeFakeJson(res, 200, fakeProviderModelCatalog(body))
    return
  }

  if (req.method === 'POST' && pathname === '/providers/usage') {
    const body = await readFakeBody(req)
    writeFakeJson(res, 200, fakeProviderUsage(body))
    return
  }

  if (req.method === 'POST' && pathname === '/compact') {
    writeFakeJson(res, 200, fakeCompactSmokeSession())
    return
  }

  if (req.method === 'GET' && pathname === '/compact/status') {
    writeFakeJson(res, 200, fakeCompactStatus)
    return
  }

  if (req.method === 'POST' && pathname === '/permission') {
    const body = await readFakeBody(req)
    const requestId = String(body.request_id || '')
    const resolve = fakePermissionRequests.get(requestId)
    if (!resolve) {
      writeFakeJson(res, 404, { error: 'no pending permission request' })
      return
    }
    const approved = Boolean(body.approved)
    const remember = String(body.remember || '')
    const tool = String(body.tool || '')
    const args = body.args && typeof body.args === 'object' ? body.args : {}
    const callId = String(body.call_id || '')
    let ruleId = ''
    if ((remember === 'allow' || remember === 'deny') && tool) {
      const rule = fakeCreatePermissionRule(tool, remember, {}, 'permission_dialog')
      ruleId = rule.id
    }
    fakeAuditPermission({ requestId, callId, tool, args, approved, remember, ruleId })
    resolve(approved)
    writeFakeJson(res, 200, { ok: true, approved, remember, rule_id: ruleId })
    return
  }

  if (req.method === 'GET' && pathname === '/permissions') {
    writeFakeJson(res, 200, {
      rules: fakePermissionRules.map(fakePermissionRulePayload),
      audit: fakePermissionAudit,
      path: 'D:\\Metis\\Smoke\\.metis\\permissions.json',
      legacy_path: 'D:\\Metis\\Smoke\\.miro\\permissions.json',
      audit_path: 'D:\\Metis\\Smoke\\.metis\\audit\\tool-permissions.jsonl'
    })
    return
  }

  if (req.method === 'POST' && pathname === '/permissions') {
    const body = await readFakeBody(req)
    const tool = String(body.tool || '').trim()
    const action = String(body.action || '').trim()
    if (!tool || !['allow', 'deny', 'ask'].includes(action)) {
      writeFakeJson(res, 400, { error: 'tool and valid action required' })
      return
    }
    const rule = fakeCreatePermissionRule(tool, action, body.args_match || {}, String(body.source || 'settings'))
    writeFakeJson(res, 200, { ok: true, rule: fakePermissionRulePayload(rule) })
    return
  }

  if (req.method === 'DELETE' && pathname.startsWith('/permissions/')) {
    const ruleId = decodeURIComponent(pathname.slice('/permissions/'.length))
    const before = fakePermissionRules.length
    fakePermissionRules = fakePermissionRules.filter(rule => rule.id !== ruleId)
    if (fakePermissionRules.length === before) {
      writeFakeJson(res, 404, { error: 'rule not found' })
      return
    }
    writeFakeJson(res, 200, { ok: true, id: ruleId })
    return
  }

  if (req.method === 'GET' && pathname === '/first-run') {
    writeFakeJson(res, 200, {
      first_run: false,
      has_api_key: true,
      has_config: true,
      config_path: '',
      legacy_config_path: ''
    })
    return
  }

  if (req.method === 'GET' && pathname === '/skills') {
    writeFakeJson(res, 200, fakeSkillsPayload())
    return
  }

  if (req.method === 'POST' && pathname === '/skills/import') {
    const body = await readFakeBody(req)
    const source = String(body.path || 'D:\\Metis\\ImportedSkill')
    const id = fakeSkills.some(skill => skill.id === 'imported-smoke-skill') ? `imported-smoke-skill-${Date.now()}` : 'imported-smoke-skill'
    const content = '# Imported Smoke Skill\n\n## Trigger\nImported from a fake local SKILL.md path.\n'
    const skill = {
      id,
      name: 'Imported Smoke Skill',
      path: `${source.replace(/[\\/]$/, '')}\\SKILL.md`,
      enabled: true,
      preview: content.slice(0, 500),
      content
    }
    fakeSkills.push(skill)
    writeFakeJson(res, 200, { ok: true, skill })
    return
  }

  if (req.method === 'GET' && pathname.startsWith('/skills/')) {
    const skillId = decodeURIComponent(pathname.slice('/skills/'.length))
    const skill = fakeSkillDetail(skillId)
    if (!skill) {
      writeFakeJson(res, 404, { error: 'Skill not found' })
      return
    }
    writeFakeJson(res, 200, skill)
    return
  }

  if (req.method === 'POST' && pathname.endsWith('/toggle') && pathname.startsWith('/skills/')) {
    const skillId = decodeURIComponent(pathname.slice('/skills/'.length, -'/toggle'.length))
    const body = await readFakeBody(req)
    const skill = fakeSkills.find(item => item.id === skillId)
    if (!skill) {
      writeFakeJson(res, 404, { error: 'Skill not found' })
      return
    }
    skill.enabled = Boolean(body.enabled)
    writeFakeJson(res, 200, { ok: true, skill: fakeSkillDetail(skillId) })
    return
  }

  if (req.method === 'POST' && pathname.endsWith('/open-folder') && pathname.startsWith('/skills/')) {
    const skillId = decodeURIComponent(pathname.slice('/skills/'.length, -'/open-folder'.length))
    const skill = fakeSkills.find(item => item.id === skillId)
    if (!skill) {
      writeFakeJson(res, 404, { error: 'Skill not found' })
      return
    }
    writeFakeJson(res, 200, { ok: true, path: path.dirname(skill.path) })
    return
  }

  if (req.method === 'POST' && pathname.startsWith('/skills/')) {
    const skillId = decodeURIComponent(pathname.slice('/skills/'.length))
    const body = await readFakeBody(req)
    const skill = fakeSkills.find(item => item.id === skillId)
    if (!skill) {
      writeFakeJson(res, 404, { error: 'Skill not found' })
      return
    }
    skill.content = String(body.content || '')
    skill.name = fakeSkillTitle(skill.content, skill.name || skill.id)
    skill.preview = skill.content.slice(0, 500)
    writeFakeJson(res, 200, { ok: true, skill: fakeSkillDetail(skillId) })
    return
  }

  if (req.method === 'DELETE' && pathname.startsWith('/skills/')) {
    const skillId = decodeURIComponent(pathname.slice('/skills/'.length))
    const before = fakeSkills.length
    fakeSkills = fakeSkills.filter(skill => skill.id !== skillId)
    if (fakeSkills.length === before) {
      writeFakeJson(res, 404, { error: 'Skill not found' })
      return
    }
    writeFakeJson(res, 200, { ok: true, id: skillId })
    return
  }

  if (req.method === 'GET' && pathname === '/memory') {
    writeFakeJson(res, 200, fakeMemoryPayload())
    return
  }

  if (req.method === 'POST' && pathname === '/memory') {
    const body = await readFakeBody(req)
    if (typeof body.global_content === 'string') {
      fakeGlobalMemory = body.global_content
    }
    if (typeof body.project_content === 'string') {
      fakeProjectMemory = body.project_content
    }
    writeFakeJson(res, 200, { ok: true })
    return
  }

  if (req.method === 'GET' && pathname === '/mcp/status') {
    writeFakeJson(res, 200, fakeMcpStatusPayload())
    return
  }

  if (req.method === 'POST' && pathname === '/mcp/reconnect') {
    const body = await readFakeBody(req)
    if (!body.server) {
      writeFakeJson(res, 400, { error: 'missing server name' })
      return
    }
    fakeMcpConnected = true
    writeFakeJson(res, 200, { success: true, tools_count: 2 })
    return
  }

  if (req.method === 'POST' && pathname === '/mcp/disconnect') {
    const body = await readFakeBody(req)
    if (!body.server) {
      writeFakeJson(res, 400, { error: 'missing server name' })
      return
    }
    fakeMcpConnected = false
    writeFakeJson(res, 200, { success: true })
    return
  }

  if (req.method === 'GET' && pathname === '/api/status') {
    writeFakeJson(res, 200, fakeDeskStatusPayload())
    return
  }

  if (req.method === 'POST' && pathname === '/api/enabled') {
    const body = await readFakeBody(req)
    fakeDeskEnabled = Boolean(body.enabled)
    writeFakeJson(res, 200, { ok: true, enabled: fakeDeskEnabled })
    return
  }

  if (req.method === 'POST' && pathname === '/api/pause') {
    fakeDeskPaused = true
    writeFakeJson(res, 200, { ok: true })
    return
  }

  if (req.method === 'POST' && pathname === '/api/resume') {
    fakeDeskPaused = false
    writeFakeJson(res, 200, { ok: true })
    return
  }

  if (req.method === 'GET' && pathname === '/api/goal/log') {
    writeFakeJson(res, 200, fakeGoalLogPayload())
    return
  }

  if (req.method === 'GET' && pathname === '/cron') {
    writeFakeJson(res, 200, { tasks: fakeCronTasks.map(fakeCronPayload) })
    return
  }

  if (req.method === 'POST' && pathname === '/cron') {
    const body = await readFakeBody(req)
    if (!String(body.prompt || '').trim()) {
      writeFakeJson(res, 400, { error: 'prompt required' })
      return
    }
    const task = makeFakeCronTask(body)
    fakeCronTasks.push(task)
    writeFakeJson(res, 200, fakeCronPayload(task))
    return
  }

  if (req.method === 'POST' && pathname.startsWith('/cron/') && pathname.endsWith('/toggle')) {
    const taskId = decodeURIComponent(pathname.slice('/cron/'.length, -'/toggle'.length))
    const task = fakeCronTasks.find(item => item.id === taskId)
    if (!task) {
      writeFakeJson(res, 404, { error: 'task not found' })
      return
    }
    task.enabled = !task.enabled
    task.nextRun = fakeCronNextRun(task.schedule)
    writeFakeJson(res, 200, fakeCronPayload(task))
    return
  }

  if (req.method === 'POST' && pathname.startsWith('/cron/') && pathname.endsWith('/run')) {
    const taskId = decodeURIComponent(pathname.slice('/cron/'.length, -'/run'.length))
    const task = fakeCronTasks.find(item => item.id === taskId)
    if (!task) {
      writeFakeJson(res, 404, { error: 'task not found' })
      return
    }
    task.lastRun = fakeNowSeconds()
    task.nextRun = fakeCronNextRun(task.schedule)
    task.lastStatus = 'ok'
    task.lastSessionId = 'cron-result-session'
    fakeCronResultReady = true
    writeFakeJson(res, 200, { ok: true, session_id: 'cron-result-session' })
    return
  }

  if (req.method === 'POST' && pathname.startsWith('/cron/')) {
    const taskId = decodeURIComponent(pathname.slice('/cron/'.length))
    const body = await readFakeBody(req)
    if (!String(body.prompt || '').trim()) {
      writeFakeJson(res, 400, { error: 'prompt required' })
      return
    }
    const task = fakeCronTasks.find(item => item.id === taskId)
    if (!task) {
      writeFakeJson(res, 404, { error: 'task not found' })
      return
    }
    const schedule = String(body.schedule || task.schedule || 'every 1 minute').trim() || 'every 1 minute'
    const scheduleChanged = schedule !== task.schedule
    task.name = String(body.name || task.name || 'Scheduled task').trim() || 'Scheduled task'
    task.schedule = schedule
    task.prompt = String(body.prompt || '').trim()
    task.workspace_id = String(body.workspace_id || task.workspace_id || 'smoke-workspace')
    if (body.enabled !== undefined) {
      task.enabled = Boolean(body.enabled)
    }
    if (scheduleChanged) {
      task.nextRun = fakeCronNextRun(schedule)
    }
    writeFakeJson(res, 200, fakeCronPayload(task))
    return
  }

  if (req.method === 'DELETE' && pathname.startsWith('/cron/')) {
    const taskId = decodeURIComponent(pathname.slice('/cron/'.length))
    const before = fakeCronTasks.length
    fakeCronTasks = fakeCronTasks.filter(item => item.id !== taskId)
    if (fakeCronTasks.length === before) {
      writeFakeJson(res, 404, { error: 'task not found' })
      return
    }
    writeFakeJson(res, 200, { ok: true, id: taskId })
    return
  }

  if (req.method === 'GET' && pathname === '/sessions') {
    writeFakeJson(res, 200, fakeSessionPayload())
    return
  }

  if (
    req.method === 'GET' &&
    (pathname === '/sessions/smoke-session' || pathname === '/sessions/search-hit-session' || pathname === '/sessions/cron-result-session')
  ) {
    const now = fakeBackendStartedAt
    const isSearchHit = pathname.endsWith('/search-hit-session')
    const isCronResult = pathname.endsWith('/cron-result-session')
    writeFakeJson(res, 200, {
      id: isCronResult ? 'cron-result-session' : isSearchHit ? 'search-hit-session' : 'smoke-session',
      title: isCronResult ? '[Cron] Smoke Cron Task Edited' : isSearchHit ? 'Smoke Search Hit' : 'Smoke Session',
      workspace_id: 'smoke-workspace',
      mode: 'auto',
      history: isCronResult
        ? [
            { role: 'user', content: 'Run edited smoke cron' },
            { role: 'assistant', content: 'Cron smoke result saved by the fake backend.' }
          ]
        : isSearchHit
        ? [
            { role: 'user', content: 'Find the smoke-search needle in old work.' },
            { role: 'assistant', content: 'The smoke-search result is here.' }
          ]
        : fakeSmokeSessionHistory,
      created_at: now,
      updated_at: now
    })
    return
  }

  if (
    req.method === 'POST' &&
    (pathname === '/sessions/smoke-session/switch' ||
      pathname === '/sessions/search-hit-session/switch' ||
      pathname === '/sessions/cron-result-session/switch')
  ) {
    fakeActiveSessionId = pathname.includes('cron-result-session')
      ? 'cron-result-session'
      : pathname.includes('search-hit-session')
      ? 'search-hit-session'
      : 'smoke-session'
    writeFakeJson(res, 200, { ok: true, session_id: fakeActiveSessionId })
    return
  }

  if (req.method === 'GET' && pathname === '/search') {
    writeFakeJson(res, 200, fakeSearchPayload(url.searchParams.get('q') || ''))
    return
  }

  if (req.method === 'GET' && pathname === '/runs') {
    const sessionId = String(url.searchParams.get('session_id') || url.searchParams.get('sessionId') || '')
    const runs = Array.from(fakeRuns.values())
      .filter(run => !sessionId || run.session_id === sessionId)
      .sort((left, right) => right.created_at - left.created_at)
      .slice(0, 50)
      .map(fakeRunPublicPayload)
    writeFakeJson(res, 200, { runs })
    return
  }

  if (req.method === 'POST' && pathname === '/runs') {
    const body = await readFakeBody(req)
    const message = body.message ?? body.prompt ?? ''
    if (!fakeRunMessageText(message).trim()) {
      writeFakeJson(res, 400, { error: 'No message provided' })
      return
    }
    const created = fakeCreateRun(body)
    if (created.blocked) {
      writeFakeJson(res, 409, { ok: false, error: 'session already has an active run', run: fakeRunPublicPayload(created.run) })
      return
    }
    writeFakeJson(res, 200, { ok: true, ...fakeRunPublicPayload(created.run) })
    return
  }

  if (pathname.startsWith('/runs/')) {
    const suffix = pathname.slice('/runs/'.length)
    const [encodedRunId, action] = suffix.split('/')
    const runId = decodeURIComponent(encodedRunId || '')
    const run = fakeRuns.get(runId)
    if (!run) {
      writeFakeJson(res, 404, { error: 'run not found' })
      return
    }
    if (req.method === 'GET' && !action) {
      writeFakeJson(res, 200, fakeRunPublicPayload(run))
      return
    }
    if (req.method === 'GET' && action === 'events') {
      await handleFakeRunEvents(req, res, run, url.searchParams.get('after') || url.searchParams.get('after_seq') || 0)
      return
    }
    if ((req.method === 'POST' || req.method === 'DELETE') && action === 'cancel') {
      if (!fakeRunTerminalStates.has(run.status)) {
        run.cancel_requested = true
        fakeRunSetStatus(run, 'canceling', 'cancel_requested')
        fakeRunAppendEvent(run, {
          schema: 'metis.agent_event.v1',
          kind: 'runtime_status',
          type: 'runtime_status',
          payload: {
            phase: 'cancel_requested',
            message: 'Cancel requested',
            recoverable: false
          }
        })
      }
      writeFakeJson(res, 200, { ok: true, ...fakeRunPublicPayload(run) })
      return
    }
  }

  if (req.method === 'GET' && pathname.startsWith('/sessions/') && pathname.endsWith('/runs/active')) {
    const sessionId = decodeURIComponent(pathname.slice('/sessions/'.length, -'/runs/active'.length))
    const run = fakeLatestActiveRun(sessionId)
    writeFakeJson(res, 200, run ? { ok: true, run: fakeRunPublicPayload(run) } : { ok: false, run: null })
    return
  }

  if (req.method === 'GET' && pathname === '/workspaces') {
    writeFakeJson(res, 200, fakeWorkspacePayload())
    return
  }

  if (req.method === 'GET' && pathname === '/workspace/tree') {
    writeFakeJson(res, 200, fakeWorkspaceTreePayload())
    return
  }

  if (req.method === 'GET' && pathname === '/workspace/file') {
    const filePayload = fakeWorkspaceFilePayload(url.searchParams.get('path') || '')
    if (!filePayload) {
      writeFakeJson(res, 404, { error: 'fake workspace file not found' })
      return
    }
    writeFakeJson(res, 200, filePayload)
    return
  }

  if (req.method === 'POST' && pathname === '/chat') {
    await handleFakeChat(req, res)
    return
  }

  if (req.method === 'POST' && pathname === '/side-chat') {
    await handleFakeSideChat(req, res)
    return
  }

  writeFakeJson(res, 404, { error: `fake backend route not found: ${req.method} ${pathname}` })
}

async function startFakeBackend(emit = () => {}) {
  if (fakeServer && fakeServer.listening && fakeServerPort) {
    emitBoot(emit, { phase: 'ready', title: 'Fake backend ready', port: fakeServerPort })
    return fakeServerPort
  }

  const port = await findFreePort()
  fakeServer = http.createServer((req, res) => {
    handleFakeRequest(req, res).catch(error => {
      writeFakeJson(res, 500, { error: error?.message || String(error) })
    })
  })

  await new Promise((resolve, reject) => {
    fakeServer.once('error', reject)
    fakeServer.listen(port, '127.0.0.1', resolve)
  })

  fakeServerPort = port
  child = {
    killed: false,
    __metisReady: true,
    __metisPort: port,
    kill() {
      this.killed = true
      if (fakeServer) {
        fakeServer.close()
      }
      fakeServer = null
      fakeServerPort = null
    }
  }

  emitBoot(emit, {
    phase: 'ready',
    title: 'Fake Metis backend ready',
    port
  })
  return port
}

function emitBackendData(source, data, emit) {
  const text = data.toString('utf8')
  for (const rawLine of text.split(/\r?\n/)) {
    const lineText = rawLine.trimEnd()
    if (!lineText) {
      continue
    }
    const lineSource = classifyBackendLogSource(source, lineText)
    const line = `[${lineSource}] ${lineText}`
    appendLogLine(line)
    emitBoot(emit, { phase: 'log', line })
  }
}

function classifyBackendLogSource(source, lineText) {
  if (source !== 'flask:err') return source
  const text = String(lineText || '')
  const isWerkzeugInfo = /\b-\s+\[INFO\]\s+-\s+werkzeug\s+-\s+/.test(text)
  const isAccessLog = /\b\d{1,3}(?:\.\d{1,3}){3}\s+-\s+-\s+\[[^\]]+\]\s+"[A-Z]+\s+[^"]+\s+HTTP\/1\.[01]"\s+[23]\d\d\b/.test(text)
  if (isWerkzeugInfo || isAccessLog) return 'flask'
  return source
}

function normalizedPathForCompare(value) {
  const resolved = path.resolve(String(value || ''))
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

function isSamePath(left, right) {
  return normalizedPathForCompare(left) === normalizedPathForCompare(right)
}

function isMigrationTargetEmpty(targetRoot) {
  if (!fs.existsSync(targetRoot)) return true
  const ignorable = new Set(['logs', '.legacy-metis-migrated'])
  return fs.readdirSync(targetRoot).filter(name => !ignorable.has(name)).length === 0
}

function maybeMigrateLegacyMetisData(devBackend, emit = () => {}) {
  try {
    if (process.env.METIS_SKIP_LEGACY_MIGRATION === '1') return
    // 默认不自动搬运旧 ~/.metis 数据——保证全新安装是干净的、不带任何历史/旧名遗留。
    // 仅在显式 METIS_MIGRATE_LEGACY_HOME=1 时才迁移。
    if (process.env.METIS_MIGRATE_LEGACY_HOME !== '1') return

    const targetRoot = metisHome()
    const legacyRoot = legacyMetisHome()
    if (isSamePath(targetRoot, legacyRoot)) return
    if (!fs.existsSync(legacyRoot)) return
    if (!isMigrationTargetEmpty(targetRoot)) return

    const names = [
      'session-state.db',
      'session-state.db-shm',
      'session-state.db-wal',
      'sessions',
      'workspaces',
      'config.json',
      'METIS.md',
      'skills',
      'plugins',
      'mcp.json',
      'tools.json',
      'cron.json'
    ]
    const copied = []
    fs.mkdirSync(targetRoot, { recursive: true })
    for (const name of names) {
      const source = path.join(legacyRoot, name)
      const destination = path.join(targetRoot, name)
      if (!fs.existsSync(source) || fs.existsSync(destination)) continue
      fs.cpSync(source, destination, { recursive: true, force: false, errorOnExist: false })
      copied.push(name)
    }
    if (copied.length === 0) return

    const marker = {
      migratedAt: new Date().toISOString(),
      source: legacyRoot,
      copied
    }
    fs.writeFileSync(path.join(targetRoot, '.legacy-metis-migrated'), JSON.stringify(marker, null, 2), 'utf8')
    appendLogLine(`[storage] migrated legacy ~/.metis data: ${copied.join(', ')}`)
    emitBoot(emit, {
      phase: 'log',
      line: `已迁移旧数据目录 ${legacyRoot} -> ${targetRoot}`
    })
  } catch (error) {
    appendLogLine(`[storage] legacy migration skipped: ${error?.message || error}`)
  }
}

async function startBackend(emit = () => {}) {
  if (process.env.METIS_FAKE_BACKEND === '1') {
    return startFakeBackend(emit)
  }

  if (child && !child.killed && child.__metisReady) {
    emitBoot(emit, { phase: 'ready', title: '后端已就绪', port: child.__metisPort })
    return child.__metisPort
  }

  if (child && !child.killed) {
    stopBackend()
  }

  ensureLogDir()
  appendLogLine('=== Metis backend startup ===')

  const port = await findFreePort()
  const devBackend = isDevBackend()
  maybeMigrateLegacyMetisData(devBackend, emit)
  let exe = ''
  let args = []
  let cwd = ''

  if (devBackend) {
    const root = backendSourceRoot()
    cwd = backendCwd(root)

    emitBoot(emit, {
      phase: 'detecting',
      line: `后端包路径: ${root}`
    })
    emitBoot(emit, {
      phase: 'detecting',
      line: `后端启动 cwd: ${cwd}`
    })

    if (!fs.existsSync(root)) {
      const detail = `找不到 backend 后端包目录:\n${root}\n\npython -m backend 必须从包含 backend/ 的仓库根目录启动。`
      const error = makeBootError('找不到后端包目录', detail)
      emitBoot(emit, { phase: 'error', title: error.title, detail: error.detail, logTail: error.logTail })
      throw error
    }

    const py = await detectPython(root, emit)
    exe = py.exe
    args = [...(py.prefixArgs || []), '-m', 'backend', '--mode', 'web', '--port', String(port)]
  } else {
    exe = packagedBackendExe()
    cwd = path.dirname(exe)
    emitBoot(emit, {
      phase: 'detecting',
      line: `打包后端路径: ${exe}`
    })

    if (!fs.existsSync(exe)) {
      const detail = [
        `找不到打包后的后端可执行文件:`,
        exe,
        '',
        '请先运行 npm run build-backend，然后重新打包安装。'
      ].join('\n')
      const error = makeBootError('打包后端不存在', detail)
      emitBoot(emit, { phase: 'error', title: error.title, detail: error.detail, logTail: error.logTail })
      throw error
    }
  }

  const commandLine = [exe, ...args].join(' ')
  emitBoot(emit, {
    phase: 'starting',
    line: `启动后端: ${commandLine} (cwd=${cwd})`
  })

  let ready = false
  manualStopRequested = false

  // Decrypt API key from safeStorage if available
  const storage = resolveDataRootInfo()
  const runtimePackDir = bundledRuntimePackDir()
  const backendEnv = {
    ...process.env,
    METIS_DATA_ROOT: storage.dataRoot,
    METIS_HOME: storage.metisHome,
    METIS_BUNDLED_RUNTIME_PACK_DIR: runtimePackDir,
    METIS_RUNTIME_PACK_BUNDLED_DIR: runtimePackDir,
    METIS_HTTP_PORT: String(port),
    METIS_PORT: String(port),
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1'
  }
  const decryptedKey = decryptApiKeyFromConfig()
  if (decryptedKey && !backendEnv.METIS_LLM_API_KEY) {
    backendEnv.METIS_LLM_API_KEY = decryptedKey
  }

  child = spawn(exe, args, {
    cwd,
    env: backendEnv,
    stdio: ['ignore', 'pipe', 'pipe']
  })
  child.__metisPort = port
  child.__metisReady = false
  const spawned = child

  spawned.stdout.on('data', data => emitBackendData('flask', data, emit))
  spawned.stderr.on('data', data => emitBackendData('flask:err', data, emit))

  const earlyFailure = new Promise((resolve, reject) => {
    spawned.once('error', error => {
      const bootError = makeBootError('后端进程启动失败', `${error.message}\n\n${tailBackendLog()}`)
      emitBoot(emit, { phase: 'error', title: bootError.title, detail: bootError.detail, logTail: bootError.logTail })
      reject(bootError)
    })

    spawned.once('exit', (code, signal) => {
      appendLogLine(`[flask] exited code=${code ?? 'null'} signal=${signal ?? 'null'}`)
      const wasReady = ready || spawned.__metisReady
      const stoppedIntentionally = manualStopRequested
      manualStopRequested = false
      if (child === spawned) {
        child = null
      }
      if (stoppedIntentionally) {
        emitBoot(emit, {
          phase: 'stopped',
          title: '后端进程已停止',
          detail: `code=${code ?? 'null'} signal=${signal ?? 'null'}`,
          logTail: tailBackendLog()
        })
        resolve()
        return
      }
      if (!wasReady) {
        const title = `后端进程提前退出 (code ${code ?? 'null'})`
        const detail = [`signal=${signal ?? 'null'}`, '', tailBackendLog()].join('\n')
        const bootError = makeBootError(title, detail)
        emitBoot(emit, { phase: 'error', title: bootError.title, detail: bootError.detail, logTail: bootError.logTail })
        reject(bootError)
        return
      }

      emitBoot(emit, {
        phase: 'exit',
        title: '后端进程已退出',
        detail: `code=${code ?? 'null'} signal=${signal ?? 'null'}`,
        logTail: tailBackendLog()
      })
      resolve()
    })
  })

  try {
    await Promise.race([waitForReady(port), earlyFailure])
  } catch (error) {
    if (error && error.title) {
      throw error
    }

    const title = '后端启动超时'
    const detail = [
      error?.message ? `最后一次探活错误: ${error.message}` : '30 秒内未收到 /health 或 /sessions 响应。',
      '',
      tailBackendLog()
    ].join('\n')
    const bootError = makeBootError(title, detail)
    emitBoot(emit, { phase: 'error', title: bootError.title, detail: bootError.detail, logTail: bootError.logTail })
    throw bootError
  }

  ready = true
  if (child) {
    child.__metisReady = true
  }

  emitBoot(emit, {
    phase: 'ready',
    title: 'Metis 后端已就绪',
    port
  })
  maybeEnsureRuntimePack(port)
  return port
}

function stopBackend() {
  const current = child
  child = null
  if (!current || current.killed) {
    return
  }

  manualStopRequested = true
  try {
    current.kill('SIGTERM')
  } catch {}

  setTimeout(() => {
    if (!current.killed) {
      try {
        current.kill('SIGKILL')
      } catch {}
    }
  }, 2000)
}

module.exports = {
  detectPython,
  getBackendLogPath,
  startBackend,
  stopBackend,
  tailBackendLog
}
