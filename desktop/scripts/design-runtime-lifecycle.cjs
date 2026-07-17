const { spawn } = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')

const desktopRoot = path.resolve(__dirname, '..')
const repoRoot = path.resolve(desktopRoot, '..')
const runtimeRoot = path.resolve(process.env.METIS_DESIGN_RUNTIME_ROOT || path.join(desktopRoot, 'resources', 'open-design-runtime'))
const electronBinary = path.resolve(process.env.METIS_DESIGN_ELECTRON || path.join(desktopRoot, 'node_modules', 'electron', 'dist', 'electron.exe'))
const holdMs = Math.max(15_000, Number(process.env.METIS_DESIGN_LIFECYCLE_MS) || 30_000)
const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-design-lifecycle-'))

async function availablePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(error => error ? reject(error) : resolve(port))
    })
  })
}

async function waitForHealthy(child, urls, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Design launcher exited before readiness (${child.exitCode})`)
    try {
      const responses = await Promise.all(urls.map(url => fetch(url, { signal: AbortSignal.timeout(2_000) })))
      if (responses.every(response => response.ok)) return
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  throw new Error('Design runtime did not become healthy before the lifecycle deadline')
}

async function main() {
  const configuredDaemonPort = Number(process.env.METIS_DESIGN_DAEMON_PORT)
  const configuredWebPort = Number(process.env.METIS_DESIGN_WEB_PORT)
  const [daemonPort, webPort] = await Promise.all([
    Number.isInteger(configuredDaemonPort) && configuredDaemonPort > 0 ? configuredDaemonPort : availablePort(),
    Number.isInteger(configuredWebPort) && configuredWebPort > 0 ? configuredWebPort : availablePort()
  ])
  const launcher = path.join(desktopRoot, 'electron', 'design-runtime-launcher.cjs')
  const child = spawn(electronBinary, [launcher], {
    cwd: repoRoot,
    env: {
      ...process.env,
      ELECTRON_RUN_AS_NODE: '1',
      METIS_DESIGN_RUNTIME_ROOT: runtimeRoot,
      METIS_MANAGED_DESIGN_RUNTIME: '1',
      OD_DATA_DIR: dataRoot,
      OD_PORT: String(daemonPort),
      OD_WEB_PORT: String(webPort),
      OD_SIDECAR_NAMESPACE: `metis-lifecycle-${process.pid}`,
      NO_COLOR: '1'
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  })
  child.stdout.on('data', chunk => process.stdout.write(chunk))
  child.stderr.on('data', chunk => process.stderr.write(chunk))
  const daemonHealth = `http://127.0.0.1:${daemonPort}/api/health`
  const webHealth = `http://127.0.0.1:${webPort}/api/health`

  try {
    await waitForHealthy(child, [daemonHealth, webHealth])
    process.stdout.write(`[lifecycle] ready web=http://127.0.0.1:${webPort}\n`)
    const deadline = Date.now() + holdMs
    while (Date.now() < deadline) {
      await waitForHealthy(child, [daemonHealth, webHealth], 5_000)
      await new Promise(resolve => setTimeout(resolve, 500))
    }
    process.stdout.write(`[lifecycle] healthy for ${holdMs}ms\n`)
  } finally {
    try { child.kill() } catch {}
    await new Promise(resolve => {
      if (child.exitCode !== null) return resolve()
      child.once('exit', resolve)
      setTimeout(resolve, 5_000)
    })
    fs.rmSync(dataRoot, { recursive: true, force: true })
  }
}

main().catch(error => {
  process.stderr.write(`[lifecycle] ${error?.stack || error}\n`)
  fs.rmSync(dataRoot, { recursive: true, force: true })
  process.exitCode = 1
})
