const test = require('node:test')
const assert = require('node:assert/strict')

const {
  buildDesignProjectUrl,
  buildDesignPageUrl,
  buildDesignSidecarStampArgs,
  buildManagedDesignConfig,
  buildMetisAgentProfile,
  buildPnpmSpawnCommand,
  isAllowedDesignNavigation,
  normalizeDesignProject,
  normalizeDesignSystem,
  parseLoopbackOrigin
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

test('managed Design runtime skips upstream onboarding and disables upstream telemetry', () => {
  assert.deepEqual(buildManagedDesignConfig({}, 1234), {
    onboardingCompleted: true,
    agentId: 'metis',
    telemetry: { metrics: false, content: false, artifactManifest: false },
    privacyDecisionAt: 1234
  })
  assert.equal(buildManagedDesignConfig({ privacyDecisionAt: 99 }, 1234).privacyDecisionAt, 99)
})

test('Metis Design profile pins the bridge executable and scoped runtime inputs', () => {
  const profile = buildMetisAgentProfile({
    executable: 'C:\\Metis\\electron.exe',
    bridgeScript: 'C:\\Metis\\design-agent-bridge.cjs',
    backendUrl: 'http://127.0.0.1:12811/path',
    designRoot: 'C:\\Metis Data\\design\\projects',
    stateFile: 'C:\\Metis Data\\design\\bridge-sessions.json',
    token: 'secret-token'
  })
  assert.equal(profile.agents[0].id, 'metis')
  assert.equal(profile.agents[0].baseAgent, 'claude')
  assert.equal(profile.agents[0].bin, 'electron')
  assert.equal(profile.agents[0].env.METIS_BACKEND_URL, 'http://127.0.0.1:12811')
  assert.equal(profile.agents[0].env.METIS_DESIGN_BRIDGE_TOKEN, 'secret-token')
  assert.deepEqual(profile.agents[0].versionArgs.slice(-1), ['--version'])
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
