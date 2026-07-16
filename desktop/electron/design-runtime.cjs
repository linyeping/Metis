const fs = require('node:fs')
const path = require('node:path')
const { URL } = require('node:url')

const DESIGN_RUNTIME_VERSION = '0.15.1'
const DESIGN_RUNTIME_REPOSITORY = 'https://github.com/linyeping/Metis'
const DESIGN_DAEMON_PORT = 17456
const DESIGN_WEB_PORT = 17573
const DESIGN_RUNTIME_RESOURCE_NAME = 'open-design-runtime'
const DESIGN_RUNTIME_NAMESPACE = 'metis'

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

function buildDesignNamespaceCommand(action, namespace = DESIGN_RUNTIME_NAMESPACE, platform = process.platform, comspec = process.env.ComSpec) {
  const verb = action === 'stop' ? 'stop' : 'status'
  const scopedNamespace = String(namespace || DESIGN_RUNTIME_NAMESPACE).replace(/[^A-Za-z0-9._-]+/g, '-') || DESIGN_RUNTIME_NAMESPACE
  return buildPnpmSpawnCommand(['tools-dev', verb, '--namespace', scopedNamespace], platform, comspec)
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

function resolveDesignSourceRoot(options = {}) {
  const candidates = [
    options.explicitRoot,
    process.env.METIS_DESIGN_SOURCE_ROOT,
    options.appPath ? path.resolve(options.appPath, '..', 'open-design') : '',
    options.mainDir ? path.resolve(options.mainDir, '..', '..', 'open-design') : ''
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
    daemonEntry: path.join(resolved, 'app', 'prebundled', 'daemon', 'daemon-sidecar.mjs'),
    daemonCliEntry: path.join(resolved, 'app', 'prebundled', 'daemon', 'daemon-cli.mjs'),
    rendererRoot: path.join(resolved, 'app', 'prebundled', 'desktop-renderer'),
    webEntry: path.join(resolved, 'app', 'prebundled', 'web-sidecar.mjs'),
    resourceRoot: path.join(resolved, 'open-design'),
    webStandaloneRoot: path.join(resolved, 'web-standalone'),
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
      layout.daemonEntry,
      layout.daemonCliEntry,
      path.join(layout.rendererRoot, 'artifact-export.js'),
      path.join(layout.rendererRoot, 'deck-capture.js'),
      path.join(layout.rendererRoot, 'pdf-export.js'),
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
  DESIGN_RUNTIME_NAMESPACE,
  DESIGN_RUNTIME_VERSION,
  DESIGN_WEB_PORT,
  buildDesignProjectUrl,
  buildDesignPageUrl,
  buildDesignNamespaceCommand,
  buildDesignSidecarStampArgs,
  buildManagedDesignConfig,
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
