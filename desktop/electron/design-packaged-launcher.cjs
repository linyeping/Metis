// Compatibility launcher for Open Design's prebundled Windows sidecars.
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const runtimeRoot = path.resolve(String(process.env.METIS_DESIGN_RUNTIME_ROOT || ''))
const dataRoot = path.resolve(String(process.env.OD_DATA_DIR || ''))
const daemonPort = String(process.env.OD_PORT || '')
const webPort = String(process.env.OD_WEB_PORT || '')

const layout = {
  executable: path.join(runtimeRoot, 'Open Design.exe'),
  appRoot: path.join(runtimeRoot, 'resources', 'app'),
  daemonEntry: path.join(runtimeRoot, 'resources', 'app', 'prebundled', 'daemon', 'daemon-sidecar.mjs'),
  daemonCliEntry: path.join(runtimeRoot, 'resources', 'app', 'prebundled', 'daemon', 'daemon-cli.mjs'),
  webEntry: path.join(runtimeRoot, 'resources', 'app', 'prebundled', 'web-sidecar.mjs'),
  resourceRoot: path.join(runtimeRoot, 'resources', 'open-design'),
  webStandaloneRoot: path.join(runtimeRoot, 'resources', 'open-design-web-standalone')
}

const required = [
  layout.executable,
  layout.daemonEntry,
  layout.daemonCliEntry,
  layout.webEntry,
  layout.resourceRoot,
  layout.webStandaloneRoot
]
if (!runtimeRoot || !dataRoot || !daemonPort || !webPort || required.some(entry => !fs.existsSync(entry))) {
  process.stderr.write('[metis-design] bundled Open Design runtime is incomplete\n')
  process.exit(2)
}

fs.mkdirSync(dataRoot, { recursive: true })
const runtimeStateRoot = path.join(dataRoot, 'sidecars')
fs.mkdirSync(runtimeStateRoot, { recursive: true })
const namespace = `metis-${process.pid}`
const pipe = app => `\\\\.\\pipe\\open-design-${namespace}-${app}`
const stampArgs = app => [
  '--od-stamp-app', app,
  '--od-stamp-mode', 'runtime',
  '--od-stamp-namespace', namespace,
  '--od-stamp-ipc', pipe(app),
  '--od-stamp-source', 'packaged'
]

const baseEnv = {
  ...process.env,
  ELECTRON_RUN_AS_NODE: '1',
  NODE_ENV: 'production',
  NO_COLOR: '1',
  OD_DATA_DIR: dataRoot,
  OD_RESOURCE_ROOT: layout.resourceRoot,
  OD_INSTALLATION_DIR: runtimeRoot,
  OD_DAEMON_CLI_PATH: layout.daemonCliEntry,
  OD_PORT: daemonPort,
  OD_WEB_PORT: webPort,
  OD_SIDECAR_BASE: runtimeStateRoot,
  OD_SIDECAR_NAMESPACE: namespace,
  OD_SIDECAR_SOURCE: 'packaged'
}

const children = []
let stopping = false

function start(label, entry, env) {
  const child = spawn(layout.executable, [entry, ...stampArgs(label)], {
    cwd: layout.appRoot,
    env: { ...baseEnv, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  })
  children.push(child)
  child.stdout?.on('data', chunk => process.stdout.write(`[${label}] ${chunk}`))
  child.stderr?.on('data', chunk => process.stderr.write(`[${label}] ${chunk}`))
  child.on('error', error => {
    process.stderr.write(`[${label}] ${error.message}\n`)
    stop(1)
  })
  child.on('exit', (code, signal) => {
    if (stopping) return
    process.stderr.write(`[${label}] exited (${code ?? signal ?? 'unknown'})\n`)
    stop(code || 1)
  })
  return child
}

function stop(exitCode = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) {
    try { child.kill() } catch {}
  }
  setTimeout(() => process.exit(exitCode), 250).unref()
}

start('daemon', layout.daemonEntry, {})
start('web', layout.webEntry, {
  OD_WEB_OUTPUT_MODE: 'standalone',
  OD_WEB_STANDALONE_ROOT: layout.webStandaloneRoot,
  PORT: webPort
})

process.on('SIGINT', () => stop(0))
process.on('SIGTERM', () => stop(0))
process.on('disconnect', () => stop(0))
