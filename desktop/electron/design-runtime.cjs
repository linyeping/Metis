const fs = require('node:fs')
const path = require('node:path')
const { URL } = require('node:url')

const DESIGN_RUNTIME_VERSION = '0.15.1'
const DESIGN_RUNTIME_REPOSITORY = 'https://github.com/nexu-io/open-design'
const DESIGN_DAEMON_PORT = 17456
const DESIGN_WEB_PORT = 17573
const DESIGN_RUNTIME_RESOURCE_NAME = 'open-design-runtime'

function parseLoopbackOrigin(value) {
  try {
    const parsed = new URL(String(value || ''))
    const loopback = parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost' || parsed.hostname === '[::1]'
    if (!loopback || (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')) return null
    if (parsed.username || parsed.password) return null
    return parsed.origin
  } catch {
    return null
  }
}

function isAllowedDesignNavigation(value, runtimeUrl) {
  const origin = parseLoopbackOrigin(runtimeUrl)
  if (!origin) return false
  try {
    const parsed = new URL(String(value || ''))
    return parsed.origin === origin
  } catch {
    return false
  }
}

function buildDesignProjectUrl(runtimeUrl, projectId) {
  const origin = parseLoopbackOrigin(runtimeUrl)
  const id = String(projectId || '').trim()
  if (!origin || !id) return ''
  return `${origin}/projects/${encodeURIComponent(id)}`
}

function buildDesignPageUrl(runtimeUrl, pagePath = '/') {
  const origin = parseLoopbackOrigin(runtimeUrl)
  if (!origin) return ''
  const raw = String(pagePath || '/').trim()
  if (!raw.startsWith('/') || raw.startsWith('//')) return ''
  try {
    const parsed = new URL(raw, origin)
    return parsed.origin === origin ? parsed.toString() : ''
  } catch {
    return ''
  }
}

function buildPnpmSpawnCommand(args, platform = process.platform, comspec = process.env.ComSpec) {
  const pnpmArgs = Array.isArray(args) ? args.map(value => String(value)) : []
  if (platform === 'win32') {
    return {
      executable: comspec || 'cmd.exe',
      args: ['/d', '/s', '/c', 'pnpm.cmd', ...pnpmArgs]
    }
  }
  return { executable: 'pnpm', args: pnpmArgs }
}

function buildManagedDesignConfig(current = {}, now = Date.now()) {
  const existingDecision = Number(current?.privacyDecisionAt)
  return {
    onboardingCompleted: true,
    agentId: 'metis',
    telemetry: {
      metrics: false,
      content: false,
      artifactManifest: false
    },
    privacyDecisionAt: Number.isFinite(existingDecision) && existingDecision >= 0
      ? existingDecision
      : now
  }
}

function buildMetisAgentProfile(options = {}) {
  const executable = path.resolve(String(options.executable || process.execPath))
  const bridgeScript = path.resolve(String(options.bridgeScript || path.join(__dirname, 'design-agent-bridge.cjs')))
  const backendUrl = parseLoopbackOrigin(options.backendUrl)
  const designRoot = path.resolve(String(options.designRoot || ''))
  const stateFile = path.resolve(String(options.stateFile || path.join(designRoot, '..', 'bridge-sessions.json')))
  const token = String(options.token || '').trim()
  if (!backendUrl || !designRoot || !token) {
    throw new Error('Metis Design agent profile requires a loopback backend, design root, and token.')
  }
  return {
    agents: [
      {
        id: 'metis',
        name: 'Metis',
        baseAgent: 'claude',
        // Open Design resolves custom agent binaries through PATH even when a
        // profile supplies an absolute path. The parent runtime prepends this
        // executable's directory to its private PATH before launching OD.
        bin: path.basename(executable, path.extname(executable)),
        args: [bridgeScript],
        versionArgs: [bridgeScript, '--version'],
        helpArgs: [bridgeScript, '--help'],
        env: {
          ELECTRON_RUN_AS_NODE: '1',
          METIS_BACKEND_URL: backendUrl,
          METIS_DESIGN_BRIDGE_TOKEN: token,
          METIS_DESIGN_ROOT: designRoot,
          METIS_DESIGN_BRIDGE_STATE_FILE: stateFile
        }
      }
    ]
  }
}

function resolveDesignSourceRoot(options = {}) {
  const candidates = [
    options.explicitRoot,
    process.env.METIS_DESIGN_SOURCE_ROOT,
    options.appPath ? path.resolve(options.appPath, '..', 'open-design-main') : '',
    options.mainDir ? path.resolve(options.mainDir, '..', '..', '..', 'open-design-main') : ''
  ]
  for (const candidate of candidates) {
    if (!candidate) continue
    const resolved = path.resolve(String(candidate))
    if (fs.existsSync(path.join(resolved, 'package.json')) && fs.existsSync(path.join(resolved, 'pnpm-lock.yaml'))) {
      return resolved
    }
  }
  return ''
}

function bundledDesignRuntimeLayout(root) {
  const resolved = path.resolve(String(root || ''))
  return {
    root: resolved,
    executable: path.join(resolved, 'Open Design.exe'),
    daemonEntry: path.join(resolved, 'resources', 'app', 'prebundled', 'daemon', 'daemon-sidecar.mjs'),
    daemonCliEntry: path.join(resolved, 'resources', 'app', 'prebundled', 'daemon', 'daemon-cli.mjs'),
    webEntry: path.join(resolved, 'resources', 'app', 'prebundled', 'web-sidecar.mjs'),
    resourceRoot: path.join(resolved, 'resources', 'open-design'),
    webStandaloneRoot: path.join(resolved, 'resources', 'open-design-web-standalone'),
    licensePath: path.join(resolved, 'OPEN-DESIGN-LICENSE.txt')
  }
}

function resolveBundledDesignRuntime(options = {}) {
  const candidates = [
    options.explicitRoot,
    process.env.METIS_DESIGN_RUNTIME_ROOT,
    options.resourcesPath ? path.join(options.resourcesPath, DESIGN_RUNTIME_RESOURCE_NAME) : '',
    options.mainDir ? path.resolve(options.mainDir, '..', 'resources', DESIGN_RUNTIME_RESOURCE_NAME) : ''
  ]
  for (const candidate of candidates) {
    if (!candidate) continue
    const layout = bundledDesignRuntimeLayout(candidate)
    const required = [
      layout.executable,
      layout.daemonEntry,
      layout.daemonCliEntry,
      layout.webEntry,
      layout.resourceRoot,
      layout.webStandaloneRoot,
      layout.licensePath
    ]
    if (required.every(entry => fs.existsSync(entry))) return layout
  }
  return null
}

function buildDesignSidecarStampArgs(options = {}) {
  const app = options.app === 'web' ? 'web' : 'daemon'
  const namespace = String(options.namespace || 'metis').replace(/[^A-Za-z0-9._-]+/g, '-') || 'metis'
  const ipc = String(options.ipc || '')
  return [
    '--od-stamp-app', app,
    '--od-stamp-mode', 'runtime',
    '--od-stamp-namespace', namespace,
    '--od-stamp-ipc', ipc,
    '--od-stamp-source', 'packaged'
  ]
}

function readDesignSourceVersion(sourceRoot) {
  if (!sourceRoot) return ''
  try {
    const payload = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'package.json'), 'utf8'))
    return typeof payload.version === 'string' ? payload.version : ''
  } catch {
    return ''
  }
}

