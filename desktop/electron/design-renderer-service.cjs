const net = require('node:net')
const path = require('node:path')
const { StringDecoder } = require('node:string_decoder')
const { pathToFileURL } = require('node:url')

const MAX_IPC_FRAME_BYTES = 128 * 1024 * 1024

function normalizeNamespace(value) {
  const namespace = String(value || '').trim()
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(namespace)) {
    throw new Error(`invalid Design renderer namespace: ${namespace || '<empty>'}`)
  }
  return namespace
}

function designDesktopIpcPath(namespace, options = {}) {
  const normalized = normalizeNamespace(namespace)
  const platform = options.platform || process.platform
  if (platform === 'win32') return `\\\\.\\pipe\\open-design-${normalized}-desktop`
  const ipcBase = path.resolve(String(options.ipcBase || process.env.OD_SIDECAR_BASE || '/tmp/open-design/ipc'))
  return path.join(ipcBase, normalized, 'desktop.sock')
}

function assertObject(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`)
  return value
}

function assertKnownKeys(value, allowed, label) {
  const expected = new Set(allowed)
  const unsupported = Object.keys(value).filter(key => !expected.has(key))
  if (unsupported.length) throw new Error(`${label} contains unsupported fields: ${unsupported.join(', ')}`)
}

function requiredString(value, label) {
  if (typeof value !== 'string' || value.length === 0) throw new Error(`${label} must be a non-empty string`)
  return value
}

function optionalPositiveNumber(value, label) {
  if (value == null) return undefined
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) throw new Error(`${label} must be a positive number`)
  return value
}

function isPathInside(parent, candidate) {
  const relative = path.relative(path.resolve(parent), path.resolve(candidate))
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative))
}

function normalizeRenderSlidesInput(input, dataRoot) {
  const value = assertObject(input, 'desktop render slides input')
  assertKnownKeys(value, ['baseHref', 'deck', 'editable', 'height', 'html', 'index', 'outputDir', 'pageImageFormat', 'stitch', 'paginate', 'width'], 'desktop render slides input')
  if (value.deck != null && typeof value.deck !== 'boolean') throw new Error('desktop render slides deck must be a boolean')
  if (value.editable != null && typeof value.editable !== 'boolean') throw new Error('desktop render slides editable must be a boolean')
  if (value.stitch != null && typeof value.stitch !== 'boolean') throw new Error('desktop render slides stitch must be a boolean')
  if (value.paginate != null && typeof value.paginate !== 'boolean') throw new Error('desktop render slides paginate must be a boolean')
  if (value.index != null && (!Number.isInteger(value.index) || value.index < 0)) throw new Error('desktop render slides index must be a non-negative integer')
  if (value.pageImageFormat != null && !['png', 'jpeg'].includes(value.pageImageFormat)) throw new Error("desktop render slides pageImageFormat must be 'png' or 'jpeg'")
  if (value.outputDir != null) {
    const outputDir = requiredString(value.outputDir, 'desktop render slides outputDir')
    if (!path.isAbsolute(outputDir)) throw new Error('desktop render slides outputDir must be an absolute path')
    if (!isPathInside(dataRoot, outputDir)) throw new Error('desktop render slides outputDir must stay inside the Metis Design data directory')
  }
  return {
    ...(value.baseHref == null ? {} : { baseHref: requiredString(value.baseHref, 'desktop render slides baseHref') }),
    ...(value.deck == null ? {} : { deck: value.deck }),
    ...(value.editable == null ? {} : { editable: value.editable }),
    html: requiredString(value.html, 'desktop render slides html'),
    ...(value.index == null ? {} : { index: value.index }),
    ...(value.outputDir == null ? {} : { outputDir: value.outputDir }),
    ...(value.pageImageFormat == null ? {} : { pageImageFormat: value.pageImageFormat }),
    ...(value.stitch == null ? {} : { stitch: value.stitch }),
    ...(value.paginate == null ? {} : { paginate: value.paginate }),
    ...(value.width == null ? {} : { width: optionalPositiveNumber(value.width, 'desktop render slides width') }),
    ...(value.height == null ? {} : { height: optionalPositiveNumber(value.height, 'desktop render slides height') })
  }
}

function normalizeExportArtifactInput(input) {
  const value = assertObject(input, 'desktop artifact export input')
  assertKnownKeys(value, ['baseHref', 'deck', 'format', 'html', 'imageFormat', 'title', 'width', 'height'], 'desktop artifact export input')
  if (!['pdf', 'image'].includes(value.format)) throw new Error(`unsupported artifact export format: ${String(value.format)}`)
  if (value.imageFormat != null && !['png', 'jpeg'].includes(value.imageFormat)) throw new Error(`unsupported artifact export image format: ${String(value.imageFormat)}`)
  if (typeof value.deck !== 'boolean') throw new Error('desktop artifact export deck must be a boolean')
  return {
    ...(value.baseHref == null ? {} : { baseHref: requiredString(value.baseHref, 'desktop artifact export baseHref') }),
    deck: value.deck,
    format: value.format,
    html: requiredString(value.html, 'desktop artifact export html'),
    ...(value.imageFormat == null ? {} : { imageFormat: value.imageFormat }),
    title: requiredString(value.title, 'desktop artifact export title'),
    ...(value.width == null ? {} : { width: optionalPositiveNumber(value.width, 'desktop artifact export width') }),
    ...(value.height == null ? {} : { height: optionalPositiveNumber(value.height, 'desktop artifact export height') })
  }
}

function normalizeExportPdfInput(input) {
  const value = assertObject(input, 'desktop PDF export input')
  assertKnownKeys(value, ['baseHref', 'deck', 'defaultFilename', 'html', 'title'], 'desktop PDF export input')
  if (typeof value.deck !== 'boolean') throw new Error('desktop PDF export deck must be a boolean')
  return {
    ...(value.baseHref == null ? {} : { baseHref: requiredString(value.baseHref, 'desktop PDF export baseHref') }),
    deck: value.deck,
    defaultFilename: requiredString(value.defaultFilename, 'desktop PDF export defaultFilename'),
    html: requiredString(value.html, 'desktop PDF export html'),
    title: requiredString(value.title, 'desktop PDF export title')
  }
}

function normalizeRequest(message, dataRoot) {
  const value = assertObject(message, 'desktop sidecar message')
  const type = requiredString(value.type, 'desktop sidecar message type')
  if (['status', 'shutdown', 'console', 'show'].includes(type)) {
    assertKnownKeys(value, ['type'], 'desktop sidecar message')
    return { type }
  }
  assertKnownKeys(value, ['input', 'type'], 'desktop sidecar message')
  if (type === 'render-slides') return { type, input: normalizeRenderSlidesInput(value.input, dataRoot) }
  if (type === 'export-artifact') return { type, input: normalizeExportArtifactInput(value.input) }
  if (type === 'export-pdf') return { type, input: normalizeExportPdfInput(value.input) }
  throw new Error(`unsupported Metis Design desktop message: ${type}`)
}

async function loadRenderers(moduleRoot) {
  const root = path.resolve(String(moduleRoot || ''))
  const [deck, artifact, pdf] = await Promise.all([
    import(pathToFileURL(path.join(root, 'deck-capture.js')).href),
    import(pathToFileURL(path.join(root, 'artifact-export.js')).href),
    import(pathToFileURL(path.join(root, 'pdf-export.js')).href)
  ])
  return {
    renderDeckSlides: deck.renderDeckSlides,
    exportArtifact: artifact.exportArtifact,
    exportPdfFromHtml: pdf.exportPdfFromHtml
  }
}

function jsonError(error) {
  return {
    message: error instanceof Error ? error.message : String(error),
    name: error instanceof Error ? error.name : 'Error'
  }
}

async function prepareSocketPath(socketPath, platform) {
  if (platform === 'win32') return
  await require('node:fs/promises').mkdir(path.dirname(socketPath), { recursive: true })
  await require('node:fs/promises').rm(socketPath, { force: true })
}

async function startDesignRendererService(options = {}) {
  const namespace = normalizeNamespace(options.namespace)
  const dataRoot = path.resolve(requiredString(options.dataRoot, 'Design renderer dataRoot'))
  const platform = options.platform || process.platform
  const socketPath = designDesktopIpcPath(namespace, { platform, ipcBase: options.ipcBase })
  const log = typeof options.log === 'function' ? options.log : () => {}
  let renderersPromise = options.renderers ? Promise.resolve(options.renderers) : null
  let closing = false
  let handle = null

  const rendererApi = () => {
    if (!renderersPromise) renderersPromise = loadRenderers(options.moduleRoot)
    return renderersPromise
  }

  const handler = async message => {
    const request = normalizeRequest(message, dataRoot)
    if (request.type === 'status') {
      return { pid: process.pid, state: 'running', updatedAt: new Date().toISOString(), url: null, windowVisible: false }
    }
    if (request.type === 'console') return []
    if (request.type === 'show') return { accepted: true }
    if (request.type === 'shutdown') {
      setImmediate(() => { void handle?.close() })
      return { accepted: true }
    }
    const renderers = await rendererApi()
    if (request.type === 'render-slides') return renderers.renderDeckSlides(request.input)
    if (request.type === 'export-artifact') return renderers.exportArtifact(request.input)
    if (request.type === 'export-pdf') return renderers.exportPdfFromHtml(request.input)
    throw new Error(`unsupported Metis Design desktop message: ${request.type}`)
  }

  await prepareSocketPath(socketPath, platform)
  const server = net.createServer(socket => {
    const decoder = new StringDecoder('utf8')
    let buffer = ''
    let handled = false
    socket.on('error', error => log(`[design-renderer] socket error: ${error.message}`))
    socket.on('data', chunk => {
      if (handled) return
      buffer += decoder.write(chunk)
      if (Buffer.byteLength(buffer, 'utf8') > MAX_IPC_FRAME_BYTES) {
        handled = true
        socket.end(`${JSON.stringify({ ok: false, error: { name: 'Error', message: 'Design renderer IPC frame is too large' } })}\n`)
        return
      }
      const newline = buffer.indexOf('\n')
      if (newline < 0) return
      handled = true
      let message
      try {
        message = JSON.parse(buffer.slice(0, newline))
      } catch (error) {
        socket.end(`${JSON.stringify({ ok: false, error: jsonError(error) })}\n`)
        return
      }
      void handler(message).then(
        result => socket.end(`${JSON.stringify({ ok: true, result })}\n`),
        error => {
          log(`[design-renderer] request failed: ${error instanceof Error ? error.message : String(error)}`)
          socket.end(`${JSON.stringify({ ok: false, error: jsonError(error) })}\n`)
        }
      )
    })
  })

  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(socketPath, () => {
      server.off('error', reject)
      resolve()
    })
  })

  handle = {
    namespace,
    socketPath,
    async close() {
      if (closing) return
      closing = true
      await new Promise(resolve => server.close(() => resolve()))
      if (platform !== 'win32') await require('node:fs/promises').rm(socketPath, { force: true })
    }
  }
  log(`[design-renderer] listening on ${socketPath}`)
  return handle
}

module.exports = {
  designDesktopIpcPath,
  isPathInside,
  normalizeRenderSlidesInput,
  startDesignRendererService
}
