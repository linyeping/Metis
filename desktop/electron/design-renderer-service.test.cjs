const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { randomUUID } = require('node:crypto')

const {
  designDesktopIpcPath,
  normalizeRenderSlidesInput,
  startDesignRendererService
} = require('./design-renderer-service.cjs')

function request(socketPath, payload) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath)
    let buffer = ''
    socket.on('connect', () => socket.write(`${JSON.stringify(payload)}\n`))
    socket.on('error', reject)
    socket.on('data', chunk => {
      buffer += chunk.toString('utf8')
      const newline = buffer.indexOf('\n')
      if (newline < 0) return
      socket.end()
      resolve(JSON.parse(buffer.slice(0, newline)))
    })
  })
}

test('Design renderer uses the sidecar contract desktop pipe', () => {
  assert.equal(
    designDesktopIpcPath('metis', { platform: 'win32' }),
    '\\\\.\\pipe\\open-design-metis-desktop'
  )
})

test('Design renderer confines slide output to the Metis Design data root', () => {
  const dataRoot = path.join(os.tmpdir(), 'metis-design-data')
  assert.throws(() => normalizeRenderSlidesInput({
    html: '<html></html>',
    outputDir: path.resolve(dataRoot, '..', 'outside')
  }, dataRoot), /must stay inside/)
  assert.equal(normalizeRenderSlidesInput({
    html: '<html></html>',
    outputDir: path.join(dataRoot, 'exports', 'one')
  }, dataRoot).outputDir, path.join(dataRoot, 'exports', 'one'))
})

test('Design renderer serves status and routes render requests without a second desktop app', async () => {
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-design-renderer-'))
  const namespace = `metis-test-${randomUUID()}`
  const seen = []
  const service = await startDesignRendererService({
    namespace,
    dataRoot,
    renderers: {
      renderDeckSlides: async input => {
        seen.push(input)
        return { mode: 'deck', slides: ['data:image/png;base64,AA=='] }
      },
      exportArtifact: async () => ({ ok: true, path: 'artifact.pdf' }),
      exportPdfFromHtml: async () => ({ ok: true, path: 'document.pdf' })
    }
  })
  try {
    const status = await request(service.socketPath, { type: 'status' })
    assert.equal(status.ok, true)
    assert.equal(status.result.state, 'running')

    const rendered = await request(service.socketPath, {
      type: 'render-slides',
      input: { html: '<html><section class="slide">One</section></html>', deck: true }
    })
    assert.equal(rendered.ok, true)
    assert.equal(rendered.result.mode, 'deck')
    assert.equal(seen.length, 1)
  } finally {
    await service.close()
    fs.rmSync(dataRoot, { recursive: true, force: true })
  }
})
