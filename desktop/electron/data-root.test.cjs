const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const {
  clearDataRootCache,
  configFilePath,
  resolveDataRootInfo
} = require('./data-root.cjs')

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'metis-data-root-'))
}

function withEnv(values, callback) {
  const previous = new Map()
  for (const key of Object.keys(values)) {
    previous.set(key, process.env[key])
    if (values[key] === null || values[key] === undefined) {
      delete process.env[key]
    } else {
      process.env[key] = values[key]
    }
  }
  clearDataRootCache()
  try {
    return callback()
  } finally {
    for (const [key, value] of previous.entries()) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
    clearDataRootCache()
  }
}

test('METIS_DATA_ROOT controls Electron and backend data roots', () => {
  const root = path.join(tempDir(), 'custom-data')
  withEnv({ METIS_DATA_ROOT: root, METIS_HOME: null }, () => {
    const info = resolveDataRootInfo()

    assert.equal(info.source, 'env')
    assert.equal(info.dataRoot, path.resolve(root))
    assert.equal(info.metisHome, path.join(path.resolve(root), 'metis'))
    assert.equal(info.electronUserData, path.join(path.resolve(root), 'electron'))
    assert.equal(configFilePath(), path.join(path.resolve(root), 'metis', 'config.json'))
  })
})

test('METIS_HOME overrides only the backend home under a shared data root', () => {
  const dataRoot = path.join(tempDir(), 'data-root')
  const metisHome = path.join(tempDir(), 'backend-home')
  withEnv({ METIS_DATA_ROOT: dataRoot, METIS_HOME: metisHome }, () => {
    const info = resolveDataRootInfo()

    assert.equal(info.dataRoot, path.resolve(dataRoot))
    assert.equal(info.metisHome, path.resolve(metisHome))
    assert.equal(info.electronUserData, path.join(path.resolve(dataRoot), 'electron'))
  })
})

test('data-root.json supports relative configured roots', () => {
  const installDir = tempDir()
  fs.writeFileSync(
    path.join(installDir, 'data-root.json'),
    JSON.stringify({ dataRoot: 'portable-data' }),
    'utf8'
  )
  withEnv({ METIS_DATA_ROOT: null, METIS_HOME: null, METIS_DATA_ROOT_CONFIG: null }, () => {
    const info = resolveDataRootInfo({ installDir, useCache: false })

    assert.equal(info.source, 'config')
    assert.equal(info.dataRoot, path.join(installDir, 'portable-data'))
    assert.equal(info.configPath, path.join(installDir, 'data-root.json'))
  })
})

test('writable install directory defaults to install-dir data', () => {
  const installDir = tempDir()
  withEnv({ METIS_DATA_ROOT: null, METIS_HOME: null, METIS_DATA_ROOT_CONFIG: null }, () => {
    const info = resolveDataRootInfo({ installDir, useCache: false })

    assert.equal(info.source, 'install')
    assert.equal(info.dataRoot, path.join(installDir, 'data'))
    assert.equal(fs.existsSync(info.dataRoot), true)
  })
})
