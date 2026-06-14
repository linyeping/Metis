const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const CONFIG_FILE_NAME = 'data-root.json'
let cachedInfo = null

function cleanConfiguredPath(value) {
  return String(value || '').trim().replace(/^["']|["']$/g, '')
}

function isPackagedRuntime(options = {}) {
  if (typeof options.packaged === 'boolean') return options.packaged
  return Boolean(process.resourcesPath && !process.env.METIS_DESKTOP_DEV_SERVER)
}

function installBaseDir(options = {}) {
  const explicit = cleanConfiguredPath(options.installDir || process.env.METIS_INSTALL_DIR)
  if (explicit) return path.resolve(explicit)
  if (isPackagedRuntime(options)) return path.dirname(process.execPath)
  return path.resolve(__dirname, '..')
}

function configFileCandidates(options = {}) {
  const candidates = []
  const explicit = cleanConfiguredPath(options.configFile || process.env.METIS_DATA_ROOT_CONFIG)
  if (explicit) candidates.push(path.resolve(explicit))
  candidates.push(path.join(installBaseDir(options), CONFIG_FILE_NAME))
  return [...new Set(candidates)]
}

function ensureWritableDirectory(dir) {
  const resolved = path.resolve(dir)
  const probe = path.join(resolved, `.metis-write-test-${process.pid}-${Date.now()}`)
  try {
    fs.mkdirSync(resolved, { recursive: true })
    fs.writeFileSync(probe, 'ok', 'utf8')
    fs.unlinkSync(probe)
    return true
  } catch {
    try {
      if (fs.existsSync(probe)) fs.unlinkSync(probe)
    } catch {}
    return false
  }
}

function configuredRoot(options = {}) {
  for (const configFile of configFileCandidates(options)) {
    if (!fs.existsSync(configFile)) continue
    try {
      const parsed = JSON.parse(fs.readFileSync(configFile, 'utf8'))
      const configured = cleanConfiguredPath(parsed.dataRoot || parsed.root || parsed.path)
      if (!configured) continue
      const dataRoot = path.isAbsolute(configured)
        ? path.normalize(configured)
        : path.resolve(path.dirname(configFile), configured)
      if (!ensureWritableDirectory(dataRoot)) continue
      return { dataRoot, source: 'config', configPath: configFile }
    } catch {
      continue
    }
  }
  return null
}

function localAppDataRoot(options = {}) {
  const explicit = cleanConfiguredPath(options.localAppData)
  if (explicit) return path.join(path.resolve(explicit), 'Metis', 'data')
  if (process.platform === 'win32') {
    const localAppData = cleanConfiguredPath(process.env.LOCALAPPDATA)
    if (localAppData) return path.join(localAppData, 'Metis', 'data')
  }
  return path.join(os.homedir(), '.local', 'share', 'Metis', 'data')
}

function resolveDataRootInfo(options = {}) {
  const useCache = options.useCache !== false && Object.keys(options).length === 0
  if (useCache && cachedInfo) return cachedInfo

  const envRoot = cleanConfiguredPath(process.env.METIS_DATA_ROOT)
  const explicitMetisHome = cleanConfiguredPath(process.env.METIS_HOME)
  let result = null

  if (envRoot && ensureWritableDirectory(envRoot)) {
    result = { dataRoot: path.resolve(envRoot), source: 'env', configPath: '' }
  }

  if (!result) {
    result = configuredRoot(options)
  }

  const baseDir = installBaseDir(options)
  const portableMarker = path.join(baseDir, 'metis-portable.marker')
  const hasPortableMarker = fs.existsSync(portableMarker)

  if (!result) {
    const installDataRoot = path.join(baseDir, 'data')
    if (ensureWritableDirectory(installDataRoot)) {
      result = {
        dataRoot: installDataRoot,
        source: hasPortableMarker ? 'portable-marker' : 'install',
        configPath: path.join(baseDir, CONFIG_FILE_NAME)
      }
    }
  }

  if (!result) {
    const fallback = localAppDataRoot(options)
    if (ensureWritableDirectory(fallback)) {
      result = { dataRoot: fallback, source: 'local-app-data', configPath: path.join(baseDir, CONFIG_FILE_NAME) }
    }
  }

  if (!result) {
    const fallback = path.join(os.homedir(), '.metis-desktop', 'data')
    ensureWritableDirectory(fallback)
    result = { dataRoot: fallback, source: 'home-fallback', configPath: path.join(baseDir, CONFIG_FILE_NAME) }
  }

  const dataRoot = path.resolve(result.dataRoot)
  const info = {
    dataRoot,
    metisHome: explicitMetisHome ? path.resolve(explicitMetisHome) : path.join(dataRoot, 'metis'),
    electronUserData: path.join(dataRoot, 'electron'),
    source: result.source,
    configPath: result.configPath || '',
    portable: hasPortableMarker,
    legacyMetisHome: legacyMetisHome()
  }
  if (useCache) cachedInfo = info
  return info
}

function resolveDataRoot(options = {}) {
  return resolveDataRootInfo(options).dataRoot
}

function metisHome(options = {}) {
  return resolveDataRootInfo(options).metisHome
}

function electronUserData(options = {}) {
  return resolveDataRootInfo(options).electronUserData
}

function configFilePath(options = {}) {
  return path.join(metisHome(options), 'config.json')
}

function legacyMetisHome() {
  return path.join(os.homedir(), '.metis')
}

function clearDataRootCache() {
  cachedInfo = null
}

module.exports = {
  CONFIG_FILE_NAME,
  clearDataRootCache,
  configFilePath,
  electronUserData,
  ensureWritableDirectory,
  legacyMetisHome,
  metisHome,
  resolveDataRoot,
  resolveDataRootInfo
}
