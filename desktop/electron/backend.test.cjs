const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const { clearDataRootCache } = require('./data-root.cjs')
const {
  backendDependencyFingerprint,
  ensureManagedPythonDependenciesSynced,
  readManagedPythonStamp,
  writeManagedPythonStamp
} = require('./backend.cjs')

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'metis-backend-test-'))
}

function withMetisHome(callback) {
  const home = tempDir()
  const previous = process.env.METIS_HOME
  process.env.METIS_HOME = home
  clearDataRootCache()
  try {
    return callback(home)
  } finally {
    if (previous === undefined) delete process.env.METIS_HOME
    else process.env.METIS_HOME = previous
    clearDataRootCache()
  }
}

function writeBackendRoot(dir, pyprojectContent) {
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, 'pyproject.toml'), pyprojectContent, 'utf8')
  return dir
}

test('backendDependencyFingerprint changes when pyproject.toml dependencies change', () => {
  const root = tempDir()
  writeBackendRoot(root, 'dependencies = ["flask>=3.0"]\n')
  const before = backendDependencyFingerprint(root)

  writeBackendRoot(root, 'dependencies = ["flask>=3.0", "ddgs>=9.0"]\n')
  const after = backendDependencyFingerprint(root)

  assert.notEqual(before, after)
  assert.equal(before.length, 64) // sha256 hex
})

test('backendDependencyFingerprint is stable for unchanged content and empty for a missing file', () => {
  const root = tempDir()
  writeBackendRoot(root, 'dependencies = ["flask>=3.0"]\n')

  assert.equal(backendDependencyFingerprint(root), backendDependencyFingerprint(root))
  assert.equal(backendDependencyFingerprint(path.join(root, 'does-not-exist')), '')
})

test('writeManagedPythonStamp merges into the existing stamp instead of overwriting it', () => {
  withMetisHome(() => {
    writeManagedPythonStamp({ managedPython: 'C:/fake/python.exe', bootstrapSource: 'test' })
    writeManagedPythonStamp({ dependencyFingerprint: 'abc123' })

    const stamp = readManagedPythonStamp()
    assert.equal(stamp.managedPython, 'C:/fake/python.exe')
    assert.equal(stamp.bootstrapSource, 'test')
    assert.equal(stamp.dependencyFingerprint, 'abc123')
  })
})

test('ensureManagedPythonDependenciesSynced skips pip entirely when the fingerprint already matches', async () => {
  await withMetisHome(async () => {
    const backendRoot = writeBackendRoot(tempDir(), 'dependencies = ["flask>=3.0"]\n')
    const fingerprint = backendDependencyFingerprint(backendRoot)
    writeManagedPythonStamp({ dependencyFingerprint: fingerprint })

    // An exe that does not exist would make runProbe fail immediately (ENOENT).
    // If this still reports ok:true, it proves no subprocess was attempted -
    // the fast path returned before ever touching `candidate.exe`.
    const result = await ensureManagedPythonDependenciesSynced(
      { exe: path.join(backendRoot, 'this-python-does-not-exist.exe') },
      backendRoot,
    )

    assert.deepEqual(result, { ok: true })
  })
})

test('ensureManagedPythonDependenciesSynced attempts a resync when the fingerprint is stale or missing', async () => {
  await withMetisHome(async () => {
    const backendRoot = writeBackendRoot(tempDir(), 'dependencies = ["flask>=3.0"]\n')
    // No stamp written at all - simulates a venv bootstrapped before this
    // feature existed, same as the real-world ddgs bug this fixes.
    const result = await ensureManagedPythonDependenciesSynced(
      { exe: path.join(backendRoot, 'this-python-does-not-exist.exe') },
      backendRoot,
    )

    assert.equal(result.ok, false)
    assert.match(result.reason, /同步托管环境依赖失败/)
  })
})