function normalizeDesignProject(project) {
  const metadata = project?.metadata && typeof project.metadata === 'object' ? project.metadata : {}
  return {
    id: String(project?.id || ''),
    name: String(project?.name || 'Untitled design'),
    kind: String(metadata.kind || 'prototype'),
    fidelity: metadata.fidelity === 'wireframe' ? 'wireframe' : 'high-fidelity',
    updatedAt: Number(project?.updatedAt || project?.createdAt || Date.now()),
    createdAt: Number(project?.createdAt || project?.updatedAt || Date.now()),
    status: String(project?.status?.value || project?.status || 'not_started')
  }
}

function normalizeDesignSystem(system) {
  return {
    id: String(system?.id || ''),
    title: String(system?.title || system?.name || system?.id || 'Design system'),
    description: String(system?.description || ''),
    source: String(system?.source || '')
  }
}

module.exports = {
  DESIGN_DAEMON_PORT,
  DESIGN_RUNTIME_RESOURCE_NAME,
  DESIGN_RUNTIME_REPOSITORY,
  DESIGN_RUNTIME_VERSION,
  DESIGN_WEB_PORT,
  buildDesignProjectUrl,
  buildDesignPageUrl,
  buildDesignSidecarStampArgs,
  buildManagedDesignConfig,
  buildMetisAgentProfile,
  buildPnpmSpawnCommand,
  bundledDesignRuntimeLayout,
  isAllowedDesignNavigation,
  normalizeDesignProject,
  normalizeDesignSystem,
  parseLoopbackOrigin,
  readDesignSourceVersion,
  resolveBundledDesignRuntime,
  resolveDesignSourceRoot
}
