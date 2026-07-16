const assert = require('node:assert/strict')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { randomUUID } = require('node:crypto')
const { app } = require('electron')

const { startDesignRendererService } = require('../electron/design-renderer-service.cjs')

app.disableHardwareAcceleration()

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
      const response = JSON.parse(buffer.slice(0, newline))
      if (!response.ok) reject(new Error(response.error?.message || 'Design renderer smoke request failed'))
      else resolve(response.result)
    })
  })
}

app.whenReady().then(async () => {
  const dataRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-design-renderer-smoke-'))
  const outputDir = path.join(dataRoot, 'exports', randomUUID())
  let service
  try {
    service = await startDesignRendererService({
      namespace: `metis-smoke-${randomUUID()}`,
      dataRoot,
      moduleRoot: path.resolve(__dirname, '..', '..', 'open-design', 'apps', 'desktop', 'dist', 'main')
    })
    const result = await request(service.socketPath, {
      type: 'render-slides',
      input: {
        deck: true,
        html: '<!doctype html><html><head><style>html,body{margin:0}.slide{width:1920px;height:1080px;background:#fff;color:#111;display:grid;place-items:center;font:80px sans-serif}</style></head><body><section class="slide">Metis Design export</section></body></html>',
        outputDir
      }
    })
    assert.equal(result.mode, 'deck')
    assert.equal(result.slideFiles.length, 1)
    const bytes = fs.readFileSync(result.slideFiles[0])
    assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10])
    process.stdout.write(`Metis Design renderer smoke passed: ${result.slideFiles[0]}\n`)
  } finally {
    await service?.close()
    fs.rmSync(dataRoot, { recursive: true, force: true })
    app.quit()
  }
}).catch(error => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`)
  app.exit(1)
})
