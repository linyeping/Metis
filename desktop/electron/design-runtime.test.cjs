const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const {
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
  resolveDesignSourceRoot
} = require('./design-runtime.cjs')

test('pnpm commands use cmd.exe on Windows so Electron can launch command shims', () => {
  assert.deepEqual(buildPnpmSpawnCommand(['tools-dev', 'run'], 'win32', 'C:\\Windows\\System32\\cmd.exe'), {
    executable: 'C:\\Windows\\System32\\cmd.exe',
    args: ['/d', '/s', '/c', 'pnpm.cmd', 'tools-dev', 'run']
  })
  assert.deepEqual(buildPnpmSpawnCommand(['tools-dev', 'run'], 'linux'), {
    executable: 'pnpm',
    args: ['tools-dev', 'run']
  })
})

test('Design namespace cleanup is scoped to the managed Metis runtime', () => {
  assert.deepEqual(buildDesignNamespaceCommand('stop', 'metis design', 'linux'), {
    executable: 'pnpm',
    args: ['tools-dev', 'stop', '--namespace', 'metis-design']
  })
})

test('managed Design runtime skips upstream onboarding and disables upstream telemetry', () => {
  assert.deepEqual(buildManagedDesignConfig({}, 1234), {
    onboardingCompleted: true,
    agentId: 'metis',
    telemetry: { metrics: false, content: false, artifactManifest: false },
    privacyDecisionAt: 1234
  })
  assert.equal(buildManagedDesignConfig({ privacyDecisionAt: 99 }, 1234).privacyDecisionAt, 99)
})

test('Design resolves repository-local source and packaged modules have no second executable', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-design-source-'))
  fs.writeFileSync(path.join(root, 'package.json'), '{"version":"0.15.1"}')
  fs.writeFileSync(path.join(root, 'pnpm-lock.yaml'), 'lockfileVersion: 9')
  assert.equal(resolveDesignSourceRoot({ explicitRoot: root }), root)
  const layout = bundledDesignRuntimeLayout('C:\\Metis\\resources\\open-design-runtime')
  assert.equal(Object.hasOwn(layout, 'executable'), false)
  assert.match(layout.daemonEntry, /app[\\/]prebundled[\\/]daemon[\\/]daemon-sidecar\.mjs$/)
  assert.match(layout.nodeModulesRoot, /app[\\/]node_modules$/)
  assert.match(layout.rendererRoot, /app[\\/]prebundled[\\/]desktop-renderer$/)
  fs.rmSync(root, { recursive: true, force: true })
})

test('Metis and embedded Design lock the same Electron native-module ABI', () => {
  const metisPackage = require('../package.json')
  const designPackage = require('../../open-design/apps/desktop/package.json')
  assert.equal(metisPackage.devDependencies.electron, designPackage.devDependencies.electron)
})

test('design runtime accepts loopback origins only', () => {
  assert.equal(parseLoopbackOrigin('http://127.0.0.1:17573/path'), 'http://127.0.0.1:17573')
  assert.equal(parseLoopbackOrigin('http://localhost:17573'), 'http://localhost:17573')
  assert.equal(parseLoopbackOrigin('https://example.com'), null)
  assert.equal(parseLoopbackOrigin('file:///tmp/design'), null)
})

test('design navigation stays on the runtime origin', () => {
  const runtime = 'http://127.0.0.1:17573'
  assert.equal(isAllowedDesignNavigation(`${runtime}/projects/p1`, runtime), true)
  assert.equal(isAllowedDesignNavigation('http://127.0.0.1:7456/api/health', runtime), false)
  assert.equal(isAllowedDesignNavigation('https://example.com', runtime), false)
})

test('project urls encode ids and project summaries keep Open Design metadata', () => {
  assert.equal(buildDesignProjectUrl('http://127.0.0.1:17573', 'a/b'), 'http://127.0.0.1:17573/projects/a%2Fb')
  assert.deepEqual(normalizeDesignProject({
    id: 'p1',
    name: 'Dashboard',
    createdAt: 10,
    updatedAt: 20,
    metadata: { kind: 'prototype', fidelity: 'wireframe' },
    status: { value: 'running' }
  }), {
    id: 'p1',
    name: 'Dashboard',
    kind: 'prototype',
    fidelity: 'wireframe',
    createdAt: 10,
    updatedAt: 20,
    status: 'running'
  })
})

test('managed Design pages stay on the loopback runtime and sidecar stamps stay scoped', () => {
  assert.equal(buildDesignPageUrl('http://127.0.0.1:17573', '/design-systems'), 'http://127.0.0.1:17573/design-systems')
  assert.equal(buildDesignPageUrl('http://127.0.0.1:17573', '//example.com'), '')
  assert.deepEqual(buildDesignSidecarStampArgs({ app: 'web', namespace: 'metis design', ipc: '\\\\.\\pipe\\od-web' }), [
    '--od-stamp-app', 'web',
    '--od-stamp-mode', 'runtime',
    '--od-stamp-namespace', 'metis-design',
    '--od-stamp-ipc', '\\\\.\\pipe\\od-web',
    '--od-stamp-source', 'packaged'
  ])
})

test('design systems are normalized for the Metis project picker', () => {
  assert.deepEqual(normalizeDesignSystem({ id: 'default', title: 'Default', description: 'Base', source: 'bundled' }), {
    id: 'default',
    title: 'Default',
    description: 'Base',
    source: 'bundled'
  })
})
