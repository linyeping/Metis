const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  extractPetZip,
  findPetDirectories,
  inferSpriteVersion,
  readImageDimensions,
  readPngDimensions,
  readWebpDimensions
} = require('./pet-package.cjs')

test('reads PNG dimensions directly from the IHDR header', () => {
  const buffer = Buffer.alloc(24)
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(buffer)
  buffer.write('IHDR', 12, 'ascii')
  buffer.writeUInt32BE(1536, 16)
  buffer.writeUInt32BE(2288, 20)
  assert.deepEqual(readPngDimensions(buffer), { width: 1536, height: 2288, format: 'png' })
  assert.equal(inferSpriteVersion(readPngDimensions(buffer)), 2)
})

test('reads VP8X WebP canvas dimensions without Electron nativeImage', () => {
  const buffer = Buffer.alloc(30)
  buffer.write('RIFF', 0, 'ascii')
  buffer.writeUInt32LE(22, 4)
  buffer.write('WEBPVP8X', 8, 'ascii')
  buffer.writeUInt32LE(10, 16)
  const width = 1536 - 1
  const height = 1872 - 1
  buffer[24] = width & 0xff
  buffer[25] = (width >>> 8) & 0xff
  buffer[26] = (width >>> 16) & 0xff
  buffer[27] = height & 0xff
  buffer[28] = (height >>> 8) & 0xff
  buffer[29] = (height >>> 16) & 0xff
  assert.deepEqual(readWebpDimensions(buffer), { width: 1536, height: 1872, format: 'webp' })
  assert.equal(inferSpriteVersion(readWebpDimensions(buffer)), 1)
})

test('accepts the Codex pet spritesheets installed on this machine when present', t => {
  const spritesheet = 'E:\\OpenAI-Codex\\AppData\\pets\\gpt-muse\\spritesheet.webp'
  if (!fs.existsSync(spritesheet)) return t.skip('local Codex pet fixture is unavailable')
  assert.deepEqual(readImageDimensions(spritesheet), { width: 1536, height: 1872, format: 'webp' })
})

test('discovers a pet folder directly or below a selected Codex pets root', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'metis-pets-'))
  try {
    const first = path.join(root, 'first')
    const wrapped = path.join(root, 'bundle', 'second')
    fs.mkdirSync(first, { recursive: true })
    fs.mkdirSync(wrapped, { recursive: true })
    fs.writeFileSync(path.join(first, 'pet.json'), '{}')
    fs.writeFileSync(path.join(wrapped, 'pet.json'), '{}')
    assert.deepEqual(findPetDirectories(root).sort(), [first, wrapped].sort())
    assert.deepEqual(findPetDirectories(first), [first])
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('rejects ZIP packages that expand beyond the pet package budget', async () => {
  const fakeExtract = async (_zipPath, options) => {
    options.onEntry({ uncompressedSize: 40 * 1024 * 1024 })
    options.onEntry({ uncompressedSize: 40 * 1024 * 1024 })
  }
  await assert.rejects(
    extractPetZip(fakeExtract, 'pet.zip', 'output'),
    /解压后超过 64 MB/
  )
})
